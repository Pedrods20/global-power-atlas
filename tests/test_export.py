"""Site tables: reproducible, tagged, and never dated by the build clock."""

import datetime as dt

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from gpa import reference, store
from gpa.battery import DEFAULT_MODELS
from gpa.battery_study import evaluate
from gpa.export import (
    _DURATIONS_MWH,
    _annual_fit_cutoff,
    _battery_competition_correlation,
    _battery_margin_yearly,
    _battery_tables,
    _cannibalisation,
    _canonical_sort,
    _capacity_extrapolation_flags,
    _capacity_price_correlation,
    _capacity_price_yearly,
    _close,
    _data_as_of,
    _forecast_page_predictions,
    export_all,
    release_predictions,
)
from gpa.schema import empty_frame
from gpa.zones import get_zone
from tests.test_battery import DAY, predictions
from tests.test_metrics import generation_frame, price_frame
from tests.test_pipeline import load_rows
from tests.test_store import price_rows


def test_export_check_tolerates_cross_platform_float_noise_not_real_change():
    reference_run = {"best_mae": 21.03558956221899, "alpha_search": [{"mae": 16.579941693983606}]}
    noisy = {"best_mae": 21.035589562219, "alpha_search": [{"mae": 16.579941693983607}]}
    assert _close(reference_run, noisy)
    assert not _close(reference_run, {**reference_run, "best_mae": 21.05})
    assert not _close(reference_run, {"alpha_search": reference_run["alpha_search"]})


def test_data_as_of_depends_on_observations_not_export_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    assert _data_as_of() is None
    # Prices are published a day ahead, so a price-only store must not date the page.
    store.write(price_rows([10.0, -20.0]), "price")
    assert _data_as_of() is None
    start = dt.datetime(2026, 6, 10, tzinfo=dt.UTC)
    store.write(load_rows("DE-LU", start, start + dt.timedelta(hours=3)), "load")
    assert _data_as_of() == "2026-06-10T02:00:00+00:00"


def test_cannibalisation_reports_capture_below_one_for_output_in_cheap_hours():
    assert _cannibalisation(empty_frame("price"), empty_frame("generation")).is_empty()
    start = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    prices = price_frame([100.0] * 12 + [20.0] * 12, start=start)
    solar = [(start + dt.timedelta(hours=h), "solar", 0.0 if h < 12 else 500.0) for h in range(24)]
    result = _cannibalisation(prices, generation_frame(solar))
    assert result["fuel"].to_list() == ["solar"]  # no wind generation, so no wind row
    assert result["zone"].to_list() == ["DE-LU"]
    assert result["capture_rate"][0] < 1.0


def _capacity(period: str, technology: str, value: float, planned: bool = False) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "country": ["DE"],
            "time_step": ["yearly"],
            "period": [period],
            "as_of": [dt.date(int(period), 12, 31)],
            "technology": [technology],
            "value": [value],
            "unit": ["GW"],
            "is_planned": [planned],
            "source": ["energy_charts"],
        },
        schema=reference.CAPACITY_SCHEMA,
    )


def test_capacity_price_yearly_joins_capacity_onto_years_with_prices():
    capacity = pl.concat(
        [
            _capacity("2019", "Solar AC", 40.0),
            _capacity("2019", "Wind onshore", 50.0),
            _capacity("2019", "Wind offshore", 6.0),
            _capacity("2020", "Solar AC", 50.0),
        ]
    )
    start = dt.datetime(2019, 6, 15, tzinfo=dt.UTC)
    prices = price_frame([100.0] * 12 + [20.0] * 12, start=start)
    solar = [(start + dt.timedelta(hours=h), "solar", 0.0 if h < 12 else 500.0) for h in range(24)]
    result = _capacity_price_yearly(prices, generation_frame(solar), capacity)
    row = result.row(0, named=True)
    assert result["year"].to_list() == ["2019"]  # 2020 has capacity but no price
    assert (row["solar_capacity_gw"], row["wind_capacity_gw"]) == (40.0, 56.0)
    assert row["solar_capture_rate"] is not None


def _yearly(years: list[str], solar_gw: list[float], capture: list[float]) -> pl.DataFrame:
    n = len(years)
    return pl.DataFrame(
        {
            "year": years,
            "solar_capacity_gw": solar_gw,
            "wind_capacity_gw": [30.0] * n,
            "spread_pct_of_price": [10.0] * n,
            "negative_pct": [2.0] * n,
            "solar_capture_rate": capture,
            "wind_capture_rate": [0.8] * n,
        }
    )


def test_capacity_correlation_recovers_a_trend_and_excludes_the_partial_year():
    # Six clean points and a wild partial current year that must not be fitted.
    years = [str(y) for y in range(2015, 2022)]
    solar = [40.0 + 10 * i for i in range(6)] + [1000.0]
    capture = [0.9 - 0.05 * i for i in range(6)] + [0.9]
    result = _capacity_price_correlation(_yearly(years, solar, capture), 2021)
    row = result.filter(pl.col("y") == "solar_capture_rate").row(0, named=True)
    assert row["pearson_r"] == pytest.approx(-1.0, abs=1e-9)
    assert (row["n"], row["fitted_year_min"], row["fitted_year_max"]) == (6, "2015", "2020")
    assert result.height == 1  # every other pair holds a constant series
    assert _capacity_price_correlation(_yearly(years[:2], solar[:2], capture[:2]), 2022).is_empty()


