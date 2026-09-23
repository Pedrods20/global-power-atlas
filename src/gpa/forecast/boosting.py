"""A fixed LightGBM challenger on the same information set as ridge.

One pooled model uses market-local hour alongside the panel features. Ridge
conditions on that same hour by fitting separate regressions. No test-period
early stopping or hyperparameter search is performed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import lightgbm as lgb
import polars as pl

from gpa.forecast.models import _FORECAST_SCHEMA, _in_window
from gpa.forecast.panel import Panel

__all__ = ["DEFAULT_LIGHTGBM_GRID", "LightGBM", "select_parameters"]

DEFAULT_LIGHTGBM_GRID: tuple[dict[str, float | int], ...] = (
    {"num_leaves": 15, "learning_rate": 0.05, "min_data_in_leaf": 48, "lambda_l2": 1.0},
    {"num_leaves": 31, "learning_rate": 0.03, "min_data_in_leaf": 48, "lambda_l2": 2.0},
    {"num_leaves": 63, "learning_rate": 0.02, "min_data_in_leaf": 64, "lambda_l2": 4.0},
)


@dataclass(frozen=True, slots=True)
class LightGBM:
    window: int | None = None
    num_boost_round: int = 100
    num_leaves: int = 15
    learning_rate: float = 0.05
    min_data_in_leaf: int = 48
    lambda_l2: float = 1.0
    objective: str = "regression_l1"
    quantile_alpha: float | None = None
    refit_every_days: int = 1
    name: str = "lightgbm"
    description: str = "Pooled LightGBM on panel features and market-local hour."

    def predict_day(self, panel: Panel, day: dt.date, *, min_train_rows: int) -> pl.DataFrame:
        """Predict unknown targets with the same fixed specification as the backtest."""
        training = (
            panel.complete().filter(pl.col("local_date") < day).sort("local_date", "local_hour")
        )
        days = training["local_date"].unique().sort()
        if len(days) < min_train_rows:
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        if self.window is not None:
            training = training.filter(pl.col("local_date") >= days[-self.window])
        target = panel.frame.filter(pl.col("local_date") == day).drop_nulls(list(panel.features))
        if target.is_empty():
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        features = [*panel.features, "local_hour"]
        fitted = self._fit(training, features, panel.target)
        return target.select("local_date", "local_hour").with_columns(
            pl.Series(
                "forecast",
                fitted.predict(target.select(features).to_numpy(), num_threads=1),
                dtype=pl.Float64,
            )
        )

    def parameters(self) -> dict[str, object]:
        """Return deterministic parameters, including optional quantile loss."""
        if self.num_boost_round < 1 or self.num_leaves < 2 or self.min_data_in_leaf < 1:
            raise ValueError("LightGBM tree settings must be positive")
        if self.refit_every_days < 1:
            raise ValueError("refit_every_days must be positive")
        if not self.learning_rate > 0.0 or self.lambda_l2 < 0.0:
            raise ValueError("LightGBM learning_rate must be positive and lambda_l2 nonnegative")
        if self.objective == "quantile" and not 0.0 < (self.quantile_alpha or 0.0) < 1.0:
            raise ValueError("quantile_alpha must be strictly between zero and one")
        parameters: dict[str, object] = {
            "objective": self.objective,
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "min_data_in_leaf": self.min_data_in_leaf,
            "lambda_l2": self.lambda_l2,
            "num_threads": 1,
            "seed": 42,
            "deterministic": True,
            "force_col_wise": True,
            "verbosity": -1,
        }
        if self.objective == "quantile":
            parameters["alpha"] = self.quantile_alpha
        return parameters

    def _fit(self, training: pl.DataFrame, features: list[str], target: str) -> lgb.Booster:
        return lgb.train(
            self.parameters(),
            lgb.Dataset(
                training.select(features).to_numpy(),
                label=training[target].to_numpy(),
                feature_name=features,
            ),
            num_boost_round=self.num_boost_round,
        )

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int,
    ) -> pl.DataFrame:
        if self.window is not None and self.window < min_train_rows:
            raise ValueError("rolling window must be at least min_train_rows")
        complete = panel.complete().sort(["local_date", "local_hour"])
        if not panel.features or complete.is_empty():
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        features = [*panel.features, "local_hour"]
        rows: list[pl.DataFrame] = []
        dates = complete.filter(_in_window(test_start, test_end))["local_date"].unique().sort()
        fitted: lgb.Booster | None = None
        for offset, day in enumerate(dates.to_list()):
            if fitted is None or offset % self.refit_every_days == 0:
                training = complete.filter(pl.col("local_date") < day)
                training_days = training["local_date"].unique().sort()
                if len(training_days) < min_train_rows:
                    continue
                if self.window is not None:
                    training = training.filter(pl.col("local_date") >= training_days[-self.window])
                fitted = self._fit(training, features, panel.target)
            target = complete.filter(pl.col("local_date") == day)
            if fitted is None:
                continue
            values = fitted.predict(target.select(features).to_numpy(), num_threads=1)
            rows.append(
                target.select("local_date", "local_hour").with_columns(
                    pl.Series("forecast", values, dtype=pl.Float64)
                )
            )
        return pl.concat(rows) if rows else pl.DataFrame(schema=_FORECAST_SCHEMA)


def select_parameters(
    panel: Panel,
    *,
    validation_start: dt.date,
    validation_end: dt.date,
    min_train_rows: int,
    grid: Sequence[dict[str, float | int]] = DEFAULT_LIGHTGBM_GRID,
    window: int | None = None,
    refit_every_days: int = 1,
) -> tuple[dict[str, float | int], pl.DataFrame]:
    """Choose LightGBM parameters on a pre-test walk-forward window."""
    if not grid:
        raise ValueError("LightGBM parameter grid must not be empty")
    actuals = panel.frame.select("local_date", "local_hour", pl.col(panel.target).alias("actual"))
    rows: list[dict[str, object]] = []
    for candidate in grid:
        model = _configured(window, candidate, refit_every_days=refit_every_days)
        forecast = model.forecasts(
            panel,
            test_start=validation_start,
            test_end=validation_end,
            min_train_rows=min_train_rows,
        )
        scored = forecast.join(actuals, on=["local_date", "local_hour"], how="inner")
        if scored.is_empty():
            continue
        rows.append(
            {
                **candidate,
                "n": scored.height,
                "mae": float(
                    scored.select((pl.col("actual") - pl.col("forecast")).abs().mean()).item()
                ),
            }
        )
    if not rows:
        raise ValueError("no LightGBM candidate produced a validation forecast")
    search = pl.DataFrame(rows).sort("mae")
    chosen = {key: search[key][0] for key in grid[0]}
    return chosen, search


def _configured(
    window: int | None,
    parameters: dict[str, float | int],
    *,
    refit_every_days: int = 1,
) -> LightGBM:
    """Construct a typed model from the small public tuning-grid mapping."""
    return LightGBM(
        window=window,
        num_leaves=int(parameters["num_leaves"]),
        learning_rate=float(parameters["learning_rate"]),
        min_data_in_leaf=int(parameters["min_data_in_leaf"]),
        lambda_l2=float(parameters["lambda_l2"]),
        refit_every_days=refit_every_days,
    )
