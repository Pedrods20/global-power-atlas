"""A fixed LightGBM challenger on the same information set as ridge.

One pooled model uses market-local hour alongside the panel features. Ridge
conditions on that same hour by fitting separate regressions. No test-period
early stopping or hyperparameter search is performed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import lightgbm as lgb
import polars as pl

from gpa.forecast.models import _FORECAST_SCHEMA, _in_window
from gpa.forecast.panel import Panel


@dataclass(frozen=True, slots=True)
class LightGBM:
    window: int | None = None
    name: str = "lightgbm"
    description: str = "Pooled LightGBM on panel features and market-local hour."

    def parameters(self) -> dict[str, object]:
        """Fixed before inspecting the challenger results; CPU deterministic."""
        return {
            "objective": "regression_l1",
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_data_in_leaf": 48,
            "lambda_l2": 1.0,
            "num_threads": 1,
            "seed": 42,
            "deterministic": True,
            "force_col_wise": True,
            "verbosity": -1,
        }

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
        for day in dates.to_list():
            training = complete.filter(pl.col("local_date") < day)
            training_days = training["local_date"].unique().sort()
            if len(training_days) < min_train_rows:
                continue
            if self.window is not None:
                training = training.filter(pl.col("local_date") >= training_days[-self.window])
            target = complete.filter(pl.col("local_date") == day)
            fitted = lgb.train(
                self.parameters(),
                lgb.Dataset(
                    training.select(features).to_numpy(),
                    label=training[panel.target].to_numpy(),
                    feature_name=features,
                ),
                num_boost_round=100,
            )
            values = fitted.predict(target.select(features).to_numpy(), num_threads=1)
            rows.append(
                target.select("local_date", "local_hour").with_columns(
                    pl.Series("forecast", values, dtype=pl.Float64)
                )
            )
        return pl.concat(rows) if rows else pl.DataFrame(schema=_FORECAST_SCHEMA)
