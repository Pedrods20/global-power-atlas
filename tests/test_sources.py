"""Source adapter parsing tests.

These run against small recorded fixtures rather than the live providers, so
the suite stays fast, deterministic and usable offline. The fixtures preserve
the exact quirks each provider has: Brazilian semicolon CSVs with blank fields,
AEMO's interval-ending stamps, and Energy-Charts mixing measurements with
derived indicators in one list.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from gpa.schema import FUELS
from gpa.sources import REGISTRY, get_source
from gpa.sources.aemo import NEM_MARKET_TIMEZONE, _parse_archive
from gpa.sources.base import infer_resolution_minutes
from gpa.sources.energy_charts import DERIVED_SERIES, FUEL_MAP, LOAD_SERIES
from gpa.sources.ons import _parse_balance
from gpa.sources.openelectricity import IGNORED_FUELTECHS, _collect

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


# --- Resolution inference --------------------------------------------------


def test_resolution_is_measured_not_assumed() -> None:
    quarter_hourly = pl.Series(
        "ts", [dt.datetime(2026, 1, 1) + dt.timedelta(minutes=15 * i) for i in range(20)]
    )
    assert infer_resolution_minutes(quarter_hourly) == 15


def test_resolution_survives_a_missing_observation() -> None:
    """The median ignores one double-length gap; a mean would not."""
    stamps = [dt.datetime(2026, 1, 1) + dt.timedelta(minutes=5 * i) for i in range(20)]
    del stamps[7]
    assert infer_resolution_minutes(pl.Series("ts", stamps)) == 5


def test_resolution_falls_back_when_there_is_nothing_to_measure() -> None:
    assert infer_resolution_minutes(pl.Series("ts", [dt.datetime(2026, 1, 1)]), default=30) == 30


# --- ONS -------------------------------------------------------------------

ONS_FIXTURE = """id_subsistema;nom_subsistema;din_instante;val_gerhidraulica;val_gertermica;val_gereolica;val_gersolar;val_carga;val_intercambio
SIN;SISTEMA INTERLIGADO NACIONAL;2026-01-01 00:00:00;53588.3;8108.7;10961.5;0.0;70000.0;100.0
SIN;SISTEMA INTERLIGADO NACIONAL;2026-01-01 01:00:00;52000.0;;10000.0;0.0;68000.0;100.0
SE ;SUDESTE/CENTRO-OESTE;2026-01-01 00:00:00;27078.8;5521.2;204.1;0.0;41048.5;-8244.5
N  ;NORTE;2026-01-01 00:00:00;11587.0;1864.9;263.0;0.0;7622.6;6094.2
"""


def test_ons_selects_one_subsystem_and_never_mixes_them() -> None:
    """SIN is the sum of the regions, so keeping both would double-count."""
    sin = _parse_balance(ONS_FIXTURE, "SIN")
    southeast = _parse_balance(ONS_FIXTURE, "SE")

    assert sin.height == 2
    assert southeast.height == 1
    assert southeast["val_gerhidraulica"][0] == pytest.approx(27078.8)


def test_ons_subsystem_codes_are_padded_in_the_source_file() -> None:
    """The real file writes 'SE ' and 'N  ', so matching must strip first."""
    assert _parse_balance(ONS_FIXTURE, "N").height == 1


def test_ons_missing_generation_is_null_not_zero() -> None:
    """Parsing a blank as zero understates the technology's share.

    This is the bug the previous version of this project shipped: it used
    ``parseFloat(x) || 0``, which turned every missing thermal reading into
    zero output and biased Brazil's fuel mix and carbon intensity downward.
    """
    sin = _parse_balance(ONS_FIXTURE, "SIN").sort("ts_utc")
    assert sin["val_gertermica"][0] == pytest.approx(8108.7)
    assert sin["val_gertermica"][1] is None


def test_ons_timestamps_convert_from_brasilia_to_utc() -> None:
    """Midnight in Sao Paulo is 03:00 UTC; Brazil has had no DST since 2019."""
    sin = _parse_balance(ONS_FIXTURE, "SIN").sort("ts_utc")
    assert sin["ts_utc"][0] == dt.datetime(2026, 1, 1, 3, tzinfo=dt.UTC)


def test_ons_thermal_maps_to_other_not_gas() -> None:
    """The file publishes one aggregate thermal column with no fuel breakdown.

    Calling it gas would invent a fact. Mapping it to 'other' keeps it out of
    the carbon factor table, which is what makes the coverage guard in
    carbon_intensity fire for Brazil instead of reporting a falsely clean grid.
    """
    from gpa.sources.ons import FUEL_COLUMNS

    assert FUEL_COLUMNS["val_gertermica"] == "other"


def test_ons_rejects_a_file_missing_expected_columns() -> None:
    from gpa.sources.base import UpstreamError

    with pytest.raises(UpstreamError, match="missing expected columns"):
        _parse_balance("id_subsistema;din_instante\nSIN;2026-01-01 00:00:00\n", "SIN")


# --- AEMO ------------------------------------------------------------------

AEMO_5MIN = """REGION,SETTLEMENTDATE,TOTALDEMAND,RRP,PERIODTYPE
NSW1,2026/08/01 00:05:00,8898.91,89.88,TRADE
NSW1,2026/08/01 00:10:00,9032.33,-95.05,TRADE
NSW1,2026/08/01 00:15:00,8989.12,88.88,TRADE
NSW1,2026/08/01 00:20:00,9036.72,88.88,TRADE
"""

AEMO_30MIN = """REGION,SETTLEMENTDATE,TOTALDEMAND,RRP,PERIODTYPE
NSW1,2019/08/01 00:30:00,7898.91,59.88,TRADE
NSW1,2019/08/01 01:00:00,7832.33,55.05,TRADE
NSW1,2019/08/01 01:30:00,7789.12,58.88,TRADE
"""


def test_aemo_shifts_interval_ending_stamps_to_interval_start() -> None:
    """A row stamped 00:05 describes 00:00 to 00:05, so the start is 00:00.

    Skipping this shifts the whole series forward by one interval and moves the
    evening peak, while quietly misaligning price against generation.
    """
    parsed = _parse_archive(AEMO_5MIN).sort("ts_utc")
    # 00:00 AEST on 1 August is 14:00 UTC on 31 July.
    assert parsed["ts_utc"][0] == dt.datetime(2026, 7, 31, 14, 0, tzinfo=dt.UTC)


def test_aemo_detects_the_thirty_minute_settlement_era() -> None:
    """The NEM moved to five-minute settlement in October 2021.

    A backfill spanning that change must shift each era by its own interval.
    """
    parsed = _parse_archive(AEMO_30MIN).sort("ts_utc")
    # 00:00 AEST on 1 August 2019 is 14:00 UTC on 31 July 2019.
    assert parsed["ts_utc"][0] == dt.datetime(2019, 7, 31, 14, 0, tzinfo=dt.UTC)
    gap = parsed["ts_utc"][1] - parsed["ts_utc"][0]
    assert gap == dt.timedelta(minutes=30)


def test_aemo_preserves_negative_prices() -> None:
    parsed = _parse_archive(AEMO_5MIN)
    assert parsed["RRP"].min() == pytest.approx(-95.05)


def test_aemo_market_timezone_never_observes_daylight_saving() -> None:
    assert NEM_MARKET_TIMEZONE == "Australia/Brisbane"


def test_aemo_rejects_a_file_missing_expected_columns() -> None:
    from gpa.sources.base import UpstreamError

    with pytest.raises(UpstreamError, match="missing expected columns"):
        _parse_archive("REGION,SETTLEMENTDATE\nNSW1,2026/08/01 00:05:00\n")


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

    This project recomputes those from the mix so the definition is its own and
    is documented, rather than inheriting a provider's undocumented one.
    """
    assert "Renewable share of load" in DERIVED_SERIES
    assert "Residual load" in DERIVED_SERIES
    assert not DERIVED_SERIES & set(FUEL_MAP)


