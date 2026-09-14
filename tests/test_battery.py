"""Physical accounting and common-sample regressions for the battery study."""

from __future__ import annotations

import datetime as dt
import itertools
import math
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from gpa.battery import BatterySpec, backtest_predictions, dispatch, summarize

MODELS = ("naive_previous_day", "naive_previous_week", "naive_similar_day", "ridge", "lightgbm")
DAY = dt.date(2025, 2, 10)
TZ = ZoneInfo("Europe/Berlin")


def intervals(day=DAY, minutes=60):
    start = dt.datetime.combine(day, dt.time(), TZ).astimezone(dt.UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), TZ).astimezone(dt.UTC)
    count = int((end - start).total_seconds() / (minutes * 60))
    stamps = [start + dt.timedelta(minutes=minutes * i) for i in range(count)]
    prices = [0.0 if i < count // 2 else 100.0 for i in range(count)]
    return pl.DataFrame(
        {
            "ts_utc": stamps,
            "local_date": [day] * count,
            "local_hour": [stamp.astimezone(TZ).hour for stamp in stamps],
            "duration_hours": [minutes / 60] * count,
            "forecast": prices,
            "actual": prices,
        }
    )


def predictions(days=(DAY,), models=MODELS, minutes=60):
    return pl.concat(
        [
            intervals(day, minutes).with_columns(pl.lit(model).alias("model"))
            for day in days
            for model in models
        ]
    )


@pytest.mark.parametrize("limit", [0.125, 0.5, 0.75, 1.0])
def test_fractional_cycle_budget_is_enforced_on_battery_side(limit):
    result = dispatch(
        intervals(), BatterySpec(energy_mwh=1.0, max_cycles_per_day=limit), strategy="test"
    )
    cycles = result["battery_throughput_mwh"].sum() / 2.0
    assert cycles <= limit + 1e-9
    assert cycles == pytest.approx(math.floor(limit / 0.25) * 0.25)
    assert result["soc_mwh"][-1] == 0.0


@pytest.mark.parametrize("day,count", [(dt.date(2025, 3, 30), 23), (dt.date(2025, 10, 26), 25)])
@pytest.mark.parametrize("minutes", [60, 15])
def test_timestamped_dst_days_keep_every_physical_interval(day, count, minutes):
    result = dispatch(
        intervals(day, minutes), BatterySpec(energy_mwh=1.0, soc_step_mwh=0.05), strategy="test"
    )
    assert result.height == count * (60 // minutes)
    assert result["ts_utc"].n_unique() == result.height
    assert result["duration_hours"].sum() == pytest.approx(count)
    assert result["soc_mwh"][-1] == 0.0
    assert (result["action_mwh"].abs() <= result["duration_hours"] + 1e-9).all()


def test_clock_hour_dst_averages_are_not_physical_delivery_intervals():
    collapsed = intervals(dt.date(2025, 10, 26)).drop("ts_utc").unique("local_hour")
    result = dispatch(collapsed, BatterySpec(), strategy="test")
    assert result.is_empty()


def test_ordinary_clock_hour_input_retains_backwards_compatibility():
    result = dispatch(intervals().drop("ts_utc", "duration_hours"), BatterySpec(), strategy="test")
    assert result.height == 24
    assert result["ts_utc"][0].hour == 23  # Berlin midnight, previous UTC date.


def test_full_day_schedule_is_independent_of_actual_settlement():
    frame = intervals()
    one = dispatch(frame, BatterySpec(), strategy="test")
    two = dispatch(frame.with_columns(-pl.col("actual")), BatterySpec(), strategy="test")
    assert one["action_mwh"].to_list() == two["action_mwh"].to_list()
    assert one["profit_eur"].sum() == pytest.approx(-two["profit_eur"].sum())


def test_short_rolling_horizon_cannot_be_mislabelled_day_ahead():
    with pytest.raises(ValueError, match="full delivery day"):
        dispatch(intervals(), BatterySpec(), strategy="test", horizon_steps=12)


def test_duplicate_delivery_intervals_raise_instead_of_double_counting():
    frame = intervals()
    with pytest.raises(ValueError, match="duplicate"):
        dispatch(pl.concat([frame, frame.head(1)]), BatterySpec(), strategy="test")


def test_overlapping_intervals_raise_and_missing_intervals_exclude_the_day():
    with pytest.raises(ValueError, match="overlap"):
        dispatch(
            intervals().with_columns(pl.lit(2.0).alias("duration_hours")),
            BatterySpec(),
            strategy="test",
        )
    assert dispatch(intervals().slice(1), BatterySpec(), strategy="test").is_empty()


def test_timestamp_labels_must_match_the_market_timezone():
    with pytest.raises(ValueError, match="local"):
        dispatch(
            intervals().with_columns(pl.lit(3).alias("local_hour")), BatterySpec(), strategy="test"
        )


@pytest.mark.parametrize(
    "column,value", [("forecast", float("nan")), ("actual", float("inf")), ("duration_hours", 0.0)]
)
def test_invalid_numeric_input_is_rejected(column, value):
    with pytest.raises(ValueError):
        dispatch(
            intervals().with_columns(pl.lit(value).alias(column)), BatterySpec(), strategy="test"
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"energy_mwh": float("nan")},
        {"variable_cost_eur_mwh": float("inf")},
        {"initial_soc_mwh": float("nan")},
    ],
)
def test_nonfinite_battery_assumptions_are_rejected(kwargs):
    with pytest.raises(ValueError, match="finite"):
        BatterySpec(**kwargs)


