"""Issuance evidence, immutable forecast identity and canonical reconciliation.

The monthly Parquet files are a mutable settlement index, not the source of
forecast truth. Immutable input/issuance snapshots live under issues/<id>.
Legacy rows remain readable but cannot become certified prospective evidence.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import polars as pl

from gpa.forecast import provenance
from gpa.forecast.models import Model
from gpa.forecast.panel import Panel, hourly_mean
from gpa.zones import Zone, get_zone

DEFAULT_ROOT: Final = Path(__file__).resolve().parents[3] / "data" / "forecast_issues"
ISSUE_SCHEMA: Final = pl.Schema(
    cast(
        Any,
        {
            "zone": pl.String,
            "model": pl.String,
            "model_version": pl.String,
            "issued_at": pl.Datetime("us", "UTC"),
            "delivery_date": pl.Date,
            "local_hour": pl.Int8,
            "delivery_start_utc": pl.Datetime("us", "UTC"),
            "forecast": pl.Float64,
            "actual": pl.Float64,
            "status": pl.String,
            "input_sha256": pl.String,
            "protocol_version": pl.UInt8,
            "issue_id": pl.String,
            "configuration": pl.String,
            "input_as_of": pl.Datetime("us", "UTC"),
            "recorded_at": pl.Datetime("us", "UTC"),
            "eligible": pl.Boolean,
            "eligibility_reason": pl.String,
            "delivery_duration_hours": pl.Float64,
        },
    )
)
_KEYS = ["zone", "model", "issued_at", "delivery_date", "local_hour"]
IMMUTABLE = [name for name in ISSUE_SCHEMA if name not in ("actual", "status")]
_LEGACY = [
    "zone",
    "model",
    "model_version",
    "issued_at",
    "delivery_date",
    "local_hour",
    "delivery_start_utc",
    "forecast",
    "actual",
    "status",
    "input_sha256",
]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class LateIssueError(ValueError):
    """The issue completed outside the declared pre-gate policy window."""


def market_gate(delivery_date: dt.date, zone: Zone) -> dt.datetime:
    """Existing research policy: noon market time on D-1, strictly before gate."""
    return dt.datetime.combine(
        delivery_date - dt.timedelta(days=1), dt.time(12), ZoneInfo(zone.timezone)
    ).astimezone(dt.UTC)


def timing_reason(
    issued: dt.datetime, recorded: dt.datetime, as_of: dt.datetime, delivery: dt.date, zone: Zone
) -> str:
    if as_of > issued:
        return "inputs_after_prediction"
    if issued.astimezone(ZoneInfo(zone.timezone)).date() != delivery - dt.timedelta(days=1):
        return "outside_issue_day"
    gate = market_gate(delivery, zone)
    if issued >= gate:
        return "late_prediction"
    if recorded >= gate:
        return "late_recording"
    return "pre_gate"


def delivery_grid(day: dt.date, zone: Zone) -> pl.DataFrame:
    """Clock-hour benchmark grid; the repeated autumn hour has duration two."""
    start = dt.datetime.combine(day, dt.time(), ZoneInfo(zone.timezone)).astimezone(dt.UTC)
    stop = dt.datetime.combine(
        day + dt.timedelta(days=1), dt.time(), ZoneInfo(zone.timezone)
    ).astimezone(dt.UTC)
    rows: dict[int, dict[str, object]] = {}
    stamp = start
    while stamp < stop:
        hour = stamp.astimezone(ZoneInfo(zone.timezone)).hour
        if hour not in rows:
            rows[hour] = {
                "local_date": day,
                "local_hour": hour,
                "delivery_start_utc": stamp,
                "delivery_duration_hours": 0.0,
            }
        rows[hour]["delivery_duration_hours"] = (
            cast(float, rows[hour]["delivery_duration_hours"]) + 1.0
        )
        stamp += dt.timedelta(hours=1)
    return (
        pl.DataFrame(list(rows.values()))
        .with_columns(pl.col("local_hour").cast(pl.Int8))
        .sort("local_hour")
    )


def issue(
    panel: Panel,
    model: Model,
    delivery_date: dt.date,
    *,
    issued_at: dt.datetime | None = None,
    model_version: str | None = None,
    allow_late: bool = False,
    min_train_rows: int = 0,
    input_as_of: dt.datetime | None = None,
    policy_id: str = "explicit-library-configuration",
) -> pl.DataFrame:
    """Compute before sampling the production issue clock; never drop abstentions.

    Explicit issued_at is for deterministic offline replay/tests. The CLI does
    not expose it. Eligibility is timing-only here; canonical selection also
    requires a verified, complete saved snapshot.
    """
    as_of = provenance.utc(input_as_of or issued_at or now_utc())
    safe = provenance.sanitized(panel, delivery_date)
    if min_train_rows < 0:
        raise ValueError("min_train_rows cannot be negative")
    configuration = {
        "model": provenance.describe(model),
        "source_sha256": provenance.SOURCE_SHA256,
        "min_train_rows": min_train_rows,
        "policy_id": policy_id,
        "label": model_version,
    }
    version = f"{model.name}:{provenance.digest(configuration)}"
    predict_day = getattr(model, "predict_day", None)
    if not callable(predict_day):
        raise ValueError("model does not support feature-only prediction")
    forecasts = predict_day(safe, delivery_date, min_train_rows=min_train_rows).select(
        "local_date", pl.col("local_hour").cast(pl.Int8), pl.col("forecast").cast(pl.Float64)
    )
    if forecasts.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
        raise ValueError("model returned duplicate delivery keys")
    if forecasts.filter(pl.col("forecast").is_not_null() & ~pl.col("forecast").is_finite()).height:
        raise ValueError("model forecasts must be finite")
    grid = delivery_grid(delivery_date, panel.zone)
    if forecasts.join(
        grid.select("local_date", "local_hour"), on=["local_date", "local_hour"], how="anti"
    ).height:
        raise ValueError("model returned unexpected delivery keys")
    stamp = provenance.utc(issued_at or now_utc())
    if as_of > stamp:
        raise ValueError("input cutoff cannot follow prediction completion")
    reason = timing_reason(stamp, stamp, as_of, delivery_date, panel.zone)
    if reason != "pre_gate" and not allow_late:
        raise LateIssueError(
            f"forecast outside the D-1 pre-gate window ({reason}); use allow_late only for diagnostics"
        )
    fingerprint = provenance.input_hash(safe)
    identity = provenance.digest(
        {
            "zone": panel.zone.code,
            "model_version": version,
            "delivery_date": delivery_date.isoformat(),
            "issued_at": stamp.isoformat(),
            "input_as_of": as_of.isoformat(),
            "input_sha256": fingerprint,
        }
    )
    result = grid.join(forecasts, on=["local_date", "local_hour"], how="left").with_columns(
        pl.lit(panel.zone.code).alias("zone"),
        pl.lit(model.name).alias("model"),
        pl.lit(version).alias("model_version"),
        pl.lit(stamp).alias("issued_at"),
        pl.col("local_date").alias("delivery_date"),
        pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.lit(fingerprint).alias("input_sha256"),
        pl.lit(2).alias("protocol_version"),
        pl.lit(identity).alias("issue_id"),
        pl.lit(json.dumps(configuration, sort_keys=True, allow_nan=False)).alias("configuration"),
        pl.lit(as_of).alias("input_as_of"),
        pl.lit(stamp).alias("recorded_at"),
        pl.lit(reason == "pre_gate").alias("eligible"),
        pl.lit(reason).alias("eligibility_reason"),
        pl.when(pl.col("forecast").is_null())
        .then(pl.lit("abstain_missing_inputs"))
        .otherwise(pl.lit("issued" if reason == "pre_gate" else "diagnostic_issued"))
        .alias("status"),
    )
    return result.select(ISSUE_SCHEMA.names()).cast(ISSUE_SCHEMA)


def record_issue(
    panel: Panel,
    model: Model,
    delivery_date: dt.date,
    *,
    root: Path = DEFAULT_ROOT,
    issued_at: dt.datetime | None = None,
    allow_late: bool = False,
    min_train_rows: int = 0,
    input_as_of: dt.datetime | None = None,
    policy_id: str = "explicit-library-configuration",
    source_frames: dict[str, pl.DataFrame] | None = None,
    observed_at: dict[str, dt.datetime] | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> pl.DataFrame:
    """Persist inputs and issuance before adding to the settlement index."""
    safe = provenance.sanitized(panel, delivery_date)
    frame = issue(
        safe,
        model,
        delivery_date,
        issued_at=issued_at,
        allow_late=allow_late,
        min_train_rows=min_train_rows,
        input_as_of=input_as_of,
        policy_id=policy_id,
    )
    recording_clock = clock or ((lambda: issued_at) if issued_at is not None else now_utc)
    final = provenance.save_snapshot(
        root,
        safe,
        model,
        frame,
        clock=recording_clock,
        source_frames=source_frames,
        observed_at=observed_at,
    )
    append(final, root=root)
    return final


def append(frame: pl.DataFrame, *, root: Path = DEFAULT_ROOT) -> Path:
    """Update only settlement fields; original issuance cannot be overwritten."""
    frame = _validate(frame)
    if frame.is_empty():
        raise ValueError("cannot append an empty issue frame")
    zones = frame["zone"].unique().to_list()
    months = frame["delivery_date"].dt.strftime("%Y-%m").unique().to_list()
    if len(zones) != 1 or len(months) != 1:
        raise ValueError("append expects one zone and delivery month")
    get_zone(zones[0])  # Resolve registry values before constructing a path.
    path = Path(root) / f"zone={zones[0]}" / f"{months[0]}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = _validate(pl.read_parquet(path))
        overlap = old.join(frame.select(_KEYS), on=_KEYS, how="semi").sort(_KEYS)
        incoming = frame.join(old.select(_KEYS), on=_KEYS, how="semi").sort(_KEYS)
        if not overlap.select(IMMUTABLE).equals(incoming.select(IMMUTABLE)):
            raise ValueError("cannot replace immutable issuance fields")
        # An idempotent retry with null settlement must not erase observations.
        frame = (
            frame.join(
                old.select(
                    *_KEYS,
                    pl.col("actual").alias("_old_actual"),
                    pl.col("status").alias("_old_status"),
                ),
                on=_KEYS,
                how="left",
            )
            .with_columns(
                pl.when(pl.col("actual").is_null() & pl.col("_old_actual").is_not_null())
                .then(pl.col("_old_status"))
                .otherwise(pl.col("status"))
                .alias("status"),
                pl.coalesce("actual", "_old_actual").alias("actual"),
            )
            .select(ISSUE_SCHEMA.names())
        )
        frame = pl.concat([old.join(frame.select(_KEYS), on=_KEYS, how="anti"), frame])
    temporary = path.with_suffix(".tmp.parquet")
    frame.sort(_KEYS).write_parquet(temporary, compression="zstd", statistics=True)
    temporary.replace(path)
    return path


def read(*, root: Path = DEFAULT_ROOT, zone: str | None = None) -> pl.DataFrame:
    if zone is not None:
        get_zone(zone)
    base = Path(root)
    paths = (
        sorted((base / f"zone={zone}").glob("*.parquet"))
        if zone
        else sorted(base.glob("zone=*/*.parquet"))
    )
    paths = [path for path in paths if not path.name.endswith(".tmp.parquet")]
    if not paths:
        return pl.DataFrame(schema=ISSUE_SCHEMA)
    return pl.concat([_validate(pl.read_parquet(path)) for path in paths]).sort(_KEYS)


def reconcile(frame: pl.DataFrame, prices: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Attach outcomes without changing eligibility or immutable identity."""
    issues = _validate(frame)
    if set(issues["zone"].unique().to_list()) - {zone.code}:
        raise ValueError("reconcile expects one matching zone")
    actuals = hourly_mean(prices, zone, "price").select(
        "local_date", "local_hour", pl.col("price").alias("_observed")
    )
    return (
        issues.join(
            actuals,
            left_on=["delivery_date", "local_hour"],
            right_on=["local_date", "local_hour"],
            how="left",
        )
        .with_columns(
            pl.coalesce("_observed", "actual").alias("actual"),
        )
        .with_columns(
            pl.when(pl.col("forecast").is_null())
            .then(pl.lit("abstain_missing_inputs"))
            .when(~pl.col("eligible"))
            .then(
                pl.when(pl.col("actual").is_not_null())
                .then(pl.lit("diagnostic_scored"))
                .otherwise(pl.lit("diagnostic_waiting_for_actual"))
            )
            .when(pl.col("actual").is_null())
            .then(pl.lit("issued_waiting_for_actual"))
            .otherwise(pl.lit("scored"))
            .alias("status"),
        )
        .select(ISSUE_SCHEMA.names())
        .cast(ISSUE_SCHEMA)
    )


