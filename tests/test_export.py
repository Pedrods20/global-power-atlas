"""Export metadata is reproducible and does not mistake build time for data time."""

import datetime as dt
import json

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from gpa import store
from gpa.battery_study import evaluate
from gpa.calendar import hours_in_local_day
from gpa.export import (
    _BATTERY_DURATIONS_MWH,
    _BATTERY_MODEL_NAMES,
    _annual_fit_cutoff,
    _battery_competition_correlation,
    _battery_margin_yearly,
    _battery_tables,
    _cannibalisation,
    _capacity,
    _capacity_extrapolation_flags,
    _capacity_price_correlation,
    _capacity_price_yearly,
    _daily_load,
    _drop_incomplete_trailing_day,
    _forecast_page_predictions,
    _fundamentals_ablation,
    _json_values_close,
    _overview,
    export_all,
)
from gpa.zones import get_zone
from tests.test_battery import DAY, predictions
from tests.test_metrics import generation_frame, price_frame
from tests.test_pipeline import load_rows
from tests.test_store import price_rows


def test_export_check_tolerates_cross_platform_float_noise_not_real_change():
    # A fitted model's last bits can differ between a Windows workstation and
    # a Linux CI runner even with a fixed seed; that must not read as stale.
    reference = {"best_mae": 21.03558956221899, "alpha_search": [{"mae": 16.579941693983606}]}
    noisy = {"best_mae": 21.035589562219, "alpha_search": [{"mae": 16.579941693983607}]}
    assert _json_values_close(reference, noisy)
    # A real change, even a small one, must still be caught.
    changed = {"best_mae": 21.05, "alpha_search": [{"mae": 16.579941693983606}]}
    assert not _json_values_close(reference, changed)
    missing_key = {"alpha_search": [{"mae": 16.579941693983606}]}
    assert not _json_values_close(reference, missing_key)


def test_overview_depends_on_observations_not_export_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    store.write(price_rows([10.0, -20.0]), "price")
    # Price is forward-published (E lets it run up to two days ahead of "now"),
    # so a price-only store must not surface a future "as of" date.
    price_only = _overview()
    assert price_only["data_as_of"] is None
    german = next(z for z in price_only["zones"] if z["code"] == "DE-LU")
    assert "age_hours" not in german["datasets"]["price"]
    assert german["datasets"]["load"]["status"] == "pending"

    store.write(
        load_rows(
            "DE-LU",
            dt.datetime(2026, 6, 10, tzinfo=dt.UTC),
            dt.datetime(2026, 6, 10, 3, tzinfo=dt.UTC),
        ),
        "load",
    )
    first = _overview()
    second = _overview()
    assert json.dumps(first, default=str) == json.dumps(second, default=str)
    assert first["data_as_of"] == "2026-06-10T02:00:00+00:00"


def test_empty_store_has_no_data_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    assert _overview()["data_as_of"] is None


def test_market_dst_is_not_inferred_from_civil_timezone_presence():
    assert not get_zone("BR-SIN").observes_market_dst
    assert get_zone("DE-LU").observes_market_dst


def test_drop_incomplete_trailing_day_trims_a_day_still_in_progress():
    zone = get_zone("DE-LU")
    daily = pl.DataFrame(
        {
            "date": [dt.date(2026, 6, 10), dt.date(2026, 6, 11)],
            "hours_observed": [24.0, 10.0],
        }
    )
    trimmed = _drop_incomplete_trailing_day(daily, zone, "hours_observed")
    assert trimmed["date"].to_list() == [dt.date(2026, 6, 10)]


def test_drop_incomplete_trailing_day_keeps_a_genuinely_short_dst_day():
    zone = get_zone("DE-LU")
    # 2026-03-29 is DE-LU's spring-forward day: a real, complete 23-hour day.
    assert hours_in_local_day(zone, dt.date(2026, 3, 29)) == 23
    daily = pl.DataFrame(
        {
            "date": [dt.date(2026, 3, 28), dt.date(2026, 3, 29)],
            "hours_observed": [24.0, 23.0],
        }
    )
    trimmed = _drop_incomplete_trailing_day(daily, zone, "hours_observed")
    assert trimmed["date"].to_list() == [dt.date(2026, 3, 28), dt.date(2026, 3, 29)]

    # But a DST day that is itself still in progress is still trimmed.
    partial = pl.DataFrame(
        {
            "date": [dt.date(2026, 3, 28), dt.date(2026, 3, 29)],
            "hours_observed": [24.0, 14.0],
        }
    )
    assert _drop_incomplete_trailing_day(partial, zone, "hours_observed")["date"].to_list() == [
        dt.date(2026, 3, 28)
    ]


