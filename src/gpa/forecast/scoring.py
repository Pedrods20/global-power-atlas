"""Error metrics chosen for power prices, and the breakdowns that matter.

Three decisions here differ from a generic forecasting scorecard.

**No percentage errors.** MAPE and its relatives are the default in most
forecasting work and are unusable on this series: the denominator passes
through zero several hundred times a year in Germany and goes negative after
that. A single hour clearing at 0.01 EUR/MWh would dominate the annual figure.
Errors are reported in currency per megawatt hour, which is also the unit a
reader can price against.

**MAE and RMSE together, because they disagree on purpose.** MAE is the mean
absolute error and asks whether the forecast is usually close. RMSE is dominated by the
few hours that were missed badly, which in a power market are the hours worth
money. A model that wins on MAE and loses on RMSE has bought a flat middle by
giving up the tails, and the two columns side by side make that visible.

**Blocks and regimes are reported separately, not summed.** An aggregate error
over a year of hourly prices hides the only hours anyone cares about. The
scarce hours and the negative hours are a small minority of the sample and
carry a large share of the value, so they are scored on their own.

Quantiles are produced by a conformal wrapper rather than by a quantile
regression: the recent distribution of a model's *own* out-of-sample errors,
taken per hour of the day, is added to its point forecast. It costs one line,
it applies identically to every model including the naive baselines so the
interval comparison is fair, and it uses only errors from days that had already
been scored when the forecast was made.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import polars as pl

from gpa.calendar import attach_block
from gpa.zones import Zone

__all__ = [
    "CONFORMAL_WINDOW_DAYS",
    "QUANTILE_LEVELS",
    "SCARCITY_QUANTILE",
    "attach_quantiles",
    "attach_regime",
    "daily_errors",
    "quantile_column",
    "scoreboard",
]

QUANTILE_LEVELS: Final[tuple[float, ...]] = (0.1, 0.5, 0.9)
"""Levels published. An eighty percent central interval plus the median."""

CONFORMAL_WINDOW_DAYS: Final = 56
"""Past observed errors, per hour of the day, behind each interval.

Eight weeks when there are no gaps; otherwise 56 observations can span longer.
The window is per hour of the day, so hour 19 is calibrated on hour 19.
"""

SCARCITY_QUANTILE: Final = 0.95
"""Realised-price quantile above which an hour is reported as scarce.

