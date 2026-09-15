"""Day-ahead fundamentals: fetch, store round-trip, and the panel wiring.

These cover the new pieces connecting real data to the previously-unused
``gpa.forecast.fundamentals`` architecture: combining onshore/offshore wind at
fetch time, the store round-trip through the new ``fundamentals`` dataset, and
the research-policy vintage :func:`gpa.forecast.fundamentals.from_store`
assigns for a historical backfill.
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


def _forecast_payloads(start: dt.datetime, hours: int = 24) -> dict[str, dict[str, object]]:
    stamps = [int((start + dt.timedelta(hours=i)).timestamp()) for i in range(hours)]
    return {
        "load": {"unix_seconds": stamps, "forecast_values": [100.0] * hours},
        "wind_onshore": {"unix_seconds": stamps, "forecast_values": [10.0] * hours},
        "wind_offshore": {"unix_seconds": stamps, "forecast_values": [5.0] * hours},
        "solar": {"unix_seconds": stamps, "forecast_values": [0.0] * hours},
    }


def test_energy_charts_fundamentals_combines_wind_and_requests_day_ahead(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payloads = _forecast_payloads(start)
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
    payloads = _forecast_payloads(start)
    payloads["wind_offshore"] = {"unix_seconds": [], "forecast_values": []}

    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    result = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=24))

    wind = result.filter(pl.col("series") == "wind")
    assert wind["forecast_mw"].unique().to_list() == [10.0]  # onshore only


def test_fundamentals_round_trip_through_the_store_and_pivot(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    # 2024-12-31 23:00 UTC = 2025-01-01 00:00 Berlin (CET, UTC+1 in January):
    # one full local delivery day, so every row shares one gate.
    start = dt.datetime(2024, 12, 31, 23, tzinfo=dt.UTC)
    payloads = _forecast_payloads(start)
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    fetched = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=24))

    store.write(fetched, "fundamentals")
    wide = fundamentals.from_store(ZONE)

    assert wide.height == 24
    assert wide["load_forecast_mw"].unique().to_list() == [100.0]
    assert wide["wind_forecast_mw"].unique().to_list() == [15.0]
    assert wide["solar_forecast_mw"].unique().to_list() == [0.0]
    # The historical-backfill policy: every row is assigned the market gate of
    # its own delivery day (noon Berlin time on D-1), not an observed
    # publication instant.
    assert wide["published_at"].n_unique() == 1
    gate = wide["published_at"][0]
    assert gate == dt.datetime(2024, 12, 31, 11, tzinfo=dt.UTC)  # noon Berlin on D-1


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
    source = EnergyChartsSource()
    payloads = _forecast_payloads(price_start)
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    fetched = source.fetch(ZONE, "fundamentals", price_start, price_start + dt.timedelta(hours=24))
    store.write(fetched, "fundamentals")

    without = load_panel(ZONE, include_fundamentals=False)
    with_fundamentals = load_panel(ZONE, include_fundamentals=True)

    assert not set(FUNDAMENTAL_FEATURES) & set(without.features)
    assert set(FUNDAMENTAL_FEATURES) <= set(with_fundamentals.features)
    row = with_fundamentals.frame.filter(
        (pl.col("local_date") == dt.date(2025, 1, 10)) & (pl.col("local_hour") == 0)
    ).row(0, named=True)
    assert row["da_load_forecast"] == 100.0
    assert row["da_wind_forecast"] == 15.0
    assert row["da_residual_load_forecast"] == pytest.approx(100.0 - 15.0 - 0.0)
    assert row["da_forecast_age_hours"] is not None
