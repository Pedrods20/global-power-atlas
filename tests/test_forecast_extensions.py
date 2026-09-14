"""Tests for prospective issuance, fundamentals and the upgraded challenger."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import polars as pl

from gpa import battery
from gpa.forecast import fundamentals, ledger, panel, scoring
from gpa.forecast.boosting import LightGBM, select_parameters
from gpa.forecast.models import Ridge
from gpa.sources.smard import SmardSource, _parse_series
from tests.test_forecast import ZONE, price_frame, small_panel


def test_fundamentals_use_latest_snapshot_before_the_market_gate():
    target = dt.datetime(2025, 1, 10, 2, tzinfo=dt.UTC)  # 03:00 Europe/Berlin
    before_gate = dt.datetime(2025, 1, 9, 10, tzinfo=dt.UTC)
    after_gate = dt.datetime(2025, 1, 9, 12, tzinfo=dt.UTC)
    snapshots = pl.DataFrame(
        {
            "zone": [ZONE.code, ZONE.code],
            "ts_utc": [target, target],
            "published_at": [before_gate, after_gate],
            "load_forecast_mw": [100.0, 999.0],
            "wind_forecast_mw": [20.0, 999.0],
            "solar_forecast_mw": [10.0, 999.0],
        }
    )
    panel = pl.DataFrame(
        {
            "local_date": [dt.date(2025, 1, 10)],
            "local_hour": [3],
            "ts_utc": [target],
            "price": [50.0],
        }
    )
    result = fundamentals.attach(panel, snapshots, ZONE)
    assert result["da_load_forecast"].to_list() == [100.0]
    assert result["da_residual_load_forecast"].to_list() == [70.0]


def test_smard_series_parser_and_combiner_keep_publication_vintage():
    published = dt.datetime(2026, 9, 13, 8, tzinfo=dt.UTC)
    payload = {"series": [[1757282400000, 100.5], [1757283300000, None]]}
    result = fundamentals.from_smard_series(
        payload, zone=ZONE, series="wind_onshore", published_at=published
    )
    combined = fundamentals.combine_smard_series([result])
    assert combined.height == 1
    assert combined["wind_forecast_mw"].to_list() == [100.5]
    assert combined["published_at"].item() == published


def test_issue_ledger_retains_abstentions_and_round_trips(tmp_path):
    """`small_panel` only has data for hours 3 and 14; the other 22 clock
    hours of the delivery day must survive as explicit abstentions rather
    than being dropped from the issued grid."""
    prepared = small_panel()
    delivery = dt.date(2025, 2, 10)
    issued_at = dt.datetime(2025, 2, 9, 10, tzinfo=dt.UTC)
    result = ledger.issue(prepared, Ridge(alpha=0.1), delivery, issued_at=issued_at)
    assert result.height == 24
    covered = result.filter(pl.col("local_hour").is_in([3, 14])).sort("local_hour")
    assert covered["status"].to_list() == ["issued", "issued"]
    abstained = result.filter(~pl.col("local_hour").is_in([3, 14]))
    assert abstained.height == 22
    assert set(abstained["status"].to_list()) == {"abstain_missing_inputs"}
    assert result["forecast"].null_count() == 22
    path = ledger.append(result, root=tmp_path)
    assert ledger.read(root=tmp_path).height == 24
    assert path.exists()


def test_panel_builds_a_null_target_grid_for_a_future_delivery_day():
    prices = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 24 * 30)
    delivery = dt.date(2025, 2, 1)

    prepared = panel.build_panel(prices, ZONE, delivery_date=delivery)
    future = prepared.frame.filter(pl.col("local_date") == delivery)

    assert future.height == 24
    assert future["price"].null_count() == 24
    assert (
        ledger.issue(
            prepared,
            Ridge(alpha=0.1),
            delivery,
            issued_at=dt.datetime(2025, 1, 31, 10, tzinfo=dt.UTC),
        ).height
        == 24
    )


def test_reconcile_scores_observed_hours_without_changing_issue_identity():
    """Only the two hours the model actually predicted can be scored; the
    22 abstained clock hours have no actual to attach and must stay
    abstentions rather than being silently marked scored or dropped."""
    prepared = small_panel()
    delivery = dt.date(2025, 2, 10)
    issued_at = dt.datetime(2025, 2, 9, 10, tzinfo=dt.UTC)
    issued = ledger.issue(prepared, Ridge(alpha=0.1), delivery, issued_at=issued_at)
    start = dt.datetime.combine(delivery, dt.time(), ZoneInfo(ZONE.timezone)).astimezone(dt.UTC)
    prices = pl.DataFrame(
        {
            "zone": [ZONE.code, ZONE.code],
            "ts_utc": [start + dt.timedelta(hours=3), start + dt.timedelta(hours=14)],
            "resolution_min": [60, 60],
            "price": [55.0, 65.0],
        }
    )

    reconciled = ledger.reconcile(issued, prices, ZONE)

    scored = reconciled.filter(pl.col("local_hour").is_in([3, 14])).sort("local_hour")
    assert scored["status"].to_list() == ["scored", "scored"]
    assert scored["actual"].to_list() == [55.0, 65.0]
    abstained = reconciled.filter(~pl.col("local_hour").is_in([3, 14]))
    assert set(abstained["status"].to_list()) == {"abstain_missing_inputs"}
    assert reconciled["issued_at"].unique().to_list() == [issued_at]
    assert reconciled["input_sha256"].null_count() == 0


def test_battery_dispatch_respects_power_soc_and_terminal_state():
    date = dt.date(2025, 2, 10)
    prices = [0.0 if hour == 1 else 100.0 if hour == 12 else 50.0 for hour in range(24)]
    frame = pl.DataFrame(
        {
            "local_date": [date] * 24,
            "local_hour": list(range(24)),
            "forecast": prices,
            "actual": prices,
        }
    )

    result = battery.dispatch(
        frame,
        battery.BatterySpec(energy_mwh=1.0),
        strategy="ridge",
        horizon_steps=24,
    )

    assert result.height == 24
    assert result.filter(pl.col("local_hour") == 1)["action_mwh"][0] < 0.0
    assert result.filter(pl.col("local_hour") == 12)["action_mwh"][0] > 0.0
    assert result["soc_mwh"].max() <= 1.0
    assert result["soc_mwh"].tail(1)[0] == 0.0
    assert result["profit_eur"].sum() > 0.0

    costed = battery.dispatch(
        frame,
        battery.BatterySpec(energy_mwh=1.0, degradation_cost_eur_mwh=10.0),
        strategy="ridge",
        horizon_steps=24,
    )
    assert costed["profit_eur"].sum() < result["profit_eur"].sum()


def test_battery_backtest_compares_forecast_no_trade_and_constrained_foresight():
    date = dt.date(2025, 2, 10)
    actual = [0.0 if hour == 1 else 100.0 if hour == 12 else 50.0 for hour in range(24)]
    predictions = pl.DataFrame(
        {
            "model": ["ridge"] * 24,
            "local_date": [date] * 24,
            "local_hour": list(range(24)),
            "forecast": actual,
            "actual": actual,
        }
    )

    result = battery.backtest_predictions(
        predictions,
        model_names=("ridge",),
        durations_mwh=(1.0,),
    )

    assert set(result.summary["strategy"].to_list()) == {
        "ridge",
        "no_trade",
        "perfect_foresight",
    }
    assert result.summary.filter(pl.col("strategy") == "ridge")["profit_eur"][0] > 0.0
    assert result.summary.filter(pl.col("strategy") == "no_trade")["profit_eur"][0] == 0.0
    assert result.summary.filter(pl.col("strategy") == "ridge")["capture_vs_perfect"][0] == 1.0


def test_lightgbm_quantiles_and_validation_tuning_are_walk_forward():
    prepared = small_panel()
    day = dt.date(2025, 2, 10)
    quantiles = LightGBM(num_boost_round=10).predict_day_quantiles(prepared, day, min_train_rows=20)
    assert {"forecast", "q10", "q50", "q90"}.issubset(quantiles.columns)
    chosen, search = select_parameters(
        prepared,
        validation_start=dt.date(2025, 2, 5),
        validation_end=dt.date(2025, 2, 8),
        min_train_rows=20,
        grid=({"num_leaves": 7, "learning_rate": 0.05, "min_data_in_leaf": 4, "lambda_l2": 1.0},),
    )
    assert chosen["num_leaves"] == 7
    assert search.height == 1


def test_scoreboard_adds_calendar_year_scope():
    prepared = small_panel(45).frame.with_columns(
        pl.lit("model").alias("model"),
        pl.col("price").alias("actual"),
        pl.col("price").alias("forecast"),
    )
    scores = scoring.scoreboard(
        prepared.select("model", "local_date", "local_hour", "actual", "forecast"),
        ZONE,
        reference="model",
        baselines=("model",),
        levels=(),
    )
    assert scores.filter(pl.col("scope") == "year")["bucket"].to_list() == ["2025"]


def test_smard_parser_accepts_documented_array_and_object_series():
    assert _parse_series(
        {"series": [[1_700_000_000_000, 2.0], {"value": [1_700_000_900_000, 3]}]}
    ) == [
        (1_700_000_000_000, 2.0),
        (1_700_000_900_000, 3.0),
    ]
    assert SmardSource().max_window_days == 7
