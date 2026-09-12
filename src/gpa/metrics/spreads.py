"""Thermal generation spreads: spark, dark and their clean variants.

A spread is the gross margin a thermal unit earns per MWh sent out, before
fixed and variable operating costs. It is the number that decides whether a
plant runs, and the sign of the clean spread is why coal gave way to gas across
Europe far faster than any policy target required.

Definitions, all in money per MWh of *electricity*:

    spark spread        = power price - gas price / efficiency
    dark spread         = power price - coal price / efficiency
    clean spark spread  = spark spread - carbon price * gas intensity / efficiency
    clean dark spread   = dark spread - carbon price * coal intensity / efficiency

Efficiency is the plant's net thermal efficiency as a fraction. Fuel prices are
per MWh of *thermal* input, which is the European convention. US practice
quotes a heat rate in MMBtu per MWh and a fuel price per MMBtu instead;
:func:`efficiency_from_heat_rate` converts between them so either input works.

The functions take fuel and carbon prices from the caller rather than fetching
them. This project does not yet ingest a gas, coal or EUA price feed, so
nothing on the published site currently plots a spread. The maths is here,
tested against worked examples, and wiring a fuel price source into it is the
next source adapter rather than new analytics.
"""

from __future__ import annotations

from typing import Final

import polars as pl

__all__ = [
    "BTU_PER_KWH",
    "CARBON_INTENSITY_T_PER_MWH_TH",
    "REFERENCE_EFFICIENCY",
    "clean_spread",
    "efficiency_from_heat_rate",
    "heat_rate_from_efficiency",
    "spread",
]

BTU_PER_KWH: Final = 3.412141633
"""MMBtu of thermal energy per MWh, used to convert a heat rate to an efficiency."""

CARBON_INTENSITY_T_PER_MWH_TH: Final[dict[str, float]] = {
    "gas": 0.2016,
    "coal": 0.3406,
    "lignite": 0.3640,
    "oil": 0.2670,
}
"""Tonnes of CO2 per MWh of thermal input, by fuel.

These are combustion factors applied to fuel burned, not to electricity sent
out, which is why they are divided by efficiency in the clean spread. Coal here
is hard coal; lignite is listed separately because the difference is large
enough to flip a merit order.
"""

REFERENCE_EFFICIENCY: Final[dict[str, float]] = {
    "ccgt": 0.50,
    "ccgt_modern": 0.58,
    "ocgt": 0.35,
    "coal": 0.38,
    "coal_supercritical": 0.45,
}
"""Conventional reference efficiencies used when quoting a standard spread.

Market quotes assume a reference plant rather than a real one, so a published
"clean spark spread" is only comparable if the assumed efficiency travels with
it. Whichever value is used appears in this project's output alongside the
number.
"""


def efficiency_from_heat_rate(heat_rate_mmbtu_per_mwh: float) -> float:
    """Convert a US-style heat rate to a thermal efficiency fraction.

    Args:
        heat_rate_mmbtu_per_mwh: MMBtu of fuel per MWh of electricity. A modern
            combined-cycle unit is around 6.4; a simple-cycle peaker around 10.

    Returns:
        Net thermal efficiency between 0 and 1.

    Raises:
        ValueError: If the heat rate is not positive.
    """
    if heat_rate_mmbtu_per_mwh <= 0:
        raise ValueError(f"heat rate must be positive, got {heat_rate_mmbtu_per_mwh}")
    return BTU_PER_KWH / heat_rate_mmbtu_per_mwh


def heat_rate_from_efficiency(efficiency: float) -> float:
    """Convert a thermal efficiency fraction to a US-style heat rate.

    Raises:
        ValueError: If efficiency is not strictly between 0 and 1.
    """
    _check_efficiency(efficiency)
    return BTU_PER_KWH / efficiency


def _check_efficiency(efficiency: float) -> None:
    if not 0.0 < efficiency < 1.0:
        raise ValueError(f"efficiency must be strictly between 0 and 1, got {efficiency}")


def spread(
    power_price: pl.Expr | float,
    fuel_price: pl.Expr | float,
    efficiency: float,
) -> pl.Expr:
    """Gross spread per MWh of electricity.

    This is the spark spread when the fuel is gas and the dark spread when it is
    coal; the arithmetic is identical and only the input differs.

    Args:
        power_price: Money per MWh of electricity.
        fuel_price: Money per MWh of *thermal* input, in the same currency.
        efficiency: Net thermal efficiency as a fraction.

    Returns:
        A polars expression giving the spread per MWh of electricity.

    Raises:
        ValueError: If efficiency is not strictly between 0 and 1.

    Example:
        A 50 percent efficient combined-cycle unit, power at 90 and gas at 30
        per MWh thermal, earns ``90 - 30/0.5 = 30`` per MWh.
    """
    _check_efficiency(efficiency)
    return _expr(power_price) - _expr(fuel_price) / efficiency


def clean_spread(
    power_price: pl.Expr | float,
    fuel_price: pl.Expr | float,
    carbon_price: pl.Expr | float,
    efficiency: float,
    *,
    fuel: str = "gas",
) -> pl.Expr:
    """Spread after the cost of emission allowances.

    The carbon cost is charged on fuel burned, so it scales with the same
    inverse efficiency as the fuel itself. A more efficient plant is shielded
    twice over: it buys less fuel and it surrenders fewer allowances.

    Args:
        power_price: Money per MWh of electricity.
        fuel_price: Money per MWh of thermal input.
        carbon_price: Money per tonne of CO2.
        efficiency: Net thermal efficiency as a fraction.
        fuel: Key into :data:`CARBON_INTENSITY_T_PER_MWH_TH`.

    Returns:
        A polars expression giving the clean spread per MWh of electricity.

    Raises:
        KeyError: If ``fuel`` has no carbon intensity registered.
        ValueError: If efficiency is not strictly between 0 and 1.

    Example:
        The same 50 percent unit with carbon at 80 per tonne pays
        ``80 * 0.2016 / 0.5 = 32.3`` per MWh in allowances, turning a gross
        spark spread of 30 into a clean spark spread of -2.3, and the plant
        stops running.
    """
    try:
        intensity = CARBON_INTENSITY_T_PER_MWH_TH[fuel]
    except KeyError:
        valid = ", ".join(sorted(CARBON_INTENSITY_T_PER_MWH_TH))
        raise KeyError(f"unknown fuel {fuel!r}; valid fuels are: {valid}") from None

    gross = spread(power_price, fuel_price, efficiency)
    return gross - _expr(carbon_price) * intensity / efficiency


def _expr(value: pl.Expr | float) -> pl.Expr:
    return value if isinstance(value, pl.Expr) else pl.lit(float(value))
