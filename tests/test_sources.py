"""Source adapter parsing tests.

These run against small recorded fixtures rather than the live providers, so
the suite stays fast, deterministic and usable offline. The fixtures preserve
the provider's quirks, such as Energy-Charts mixing measurements with derived
indicators in one list.
"""

from __future__ import annotations

import pytest

from gpa.schema import FUELS
from gpa.sources import REGISTRY, get_source
from gpa.sources.energy_charts import DERIVED_SERIES, FUEL_MAP, LOAD_SERIES

# --- Registry --------------------------------------------------------------


def test_every_zone_source_is_registered() -> None:
    """A typo in a zone's source mapping should fail here, not at 3am in cron."""
    from gpa.zones import ZONES

    for zone in ZONES:
        for dataset, source_name in zone.sources.items():
            source = get_source(source_name)
            assert dataset in source.datasets, (
                f"{zone.code} asks {source_name} for {dataset}, "
                f"which it cannot produce ({source.datasets})"
            )


def test_get_source_reports_valid_names() -> None:
    with pytest.raises(KeyError, match="registered sources are"):
        get_source("nordpool")


def test_every_adapter_declares_a_window_policy() -> None:
    for name, source in REGISTRY.items():
        assert hasattr(source, "max_window_days"), f"{name} has no max_window_days"


# --- Energy-Charts ---------------------------------------------------------


def test_energy_charts_fuel_map_targets_only_canonical_fuels() -> None:
    unknown = set(FUEL_MAP.values()) - set(FUELS)
    assert not unknown, f"fuel map targets unknown fuels: {sorted(unknown)}"


def test_energy_charts_collapses_both_wind_series_onto_one_fuel() -> None:
    assert FUEL_MAP["Wind onshore"] == "wind"
    assert FUEL_MAP["Wind offshore"] == "wind"


def test_energy_charts_keeps_pumped_storage_out_of_hydro() -> None:
    """Pumped storage is recycled grid energy, not new primary renewable supply."""
    assert FUEL_MAP["Hydro Run-of-River"] == "hydro"
    assert FUEL_MAP["Hydro water reservoir"] == "hydro"
    assert FUEL_MAP["Hydro pumped storage"] == "hydro_pumped_storage"
    assert FUEL_MAP["Hydro pumped storage consumption"] == "hydro_pumped_storage"


def test_energy_charts_drops_derived_indicators() -> None:
    """Renewable share and residual load are computed, not measured.

    What this project needs, residual load among it, is recomputed from the
    measured series, so the definition is its own and documented rather than a
    provider's undocumented one.
    """
    assert "Renewable share of load" in DERIVED_SERIES
    assert "Residual load" in DERIVED_SERIES
    assert not DERIVED_SERIES & set(FUEL_MAP)


def test_energy_charts_separates_load_from_generation_series() -> None:
    assert not LOAD_SERIES & set(FUEL_MAP)
