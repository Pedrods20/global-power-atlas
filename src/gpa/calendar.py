"""Market calendars: local time, daylight saving, and peak/off-peak blocks.

This module exists because the single most common way to get power market
analysis wrong is to treat a timestamp as a date. Three rules are enforced here
and tested in ``tests/test_calendar.py``:

1. Every instant is stored in UTC and interpreted through the zone's *market*
   timezone. No aggregation is ever keyed on a UTC calendar day.
2. A local day has 23, 24 or 25 hours. Daily means divide by the hours that
   actually existed, not by 24.
3. On-peak is a market block defined over local hours and weekdays.
   It is never the daily maximum, and off-peak is never the daily minimum.
"""

from __future__ import annotations

import polars as pl

from gpa.zones import Zone

__all__ = ["BLOCK_OFF_PEAK", "BLOCK_ON_PEAK", "attach_block", "attach_local_time"]

BLOCK_ON_PEAK = "on_peak"
BLOCK_OFF_PEAK = "off_peak"


# --- Local time ------------------------------------------------------------


def attach_local_time(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Add market-local time columns derived from ``ts_utc``.

    Adds ``ts_local`` (the same instant rendered in market time), ``local_date``
    and ``local_hour``. These are the only keys any downstream aggregation is
    allowed to group on.

    Args:
        frame: Must contain a UTC-aware ``ts_utc`` datetime column.
        zone: Supplies the market timezone.

    Raises:
        ValueError: If ``ts_utc`` is missing or is not timezone-aware UTC.
    """
    if "ts_utc" not in frame.columns:
        raise ValueError("frame must contain a 'ts_utc' column")

    dtype = frame.schema["ts_utc"]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
        raise ValueError(
            f"'ts_utc' must be a UTC-aware Datetime, got {dtype!r}. "
            "Parse upstream timestamps to UTC before calling attach_local_time."
        )

    local = pl.col("ts_utc").dt.convert_time_zone(zone.timezone)
    return frame.with_columns(
        local.alias("ts_local"),
        local.dt.date().alias("local_date"),
        local.dt.hour().cast(pl.Int8).alias("local_hour"),
    )


# --- Blocks ----------------------------------------------------------------


def attach_block(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Label every row ``on_peak`` or ``off_peak`` per the zone's block rule.

    Requires the local-time columns from :func:`attach_local_time`, and adds
    them first if they are absent.
    """
    if "local_date" not in frame.columns or "local_hour" not in frame.columns:
        frame = attach_local_time(frame, zone)

    block = zone.peak
    in_hours = pl.col("local_hour").is_between(block.start_hour, block.end_hour, closed="left")
    in_weekdays = (
        pl.col("local_date").dt.weekday().is_in(list(block.weekdays))
        if block.weekdays != (1, 2, 3, 4, 5, 6, 7)
        else pl.lit(True)
    )

    condition = in_hours & in_weekdays

    return frame.with_columns(
        pl.when(condition)
        .then(pl.lit(BLOCK_ON_PEAK))
        .otherwise(pl.lit(BLOCK_OFF_PEAK))
        .alias("block")
    )
