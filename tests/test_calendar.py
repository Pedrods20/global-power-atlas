"""Market calendar tests.

These cover the three failure modes that make power market analysis silently
wrong: bucketing by UTC day, assuming every day has 24 hours, and treating
peak/off-peak as a daily maximum and minimum.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from gpa.calendar import (
    BLOCK_OFF_PEAK,
    BLOCK_ON_PEAK,
    attach_block,
    attach_local_time,
)
from gpa.zones import get_zone

GERMANY = get_zone("DE-LU")


def hourly(start: dt.datetime, hours: int) -> pl.DataFrame:
    """A frame of consecutive UTC hours, the minimum a calendar function needs."""
    return pl.DataFrame(
        {
            "ts_utc": pl.datetime_range(
                start, start + dt.timedelta(hours=hours - 1), "1h", time_zone="UTC", eager=True
            )
        }
    )


# --- Daylight saving -------------------------------------------------------


def test_fall_back_day_yields_25_distinct_local_hours() -> None:
    """The 25 hours must survive as data, not collapse onto 24 buckets."""
    frame = hourly(dt.datetime(2026, 10, 24, 12, tzinfo=dt.UTC), 48)
    local = attach_local_time(frame, GERMANY)
    counts = local.filter(pl.col("local_date") == dt.date(2026, 10, 25)).height
    assert counts == 25


# --- Local time ------------------------------------------------------------


def test_local_date_is_not_the_utc_date() -> None:
    """22:00 UTC in summer is already the next day in Berlin."""
    frame = pl.DataFrame(
        {"ts_utc": [dt.datetime(2026, 6, 15, 22, tzinfo=dt.UTC)]},
        schema={"ts_utc": pl.Datetime("us", "UTC")},
    )
    assert attach_local_time(frame, GERMANY)["local_date"][0] == dt.date(2026, 6, 16)


def test_attach_local_time_rejects_a_naive_column() -> None:
    frame = pl.DataFrame({"ts_utc": [dt.datetime(2026, 1, 1)]})
    with pytest.raises(ValueError, match="UTC-aware"):
        attach_local_time(frame, GERMANY)


def test_attach_local_time_requires_the_column() -> None:
    with pytest.raises(ValueError, match="ts_utc"):
        attach_local_time(pl.DataFrame({"other": [1]}), GERMANY)


# --- Blocks ----------------------------------------------------------------


def test_attach_block_labels_every_row() -> None:
    frame = hourly(dt.datetime(2026, 6, 1, tzinfo=dt.UTC), 24 * 14)
    blocked = attach_block(frame, GERMANY)
    assert blocked.height == frame.height
    assert set(blocked["block"].unique().to_list()) == {BLOCK_ON_PEAK, BLOCK_OFF_PEAK}
    assert blocked["block"].null_count() == 0
