"""Immutable local evidence for forecast issuance; no invented publication vintage."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
from collections.abc import Callable
from dataclasses import asdict, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import polars as pl

from gpa.forecast.boosting import LightGBM
from gpa.forecast.models import Model, Naive, Ridge
from gpa.forecast.panel import Panel
from gpa.zones import get_zone

POLICY_ID = "de-lu-development-v1-ridge-a0.1-train270"
MIN_TRAIN_ROWS = 270
_BASE = Path(__file__).resolve().parent
_SOURCE_FILES = {f"forecast/{p.name}": p.read_bytes() for p in sorted(_BASE.glob("*.py"))}
_SOURCE_FILES.update({name: (_BASE.parent / name).read_bytes() for name in ("zones.py", "calendar.py")})
SOURCE_SHA256 = hashlib.sha256(b"".join(name.encode() + data for name, data in sorted(_SOURCE_FILES.items()))).hexdigest()


def utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return value.astimezone(dt.UTC)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def default_model(name: str) -> Model:
    """Frozen development configuration; never select on prospective outcomes."""
    if name == "ridge":
        return Ridge(alpha=0.1)
    if name == "lightgbm":
        return LightGBM()
    columns = {"naive_previous_day": "price_d1", "naive_previous_week": "price_d7", "naive_similar_day": "price_similar_day"}
    if name in columns:
        return Naive(name, columns[name], "Fixed lagged-price comparator.")
    raise ValueError("model must be ridge, lightgbm or an existing naive comparator")


def describe(model: Model) -> dict[str, Any]:
    if type(model) not in (Ridge, LightGBM, Naive):
        raise ValueError("only registered reproducible model classes can be issued")
    assert isinstance(model, (Ridge, LightGBM, Naive))
    result = {"class": type(model).__name__, "parameters": asdict(model)}
    digest(result)  # Reject non-finite parameters before persisting any evidence.
    return result


def restore(spec: dict[str, Any]) -> Model:
    factories = {"Ridge": Ridge, "LightGBM": LightGBM, "Naive": Naive}
    name = spec["class"]
    if name not in factories:
        raise ValueError("unregistered model in evidence")
    return factories[name](**spec["parameters"])


def sanitized(panel: Panel, delivery: dt.date) -> Panel:
    """Remove later rows and mask target-day outcomes before fitting or hashing."""
    if panel.target in panel.features:
        raise ValueError("target must not be a predictor")
    required = {"local_date", "local_hour", "ts_utc", panel.target, *panel.features}
    if missing := required - set(panel.frame.columns):
        raise ValueError(f"panel missing columns: {sorted(missing)}")
    frame = panel.frame.filter(pl.col("local_date") <= delivery).with_columns(
        pl.when(pl.col("local_date") < delivery).then(pl.col(panel.target)).otherwise(None).alias(panel.target)
    ).sort("local_date", "local_hour")
    if frame.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
        raise ValueError("duplicate modelling clock-hour keys")
    for name, dtype in frame.schema.items():
        if dtype.is_float() and frame.filter(pl.col(name).is_not_null() & ~pl.col(name).is_finite()).height:
            raise ValueError(f"panel {name} must be finite when present")
    return replace(panel, frame=frame)


def input_hash(panel: Panel) -> str:
    return digest({
        "zone": panel.zone.code, "timezone": panel.zone.timezone, "features": panel.features,
        "target": panel.target, "similar_day": panel.similar_day,
        "schema": {k: str(v) for k, v in panel.frame.schema.items()},
        "rows": panel.frame.sort("local_date", "local_hour").write_json(),
    })


def _dependencies() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for package in ("polars", "numpy", "lightgbm", "scikit-learn", "tzdata"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not_installed"
    return result


def save_snapshot(
    root: Path, panel: Panel, model: Model, frame: pl.DataFrame, *,
    clock: Callable[[], dt.datetime], source_frames: dict[str, pl.DataFrame] | None = None,
    observed_at: dict[str, dt.datetime] | None = None,
) -> pl.DataFrame:
    """Write inputs first, then sample recording time and finalize evidence.

    Caller-supplied observation times describe local reads, not provider release
    times. Evidence is local; it is not a cryptographic external timestamp.
    Incomplete directories without a completion manifest are never eligible.
    """
    from gpa.forecast.ledger import timing_reason

    sources, observations = source_frames or {}, observed_at or {}
    if set(sources) != set(observations) or set(sources) - {"price", "load", "generation"}:
        raise ValueError("source frames require matching local observation timestamps")
    issued_at = frame["issued_at"][0]
    if any(utc(stamp) > frame["input_as_of"][0] for stamp in observations.values()):
        raise ValueError("source observation is later than the input cutoff")
    identifier = frame["issue_id"][0]
    path = Path(root) / "issues" / identifier
    path.mkdir(parents=True, exist_ok=False)
    checksums: dict[str, str] = {}

    def track(name: str) -> None:
        checksums[name] = hashlib.sha256((path / name).read_bytes()).hexdigest()

    panel.frame.write_parquet(path / "input_panel.parquet", compression="zstd")
    track("input_panel.parquet")
    for name, values in sources.items():
        filename = f"source_{name}.parquet"
        values.write_parquet(path / filename, compression="zstd")
        track(filename)
    for name, data in _SOURCE_FILES.items():
        filename = f"code/{name}"
        file = path / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        track(filename)

    recorded = utc(clock())
    if recorded < issued_at:
        raise ValueError("recording clock precedes completed prediction")
    reason = timing_reason(issued_at, recorded, frame["input_as_of"][0], frame["delivery_date"][0], panel.zone)
    final = frame.with_columns(pl.lit(recorded).alias("recorded_at"), pl.lit(reason == "pre_gate").alias("eligible"), pl.lit(reason).alias("eligibility_reason"))
    if reason != "pre_gate":
        final = final.with_columns(pl.when(pl.col("forecast").is_null()).then(pl.lit("abstain_missing_inputs"))
                                   .otherwise(pl.lit("diagnostic_issued")).alias("status"))
    final.write_parquet(path / "issued.parquet", compression="zstd")
    track("issued.parquet")
    metadata = {
        "issue_id": identifier, "input_sha256": input_hash(panel), "model": describe(model),
        "configuration": json.loads(frame["configuration"][0]),
        "source_sha256": SOURCE_SHA256, "environment": _dependencies(),
        "zone": panel.zone.code, "timezone": panel.zone.timezone, "features": list(panel.features),
        "target": panel.target, "similar_day": panel.similar_day,
        "source_availability": "local_read_observations_only" if sources else "not_supplied",
        "observed_at": {name: utc(stamp).isoformat() for name, stamp in observations.items()},
        "provider_publication_times": "unknown; availability-aware ingestion remains P0/E",
        "timestamp_basis": "local_clock_not_external_attestation",
        "checksums": checksums,
    }
    (path / "manifest.json").write_text(json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    return final


def read_snapshot(root: Path, identifier: str) -> tuple[dict[str, Any], Panel, Model, pl.DataFrame]:
    if len(identifier) != 64 or any(char not in "0123456789abcdef" for char in identifier):
        raise ValueError("invalid issue identifier")
    path = Path(root) / "issues" / identifier
    metadata = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if metadata["issue_id"] != identifier:
        raise ValueError("snapshot identity mismatch")
    checksums = metadata["checksums"]
    if not {"input_panel.parquet", "issued.parquet"}.issubset(checksums):
        raise ValueError("missing snapshot checksum")
    for name, expected in checksums.items():
        file = (path / name).resolve()
        if not file.is_relative_to(path.resolve()):
            raise ValueError("unsafe snapshot artifact path")
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError(f"snapshot checksum mismatch: {name}")
    code = sorted(name.removeprefix("code/") for name in checksums if name.startswith("code/"))
    code_hash = hashlib.sha256(b"".join(name.encode() + (path / "code" / name).read_bytes() for name in code)).hexdigest()
    if code_hash != metadata["source_sha256"]:
        raise ValueError("snapshot source checksum mismatch")
    panel = Panel(get_zone(metadata["zone"]), pl.read_parquet(path / "input_panel.parquet"),
                  tuple(metadata["features"]), metadata["target"], metadata["similar_day"])
    if input_hash(panel) != metadata["input_sha256"]:
        raise ValueError("snapshot input fingerprint mismatch")
    frame = pl.read_parquet(path / "issued.parquet")
    if (frame["issue_id"].unique().to_list() != [identifier]
            or frame["input_sha256"].unique().to_list() != [metadata["input_sha256"]]
            or json.loads(frame["configuration"][0]) != metadata["configuration"]
            or metadata["configuration"]["model"] != metadata["model"]
            or metadata["configuration"]["source_sha256"] != metadata["source_sha256"]):
        raise ValueError("snapshot metadata does not match issued evidence")
    return metadata, panel, restore(metadata["model"]), frame
