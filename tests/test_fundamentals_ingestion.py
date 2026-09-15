"""Day-ahead fundamentals: fetch, store round-trip, and the panel wiring.

These cover the new pieces connecting real data to the previously-unused
``gpa.forecast.fundamentals`` architecture: combining onshore/offshore wind at
fetch time, the store round-trip through the new ``fundamentals`` dataset, and
the hourly aggregation and research-policy vintage
:func:`gpa.forecast.fundamentals.from_store` applies for a historical backfill.

Energy-Charts' forecast endpoint has been quarter-hourly throughout its
archive (unlike ``price``/``load``/``generation``, which changed resolution
later), so the fixtures below use quarter-hour spacing to match what a real
backfill actually returns, not the hourly shape those other datasets have had
for most of their history.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from gpa import store
from gpa.forecast import fundamentals
from gpa.forecast.fundamentals import FUNDAMENTAL_FEATURES
from gpa.forecast.panel import load_panel
from gpa.sources.energy_charts import EnergyChartsSource
from gpa.zones import get_zone

ZONE = get_zone("DE-LU")


def _hourly_payloads(start: dt.datetime, hours: int = 24) -> dict[str, dict[str, object]]:
    stamps = [int((start + dt.timedelta(hours=i)).timestamp()) for i in range(hours)]
    return {
        "load": {"unix_seconds": stamps, "forecast_values": [100.0] * hours},
        "wind_onshore": {"unix_seconds": stamps, "forecast_values": [10.0] * hours},
        "wind_offshore": {"unix_seconds": stamps, "forecast_values": [5.0] * hours},
        "solar": {"unix_seconds": stamps, "forecast_values": [0.0] * hours},
    }


def _quarter_hour_payloads(start: dt.datetime, hours: int = 24) -> dict[str, dict[str, object]]:
    """Realistic shape: 4 quarter-hour values per clock hour, like the real provider."""
    n = hours * 4
    stamps = [int((start + dt.timedelta(minutes=15 * i)).timestamp()) for i in range(n)]
    # Values step up by 1 each quarter-hour so an hourly mean is checkable and
    # distinct from any single sub-interval's value.
    return {
        "load": {"unix_seconds": stamps, "forecast_values": [100.0 + i for i in range(n)]},
        "wind_onshore": {"unix_seconds": stamps, "forecast_values": [10.0] * n},
        "wind_offshore": {"unix_seconds": stamps, "forecast_values": [5.0] * n},
        "solar": {"unix_seconds": stamps, "forecast_values": [0.0] * n},
    }


def test_energy_charts_fundamentals_combines_wind_and_requests_day_ahead(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payloads = _hourly_payloads(start)
    seen_forecast_types = []

    def fake_get(path, params):
        seen_forecast_types.append(params["forecast_type"])
        return payloads[params["production_type"]]

    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", fake_get)
    result = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=24))

    assert set(seen_forecast_types) == {"day-ahead"}
    assert set(result["series"].unique().to_list()) == {"load", "wind", "solar"}
    wind = result.filter(pl.col("series") == "wind")
    assert wind["forecast_mw"].unique().to_list() == [15.0]  # 10 onshore + 5 offshore
    assert result["resolution_min"].unique().to_list() == [60]
    assert result["zone"].unique().to_list() == ["DE-LU"]


def test_energy_charts_fundamentals_tolerates_one_missing_series(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payloads = _hourly_payloads(start)
    payloads["wind_offshore"] = {"unix_seconds": [], "forecast_values": []}

    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    result = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=24))

    wind = result.filter(pl.col("series") == "wind")
    assert wind["forecast_mw"].unique().to_list() == [10.0]  # onshore only


def test_energy_charts_fundamentals_measures_quarter_hour_resolution(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payloads = _quarter_hour_payloads(start, hours=2)
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    result = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=2))
    assert result["resolution_min"].unique().to_list() == [15]
    assert result.filter(pl.col("series") == "load").height == 8


def _write_fundamentals(monkeypatch, start: dt.datetime, hours: int) -> None:
    source = EnergyChartsSource()
    payloads = _quarter_hour_payloads(start, hours=hours)
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    fetched = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=hours))
    store.write(fetched, "fundamentals")


def test_from_store_averages_quarter_hours_into_one_hourly_value(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    # 2024-12-31 23:00 UTC = 2025-01-01 00:00 Berlin (CET, UTC+1 in January):
    # one full local delivery day, so every row shares one gate.
    start = dt.datetime(2024, 12, 31, 23, tzinfo=dt.UTC)
    _write_fundamentals(monkeypatch, start, hours=24)

    wide = fundamentals.from_store(ZONE)

    assert wide.height == 24  # one row per clock hour, not per quarter-hour
    first_hour = wide.sort("ts_utc").row(0, named=True)
    # Quarter-hour load values for the first hour are 100, 101, 102, 103.
    assert first_hour["load_forecast_mw"] == pytest.approx(101.5)
    assert first_hour["wind_forecast_mw"] == pytest.approx(15.0)
    assert first_hour["solar_forecast_mw"] == pytest.approx(0.0)
    # The historical-backfill policy: every row is assigned the market gate of
    # its own delivery day (noon Berlin time on D-1), not an observed
    # publication instant.
    assert wide["published_at"].n_unique() == 1
    assert wide["published_at"][0] == dt.datetime(2024, 12, 31, 11, tzinfo=dt.UTC)


def test_from_store_drops_a_partial_hour_rather_than_averaging_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    start = dt.datetime(2024, 12, 31, 23, tzinfo=dt.UTC)
    source = EnergyChartsSource()
    payloads = _quarter_hour_payloads(start, hours=2)
    # Drop the last quarter-hour of the second hour: that hour is now partial.
    for payload in payloads.values():
        payload["unix_seconds"] = payload["unix_seconds"][:-1]
        payload["forecast_values"] = payload["forecast_values"][:-1]
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    fetched = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=2))
    store.write(fetched, "fundamentals")

    wide = fundamentals.from_store(ZONE)

    assert wide.height == 1  # only the complete first hour survives


def test_from_store_is_empty_when_nothing_backfilled(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    assert fundamentals.from_store(ZONE).is_empty()


def test_load_panel_include_fundamentals_adds_the_feature_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    # A full local day of price plus one day-ahead fundamentals snapshot.
    # 2025-01-09 23:00 UTC = 2025-01-10 00:00 Berlin.
    price_start = dt.datetime(2025, 1, 9, 23, tzinfo=dt.UTC)
    price_stamps = [price_start + dt.timedelta(hours=i) for i in range(24)]
    store.write(
        pl.DataFrame(
            {
                "zone": [ZONE.code] * 24,
                "ts_utc": price_stamps,
                "resolution_min": [60] * 24,
                "price": [40.0 + i for i in range(24)],
                "currency": ["EUR"] * 24,
                "source": ["test"] * 24,
            },
            schema={
                "zone": pl.String,
                "ts_utc": pl.Datetime("us", "UTC"),
                "resolution_min": pl.Int16,
                "price": pl.Float64,
                "currency": pl.String,
                "source": pl.String,
            },
        ),
        "price",
    )
    _write_fundamentals(monkeypatch, price_start, hours=24)

    without = load_panel(ZONE, include_fundamentals=False)
    with_fundamentals = load_panel(ZONE, include_fundamentals=True)

    assert not set(FUNDAMENTAL_FEATURES) & set(without.features)
    assert set(FUNDAMENTAL_FEATURES) <= set(with_fundamentals.features)
    row = with_fundamentals.frame.filter(
        (pl.col("local_date") == dt.date(2025, 1, 10)) & (pl.col("local_hour") == 0)
    ).row(0, named=True)
    assert row["da_load_forecast"] == pytest.approx(101.5)  # mean of 100, 101, 102, 103
    assert row["da_wind_forecast"] == pytest.approx(15.0)
    assert row["da_residual_load_forecast"] == pytest.approx(101.5 - 15.0 - 0.0)
    assert row["da_forecast_age_hours"] is not None
