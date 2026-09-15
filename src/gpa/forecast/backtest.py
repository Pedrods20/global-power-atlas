"""The walk-forward harness: how a model is judged, and on what.

A random train/test split does not work on a time series. Shuffling puts a
Wednesday in the training set and the Tuesday either side of it in the test set,
so the model is asked to interpolate between hours it has already seen, and the
reported error is not the error anyone would have experienced. The gap between a
shuffled split and an honest one on hourly power prices is large enough to turn
a model that loses to a naive baseline into one that appears to beat it
comfortably.

So the origin walks. The test period is scored one day at a time, and for each
day the model is refitted on everything that had already happened, which is
exactly what a desk running this every afternoon would do. Three properties are
enforced rather than assumed:

*The split is by date, never by row.* Training rows for day D are the rows
strictly before D, taken in date order.

*The model is refitted at every step.* Not fitted once and rolled forward; the
day the model is used is a day it had never seen, every time.

*The penalty is chosen before the test period starts.* A hyperparameter picked
on the test set is leakage wearing a different hat. The ridge penalty is
selected on a validation window that ends the day before the test period opens,
by the same walk-forward, and is then frozen.

The evaluation sample is also shared. Models are scored only on hours that every
model could forecast, so a model does not gain by declining the hard hours and a
comparison between two columns of the scoreboard is a comparison on identical
data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import polars as pl

from gpa.forecast import scoring
from gpa.forecast.boosting import DEFAULT_LIGHTGBM_GRID, LightGBM, select_parameters
from gpa.forecast.models import DEFAULT_ALPHA_GRID, Model, Ridge, naive_baselines
from gpa.forecast.panel import Panel, load_panel
from gpa.zones import Zone, get_zone

__all__ = [
    "MIN_TRAIN_DAYS",
    "PUBLISHED_ZONES",
    "REFERENCE_MODEL",
    "VALIDATION_DAYS",
    "BacktestResult",
    "InsufficientHistory",
    "published",
    "run",
    "select_alpha",
]

log = logging.getLogger(__name__)

MIN_TRAIN_DAYS: Final = 270
"""Usable days required before the first fit.

Nine months. Enough that every day of the week has occurred around forty times
and the annual sine pair has seen three quarters of a cycle, which is the point
below which the seasonal terms are fitting noise.
"""

VALIDATION_DAYS: Final = 90
"""Days between the end of the minimum training history and the test period.

Used only to choose the ridge penalty, then never scored. Three months is long
enough to rank a handful of penalties and short enough to leave most of the
history for the test.
"""

REFERENCE_MODEL: Final = "naive_similar_day"
"""The baseline skill is measured against.

The strongest of the three naive forecasters on power prices, and the standard
reference in the forecasting literature. Measuring against the weakest baseline
available would be a way of manufacturing skill.
"""

PUBLISHED_ZONES: Final[tuple[str, ...]] = ("DE-LU",)
"""Zones the site publishes a backtest for.

