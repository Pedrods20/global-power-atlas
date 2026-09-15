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
    _battery_tables,
    _daily_load,
    _drop_incomplete_trailing_day,
    _json_values_close,
    _overview,
    export_all,
)
from gpa.zones import get_zone
from tests.test_battery import DAY, predictions
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


def test_export_all_raises_without_a_frozen_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("gpa.forecast.snapshot.read", lambda: None)
    with pytest.raises(RuntimeError, match="snapshot"):
        export_all(tmp_path / "site-data")


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
        ("battery_dispatch", expected.dispatch),
        ("battery_summary", expected.summary),
        ("battery_risk", expected.risk),
        ("battery_comparisons", expected.comparisons),
        ("battery_coverage", expected.coverage),
    ):
        assert_frame_equal(tables[name].drop("zone"), table, check_row_order=False)


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
