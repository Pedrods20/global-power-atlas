"""Generation mix.

Shares here are shares of *energy*, computed by integrating megawatts over each
interval's real duration. They are not shares of installed capacity, which is a
different and often wildly different number: a system can be half wind by
nameplate and a quarter wind by output.
"""

from __future__ import annotations

from typing import Final

import polars as pl

from gpa.calendar import attach_local_time
from gpa.zones import Zone

__all__ = ["generation_mix"]


_INTERVAL_HOURS = pl.col("resolution_min").cast(pl.Float64) / 60.0


_EXCLUDE_FROM_MIX: Final[frozenset[str]] = frozenset({"imports"})
"""Net interchange is not generation and never enters a mix share."""


def _energy(frame: pl.DataFrame, zone: Zone, period: str) -> pl.DataFrame:
    formats = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y", "all": None}
    if period not in formats:
        raise ValueError(f"period must be one of {sorted(formats)}, got {period!r}")

    prepared = attach_local_time(frame, zone).filter(~pl.col("fuel").is_in(list(_EXCLUDE_FROM_MIX)))
    fmt = formats[period]
    prepared = prepared.with_columns(
        pl.lit("all").alias("period")
        if fmt is None
        else pl.col("local_date").dt.strftime(fmt).alias("period")
    )
    return prepared.group_by(["period", "fuel"]).agg(
        (pl.col("gen_mw") * _INTERVAL_HOURS).sum().alias("energy_mwh")
    )


def generation_mix(
    frame: pl.DataFrame,
    zone: Zone,
    *,
    period: str = "month",
) -> pl.DataFrame:
    """Energy share by fuel.

    Shares are computed over gross generation excluding net interchange. Pumped
    storage is included and can be negative over a period in which it consumed
    more than it produced, which is normal; its share is then negative and the
    remaining shares exceed 100 percent by that amount. That is the arithmetically
    honest presentation, and the site plots storage separately for this reason.

    Args:
        frame: Rows matching the ``generation`` schema.
        zone: Supplies the market timezone.
        period: ``"day"``, ``"month"``, ``"year"`` or ``"all"``.

    Returns:
        Columns ``period``, ``fuel``, ``energy_mwh`` and ``share_pct``, sorted
        by period then descending share.
    """
    empty = pl.DataFrame(
        schema={
            "period": pl.String,
            "fuel": pl.String,
            "energy_mwh": pl.Float64,
            "share_pct": pl.Float64,
        }
    )
    if frame.is_empty():
        return empty

    energy = _energy(frame, zone, period)
    if energy.is_empty():
        return empty

    return energy.with_columns(
        (pl.col("energy_mwh") / pl.col("energy_mwh").sum().over("period") * 100.0).alias(
            "share_pct"
        )
    ).sort(["period", "share_pct"], descending=[False, True])