One market, deliberately. DE-LU has the deepest day-ahead auction in Europe, a
clean two-year history at a single resolution family, and the load and
generation series the residual-load features need. Adding markets before the
harness has been read by anyone would be breadth bought at the cost of the thing
the front exists to demonstrate.
"""

# Already inspected history is a retrospective development benchmark. Keep it
# frozen when ingestion advances; prospective acceptance needs a separate run.
BENCHMARK_END: Final = dt.date(2026, 9, 12)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything one walk-forward run produced.

    Attributes:
        zone: The market forecast.
        predictions: One row per model, day and hour, with the realised price,
            the point forecast and the conformal quantiles.
        scores: The scoreboard from :func:`gpa.forecast.scoring.scoreboard`.
        daily: Mean absolute error per model per day.
        coefficients: The ridge weights at the final refit, for inspection.
        alpha: The penalty chosen on the validation window.
        alpha_search: Validation MAE for every penalty offered.
        lightgbm_parameters: The selected tree configuration.
        lightgbm_search: Validation MAE for every tree configuration offered.
        train_start: First usable day in the panel.
        validation_start: First day of the penalty-selection window.
        test_start: First day scored.
        test_end: Last day scored.
        features: Feature columns the ridge was given.
        models: Model names scored, in the order they were run.
        baselines: Names of the naive models among them.
    """

    zone: Zone
    predictions: pl.DataFrame
    scores: pl.DataFrame
    daily: pl.DataFrame
    coefficients: pl.DataFrame
    alpha: float
    alpha_search: pl.DataFrame
    lightgbm_parameters: dict[str, object]
    lightgbm_search: pl.DataFrame
    train_start: dt.date
    validation_start: dt.date
    test_start: dt.date
    test_end: dt.date
    features: tuple[str, ...]
    models: tuple[str, ...]
    baselines: tuple[str, ...]
    input_sha256: str
    input_panel: pl.DataFrame
    eligible_hours: int
    min_train_days: int
    validation_days: int
    window: int | None
    levels: tuple[float, ...]

    @property
    def test_days(self) -> int:
        """Days in the scored period, counted on the calendar."""
        return (self.test_end - self.test_start).days + 1

    def summary(self) -> pl.DataFrame:
        """The overall row of the scoreboard, best first."""
        return self.scores.filter(pl.col("scope") == "overall").sort("mae")

    def metadata(self) -> dict[str, object]:
        """A JSON-safe description of the run, for the published page."""
        overall = self.summary()
        best = overall.row(0, named=True) if not overall.is_empty() else {}
        return {
            "zone": self.zone.code,
            "zone_name": self.zone.name,
            "currency": self.zone.currency,
            "timezone": self.zone.timezone,
            "target": (
                "Day-ahead price for one market-local hour, as the duration-weighted "
                "mean of the intervals the auction published for that hour."
            ),
            "gate": (
                "12:00 market time on the day before delivery, when the day-ahead "
                "auction closes for the following delivery day."
            ),
            "train_start": self.train_start.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "test_days": self.test_days,
            "scored_hours": int(self.predictions.filter(pl.col("model") == self.models[0]).height)
            if self.models
            else 0,
            "alpha": self.alpha,
            "reference": REFERENCE_MODEL,
            "features": list(self.features),
            "models": list(self.models),
            "best_model": best.get("model"),
            "baselines": list(self.baselines),
            "best_mae": best.get("mae"),
            "best_skill_pct": best.get("skill_pct"),
            "best_skill_vs_best_baseline_pct": best.get("skill_vs_best_baseline_pct"),
            "quantile_levels": list(self.levels),
            "conformal_window_days": scoring.CONFORMAL_WINDOW_DAYS,
            "evaluation": "retrospective development benchmark; not an untouched holdout",
            "data_vintages": "latest revised observations; publication-time vintages unavailable",
            "benchmark_end": BENCHMARK_END.isoformat(),
            "input_sha256": self.input_sha256,
            "eligible_hours": self.eligible_hours,
            "min_train_days": self.min_train_days,
            "validation_days": self.validation_days,
            "window": self.window,
            "lightgbm_parameters": self.lightgbm_parameters,
            "lightgbm_search": self.lightgbm_search.to_dicts(),
            "lightgbm_refit_days": self.lightgbm_parameters.get("refit_every_days", 1),
            "lightgbm_features": [*self.features, "local_hour"],
            "target_convention": "complete UTC hours; repeated autumn clock hour averaged; each clock-hour cell scored equally",
            "feature_mode": (
                "lagged actual load/generation"
                if any(name.startswith("residual_") for name in self.features)
                else "price and calendar only"
            ),
        }


class InsufficientHistory(RuntimeError):
    """The zone does not hold enough usable days to run an honest backtest."""


