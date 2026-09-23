"""Metric tests with hand-checkable values.

Each test fixes an input whose answer can be computed on paper, so a regression
shows up as a wrong number rather than as a chart that merely looks different.
The negative-price and low-coverage cases are here because both were real bugs
in the previous version of this project.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from gpa.metrics import mix as mix_metrics
from gpa.metrics import price as price_metrics
from gpa.zones import get_zone

GERMANY = get_zone("DE-LU")


def test_negative_duration_and_block_mean_weight_mixed_resolutions():
    frame = price_frame([-100.0, 100.0], start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    frame = frame.with_columns(pl.Series("resolution_min", [60, 15], dtype=pl.Int16))
    negative = price_metrics.negative_price_summary(frame, GERMANY)
    assert negative["negative_pct"][0] == pytest.approx(80)
    blocks = price_metrics.block_prices(frame, GERMANY)
    assert blocks["all_hours"][0] == pytest.approx(-60)


def test_negative_run_breaks_at_missing_interval():
    frame = price_frame([-1.0, -2.0, -3.0], start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    frame = frame.filter(pl.col("price") != -2)
    result = price_metrics.negative_price_summary(frame, GERMANY)
    assert result["max_run_hours"][0] == 1


APPROX = pytest.approx

# Berlin runs UTC+2 in June, so a local calendar day starts at 22:00 UTC the
# day before. Tests that assert on a single local day must start there, or the
# 24 hours they build straddle two local dates and the counts stop matching.
JUNE_15_BERLIN_MIDNIGHT = dt.datetime(2026, 6, 14, 22, tzinfo=dt.UTC)


def price_frame(values: list[float], *, start: dt.datetime, resolution: int = 60) -> pl.DataFrame:
    stamps = [start + dt.timedelta(minutes=resolution * i) for i in range(len(values))]
    return pl.DataFrame(
        {
            "zone": ["DE-LU"] * len(values),
            "ts_utc": stamps,
            "resolution_min": [resolution] * len(values),
            "price": values,
            "currency": ["EUR"] * len(values),
            "source": ["test"] * len(values),
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "price": pl.Float64,
            "currency": pl.String,
            "source": pl.String,
        },
    )


def generation_frame(
    rows: list[tuple[dt.datetime, str, float]], resolution: int = 60
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "zone": ["DE-LU"] * len(rows),
            "ts_utc": [r[0] for r in rows],
            "resolution_min": [resolution] * len(rows),
            "fuel": [r[1] for r in rows],
            "gen_mw": [r[2] for r in rows],
            "source": ["test"] * len(rows),
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "fuel": pl.String,
            "gen_mw": pl.Float64,
            "source": pl.String,
        },
    )


# --- Negative prices -------------------------------------------------------


def test_negative_prices_survive_into_the_block_average() -> None:
    """Filtering price > 0 was the original bug. The average must feel the negatives."""
    frame = price_frame([-50.0] * 24, start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    blocks = price_metrics.block_prices(frame, GERMANY, period="day")
    assert blocks["all_hours"][0] == APPROX(-50.0)


def test_negative_summary_counts_hours_and_longest_run() -> None:
    # Twelve normal hours, then a five-hour unbroken negative run, then seven more.
    values = [40.0] * 12 + [-10.0, -20.0, -30.0, -15.0, -5.0] + [40.0] * 7
    frame = price_frame(values, start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    summary = price_metrics.negative_price_summary(frame, GERMANY)

    row = summary.row(0, named=True)
    assert row["n_negative"] == 5
    assert row["negative_hours"] == APPROX(5.0)
    assert row["min_price"] == APPROX(-30.0)
    assert row["mean_negative"] == APPROX(-16.0)
    assert row["max_run_hours"] == APPROX(5.0)


def test_negative_run_length_respects_sub_hourly_resolution() -> None:
    """Four consecutive 15-minute negative intervals are one hour, not four."""
    values = [20.0] * 4 + [-5.0] * 4 + [20.0] * 4
    frame = price_frame(values, start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC), resolution=15)
    summary = price_metrics.negative_price_summary(frame, GERMANY)
    assert summary["n_negative"][0] == 4
    assert summary["negative_hours"][0] == APPROX(1.0)
    assert summary["max_run_hours"][0] == APPROX(1.0)


# --- Blocks ----------------------------------------------------------------


def test_spread_is_block_difference_not_intraday_range() -> None:
    """The original project plotted max minus min and called it a peak spread.

    German peakload is 08:00-20:00 CET, which in June (CEST, UTC+2) is 06:00 to
    18:00 UTC. Here every on-peak hour is 100 and every off-peak hour is 20, so
    the block spread is exactly 80 while the intraday range is also 80 only by
    construction. The count assertions are what separate the two definitions.
    """
    start = JUNE_15_BERLIN_MIDNIGHT
    # Index i is the i-th local hour, so 08:00-20:00 local is indices 8 to 19.
    values = [100.0 if 8 <= hour < 20 else 20.0 for hour in range(24)]
    frame = price_frame(values, start=start)

    blocks = price_metrics.block_prices(frame, GERMANY, period="day")
    row = blocks.row(0, named=True)

    assert row["on_peak"] == APPROX(100.0)
    assert row["off_peak"] == APPROX(20.0)
    assert row["spread"] == APPROX(80.0)
    assert row["n_on_peak"] == 12
    assert row["n_off_peak"] == 12


def test_spread_goes_negative_under_solar_cannibalisation() -> None:
    """Midday cheaper than the shoulders is a real market state, not an error."""
    start = JUNE_15_BERLIN_MIDNIGHT
    values = [10.0 if 8 <= hour < 20 else 90.0 for hour in range(24)]
    blocks = price_metrics.block_prices(price_frame(values, start=start), GERMANY, period="day")
    assert blocks["spread"][0] == APPROX(-80.0)


# --- Mix --------------------------------------------------------


def test_generation_mix_shares_sum_to_one_hundred() -> None:
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame(
        [(moment, "wind", 300.0), (moment, "gas", 100.0), (moment, "coal", 100.0)]
    )
    result = mix_metrics.generation_mix(frame, GERMANY, period="all")

    assert result["share_pct"].sum() == APPROX(100.0)
    wind = result.filter(pl.col("fuel") == "wind").row(0, named=True)
    assert wind["share_pct"] == APPROX(60.0)


def test_interchange_is_excluded_from_the_mix() -> None:
    """Net imports are not generation and must not dilute a fuel share."""
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame(
        [(moment, "wind", 100.0), (moment, "gas", 100.0), (moment, "imports", 800.0)]
    )
    result = mix_metrics.generation_mix(frame, GERMANY, period="all")

    assert "imports" not in result["fuel"].to_list()
    assert result.filter(pl.col("fuel") == "wind")["share_pct"][0] == APPROX(50.0)


# --- Capture rate ----------------------------------------------------------


def test_capture_rate_is_generation_weighted() -> None:
    """Solar producing only in the cheap hours must show a capture rate below one.

    Prices are 100 for six hours and 20 for the next six. Solar generates only
    in the cheap block, so its capture price is 20 against a time-weighted
    average of 60, giving a rate of one third.
    """
    start = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    prices = price_frame([100.0] * 6 + [20.0] * 6, start=start)
    generation = generation_frame(
        [(start + dt.timedelta(hours=h), "solar", 0.0 if h < 6 else 500.0) for h in range(12)]
    )

    result = price_metrics.capture_rate(prices, generation, GERMANY, fuel="solar", period="all")
    row = result.row(0, named=True)

    assert row["capture_price"] == APPROX(20.0)
    assert row["baseload_price"] == APPROX(60.0)
    assert row["capture_rate"] == APPROX(1 / 3)


def test_capture_rate_is_empty_for_a_fuel_with_no_generation() -> None:
    start = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    prices = price_frame([50.0] * 4, start=start)
    generation = generation_frame([(start, "wind", 100.0)])
    result = price_metrics.capture_rate(prices, generation, GERMANY, fuel="solar")
    assert result.is_empty()


# --- Empty input -----------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda f: price_metrics.block_prices(f, GERMANY),
    ],
)
def test_price_metrics_handle_empty_input(call) -> None:  # type: ignore[no-untyped-def]
    from gpa.schema import empty_frame

    assert call(empty_frame("price")).is_empty()


# --- Shape: the two spreads that move in opposite directions ---------------


def test_intraday_range_and_block_spread_separate_under_a_midday_trough() -> None:
    """A solar-shaped day where the two spread definitions disagree in sign.

    German peakload is 08:00-20:00 CET. Build a day whose on-peak block averages
    *below* its off-peak block -- the cannibalisation signature -- while the
    high-to-low range within the day is large. A block contract loses money on
    this day; a battery makes its best day of the year on it. Both numbers are
    hand-checkable: on-peak is twelve hours averaging (6*10 + 6*40)/12 = 25,
    off-peak twelve hours at 60, and the range is 90 minus 10.
    """
    start = JUNE_15_BERLIN_MIDNIGHT
    values = [10.0 if 8 <= hour < 14 else 40.0 if 14 <= hour < 20 else 60.0 for hour in range(24)]
    values[20] = 90.0  # the evening peak, off-peak by the clock definition
    frame = price_frame(values, start=start)

    blocks = price_metrics.block_prices(frame, GERMANY, period="day").row(0, named=True)
    assert blocks["on_peak"] == APPROX(25.0)
    assert blocks["spread"] < 0

    ranges = price_metrics.intraday_spread(frame, GERMANY, period="day").row(0, named=True)
    assert ranges["mean_spread"] == APPROX(80.0)
    assert ranges["n_days"] == 1


def test_intraday_range_drops_a_partial_day_rather_than_shrinking_it() -> None:
    """A half-reported day has a genuinely smaller range and must not be averaged in.

    Including it would report falling spreads that are really a reporting gap,
    which is the failure this filter exists to prevent.
    """
    start = JUNE_15_BERLIN_MIDNIGHT
    frame = price_frame([float(hour) for hour in range(24)], start=start)
    assert price_metrics.intraday_spread(frame, GERMANY, period="day").height == 1
    assert price_metrics.intraday_spread(frame.head(12), GERMANY, period="day").is_empty()


def test_hourly_shape_is_duration_weighted_and_indexes_to_baseload() -> None:
    """Quarter-hours must not outvote hours inside the same clock hour."""
    start = JUNE_15_BERLIN_MIDNIGHT
    frame = price_frame([float(hour) for hour in range(24)], start=start)
    shape = price_metrics.hourly_shape(frame, GERMANY, period="day")
    assert shape.height == 24
    assert shape.sort("local_hour")["price"].to_list() == [float(h) for h in range(24)]

    quarters = price_frame([0.0, 0.0, 0.0, 100.0], start=start, resolution=15)
    hour = price_metrics.hourly_shape(quarters, GERMANY, period="day").row(0, named=True)
    assert hour["price"] == APPROX(25.0)
    assert hour["observed_hours"] == APPROX(1.0)
    assert hour["n_intervals"] == 4


def test_shape_metrics_reject_an_unsupported_period() -> None:
    frame = price_frame([1.0], start=JUNE_15_BERLIN_MIDNIGHT)
    for metric in (price_metrics.intraday_spread, price_metrics.hourly_shape):
        with pytest.raises(ValueError, match="period must be one of"):
            metric(frame, GERMANY, period="decade")
