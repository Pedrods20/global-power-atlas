"""Fixed stresses preserve observations, physical keys and the sample denominator."""

import datetime as dt

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from gpa.battery_sensitivity import (
    OUTAGE_ANCHOR,
    SCENARIOS,
    calendar_outages,
    scenario_tables,
    weaken_signal,
)
from gpa.battery_study import evaluate
from tests.test_battery import DAY, predictions


@pytest.mark.parametrize("timestamped", [True, False])
def test_signal_uses_only_previous_day_forecasts_and_preserves_naives(timestamped):
    frame = predictions().with_columns(
        pl.when(pl.col("model").is_in(["ridge", "lightgbm"]))
        .then(pl.col("forecast") + 20)
        .otherwise(pl.col("forecast"))
        .alias("forecast")
    )
    if not timestamped:
        frame = frame.drop("ts_utc")
    keys = ["model", "local_date", "local_hour"]
    weakened = weaken_signal(frame, 0.5).sort(keys)
    expected = frame.with_columns(
        pl.when(pl.col("model").is_in(["ridge", "lightgbm"]))
        .then(pl.col("forecast") - 10)
        .otherwise(pl.col("forecast"))
        .alias("forecast")
    ).sort(keys)
    assert_frame_equal(weakened, expected)
    alternate = weaken_signal(frame.with_columns(pl.lit(-9999.0).alias("actual")), 0.5)
    assert_frame_equal(alternate.sort(keys).drop("actual"), weakened.drop("actual"))
    assert_frame_equal(weaken_signal(frame, 1).sort(keys), frame.sort(keys))


@pytest.mark.parametrize("strength", [-1, 2, float("nan"), float("inf")])
def test_signal_rejects_invalid_strength(strength):
    with pytest.raises(ValueError, match="retained_signal"):
        weaken_signal(predictions(), strength)


def test_missing_baseline_is_not_filled_and_repeated_dst_hours_are_not_merged():
    frame = predictions(days=(dt.date(2025, 10, 26),))
    assert weaken_signal(frame, 0.5).height == frame.height
    with pytest.raises(ValueError, match="previous-day"):
        weaken_signal(frame.filter(pl.col("model") != "naive_previous_day"), 0.5)
    missing = frame.with_columns(
        pl.when((pl.col("model") == "naive_previous_day") & (pl.col("local_hour") == 2))
        .then(None)
        .otherwise(pl.col("forecast"))
        .alias("forecast")
    )
    assert (
        weaken_signal(missing, 0.5).filter(pl.col("model") == "ridge")["forecast"].null_count() == 2
    )


def test_calendar_outage_is_shared_and_keeps_actuals_days_and_balance():
    days = (OUTAGE_ANCHOR + dt.timedelta(days=40), OUTAGE_ANCHOR + dt.timedelta(days=41))
    result = evaluate(predictions(days=days), durations_mwh=(1.0,))
    stressed = calendar_outages(result.dispatch)
    assert_frame_equal(
        stressed.select("actual", "forecast", "ts_utc"),
        result.dispatch.select("actual", "forecast", "ts_utc"),
    )
    outage = stressed.filter(pl.col("local_date") == days[0])
    for column in ("profit_eur", "action_mwh", "soc_mwh", "battery_throughput_mwh"):
        assert outage[column].abs().sum() == 0
    assert_frame_equal(
        stressed.filter(pl.col("local_date") == days[1]),
        result.dispatch.filter(pl.col("local_date") == days[1]),
    )
    with pytest.raises(ValueError, match="positive"):
        calendar_outages(result.dispatch, 0)


def test_scenarios_keep_all_comparators_and_rerun_costs_on_the_same_days():
    frame = predictions(days=(DAY, DAY + dt.timedelta(days=1)))
    base = evaluate(frame, durations_mwh=(1.0,))
    costs, stresses = scenario_tables(frame, base, durations_mwh=(1.0,))
    # Derived from the registry rather than hardcoded, so adding a stress does
    # not fail this test for a reason that has nothing to do with what it checks.
    strategies = base.summary.height
    assert costs.height == 3 * strategies
    assert stresses.height == len(SCENARIOS) * strategies
    assert set(stresses["days"]) == {2}
    assert set(stresses["available_days"] + stresses["unavailable_days"]) == {2}
    assert stresses.filter(pl.col("scenario") == "base")["profit_eur"].sum() == pytest.approx(
        base.summary["profit_eur"].sum()
    )
    assert all(stresses.filter(pl.col("strategy") == "no_trade")["profit_eur"] == 0)
    assert costs["operating_cost_eur"].max() > 0
    assert costs["degradation_cost_eur"].max() > 0
    for row in costs.to_dicts():
        assert row["profit_eur"] == pytest.approx(
            row["gross_revenue_eur"] - row["operating_cost_eur"] - row["degradation_cost_eur"]
        )
    # All models are identical in this fixture, even after attenuation.
    ridge = stresses.filter(pl.col("strategy") == "ridge")
    assert ridge["incremental_vs_best_naive_eur_mw"].abs().max() < 1e-9


def test_a_second_episode_can_only_add_margin_and_leaves_one_episode_untouched():
    """The published benchmark must be bit-identical to the one-episode optimum.

    A wider feasible set cannot pay less, so a two-episode day is bounded below
    by the one-episode day it contains. If this ever fails, the phase transition
    is dropping a schedule rather than adding one.
    """
    frame = predictions(days=(DAY, DAY + dt.timedelta(days=1)))
    base = evaluate(frame, durations_mwh=(1.0,))
    _, stresses = scenario_tables(frame, base, durations_mwh=(1.0,))
    one = stresses.filter(pl.col("scenario") == "base").sort("strategy")
    two = stresses.filter(pl.col("scenario") == "two_episodes").sort("strategy")
    assert one.height == two.height
    assert set(one["episodes_per_day"]) == {1}
    assert set(two["episodes_per_day"]) == {2}
    for single, double in zip(one["profit_eur"], two["profit_eur"], strict=True):
        assert double >= single - 1e-9


def test_sensitivity_rejects_sample_drift():
    frame = predictions(days=(DAY, DAY + dt.timedelta(days=1)))
    wrong = evaluate(frame.filter(pl.col("local_date") == DAY), durations_mwh=(1.0,))
    with pytest.raises(ValueError, match="sample"):
        scenario_tables(frame, wrong, durations_mwh=(1.0,))