def select_alpha(
    panel: Panel,
    *,
    validation_start: dt.date,
    validation_end: dt.date,
    min_train_rows: int,
    grid: Sequence[float] = DEFAULT_ALPHA_GRID,
    window: int | None = None,
) -> tuple[float, pl.DataFrame]:
    """Choose the ridge penalty on a window that ends before the test period.

    Each candidate is run through the same walk-forward the test period will
    use, so the penalty is selected under the conditions it will be used in
    rather than by an in-sample criterion.

    Args:
        panel: The modelling panel.
        validation_start: First day of the selection window.
        validation_end: Last day of it, inclusive.
        min_train_rows: Usable days required before the first fit.
        grid: Penalties to try.
        window: Rolling training window, or ``None`` for an expanding origin.

    Returns:
        The best penalty and a frame of ``alpha``, ``n`` and ``mae`` for all of
        them, so the flatness of the choice is visible rather than hidden.

    Raises:
        ValueError: If the grid is empty.
        InsufficientHistory: If no candidate produced a forecast.
    """
    if not grid:
        raise ValueError("alpha grid must not be empty")
    if any(not math.isfinite(value) or value < 0 for value in grid):
        raise ValueError("alpha grid values must be finite and nonnegative")
    if validation_start > validation_end:
        raise ValueError("validation window is inverted")

    rows: list[dict[str, object]] = []
    actuals = panel.frame.select("local_date", "local_hour", pl.col(panel.target).alias("actual"))

    for alpha in grid:
        forecasts = Ridge(alpha=alpha, window=window).forecasts(
            panel,
            test_start=validation_start,
            test_end=validation_end,
            min_train_rows=min_train_rows,
        )
        if forecasts.is_empty():
            continue
        scored = forecasts.join(actuals, on=["local_date", "local_hour"], how="inner")
        rows.append(
            {
                "alpha": alpha,
                "n": scored.height,
                "mae": float(
                    scored.select((pl.col("actual") - pl.col("forecast")).abs().mean()).item()
                ),
            }
        )

    if not rows:
        raise InsufficientHistory(
            "no ridge forecast could be produced on the validation window; "
            "the panel does not hold enough usable days"
        )

    search = pl.DataFrame(rows).sort("mae")
    return float(search.get_column("alpha").item(0)), search.sort("alpha")


def stable_hash(frame: pl.DataFrame) -> str:
    """SHA-256 of a frame's content, insensitive to platform floating-point noise.

    A duration-weighted mean or join is a parallel reduction, and its
    summation order (and so its last bit) can differ between machines with
    different core counts even given byte-identical input. Rounding before
    serialising keeps the hash stable across a Windows workstation and a
    Linux CI runner, at a precision far finer than any real change to the
    underlying market data would produce.
    """
    float_cols = [name for name, dtype in frame.schema.items() if dtype.is_float()]
    stable = frame.with_columns(pl.col(c).round(6) for c in float_cols) if float_cols else frame
    return hashlib.sha256(stable.write_json().encode()).hexdigest()


