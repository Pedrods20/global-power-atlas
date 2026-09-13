"""Price analytics: blocks, duration curves, negative prices and volatility.

Two conventions in this module differ from what a generic time-series library
would do, and both differences are deliberate.

**No log returns.** Power prices reach zero and go negative, so ``log(p_t/p_t-1)``
is undefined exactly when the market is doing the interesting thing. Every
return here is an arithmetic difference in money per MWh. That also keeps the
units interpretable: a volatility of 30 means 30 currency units per MWh, not an
abstract percentage of an undefined base.

**Annualisation uses 365 days, not 252.** The 252 convention counts the trading
days of a financial exchange. Spot electricity settles every day of the year,
including weekends and holidays, so scaling by the square root of 252
understates annualised volatility by about twenty percent.
"""

from __future__ import annotations

import polars as pl

from gpa.calendar import BLOCK_OFF_PEAK, BLOCK_ON_PEAK, attach_block, attach_local_time
from gpa.metrics.load import _duration_curve
from gpa.zones import Zone

__all__ = [
    "DAYS_PER_YEAR",
    "block_prices",
    "capture_rate",
    "duration_curve",
    "negative_price_summary",
    "price_spikes",
    "realised_volatility",
]

DAYS_PER_YEAR = 365
"""Spot power settles every calendar day. See the module docstring."""

_INTERVAL_HOURS = pl.col("resolution_min").cast(pl.Float64) / 60.0


def duration_curve(frame: pl.DataFrame, *, points: int | None = None) -> pl.DataFrame:
    """Price duration curve: price sorted descending against exceedance.

    Read the two ends. The left tail is scarcity, and a market that earns its
    fixed costs in a handful of hours lives there. The right tail crossing zero
    is renewable surplus, and its width is the clearest single measure of how
    often the system has more must-run output than demand.

    Returns:
        Columns ``exceedance_pct`` and ``price``, descending by price.
    """
    return _duration_curve(frame, "price", points)


def block_prices(frame: pl.DataFrame, zone: Zone, *, period: str = "month") -> pl.DataFrame:
    """Average price by market block, and the peak-to-off-peak spread.

    The spread is the genuine article: the mean of on-peak intervals minus the
    mean of off-peak intervals, where membership follows the market's own block
    definition. It is not the daily maximum minus the daily minimum, which is
    intraday range and a much larger and more volatile number.

    A negative spread is not an error. It is the signature of solar
    cannibalisation, where midday output pushes the on-peak block below the
    hours around it, and it is now routine in Germany, Spain, California and
    South Australia.

    Args:
        frame: Rows matching the ``price`` schema.
        zone: Supplies the block definition and market timezone.
        period: ``"day"``, ``"month"`` or ``"year"``.

    Returns:
        Columns ``period``, ``on_peak``, ``off_peak``, ``spread``, ``all_hours``
        and the interval counts behind each block.

    Raises:
        ValueError: If ``period`` is not a supported grouping.
    """
    formats = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}
    if period not in formats:
        raise ValueError(f"period must be one of {sorted(formats)}, got {period!r}")

    empty = pl.DataFrame(
        schema={
            "period": pl.String,
            "on_peak": pl.Float64,
            "off_peak": pl.Float64,
            "spread": pl.Float64,
            "all_hours": pl.Float64,
            "n_on_peak": pl.UInt32,
            "n_off_peak": pl.UInt32,
        }
    )
    if frame.is_empty():
        return empty

    prepared = attach_block(frame, zone).with_columns(
        pl.col("local_date").dt.strftime(formats[period]).alias("period")
    )

    on = pl.col("price").filter(pl.col("block") == BLOCK_ON_PEAK)
    off = pl.col("price").filter(pl.col("block") == BLOCK_OFF_PEAK)

    return (
        prepared.group_by("period")
        .agg(
            (
                (pl.col("price") * _INTERVAL_HOURS).filter(pl.col("block") == BLOCK_ON_PEAK).sum()
                / _INTERVAL_HOURS.filter(pl.col("block") == BLOCK_ON_PEAK).sum()
            ).alias("on_peak"),
            (
                (pl.col("price") * _INTERVAL_HOURS).filter(pl.col("block") == BLOCK_OFF_PEAK).sum()
                / _INTERVAL_HOURS.filter(pl.col("block") == BLOCK_OFF_PEAK).sum()
            ).alias("off_peak"),
            ((pl.col("price") * _INTERVAL_HOURS).sum() / _INTERVAL_HOURS.sum()).alias("all_hours"),
            on.len().alias("n_on_peak"),
            off.len().alias("n_off_peak"),
        )
        .with_columns(pl.col("on_peak", "off_peak").fill_nan(None))
        .with_columns((pl.col("on_peak") - pl.col("off_peak")).alias("spread"))
        .select("period", "on_peak", "off_peak", "spread", "all_hours", "n_on_peak", "n_off_peak")
        .sort("period")
    )