def test_unsettled_dispatch_is_not_reported_as_zero_or_partial_profit():
    frame = intervals().with_columns(
        pl.when(pl.col("local_hour") == 23).then(None).otherwise(pl.col("actual")).alias("actual")
    )
    result = dispatch(frame, BatterySpec(), strategy="test")
    assert result.height == 24
    assert summarize(result)["profit_eur"].item() is None


def test_all_models_and_three_durations_are_default_comparators():
    result = backtest_predictions(predictions())
    assert set(result.summary["strategy"]) == {*MODELS, "no_trade", "perfect_foresight"}
    assert set(result.summary["energy_mwh"]) == {1.0, 2.0, 4.0}
    assert result.summary.height == 21
    assert result.summary["days"].unique().to_list() == [1]


def test_comparisons_use_identical_complete_days_and_report_exclusions():
    second = DAY + dt.timedelta(days=1)
    frame = predictions((DAY, second)).filter(
        ~(
            (pl.col("model") == "ridge")
            & (pl.col("local_date") == second)
            & (pl.col("local_hour") == 23)
        )
    )
    result = backtest_predictions(frame, durations_mwh=(1.0,))
    assert result.dispatch["local_date"].unique().to_list() == [DAY]
    assert result.summary["days"].unique().to_list() == [1]
    ridge = result.coverage.filter(pl.col("model") == "ridge").row(0, named=True)
    assert ridge["candidate_days"] == 2
    assert ridge["complete_days"] == 1
    assert ridge["common_days"] == 1
    assert ridge["excluded_days"] == 1


def test_missing_requested_model_does_not_silently_weaken_comparison():
    with pytest.raises(ValueError, match="missing requested models"):
        backtest_predictions(predictions(models=("ridge",)))


def test_settlement_prices_must_agree_between_models():
    frame = predictions().with_columns(
        pl.when(pl.col("model") == "ridge")
        .then(pl.col("actual") + 1)
        .otherwise(pl.col("actual"))
        .alias("actual")
    )
    with pytest.raises(ValueError, match=r"inconsistent.*actual"):
        backtest_predictions(frame)


def test_missing_settlement_excludes_day_from_every_strategy():
    frame = predictions().with_columns(
        pl.when(pl.col("local_hour") == 23).then(None).otherwise(pl.col("actual")).alias("actual")
    )
    result = backtest_predictions(frame)
    assert result.dispatch.is_empty()
    assert result.coverage["common_days"].unique().to_list() == [0]


def test_quarter_hour_duration_survives_perfect_foresight_adapter():
    result = backtest_predictions(
        predictions(models=("ridge",), minutes=15),
        model_names=("ridge",),
        durations_mwh=(1.0,),
        spec_kwargs={"soc_step_mwh": 0.05},
    )
    assert result.summary["intervals"].to_list() == [96, 96, 96]
    assert result.dispatch["duration_hours"].unique().to_list() == [0.25]
    assert result.summary.filter(pl.col("strategy") == "ridge")[
        "capture_vs_perfect"
    ].item() == pytest.approx(1.0)


def test_no_trade_preserves_nonzero_initial_soc_without_throughput():
    result = backtest_predictions(
        predictions(models=("ridge",)),
        model_names=("ridge",),
        durations_mwh=(1.0,),
        spec_kwargs={"initial_soc_mwh": 0.5},
    )
    idle = result.dispatch.filter(pl.col("strategy") == "no_trade")
    assert idle["soc_mwh"].unique().to_list() == [0.5]
    assert idle["battery_throughput_mwh"].sum() == 0.0
    assert idle["profit_eur"].sum() == 0.0
    assert result.dispatch.filter(pl.col("strategy") == "ridge")["soc_mwh"][-1] == 0.5


@pytest.mark.parametrize("initial,limit", [(0.0, 1.0), (0.0, 0.5), (1.0, 0.5)])
def test_dispatch_matches_exhaustive_feasible_schedules(initial, limit):
    spec = BatterySpec(
        energy_mwh=2.0,
        soc_step_mwh=1.0,
        initial_soc_mwh=initial,
        max_cycles_per_day=limit,
        variable_cost_eur_mwh=2.0,
        degradation_cost_eur_mwh=3.0,
    )
    frame = intervals(minutes=360).with_columns(pl.Series("forecast", [-10.0, 20.0, 80.0, 40.0]))
    frame = frame.with_columns(pl.col("forecast").alias("actual"))
    best = -math.inf
    for path in itertools.product([0.0, 1.0, 2.0], repeat=3):
        schedule = [initial, *path, initial]
        deltas = [b - a for a, b in itertools.pairwise(schedule)]
        if sum(map(abs, deltas)) > 2 * spec.energy_mwh * limit + 1e-9:
            continue
        discharged = False
        value = 0.0
        for delta, price in zip(deltas, frame["actual"], strict=True):
            if delta > 0 and discharged:
                break
            discharged |= delta < 0
            grid = (
                -delta / spec.charge_efficiency if delta > 0 else -delta * spec.discharge_efficiency
            )
            value += grid * price - abs(grid) * (
                spec.variable_cost_eur_mwh + spec.degradation_cost_eur_mwh
            )
        else:
            best = max(best, value)
    result = dispatch(frame, spec, strategy="test")
    assert result["profit_eur"].sum() == pytest.approx(best)


def test_costs_can_change_the_optimal_schedule_to_no_trade():
    free = dispatch(intervals(), BatterySpec(energy_mwh=1.0), strategy="test")
    costly = dispatch(
        intervals(), BatterySpec(energy_mwh=1.0, variable_cost_eur_mwh=100.0), strategy="test"
    )
    assert free["battery_throughput_mwh"].sum() > 0.0
    assert costly["battery_throughput_mwh"].sum() == 0.0