def run(
    zone: Zone | str,
    *,
    panel: Panel | None = None,
    min_train_days: int = MIN_TRAIN_DAYS,
    validation_days: int = VALIDATION_DAYS,
    alpha: float | None = None,
    alpha_grid: Sequence[float] = DEFAULT_ALPHA_GRID,
    lightgbm_grid: Sequence[dict[str, float | int]] = DEFAULT_LIGHTGBM_GRID,
    window: int | None = None,
    levels: Sequence[float] = scoring.QUANTILE_LEVELS,
    include_lightgbm: bool = True,
    tune_lightgbm: bool = True,
    lightgbm_refit_days: int = 1,
    include_actual_features: bool = True,
    include_fundamentals: bool = False,
    end: dt.date | None = BENCHMARK_END,
) -> BacktestResult:
    """Run the whole harness for one zone.

    Args:
        zone: Zone or zone code to forecast.
        panel: A prepared panel, for tests. Read from the store when omitted.
        min_train_days: Usable days required before the first fit.
        validation_days: Days reserved for choosing the penalty.
        alpha: Fix the penalty instead of selecting it. Selection is skipped and
            the validation window is still withheld from scoring, so a fixed
            penalty and a selected one are scored on the same days.
        alpha_grid: Penalties offered to the selection.
        lightgbm_grid: Tree configurations offered to the selection.
        window: Rolling training window in usable days, or ``None`` for
            expanding.
        levels: Quantile levels to publish.
        lightgbm_refit_days: Refit cadence for the tree challenger. Daily is
            the operational default; a larger value accelerates long diagnostics.
        include_actual_features: Include lagged realised load/generation when
            loading from the store. Disable for a long price-only stress test.
        include_fundamentals: Add the day-ahead load/wind/solar forecast
            features (see :func:`gpa.forecast.panel.load_panel`). An ablation
            input, ignored when ``panel`` is supplied directly.

    Returns:
        A :class:`BacktestResult`.

    Raises:
        InsufficientHistory: If the zone holds fewer usable days than the
            training, validation and at least one test day require.
    """
    market = get_zone(zone) if isinstance(zone, str) else zone
    if min_train_days < 1 or validation_days < 1:
        raise ValueError("training and validation days must be positive")
    if alpha is not None and (not math.isfinite(alpha) or alpha < 0):
        raise ValueError("alpha must be finite and nonnegative")
    if lightgbm_refit_days < 1:
        raise ValueError("lightgbm_refit_days must be positive")
    prepared = (
        panel
        if panel is not None
        else load_panel(
            market,
            include_actuals=include_actual_features,
            include_fundamentals=include_fundamentals,
        )
    )
    if prepared.zone != market:
        raise ValueError("panel zone does not match requested market")
    if end is not None:
        prepared = Panel(
            market,
            prepared.frame.filter(pl.col("local_date") <= end),
            prepared.features,
            prepared.target,
            prepared.similar_day,
        )
    if prepared.frame.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
        raise ValueError("panel has duplicate local date/hour keys")
    input_sha256 = stable_hash(prepared.frame.sort(["local_date", "local_hour"]))

    usable = prepared.complete().get_column("local_date").unique().sort().to_list()
    required = min_train_days + validation_days + 1
    if len(usable) < required:
        raise InsufficientHistory(
            f"{market.code} has {len(usable)} usable days; "
            f"{required} are needed for {min_train_days} training, "
            f"{validation_days} validation and at least one test day"
        )

    train_start = usable[0]
    validation_start = usable[min_train_days]
    validation_end = usable[min_train_days + validation_days - 1]
    test_start = usable[min_train_days + validation_days]
    test_end = usable[-1]

    if alpha is None:
        chosen, search = select_alpha(
            prepared,
            validation_start=validation_start,
            validation_end=validation_end,
            min_train_rows=min_train_days,
            grid=alpha_grid,
            window=window,
        )
    else:
        chosen, search = (
            alpha,
            pl.DataFrame(
                {"alpha": [alpha], "n": [0], "mae": [None]},
                schema={"alpha": pl.Float64, "n": pl.Int64, "mae": pl.Float64},
            ),
        )
    log.info(
        "%s: ridge penalty %.3g over %d validation days",
        market.code,
        chosen,
        validation_days,
    )

    if include_lightgbm and tune_lightgbm:
        lightgbm_parameters, lightgbm_search = select_parameters(
            prepared,
            validation_start=validation_start,
            validation_end=validation_end,
            min_train_rows=min_train_days,
            grid=lightgbm_grid,
            window=window,
            refit_every_days=lightgbm_refit_days,
        )
        lightgbm_parameters["refit_every_days"] = lightgbm_refit_days
    else:
        lightgbm_parameters = {
            "num_leaves": 15,
            "learning_rate": 0.05,
            "min_data_in_leaf": 48,
            "lambda_l2": 1.0,
        }
        lightgbm_search = pl.DataFrame(
            schema={
                "num_leaves": pl.Int64,
                "learning_rate": pl.Float64,
                "min_data_in_leaf": pl.Int64,
                "lambda_l2": pl.Float64,
                "n": pl.Int64,
                "mae": pl.Float64,
            }
        )

    ridge = Ridge(alpha=chosen, window=window)
    baselines = naive_baselines(prepared)
    baseline_names = tuple(baseline.name for baseline in baselines)
    models: tuple[Model, ...] = (
        *baselines,
        ridge,
        *(
            (
                LightGBM(
                    window=window,
                    num_leaves=int(lightgbm_parameters["num_leaves"]),
                    learning_rate=float(lightgbm_parameters["learning_rate"]),
                    min_data_in_leaf=int(lightgbm_parameters["min_data_in_leaf"]),
                    lambda_l2=float(lightgbm_parameters["lambda_l2"]),
                    refit_every_days=lightgbm_refit_days,
                ),
            )
            if include_lightgbm
            else ()
        ),
    )

    frames: list[pl.DataFrame] = []
    for model in models:
        forecasts = model.forecasts(
            prepared, test_start=test_start, test_end=test_end, min_train_rows=min_train_days
        )
        if forecasts.is_empty():
            raise InsufficientHistory(f"{model.name} produced no forecasts for {market.code}")
        frames.append(forecasts.with_columns(pl.lit(model.name).alias("model")))

    if not frames:
        raise InsufficientHistory(f"no model produced a forecast for {market.code}")

    predictions = _common_sample(pl.concat(frames, how="vertical"), len(frames))
    if predictions.is_empty():
        raise InsufficientHistory("models have no common forecast hours")
    predictions = predictions.join(
        prepared.frame.select(
            "local_date",
            "local_hour",
            "ts_utc",
            pl.col(prepared.target).alias("actual"),
        ),
        on=["local_date", "local_hour"],
        how="inner",
    )
    predictions = scoring.attach_quantiles(predictions, levels=levels)

    scores = scoring.scoreboard(
        predictions,
        market,
        reference=REFERENCE_MODEL,
        baselines=baseline_names,
        levels=levels,
    )

    return BacktestResult(
        zone=market,
        predictions=predictions.sort(["model", "local_date", "local_hour"]),
        scores=scores,
        daily=scoring.daily_errors(predictions),
        coefficients=ridge.coefficients(prepared, before=test_end),
        alpha=chosen,
        alpha_search=search,
        lightgbm_parameters={
            **lightgbm_parameters,
            "num_boost_round": LightGBM().num_boost_round,
        },
        lightgbm_search=lightgbm_search,
        train_start=train_start,
        validation_start=validation_start,
        test_start=test_start,
        test_end=test_end,
        features=prepared.features,
        models=tuple(frame.get_column("model").item(0) for frame in frames),
        baselines=baseline_names,
        input_sha256=input_sha256,
        input_panel=prepared.frame,
        eligible_hours=prepared.frame.filter(
            pl.col("local_date").is_between(test_start, test_end)
        ).height,
        min_train_days=min_train_days,
        validation_days=validation_days,
        window=window,
        levels=tuple(levels),
    )