def canonical(frame: pl.DataFrame, *, root: Path = DEFAULT_ROOT) -> pl.DataFrame:
    """Earliest complete, verified pre-gate issue per model/day, chosen as a unit.

    Selection never uses realised outcomes or P&L. Missing/tampered evidence
    raises instead of quietly replacing the earlier run with a better one.
    """
    valid = _validate(frame).filter(pl.col("eligible") & (pl.col("protocol_version") == 2))
    candidates: list[pl.DataFrame] = []
    for group in valid.partition_by("issue_id", maintain_order=True):
        if group["forecast"].null_count():
            continue
        zone = get_zone(group["zone"][0])
        expected = delivery_grid(group["delivery_date"][0], zone)
        keys = ["local_hour", "delivery_start_utc", "delivery_duration_hours"]
        if not group.select(keys).sort("local_hour").equals(expected.select(keys)):
            continue
        _, _, _, original = provenance.read_snapshot(root, group["issue_id"][0])
        if (
            not group.select(IMMUTABLE)
            .sort("local_hour")
            .equals(original.select(IMMUTABLE).sort("local_hour"))
        ):
            raise ValueError("immutable issue index differs from snapshot")
        candidates.append(group)
    if not candidates:
        return pl.DataFrame(schema=ISSUE_SCHEMA)
    combined = pl.concat(candidates)
    chosen = (
        combined.select("zone", "model", "delivery_date", "issued_at", "issue_id")
        .unique()
        .sort("zone", "model", "delivery_date", "issued_at", "issue_id")
        .unique(["zone", "model", "delivery_date"], keep="first", maintain_order=True)
    )
    return combined.join(chosen.select("issue_id"), on="issue_id", how="semi").sort(_KEYS)