def test_daily_load_drops_a_trailing_partial_day(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    # Berlin is UTC+2 in June: start/end are chosen so the first two local
    # days are whole and only the last one is cut short mid-day.
    store.write(
        load_rows(
            "DE-LU",
            dt.datetime(2026, 6, 9, 22, tzinfo=dt.UTC),
            dt.datetime(2026, 6, 12, 4, tzinfo=dt.UTC),
        ),
        "load",
    )
    dates = _daily_load().filter(pl.col("zone") == "DE-LU")["date"].to_list()
    assert dt.date(2026, 6, 10) in dates
    assert dt.date(2026, 6, 11) in dates
    assert dt.date(2026, 6, 12) not in dates


def test_capacity_is_empty_before_anything_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    assert _capacity().is_empty()


def test_fundamentals_ablation_is_empty_before_anything_is_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    assert _fundamentals_ablation().is_empty()


def test_fundamentals_ablation_reads_the_reference_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import fundamentals_ablation as ablation_module

    ablation_module.write(
        pl.DataFrame(
            {
                "zone": ["DE-LU"],
                "model": ["ridge"],
                "include_fundamentals": [False],
                "n": [1000],
                "mae": [22.07],
                "rmse": [28.0],
                "skill_vs_best_baseline_pct": [24.3],
                "test_start": ["2020-01-03"],
                "test_end": ["2026-09-12"],
            },
            schema=ablation_module.ABLATION_COLUMNS,
        )
    )
    result = _fundamentals_ablation()
    assert result["mae"].to_list() == [22.07]


def test_capacity_tags_the_delu_zone_alongside_the_raw_country(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    capacity_module.write(
        pl.DataFrame(
            {
                "country": ["DE"],
                "time_step": ["yearly"],
                "period": ["2024"],
                "as_of": [dt.date(2024, 12, 31)],
                "technology": ["Solar DC"],
                "value": [120.0],
                "unit": ["GW"],
                "is_planned": [False],
                "source": ["energy_charts"],
            },
            schema=capacity_module.CAPACITY_COLUMNS,
        )
    )
    result = _capacity()
    assert result["zone"].to_list() == ["DE-LU"]
    assert result["country"].to_list() == ["DE"]  # the underlying scope stays visible


def test_cannibalisation_is_empty_without_stored_price_or_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    assert _cannibalisation().is_empty()


def test_cannibalisation_reports_solar_and_wind_capture_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    # Solar generates only in the cheap second half of the day, so its
    # capture rate must land below one, the cannibalisation signature.
    start = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    store.write(price_frame([100.0] * 12 + [20.0] * 12, start=start), "price")
    store.write(
        generation_frame(
            [(start + dt.timedelta(hours=h), "solar", 0.0 if h < 12 else 500.0) for h in range(24)]
        ),
        "generation",
    )
    result = _cannibalisation()
    solar = result.filter(pl.col("fuel") == "solar").row(0, named=True)
    assert solar["zone"] == "DE-LU"
    assert solar["capture_rate"] < 1.0
    assert result.filter(pl.col("fuel") == "wind").is_empty()  # no wind generation stored


def _capacity_rows(
    *, period: str, technology: str, value: float, is_planned: bool = False
) -> pl.DataFrame:
    from gpa import capacity as capacity_module

    year = int(period)
    return pl.DataFrame(
        {
            "country": ["DE"],
            "time_step": ["yearly"],
            "period": [period],
            "as_of": [dt.date(year, 12, 31)],
            "technology": [technology],
            "value": [value],
            "unit": ["GW"],
            "is_planned": [is_planned],
            "source": ["energy_charts"],
        },
        schema=capacity_module.CAPACITY_COLUMNS,
    )


def test_capacity_price_yearly_joins_capacity_onto_price_shape_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path / "reference"))
    from gpa import capacity as capacity_module

    capacity_module.write(
        pl.concat(
            [
                _capacity_rows(period="2019", technology="Solar AC", value=40.0),
                _capacity_rows(period="2019", technology="Wind onshore", value=50.0),
                _capacity_rows(period="2019", technology="Wind offshore", value=6.0),
            ]
        )
    )
    start = dt.datetime(2019, 6, 15, tzinfo=dt.UTC)
    store.write(price_frame([100.0] * 12 + [20.0] * 12, start=start), "price")
    store.write(
        generation_frame(
            [(start + dt.timedelta(hours=h), "solar", 0.0 if h < 12 else 500.0) for h in range(24)]
        ),
        "generation",
    )

    result = _capacity_price_yearly()
    row = result.row(0, named=True)
    assert row["year"] == "2019"
    assert row["solar_capacity_gw"] == 40.0
    assert row["wind_capacity_gw"] == 56.0  # onshore + offshore
    assert row["solar_capture_rate"] is not None


