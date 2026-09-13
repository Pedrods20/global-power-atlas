"""The forecasters: three naive baselines and one regularised linear model.

The baselines come first and are not a formality. "Same hour yesterday" and
"same hour last week" are strong on power prices because the series is
dominated by a daily and a weekly cycle, and a great deal of published
forecasting loses to them. Anything more elaborate has to earn its place
against them, in the same walk-forward, on the same hours.

Ridge fits a separate regression for each clock hour, with its penalty chosen
on a validation window ending before evaluation. The LightGBM challenger in
``boosting.py`` pools hours and receives local hour as an explicit input.
Both consume the same lagged price, residual-load and calendar information.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import polars as pl

from gpa.forecast.linalg import predict, ridge_from_moments
from gpa.forecast.panel import Panel

__all__ = [
    "DEFAULT_ALPHA_GRID",
    "Model",
    "Naive",
    "Ridge",
    "naive_baselines",
]

DEFAULT_ALPHA_GRID: tuple[float, ...] = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
"""Ridge penalties offered to the validation search, on standardised features.

Spaced by roughly half an order of magnitude. A finer grid is not worth the
refits: the validation error surface over this range is flat enough that
neighbouring values are indistinguishable given the sample.
"""

_FORECAST_SCHEMA = {
    "local_date": pl.Date,
    "local_hour": pl.Int8,
    "forecast": pl.Float64,
}


def _in_window(test_start: dt.date, test_end: dt.date | None) -> pl.Expr:
    """Row filter for the closed date interval a caller asked for."""
    within = pl.col("local_date") >= test_start
    if test_end is not None:
        within = within & (pl.col("local_date") <= test_end)
    return within


class Model(Protocol):
    """What the backtest requires of a forecaster."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int,
    ) -> pl.DataFrame:
        """Forecast every hour from ``test_start`` to ``test_end`` inclusive.

        Implementations must use only columns the panel declares as features or
        as naive predictors, and must never read a row dated on or after the day
        being forecast.

        Args:
            panel: The modelling panel.
            test_start: First day to forecast.
            test_end: Last day to forecast, or ``None`` for the end of the panel.
            min_train_rows: Usable observations per hour required before a fit.

        Returns:
            Columns ``local_date``, ``local_hour`` and ``forecast``. Hours the
            model cannot forecast are omitted rather than filled.
        """
        ...


@dataclass(frozen=True, slots=True)
class Naive:
    """A baseline that repeats one already-lagged column of the panel.

    The forecast is a column lookup because the lag was applied in
    :mod:`gpa.forecast.panel`, where availability is enforced. A baseline that
    re-derived its own lag would be a second place for that rule to be wrong.
    """

    name: str
    column: str
    description: str

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int = 0,
    ) -> pl.DataFrame:
        """Select the lagged column for every test hour that has one."""
        del min_train_rows  # A naive baseline is not fitted, so it needs no history.
        if panel.frame.is_empty():
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        return (
            panel.frame.filter(_in_window(test_start, test_end))
            .select(
                "local_date",
                "local_hour",
                pl.col(self.column).cast(pl.Float64).alias("forecast"),
            )
            .drop_nulls("forecast")
            .sort(["local_date", "local_hour"])
        )


