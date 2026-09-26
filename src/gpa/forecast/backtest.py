"""The walk-forward harness: split by date, refit every day, tune before the test opens.

Every model is scored only on the hours all of them forecast, so any two rows of
the scoreboard describe identical data.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import polars as pl

from gpa.forecast import scoring
from gpa.forecast.boosting import LightGBM, select_parameters
from gpa.forecast.models import (
    DEFAULT_ALPHA_GRID,
    Model,
    Ridge,
    naive_baselines,
    validation_error,
)
from gpa.forecast.panel import Panel, load_panel
from gpa.zones import Zone, get_zone

__all__ = [
    "MIN_TRAIN_DAYS",
    "REFERENCE_MODEL",
    "VALIDATION_DAYS",
    "BacktestResult",
    "InsufficientHistory",
    "run",
    "select_alpha",
]

log = logging.getLogger(__name__)

MIN_TRAIN_DAYS: Final = 270
"""Nine months: each weekday about forty times, three quarters of the annual cycle."""

VALIDATION_DAYS: Final = 90
"""Pre-test days that choose the penalty and tree settings, then are never scored."""

REFERENCE_MODEL: Final = "naive_similar_day"
"""The strongest naive on power prices; skill against a weaker one would be manufactured."""

BENCHMARK_END: Final = dt.date(2026, 9, 12)
"""The inspected history stays frozen; prospective evidence comes from the ledger."""

_TREE_SEARCH_SCHEMA = {
    "num_leaves": pl.Int64,
    "learning_rate": pl.Float64,
    "min_data_in_leaf": pl.Int64,
    "lambda_l2": pl.Float64,
    "n": pl.Int64,
    "mae": pl.Float64,
}


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything one walk-forward run produced."""

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

    @property
    def test_days(self) -> int:
        return (self.test_end - self.test_start).days + 1

    def metadata(self) -> dict[str, object]:
        """A JSON-safe description of the run, for the published page."""
        overall = self.scores.filter(pl.col("scope") == "overall").sort("mae")
        best = overall.row(0, named=True) if not overall.is_empty() else {}
        feature_mode = (
            "lagged actual load/generation"
            if any(name.startswith("residual_") for name in self.features)
            else "price and calendar only"
        )
        if any(name.startswith("da_") for name in self.features):
            feature_mode += "; day-ahead load/wind/solar forecasts (assumed gate vintage)"
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
            "scored_hours": self.predictions.filter(pl.col("model") == self.models[0]).height,
            "alpha": self.alpha,
            "reference": REFERENCE_MODEL,
            "features": list(self.features),
            "models": list(self.models),
            "best_model": best.get("model"),
            "baselines": list(self.baselines),
            "best_mae": best.get("mae"),
            "best_skill_pct": best.get("skill_pct"),
            "best_skill_vs_best_baseline_pct": best.get("skill_vs_best_baseline_pct"),
            "quantile_levels": list(scoring.QUANTILE_LEVELS),
            "conformal_window_days": scoring.CONFORMAL_WINDOW_DAYS,
            "evaluation": "retrospective development benchmark; not an untouched holdout",
            "data_vintages": "latest revised observations; publication-time vintages unavailable",
            "benchmark_end": BENCHMARK_END.isoformat(),
            "input_sha256": self.input_sha256,
            "eligible_hours": self.eligible_hours,
            "min_train_days": self.min_train_days,
            "validation_days": self.validation_days,
            "lightgbm_parameters": self.lightgbm_parameters,
            "lightgbm_search": self.lightgbm_search.to_dicts(),
            "lightgbm_features": [*self.features, "local_hour"],
            "target_convention": (
                "complete UTC hours; repeated autumn clock hour averaged; "
                "each clock-hour cell scored equally"
            ),
            "feature_mode": feature_mode,
        }


class InsufficientHistory(RuntimeError):
    """The zone does not hold enough usable days for an honest backtest."""


def select_alpha(
    panel: Panel,
    *,
    validation_start: dt.date,
    validation_end: dt.date,
    min_train_rows: int,
    grid: Sequence[float] = DEFAULT_ALPHA_GRID,
) -> tuple[float, pl.DataFrame]:
    """Choose the ridge penalty by walk-forward MAE on a window that ends before the test."""
    if not grid or any(not math.isfinite(value) or value < 0 for value in grid):
        raise ValueError("alpha grid must be non-empty, finite and nonnegative")
    if validation_start > validation_end:
        raise ValueError("validation window is inverted")
    rows = [
        {"alpha": alpha, **error}
        for alpha in grid
        if (
            error := validation_error(
                Ridge(alpha=alpha), panel, validation_start, validation_end, min_train_rows
            )
        )
    ]
    if not rows:
        raise InsufficientHistory("no ridge forecast on the validation window")
    search = pl.DataFrame(rows).sort("mae")
    return float(search["alpha"][0]), search.sort("alpha")


def stable_hash(frame: pl.DataFrame) -> str:
    """SHA-256 of a frame, rounded so parallel-reduction noise cannot change it across machines."""
    floats = [name for name, dtype in frame.schema.items() if dtype.is_float()]
    stable = frame.with_columns(pl.col(floats).round(6)) if floats else frame
    return hashlib.sha256(stable.write_json().encode()).hexdigest()