Defined on the realised price of the evaluation sample, after the fact. It is a
diagnostic label for reading the results, never an input: no model is told which
regime it is forecasting, and knowing would be knowing the answer.
"""

_REGIME_NEGATIVE: Final = "negative"
_REGIME_SCARCITY: Final = "scarcity"
_REGIME_NORMAL: Final = "normal"


def quantile_column(level: float) -> str:
    """Name of the column holding the forecast at ``level``."""
    return f"q{round(level * 100):02d}"


def attach_quantiles(
    predictions: pl.DataFrame,
    *,
    levels: Sequence[float] = QUANTILE_LEVELS,
    window: int = CONFORMAL_WINDOW_DAYS,
) -> pl.DataFrame:
    """Add a predictive interval around each point forecast.

    For each model and each hour of the day, the empirical quantiles of the last
    ``window`` observed errors are added to the point forecast. The error
    series is shifted by one observation first, so an interval for day D uses only
    from errors that were already observable on D-1.

    The first ``window`` test observations per hour carry null quantiles. They
    are scored on their point forecast only; the pinball columns ignore them.

    Args:
        predictions: Columns ``model``, ``local_date``, ``local_hour``,
            ``actual`` and ``forecast``.
        levels: Quantile levels to produce.
        window: Observed past errors per clock hour behind each estimate.

    Returns:
        ``predictions`` with one column per level, named by
        :func:`quantile_column`.

    Raises:
        ValueError: If a level is not strictly between zero and one, or the
            window is shorter than two days.
    """
    for level in levels:
        if not 0.0 < level < 1.0:
            raise ValueError(f"quantile levels must be strictly between 0 and 1, got {level}")
    if len({quantile_column(level) for level in levels}) != len(levels):
        raise ValueError("quantile levels must have distinct percentage column names")
    if window < 2:
        raise ValueError(f"window must be at least 2 days, got {window}")

    if predictions.is_empty():
        schema = pl.Schema(predictions.schema)
        schema.update({quantile_column(level): pl.Float64() for level in levels})
        return pl.DataFrame(schema=schema)

    ordered = predictions.sort(["model", "local_hour", "local_date"]).with_columns(
        (pl.col("actual") - pl.col("forecast")).alias("_error")
    )
    return ordered.with_columns(
        *[
            (
                pl.col("forecast")
                + pl.col("_error")
                .shift(1)
                .rolling_quantile(
                    level, interpolation="linear", window_size=window, min_samples=window
                )
                .over(["model", "local_hour"])
            ).alias(quantile_column(level))
            for level in levels
        ]
    ).drop("_error")


def attach_regime(
    predictions: pl.DataFrame, *, scarcity_quantile: float = SCARCITY_QUANTILE
) -> pl.DataFrame:
    """Label each hour negative, scarce or normal by its realised price.

    The threshold is taken over the distinct hours of the evaluation sample, not
    over the rows, so it does not shift when a model is added or removed.

    Raises:
        ValueError: If ``scarcity_quantile`` is not strictly between zero and one.
    """
    if not 0.0 < scarcity_quantile < 1.0:
        raise ValueError(
            f"scarcity_quantile must be strictly between 0 and 1, got {scarcity_quantile}"
        )
    if predictions.is_empty():
        return predictions.with_columns(pl.lit(None, dtype=pl.String).alias("regime"))

    hours = predictions.unique(subset=["local_date", "local_hour"]).get_column("actual")
    threshold = hours.quantile(scarcity_quantile, interpolation="linear")

    return predictions.with_columns(
        pl.when(pl.col("actual") < 0.0)
        .then(pl.lit(_REGIME_NEGATIVE))
        .when(pl.col("actual") >= threshold)
        .then(pl.lit(_REGIME_SCARCITY))
        .otherwise(pl.lit(_REGIME_NORMAL))
        .alias("regime")
    )


def _metrics(levels: Sequence[float]) -> list[pl.Expr]:
    """Aggregations applied within every scope of the scoreboard."""
    error = pl.col("actual") - pl.col("forecast")
    aggregations = [
        pl.len().cast(pl.UInt32).alias("n"),
        error.abs().mean().alias("mae"),
        (error.pow(2).mean().sqrt()).alias("rmse"),
        error.mean().alias("bias"),
        pl.col("actual").mean().alias("mean_actual"),
    ]
    # Score intervals before the site export rounds predictions. A boundary
    # rounded to cents can otherwise change whether an observation is covered.
    available = (
        pl.all_horizontal([pl.col(quantile_column(level)).is_not_null() for level in levels])
        if levels
        else pl.lit(False)
    )
    aggregations.append(
        available.sum().cast(pl.UInt32).alias("n_interval")
        if levels
        else pl.lit(0, dtype=pl.UInt32).alias("n_interval")
    )
    if len(levels) >= 2:
        lower = pl.col(quantile_column(min(levels)))
        upper = pl.col(quantile_column(max(levels)))
        aggregations.extend(
            [
                (pl.col("actual").is_between(lower, upper).filter(available).mean() * 100.0).alias(
                    "interval_coverage_pct"
                ),
                (upper - lower).filter(available).mean().alias("interval_mean_width"),
            ]
        )
    else:
        aggregations.extend(
            pl.lit(None, dtype=pl.Float64).alias(name)
            for name in ("interval_coverage_pct", "interval_mean_width")
        )
    for level in levels:
        column = quantile_column(level)
        gap = pl.col("actual") - pl.col(column)
        loss = pl.max_horizontal(level * gap, (level - 1.0) * gap)
        aggregations.append(loss.mean().alias(f"pinball_{column}"))
    return aggregations


def _scope(
    predictions: pl.DataFrame, *, scope: str, bucket: pl.Expr, levels: Sequence[float]
) -> pl.DataFrame:
    return (
        predictions.with_columns(bucket.alias("bucket"))
        .group_by(["model", "bucket"])
        .agg(_metrics(levels))
        .with_columns(pl.lit(scope).alias("scope"))
    )


def scoreboard(
    predictions: pl.DataFrame,
    zone: Zone,
    *,
    reference: str,
    baselines: Sequence[str] = (),
    levels: Sequence[float] = QUANTILE_LEVELS,
    scarcity_quantile: float = SCARCITY_QUANTILE,
) -> pl.DataFrame:
    """Score every model overall and within each block, regime and hour.

    Two skill columns are published rather than one, because they can disagree
    and the difference is the honest part.

    ``skill_pct`` measures against the declared ``reference``, fixed in advance
    so that the yardstick cannot be chosen after the results are in.
    ``skill_vs_best_baseline_pct`` measures against whichever baseline actually
    won that bucket, which is the harder test and the one a sceptical reader
    would apply. A model can show comfortable skill against the reference and
    none at all against the best baseline, and where that happens here it is
    printed rather than smoothed over.

    Both are percentage improvements in MAE *within the same bucket*. Positive
    beats the baseline, negative loses to it, and negative numbers are
    published.

    Args:
        predictions: Columns ``model``, ``local_date``, ``local_hour``,
            ``actual``, ``forecast`` and one column per quantile level.
        zone: Supplies the market block definition and timezone.
        reference: Name of the model every other is measured against.
        baselines: Names of the naive baselines. ``skill_vs_best_baseline_pct``
            is null when none are given.
        levels: Quantile levels present in ``predictions``.
        scarcity_quantile: Passed to :func:`attach_regime`.

    Returns:
        Columns ``scope``, ``bucket``, ``model``, ``n``, ``n_interval``,
        ``interval_coverage_pct``, ``interval_mean_width``, ``mae``, ``rmse``,
        ``bias``, ``mean_actual``, the pinball columns,
        ``mean_pinball``, ``skill_pct`` and ``skill_vs_best_baseline_pct``.

    Raises:
        ValueError: If ``reference`` is not among the models scored.
    """
    columns = [
        "scope",
        "bucket",
        "model",
        "n",
        "n_interval",
        "interval_coverage_pct",
        "interval_mean_width",
        "mae",
        "rmse",
        "bias",
        "mean_actual",
        *[f"pinball_{quantile_column(level)}" for level in levels],
        "mean_pinball",
        "skill_pct",
        "skill_vs_best_baseline_pct",
    ]
    if predictions.is_empty():
        return pl.DataFrame(schema={name: pl.Float64 for name in columns})

    models = predictions.get_column("model").unique().to_list()
    if reference not in models:
        raise ValueError(f"reference {reference!r} is not among the scored models {sorted(models)}")

    prepared = attach_regime(predictions, scarcity_quantile=scarcity_quantile)
    prepared = attach_block(prepared, zone)

    scopes = pl.concat(
        [
            _scope(prepared, scope="overall", bucket=pl.lit("all"), levels=levels),
            _scope(prepared, scope="block", bucket=pl.col("block"), levels=levels),
            _scope(prepared, scope="regime", bucket=pl.col("regime"), levels=levels),
            _scope(
                prepared,
                scope="hour",
                bucket=pl.col("local_hour").cast(pl.String).str.zfill(2),
                levels=levels,
            ),
        ],
        how="vertical",
    )

    pinball_columns = [f"pinball_{quantile_column(level)}" for level in levels]
    reference_mae = scopes.filter(pl.col("model") == reference).select(
        "scope", "bucket", pl.col("mae").alias("_reference_mae")
    )
    scopes = scopes.join(reference_mae, on=["scope", "bucket"], how="left")

    if baselines:
        best = (
            scopes.filter(pl.col("model").is_in(list(baselines)))
            .group_by(["scope", "bucket"])
            .agg(pl.col("mae").min().alias("_best_baseline_mae"))
        )
        scopes = scopes.join(best, on=["scope", "bucket"], how="left")
    else:
        scopes = scopes.with_columns(pl.lit(None, dtype=pl.Float64).alias("_best_baseline_mae"))

    return (
        scopes.with_columns(
            pl.mean_horizontal(pinball_columns).alias("mean_pinball")
            if pinball_columns
            else pl.lit(None, dtype=pl.Float64).alias("mean_pinball"),
            _skill("_reference_mae").alias("skill_pct"),
            _skill("_best_baseline_mae").alias("skill_vs_best_baseline_pct"),
        )
        .select(columns)
        .sort(["scope", "bucket", "mae"])
    )


def _skill(against: str) -> pl.Expr:
    """Percentage improvement in MAE over the value in column ``against``."""
    return (
        pl.when(pl.col(against) > 0)
        .then((1.0 - pl.col("mae") / pl.col(against)) * 100.0)
        .otherwise(None)
    )


def daily_errors(predictions: pl.DataFrame) -> pl.DataFrame:
    """Mean absolute error per model per local day, for the error-over-time chart.

    A single annual number says whether a model works on average. This says when
    it stopped working, which is the question a reader who has to rely on it
    would ask next.
    """
    if predictions.is_empty():
        return pl.DataFrame(
            schema={
                "model": pl.String,
                "local_date": pl.Date,
                "n": pl.UInt32,
                "mae": pl.Float64,
                "mean_actual": pl.Float64,
            }
        )

    return (
        predictions.group_by(["model", "local_date"])
        .agg(
            pl.len().cast(pl.UInt32).alias("n"),
            (pl.col("actual") - pl.col("forecast")).abs().mean().alias("mae"),
            pl.col("actual").mean().alias("mean_actual"),
        )
        .sort(["model", "local_date"])
    )
