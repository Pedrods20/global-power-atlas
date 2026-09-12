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
    nerc_holidays,
)
from gpa.zones import get_zone

ERCOT = get_zone("ERCOT")
GERMANY = get_zone("DE-LU")
BRAZIL = get_zone("BR-SIN")
AUSTRALIA = get_zone("AU-NSW1")


def hourly(start: dt.datetime, hours: int) -> pl.DataFrame:
    """A frame of consecutive UTC hours, the minimum a calendar function needs."""
    return pl.DataFrame(
        {
            "ts_utc": pl.datetime_range(
                start, start + dt.timedelta(hours=hours - 1), "1h", time_zone="UTC", eager=True
            )
        }
    )


# --- NERC holidays ---------------------------------------------------------


def test_nerc_holidays_are_the_canonical_six() -> None:
    assert len(nerc_holidays(2026)) == 6


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2026, dt.date(2026, 5, 25)),
        (2027, dt.date(2027, 5, 31)),
    ],
)
def test_memorial_day_is_the_last_monday_in_may(year: int, expected: dt.date) -> None:
    assert expected in nerc_holidays(year)
    assert expected.isoweekday() == 1


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2026, dt.date(2026, 11, 26)),
        (2027, dt.date(2027, 11, 25)),
    ],
)
def test_thanksgiving_is_the_fourth_thursday_in_november(year: int, expected: dt.date) -> None:
    assert expected in nerc_holidays(year)
    assert expected.isoweekday() == 4


def test_a_sunday_holiday_is_observed_on_the_monday() -> None:
    # 2022-01-01 was a Saturday and 2023-01-01 a Sunday.
    assert dt.date(2023, 1, 2) in nerc_holidays(2023)
    assert dt.date(2023, 1, 1) not in nerc_holidays(2023)


def test_a_saturday_holiday_is_not_moved_to_the_friday() -> None:
    """NERC differs from the US federal calendar here, and the difference is real.

    The federal rule observes a Saturday holiday on the preceding Friday. NERC
    does not, so 4 July 2026, a Saturday, stays a Saturday holiday and the
    Friday before it remains an ordinary on-peak day.
    """
    assert dt.date(2026, 7, 4).isoweekday() == 6
    assert dt.date(2026, 7, 4) in nerc_holidays(2026)
    assert dt.date(2026, 7, 3) not in nerc_holidays(2026)


# --- Daylight saving -------------------------------------------------------


def test_european_spring_forward_day_has_23_hours() -> None:
    assert hours_in_local_day(GERMANY, dt.date(2026, 3, 29)) == 23


def test_european_fall_back_day_has_25_hours() -> None:
    assert hours_in_local_day(GERMANY, dt.date(2026, 10, 25)) == 25


def test_ordinary_day_has_24_hours() -> None:
    assert hours_in_local_day(GERMANY, dt.date(2026, 6, 15)) == 24


def test_nem_market_time_never_changes_length() -> None:
    """AEMO settles on AEST all year, so no NEM day is ever 23 or 25 hours.

    New South Wales civil time does shift, which is exactly why the zone carries
    a separate civil timezone. Reading market data on the civil clock would
    produce a 23-hour trading day that the market itself does not have.
    """
    civil = ZoneInfo(AUSTRALIA.civil_timezone or "Australia/Sydney")
    transition = dt.date(2026, 10, 4)

    assert hours_in_local_day(AUSTRALIA, transition) == 24

    midnight = dt.datetime.combine(transition, dt.time(0), tzinfo=civil)
    next_midnight = dt.datetime.combine(transition + dt.timedelta(days=1), dt.time(0), tzinfo=civil)
    civil_hours = (next_midnight.astimezone(dt.UTC) - midnight.astimezone(dt.UTC)).total_seconds()
    assert civil_hours / 3600 == 23


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
    """22:00 UTC is already the next day in Berlin, and still today in Chicago."""
    frame = pl.DataFrame(
        {"ts_utc": [dt.datetime(2026, 6, 15, 22, tzinfo=dt.UTC)]},
        schema={"ts_utc": pl.Datetime("us", "UTC")},
    )
    assert attach_local_time(frame, GERMANY)["local_date"][0] == dt.date(2026, 6, 16)
    assert attach_local_time(frame, ERCOT)["local_date"][0] == dt.date(2026, 6, 15)