def test_energy_charts_separates_load_from_generation_series() -> None:
    assert not LOAD_SERIES & set(FUEL_MAP)


# --- OpenElectricity -------------------------------------------------------

OE_FIXTURE = {
    "success": True,
    "data": [
        {
            "metric": "power",
            "unit": "MW",
            "interval": "1h",
            "results": [
                {
                    "columns": {"region": "NSW1", "fueltech_group": "coal"},
                    "data": [["2026-09-01T00:00:00+10:00", 5079.1]],
                },
                {
                    "columns": {"region": "NSW1", "fueltech_group": "battery"},
                    "data": [["2026-09-01T00:00:00+10:00", -19.85]],
                },
                {
                    "columns": {"region": "NSW1", "fueltech_group": "battery_charging"},
                    "data": [["2026-09-01T00:00:00+10:00", 29.86]],
                },
                {
                    "columns": {"region": "NSW1", "fueltech_group": "battery_discharging"},
                    "data": [["2026-09-01T00:00:00+10:00", 10.0]],
                },
                {
                    "columns": {"region": "QLD1", "fueltech_group": "coal"},
                    "data": [["2026-09-01T00:00:00+10:00", 4000.0]],
                },
            ],
        }
    ],
}


def test_openelectricity_keeps_only_net_battery() -> None:
    """Net plus both halves would count the same megawatts three times."""
    rows = _collect(OE_FIXTURE, region="NSW1")
    battery = [r for r in rows if r["fuel"] == "battery"]

    assert len(battery) == 1
    assert battery[0]["gen_mw"] == pytest.approx(-19.85)
    assert {"battery_charging", "battery_discharging"} == IGNORED_FUELTECHS


def test_openelectricity_filters_to_the_requested_region() -> None:
    rows = _collect(OE_FIXTURE, region="NSW1")
    assert len(rows) == 2  # coal and net battery
    assert all("QLD" not in str(r) for r in rows)


def test_openelectricity_buckets_an_unmapped_technology_rather_than_dropping_it() -> None:
    """A new fueltech should appear as 'other', not silently vanish from the mix."""
    payload = {
        "success": True,
        "data": [
            {
                "results": [
                    {
                        "columns": {"region": "NSW1", "fueltech_group": "fusion"},
                        "data": [["2026-09-01T00:00:00+10:00", 42.0]],
                    }
                ]
            }
        ],
    }
    rows = _collect(payload, region="NSW1")
    assert rows[0]["fuel"] == "other"


def test_openelectricity_raises_on_a_reported_failure() -> None:
    from gpa.sources.base import UpstreamError

    with pytest.raises(UpstreamError, match="reported failure"):
        _collect({"success": False, "error": "bad request"}, region="NSW1")
