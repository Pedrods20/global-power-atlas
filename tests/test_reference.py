"""Reference tables: installed-capacity parsing and the replace-not-merge store."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from gpa import reference
from gpa.sources.energy_charts import EnergyChartsSource
from gpa.zones import get_zone

ZONE = get_zone("DE-LU")


def _installed(
    labels: list[str], series: dict[str, list[float | None]], time_step: str, monkeypatch
):
    payload = {
        "time": labels,
        "production_types": [{"name": k, "data": v} for k, v in series.items()],
    }
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payload)
    return source.fetch_installed_power(ZONE, time_step=time_step)


def test_installed_power_units_planned_flags_and_period_ends(monkeypatch):
    result = _installed(
        ["2023", "2024"],
        {
            "Battery storage (power)": [10.0, 15.0],
            "Battery storage (capacity)": [20.0, 30.0],
            "Wind onshore planned (EEG 2023)": [None, 99.0],
        },
        "yearly",
        monkeypatch,
    )
    units = dict(zip(result["technology"], result["unit"], strict=False))
    assert units == {
        "Battery storage (power)": "GW",
        "Battery storage (capacity)": "GWh",
        "Wind onshore planned (EEG 2023)": "GW",
    }
    planned = result.filter(pl.col("is_planned"))
    assert planned["value"].to_list() == [99.0]  # the null 2023 target is dropped, not zeroed
    assert result["as_of"].max() == dt.date(2024, 12, 31)
    # Monthly labels are "MM.YYYY", and a value describes the stock at the period's end.
    monthly = _installed(["02.2024"], {"Solar DC": [100.0]}, "monthly", monkeypatch)
    assert monthly["as_of"][0] == dt.date(2024, 2, 29)


def test_installed_power_is_empty_with_its_schema_and_rejects_a_bad_step(monkeypatch):
    assert _installed([], {}, "yearly", monkeypatch).schema == reference.CAPACITY_SCHEMA
    with pytest.raises(ValueError, match="time_step"):
        EnergyChartsSource().fetch_installed_power(ZONE, time_step="daily")


def _row(name: str, key: str) -> pl.DataFrame:
    schema = reference.CAPACITY_SCHEMA if name == "capacity" else reference.ABLATION_SCHEMA
    values = {
        "country": "DE",
        "time_step": "yearly",
        "period": key,
        "as_of": dt.date(2024, 12, 31),
        "technology": "Solar DC",
        "value": 120.0,
        "unit": "GW",
        "is_planned": False,
        "source": "energy_charts",
        "zone": "DE-LU",
        "model": key,
        "include_fundamentals": False,
        "n": 1000,
        "mae": 22.07,
        "rmse": 28.0,
        "skill_vs_best_baseline_pct": 24.3,
        "test_start": "2020-01-03",
        "test_end": "2026-09-12",
    }
    return pl.DataFrame({column: [values[column]] for column in schema}, schema=schema)


@pytest.mark.parametrize("name", ["capacity", "fundamentals_ablation"])
def test_reference_tables_are_replaced_wholesale(name, tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    empty = reference.read(name)
    assert empty.is_empty() and empty.columns == list(_row(name, "a").columns)
    reference.write(name, pl.DataFrame(schema=_row(name, "a").schema))
    assert not reference.path(name).exists()  # an empty fetch never erases the table

    reference.write(name, _row(name, "a"))
    # A revised series that no longer carries a row must not leave it behind.
    reference.write(name, _row(name, "b"))
    kept = reference.read(name)
    assert kept.height == 1
    assert kept.row(0) == _row(name, "b").row(0)
