"""Scores for a series that crosses zero: EUR/MWh errors, never percentages.

MAE asks whether a forecast is usually close; RMSE is dominated by the few badly
missed hours, which are the valuable ones, so both are published. Blocks, price
regimes, years and hours are scored separately because the aggregate hides the
hours that matter. Intervals are conformal: each model's own recent errors at the
same clock hour, applied identically to the naive baselines.
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

QUANTILE_LEVELS: Final = (0.1, 0.5, 0.9)
"""An eighty percent central interval and the median."""

CONFORMAL_WINDOW_DAYS: Final = 56
"""Past errors per clock hour behind each interval: eight weeks without gaps."""

SCARCITY_QUANTILE: Final = 0.95
"""Realised-price quantile above which an hour is labelled scarce, after the fact; never an input."""


def quantile_column(level: float) -> str:
    return f"q{round(level * 100):02d}"


_PINBALL = [f"pinball_{quantile_column(level)}" for level in QUANTILE_LEVELS]


def attach_quantiles(
    predictions: pl.DataFrame, *, window: int = CONFORMAL_WINDOW_DAYS
) -> pl.DataFrame:
    """Point forecast plus quantiles of the model's last ``window`` errors at that hour.

    Errors are shifted one observation, so day D's interval uses only errors known on
    D-1; the first ``window`` observations per hour carry null quantiles.
    """
    if window < 2:
        raise ValueError(f"window must be at least 2 days, got {window}")
    errors = (pl.col("actual") - pl.col("forecast")).shift(1)
    return predictions.sort("model", "local_hour", "local_date").with_columns(
        (
            pl.col("forecast")
            + errors.rolling_quantile(
                level, interpolation="linear", window_size=window, min_samples=window
            ).over("model", "local_hour")
        ).alias(quantile_column(level))
        for level in QUANTILE_LEVELS
    )


def attach_regime(
    predictions: pl.DataFrame, *, scarcity_quantile: float = SCARCITY_QUANTILE
) -> pl.DataFrame:
    """Label each hour negative, scarce or normal by its realised price.

    The threshold is taken over distinct hours, so adding a model cannot move it.
    """
    if not 0.0 < scarcity_quantile < 1.0:
        raise ValueError(
            f"scarcity_quantile must be strictly between 0 and 1, got {scarcity_quantile}"
        )
    hours = predictions.unique(subset=["local_date", "local_hour"])["actual"]
    threshold = hours.quantile(scarcity_quantile, interpolation="linear")
    return predictions.with_columns(
        pl.when(pl.col("actual") < 0.0)
        .then(pl.lit("negative"))
        .when(pl.col("actual") >= threshold)
        .then(pl.lit("scarcity"))
        .otherwise(pl.lit("normal"))
        .alias("regime")
    )


def _pinball(level: float) -> pl.Expr:
    gap = pl.col("actual") - pl.col(quantile_column(level))
    return pl.max_horizontal(level * gap, (level - 1.0) * gap).mean()


def _metrics() -> list[pl.Expr]:
    error = pl.col("actual") - pl.col("forecast")
    lower, upper = (
        pl.col(quantile_column(level)) for level in (QUANTILE_LEVELS[0], QUANTILE_LEVELS[-1])
    )
    # Scored before the site rounds predictions: a boundary rounded to cents can flip coverage.
    available = pl.all_horizontal(
        pl.col(quantile_column(level)).is_not_null() for level in QUANTILE_LEVELS
    )
    pinball = [
        _pinball(level).alias(name) for level, name in zip(QUANTILE_LEVELS, _PINBALL, strict=True)
    ]
    return [
        pl.len().cast(pl.UInt32).alias("n"),
        error.abs().mean().alias("mae"),
        error.pow(2).mean().sqrt().alias("rmse"),
        error.mean().alias("bias"),
        pl.col("actual").mean().alias("mean_actual"),
        available.sum().cast(pl.UInt32).alias("n_interval"),
        (pl.col("actual").is_between(lower, upper).filter(available).mean() * 100.0).alias(
            "interval_coverage_pct"
        ),
        (upper - lower).filter(available).mean().alias("interval_mean_width"),
        *pinball,
    ]


def _skill(against: str) -> pl.Expr:
    """Percent MAE improvement over ``against``; negative numbers are published too."""
    return pl.when(pl.col(against) > 0).then((1.0 - pl.col("mae") / pl.col(against)) * 100.0)


def scoreboard(
    predictions: pl.DataFrame,
    zone: Zone,
    *,
    reference: str,
    baselines: Sequence[str] = (),
    scarcity_quantile: float = SCARCITY_QUANTILE,
) -> pl.DataFrame:
    """Every model scored overall and by block, regime, year and hour.

    ``skill_pct`` is against the reference fixed in advance; ``skill_vs_best_baseline_pct``
    is against whichever naive won that bucket, the harder test a sceptic would apply.
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
        *_PINBALL,
        "mean_pinball",
        "skill_pct",
        "skill_vs_best_baseline_pct",
    ]
    if predictions.is_empty():
        return pl.DataFrame(schema=dict.fromkeys(columns, pl.Float64))
    models = predictions["model"].unique().to_list()
    if reference not in models:
        raise ValueError(f"reference {reference!r} is not among the scored models {sorted(models)}")
    prepared = attach_block(attach_regime(predictions, scarcity_quantile=scarcity_quantile), zone)
    buckets = {
        "overall": pl.lit("all"),
        "block": pl.col("block"),
        "regime": pl.col("regime"),
        "year": pl.col("local_date").dt.year().cast(pl.String),
        "hour": pl.col("local_hour").cast(pl.String).str.zfill(2),
    }
    scopes = pl.concat(
        prepared.with_columns(bucket.alias("bucket"))
        .group_by("model", "bucket")
        .agg(_metrics())
        .with_columns(pl.lit(scope).alias("scope"))
        for scope, bucket in buckets.items()
    )
    keys = ["scope", "bucket"]
    best = (
        scopes.filter(pl.col("model").is_in(list(baselines)))
        .group_by(keys)
        .agg(pl.col("mae").min().alias("_best"))
    )
    return (
        scopes.join(
            scopes.filter(pl.col("model") == reference).select(
                *keys, pl.col("mae").alias("_reference")
            ),
            on=keys,
            how="left",
        )
        .join(best, on=keys, how="left")
        .with_columns(
            pl.mean_horizontal(_PINBALL).alias("mean_pinball"),
            _skill("_reference").alias("skill_pct"),
            _skill("_best").alias("skill_vs_best_baseline_pct"),
        )
        .select(columns)
        .sort("scope", "bucket", "mae")
    )


def daily_errors(predictions: pl.DataFrame) -> pl.DataFrame:
    """MAE per model per local day: when a model stopped working, not just whether."""
    return (
        predictions.group_by("model", "local_date")
        .agg(
            pl.len().cast(pl.UInt32).alias("n"),
            (pl.col("actual") - pl.col("forecast")).abs().mean().alias("mae"),
            pl.col("actual").mean().alias("mean_actual"),
        )
        .sort("model", "local_date")
    )