def negative_price_summary(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """How often, how deep and for how long price went below zero, by local month.

    Negative prices happen when inflexible output plus must-run generation
    exceeds demand and it is cheaper to pay to offload energy than to shut down
    and restart. Their frequency tracks renewable penetration against system
    flexibility, so this is a transition indicator, not a data quality problem.

    ``max_run_hours`` is the longest unbroken stretch below zero in the month.
    Frequency alone understates the problem: many isolated negative intervals
    are a nuisance, while one long run is a curtailment event.

    Returns:
        Columns ``local_month``, ``n_intervals``, ``n_negative``,
        ``negative_pct``, ``negative_hours``, ``min_price``, ``mean_negative``
        and ``max_run_hours``.
    """
    empty = pl.DataFrame(
        schema={
            "local_month": pl.String,
            "n_intervals": pl.UInt32,
            "n_negative": pl.UInt32,
            "negative_pct": pl.Float64,
            "negative_hours": pl.Float64,
            "observed_hours": pl.Float64,
            "min_price": pl.Float64,
            "mean_negative": pl.Float64,
            "max_run_hours": pl.Float64,
        }
    )
    if frame.is_empty():
        return empty

    prepared = (
        attach_local_time(frame, zone)
        .sort("ts_utc")
        .with_columns(
            pl.col("local_date").dt.strftime("%Y-%m").alias("local_month"),
            (pl.col("price") < 0).alias("_neg"),
        )
        # A run is a maximal block of consecutive negative intervals. Numbering
        # the transitions gives each run a distinct id to group on.
        .with_columns(
            (
                (pl.col("_neg") != pl.col("_neg").shift(1))
                | (pl.col("ts_utc").diff().dt.total_minutes() != pl.col("resolution_min").shift(1))
            )
            .fill_null(True)
            .cum_sum()
            .alias("_run")
        )
    )

    runs = (
        prepared.filter(pl.col("_neg"))
        .group_by(["local_month", "_run"])
        .agg((_INTERVAL_HOURS).sum().alias("run_hours"))
        .group_by("local_month")
        .agg(pl.col("run_hours").max().alias("max_run_hours"))
    )

    summary = prepared.group_by("local_month").agg(
        pl.len().alias("n_intervals"),
        pl.col("_neg").sum().cast(pl.UInt32).alias("n_negative"),
        (_INTERVAL_HOURS.filter(pl.col("_neg"))).sum().alias("negative_hours"),
        _INTERVAL_HOURS.sum().alias("observed_hours"),
        pl.col("price").min().alias("min_price"),
        (
            (pl.col("price") * _INTERVAL_HOURS).filter(pl.col("_neg")).sum()
            / _INTERVAL_HOURS.filter(pl.col("_neg")).sum()
        )
        .fill_nan(None)
        .alias("mean_negative"),
    )

    return (
        summary.join(runs, on="local_month", how="left")
        .with_columns(
            (pl.col("negative_hours") / pl.col("observed_hours") * 100.0).alias("negative_pct"),
            pl.col("max_run_hours").fill_null(0.0),
        )
        .select(
            "local_month",
            "n_intervals",
            "n_negative",
            "negative_pct",
            "negative_hours",
            "observed_hours",
            "min_price",
            "mean_negative",
            "max_run_hours",
        )
        .sort("local_month")
    )


def price_spikes(frame: pl.DataFrame, zone: Zone, *, quantile: float = 0.99) -> pl.DataFrame:
    """Upper-tail statistics, and how concentrated revenue is in few intervals.

    ``share_of_value_pct`` is the fraction of the period's total price-hours
    delivered by intervals above the threshold. In a scarcity-priced market a
    single-digit percentage of intervals routinely carries a large share of
    annual value, which is why averages mislead and why peaking assets are
    valued on the tail rather than the mean.

    Args:
        frame: Rows matching the ``price`` schema.
        zone: Supplies the market timezone.
        quantile: Tail cut-off, between 0 and 1 exclusive.

    Returns:
        One row per local month with the threshold, tail count, tail mean,
        maximum and the tail's share of total value.

    Raises:
        ValueError: If ``quantile`` is not strictly between 0 and 1.
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must be strictly between 0 and 1, got {quantile}")

    empty = pl.DataFrame(
        schema={
            "local_month": pl.String,
            "threshold": pl.Float64,
            "n_above": pl.UInt32,
            "mean_above": pl.Float64,
            "max_price": pl.Float64,
            "share_of_value_pct": pl.Float64,
        }
    )
    if frame.is_empty():
        return empty

    prepared = attach_local_time(frame, zone).with_columns(
        pl.col("local_date").dt.strftime("%Y-%m").alias("local_month"),
        (pl.col("price") * _INTERVAL_HOURS).alias("_value"),
    )

    threshold = pl.col("price").quantile(quantile, interpolation="linear")
    above = pl.col("price") >= threshold

    return (
        prepared.group_by("local_month")
        .agg(
            threshold.alias("threshold"),
            above.sum().cast(pl.UInt32).alias("n_above"),
            pl.col("price").filter(above).mean().alias("mean_above"),
            pl.col("price").max().alias("max_price"),
            (pl.col("_value").filter(above).sum() / pl.col("_value").sum() * 100.0).alias(
                "share_of_value_pct"
            ),
        )
        .sort("local_month")
    )


def realised_volatility(
    frame: pl.DataFrame,
    zone: Zone,
    *,
    window: int = 30,
    annualise: bool = True,
) -> pl.DataFrame:
    """Rolling volatility of daily mean price, in currency per MWh.

    The daily mean is taken over market-local days, then volatility is the
    rolling standard deviation of its day-on-day arithmetic change. Arithmetic
    rather than logarithmic, for the reason in the module docstring: prices go
    negative, and a log return would be undefined precisely there.

    Args:
        frame: Rows matching the ``price`` schema.
        zone: Supplies the market timezone.
        window: Number of days in the rolling window.
        annualise: Scale by the square root of 365.

    Returns:
        Columns ``local_date``, ``daily_price``, ``daily_change`` and
        ``volatility``. The first ``window`` days carry a null volatility.

    Raises:
        ValueError: If ``window`` is less than 2.
    """
    if window < 2:
        raise ValueError(f"window must be at least 2, got {window}")

    empty = pl.DataFrame(
        schema={
            "local_date": pl.Date,
            "daily_price": pl.Float64,
            "daily_change": pl.Float64,
            "volatility": pl.Float64,
        }
    )
    if frame.is_empty():
        return empty

    daily = (
        attach_local_time(frame, zone)
        .group_by("local_date")
        .agg(
            ((pl.col("price") * _INTERVAL_HOURS).sum() / _INTERVAL_HOURS.sum()).alias("daily_price")
        )
        .sort("local_date")
    )
    if daily.height < 2:
        return empty

    scale = DAYS_PER_YEAR**0.5 if annualise else 1.0

    # Insert absent calendar dates as nulls in this derived table only. No
    # provider observation is filled, and rolling windows cannot bridge a gap.
    daily = daily.upsample(time_column="local_date", every="1d")
    return daily.with_columns(pl.col("daily_price").diff().alias("daily_change")).with_columns(
        (pl.col("daily_change").rolling_std(window_size=window, min_samples=window) * scale).alias(
            "volatility"
        )
    )


def capture_rate(
    prices: pl.DataFrame,
    generation: pl.DataFrame,
    zone: Zone,
    *,
    fuel: str,
    period: str = "month",
) -> pl.DataFrame:
    """Generation-weighted capture price and capture rate for one technology.

    The capture price is what a technology actually earns: its output weighted
    by the price prevailing when that output happened. The capture rate is that
    divided by the simple time-weighted average price. A rate below one means
    the technology produces when the market is cheap, which is the mechanism by
    which solar erodes its own revenue as penetration grows.

    This is weighted by megawatt-hours generated, not by a fixed window of
    "solar hours". A fixed-hours approximation ignores cloud, season and the
    installed base, and drifts further from the truth the more it matters.

    Args:
        prices: Rows matching the ``price`` schema.
        generation: Rows matching the ``generation`` schema.
        zone: Supplies the market timezone.
        fuel: Canonical fuel to evaluate.
        period: ``"day"``, ``"month"``, ``"year"`` or ``"all"``.

    Returns:
        Columns ``period``, ``capture_price``, ``baseload_price``,
        ``capture_rate`` and ``energy_mwh``. Empty if the two frames share no
        timestamps, which happens when a zone's price and generation come from
        providers on different resolutions with no overlap.

    Raises:
        ValueError: If ``period`` is unsupported.
    """
    formats: dict[str, str | None] = {
        "day": "%Y-%m-%d",
        "month": "%Y-%m",
        "year": "%Y",
        "all": None,
    }
    if period not in formats:
        raise ValueError(f"period must be one of {sorted(formats)}, got {period!r}")

    empty = pl.DataFrame(
        schema={
            "period": pl.String,
            "capture_price": pl.Float64,
            "baseload_price": pl.Float64,
            "capture_rate": pl.Float64,
            "energy_mwh": pl.Float64,
        }
    )
    if prices.is_empty() or generation.is_empty():
        return empty

    fuel_gen = generation.filter(pl.col("fuel") == fuel)
    if fuel_gen.is_empty():
        return empty

    # Integrate intersections of the actual UTC intervals. Values describe
    # average power/price over each published interval; no subinterval shape
    # is invented, and missing intervals never get forward-filled past the end.
    p = prices.select(
        "ts_utc",
        "price",
        (pl.col("ts_utc") + pl.duration(minutes=pl.col("resolution_min"))).alias("_price_end"),
    ).sort("ts_utc")
    g = fuel_gen.select(
        "ts_utc",
        "gen_mw",
        (pl.col("ts_utc") + pl.duration(minutes=pl.col("resolution_min"))).alias("_gen_end"),
    ).sort("ts_utc")
    boundaries = (
        pl.concat(
            [
                p.select("ts_utc"),
                p.select(pl.col("_price_end").alias("ts_utc")),
                g.select("ts_utc"),
                g.select(pl.col("_gen_end").alias("ts_utc")),
            ]
        )
        .unique()
        .sort("ts_utc")
    )
    joined = (
        boundaries.with_columns(pl.col("ts_utc").shift(-1).alias("_end"))
        .join_asof(p, on="ts_utc")
        .join_asof(g, on="ts_utc")
        .filter((pl.col("_end") <= pl.col("_price_end")) & (pl.col("_end") <= pl.col("_gen_end")))
        .with_columns(
            ((pl.col("_end") - pl.col("ts_utc")).dt.total_seconds() / 3600.0).alias("_hours")
        )
        .with_columns((pl.col("gen_mw") * pl.col("_hours")).alias("energy_mwh"))
    )
    if joined.is_empty():
        return empty

    fmt = formats[period]
    return (
        attach_local_time(joined, zone)
        .with_columns(
            pl.lit("all").alias("period")
            if fmt is None
            else pl.col("local_date").dt.strftime(fmt).alias("period")
        )
        .group_by("period")
        .agg(
            (pl.col("price") * pl.col("energy_mwh")).sum().alias("_weighted"),
            pl.col("energy_mwh").sum().alias("energy_mwh"),
            ((pl.col("price") * pl.col("_hours")).sum() / pl.col("_hours").sum()).alias(
                "baseload_price"
            ),
        )
        .with_columns(
            pl.when(pl.col("energy_mwh") > 0)
            .then(pl.col("_weighted") / pl.col("energy_mwh"))
            .otherwise(None)
            .alias("capture_price")
        )
        .with_columns(
            pl.when(pl.col("baseload_price") != 0)
            .then(pl.col("capture_price") / pl.col("baseload_price"))
            .otherwise(None)
            .alias("capture_rate")
        )
        .select("period", "capture_price", "baseload_price", "capture_rate", "energy_mwh")
        .sort("period")
    )
