"""Fuel and carbon spread arithmetic remains explicit and reproducible."""

import polars as pl
import pytest

from gpa.benchmarks import calculate_spreads


def test_clean_spreads_include_fuel_and_carbon_costs():
    reference = pl.DataFrame(
        {
            "month": ["2026-01"],
            "gas_eur_mwhth": [30.0],
            "coal_eur_tonne": [100.0],
            "eua_eur_tco2": [80.0],
        }
    )
    power = pl.DataFrame({"period": ["2026-01"], "all_hours": [100.0]})

    result = calculate_spreads(reference, power)

    assert result["clean_spark_eur_mwh"][0] == pytest.approx(7.6864)
    assert round(result["clean_dark_eur_mwh"][0], 6) == round(
        100 - 100 / 6.978 / 0.38 - 80 * 0.34056 / 0.38, 6
    )
