"""Independent numerical cases and source drift scenarios from the data audit."""

import datetime as dt

import polars as pl
import pytest

from gpa import quality, store
from gpa.metrics import load, price
from gpa.sources.base import UpstreamError
from gpa.sources.energy_charts import EnergyChartsSource
from gpa.sources.ons import OnsSource
from gpa.zones import ZONES, get_zone

ZONE = get_zone("DE-LU")


def test_active_markets_need_no_market_credentials_or_manual_imports():
    assert {z.code for z in ZONES} == {"DE-LU", "FR", "ES", "BR-SIN"}
    assert {s for z in ZONES for s in z.sources.values()} == {"energy_charts", "ons"}
    with pytest.raises(KeyError):
        get_zone("CAISO")


def test_mixed_duration_statistics_and_curve_endpoints():
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    frame = pl.DataFrame(
        {
            "ts_utc": [start, start + dt.timedelta(hours=1)],
            "load_mw": [100.0, 0.0],
            "resolution_min": [60, 15],
        }
    )
    assert load.load_factor(frame, ZONE)["load_factor"][0] == 0.8
    assert load.duration_curve(frame)["exceedance_pct"].to_list() == [80.0, 100.0]
    assert load.duration_curve(frame, points=1)["exceedance_pct"].to_list() == [100.0]


def test_capture_preserves_both_autumn_delivery_hours():
    stamps = [dt.datetime(2025, 10, 26, h, tzinfo=dt.UTC) for h in (0, 1)]
    prices = pl.DataFrame({"ts_utc": stamps, "price": [0.0, 100.0], "resolution_min": [60, 60]})
    gen = pl.DataFrame(
        {"ts_utc": stamps, "gen_mw": [1.0, 9.0], "fuel": ["wind"] * 2, "resolution_min": [60, 60]}
    )
    row = price.capture_rate(prices, gen, ZONE, fuel="wind", period="all").row(0, named=True)
    assert row["capture_price"] == 90
    assert row["capture_rate"] == 1.8


def test_capture_integrates_overlaps_without_filling_a_gap():
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    prices = pl.DataFrame(
        {
            "ts_utc": [start, start + dt.timedelta(hours=2)],
            "price": [20.0, 100.0],
            "resolution_min": [60, 60],
        }
    )
    gen = pl.DataFrame(
        {
            "ts_utc": [start + dt.timedelta(minutes=30 * i) for i in range(6)],
            "gen_mw": [10.0] * 6,
            "fuel": ["wind"] * 6,
            "resolution_min": [30] * 6,
        }
    )
    result = price.capture_rate(prices, gen, ZONE, fuel="wind", period="all")
    assert result["energy_mwh"][0] == 20
    assert result["capture_price"][0] == 60


def test_volatility_does_not_bridge_missing_calendar_days():
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    frame = pl.DataFrame(
        {
            "ts_utc": [start + dt.timedelta(days=i) for i in (0, 1, 3, 4)],
            "price": [10.0, 20.0, 100.0, 120.0],
            "resolution_min": [60] * 4,
        }
    )
    result = price.realised_volatility(frame, ZONE, window=2)
    assert result["daily_change"].to_list() == [None, 10.0, None, None, 20.0]
    assert result["volatility"].null_count() == 5


def test_energy_charts_resolution_changes_by_delivery_day(monkeypatch):
    # Local midnight on Oct 1 is 22:00 UTC; request spans both products.
    start = dt.datetime(2025, 9, 29, 22, tzinfo=dt.UTC)
    stamps = [start + dt.timedelta(hours=i) for i in range(24)]
    stamps += [start + dt.timedelta(days=1, minutes=15 * i) for i in range(96)]
    payload = {
        "unix_seconds": [int(t.timestamp()) for t in stamps],
        "price": [10.0] * 120,
        "unit": "EUR / MWh",
    }
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda *a: payload)
    result = source.fetch(ZONE, "price", start, start + dt.timedelta(days=2))
    assert result["resolution_min"].to_list() == [60] * 24 + [15] * 96
    # A missing value is not a longer settlement interval.
    payload["price"][26] = None
    result = source.fetch(ZONE, "price", start, start + dt.timedelta(days=2))
    assert result["resolution_min"].to_list() == [60] * 24 + [15] * 95


def test_energy_charts_single_stamp_edge_day_takes_the_adjacent_cadence(monkeypatch):
    # The provider's inclusive end adds local midnight of the next day alone.
    start = dt.datetime(2025, 11, 9, 23, tzinfo=dt.UTC)  # 00:00 Berlin, 10 Nov
    stamps = [start + dt.timedelta(minutes=15 * i) for i in range(97)]
    payload = {
        "unix_seconds": [int(t.timestamp()) for t in stamps],
        "price": [1.0] * 97,
        "unit": "EUR / MWh",
    }
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda *a: payload)
    result = source.fetch(ZONE, "price", start, start + dt.timedelta(days=2))
    assert result.height == 97
    assert result["resolution_min"].unique().to_list() == [15]


def test_energy_charts_rejects_spacing_that_mixes_products_within_a_day(monkeypatch):
    start = dt.datetime(2025, 11, 9, 23, tzinfo=dt.UTC)
    minutes = [0, 15, 65, 80]  # a 50-minute gap is not a whole number of 15-minute steps
    payload = {
        "unix_seconds": [int((start + dt.timedelta(minutes=m)).timestamp()) for m in minutes],
        "price": [1.0] * len(minutes),
        "unit": "EUR / MWh",
    }
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda *a: payload)
    with pytest.raises(UpstreamError, match="irregular"):
        source.fetch(ZONE, "price", start, start + dt.timedelta(days=1))


