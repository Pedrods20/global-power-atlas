"""Generation mix, renewable penetration and carbon intensity.

Shares here are shares of *energy*, computed by integrating megawatts over each
interval's real duration. They are not shares of installed capacity, which is a
different and often wildly different number: a system can be half wind by
nameplate and a quarter wind by output.

Two emission factor sets are provided and never mixed, because they answer
different questions and differ by an order of magnitude for nuclear and wind.
Reporting one while labelling it the other is the most common way carbon
intensity figures become wrong.
"""

from __future__ import annotations

from typing import Final, Literal

import polars as pl

from gpa.calendar import attach_local_time
from gpa.schema import RENEWABLE_FUELS
from gpa.zones import Zone

__all__ = [
    "EMISSION_FACTORS",
    "FactorBasis",
    "carbon_intensity",
    "generation_mix",
    "renewable_share",
]

FactorBasis = Literal["operational", "lifecycle"]

_INTERVAL_HOURS = pl.col("resolution_min").cast(pl.Float64) / 60.0

EMISSION_FACTORS: Final[dict[FactorBasis, dict[str, float]]] = {
    "operational": {
        # Direct combustion only, grams of CO2 per kWh of electricity sent out.
        # This is the basis system operators publish and the one to use when
        # comparing against a TSO's own carbon intensity figure.
        "coal": 950.0,
        "gas": 400.0,
        "oil": 700.0,
        "waste": 400.0,
        # Biomass combustion is counted as zero at the stack by the standard
        # accounting convention, which books biogenic carbon to the land sector
        # instead. This is a convention, not a physical claim, and it is the
        # single largest judgement call in this table.
        "biomass": 0.0,
        "nuclear": 0.0,
        "hydro": 0.0,
        "hydro_pumped_storage": 0.0,
        "wind": 0.0,
        "solar": 0.0,
        "geothermal": 0.0,
        "battery": 0.0,
    },
    "lifecycle": {
        # IPCC AR5 Annex III median values, grams of CO2-equivalent per kWh,
        # covering construction, fuel cycle, operation and decommissioning.
        "coal": 820.0,
        "gas": 490.0,
        "oil": 650.0,
        "waste": 400.0,
        "biomass": 230.0,
        "nuclear": 12.0,
        "hydro": 24.0,
        "hydro_pumped_storage": 24.0,
        "wind": 11.0,
        "solar": 48.0,
        "geothermal": 38.0,
        "battery": 0.0,
    },
}
"""Emission factors by basis and canonical fuel, in grams of CO2 per kWh.

``other`` and ``imports`` are deliberately absent. ``other`` is an unresolved
mixture whose composition varies by market, and ``imports`` carries the
intensity of a neighbouring system that this project does not model. Assigning
either a number would be a guess, so instead they are excluded and reported as
uncovered generation. See :func:`carbon_intensity`.
"""

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


def renewable_share(
    frame: pl.DataFrame,
    zone: Zone,
    *,
    period: str = "month",
) -> pl.DataFrame:
    """Renewable share of generated energy.

    Renewable means hydro, wind, solar, biomass and geothermal, as declared by
    :data:`gpa.schema.RENEWABLE_FUELS`. Pumped storage is excluded because its
    output is recycled grid energy rather than new primary supply, and waste is
    excluded because only its biogenic fraction would qualify and this project
    does not know that fraction.

    Because the definition is ours and is applied identically everywhere, these
    numbers are comparable across markets in a way each operator's own published
    figure is not.

    Returns:
        Columns ``period``, ``renewable_mwh``, ``total_mwh`` and
        ``renewable_pct``.
    """
    empty = pl.DataFrame(
        schema={
            "period": pl.String,
            "renewable_mwh": pl.Float64,
            "total_mwh": pl.Float64,
            "renewable_pct": pl.Float64,
        }
    )
    if frame.is_empty():
        return empty

    energy = _energy(frame, zone, period)
    if energy.is_empty():
        return empty

    renewable = pl.col("energy_mwh").filter(pl.col("fuel").is_in(list(RENEWABLE_FUELS)))

    return (
        energy.group_by("period")
        .agg(
            renewable.sum().alias("renewable_mwh"),
            pl.col("energy_mwh").sum().alias("total_mwh"),
        )
        .with_columns(
            pl.when(pl.col("total_mwh") > 0)
            .then(pl.col("renewable_mwh") / pl.col("total_mwh") * 100.0)
            .otherwise(None)
            .alias("renewable_pct")
        )
        .sort("period")
    )