def test_attach_local_time_rejects_a_naive_column() -> None:
    frame = pl.DataFrame({"ts_utc": [dt.datetime(2026, 1, 1)]})
    with pytest.raises(ValueError, match="UTC-aware"):
        attach_local_time(frame, GERMANY)


def test_attach_local_time_requires_the_column() -> None:
    with pytest.raises(ValueError, match="ts_utc"):
        attach_local_time(pl.DataFrame({"other": [1]}), GERMANY)


# --- Blocks ----------------------------------------------------------------


def test_nerc_block_covers_saturday_but_not_sunday() -> None:
    """A five-weekday assumption is wrong for North America and costs money."""
    saturday_noon = dt.datetime(2026, 6, 13, 17, tzinfo=dt.UTC)  # 12:00 Chicago
    sunday_noon = dt.datetime(2026, 6, 14, 17, tzinfo=dt.UTC)
    assert saturday_noon.astimezone(ZoneInfo(ERCOT.timezone)).isoweekday() == 6
    assert is_on_peak(ERCOT, saturday_noon) is True
    assert is_on_peak(ERCOT, sunday_noon) is False


def test_european_block_excludes_saturday() -> None:
    saturday_noon = dt.datetime(2026, 6, 13, 10, tzinfo=dt.UTC)  # 12:00 Berlin
    assert is_on_peak(GERMANY, saturday_noon) is False


def test_european_block_ignores_holidays() -> None:
    """Unlike NERC, EEX peakload has no holiday carve-out. Christmas 2026 is a Friday."""
    christmas_noon = dt.datetime(2026, 12, 25, 11, tzinfo=dt.UTC)  # 12:00 Berlin
    assert christmas_noon.astimezone(ZoneInfo(GERMANY.timezone)).isoweekday() == 5
    assert is_on_peak(GERMANY, christmas_noon) is True


def test_nerc_holiday_is_fully_off_peak() -> None:
    frame = hourly(dt.datetime(2026, 7, 4, 5, tzinfo=dt.UTC), 24)
    blocked = attach_block(frame, ERCOT)
    july4 = blocked.filter(pl.col("local_date") == dt.date(2026, 7, 4))
    assert july4.height == 24
    assert july4["block"].unique().to_list() == [BLOCK_OFF_PEAK]


def test_block_boundaries_follow_hour_ending_convention() -> None:
    """NERC on-peak is HE0700 to HE2200, so hours beginning 06:00 through 21:00."""
    tz = ZoneInfo(ERCOT.timezone)
    wednesday = dt.date(2026, 6, 17)

    def local_hour(hour: int) -> dt.datetime:
        return dt.datetime.combine(wednesday, dt.time(hour), tzinfo=tz)

    assert is_on_peak(ERCOT, local_hour(5)) is False
    assert is_on_peak(ERCOT, local_hour(6)) is True
    assert is_on_peak(ERCOT, local_hour(21)) is True
    assert is_on_peak(ERCOT, local_hour(22)) is False


def test_attach_block_labels_every_row() -> None:
    frame = hourly(dt.datetime(2026, 6, 1, tzinfo=dt.UTC), 24 * 14)
    blocked = attach_block(frame, ERCOT)
    assert blocked.height == frame.height
    assert set(blocked["block"].unique().to_list()) == {BLOCK_ON_PEAK, BLOCK_OFF_PEAK}
    assert blocked["block"].null_count() == 0


def test_is_on_peak_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_on_peak(ERCOT, dt.datetime(2026, 6, 17, 12))
