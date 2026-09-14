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

from gpa.metrics import load as load_metrics
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


def load_frame(values: list[float], *, start: dt.datetime, resolution: int = 60) -> pl.DataFrame:
    stamps = [start + dt.timedelta(minutes=resolution * i) for i in range(len(values))]
    return pl.DataFrame(
        {
            "zone": ["DE-LU"] * len(values),
            "ts_utc": stamps,
            "resolution_min": [resolution] * len(values),
            "load_mw": values,
            "source": ["test"] * len(values),
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "load_mw": pl.Float64,
            "source": pl.String,
        },
    )


# --- Negative prices -------------------------------------------------------


def test_negative_prices_survive_into_the_block_average() -> None:
    """Filtering price > 0 was the original bug. The average must feel the negatives."""
    frame = price_frame([-50.0] * 24, start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    blocks = price_metrics.block_prices(frame, GERMANY, period="day")
    assert blocks["all_hours"][0] == APPROX(-50.0)


def test_volatility_is_finite_when_prices_go_negative() -> None:
    """A log return would be undefined here; an arithmetic difference is not."""
    # Twenty days of hourly prices so the rolling window has something to fill.
    values = [10.0, -20.0, 0.0, 45.0, -5.0, 30.0] * 80
    frame = price_frame(values, start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    result = price_metrics.realised_volatility(frame, GERMANY, window=3)
    finite = result["volatility"].drop_nulls()
    assert finite.len() > 0
    assert finite.is_finite().all()
    assert not finite.is_nan().any()


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


# --- Volatility ------------------------------------------------------------


def test_volatility_annualises_on_365_days() -> None:
    """252 is the trading-day convention and understates power volatility by ~20 percent."""
    values: list[float] = []
    for day in range(40):
        values.extend([50.0 + (10.0 if day % 2 else -10.0)] * 24)
    frame = price_frame(values, start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))

    annualised = price_metrics.realised_volatility(frame, GERMANY, window=10)
    plain = price_metrics.realised_volatility(frame, GERMANY, window=10, annualise=False)

    ratio = annualised["volatility"].drop_nulls()[0] / plain["volatility"].drop_nulls()[0]
    assert ratio == APPROX(365**0.5, rel=1e-9)
    assert price_metrics.DAYS_PER_YEAR == 365


# --- Duration curves -------------------------------------------------------


def test_price_duration_curve_is_monotonic_and_spans_full_exceedance() -> None:
    frame = price_frame(
        [5.0, 90.0, -10.0, 40.0, 120.0], start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    )
    curve = price_metrics.duration_curve(frame)

    assert curve["price"].to_list() == [120.0, 90.0, 40.0, 5.0, -10.0]
    assert curve["exceedance_pct"][0] == APPROX(20.0)
    assert curve["exceedance_pct"][-1] == APPROX(100.0)


def test_load_duration_curve_orders_descending() -> None:
    frame = load_frame([100.0, 300.0, 200.0], start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    curve = load_metrics.duration_curve(frame)
    assert curve["load_mw"].to_list() == [300.0, 200.0, 100.0]


# --- Load ------------------------------------------------------------------


def test_load_factor_matches_hand_calculation() -> None:
    # Mean of 12 hours at 50 and 12 at 100 is 75; peak is 100; factor is 0.75.
    values = [50.0] * 12 + [100.0] * 12
    frame = load_frame(values, start=JUNE_15_BERLIN_MIDNIGHT)
    result = load_metrics.load_factor(frame, GERMANY, period="day")

    row = result.row(0, named=True)
    assert row["avg_load_mw"] == APPROX(75.0)
    assert row["peak_load_mw"] == APPROX(100.0)
    assert row["load_factor"] == APPROX(0.75)


def test_daily_energy_integrates_by_interval_not_by_assumed_hour() -> None:
    """96 quarter-hours at 100 MW is 2400 MWh, not 9600."""
    frame = load_frame(
        [100.0] * 96, start=dt.datetime(2026, 6, 14, 22, tzinfo=dt.UTC), resolution=15
    )
    energy = load_metrics.daily_energy(frame, GERMANY)
    assert energy["energy_mwh"].sum() == APPROX(2400.0)
    assert energy["hours_observed"].sum() == APPROX(24.0)


def test_load_factor_rejects_an_unknown_period() -> None:
    frame = load_frame([1.0, 2.0], start=dt.datetime(2026, 6, 15, tzinfo=dt.UTC))
    with pytest.raises(ValueError, match="period must be one of"):
        load_metrics.load_factor(frame, GERMANY, period="fortnight")


# --- Mix and carbon --------------------------------------------------------


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


def test_renewable_share_excludes_pumped_storage_and_waste() -> None:
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame(
        [
            (moment, "wind", 250.0),
            (moment, "solar", 250.0),
            (moment, "hydro_pumped_storage", 250.0),
            (moment, "waste", 250.0),
        ]
    )
    result = mix_metrics.renewable_share(frame, GERMANY, period="all")
    assert result["renewable_pct"][0] == APPROX(50.0)


def test_carbon_intensity_matches_hand_calculation() -> None:
    """Half coal at 950 and half wind at 0 is 475 g/kWh on the operational basis."""
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame([(moment, "coal", 500.0), (moment, "wind", 500.0)])

    result = mix_metrics.carbon_intensity(frame, GERMANY, basis="operational", period="all")
    row = result.row(0, named=True)

    assert row["intensity_g_per_kwh"] == APPROX(475.0)
    assert row["coverage_pct"] == APPROX(100.0)


def test_operational_and_lifecycle_bases_differ_for_nuclear() -> None:
    """Reporting one while labelling it the other is the common carbon-figure error."""
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame([(moment, "nuclear", 1000.0)])

    operational = mix_metrics.carbon_intensity(frame, GERMANY, basis="operational", period="all")
    lifecycle = mix_metrics.carbon_intensity(frame, GERMANY, basis="lifecycle", period="all")

    assert operational["intensity_g_per_kwh"][0] == APPROX(0.0)
    assert lifecycle["intensity_g_per_kwh"][0] == APPROX(12.0)


def test_carbon_intensity_is_withheld_at_brazils_actual_coverage() -> None:
    """87 percent coverage still reports zero, so the threshold must reject it.

    This is the real Brazilian case, not a contrived one. ONS publishes hydro,
    wind and solar separately and folds every thermal unit into one aggregate
    column, so the covered 87 percent is entirely zero-carbon and the uncovered
    13 percent is the entire emitting fleet. Renormalising over the remainder
    reports 0 g/kWh for a system that is not carbon free.

    A looser threshold, such as 80 percent, lets this through. That is why the
    default is 95.
    """
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame(
        [
            (moment, "hydro", 700.0),
            (moment, "wind", 120.0),
            (moment, "solar", 54.0),
            (moment, "other", 126.0),
        ]
    )

    result = mix_metrics.carbon_intensity(frame, GERMANY, basis="operational", period="all")
    row = result.row(0, named=True)

    assert row["coverage_pct"] == APPROX(87.4, abs=0.1)
    assert row["intensity_g_per_kwh"] is None, (
        "87% coverage of exclusively zero-carbon fuels must not publish an intensity"
    )


def test_carbon_intensity_is_withheld_when_fuel_coverage_is_poor() -> None:
    """The Brazil case: an unresolved thermal block must not be silently dropped.

    Renormalising over the clean remainder would report roughly zero here, which
    is exactly the bias the previous implementation shipped. Returning null and
    publishing the coverage is the honest answer.
    """
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame([(moment, "hydro", 600.0), (moment, "other", 400.0)])

    result = mix_metrics.carbon_intensity(frame, GERMANY, basis="operational", period="all")
    row = result.row(0, named=True)

    assert row["coverage_pct"] == APPROX(60.0)
    assert row["intensity_g_per_kwh"] is None
    assert row["uncovered_mwh"] == APPROX(400.0)


def test_carbon_intensity_handles_a_factored_fuel_with_negative_energy() -> None:
    """Pumped storage that consumed more than it produced must not break the sum.

    It has a known emission factor and negative net energy, and an earlier
    implementation raised a shape error on exactly that combination. The
    negative period must also not subtract emissions the fleet never avoided,
    so it is excluded from both the numerator and the denominator.
    """
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame(
        [
            (moment, "coal", 500.0),
            (moment, "wind", 500.0),
            (moment, "hydro_pumped_storage", -100.0),
        ]
    )

    result = mix_metrics.carbon_intensity(frame, GERMANY, basis="operational", period="all")
    row = result.row(0, named=True)

    assert row["intensity_g_per_kwh"] == APPROX(475.0)
    assert row["coverage_pct"] == APPROX(100.0)


def test_carbon_intensity_rejects_an_unknown_basis() -> None:
    moment = dt.datetime(2026, 6, 15, 12, tzinfo=dt.UTC)
    frame = generation_frame([(moment, "coal", 1.0)])
    with pytest.raises(KeyError, match="unknown basis"):
        mix_metrics.carbon_intensity(frame, GERMANY, basis="guess")  # type: ignore[arg-type]


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
