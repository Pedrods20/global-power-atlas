"""Prospective forecast issuance and reconciliation.

The retrospective backtest is useful for development, but it cannot answer
whether a forecast was actually available before the market gate.  This module
keeps that operational record small and append-only: one parquet file per zone
and delivery month, with the issue timestamp, model version and input
fingerprint carried beside every forecast.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import polars as pl

from gpa.forecast.models import Model
from gpa.forecast.panel import Panel, hourly_mean
from gpa.zones import Zone

__all__ = [
    "DEFAULT_ROOT",
    "ISSUE_SCHEMA",
    "append",
    "issue",
    "market_gate",
    "read",
    "reconcile",
]

DEFAULT_ROOT: Final = Path(__file__).resolve().parents[3] / "data" / "forecast_issues"

ISSUE_SCHEMA: Final[pl.Schema] = pl.Schema(
    cast(
        Any,
        {
            "zone": pl.String,
            "model": pl.String,
            "model_version": pl.String,
            "issued_at": pl.Datetime(time_unit="us", time_zone="UTC"),
            "delivery_date": pl.Date,
            "local_hour": pl.Int8,
            "delivery_start_utc": pl.Datetime(time_unit="us", time_zone="UTC"),
            "forecast": pl.Float64,
            "actual": pl.Float64,
            "status": pl.String,
            "input_sha256": pl.String,
        },
    )
)


def market_gate(delivery_date: dt.date, zone: Zone) -> dt.datetime:
    """Return the stated noon D-1 market gate as an aware UTC instant."""
    local = dt.datetime.combine(
        delivery_date - dt.timedelta(days=1), dt.time(12), tzinfo=ZoneInfo(zone.timezone)
    )
    return local.astimezone(dt.UTC)


def issue(
    panel: Panel,
    model: Model,
    delivery_date: dt.date,
    *,
    issued_at: dt.datetime,
    model_version: str | None = None,
    allow_late: bool = False,
) -> pl.DataFrame:
    """Create a complete issue record, including explicit abstentions.

    A missing feature or insufficient history is represented by a row with a
    null forecast and ``status='abstain_missing_inputs'``.  Silently dropping
    that hour would overstate prospective coverage.
    """
    issued_at = _utc(issued_at, "issued_at")
    gate = market_gate(delivery_date, panel.zone)
    if issued_at > gate and not allow_late:
        raise ValueError(
            f"forecast was issued after the {gate.isoformat()} gate; "
            "pass allow_late=True only for a labelled diagnostic run"
        )

    delivery = panel.frame.filter(pl.col("local_date") == delivery_date).select(
        "local_date", "local_hour", "ts_utc"
    )
    if delivery.is_empty():
        raise ValueError(f"panel has no delivery grid for {delivery_date}")

    predict_day = getattr(model, "predict_day", None)
    if not callable(predict_day):
        raise ValueError(f"model {model.name!r} does not support feature-only prediction")
    forecasts = predict_day(panel, delivery_date, min_train_rows=0)
    if forecasts.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
        raise ValueError("model returned duplicate delivery keys")

    model_name = model.name
    version = model_version or _model_version(model)
    input_sha256 = _input_hash(panel, delivery_date)
    result = (
        delivery.join(forecasts, on=["local_date", "local_hour"], how="left")
        .with_columns(
            pl.lit(panel.zone.code).alias("zone"),
            pl.lit(model_name).alias("model"),
            pl.lit(version).alias("model_version"),
            pl.lit(issued_at).alias("issued_at"),
            pl.col("local_date").alias("delivery_date"),
            pl.col("ts_utc").alias("delivery_start_utc"),
            pl.when(pl.col("forecast").is_not_null())
            .then(pl.lit("issued"))
            .otherwise(pl.lit("abstain_missing_inputs"))
            .alias("status"),
            pl.lit(None, dtype=pl.Float64).alias("actual"),
            pl.lit(input_sha256).alias("input_sha256"),
        )
        .select(ISSUE_SCHEMA.names())
    )
    return result.cast(ISSUE_SCHEMA)


def append(frame: pl.DataFrame, *, root: Path = DEFAULT_ROOT) -> Path:
    """Upsert an issue frame into its zone/month partition."""
    frame = _validate(frame)
    if frame.is_empty():
        raise ValueError("cannot append an empty issue frame")
    zones = frame["zone"].unique().to_list()
    months = (
        frame.with_columns(pl.col("delivery_date").dt.strftime("%Y-%m").alias("_month"))["_month"]
        .unique()
        .to_list()
    )
    if len(zones) != 1 or len(months) != 1:
        raise ValueError("append expects one zone and delivery month at a time")

    path = Path(root) / f"zone={zones[0]}" / f"{months[0]}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    key = ["zone", "model", "issued_at", "delivery_date", "local_hour"]
    merged = (
        pl.concat([pl.read_parquet(path), frame], how="vertical_relaxed")
        if path.exists()
        else frame
    )
    merged = merged.unique(subset=key, keep="last").sort(key)
    temporary = path.with_suffix(".tmp.parquet")
    merged.write_parquet(temporary, compression="zstd", statistics=True)
    temporary.replace(path)
    return path


def read(*, root: Path = DEFAULT_ROOT, zone: str | None = None) -> pl.DataFrame:
    """Read all issue partitions, optionally restricted to one zone."""
    base = Path(root)
    paths = (
        sorted((base / f"zone={zone}").glob("*.parquet"))
        if zone
        else sorted(base.glob("zone=*/*.parquet"))
    )
    if not paths:
        return pl.DataFrame(schema=ISSUE_SCHEMA)
    return (
        pl.scan_parquet(paths).collect().sort(["zone", "delivery_date", "local_hour", "issued_at"])
    )


def reconcile(frame: pl.DataFrame, prices: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Attach observed hourly prices and score only forecasts that existed."""
    issues = _validate(frame)
    actuals = hourly_mean(prices, zone, "price").select(
        "local_date", "local_hour", pl.col("price").alias("actual")
    )
    return (
        issues.drop("actual")
        .join(
            actuals,
            left_on=["delivery_date", "local_hour"],
            right_on=["local_date", "local_hour"],
            how="left",
        )
        .with_columns(
            pl.when(pl.col("forecast").is_null())
            .then(pl.col("status"))
            .when(pl.col("actual").is_null())
            .then(pl.lit("issued_waiting_for_actual"))
            .otherwise(pl.lit("scored"))
            .alias("status")
        )
        .select(ISSUE_SCHEMA.names())
        .cast(ISSUE_SCHEMA)
    )


def _validate(frame: pl.DataFrame) -> pl.DataFrame:
    missing = set(ISSUE_SCHEMA.names()) - set(frame.columns)
    if missing:
        raise ValueError(f"issue frame is missing columns: {sorted(missing)}")
    return frame.select(ISSUE_SCHEMA.names()).cast(ISSUE_SCHEMA)


def _utc(value: dt.datetime, name: str) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(dt.UTC)


def _input_hash(panel: Panel, delivery_date: dt.date) -> str:
    frame = panel.frame.filter(pl.col("local_date") < delivery_date).sort(
        ["local_date", "local_hour"]
    )
    return hashlib.sha256(frame.write_json().encode()).hexdigest()


def _model_version(model: Model) -> str:
    return f"{type(model).__name__}:{model.name}"