def test_capacity_price_yearly_drops_a_year_with_no_price_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path / "reference"))
    from gpa import capacity as capacity_module

    capacity_module.write(
        pl.concat(
            [
                _capacity_rows(period="2019", technology="Solar AC", value=40.0),
                _capacity_rows(period="2020", technology="Solar AC", value=50.0),
            ]
        )
    )
    start = dt.datetime(2019, 6, 15, tzinfo=dt.UTC)
    store.write(price_frame([50.0] * 4, start=start), "price")
    store.write(generation_frame([(start, "solar", 100.0)]), "generation")

    result = _capacity_price_yearly()
    assert result["year"].to_list() == ["2019"]  # 2020 has capacity but no price


def _yearly_fixture(
    years: list[str], solar_gw: list[float], solar_capture: list[float]
) -> pl.DataFrame:
    n = len(years)
    return pl.DataFrame(
        {
            "year": years,
            "solar_capacity_gw": solar_gw,
            "wind_capacity_gw": [30.0] * n,
            "spread_pct_of_price": [10.0] * n,
            "negative_pct": [2.0] * n,
            "solar_capture_rate": solar_capture,
            "wind_capture_rate": [0.8] * n,
        }
    )


def test_capacity_price_correlation_matches_a_known_negative_relationship():
    # Solar capacity rises while its capture rate falls in lockstep: a clean,
    # unambiguous negative correlation the coefficient must recover.
    years = [str(y) for y in range(2015, 2022)]  # none is the real current year
    yearly = _yearly_fixture(
        years,
        solar_gw=[40.0 + 10 * i for i in range(7)],
        solar_capture=[0.9 - 0.05 * i for i in range(7)],
    )
    result = _capacity_price_correlation(yearly, before_year=2022)
    row = result.filter(
        (pl.col("x") == "solar_capacity_gw") & (pl.col("y") == "solar_capture_rate")
    ).row(0, named=True)
    assert row["pearson_r"] == pytest.approx(-1.0, abs=1e-9)
    assert row["n"] == 7
    assert row["fitted_year_min"] == "2015"
    assert row["fitted_year_max"] == "2021"


def test_capacity_price_correlation_excludes_a_historical_partial_year():
    current_year = "2021"
    years = [str(y) for y in range(2015, 2021)] + [current_year]
    # The current year is a wild outlier; if it were included the fit would
    # be pulled toward it instead of the other six points' clean trend.
    solar_gw = [40.0 + 10 * i for i in range(6)] + [1000.0]
    solar_capture = [0.9 - 0.05 * i for i in range(6)] + [0.9]
    yearly = _yearly_fixture(years, solar_gw, solar_capture)

    result = _capacity_price_correlation(yearly, before_year=2021)
    row = result.filter(
        (pl.col("x") == "solar_capacity_gw") & (pl.col("y") == "solar_capture_rate")
    ).row(0, named=True)
    assert row["n"] == 6
    assert current_year not in (row["fitted_year_min"], row["fitted_year_max"])
    assert row["pearson_r"] == pytest.approx(-1.0, abs=1e-9)


def test_capacity_price_correlation_is_empty_with_fewer_than_three_points():
    yearly = _yearly_fixture(["2015", "2016"], solar_gw=[40.0, 50.0], solar_capture=[0.9, 0.8])
    assert _capacity_price_correlation(yearly, before_year=2022).is_empty()


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


def test_annual_fit_cutoff_without_observations_is_unknown():
    assert _annual_fit_cutoff(pl.DataFrame(), get_zone("DE-LU")) is None


