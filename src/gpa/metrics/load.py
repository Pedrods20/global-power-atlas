"""Demand analytics: profiles, duration curves and load factor.

Every function here keys on market-local time via :mod:`gpa.calendar`, and every
conversion from power to energy multiplies by the interval's real duration. A
daily mean is the mean of the intervals that existed, so a 23-hour or 25-hour
daylight-saving day is handled by construction rather than by a special case.
"""

from __future__ import annotations

import polars as pl

from gpa.calendar import attach_block, attach_local_time
from gpa.zones import Zone

__all__ = [
    "daily_energy",
    "daily_profile",
    "duration_curve",
    "load_factor",
    "peak_demand",
]

_INTERVAL_HOURS = pl.col("resolution_min").cast(pl.Float64) / 60.0


def daily_profile(frame: pl.DataFrame, zone: Zone, *, by_block: bool = False) -> pl.DataFrame:
    """Average demand by local hour of day.

    This is the shape people mean by "the load curve": the average megawatts
    observed in each of the market's local hours, which exposes the morning ramp
    and the evening peak.

    Args:
        frame: Rows matching the ``load`` schema.
        zone: Supplies the market timezone.
        by_block: Split the profile into on-peak and off-peak rows as well.

    Returns:
        Columns ``local_hour`` and ``avg_load_mw``, plus ``block`` when
        ``by_block`` is set, sorted by hour.
    """
    if frame.is_empty():
        return pl.DataFrame(schema={"local_hour": pl.Int8, "avg_load_mw": pl.Float64})

    prepared = attach_block(frame, zone) if by_block else attach_local_time(frame, zone)
    keys = ["local_hour", "block"] if by_block else ["local_hour"]

    return prepared.group_by(keys).agg(pl.col("load_mw").mean().alias("avg_load_mw")).sort(keys)


def daily_energy(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Energy consumed per local calendar day, with the hours that produced it.

    Energy is the sum of ``load_mw * resolution_min / 60`` rather than a mean
    multiplied by 24, which is what keeps a short or long daylight-saving day
    correct. ``hours_observed`` is reported alongside so a reader can see when a
    day is incomplete instead of mistaking a gap for a fall in demand.

    Returns:
        Columns ``local_date``, ``energy_mwh``, ``avg_load_mw``, ``peak_load_mw``
        and ``hours_observed``.
    """
    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "local_date": pl.Date,
                "energy_mwh": pl.Float64,
                "avg_load_mw": pl.Float64,
                "peak_load_mw": pl.Float64,
                "hours_observed": pl.Float64,
            }
        )

    return (
        attach_local_time(frame, zone)
        .group_by("local_date")
        .agg(
            (pl.col("load_mw") * _INTERVAL_HOURS).sum().alias("energy_mwh"),
            pl.col("load_mw").mean().alias("avg_load_mw"),
            pl.col("load_mw").max().alias("peak_load_mw"),
            _INTERVAL_HOURS.sum().alias("hours_observed"),
        )
        .sort("local_date")
    )


def duration_curve(frame: pl.DataFrame, *, points: int | None = None) -> pl.DataFrame:
    """Load duration curve: demand sorted descending against exceedance.

    The curve answers "what load is exceeded x percent of the time", which is
    how capacity adequacy and the value of peaking plant are actually assessed.
    The steepness of its left-hand tail is the entire economic case for peakers.

    Args:
        frame: Rows matching the ``load`` schema.
        points: Downsample to roughly this many points for plotting. ``None``
            keeps every observation.

    Returns:
        Columns ``exceedance_pct`` and ``load_mw``, descending by load.
    """
    return _duration_curve(frame, "load_mw", points)


def peak_demand(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """The single highest observed interval per local month.

    Returns:
        Columns ``local_month``, ``peak_load_mw``, ``ts_local`` and
        ``local_hour`` identifying when the peak occurred.
    """
    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "local_month": pl.String,
                "peak_load_mw": pl.Float64,
                "ts_local": pl.String,
                "local_hour": pl.Int8,
            }
        )

    prepared = attach_local_time(frame, zone).with_columns(
        pl.col("local_date").dt.strftime("%Y-%m").alias("local_month")
    )
    return (
        prepared.sort("load_mw", descending=True)
        .group_by("local_month")
        .agg(
            pl.col("load_mw").first().alias("peak_load_mw"),
            pl.col("ts_local").first().dt.strftime("%Y-%m-%d %H:%M").alias("ts_local"),
            pl.col("local_hour").first(),
        )
        .sort("local_month")
    )


def load_factor(frame: pl.DataFrame, zone: Zone, *, period: str = "month") -> pl.DataFrame:
    """Load factor: average demand divided by peak demand.

    A high load factor means a flat system that can be served by baseload plant.
    A low one means a peaky system whose capacity sits idle most of the time, so
    it needs more installed megawatts per megawatt-hour delivered. It is the
    cleanest single number for comparing the *shape* of two markets' demand
    independently of their size.

    Args:
        frame: Rows matching the ``load`` schema.
        zone: Supplies the market timezone.
        period: ``"day"``, ``"month"`` or ``"year"``.

    Returns:
        Columns ``period``, ``avg_load_mw``, ``peak_load_mw`` and
        ``load_factor``, the last between 0 and 1.

    Raises:
        ValueError: If ``period`` is not a supported grouping.
    """
    formats = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}
    if period not in formats:
        raise ValueError(f"period must be one of {sorted(formats)}, got {period!r}")

    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "period": pl.String,
                "avg_load_mw": pl.Float64,
                "peak_load_mw": pl.Float64,
                "load_factor": pl.Float64,
            }
        )

    return (
        attach_local_time(frame, zone)
        .with_columns(pl.col("local_date").dt.strftime(formats[period]).alias("period"))
        .group_by("period")
        .agg(
            pl.col("load_mw").mean().alias("avg_load_mw"),
            pl.col("load_mw").max().alias("peak_load_mw"),
        )
        .with_columns(
            pl.when(pl.col("peak_load_mw") > 0)
            .then(pl.col("avg_load_mw") / pl.col("peak_load_mw"))
            .otherwise(None)
            .alias("load_factor")
        )
        .sort("period")
    )


def _duration_curve(frame: pl.DataFrame, column: str, points: int | None) -> pl.DataFrame:
    """Shared duration-curve construction, used for both load and price."""
    if frame.is_empty():
        return pl.DataFrame(schema={"exceedance_pct": pl.Float64, column: pl.Float64})

    ordered = frame.select(pl.col(column)).drop_nulls().sort(column, descending=True)
    n = ordered.height
    if n == 0:
        return pl.DataFrame(schema={"exceedance_pct": pl.Float64, column: pl.Float64})

    curve = ordered.with_columns(
        ((pl.int_range(1, n + 1, eager=False) / n) * 100.0).alias("exceedance_pct")
    ).select("exceedance_pct", column)

    if points is not None and points > 0 and n > points:
        step = max(1, n // points)
        curve = curve.gather_every(step)

    return curve
