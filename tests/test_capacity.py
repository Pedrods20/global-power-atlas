"""Installed capacity: fetch parsing and the reference-data store round trip.

Covers the new pieces added for the P3 capacity/cannibalisation study:
``EnergyChartsSource.fetch_installed_power``'s parsing of the
``/installed_power`` response, and ``gpa.capacity``'s file-per-country store,
kept deliberately separate from the interval-series ``gpa.store`` contract.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from gpa import capacity
from gpa.sources.energy_charts import EnergyChartsSource
from gpa.zones import get_zone

ZONE = get_zone("DE-LU")


def _payload(labels: list[str], series: dict[str, list[float | None]]) -> dict[str, object]:
    return {
        "time": labels,
        "production_types": [{"name": name, "data": data} for name, data in series.items()],
        "last_update": 0,
        "deprecated": False,
    }


def test_fetch_installed_power_labels_gwh_only_for_battery_capacity(monkeypatch):
    payload = _payload(
        ["2023", "2024"],
        {
            "Solar DC": [100.0, 120.0],
            "Battery storage (power)": [10.0, 15.0],
            "Battery storage (capacity)": [20.0, 30.0],
        },
    )
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payload)
    result = source.fetch_installed_power(ZONE, time_step="yearly")

    units = dict(zip(result["technology"], result["unit"], strict=True))
    assert units["Solar DC"] == "GW"
    assert units["Battery storage (power)"] == "GW"
    assert units["Battery storage (capacity)"] == "GWh"


def test_fetch_installed_power_flags_planned_series_and_keeps_it_distinct(monkeypatch):
    payload = _payload(
        ["2026", "2028", "2030"],
        {
            "Wind onshore": [70.0, None, None],
            "Wind onshore planned (EEG 2023)": [84.0, 99.0, 115.0],
        },
    )
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payload)
    result = source.fetch_installed_power(ZONE, time_step="yearly")

    realised = result.filter(pl.col("technology") == "Wind onshore")
    planned = result.filter(pl.col("technology") == "Wind onshore planned (EEG 2023)")
    assert realised["is_planned"].to_list() == [False]  # the null 2028/2030 rows are dropped
    assert planned["is_planned"].to_list() == [True, True, True]
    assert planned.sort("as_of")["value"].to_list() == [84.0, 99.0, 115.0]


def test_fetch_installed_power_as_of_is_the_end_of_the_labelled_period(monkeypatch):
    # Confirmed live: monthly labels are "MM.YYYY", not ISO "YYYY-MM".
    payload = _payload(["02.2024"], {"Solar DC": [100.0]})
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payload)
    result = source.fetch_installed_power(ZONE, time_step="monthly")

    assert result["as_of"][0] == dt.date(2024, 2, 29)  # 2024 is a leap year


def test_fetch_installed_power_rejects_an_unknown_time_step():
    source = EnergyChartsSource()
    with pytest.raises(ValueError, match="time_step"):
        source.fetch_installed_power(ZONE, time_step="daily")


def test_fetch_installed_power_is_empty_when_the_provider_returns_nothing(monkeypatch):
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: _payload([], {}))
    result = source.fetch_installed_power(ZONE, time_step="yearly")
    assert result.is_empty()
    assert set(result.columns) == set(capacity.CAPACITY_COLUMNS)


def test_capacity_write_then_read_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    frame = pl.DataFrame(
        {
            "country": ["DE"],
            "time_step": ["yearly"],
            "period": ["2024"],
            "as_of": [dt.date(2024, 12, 31)],
            "technology": ["Solar DC"],
            "value": [120.0],
            "unit": ["GW"],
            "is_planned": [False],
            "source": ["energy_charts"],
        },
        schema=capacity.CAPACITY_COLUMNS,
    )
    path = capacity.write(frame)
    assert path.exists()
    assert capacity.read()["value"].to_list() == [120.0]


def test_capacity_write_replaces_rather_than_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))

    def row(period: str, value: float) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "country": ["DE"],
                "time_step": ["yearly"],
                "period": [period],
                "as_of": [dt.date(int(period), 12, 31)],
                "technology": ["Solar DC"],
                "value": [value],
                "unit": ["GW"],
                "is_planned": [False],
                "source": ["energy_charts"],
            },
            schema=capacity.CAPACITY_COLUMNS,
        )

    capacity.write(row("2023", 100.0))
    # A revised fetch that no longer reports 2023 (e.g. it fell off a
    # provider's window) must not leave the old 2023 row behind.
    capacity.write(row("2024", 120.0))

    assert capacity.read()["period"].to_list() == ["2024"]


def test_capacity_read_is_empty_before_anything_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    result = capacity.read()
    assert result.is_empty()
    assert set(result.columns) == set(capacity.CAPACITY_COLUMNS)