def test_capacity_correlation_reports_only_finite_pairs_and_their_actual_years():
    yearly = _yearly_fixture(
        ["2015", "2016", "2017", "2018", "2019"],
        solar_gw=[40.0, 50.0, 60.0, 70.0, 80.0],
        solar_capture=[float("nan"), 0.9, 0.8, 0.7, float("inf")],
    )
    result = _capacity_price_correlation(yearly, before_year=2020)
    assert result.height == 1  # all other pairs contain a constant series
    row = result.row(0, named=True)
    assert row["n"] == 3
    assert row["fitted_year_min"] == "2016"
    assert row["fitted_year_max"] == "2018"
    assert row["pearson_r"] == pytest.approx(-1.0)


def test_capacity_extrapolation_flags_a_target_above_the_realised_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    capacity_module.write(
        pl.concat(
            [
                _capacity_rows(period="2024", technology="Solar AC", value=100.0),
                _capacity_rows(period="2024", technology="Solar DC", value=110.0),
                _capacity_rows(
                    period="2030",
                    technology="Solar planned (EEG 2023)",
                    value=215.0,
                    is_planned=True,
                ),
            ]
        )
    )
    result = _capacity_extrapolation_flags(before_year=2025)
    assert set(result["technology"]) == {"Solar AC", "Solar DC"}
    assert result["exceeds_realised_max"].all()
    assert result.filter(pl.col("technology") == "Solar AC")["realised_max_gw"][0] == 100.0


def test_capacity_extrapolation_does_not_flag_a_target_already_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    capacity_module.write(
        pl.concat(
            [
                _capacity_rows(period="2024", technology="Wind offshore", value=40.0),
                _capacity_rows(
                    period="2030",
                    technology="Wind offshore planned (WindSeeG)",
                    value=30.0,
                    is_planned=True,
                ),
            ]
        )
    )
    result = _capacity_extrapolation_flags(before_year=2025)
    assert result["exceeds_realised_max"].to_list() == [False]