def published(**kwargs: object) -> list[BacktestResult]:
    """Run the backtest for every zone the site publishes.

    A zone that cannot support an honest run is skipped with a log line rather
    than failing the export: the site should lose a chart, not a build.
    """
    results: list[BacktestResult] = []
    for code in PUBLISHED_ZONES:
        try:
            results.append(run(code, **kwargs))  # type: ignore[arg-type]
        except (InsufficientHistory, KeyError):
            log.exception("backtest skipped for %s", code)
    return results


def _common_sample(predictions: pl.DataFrame, model_count: int) -> pl.DataFrame:
    """Keep only the hours every model forecast.

    Without this a model could improve its score by declining to forecast the
    hours it finds hard, and two rows of the scoreboard would no longer describe
    the same days.
    """
    if predictions.select(
        pl.struct("model", "local_date", "local_hour").is_duplicated().any()
    ).item():
        raise ValueError("duplicate prediction keys would bias scoring")
    if predictions.filter(~pl.col("forecast").is_finite() | pl.col("forecast").is_null()).height:
        raise ValueError("forecasts must be finite")
    shared = (
        predictions.group_by(["local_date", "local_hour"])
        .agg(pl.col("model").n_unique().alias("_models"))
        .filter(pl.col("_models") == model_count)
        .drop("_models")
    )
    return predictions.join(shared, on=["local_date", "local_hour"], how="inner")
