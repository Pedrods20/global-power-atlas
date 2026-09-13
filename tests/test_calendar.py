"""Market calendar tests.

These cover the three failure modes that make power market analysis silently
wrong: bucketing by UTC day, assuming every day has 24 hours, and treating
peak/off-peak as a daily maximum and minimum.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from gpa.calendar import (
    BLOCK_OFF_PEAK,
    BLOCK_ON_PEAK,
    attach_block,
    attach_local_time,
    hours_in_local_day,
    is_on_peak,
)
from gpa.zones import get_zone

GERMANY = get_zone("DE-LU")
BRAZIL = get_zone("BR-SIN")


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


def test_european_spring_forward_day_has_23_hours() -> None:
    assert hours_in_local_day(GERMANY, dt.date(2026, 3, 29)) == 23


def test_european_fall_back_day_has_25_hours() -> None:
    assert hours_in_local_day(GERMANY, dt.date(2026, 10, 25)) == 25


def test_ordinary_day_has_24_hours() -> None:
    assert hours_in_local_day(GERMANY, dt.date(2026, 6, 15)) == 24


def test_brazil_has_no_daylight_saving_since_2019() -> None:
    for day in (dt.date(2026, 2, 15), dt.date(2026, 10, 18), dt.date(2026, 11, 1)):
        assert hours_in_local_day(BRAZIL, day) == 24


def test_fall_back_day_yields_25_distinct_local_hours() -> None:
    """The 25 hours must survive as data, not collapse onto 24 buckets."""
    frame = hourly(dt.datetime(2026, 10, 24, 12, tzinfo=dt.UTC), 48)
    local = attach_local_time(frame, GERMANY)
    counts = local.filter(pl.col("local_date") == dt.date(2026, 10, 25)).height
    assert counts == 25


# --- Local time ------------------------------------------------------------


def test_local_date_is_not_the_utc_date() -> None:
    """22:00 UTC is already the next day in Berlin, and still today in Sao Paulo."""
    frame = pl.DataFrame(
        {"ts_utc": [dt.datetime(2026, 6, 15, 22, tzinfo=dt.UTC)]},
        schema={"ts_utc": pl.Datetime("us", "UTC")},
    )
    assert attach_local_time(frame, GERMANY)["local_date"][0] == dt.date(2026, 6, 16)
    assert attach_local_time(frame, BRAZIL)["local_date"][0] == dt.date(2026, 6, 15)


def test_attach_local_time_rejects_a_naive_column() -> None:
    frame = pl.DataFrame({"ts_utc": [dt.datetime(2026, 1, 1)]})
    with pytest.raises(ValueError, match="UTC-aware"):
        attach_local_time(frame, GERMANY)


def test_attach_local_time_requires_the_column() -> None:
    with pytest.raises(ValueError, match="ts_utc"):
        attach_local_time(pl.DataFrame({"other": [1]}), GERMANY)


# --- Blocks ----------------------------------------------------------------


def test_european_block_excludes_saturday() -> None:
    saturday_noon = dt.datetime(2026, 6, 13, 10, tzinfo=dt.UTC)  # 12:00 Berlin
    assert is_on_peak(GERMANY, saturday_noon) is False


def test_european_block_ignores_holidays() -> None:
    """EEX peakload has no holiday carve-out. Christmas 2026 is a Friday."""
    christmas_noon = dt.datetime(2026, 12, 25, 11, tzinfo=dt.UTC)  # 12:00 Berlin
    assert christmas_noon.astimezone(ZoneInfo(GERMANY.timezone)).isoweekday() == 5
    assert is_on_peak(GERMANY, christmas_noon) is True


def test_block_boundaries_follow_hour_ending_convention() -> None:
    """European peakload is 08:00 to 20:00, so hours beginning 08:00 through 19:00."""
    tz = ZoneInfo(GERMANY.timezone)
    wednesday = dt.date(2026, 6, 17)

    def local_hour(hour: int) -> dt.datetime:
        return dt.datetime.combine(wednesday, dt.time(hour), tzinfo=tz)

    assert is_on_peak(GERMANY, local_hour(7)) is False
    assert is_on_peak(GERMANY, local_hour(8)) is True
    assert is_on_peak(GERMANY, local_hour(19)) is True
    assert is_on_peak(GERMANY, local_hour(20)) is False


def test_attach_block_labels_every_row() -> None:
    frame = hourly(dt.datetime(2026, 6, 1, tzinfo=dt.UTC), 24 * 14)
    blocked = attach_block(frame, GERMANY)
    assert blocked.height == frame.height
    assert set(blocked["block"].unique().to_list()) == {BLOCK_ON_PEAK, BLOCK_OFF_PEAK}
    assert blocked["block"].null_count() == 0


def test_is_on_peak_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_on_peak(GERMANY, dt.datetime(2026, 6, 17, 12))
