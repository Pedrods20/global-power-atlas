"""Structural audit of the store: every series over its own span, gaps left as gaps."""

from __future__ import annotations

import polars as pl

from gpa import store
from gpa.zones import ZONES


def inspect(frame: pl.DataFrame, dataset: str, zone: str) -> pl.DataFrame:
    """Coverage, gaps and invalid intervals per fuel or series, over its own observed span."""
    value = {
        "price": "price",
        "load": "load_mw",
        "generation": "gen_mw",
        "fundamentals": "forecast_mw",
    }[dataset]
    if "series" in frame.columns:
        data = frame.rename({"series": "fuel"})
    elif "fuel" in frame.columns:
        data = frame
    else:
        data = frame.with_columns(pl.lit("all").alias("fuel"))
    rows: list[dict[str, object]] = []
    if data.is_empty():
        return pl.DataFrame(
            {
                "zone": [zone],
                "dataset": [dataset],
                "fuel": ["all"],
                "rows": [0],
                "invalid": [1],
                "gap_hours": [None],
                "coverage_pct": [None],
            }
        )
    for (fuel,), series in data.group_by("fuel"):
        series = (
            series.sort("ts_utc")
            .with_columns(
                (pl.col("ts_utc") + pl.duration(minutes=pl.col("resolution_min"))).alias("_end")
            )
            .with_columns(
                (pl.col("ts_utc").shift(-1) - pl.col("_end")).dt.total_seconds().alias("_gap")
            )
        )
        span = (series["_end"].max() - series["ts_utc"].min()).total_seconds() / 3600  # type: ignore[operator,union-attr]
        hours = float(series["resolution_min"].sum()) / 60
        invalid = series.filter(
            (pl.col("_gap") < 0).fill_null(False)
            | ~pl.col(value).is_finite().fill_null(False)
            | (pl.col("resolution_min") <= 0)
            | (
                (pl.col("ts_utc").dt.epoch("s") % (pl.col("resolution_min").cast(pl.Int64) * 60))
                != 0
            )
        ).height
        gap_hours = float(series.filter(pl.col("_gap") > 0)["_gap"].sum()) / 3600
        rows.append(
            {
                "zone": zone,
                "dataset": dataset,
                "fuel": fuel,
                "rows": series.height,
                "first": series["ts_utc"].min(),
                "last": series["_end"].max(),
                "observed_hours": hours,
                "gap_hours": gap_hours,
                "coverage_pct": min(100.0, hours / span * 100) if span else None,
                "invalid": invalid,
            }
        )
    return pl.DataFrame(rows).sort("fuel")


def report() -> pl.DataFrame:
    """Audit active series, including ones that are entirely missing."""
    return pl.concat(
        [
            inspect(store.read(dataset, zone.code), dataset, zone.code)
            for zone in ZONES
            for dataset in zone.sources
        ],
        how="diagonal_relaxed",
    ).sort("zone", "dataset", "fuel")