def test_capacity_extrapolation_excludes_a_historical_partial_year_from_realised_max(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    current_year = "2024"
    capacity_module.write(
        pl.concat(
            [
                _capacity_rows(period="2023", technology="Wind offshore", value=9.0),
                # A partial current year can read as artificially low or high
                # depending on when in the year it is fetched; either way it
                # must not stand in for this technology's realised ceiling.
                _capacity_rows(period=current_year, technology="Wind offshore", value=500.0),
                _capacity_rows(
                    period="2030",
                    technology="Wind offshore planned (WindSeeG)",
                    value=30.0,
                    is_planned=True,
                ),
            ]
        )
    )
    result = _capacity_extrapolation_flags(before_year=2024)
    assert result["realised_max_gw"][0] == 9.0
    assert result["realised_max_year"][0] == "2023"


def _fleet_rows(period: str, *, power_gw: float, energy_gwh: float) -> pl.DataFrame:
    return pl.concat(
        [
            _capacity_rows(period=period, technology="Battery storage (power)", value=power_gw),
            _capacity_rows(
                period=period, technology="Battery storage (capacity)", value=energy_gwh
            ),
        ]
    )


def _monthly_row(
    *, strategy: str, energy_mwh: float, month: str, days: int, profit_eur: float
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "power_mw": 1.0,
        "energy_mwh": energy_mwh,
        "month": month,
        "days": days,
        "profit_eur": profit_eur,
        "zone": "DE-LU",
    }


def test_battery_margin_yearly_joins_the_fleet_and_normalises_to_eur_per_mw_day(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    capacity_module.write(_fleet_rows("2020", power_gw=1.5, energy_gwh=2.3))
    monthly = pl.DataFrame(
        [
            _monthly_row(
                strategy="perfect_foresight",
                energy_mwh=2.0,
                month="2020-01",
                days=31,
                profit_eur=310.0,
            ),
            _monthly_row(
                strategy="perfect_foresight",
                energy_mwh=2.0,
                month="2020-02",
                days=29,
                profit_eur=290.0,
            ),
        ]
    )
    result = _battery_margin_yearly(monthly)
    row = result.row(0, named=True)
    assert row["year"] == "2020"
    assert row["days"] == 60
    assert row["profit_eur"] == pytest.approx(600.0)
    assert row["eur_per_mw_day"] == pytest.approx(10.0)
    assert row["battery_power_gw"] == 1.5
    assert row["battery_energy_gwh"] == 2.3


def test_battery_margin_yearly_only_keeps_the_competition_strategies(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    capacity_module.write(_fleet_rows("2020", power_gw=1.5, energy_gwh=2.3))
    monthly = pl.DataFrame(
        [
            _monthly_row(
                strategy="no_trade", energy_mwh=2.0, month="2020-01", days=31, profit_eur=0.0
            ),
            _monthly_row(
                strategy="ridge", energy_mwh=2.0, month="2020-01", days=31, profit_eur=100.0
            ),
        ]
    )
    result = _battery_margin_yearly(monthly)
    assert result["strategy"].to_list() == ["ridge"]


def test_battery_margin_yearly_drops_a_year_the_fleet_has_no_data_for(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    from gpa import capacity as capacity_module

    capacity_module.write(_fleet_rows("2020", power_gw=1.5, energy_gwh=2.3))
    monthly = pl.DataFrame(
        [
            _monthly_row(
                strategy="ridge", energy_mwh=2.0, month="2020-01", days=31, profit_eur=100.0
            ),
            _monthly_row(
                strategy="ridge", energy_mwh=2.0, month="2021-01", days=31, profit_eur=200.0
            ),
        ]
    )
    assert _battery_margin_yearly(monthly)["year"].to_list() == ["2020"]


def test_battery_margin_yearly_is_empty_without_fleet_data(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    monthly = pl.DataFrame(
        [_monthly_row(strategy="ridge", energy_mwh=2.0, month="2020-01", days=31, profit_eur=100.0)]
    )
    assert _battery_margin_yearly(monthly).is_empty()


def _battery_yearly_fixture(
    years: list[str], power_gw: list[float], margin: list[float]
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "year": years,
            "strategy": ["perfect_foresight"] * len(years),
            "energy_mwh": [2.0] * len(years),
            "battery_power_gw": power_gw,
            "eur_per_mw_day": margin,
        }
    )


def test_battery_competition_correlation_matches_a_known_relationship():
    years = [str(y) for y in range(2015, 2022)]
    yearly = _battery_yearly_fixture(
        years, power_gw=[1.0 + i for i in range(7)], margin=[50.0 - 5 * i for i in range(7)]
    )
    result = _battery_competition_correlation(yearly, before_year=2022)
    row = result.filter(
        (pl.col("strategy") == "perfect_foresight") & (pl.col("energy_mwh") == 2.0)
    ).row(0, named=True)
    assert row["pearson_r"] == pytest.approx(-1.0, abs=1e-9)
    assert row["n"] == 7


def test_battery_competition_correlation_excludes_a_historical_partial_year():
    current_year = "2021"
    years = [str(y) for y in range(2015, 2021)] + [current_year]
    power_gw = [1.0 + i for i in range(6)] + [1000.0]
    margin = [50.0 - 5 * i for i in range(6)] + [50.0]
    yearly = _battery_yearly_fixture(years, power_gw, margin)

    result = _battery_competition_correlation(yearly, before_year=2021)
    row = result.row(0, named=True)
    assert row["n"] == 6
    assert current_year not in (row["fitted_year_min"], row["fitted_year_max"])


def test_battery_competition_correlation_is_empty_with_fewer_than_three_points():
    yearly = _battery_yearly_fixture(["2015", "2016"], power_gw=[1.0, 2.0], margin=[50.0, 40.0])
    assert _battery_competition_correlation(yearly, before_year=2022).is_empty()


def test_battery_correlation_reports_pair_specific_years_and_skips_constant_series():
    yearly = _battery_yearly_fixture(
        ["2015", "2016", "2017", "2018", "2019"],
        power_gw=[1.0, 2.0, 3.0, 4.0, 5.0],
        margin=[None, 30.0, 20.0, 10.0, float("inf")],
    )
    row = _battery_competition_correlation(yearly, before_year=2020).row(0, named=True)
    assert row["n"] == 3
    assert row["fitted_year_min"] == "2016"
    assert row["fitted_year_max"] == "2018"
    constant = yearly.with_columns(pl.lit(1.0).alias("battery_power_gw"))
    assert _battery_competition_correlation(constant, before_year=2020).is_empty()


def test_export_all_raises_without_a_frozen_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("gpa.forecast.snapshot.read", lambda: None)
    with pytest.raises(RuntimeError, match="snapshot"):
        export_all(tmp_path / "site-data")


def test_export_keeps_full_study_input_separate_from_browser_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path / "curated"))
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path / "reference"))
    frame = predictions(days=tuple(DAY + dt.timedelta(days=7 * i) for i in range(20)))
    frames = dict.fromkeys(("scores", "daily", "coefficients"), pl.DataFrame())
    frames["predictions"] = frame
    monkeypatch.setattr("gpa.forecast.snapshot.read", lambda: ({"zone": "DE-LU"}, frames))
    received = []

    def battery_tables(full):
        received.append(full)
        return {"battery_monthly": pl.DataFrame()}

    monkeypatch.setattr("gpa.export._battery_tables", battery_tables)
    output = tmp_path / "site-data"
    export_all(output)
    full = pl.read_parquet(output / "forecast_predictions.parquet")
    preview = pl.read_parquet(output / "forecast_preview.parquet")
    assert full.height == frame.height == received[0].height
    assert preview.height < full.height
    assert preview["local_date"].str.to_date().dt.truncate("1w").n_unique() == 12
    assert "ts_utc" not in full.columns  # retain the documented clock-hour contract
    assert_frame_equal(
        full.with_columns(pl.col("local_date").str.to_date()), received[0], check_row_order=False
    )