def _validate(frame: pl.DataFrame) -> pl.DataFrame:
    if missing := set(_LEGACY) - set(frame.columns):
        raise ValueError(f"issue frame is missing columns: {sorted(missing)}")
    if "protocol_version" not in frame.columns:
        frame = frame.with_columns(
            pl.lit(1).alias("protocol_version"),
            pl.lit("legacy").alias("issue_id"),
            pl.lit("{}").alias("configuration"),
            pl.col("issued_at").alias("input_as_of"),
            pl.col("issued_at").alias("recorded_at"),
            pl.lit(False).alias("eligible"),
            pl.lit("legacy_unverified").alias("eligibility_reason"),
            pl.lit(1.0).alias("delivery_duration_hours"),
        )
    frame = frame.select(ISSUE_SCHEMA.names()).cast(ISSUE_SCHEMA)
    if frame.select(pl.struct(_KEYS).is_duplicated().any()).item():
        raise ValueError("duplicate issue delivery keys")
    if frame.filter(pl.col("forecast").is_not_null() & ~pl.col("forecast").is_finite()).height:
        raise ValueError("issue forecasts must be finite")
    for group in frame.filter(pl.col("protocol_version") == 2).partition_by("issue_id"):
        metadata = [
            name
            for name in IMMUTABLE
            if name
            not in ("local_hour", "delivery_start_utc", "delivery_duration_hours", "forecast")
        ]
        if any(group[name].n_unique() != 1 or group[name].null_count() for name in metadata):
            raise ValueError("inconsistent issue metadata")
        row = group.row(0, named=True)
        reason = timing_reason(
            row["issued_at"],
            row["recorded_at"],
            row["input_as_of"],
            row["delivery_date"],
            get_zone(row["zone"]),
        )
        if row["eligible"] != (reason == "pre_gate") or row["eligibility_reason"] != reason:
            raise ValueError("issue eligibility does not match timing evidence")
        configuration = json.loads(row["configuration"])
        if row["model_version"] != f"{row['model']}:{provenance.digest(configuration)}":
            raise ValueError("model version does not match configuration")
        identity = provenance.digest(
            {
                "zone": row["zone"],
                "model_version": row["model_version"],
                "delivery_date": row["delivery_date"].isoformat(),
                "issued_at": row["issued_at"].isoformat(),
                "input_as_of": row["input_as_of"].isoformat(),
                "input_sha256": row["input_sha256"],
            }
        )
        if row["issue_id"] != identity:
            raise ValueError("issue identity does not match evidence")
    return frame