def run(
    zone: Zone | str,
    *,
    panel: Panel | None = None,
    min_train_days: int = MIN_TRAIN_DAYS,
    validation_days: int = VALIDATION_DAYS,
    alpha: float | None = None,
    include_lightgbm: bool = True,
    include_fundamentals: bool = False,
    end: dt.date | None = BENCHMARK_END,
) -> BacktestResult:
    """Run the harness for one zone. ``panel`` is for tests; ``alpha`` skips selection.

    A fixed penalty still withholds the validation window, so both are scored on the
    same days. Fundamentals are the labelled ablation input, ignored with a ``panel``.
    """
    market = get_zone(zone) if isinstance(zone, str) else zone
    if min_train_days < 1 or validation_days < 1:
        raise ValueError("training and validation days must be positive")
    if alpha is not None and (not math.isfinite(alpha) or alpha < 0):
        raise ValueError("alpha must be finite and nonnegative")
    prepared = panel or load_panel(market, include_fundamentals=include_fundamentals)
    if prepared.zone != market:
        raise ValueError("panel zone does not match requested market")
    if end is not None:
        prepared = dataclasses.replace(
            prepared, frame=prepared.frame.filter(pl.col("local_date") <= end)
        )
    if prepared.frame.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
        raise ValueError("panel has duplicate local date/hour keys")

    usable = prepared.complete()["local_date"].unique().sort().to_list()
    if len(usable) < min_train_days + validation_days + 1:
        raise InsufficientHistory(
            f"{market.code} has {len(usable)} usable days; {min_train_days} training, "
            f"{validation_days} validation and at least one test day are needed"
        )
    validation_start, validation_end = (
        usable[min_train_days],
        usable[min_train_days + validation_days - 1],
    )
    test_start, test_end = usable[min_train_days + validation_days], usable[-1]
    if alpha is None:
        chosen, search = select_alpha(
            prepared,
            validation_start=validation_start,
            validation_end=validation_end,
            min_train_rows=min_train_days,
        )
    else:
        chosen, search = (
            alpha,
            pl.DataFrame(
                {"alpha": [alpha], "n": [0], "mae": [None]},
                schema={"alpha": pl.Float64, "n": pl.Int64, "mae": pl.Float64},
            ),
        )
    log.info("%s: ridge penalty %.3g over %d validation days", market.code, chosen, validation_days)

    ridge = Ridge(alpha=chosen)
    baselines = naive_baselines(prepared)
    models: list[Model] = [*baselines, ridge]
    lightgbm_parameters: dict[str, object] = {}
    lightgbm_search = pl.DataFrame(schema=_TREE_SEARCH_SCHEMA)
    if include_lightgbm:
        tree, lightgbm_search = select_parameters(
            prepared,
            validation_start=validation_start,
            validation_end=validation_end,
            min_train_rows=min_train_days,
        )
        booster = LightGBM.configured(tree)
        models.append(booster)
        lightgbm_parameters = {
            **tree,
            "refit_every_days": booster.refit_every_days,
            "num_boost_round": booster.num_boost_round,
        }

    frames = []
    for model in models:
        forecasts = model.forecasts(
            prepared, test_start=test_start, test_end=test_end, min_train_rows=min_train_days
        )
        if forecasts.is_empty():
            raise InsufficientHistory(f"{model.name} produced no forecasts for {market.code}")
        frames.append(forecasts.with_columns(pl.lit(model.name).alias("model")))
    predictions = _common_sample(pl.concat(frames), len(frames))
    if predictions.is_empty():
        raise InsufficientHistory("models have no common forecast hours")
    actual = pl.col(prepared.target).alias("actual")
    predictions = scoring.attach_quantiles(
        predictions.join(
            prepared.frame.select("local_date", "local_hour", "ts_utc", actual),
            on=["local_date", "local_hour"],
        )
    )
    baseline_names = tuple(baseline.name for baseline in baselines)
    return BacktestResult(
        zone=market,
        predictions=predictions.sort("model", "local_date", "local_hour"),
        scores=scoring.scoreboard(
            predictions, market, reference=REFERENCE_MODEL, baselines=baseline_names
        ),
        daily=scoring.daily_errors(predictions),
        coefficients=ridge.coefficients(prepared, before=test_end),
        alpha=chosen,
        alpha_search=search,
        lightgbm_parameters=lightgbm_parameters,
        lightgbm_search=lightgbm_search,
        train_start=usable[0],
        validation_start=validation_start,
        test_start=test_start,
        test_end=test_end,
        features=prepared.features,
        models=tuple(model.name for model in models),
        baselines=baseline_names,
        input_sha256=stable_hash(prepared.frame.sort("local_date", "local_hour")),
        input_panel=prepared.frame,
        eligible_hours=prepared.frame.filter(
            pl.col("local_date").is_between(test_start, test_end)
        ).height,
        min_train_days=min_train_days,
        validation_days=validation_days,
    )


def _common_sample(predictions: pl.DataFrame, model_count: int) -> pl.DataFrame:
    """Keep the hours every model forecast, so no model gains by declining hard hours."""
    if predictions.select(
        pl.struct("model", "local_date", "local_hour").is_duplicated().any()
    ).item():
        raise ValueError("duplicate prediction keys would bias scoring")
    if predictions.filter(~pl.col("forecast").is_finite() | pl.col("forecast").is_null()).height:
        raise ValueError("forecasts must be finite")
    shared = (
        predictions.group_by("local_date", "local_hour")
        .agg(pl.col("model").n_unique().alias("_models"))
        .filter(pl.col("_models") == model_count)
        .drop("_models")
    )
    return predictions.join(shared, on=["local_date", "local_hour"])