def test_battery_tables_match_battery_study_evaluate_bit_for_bit():
    assert set(_BATTERY_MODEL_NAMES) == {
        "ridge",
        "lightgbm",
        "naive_previous_day",
        "naive_previous_week",
        "naive_similar_day",
    }
    assert _BATTERY_DURATIONS_MWH == (1.0, 2.0, 4.0)
    frame = predictions(days=(DAY, DAY + dt.timedelta(days=1)))
    tables = _battery_tables(frame)
    expected = evaluate(
        frame, model_names=_BATTERY_MODEL_NAMES, durations_mwh=_BATTERY_DURATIONS_MWH
    )

    for name, table in (
        ("battery_summary", expected.summary),
        ("battery_risk", expected.risk),
        ("battery_comparisons", expected.comparisons),
        ("battery_coverage", expected.coverage),
    ):
        assert_frame_equal(tables[name].drop("zone"), table, check_row_order=False)


def test_battery_page_gets_monthly_margins_and_one_dispatch_day_not_every_interval():
    # Every interval of every strategy, duration and day is far more than the
    # page draws, and the browser would have to materialize all of it.
    first, second = dt.date(2025, 1, 31), dt.date(2025, 2, 1)
    frame = predictions(days=(first, second))
    tables = _battery_tables(frame)
    expected = evaluate(
        frame, model_names=_BATTERY_MODEL_NAMES, durations_mwh=_BATTERY_DURATIONS_MWH
    )
    assert "battery_dispatch" not in tables

    monthly = tables["battery_monthly"]
    assert set(monthly["month"]) == {"2025-01", "2025-02"}
    assert set(monthly["days"]) == {1}
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


def test_forecast_page_predictions_are_bounded_to_representative_weeks():
    frame = predictions(days=tuple(DAY + dt.timedelta(days=7 * i) for i in range(20)))
    bounded = _forecast_page_predictions(frame)

    assert bounded["local_date"].dt.truncate("1w").n_unique() == 12
    assert set(bounded["model"].unique()) == set(frame["model"].unique())
    assert bounded.height < frame.height


def test_battery_costs_table_covers_the_illustrative_scenarios():
    frame = predictions(days=(DAY, DAY + dt.timedelta(days=1)))
    costs = _battery_tables(frame)["battery_costs"]
    scenarios = set(
        zip(
            costs["variable_cost_eur_mwh"].to_list(),
            costs["degradation_cost_eur_mwh"].to_list(),
            strict=False,
        )
    )
    assert scenarios == {(0.0, 0.0), (2.0, 3.0), (5.0, 10.0)}

    zero_cost = costs.filter(
        (pl.col("variable_cost_eur_mwh") == 0.0) & (pl.col("degradation_cost_eur_mwh") == 0.0)
    ).select("strategy", "power_mw", "energy_mwh", "profit_eur")
    expected = evaluate(
        frame, model_names=_BATTERY_MODEL_NAMES, durations_mwh=_BATTERY_DURATIONS_MWH
    ).summary.select("strategy", "power_mw", "energy_mwh", "profit_eur")
    assert_frame_equal(
        zero_cost.sort(["strategy", "energy_mwh"]),
        expected.sort(["strategy", "energy_mwh"]),
        check_row_order=False,
    )
