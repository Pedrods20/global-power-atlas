"""Generation mix as shares of energy, integrated over each interval's real duration."""

from __future__ import annotations

import polars as pl

from gpa.calendar import INTERVAL_HOURS, attach_local_time, period_label
from gpa.zones import Zone

__all__ = ["generation_mix"]


def generation_mix(frame: pl.DataFrame, zone: Zone, *, period: str = "month") -> pl.DataFrame:
    """Energy share by fuel, excluding net imports.

    Pumped storage can be net negative over a period; its share is then negative and
    the others exceed 100 percent, which is the honest arithmetic.
    """
    energy = (
        attach_local_time(frame, zone)
        .filter(pl.col("fuel") != "imports")
        .with_columns(period_label(period))
        .group_by("period", "fuel")
        .agg((pl.col("gen_mw") * INTERVAL_HOURS).sum().alias("energy_mwh"))
    )
    return energy.with_columns(
        (pl.col("energy_mwh") / pl.col("energy_mwh").sum().over("period") * 100.0).alias(
            "share_pct"
        )
    ).sort(["period", "share_pct"], descending=[False, True])
