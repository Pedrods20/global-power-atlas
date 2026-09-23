"""Day-ahead fundamentals: fetch, store round-trip, and the panel wiring.

These cover the new pieces connecting real data to the previously-unused
``gpa.forecast.fundamentals`` architecture: combining onshore/offshore wind at
fetch time, the store round-trip through the new ``fundamentals`` dataset, and
the hourly aggregation and research-policy vintage
:func:`gpa.forecast.fundamentals.from_store` applies for a historical backfill.

The historical forecast backfill contains quarter-hourly values, so the
fixtures below exercise that spacing as well as hourly provider responses.
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


def test_energy_charts_fundamentals_omits_total_wind_when_offshore_is_missing(monkeypatch):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payloads = _hourly_payloads(start)
    payloads["wind_offshore"] = {"unix_seconds": [], "forecast_values": []}

    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    result = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=24))

    wind = result.filter(pl.col("series") == "wind")
    assert wind.is_empty()
    assert set(result["series"]) == {"load", "solar"}


@pytest.mark.parametrize("component", ["wind_onshore", "wind_offshore"])
@pytest.mark.parametrize("failure", ["missing", "null", "duplicate"])
def test_energy_charts_fundamentals_requires_both_wind_components_per_interval(
    monkeypatch, component, failure
):
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    payloads = _hourly_payloads(start, hours=2)
    if failure == "missing":
        payloads[component]["unix_seconds"] = payloads[component]["unix_seconds"][1:]
        payloads[component]["forecast_values"] = payloads[component]["forecast_values"][1:]
    elif failure == "null":
        payloads[component]["forecast_values"][0] = None
    else:
        payloads[component]["unix_seconds"].append(int(start.timestamp()))
        payloads[component]["forecast_values"].append(10.0)
    source = EnergyChartsSource()
    monkeypatch.setattr(source, "_get", lambda path, params: payloads[params["production_type"]])
    result = source.fetch(ZONE, "fundamentals", start, start + dt.timedelta(hours=2))
    wind = result.filter(pl.col("series") == "wind")
    assert wind["ts_utc"].to_list() == [start + dt.timedelta(hours=1)]
    assert wind["forecast_mw"].to_list() == [15.0]
    assert result.filter(pl.col("series") == "load").height == 2


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


# --- the prospective vintage -------------------------------------------------
#
# 2024-12-31 23:00 UTC starts one full Berlin delivery day (2025-01-01), whose
# gate is noon Berlin on D-1, i.e. 2024-12-31 11:00 UTC in January.

_GATE = dt.datetime(2024, 12, 31, 11, tzinfo=dt.UTC)
_DAY_START = dt.datetime(2024, 12, 31, 23, tzinfo=dt.UTC)


def _first_hour_panel() -> pl.DataFrame:
    return pl.DataFrame(
        {"local_date": [dt.date(2025, 1, 1)], "local_hour": [0]},
        schema={"local_date": pl.Date, "local_hour": pl.Int8},
    )


def test_an_observed_vintage_makes_the_forecast_age_real_rather_than_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    _write_fundamentals(monkeypatch, _DAY_START, hours=24)
    panel = _first_hour_panel()

    policy = fundamentals.attach(panel, fundamentals.from_store(ZONE), ZONE)
    observed = fundamentals.attach(
        panel,
        fundamentals.from_store_prospective(
            ZONE, delivery_date=dt.date(2025, 1, 1), retrieved_at=_GATE - dt.timedelta(hours=6)
        ),
        ZONE,
    )

    # The constant zero is exactly why the backfilled features are published
    # only as a labelled ablation; a real run has a real, varying age.
    assert policy["da_forecast_age_hours"][0] == 0
    assert observed["da_forecast_age_hours"][0] == 6


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


# --- the mixed vintage a live issue can actually train on ---------------------
#
# 2025-01-05 is the delivery day; 2025-01-01 onwards is its training history.
# The gate for 2025-01-05 is noon Berlin on 2025-01-04, i.e. 11:00 UTC.

_DELIVERY = dt.date(2025, 1, 5)
_DELIVERY_GATE = dt.datetime(2025, 1, 4, 11, tzinfo=dt.UTC)
_RETRIEVED = _DELIVERY_GATE - dt.timedelta(hours=6)


def _multi_day_panel() -> pl.DataFrame:
    """Hour zero of five consecutive delivery days: four of history, one target."""
    return pl.DataFrame(
        {
            "local_date": [dt.date(2025, 1, day) for day in range(1, 6)],
            "local_hour": [0] * 5,
        },
        schema={"local_date": pl.Date, "local_hour": pl.Int8},
    )


def _write_five_days(monkeypatch) -> None:
    _write_fundamentals(monkeypatch, _DAY_START, hours=24 * 5)


def test_the_prospective_vintage_keeps_the_history_the_model_has_to_fit(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    _write_five_days(monkeypatch)

    attached = fundamentals.attach(
        _multi_day_panel(),
        fundamentals.from_store_prospective(ZONE, delivery_date=_DELIVERY, retrieved_at=_RETRIEVED),
        ZONE,
    )

    assert attached["da_load_forecast"].null_count() == 0
    # Only the delivery day's vintage is a claim about what the issue knew, and
    # only there is it observed. History keeps the research-policy gate, which
    # is what makes its age identically zero.
    history = attached.filter(pl.col("local_date") < _DELIVERY)
    delivery = attached.filter(pl.col("local_date") == _DELIVERY)
    assert history["da_forecast_age_hours"].unique().to_list() == [0]
    assert delivery["da_forecast_age_hours"].to_list() == [6]


def test_the_prospective_vintage_still_refuses_a_delivery_day_read_after_the_gate(
    tmp_path, monkeypatch
):
    """A late retrieval costs the delivery day, never the training history."""
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    _write_five_days(monkeypatch)

    attached = fundamentals.attach(
        _multi_day_panel(),
        fundamentals.from_store_prospective(
            ZONE,
            delivery_date=_DELIVERY,
            retrieved_at=_DELIVERY_GATE + dt.timedelta(hours=1),
        ),
        ZONE,
    )

    assert attached.filter(pl.col("local_date") == _DELIVERY)["da_load_forecast"][0] is None
    assert attached.filter(pl.col("local_date") < _DELIVERY)["da_load_forecast"].null_count() == 0


def test_from_store_prospective_rejects_a_naive_instant(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="timezone-aware"):
        fundamentals.from_store_prospective(
            ZONE, delivery_date=_DELIVERY, retrieved_at=dt.datetime(2025, 1, 4, 5)
        )


def test_from_store_prospective_is_empty_when_nothing_backfilled(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    assert fundamentals.from_store_prospective(
        ZONE, delivery_date=_DELIVERY, retrieved_at=_RETRIEVED
    ).is_empty()


def test_the_forecast_age_is_recorded_on_the_panel_but_never_fitted(tmp_path, monkeypatch):
    """It is constant wherever the vintage is the gate, so it can only mislead."""
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    _write_five_days(monkeypatch)

    attached = fundamentals.attach(
        _multi_day_panel(),
        fundamentals.from_store_prospective(ZONE, delivery_date=_DELIVERY, retrieved_at=_RETRIEVED),
        ZONE,
    )

    assert "da_forecast_age_hours" in attached.columns
    assert "da_forecast_age_hours" in fundamentals.FUNDAMENTAL_METADATA
    assert "da_forecast_age_hours" not in FUNDAMENTAL_FEATURES
    assert set(fundamentals.FUNDAMENTAL_COLUMNS) == set(FUNDAMENTAL_FEATURES) | set(
        fundamentals.FUNDAMENTAL_METADATA
    )