def test_energy_charts_battery_output_and_charging_net_but_never_double_count(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    stamps = [int((start + dt.timedelta(hours=i)).timestamp()) for i in range(24)]
    payload = {
        "unix_seconds": stamps,
        "production_types": [
            {"name": "Battery", "data": [30.0] * 24},
            {"name": "Battery Consumption", "data": [-10.0] * 24},
        ],
    }
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda *a: payload)
    result = source.fetch(ZONE, "generation", start, start + dt.timedelta(hours=24))
    assert result["gen_mw"].unique().to_list() == [20.0]
    payload["production_types"].append({"name": "Battery Storage (Power)", "data": [20.0] * 24})
    with pytest.raises(UpstreamError, match="battery"):
        source.fetch(ZONE, "generation", start, start + dt.timedelta(hours=24))


def test_energy_charts_selects_one_load_definition_and_rejects_unknown_units(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payload = {
        "unix_seconds": [int((start + dt.timedelta(hours=i)).timestamp()) for i in range(24)],
        "production_types": [
            {"name": "Load", "data": [100.0] * 24},
            {"name": "Load (incl. self-consumption)", "data": [110.0] * 24},
            {"name": "New category", "data": [1.0] * 24},
        ],
    }
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda *a: payload)
    assert (
        source.fetch(ZONE, "load", start, start + dt.timedelta(hours=20))["load_mw"].to_list()
        == [100.0] * 20
    )
    with pytest.raises(UpstreamError, match="unrecognised"):
        source.fetch(ZONE, "generation", start, start + dt.timedelta(hours=20))


def test_ons_load_failure_cannot_fall_back_to_an_overlapping_hourly_series(monkeypatch):
    source = OnsSource()

    def fail(*a):
        raise UpstreamError("offline")

    monkeypatch.setattr(source, "_fetch_load_api", fail)
    with pytest.raises(UpstreamError, match="offline"):
        source.fetch(
            get_zone("BR-SIN"),
            "load",
            dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2025, 1, 2, tzinfo=dt.UTC),
        )


def test_quality_distinguishes_missing_time_from_overlapping_observations():
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    frame = pl.DataFrame(
        {
            "ts_utc": [start, start + dt.timedelta(hours=2)],
            "price": [1.0, 2.0],
            "resolution_min": [60, 60],
        }
    )
    result = quality.inspect(frame, "price", "DE-LU")
    assert result["invalid"][0] == 0
    assert result["gap_hours"][0] == 1
    assert result["coverage_pct"][0] == pytest.approx(200 / 3)
    overlap = frame.with_columns(pl.Series("ts_utc", [start, start + dt.timedelta(minutes=30)]))
    assert quality.inspect(overlap, "price", "DE-LU")["invalid"][0] > 0


def test_failed_atomic_write_preserves_original_partition(tmp_path, monkeypatch):
    path = tmp_path / "data.parquet"
    pl.DataFrame({"value": [1]}).write_parquet(path)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", fail)
    with pytest.raises(OSError, match="disk full"):
        store.atomic_parquet(pl.DataFrame({"value": [2]}), path)
    assert pl.read_parquet(path)["value"].to_list() == [1]
    assert list(tmp_path.iterdir()) == [path]


def test_ons_load_request_covers_local_days_around_a_utc_window(monkeypatch):
    """A Brasilia day ends at 03:00Z, so a UTC-midnight window needs the prior day."""
    from gpa.sources import ons

    requests: list[dict[str, str]] = []
    # One local day, 2026-01-01, as the API publishes it: 00:30 to 24:00 local.
    first_end = dt.datetime(2026, 1, 1, 3, 30, tzinfo=dt.UTC)
    stamps = [first_end + dt.timedelta(minutes=30 * i) for i in range(48)]
    earlier = [
        dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.UTC) + dt.timedelta(minutes=30 * i)
        for i in range(6)
    ]

    def fake(url, client=None, params=None, **kwargs):
        requests.append(params)
        chosen = earlier if params["dat_inicio"] <= "2025-12-31" else []
        rows = [
            {"din_referenciautc": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "val_cargaglobal": 10.0}
            for t in [*chosen, *stamps]
        ]
        return rows

    monkeypatch.setattr(ons, "fetch_json", fake)
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    result = OnsSource().fetch(get_zone("BR-SIN"), "load", start, start + dt.timedelta(days=1))
    assert requests[0]["dat_inicio"] == "2025-12-31"
    # 00:00Z is the start of the interval stamped 00:30Z; nothing before the window.
    assert result["ts_utc"].min() == start
    assert result["ts_utc"].max() == start + dt.timedelta(hours=23, minutes=30)
    assert result.height == 48
    assert result["resolution_min"].unique().to_list() == [30]


def test_ons_balance_window_opening_on_new_year_reads_the_previous_file(monkeypatch):
    from gpa.sources import ons

    years: list[str] = []

    def fake(url, client=None, allow_missing=False, **kwargs):
        years.append(url.rsplit("_", 1)[1][:4])
        return None

    monkeypatch.setattr(ons, "fetch_text", fake)
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    OnsSource().fetch(get_zone("BR-SIN"), "generation", start, start + dt.timedelta(days=2))
    assert years == ["2025", "2026"]
