"""Forecast acceptance tests: time availability, missing data and numerical oracles."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from gpa.forecast import backtest, linalg, models, panel, scoring
from gpa.forecast.boosting import LightGBM
from gpa.zones import get_zone

ZONE = get_zone("DE-LU")


def price_frame(start: dt.datetime, hours: int, minutes: int = 60) -> pl.DataFrame:
    stamps = [start + dt.timedelta(minutes=minutes * i) for i in range(hours * 60 // minutes)]
    return pl.DataFrame(
        {
            "zone": [ZONE.code] * len(stamps),
            "ts_utc": stamps,
            "resolution_min": [minutes] * len(stamps),
            "price": [float(i % 73 - 20) for i in range(len(stamps))],
        }
    )


def small_panel(days: int = 45) -> panel.Panel:
    """Synthetic nonlinear data; two clock hours and an explicitly known input."""
    rows = []
    for day in range(days):
        for hour in (3, 14):
            date = dt.date(2025, 1, 1) + dt.timedelta(days=day)
            x = float((day * 7 + hour) % 19 - 9)
            rows.append(
                {
                    "local_date": date,
                    "local_hour": hour,
                    "ts_utc": dt.datetime.combine(date, dt.time(hour), dt.UTC),
                    "x": x,
                    "price": x * x - 10 + day / 20,
                    "price_d1": float(day),
                    "price_d7": float(day - 7),
                    "price_similar_day": float(day - 2),
                }
            )
    frame = pl.DataFrame(rows).with_columns(pl.col("local_hour").cast(pl.Int8))
    return panel.Panel(ZONE, frame, ("x",))


def test_hourly_mean_preserves_negative_prices_and_weights_duration():
    frame = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 1, 15)
    frame = frame.with_columns(pl.Series("price", [-40.0, 0.0, 40.0, 80.0]))
    observed = panel.hourly_mean(frame, ZONE, "price")
    assert observed["price"].to_list() == [20.0]
    assert observed["covered_hours"].to_list() == [1.0]


@pytest.mark.parametrize("fault", ["gap", "duplicate", "null", "overlap", "wrong_resolution"])
def test_incomplete_or_overlapping_hours_are_not_published(fault):
    frame = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 1, 15)
    if fault == "gap":
        frame = frame.head(3)
    elif fault == "duplicate":
        frame = pl.concat([frame, frame.head(1)])
    elif fault == "null":
        frame = frame.with_columns(pl.Series("price", [None, 1.0, 2.0, 3.0]))
    elif fault == "overlap":
        frame = frame.with_columns(pl.Series("resolution_min", [30, 15, 15, 15]))
    else:
        frame = frame.head(1)
    assert panel.hourly_mean(frame, ZONE, "price").is_empty()


@pytest.mark.parametrize(
    "date,hours,cells", [(dt.date(2025, 3, 30), 23, 23), (dt.date(2025, 10, 26), 25, 24)]
)
def test_dst_day_duration_and_daily_mean(date, hours, cells):
    start = dt.datetime.combine(date, dt.time(), ZoneInfo(ZONE.timezone)).astimezone(dt.UTC)
    frame = price_frame(start, hours + 24)
    observed = panel.hourly_mean(frame, ZONE, "price").filter(pl.col("local_date") == date)
    assert observed.height == cells
    assert observed["covered_hours"].sum() == hours
    following = panel.build_panel(frame, ZONE).frame.filter(
        pl.col("local_date") == date + dt.timedelta(days=1)
    )
    assert following["price_d1_mean"][0] == pytest.approx(frame.head(hours)["price"].mean())


def test_23_observations_on_an_ordinary_day_do_not_make_a_complete_day():
    frame = price_frame(dt.datetime(2025, 1, 1, 23, tzinfo=dt.UTC), 48)
    frame = frame.filter(pl.col("ts_utc") != dt.datetime(2025, 1, 2, 12, tzinfo=dt.UTC))
    following = panel.build_panel(frame, ZONE).frame.filter(
        pl.col("local_date") == dt.date(2025, 1, 3)
    )
    assert following["price_d1_mean"].null_count() == following.height


@pytest.mark.parametrize("missing_hour", [0, 1])
def test_autumn_repeated_hour_requires_both_delivery_intervals(missing_hour):
    frame = price_frame(dt.datetime(2025, 10, 26, tzinfo=dt.UTC), 3)
    frame = frame.filter(pl.col("ts_utc").dt.hour() != missing_hour)
    observed = panel.hourly_mean(frame, ZONE, "price")
    assert 2 not in observed["local_hour"].to_list()
    assert observed["local_hour"].to_list() == [3]


def test_price_features_do_not_read_the_target_day_or_future():
    frame = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 24 * 12)
    gate = dt.date(2025, 1, 10)
    original = panel.build_panel(frame, ZONE)
    modified = frame.with_columns(
        pl.when(pl.col("ts_utc").dt.convert_time_zone(ZONE.timezone).dt.date() >= gate)
        .then(99999.0)
        .otherwise(pl.col("price"))
        .alias("price")
    )
    changed = panel.build_panel(modified, ZONE)
    columns = ["local_date", "local_hour", *original.features]
    assert_frame_equal(
        original.frame.filter(pl.col("local_date") <= gate).select(columns),
        changed.frame.filter(pl.col("local_date") <= gate).select(columns),
    )


def test_missing_calendar_day_is_not_replaced_by_previous_observation():
    frame = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 24 * 12)
    missing = dt.date(2025, 1, 8)
    frame = frame.filter(pl.col("ts_utc").dt.convert_time_zone(ZONE.timezone).dt.date() != missing)
    result = panel.build_panel(frame, ZONE).frame.filter(
        pl.col("local_date") == missing + dt.timedelta(days=1)
    )
    assert result["price_d1"].null_count() == result.height
    assert result["price_d7"].null_count() == 0


def test_residual_requires_both_fuels_and_only_uses_d2():
    prices = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 24 * 12)
    load = prices.rename({"price": "load_mw"}).with_columns(pl.lit(100.0).alias("load_mw"))
    wind = prices.rename({"price": "gen_mw"}).with_columns(
        pl.lit(20.0).alias("gen_mw"), pl.lit("wind").alias("fuel")
    )
    solar = wind.with_columns(pl.lit(0.0).alias("gen_mw"), pl.lit("solar").alias("fuel"))
    assert panel.hourly_residual_load(load, wind, ZONE).is_empty()
    generation = pl.concat([wind, solar])
    base = panel.build_panel(prices, ZONE, load=load, generation=generation)
    gate = dt.date(2025, 1, 10)
    changed_load = load.with_columns(
        pl.when(
            pl.col("ts_utc").dt.convert_time_zone(ZONE.timezone).dt.date()
            >= gate - dt.timedelta(days=1)
        )
        .then(9999.0)
        .otherwise(pl.col("load_mw"))
        .alias("load_mw")
    )
    changed = panel.build_panel(prices, ZONE, load=changed_load, generation=generation)
    original = base.frame.filter(pl.col("local_date") == gate)
    assert original["residual_d2"].to_list() == [80.0] * 24
    assert_frame_equal(original, changed.frame.filter(pl.col("local_date") == gate))


def test_panel_rejects_wrong_zone():
    frame = price_frame(dt.datetime(2025, 1, 1, tzinfo=dt.UTC), 24)
    with pytest.raises(ValueError, match="zone"):
        panel.build_panel(frame, get_zone("FR"))


@pytest.mark.parametrize("alpha", [0.0, 0.1, 10.0])
def test_ridge_solver_matches_independent_numpy_solution(alpha):
    rng = np.random.default_rng(42)
    x = rng.normal(size=(100, 3)) * np.array([1.0, 1000.0, 0.01])
    y = 4 + x @ np.array([2.0, -1.0, 3.0]) + rng.normal(size=100)
    design = np.column_stack([np.ones(100), x])
    actual = linalg.ridge_from_moments(
        (design.T @ design).tolist(), (design.T @ y).tolist(), alpha=alpha
    )
    means, scales = x.mean(axis=0), x.std(axis=0)
    z = (x - means) / scales
    expected = np.linalg.solve(z.T @ z + alpha * len(x) * np.eye(3), z.T @ (y - y.mean())) / scales
    np.testing.assert_allclose(actual, np.r_[y.mean() - means @ expected, expected], rtol=1e-8)


def test_constant_feature_and_solver_errors():
    assert linalg.ridge_from_moments([[4.0, 8.0], [8.0, 16.0]], [20.0, 40.0], alpha=0.1) == [
        5.0,
        0.0,
    ]
    with pytest.raises(linalg.NotPositiveDefinite):
        linalg.cholesky([[0.0]])
    with pytest.raises(ValueError):
        linalg.cholesky([[1.0, 0.0], [1.0]])
    with pytest.raises(ValueError):
        linalg.cholesky_solve([[1.0]], [])
    with pytest.raises(ValueError):
        linalg.predict([1.0], [2.0])
    with pytest.raises(ValueError):
        linalg.ridge_from_moments([[1.0]], [], alpha=1.0)
    with pytest.raises(ValueError):
        linalg.ridge_from_moments([[1.0]], [1.0], alpha=-1.0)


@pytest.mark.parametrize("model", [models.Ridge(alpha=0.1), LightGBM()])
def test_refit_does_not_see_current_or_future_targets(model):
    source = small_panel()
    gate = dt.date(2025, 2, 10)
    changed = replace(
        source,
        frame=source.frame.with_columns(
            pl.when(pl.col("local_date") >= gate)
            .then(1e9)
            .otherwise(pl.col("price"))
            .alias("price")
        ),
    )
    args = dict(test_start=gate, test_end=gate, min_train_rows=10)
    first = model.forecasts(source, **args)
    assert first.height == 2
    assert_frame_equal(first, model.forecasts(changed, **args))
    assert_frame_equal(first, model.forecasts(source, **args))


@pytest.mark.parametrize("model", [models.Ridge(alpha=0.1, window=15), LightGBM(window=15)])
def test_rolling_window_ignores_old_targets(model):
    source = small_panel()
    gate = dt.date(2025, 2, 10)
    changed = replace(
        source,
        frame=source.frame.with_columns(
            pl.when(pl.col("local_date") < gate - dt.timedelta(days=15))
            .then(9999.0)
            .otherwise(pl.col("price"))
            .alias("price")
        ),
    )
    args = dict(test_start=gate, test_end=gate, min_train_rows=10)
    assert_frame_equal(
        model.forecasts(source, **args), model.forecasts(changed, **args), rel_tol=1e-6
    )
    if isinstance(model, models.Ridge):
        assert_frame_equal(
            model.coefficients(source, before=gate), model.coefficients(changed, before=gate)
        )


@pytest.mark.parametrize("model", [models.Ridge(alpha=0.1, window=3), LightGBM(window=3)])
def test_too_small_training_window_is_rejected(model):
    with pytest.raises(ValueError, match="window"):
        model.forecasts(small_panel(), test_start=dt.date(2025, 2, 1), min_train_rows=10)


def test_naive_variants_use_their_declared_lags():
    source = small_panel()
    for model in models.naive_baselines(source):
        result = model.forecasts(
            source, test_start=dt.date(2025, 2, 1), test_end=dt.date(2025, 2, 2)
        )
        expected = source.frame.filter(
            pl.col("local_date").is_between(dt.date(2025, 2, 1), dt.date(2025, 2, 2))
        )
        assert result["forecast"].to_list() == expected[model.column].to_list()


def test_quantiles_use_only_prior_errors_and_keep_models_separate():
    rows = []
    for model, error in [("a", 10.0), ("b", -5.0)]:
        for day in range(6):
            rows.append(
                {
                    "model": model,
                    "local_date": dt.date(2025, 1, 1) + dt.timedelta(days=day),
                    "local_hour": 3,
                    "actual": error if day < 4 else 9999.0,
                    "forecast": 0.0,
                }
            )
    result = scoring.attach_quantiles(pl.DataFrame(rows), window=4)
    for model, expected in [("a", 10.0), ("b", -5.0)]:
        series = result.filter(pl.col("model") == model)["q50"]
        assert series[:4].null_count() == 4
        assert series[4] == expected


@pytest.mark.parametrize("kwargs", [{"window": 1}, {"levels": [0.0]}, {"levels": [1.0]}])
def test_invalid_interval_settings_fail(kwargs):
    with pytest.raises(ValueError):
        scoring.attach_quantiles(pl.DataFrame(), **kwargs)


def test_scores_handle_zero_and_negative_prices_with_known_errors():
    source = small_panel(2).frame.head(3)
    predictions = source.with_columns(
        pl.lit("baseline").alias("model"),
        pl.Series("actual", [-10.0, 0.0, 10.0]),
        pl.Series("forecast", [-8.0, 0.0, 6.0]),
    )
    scores = scoring.scoreboard(
        predictions, ZONE, reference="baseline", baselines=("baseline",), levels=()
    )
    overall = scores.filter(pl.col("scope") == "overall").row(0, named=True)
    assert overall["mae"] == 2.0
    assert overall["rmse"] == pytest.approx((20 / 3) ** 0.5)
    assert overall["bias"] == pytest.approx(2 / 3)
    assert overall["skill_pct"] == 0.0
    assert "negative" in scores["bucket"]


def test_common_sample_excludes_missing_model_hours_and_rejects_duplicates():
    first = (
        small_panel(2)
        .frame.select("local_date", "local_hour")
        .with_columns(pl.lit("a").alias("model"), pl.lit(1.0).alias("forecast"))
    )
    other = first.head(3).with_columns(pl.lit("b").alias("model"))
    combined = pl.concat([first, other])
    assert backtest._common_sample(combined, 2).height == 6
    with pytest.raises(ValueError, match="duplicate"):
        backtest._common_sample(pl.concat([combined, first.head(1)]), 2)
    with pytest.raises(ValueError, match="finite"):
        backtest._common_sample(combined.with_columns(pl.lit(float("nan")).alias("forecast")), 2)


def test_pinball_and_interval_diagnostics_match_hand_calculation():
    predictions = small_panel(2).frame.with_columns(
        pl.lit("baseline").alias("model"),
        pl.Series("actual", [-10.0, 0.0, 10.0, 100.0]),
        pl.lit(0.0).alias("forecast"),
        pl.Series("q10", [-5.0, -5.0, -5.0, None]),
        pl.Series("q50", [0.0, 0.0, 0.0, None]),
        pl.Series("q90", [5.0, 5.0, 5.0, None]),
    )
    result = scoring.scoreboard(predictions, ZONE, reference="baseline")
    overall = result.filter(pl.col("scope") == "overall").row(0, named=True)
    assert overall["n"] == 4
    assert overall["n_interval"] == 3
    assert overall["interval_coverage_pct"] == pytest.approx(100 / 3)
    assert overall["interval_mean_width"] == 10.0
    assert overall["pinball_q10"] == pytest.approx((4.5 + 0.5 + 1.5) / 3)
    assert overall["pinball_q50"] == pytest.approx((5.0 + 0.0 + 5.0) / 3)
    assert overall["pinball_q90"] == pytest.approx((1.5 + 0.5 + 4.5) / 3)
    assert overall["mean_pinball"] == pytest.approx(23 / 9)


def test_interval_coverage_uses_unrounded_values_and_complete_intervals():
    predictions = small_panel(2).frame.with_columns(
        pl.lit("baseline").alias("model"),
        pl.Series("actual", [1.004, 0.0, 0.0, 0.0]),
        pl.lit(0.0).alias("forecast"),
        pl.Series("q10", [-1.0, -1.0, -1.0, None]),
        pl.Series("q50", [0.0, 0.0, None, None]),
        pl.Series("q90", [1.003, 1.0, 1.0, None]),
    )
    result = scoring.scoreboard(predictions, ZONE, reference="baseline")
    overall = result.filter(pl.col("scope") == "overall").row(0, named=True)
    assert overall["n_interval"] == 2
    assert overall["interval_coverage_pct"] == 50.0
    assert overall["interval_mean_width"] == pytest.approx(2.0015)


def test_negative_skill_is_preserved_when_model_loses_to_baseline():
    baseline = small_panel(2).frame.with_columns(
        pl.lit("baseline").alias("model"),
        pl.lit(-10.0).alias("actual"),
        pl.lit(-9.0).alias("forecast"),
    )
    challenger = baseline.with_columns(
        pl.lit("challenger").alias("model"), pl.lit(-7.0).alias("forecast")
    )
    scores = scoring.scoreboard(
        pl.concat([baseline, challenger]),
        ZONE,
        reference="baseline",
        baselines=("baseline",),
        levels=(),
    )
    losses = scores.filter(pl.col("model") == "challenger")
    assert losses["skill_vs_best_baseline_pct"].to_list() == [-200.0] * losses.height
    assert losses["interval_coverage_pct"].null_count() == losses.height
    assert losses["interval_mean_width"].null_count() == losses.height
    assert losses["n_interval"].sum() == 0


def test_validation_and_cutoff_do_not_read_later_prices():
    source = small_panel()
    end = dt.date(2025, 2, 5)
    options = dict(min_train_days=25, validation_days=5, alpha_grid=(0.01, 1.0), end=end)
    result = backtest.run(ZONE, panel=source, **options)
    later = replace(
        source,
        frame=source.frame.with_columns(
            pl.when(pl.col("local_date") > end)
            .then(99999.0)
            .otherwise(pl.col("price"))
            .alias("price")
        ),
    )
    repeated = backtest.run(ZONE, panel=later, **options)
    assert_frame_equal(result.predictions, repeated.predictions)
    assert result.input_sha256 == repeated.input_sha256
    assert len(result.models) == 5
    assert result.predictions.group_by("model").len()["len"].n_unique() == 1
    assert result.validation_start < result.test_start <= result.test_end == end
    assert "retrospective" in result.metadata()["evaluation"]
    assert result.summary().height == 5
    changed_test = replace(
        source,
        frame=source.frame.with_columns(
            pl.when(pl.col("local_date") >= result.test_start)
            .then(8888.0)
            .otherwise(pl.col("price"))
            .alias("price")
        ),
    )
    search_options = dict(
        validation_start=result.validation_start,
        validation_end=result.test_start - dt.timedelta(days=1),
        min_train_rows=25,
        grid=(0.01, 1.0),
    )
    original_alpha, original_search = backtest.select_alpha(source, **search_options)
    changed_alpha, changed_search = backtest.select_alpha(changed_test, **search_options)
    assert original_alpha == changed_alpha
    assert_frame_equal(original_search, changed_search)


@pytest.mark.parametrize(
    "kwargs",
    [{"min_train_days": 0}, {"validation_days": 0}, {"alpha": -1.0}, {"alpha": float("nan")}],
)
def test_run_rejects_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        backtest.run(ZONE, panel=small_panel(), **kwargs)


def test_empty_history_and_fixed_alpha():
    with pytest.raises(backtest.InsufficientHistory):
        backtest.run(ZONE, panel=small_panel(1))
    result = backtest.run(
        ZONE,
        panel=small_panel(),
        min_train_days=30,
        validation_days=5,
        alpha=0.1,
        include_lightgbm=False,
    )
    assert result.alpha == 0.1
    assert result.alpha_search["mae"].null_count() == 1
    assert len(result.models) == 4
    with pytest.raises(ValueError):
        backtest.select_alpha(
            small_panel(),
            validation_start=dt.date(2025, 2, 1),
            validation_end=dt.date(2025, 2, 2),
            min_train_rows=10,
            grid=(),
        )