def carbon_intensity(
    frame: pl.DataFrame,
    zone: Zone,
    *,
    basis: FactorBasis = "operational",
    period: str = "month",
    min_coverage_pct: float = 95.0,
) -> pl.DataFrame:
    """Average carbon intensity of generation, in grams of CO2 per kWh.

    The intensity is the energy-weighted mean of the fuel factors, renormalised
    over the generation whose fuel has a known factor. ``coverage_pct`` reports
    how much of the period's generation that was, and the result is nulled when
    coverage falls below ``min_coverage_pct``.

    That guard matters, and the default is deliberately strict. Brazil's ONS
    balance publishes a single aggregate thermal column that does not separate
    gas, coal, oil and biomass, so this project maps it to ``other``. Brazilian
    coverage lands near 87 percent, and the missing 13 percent is not a random
    sample of the fleet, it *is* the entire emitting fleet. Renormalising over
    the clean remainder yields an intensity of roughly zero, which is what the
    previous version of this project reported and is badly wrong.

    A coverage threshold alone cannot detect that, because the uncovered share
    looks small. The threshold is therefore set at 95 percent rather than a
    looser figure, on the reasoning that uncovered generation is usually
    unresolved *thermal* output, so even a tenth of it left out can move the
    answer by more than a hundred grams per kWh. Publishing nothing, and
    publishing the coverage alongside, is the honest outcome.

    Args:
        frame: Rows matching the ``generation`` schema.
        zone: Supplies the market timezone.
        basis: ``"operational"`` for direct combustion, matching what system
            operators publish, or ``"lifecycle"`` for IPCC AR5 medians.
        period: ``"day"``, ``"month"``, ``"year"`` or ``"all"``.
        min_coverage_pct: Minimum share of generation with a known factor
            required before an intensity is reported. Lower it only if you know
            the uncovered fuels are not predominantly thermal.

    Returns:
        Columns ``period``, ``basis``, ``intensity_g_per_kwh``, ``coverage_pct``
        and ``uncovered_mwh``.

    Raises:
        KeyError: If ``basis`` is not a known factor set.
    """
    if basis not in EMISSION_FACTORS:
        valid = ", ".join(sorted(EMISSION_FACTORS))
        raise KeyError(f"unknown basis {basis!r}; valid bases are: {valid}")

    empty = pl.DataFrame(
        schema={
            "period": pl.String,
            "basis": pl.String,
            "intensity_g_per_kwh": pl.Float64,
            "coverage_pct": pl.Float64,
            "uncovered_mwh": pl.Float64,
        }
    )
    if frame.is_empty():
        return empty

    energy = _energy(frame, zone, period)
    if energy.is_empty():
        return empty

    factors = EMISSION_FACTORS[basis]

    # Negative energy, from storage consuming over the period, would otherwise
    # subtract emissions it never avoided, so only positive output is weighted.
    #
    # These use `when/then/otherwise` rather than `filter`. Two filters with
    # different predicates produce operands of different lengths inside an
    # aggregation, which polars rejects, and the mismatch only appears once a
    # fuel that *has* an emission factor also has negative energy. German
    # pumped storage is exactly that case.
    positive = pl.col("energy_mwh") > 0
    has_factor = pl.col("_factor").is_not_null()
    counted = positive & has_factor

    weighted = pl.when(counted).then(pl.col("energy_mwh") * pl.col("_factor")).otherwise(0.0).sum()
    covered_mwh = pl.when(counted).then(pl.col("energy_mwh")).otherwise(0.0).sum()
    total_mwh = pl.when(positive).then(pl.col("energy_mwh")).otherwise(0.0).sum()

    return (
        energy.with_columns(
            pl.col("fuel")
            .replace_strict(factors, default=None, return_dtype=pl.Float64)
            .alias("_factor")
        )
        .group_by("period")
        .agg(
            weighted.alias("_weighted"),
            covered_mwh.alias("_covered_mwh"),
            total_mwh.alias("_total_mwh"),
        )
        .with_columns(
            pl.when(pl.col("_total_mwh") > 0)
            .then(pl.col("_covered_mwh") / pl.col("_total_mwh") * 100.0)
            .otherwise(None)
            .alias("coverage_pct"),
            (pl.col("_total_mwh") - pl.col("_covered_mwh")).alias("uncovered_mwh"),
        )
        .with_columns(
            pl.when((pl.col("_covered_mwh") > 0) & (pl.col("coverage_pct") >= min_coverage_pct))
            .then(pl.col("_weighted") / pl.col("_covered_mwh"))
            .otherwise(None)
            .alias("intensity_g_per_kwh"),
            pl.lit(basis).alias("basis"),
        )
        .select("period", "basis", "intensity_g_per_kwh", "coverage_pct", "uncovered_mwh")
        .sort("period")
    )