def naive_baselines(panel: Panel) -> tuple[Naive, ...]:
    """The three baselines every other model is judged against."""
    return (
        Naive(
            name="naive_previous_day",
            column="price_d1",
            description="Yesterday's price for the same market-local hour.",
        ),
        Naive(
            name="naive_previous_week",
            column="price_d7",
            description="The same hour of the same weekday one week earlier.",
        ),
        Naive(
            name="naive_similar_day",
            column=panel.similar_day,
            description=(
                "Yesterday from Tuesday to Friday, one week earlier on Monday, "
                "Saturday and Sunday. The standard seasonal naive for power prices."
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class Ridge:
    """Per-hour ridge regression, refitted at every walk-forward step.

    Attributes:
        alpha: Penalty on the standardised coefficients.
        window: Training days retained, counted in usable observations per hour.
            ``None`` is an expanding origin, which is the default here: with two
            years of history a rolling window throws away a quarter of the
            sample to buy adaptivity the series has not shown it needs.
        name: Identifier used in the scoreboard.
        description: One line for the published page.
    """

    alpha: float
    window: int | None = None
    name: str = "ridge"
    description: str = "Per-hour ridge on lagged prices, residual load and the calendar."

    def forecasts(
        self,
        panel: Panel,
        *,
        test_start: dt.date,
        test_end: dt.date | None = None,
        min_train_rows: int,
    ) -> pl.DataFrame:
        """Refit and predict for each hour, walking one day at a time."""
        if not panel.features or panel.frame.is_empty():
            return pl.DataFrame(schema=_FORECAST_SCHEMA)

        rows: list[dict[str, object]] = []
        complete = panel.complete()
        for hour in sorted(complete.get_column("local_hour").unique().to_list()):
            slice_ = complete.filter(pl.col("local_hour") == hour).sort("local_date")
            rows.extend(
                _walk_one_hour(
                    slice_,
                    features=panel.features,
                    target=panel.target,
                    alpha=self.alpha,
                    window=self.window,
                    test_start=test_start,
                    test_end=test_end,
                    min_train_rows=min_train_rows,
                )
            )

        if not rows:
            return pl.DataFrame(schema=_FORECAST_SCHEMA)
        return pl.DataFrame(rows, schema=_FORECAST_SCHEMA).sort(["local_date", "local_hour"])

    def coefficients(self, panel: Panel, *, before: dt.date) -> pl.DataFrame:
        """Fit once per hour on everything before ``before`` and report the weights.

        Published so a reader can see what the model actually leans on rather
        than taking the error table on faith. ``effect`` is the coefficient
        multiplied by the feature's standard deviation in the training window,
        which puts features carrying euros, megawatts and zero-one dummies on
        one comparable scale: it is the price move associated with a
        one-standard-deviation move in that feature.

        Returns:
            Columns ``local_hour``, ``feature``, ``coefficient``, ``train_sd``
            and ``effect``. Empty if nothing can be fitted before ``before``.
        """
        if not panel.features or panel.frame.is_empty():
            return pl.DataFrame(
                schema={
                    "local_hour": pl.Int8,
                    "feature": pl.String,
                    "coefficient": pl.Float64,
                    "train_sd": pl.Float64,
                    "effect": pl.Float64,
                }
            )

        features = list(panel.features)
        training = panel.complete().filter(pl.col("local_date") < before)
        rows: list[dict[str, object]] = []

        for hour in sorted(training.get_column("local_hour").unique().to_list()):
            slice_ = training.filter(pl.col("local_hour") == hour).sort("local_date")
            if self.window is not None:
                slice_ = slice_.tail(self.window)
            if slice_.height <= len(features):
                continue
            gram, cross = _moments(slice_, features, panel.target)
            coefficients = ridge_from_moments(gram, cross, alpha=self.alpha)
            deviations = slice_.select(pl.col(features).std(ddof=0)).row(0)
            for name, coefficient, sd in zip(features, coefficients[1:], deviations, strict=True):
                spread = float(sd or 0.0)
                rows.append(
                    {
                        "local_hour": hour,
                        "feature": name,
                        "coefficient": coefficient,
                        "train_sd": spread,
                        "effect": coefficient * spread,
                    }
                )

        return pl.DataFrame(
            rows,
            schema={
                "local_hour": pl.Int8,
                "feature": pl.String,
                "coefficient": pl.Float64,
                "train_sd": pl.Float64,
                "effect": pl.Float64,
            },
        )


# --- Walk-forward internals ------------------------------------------------


def _pair_indices(size: int) -> list[tuple[int, int]]:
    """Upper-triangle index pairs of a symmetric ``size x size`` matrix."""
    return [(a, b) for a in range(size) for b in range(a, size)]


def _cumulative_moments(
    slice_: pl.DataFrame, features: Sequence[str], target: str
) -> tuple[list[tuple[float, ...]], list[tuple[int, int]]]:
    """Cross-moments of every prefix of ``slice_``, in one pass.

    Row ``i`` of the result holds the moments over rows ``0`` through ``i``
    inclusive. That is the whole trick behind refitting daily: an expanding
    training window differs from the previous step by exactly one row, so the
    sequence of training matrices is a cumulative sum and polars produces all of
    them at once. A rolling window is then the difference of two of these rows.
    """
    columns = ["_one", *features]
    size = len(columns)
    pairs = _pair_indices(size)
    prepared = slice_.with_columns(pl.lit(1.0).alias("_one"))

    cumulative = prepared.select(
        *[
            (pl.col(columns[a]) * pl.col(columns[b])).cum_sum().alias(f"g{index}")
            for index, (a, b) in enumerate(pairs)
        ],
        *[(pl.col(columns[a]) * pl.col(target)).cum_sum().alias(f"c{a}") for a in range(size)],
    )
    return cumulative.rows(), pairs


def _moments(
    slice_: pl.DataFrame, features: Sequence[str], target: str
) -> tuple[list[list[float]], list[float]]:
    """Cross-moments over the whole of ``slice_``."""
    totals, pairs = _cumulative_moments(slice_, features, target)
    return _assemble(totals[-1], pairs, len(features) + 1)


def _assemble(
    totals: Sequence[float], pairs: Sequence[tuple[int, int]], size: int
) -> tuple[list[list[float]], list[float]]:
    """Expand a flat moment row into a symmetric matrix and a cross vector."""
    gram = [[0.0] * size for _ in range(size)]
    for index, (a, b) in enumerate(pairs):
        value = totals[index]
        gram[a][b] = value
        gram[b][a] = value
    return gram, list(totals[len(pairs) :])


def _walk_one_hour(
    slice_: pl.DataFrame,
    *,
    features: Sequence[str],
    target: str,
    alpha: float,
    window: int | None,
    test_start: dt.date,
    test_end: dt.date | None,
    min_train_rows: int,
) -> list[dict[str, object]]:
    """Forecast one hour of the day across the test period, refitting each step."""
    size = len(features) + 1
    floor = max(min_train_rows, size + 1)
    if window is not None and window < floor:
        raise ValueError(f"rolling window must hold at least {floor} observations")
    if slice_.height <= floor:
        return []

    totals, pairs = _cumulative_moments(slice_, features, target)
    design = slice_.select(list(features)).rows()
    dates = slice_.get_column("local_date").to_list()
    hour = int(slice_.get_column("local_hour").item(0))

    results: list[dict[str, object]] = []
    for index in range(floor, slice_.height):
        if dates[index] < test_start:
            continue
        if test_end is not None and dates[index] > test_end:
            break

        # Moments over the training rows, which are every row strictly before
        # this one. Nothing dated on or after the forecast day can enter here,
        # which is the walk-forward guarantee stated as an index bound.
        prefix = totals[index - 1]
        if window is not None and index - window - 1 >= 0:
            earlier = totals[index - window - 1]
            prefix = tuple(a - b for a, b in zip(prefix, earlier, strict=True))

        gram, cross = _assemble(prefix, pairs, size)
        coefficients = ridge_from_moments(gram, cross, alpha=alpha)
        results.append(
            {
                "local_date": dates[index],
                "local_hour": hour,
                "forecast": predict(coefficients, design[index]),
            }
        )

    return results