def test_capacity_correlation_fits_only_finite_pairs_and_reports_their_years():
    yearly = _yearly(
        ["2015", "2016", "2017", "2018", "2019"],
        [40.0, 50.0, 60.0, 70.0, 80.0],
        [float("nan"), 0.9, 0.8, 0.7, float("inf")],
    )
    row = _capacity_price_correlation(yearly, 2020).row(0, named=True)
    assert (row["n"], row["fitted_year_min"], row["fitted_year_max"]) == (3, "2016", "2018")


@pytest.mark.parametrize(
    ("stamp", "minutes", "cutoff"),
    [
        (dt.datetime(2021, 9, 1, tzinfo=dt.UTC), 60, 2021),
        (dt.datetime(2021, 12, 31, 22, tzinfo=dt.UTC), 60, 2022),
        (dt.datetime(2021, 12, 31, 22, 45, tzinfo=dt.UTC), 15, 2022),
        (dt.datetime(2021, 12, 31, 22, 30, tzinfo=dt.UTC), 15, 2021),
    ],
)
def test_annual_fit_cutoff_uses_local_interval_end_not_wall_clock(stamp, minutes, cutoff):
    intervals = pl.DataFrame({"ts_utc": [stamp], "resolution_min": [minutes]})
    assert _annual_fit_cutoff(intervals, get_zone("DE-LU")) == cutoff
    if minutes == 60:
        assert _annual_fit_cutoff(intervals.drop("resolution_min"), get_zone("DE-LU")) == cutoff
    assert _annual_fit_cutoff(pl.DataFrame(), get_zone("DE-LU")) is None


def test_extrapolation_flags_compare_2030_targets_with_complete_years_only():
    capacity = pl.concat(
        [
            _capacity("2024", "Solar AC", 100.0),
            _capacity("2024", "Solar DC", 110.0),
            _capacity("2030", "Solar planned (EEG 2023)", 215.0, planned=True),
            _capacity("2023", "Wind offshore", 9.0),
            # A partial current year must not stand in for the realised ceiling.
            _capacity("2025", "Wind offshore", 500.0),
            _capacity("2030", "Wind offshore planned (WindSeeG)", 30.0, planned=True),
            _capacity("2024", "Wind onshore", 120.0),
            _capacity("2030", "Wind onshore planned (EEG 2023)", 115.0, planned=True),
        ]
    )
    result = _capacity_extrapolation_flags(capacity, 2025)
    flags = dict(zip(result["technology"], result["exceeds_realised_max"], strict=True))
    assert flags == {
        "Solar AC": True,
        "Solar DC": True,
        "Wind offshore": True,
        "Wind onshore": False,
    }
    wind = result.filter(pl.col("technology") == "Wind offshore").row(0, named=True)
    assert (wind["realised_max_gw"], wind["realised_max_year"]) == (9.0, "2023")


def _fleet(period: str) -> pl.DataFrame:
    return pl.concat(
        [
            _capacity(period, "Battery storage (power)", 1.5),
            _capacity(period, "Battery storage (capacity)", 2.3),
        ]
    )


def _month(strategy: str, month: str, days: int, profit: float) -> dict[str, object]:
    return {
        "strategy": strategy,
        "power_mw": 1.0,
        "energy_mwh": 2.0,
        "month": month,
        "days": days,
        "profit_eur": profit,
    }


def test_battery_margin_is_per_mw_day_beside_the_fleet_for_competition_strategies():
    monthly = pl.DataFrame(
        [
            _month("perfect_foresight", "2020-01", 31, 310.0),
            _month("perfect_foresight", "2020-02", 29, 290.0),
            _month("no_trade", "2020-01", 31, 0.0),
            _month("ridge", "2021-01", 31, 200.0),  # the fleet has no 2021 figure
        ]
    )
    result = _battery_margin_yearly(monthly, _fleet("2020"))
    row = result.row(0, named=True)
    assert result.height == 1
    assert (row["year"], row["strategy"], row["days"]) == ("2020", "perfect_foresight", 60)
    assert row["eur_per_mw_day"] == pytest.approx(10.0)
    assert (row["battery_power_gw"], row["battery_energy_gwh"]) == (1.5, 2.3)
    assert _battery_margin_yearly(monthly, _fleet("2020").clear()).is_empty()


def _margins(
    years: list[str], power: list[float | None], margin: list[float | None]
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "year": years,
            "strategy": ["perfect_foresight"] * len(years),
            "energy_mwh": [2.0] * len(years),
            "battery_power_gw": power,
            "eur_per_mw_day": margin,
        }
    )


