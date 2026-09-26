"""The forecasters: three naive baselines and a per-hour ridge regression.

Ridge refits every walk-forward day. An expanding window grows by one row per
step, so every origin's moment matrix is a prefix of one cumulative sum, and all
origins of one clock hour are solved as a single batched linear system.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt
import polars as pl

from gpa.forecast.panel import Panel

__all__ = ["DEFAULT_ALPHA_GRID", "Model", "Naive", "Ridge", "naive_baselines", "ridge_solve"]

DEFAULT_ALPHA_GRID: tuple[float, ...] = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
"""Penalties on standardised features, half an order of magnitude apart."""

_CONSTANT_TOLERANCE = 1e-9
"""Relative spread below which a feature is constant in its window and gets a zero weight."""

Array = npt.NDArray[np.float64]

_FORECAST_SCHEMA = {"local_date": pl.Date, "local_hour": pl.Int8, "forecast": pl.Float64}
_COEFFICIENT_SCHEMA = {
    "local_hour": pl.Int8,
    "feature": pl.String,
    "coefficient": pl.Float64,
    "train_sd": pl.Float64,
    "effect": pl.Float64,
}


def _in_window(test_start: dt.date, test_end: dt.date | None) -> pl.Expr:
    within = pl.col("local_date") >= test_start
    return within if test_end is None else within & (pl.col("local_date") <= test_end)


class Model(Protocol):
    """What the backtest requires: forecasts that read only rows before each day."""

    @property
    def name(self) -> str: ...

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int,
    ) -> pl.DataFrame: ...


@dataclass(frozen=True, slots=True)
class Naive:
    """Repeats an already-lagged panel column, so availability is enforced in one place."""

    name: str
    column: str
    description: str

    def predict_day(self, panel: Panel, day: dt.date, *, min_train_rows: int = 0) -> pl.DataFrame:
        return self.forecasts(panel, test_start=day, test_end=day)

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int = 0,
    ) -> pl.DataFrame:
        return (
            panel.frame.filter(_in_window(test_start, test_end))
            .select(
                "local_date", "local_hour", pl.col(self.column).cast(pl.Float64).alias("forecast")
            )
            .drop_nulls("forecast")
            .sort("local_date", "local_hour")
        )


def validation_error(
    model: Model, panel: Panel, start: dt.date, end: dt.date, min_train_rows: int
) -> dict[str, int | float] | None:
    """Walk-forward MAE over a validation window, or ``None`` if nothing was forecast."""
    forecasts = model.forecasts(
        panel, test_start=start, test_end=end, min_train_rows=min_train_rows
    )
    actual = panel.frame.select("local_date", "local_hour", pl.col(panel.target).alias("actual"))
    scored = forecasts.join(actual, on=["local_date", "local_hour"])
    if scored.is_empty():
        return None
    error = (pl.col("actual") - pl.col("forecast")).abs().mean()
    return {"n": scored.height, "mae": float(scored.select(error).item())}


def naive_baselines(panel: Panel) -> tuple[Naive, ...]:
    return (
        Naive(
            "naive_previous_day", "price_d1", "Yesterday's price for the same market-local hour."
        ),
        Naive(
            "naive_previous_week", "price_d7", "The same hour of the same weekday one week earlier."
        ),
        Naive(
            "naive_similar_day",
            panel.similar_day,
            "Yesterday from Tuesday to Friday, one week earlier on Monday, Saturday and Sunday.",
        ),
    )


def ridge_solve(gram: Array, cross: Array, alpha: float) -> Array:
    """Ridge weights from stacked moments of ``[1, x]``: ``gram`` (..., q, q), ``cross`` (..., q).

    Features are centred and scaled analytically from the moments, so ``alpha`` means
    the same whatever a feature's unit, and the penalty grows with the window. The
    intercept is unpenalised. Returns (..., q) weights, intercept first.
    """
    n = gram[..., 0, 0]
    mean = gram[..., 0, 1:] / n[..., None]
    target_mean = cross[..., 0] / n
    centred = gram[..., 1:, 1:] - n[..., None, None] * mean[..., :, None] * mean[..., None, :]
    centred_cross = cross[..., 1:] - n[..., None] * mean * target_mean[..., None]
    scale = np.sqrt(np.maximum(np.diagonal(centred, axis1=-2, axis2=-1), 0.0) / n[..., None])
    active = scale > _CONSTANT_TOLERANCE * (1.0 + np.abs(mean))
    scale = np.where(active, scale, 1.0)
    system = np.where(
        active[..., :, None] & active[..., None, :],
        centred / (scale[..., :, None] * scale[..., None, :]),
        0.0,
    )
    diagonal = np.arange(system.shape[-1])
    system[..., diagonal, diagonal] += np.where(active, alpha * n[..., None], 1.0)
    right = np.where(active, centred_cross / scale, 0.0)
    weights = np.where(active, np.linalg.solve(system, right[..., None])[..., 0] / scale, 0.0)
    intercept = target_mean - (weights * mean).sum(axis=-1)
    return np.concatenate([intercept[..., None], weights], axis=-1)


def _design(frame: pl.DataFrame, features: tuple[str, ...]) -> Array:
    x = frame.select(features).to_numpy().astype(float)
    return np.column_stack([np.ones(len(x)), x])


@dataclass(frozen=True, slots=True)
class Ridge:
    """Per-hour ridge; ``window`` keeps the last rows per hour, ``None`` is an expanding origin."""

    alpha: float
    window: int | None = None
    name: str = "ridge"
    description: str = "Per-hour ridge on lagged prices, residual load and the calendar."

    def _recent(self, rows: pl.DataFrame) -> pl.DataFrame:
        return rows if self.window is None else rows.tail(self.window)

    def _weights(self, rows: pl.DataFrame, panel: Panel) -> Array:
        z = _design(rows, panel.features)
        return ridge_solve(z.T @ z, z.T @ rows[panel.target].to_numpy(), self.alpha)

    def predict_day(self, panel: Panel, day: dt.date, *, min_train_rows: int) -> pl.DataFrame:
        """Fit on targets known before ``day`` and forecast its feature-only rows."""
        training = panel.complete().filter(pl.col("local_date") < day).sort("local_date")
        target = panel.frame.filter(pl.col("local_date") == day).drop_nulls(list(panel.features))
        rows = []
        for row in target.iter_rows(named=True):
            history = self._recent(training.filter(pl.col("local_hour") == row["local_hour"]))
            if history.height < max(min_train_rows, len(panel.features) + 2):
                continue
            x = np.array([1.0, *(row[name] for name in panel.features)])
            forecast = float(self._weights(history, panel) @ x)
            rows.append({"local_date": day, "local_hour": row["local_hour"], "forecast": forecast})
        return pl.DataFrame(rows, schema=_FORECAST_SCHEMA)

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int,
    ) -> pl.DataFrame:
        """Refit every test day on all earlier rows of the same hour: one solve per hour."""
        floor = max(min_train_rows, len(panel.features) + 2)
        if self.window is not None and self.window < floor:
            raise ValueError(f"rolling window must hold at least {floor} observations")
        frames = []
        complete = panel.complete().sort("local_date", "local_hour")
        for hour in complete.partition_by("local_hour") if panel.features else []:
            in_test = hour.select(_in_window(test_start, test_end)).to_series().to_numpy()
            index = np.flatnonzero(in_test & (np.arange(hour.height) >= floor))
            if not index.size:
                continue
            z = _design(hour, panel.features)
            y = hour[panel.target].to_numpy()
            # Moments over rows [0, i) for every origin i, as prefixes of one cumulative sum.
            gram = np.cumsum(z[:, :, None] * z[:, None, :], axis=0)
            cross = np.cumsum(z * y[:, None], axis=0)
            g, c = gram[index - 1], cross[index - 1]
            if self.window is not None:
                dropped = index - self.window - 1
                has = dropped >= 0
                g = g - np.where(has[:, None, None], gram[np.maximum(dropped, 0)], 0.0)
                c = c - np.where(has[:, None], cross[np.maximum(dropped, 0)], 0.0)
            weights = ridge_solve(g, c, self.alpha)
            frames.append(
                hour[index]
                .select("local_date", "local_hour")
                .with_columns(forecast=pl.Series((weights * z[index]).sum(axis=1)))
            )
        if not frames:
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        return pl.concat(frames).sort("local_date", "local_hour")

    def coefficients(self, panel: Panel, *, before: dt.date) -> pl.DataFrame:
        """Weights at the final refit; ``effect`` is weight times the feature's training SD."""
        frames = []
        training = panel.complete().filter(pl.col("local_date") < before).sort("local_date")
        for hour in training.partition_by("local_hour") if panel.features else []:
            rows = self._recent(hour)
            if rows.height <= len(panel.features):
                continue
            weights = self._weights(rows, panel)[1:]
            spread = rows.select(pl.col(panel.features).std(ddof=0)).row(0)
            frames.append(
                pl.DataFrame(
                    {
                        "local_hour": [hour["local_hour"][0]] * len(weights),
                        "feature": list(panel.features),
                        "coefficient": weights,
                        "train_sd": [float(sd or 0.0) for sd in spread],
                    },
                    schema_overrides={"local_hour": pl.Int8},
                ).with_columns(effect=pl.col("coefficient") * pl.col("train_sd"))
            )
        if not frames:
            return pl.DataFrame(schema=_COEFFICIENT_SCHEMA)
        return pl.concat(frames).sort("local_hour", maintain_order=True)
