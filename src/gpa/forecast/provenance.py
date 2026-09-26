"""Immutable evidence for each prospective issue: its inputs, its code and its model.

Large artefacts are content-addressed blobs shared across issues and split by month,
so an unchanged month costs nothing the next day. Timestamps are local reads, not
external attestations, and no publication vintage is ever invented.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import platform
import re
from collections.abc import Callable
from dataclasses import asdict, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast

import polars as pl

from gpa.forecast.boosting import LightGBM
from gpa.forecast.models import Model, Naive, Ridge
from gpa.forecast.panel import RESIDUAL_LOAD_FUELS, Panel, hourly_residual_load
from gpa.store import atomic_write
from gpa.zones import get_zone

MIN_TRAIN_ROWS = 270

FUNDAMENTALS_SUFFIX = "_da"
"""Marks the arm with fundamentals: two information sets must never share a ledger key."""


def uses_fundamentals(name: str) -> bool:
    """Whether this model identity fits day-ahead operator forecasts."""
    return name.endswith(FUNDAMENTALS_SUFFIX)


def policy_id(name: str) -> str:
    """The frozen issuance policy, recorded on every issue so the arms read apart."""
    information_set = "da-fundamentals" if uses_fundamentals(name) else "published-set"
    return f"de-lu-development-v1-{name}-train{MIN_TRAIN_ROWS}-{information_set}"


_BASE = Path(__file__).resolve().parent
_SOURCE_FILES = {f"forecast/{p.name}": p.read_bytes() for p in sorted(_BASE.glob("*.py"))}
_SOURCE_FILES.update(
    {name: (_BASE.parent / name).read_bytes() for name in ("zones.py", "calendar.py")}
)
SOURCE_SHA256 = hashlib.sha256(
    b"".join(name.encode() + data for name, data in sorted(_SOURCE_FILES.items()))
).hexdigest()


def utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return value.astimezone(dt.UTC)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def default_model(name: str) -> Model:
    """Frozen configurations; ``ridge`` and ``ridge_da`` differ only in their inputs."""
    if name == "ridge":
        return Ridge(alpha=0.1)
    if name == "ridge_da":
        return Ridge(
            alpha=0.1,
            name="ridge_da",
            description=(
                "Per-hour ridge on the published set plus day-ahead operator "
                "forecasts of load, wind and solar."
            ),
        )
    if name == "lightgbm":
        return LightGBM()
    columns = {
        "naive_previous_day": "price_d1",
        "naive_previous_week": "price_d7",
        "naive_similar_day": "price_similar_day",
    }
    if name in columns:
        return Naive(name, columns[name], "Fixed lagged-price comparator.")
    raise ValueError("model must be ridge, ridge_da, lightgbm or an existing naive comparator")


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
    return cast(Model, factories[name](**spec["parameters"]))


def sanitized(panel: Panel, delivery: dt.date) -> Panel:
    """Remove later rows and mask target-day outcomes before fitting or hashing."""
    if panel.target in panel.features:
        raise ValueError("target must not be a predictor")
    required = {"local_date", "local_hour", "ts_utc", panel.target, *panel.features}
    if missing := required - set(panel.frame.columns):
        raise ValueError(f"panel missing columns: {sorted(missing)}")
    frame = (
        panel.frame.filter(pl.col("local_date") <= delivery)
        .with_columns(
            pl.when(pl.col("local_date") < delivery)
            .then(pl.col(panel.target))
            .otherwise(None)
            .alias(panel.target)
        )
        .sort("local_date", "local_hour")
    )
    if frame.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
        raise ValueError("duplicate modelling clock-hour keys")
    for name, dtype in frame.schema.items():
        if (
            dtype.is_float()
            and frame.filter(pl.col(name).is_not_null() & ~pl.col(name).is_finite()).height
        ):
            raise ValueError(f"panel {name} must be finite when present")
    return replace(panel, frame=frame)


def input_hash(panel: Panel) -> str:
    return digest(
        {
            "zone": panel.zone.code,
            "timezone": panel.zone.timezone,
            "features": panel.features,
            "target": panel.target,
            "similar_day": panel.similar_day,
            "schema": {k: str(v) for k, v in panel.frame.schema.items()},
            "rows": panel.frame.sort("local_date", "local_hour").write_json(),
        }
    )


def _dependencies() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for package in ("polars", "numpy", "lightgbm", "tzdata"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not_installed"
    return result


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _write_blob(root: Path, data: bytes) -> str:
    """Write ``data`` once under its hash; writing identical bytes again is a no-op."""
    digest = hashlib.sha256(data).hexdigest()
    target = Path(root) / "blobs" / digest
    if not target.exists():
        atomic_write(target, data)
    return digest


def _read_blob(root: Path, digest: str) -> bytes:
    if not _DIGEST_RE.fullmatch(digest):
        raise ValueError("invalid snapshot blob reference")
    data = (Path(root) / "blobs" / digest).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"snapshot blob checksum mismatch: {digest}")
    return data


def _write_partitioned(root: Path, frame: pl.DataFrame, column: str) -> dict[str, str]:
    """Blob each UTC month of ``frame`` separately, so past months are shared."""
    if frame.is_empty():
        return {}
    labeled = frame.with_columns(pl.col(column).dt.strftime("%Y-%m").alias("_month"))
    parts = labeled.partition_by("_month", as_dict=True, maintain_order=True)
    written: dict[str, str] = {}
    for month, part in parts.items():
        buffer = io.BytesIO()
        part.drop("_month").write_parquet(buffer, compression="zstd")
        written[month[0]] = _write_blob(root, buffer.getvalue())
    return written


def _read_partitioned(root: Path, manifest: dict[str, str]) -> pl.DataFrame:
    parts = [
        pl.read_parquet(io.BytesIO(_read_blob(root, manifest[month]))) for month in sorted(manifest)
    ]
    return pl.concat(parts, how="vertical") if parts else pl.DataFrame()


def save_snapshot(
    root: Path,
    panel: Panel,
    model: Model,
    frame: pl.DataFrame,
    *,
    clock: Callable[[], dt.datetime],
    source_frames: dict[str, pl.DataFrame] | None = None,
    observed_at: dict[str, dt.datetime] | None = None,
) -> pl.DataFrame:
    """Write the inputs, then sample the recording clock and finalise the evidence.

    Generation is archived for the residual-load fuels only, after checking that the
    reduced archive reproduces the panel's residual load exactly.
    """
    from gpa.forecast.ledger import timing_reason

    sources, observations = source_frames or {}, observed_at or {}
    if set(sources) != set(observations) or set(sources) - {
        "price",
        "load",
        "generation",
        "fundamentals",
    }:
        raise ValueError("source frames require matching local observation timestamps")
    issued_at = frame["issued_at"][0]
    if any(utc(stamp) > frame["input_as_of"][0] for stamp in observations.values()):
        raise ValueError("source observation is later than the input cutoff")

    stored_sources = dict(sources)
    if "generation" in stored_sources:
        full_generation = stored_sources["generation"]
        minimal_generation = full_generation.filter(pl.col("fuel").is_in(RESIDUAL_LOAD_FUELS))
        if "load" in stored_sources:
            load = stored_sources["load"]
            if not hourly_residual_load(load, full_generation, panel.zone).equals(
                hourly_residual_load(load, minimal_generation, panel.zone)
            ):
                raise ValueError(
                    "residual-load fuels changed; RESIDUAL_LOAD_FUELS no longer covers "
                    "every fuel the panel reads, so a minimal snapshot would not "
                    "reproduce it. Widen RESIDUAL_LOAD_FUELS in panel.py to match."
                )
        stored_sources["generation"] = minimal_generation

    identifier = frame["issue_id"][0]
    path = Path(root) / "issues" / identifier
    path.mkdir(parents=True, exist_ok=False)

    artifacts: dict[str, dict[str, str]] = {
        "input_panel": _write_partitioned(root, panel.frame, "local_date"),
        **{
            f"source_{name}": _write_partitioned(root, values, "ts_utc")
            for name, values in stored_sources.items()
        },
        "code": {name: _write_blob(root, data) for name, data in _SOURCE_FILES.items()},
    }

    recorded = utc(clock())
    if recorded < issued_at:
        raise ValueError("recording clock precedes completed prediction")
    reason = timing_reason(
        issued_at, recorded, frame["input_as_of"][0], frame["delivery_date"][0], panel.zone
    )
    final = frame.with_columns(
        pl.lit(recorded).alias("recorded_at"),
        pl.lit(reason == "pre_gate").alias("eligible"),
        pl.lit(reason).alias("eligibility_reason"),
    )
    if reason != "pre_gate":
        final = final.with_columns(
            pl.when(pl.col("forecast").is_null())
            .then(pl.lit("abstain_missing_inputs"))
            .otherwise(pl.lit("diagnostic_issued"))
            .alias("status")
        )
    final.write_parquet(path / "issued.parquet", compression="zstd")
    issued_checksum = hashlib.sha256((path / "issued.parquet").read_bytes()).hexdigest()
    metadata = {
        "issue_id": identifier,
        "input_sha256": input_hash(panel),
        "model": describe(model),
        "configuration": json.loads(frame["configuration"][0]),
        "source_sha256": SOURCE_SHA256,
        "environment": _dependencies(),
        "zone": panel.zone.code,
        "timezone": panel.zone.timezone,
        "features": list(panel.features),
        "target": panel.target,
        "similar_day": panel.similar_day,
        "source_availability": "local_read_observations_only" if sources else "not_supplied",
        "generation_fuels_stored": (
            list(RESIDUAL_LOAD_FUELS) if "generation" in stored_sources else None
        ),
        "observed_at": {name: utc(stamp).isoformat() for name, stamp in observations.items()},
        "provider_publication_times": "unknown; the provider exposes none",
        "timestamp_basis": "local_clock_not_external_attestation",
        "issued_checksum": issued_checksum,
        "artifacts": artifacts,
    }
    (path / "manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    return final


def read_snapshot(root: Path, identifier: str) -> tuple[dict[str, Any], Panel, Model, pl.DataFrame]:
    if len(identifier) != 64 or any(char not in "0123456789abcdef" for char in identifier):
        raise ValueError("invalid issue identifier")
    path = Path(root) / "issues" / identifier
    metadata = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if metadata["issue_id"] != identifier:
        raise ValueError("snapshot identity mismatch")
    artifacts = metadata["artifacts"]
    if not {"input_panel", "code"}.issubset(artifacts):
        raise ValueError("missing snapshot artifacts")

    issued_bytes = (path / "issued.parquet").read_bytes()
    if hashlib.sha256(issued_bytes).hexdigest() != metadata["issued_checksum"]:
        raise ValueError("snapshot checksum mismatch: issued.parquet")

    input_frame = _read_partitioned(root, artifacts["input_panel"])
    for name, parts in artifacts.items():
        if name in ("code", "input_panel"):
            continue
        for digest in parts.values():
            _read_blob(root, digest)  # raw source evidence: verified, not reconstructed

    code_bytes = {name: _read_blob(root, digest) for name, digest in artifacts["code"].items()}
    code_hash = hashlib.sha256(
        b"".join(name.encode() + code_bytes[name] for name in sorted(code_bytes))
    ).hexdigest()
    if code_hash != metadata["source_sha256"]:
        raise ValueError("snapshot source checksum mismatch")

    panel = Panel(
        get_zone(metadata["zone"]),
        input_frame,
        tuple(metadata["features"]),
        metadata["target"],
        metadata["similar_day"],
    )
    if input_hash(panel) != metadata["input_sha256"]:
        raise ValueError("snapshot input fingerprint mismatch")
    frame = pl.read_parquet(io.BytesIO(issued_bytes))
    if (
        frame["issue_id"].unique().to_list() != [identifier]
        or frame["input_sha256"].unique().to_list() != [metadata["input_sha256"]]
        or json.loads(frame["configuration"][0]) != metadata["configuration"]
        or metadata["configuration"]["model"] != metadata["model"]
        or metadata["configuration"]["source_sha256"] != metadata["source_sha256"]
    ):
        raise ValueError("snapshot metadata does not match issued evidence")
    return metadata, panel, restore(metadata["model"]), frame
