"""Market-local time and peak blocks: nothing is ever keyed on a UTC calendar day.

A local day has 23, 24 or 25 hours, and on-peak is a block of local hours and
weekdays, never the daily maximum.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import polars as pl

from gpa.zones import Zone

__all__ = [
    "BLOCK_OFF_PEAK",
    "BLOCK_ON_PEAK",
    "INTERVAL_HOURS",
    "attach_block",
    "attach_local_time",
    "delivery_hours",
    "period_label",
]

BLOCK_ON_PEAK = "on_peak"
BLOCK_OFF_PEAK = "off_peak"

INTERVAL_HOURS = pl.col("resolution_min").cast(pl.Float64) / 60.0
"""A row's duration in hours: energy is power times this, never an assumed hour."""

_PERIOD_FORMATS = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y", "all": None}


def attach_local_time(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Add ``ts_local``, ``local_date`` and ``local_hour``, the only keys aggregations use."""
    dtype = frame.schema.get("ts_utc")
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
        raise ValueError(f"frame needs a UTC-aware 'ts_utc' Datetime column, got {dtype!r}")
    local = pl.col("ts_utc").dt.convert_time_zone(zone.timezone)
    return frame.with_columns(
        local.alias("ts_local"),
        local.dt.date().alias("local_date"),
        local.dt.hour().cast(pl.Int8).alias("local_hour"),
    )


def attach_block(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Label each row ``on_peak`` or ``off_peak`` by the zone's local block rule."""
    if "local_date" not in frame.columns or "local_hour" not in frame.columns:
        frame = attach_local_time(frame, zone)
    peak = zone.peak
    on_peak = pl.col("local_hour").is_between(
        peak.start_hour, peak.end_hour, closed="left"
    ) & pl.col("local_date").dt.weekday().is_in(list(peak.weekdays))
    return frame.with_columns(
        pl.when(on_peak)
        .then(pl.lit(BLOCK_ON_PEAK))
        .otherwise(pl.lit(BLOCK_OFF_PEAK))
        .alias("block")
    )


def period_label(period: str) -> pl.Expr:
    """``local_date`` as a day, month or year label, or one ``"all"`` bucket."""
    if period not in _PERIOD_FORMATS:
        raise ValueError(f"period must be one of {sorted(_PERIOD_FORMATS)}, got {period!r}")
    fmt = _PERIOD_FORMATS[period]
    return (pl.lit("all") if fmt is None else pl.col("local_date").dt.strftime(fmt)).alias("period")


def delivery_hours(day: dt.date, zone: Zone) -> pl.DataFrame:
    """The clock hours of one local delivery day; the repeated autumn hour lasts two."""
    tz = ZoneInfo(zone.timezone)
    start, stop = (
        dt.datetime.combine(d, dt.time(), tz).astimezone(dt.UTC)
        for d in (day, day + dt.timedelta(days=1))
    )
    stamps = pl.datetime_range(start, stop, interval="1h", closed="left", eager=True)
    return (
        attach_local_time(pl.DataFrame({"ts_utc": stamps}), zone)
        .group_by("local_date", "local_hour")
        .agg(
            pl.col("ts_utc").min().alias("delivery_start_utc"),
            pl.len().cast(pl.Float64).alias("delivery_duration_hours"),
        )
        .sort("local_hour")
    )