def test_battery_competition_correlation_uses_complete_finite_years_only():
    years = [str(y) for y in range(2015, 2022)]
    power = [1.0 + i for i in range(6)] + [1000.0]
    margin = [50.0 - 5 * i for i in range(6)] + [50.0]
    row = _battery_competition_correlation(_margins(years, power, margin), 2021).row(0, named=True)
    assert row["pearson_r"] == pytest.approx(-1.0, abs=1e-9)
    assert (row["n"], row["fitted_year_max"]) == (6, "2020")
    gaps = _margins(years[:5], power[:5], [None, 30.0, 20.0, 10.0, float("inf")])
    row = _battery_competition_correlation(gaps, 2020).row(0, named=True)
    assert (row["n"], row["fitted_year_min"], row["fitted_year_max"]) == (3, "2016", "2018")
    constant = gaps.with_columns(pl.lit(1.0).alias("battery_power_gw"))
    assert _battery_competition_correlation(constant, 2020).is_empty()


def test_export_all_raises_without_a_frozen_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("gpa.forecast.snapshot.read", lambda: None)
    with pytest.raises(RuntimeError, match="snapshot"):
        export_all(tmp_path / "site-data")


def test_battery_tables_read_every_prediction_and_the_page_a_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path / "curated"))
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path / "reference"))
    frame = predictions(days=tuple(DAY + dt.timedelta(days=7 * i) for i in range(20)))
    frames = {"scores": pl.DataFrame(), "daily": pl.DataFrame(), "predictions": frame}
    monkeypatch.setattr("gpa.forecast.snapshot.read", lambda: ({"zone": "DE-LU"}, frames))
    received = []
    monkeypatch.setattr(
        "gpa.export._battery_tables",
        lambda full: received.append(full) or {"battery_monthly": pl.DataFrame()},
    )
    export_all(tmp_path / "site-data")
    preview = pl.read_parquet(tmp_path / "site-data" / "forecast_preview.parquet")
    assert received[0].height == frame.height
    assert "ts_utc" not in received[0].columns  # the documented clock-hour contract
    assert preview["local_date"].str.to_date().dt.truncate("1w").n_unique() == 12
    assert_frame_equal(release_predictions(), received[0])


def test_battery_tables_are_the_study_itself_reduced_for_the_page():
    first, second = dt.date(2025, 1, 31), dt.date(2025, 2, 1)
    frame = predictions(days=(first, second))
    tables = _battery_tables(frame)
    expected = evaluate(frame, model_names=DEFAULT_MODELS, durations_mwh=_DURATIONS_MWH)
    for name, table in (
        ("battery_summary", expected.summary),
        ("battery_risk", expected.risk),
        ("battery_comparisons", expected.comparisons),
        ("battery_coverage", expected.coverage),
    ):
        assert_frame_equal(tables[name].drop("zone"), table, check_row_order=False)
    # The page draws monthly margins and one example day, never every interval.
    monthly = tables["battery_monthly"]
    assert set(monthly["month"]) == {"2025-01", "2025-02"} and set(monthly["days"]) == {1}
    assert_frame_equal(
        monthly.group_by("strategy", "energy_mwh").agg(pl.col("profit_eur").sum()),
        expected.summary.select("strategy", "energy_mwh", "profit_eur"),
        check_row_order=False,
    )
    assert_frame_equal(
        tables["battery_dispatch_example"].drop("zone"),
        expected.dispatch.filter(pl.col("local_date") == second),
        check_row_order=False,
    )
    costs = tables["battery_costs"]
    scenarios = set(
        zip(costs["variable_cost_eur_mwh"], costs["degradation_cost_eur_mwh"], strict=True)
    )
    assert scenarios == {(0.0, 0.0), (2.0, 3.0), (5.0, 10.0)}


def test_forecast_page_predictions_are_bounded_to_representative_weeks():
    frame = predictions(days=tuple(DAY + dt.timedelta(days=7 * i) for i in range(20)))
    bounded = _forecast_page_predictions(frame)
    assert bounded["local_date"].dt.truncate("1w").n_unique() == 12
    assert set(bounded["model"]) == set(frame["model"])


def test_canonical_sort_survives_float_noise_that_would_reorder_an_all_column_sort():
    # Two scenarios agreeing to within any tolerance once swapped places between
    # runs, because an all-column sort let summation noise in a float decide.
    first = pl.DataFrame(
        {
            "strategy": ["no_trade", "no_trade", "ridge"],
            "profit_eur": [0.0, 0.0, 100.0],
            "scenario": ["two_episodes", "base", "base"],
        }
    )
    noisy = first.with_columns(pl.Series("profit_eur", [0.0, 1e-13, 100.0]))
    assert_frame_equal(_canonical_sort(first), _canonical_sort(noisy))
    assert first.sort(first.columns)["scenario"].to_list() != (
        noisy.sort(noisy.columns)["scenario"].to_list()
    )
    assert _canonical_sort(pl.DataFrame({"value": [3.0, 1.0, 2.0]}))["value"].to_list() == [
        1.0,
        2.0,
        3.0,
    ]
