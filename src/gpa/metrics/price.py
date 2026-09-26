"""Price analytics for a market that clears below zero: blocks, daily range, shape, capture.

Every return is an arithmetic difference in EUR/MWh; a log return is undefined
exactly when the market is doing the interesting thing.
"""

from __future__ import annotations

import polars as pl

from gpa.calendar import (
    BLOCK_OFF_PEAK,
    BLOCK_ON_PEAK,
    INTERVAL_HOURS,
    attach_block,
    attach_local_time,
    period_label,
)
from gpa.zones import Zone

__all__ = ["block_prices", "capture_rate", "hourly_shape", "intraday_spread", "negative_share"]

_COMPLETE_DAY_HOURS = 23.0
"""A day is complete at 23 observed hours (spring clock change); partial days are dropped."""


def _mean_price(mask: pl.Expr | None = None) -> pl.Expr:
    price, hours = pl.col("price") * INTERVAL_HOURS, INTERVAL_HOURS
    if mask is not None:
        price, hours = price.filter(mask), hours.filter(mask)
    return price.sum() / hours.sum()


def block_prices(frame: pl.DataFrame, zone: Zone, *, period: str = "month") -> pl.DataFrame:
    """Duration-weighted on-peak, off-peak and baseload price, and the block spread.

    The spread is the market's block difference, not the daily high minus low; a
    negative spread is the signature of solar cannibalisation.
    """
    label = period_label(period)
    columns = {
        "period": pl.String,
        "on_peak": pl.Float64,
        "off_peak": pl.Float64,
        "spread": pl.Float64,
        "all_hours": pl.Float64,
        "n_on_peak": pl.UInt32,
        "n_off_peak": pl.UInt32,
    }
    if frame.is_empty():
        return pl.DataFrame(schema=columns)
    on, off = pl.col("block") == BLOCK_ON_PEAK, pl.col("block") == BLOCK_OFF_PEAK
    return (
        attach_block(frame, zone)
        .with_columns(label)
        .group_by("period")
        .agg(
            _mean_price(on).alias("on_peak"),
            _mean_price(off).alias("off_peak"),
            _mean_price().alias("all_hours"),
            pl.col("price").filter(on).len().alias("n_on_peak"),
            pl.col("price").filter(off).len().alias("n_off_peak"),
        )
        .with_columns(pl.col("on_peak", "off_peak").fill_nan(None))
        .with_columns((pl.col("on_peak") - pl.col("off_peak")).alias("spread"))
        .select(*columns)
        .sort("period")
    )


def intraday_spread(frame: pl.DataFrame, zone: Zone, *, period: str = "year") -> pl.DataFrame:
    """Mean within-day high minus low, over complete local days.

    This is the spread a battery is paid, wherever in the day the extremes fall; it
    can widen while the clock-defined block spread collapses. It is an upper bound on
    one cycle's gross value; :mod:`gpa.battery` measures what survives the constraints.
    """
    label = period_label(period)
    daily = (
        attach_local_time(frame, zone)
        .group_by("local_date")
        .agg(
            (pl.col("price").max() - pl.col("price").min()).alias("spread"),
            INTERVAL_HOURS.sum().alias("observed_hours"),
        )
        .filter(pl.col("observed_hours") >= _COMPLETE_DAY_HOURS)
    )
    return (
        daily.with_columns(label)
        .group_by("period")
        .agg(
            pl.col("spread").mean().alias("mean_spread"),
            pl.col("spread").median().alias("median_spread"),
            pl.len().cast(pl.UInt32).alias("n_days"),
        )
        .sort("period")
    )


def hourly_shape(frame: pl.DataFrame, zone: Zone, *, period: str = "year") -> pl.DataFrame:
    """Duration-weighted mean price per local clock hour: where in the day the money sits."""
    return (
        attach_local_time(frame, zone)
        .with_columns(period_label(period))
        .group_by("period", "local_hour")
        .agg(
            _mean_price().alias("price"),
            INTERVAL_HOURS.sum().alias("observed_hours"),
            pl.len().cast(pl.UInt32).alias("n_intervals"),
        )
        .sort("period", "local_hour")
    )


def negative_share(frame: pl.DataFrame, zone: Zone, *, period: str = "year") -> pl.DataFrame:
    """Percent of observed hours that cleared below zero."""
    negative = INTERVAL_HOURS.filter(pl.col("price") < 0).sum()
    return (
        attach_local_time(frame, zone)
        .with_columns(period_label(period))
        .group_by("period")
        .agg((negative / INTERVAL_HOURS.sum() * 100.0).alias("negative_pct"))
        .sort("period")
    )


def capture_rate(
    prices: pl.DataFrame,
    generation: pl.DataFrame,
    zone: Zone,
    *,
    fuel: str,
    period: str = "month",
) -> pl.DataFrame:
    """Generation-weighted capture price, and its ratio to the time-weighted baseload.

    Weighted by energy actually generated, over the exact intersections of price and
    generation intervals: no sub-interval shape is invented and no gap is filled.
    """
    label = period_label(period)
    columns = {
        "period": pl.String,
        "capture_price": pl.Float64,
        "baseload_price": pl.Float64,
        "capture_rate": pl.Float64,
        "energy_mwh": pl.Float64,
    }
    output = generation.filter(pl.col("fuel") == fuel)
    if prices.is_empty() or output.is_empty():
        return pl.DataFrame(schema=columns)

    def spans(frame: pl.DataFrame, value: str, end: str) -> pl.DataFrame:
        stop = pl.col("ts_utc") + pl.duration(minutes=pl.col("resolution_min"))
        return frame.select("ts_utc", value, stop.alias(end)).sort("ts_utc")

    p, g = spans(prices, "price", "_price_end"), spans(output, "gen_mw", "_gen_end")
    edges = pl.concat(
        [
            p.select("ts_utc"),
            p.select(pl.col("_price_end").alias("ts_utc")),
            g.select("ts_utc"),
            g.select(pl.col("_gen_end").alias("ts_utc")),
        ]
    )
    joined = (
        edges.unique()
        .sort("ts_utc")
        .with_columns(pl.col("ts_utc").shift(-1).alias("_end"))
        .join_asof(p, on="ts_utc")
        .join_asof(g, on="ts_utc")
        .filter((pl.col("_end") <= pl.col("_price_end")) & (pl.col("_end") <= pl.col("_gen_end")))
        .with_columns(
            ((pl.col("_end") - pl.col("ts_utc")).dt.total_seconds() / 3600.0).alias("_hours")
        )
        .with_columns((pl.col("gen_mw") * pl.col("_hours")).alias("energy_mwh"))
    )
    if joined.is_empty():
        return pl.DataFrame(schema=columns)
    return (
        attach_local_time(joined, zone)
        .with_columns(label)
        .group_by("period")
        .agg(
            (pl.col("price") * pl.col("energy_mwh")).sum().alias("_weighted"),
            pl.col("energy_mwh").sum(),
            ((pl.col("price") * pl.col("_hours")).sum() / pl.col("_hours").sum()).alias(
                "baseload_price"
            ),
        )
        .with_columns(
            pl.when(pl.col("energy_mwh") > 0)
            .then(pl.col("_weighted") / pl.col("energy_mwh"))
            .alias("capture_price")
        )
        .with_columns(
            pl.when(pl.col("baseload_price") != 0)
            .then(pl.col("capture_price") / pl.col("baseload_price"))
            .alias("capture_rate")
        )
        .select(*columns)
        .sort("period")
    )
