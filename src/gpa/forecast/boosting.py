"""A pooled LightGBM challenger on ridge's information set, with local hour as a feature."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import lightgbm as lgb
import polars as pl

from gpa.forecast.models import _FORECAST_SCHEMA, _in_window, validation_error
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

    @classmethod
    def configured(cls, parameters: dict[str, float | int]) -> LightGBM:
        return cls(
            num_leaves=int(parameters["num_leaves"]),
            learning_rate=float(parameters["learning_rate"]),
            min_data_in_leaf=int(parameters["min_data_in_leaf"]),
            lambda_l2=float(parameters["lambda_l2"]),
        )

    def parameters(self) -> dict[str, object]:
        """Deterministic single-threaded training parameters."""
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

    def _training(
        self, complete: pl.DataFrame, day: dt.date, min_train_rows: int
    ) -> pl.DataFrame | None:
        training = complete.filter(pl.col("local_date") < day)
        days = training["local_date"].unique().sort()
        if len(days) < min_train_rows:
            return None
        return (
            training
            if self.window is None
            else training.filter(pl.col("local_date") >= days[-self.window])
        )

    def _fit(self, training: pl.DataFrame, features: list[str], target: str) -> lgb.Booster:
        data = lgb.Dataset(
            training.select(features).to_numpy(),
            label=training[target].to_numpy(),
            feature_name=features,
        )
        return lgb.train(self.parameters(), data, num_boost_round=self.num_boost_round)

    @staticmethod
    def _predict(booster: lgb.Booster, rows: pl.DataFrame, features: list[str]) -> pl.DataFrame:
        values = booster.predict(rows.select(features).to_numpy(), num_threads=1)
        return rows.select("local_date", "local_hour").with_columns(
            pl.Series("forecast", values, dtype=pl.Float64)
        )

    def predict_day(self, panel: Panel, day: dt.date, *, min_train_rows: int) -> pl.DataFrame:
        """Forecast ``day`` from the same fixed specification the backtest scores."""
        complete = panel.complete().sort("local_date", "local_hour")
        training = self._training(complete, day, min_train_rows)
        target = panel.frame.filter(pl.col("local_date") == day).drop_nulls(list(panel.features))
        if training is None or target.is_empty():
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        features = [*panel.features, "local_hour"]
        return self._predict(self._fit(training, features, panel.target), target, features)

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
        complete = panel.complete().sort("local_date", "local_hour")
        features = [*panel.features, "local_hour"]
        dates = complete.filter(_in_window(test_start, test_end))["local_date"].unique().sort()
        booster: lgb.Booster | None = None
        rows = []
        for offset, day in enumerate(dates.to_list() if panel.features else []):
            if booster is None or offset % self.refit_every_days == 0:
                training = self._training(complete, day, min_train_rows)
                if training is None:
                    continue
                booster = self._fit(training, features, panel.target)
            rows.append(
                self._predict(booster, complete.filter(pl.col("local_date") == day), features)
            )
        return pl.concat(rows) if rows else pl.DataFrame(schema=_FORECAST_SCHEMA)


def select_parameters(
    panel: Panel,
    *,
    validation_start: dt.date,
    validation_end: dt.date,
    min_train_rows: int,
    grid: Sequence[dict[str, float | int]] = DEFAULT_LIGHTGBM_GRID,
) -> tuple[dict[str, float | int], pl.DataFrame]:
    """Choose tree settings by walk-forward MAE on the pre-test window."""
    if not grid:
        raise ValueError("LightGBM parameter grid must not be empty")
    rows = []
    for candidate in grid:
        error = validation_error(
            LightGBM.configured(candidate), panel, validation_start, validation_end, min_train_rows
        )
        if error is not None:
            rows.append({**candidate, **error})
    if not rows:
        raise ValueError("no LightGBM candidate produced a validation forecast")
    search = pl.DataFrame(rows).sort("mae")
    return {key: search[key][0] for key in grid[0]}, search
