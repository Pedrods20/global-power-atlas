"""Energy-Charts (Fraunhofer ISE): an open, credential-free republication of TSO data.

Its ``end`` is inclusive, so rows are trimmed to the caller's half-open window, and
its resolution changes without notice, so each day's cadence is measured, not assumed.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
from typing import Any, Final

import polars as pl

from gpa.reference import CAPACITY_SCHEMA
from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import UpstreamError, fetch_json, http_client
from gpa.zones import Zone

__all__ = ["DERIVED_SERIES", "FUEL_MAP", "LOAD_SERIES", "EnergyChartsSource"]

log = logging.getLogger(__name__)

_BASE = "https://api.energy-charts.info"

FUEL_MAP: Final[dict[str, str]] = {
    "Hydro Run-of-River": "hydro",
    "Hydro water reservoir": "hydro",
    "Hydro pumped storage": "hydro_pumped_storage",
    "Hydro pumped storage consumption": "hydro_pumped_storage",
    "Biomass": "biomass",
    "Fossil brown coal / lignite": "coal",
    "Fossil hard coal": "coal",
    "Fossil peat": "coal",
    "Fossil oil": "oil",
    "Fossil oil shale": "oil",
    "Fossil coal-derived gas": "gas",
    "Fossil gas": "gas",
    "Geothermal": "geothermal",
    "Nuclear": "nuclear",
    "Waste": "waste",
    "Wind offshore": "wind",
    "Wind onshore": "wind",
    "Solar": "solar",
    "Battery Storage (Power)": "battery",
    "Battery": "battery",
    "Battery Consumption": "battery",
    "Other renewables": "other",
    "Others": "other",
    "Other": "other",
    "Cross border electricity trading": "imports",
}
"""Names sharing a fuel are summed; storage consumption is negative, so the sum is net output."""

LOAD_SERIES: Final = frozenset({"Load", "Load (incl. self-consumption)"})

DERIVED_SERIES: Final = frozenset(
    {"Residual load", "Renewable share of load", "Renewable share of generation"}
)
"""Computed indicators, dropped: anything needed is recomputed from the measurements."""

_FORECAST_TYPES: Final = {
    "load": "load",
    "wind_onshore": "wind",
    "wind_offshore": "wind",
    "solar": "solar",
}


class EnergyChartsSource:
    name: str = "energy_charts"
    datasets: tuple[str, ...] = ("price", "load", "generation", "fundamentals")
    # No documented cap, but /public_power times out well before a quarter of 15-minute data.
    max_window_days: int | None = 60

    def fetch(self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")
        if dataset == "price":
            return self._fetch_price(zone, start, end)
        if dataset == "fundamentals":
            return self._fetch_fundamentals(zone, start, end)
        return self._fetch_power(zone, dataset, start, end)

    def _finish(
        self, frame: pl.DataFrame, zone: Zone, seconds: list[int], *columns: str
    ) -> pl.DataFrame:
        """Attach each interval's measured duration and the provenance columns."""
        return (
            frame.join(_resolution_table(seconds, zone), on="ts_utc")
            .with_columns(pl.lit(zone.code).alias("zone"), pl.lit(self.name).alias("source"))
            .select("zone", "ts_utc", "resolution_min", *columns, "source")
            .sort("ts_utc", *(c for c in columns if c in ("fuel", "series")))
        )

    def _fetch_price(self, zone: Zone, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        params = {"bzn": zone.code, "start": _as_date(start), "end": _as_date(end)}
        payload = self._get("/price", params)
        seconds = payload.get("unix_seconds") or []
        if not seconds:
            return empty_frame("price")
        currency = _currency(str(payload.get("unit", "")), zone)
        prices = _window(_stamp(_values(seconds, payload.get("price") or [])), start, end)
        if prices.is_empty():
            return empty_frame("price")
        prices = prices.rename({"_value": "price"}).with_columns(currency=pl.lit(currency))
        return self._finish(prices, zone, seconds, "price", "currency")

    def _fetch_power(
        self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime
    ) -> pl.DataFrame:
        params = {"country": _country(zone), "start": _as_date(start), "end": _as_date(end)}
        payload = self._get("/public_power", params)
        seconds = payload.get("unix_seconds") or []
        series = payload.get("production_types") or []
        if not seconds or not series:
            return empty_frame(dataset)
        labels = {str(entry.get("name", "")) for entry in series}
        # Alternative definitions, not additive: prefer the load on the generation boundary.
        load_label = "Load" if "Load" in labels else "Load (incl. self-consumption)"
        # Some countries publish battery output and charging apart, others net: never both.
        if "Battery Storage (Power)" in labels and labels & {"Battery", "Battery Consumption"}:
            raise UpstreamError("net and gross battery series published together")
        frames = []
        for entry in series:
            label = str(entry.get("name", ""))
            skip = label != load_label if dataset == "load" else label in LOAD_SERIES
            if label in DERIVED_SERIES or skip:
                continue
            if dataset != "load" and label not in FUEL_MAP:
                raise UpstreamError(f"unrecognised generation series {label!r}; review its units")
            chunk = _values(seconds, entry.get("data") or [])
            if dataset != "load":
                chunk = chunk.with_columns(fuel=pl.lit(FUEL_MAP[label]))
            frames.append(chunk)
        combined = _window(_stamp(pl.concat(frames)), start, end) if frames else pl.DataFrame()
        if combined.is_empty():
            return empty_frame(dataset)
        if dataset == "load":
            load = combined.group_by("ts_utc").agg(pl.col("_value").sum().alias("load_mw"))
            return self._finish(load, zone, seconds, "load_mw")
        output = combined.group_by("ts_utc", "fuel").agg(pl.col("_value").sum().alias("gen_mw"))
        return self._finish(output, zone, seconds, "fuel", "gen_mw")

    def _fetch_fundamentals(self, zone: Zone, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        """Day-ahead forecasts as the provider holds them now; it exposes no vintage."""
        frames: list[pl.DataFrame] = []
        stamps: set[int] = set()
        for production_type, series in _FORECAST_TYPES.items():
            params = {
                "country": _country(zone),
                "production_type": production_type,
                "forecast_type": "day-ahead",
                "start": _as_date(start),
                "end": _as_date(end),
            }
            payload = self._get("/public_power_forecast", params)
            seconds = payload.get("unix_seconds") or []
            values = payload.get("forecast_values") or []
            n = min(len(seconds), len(values))
            if not n:
                continue
            stamps.update(seconds[:n])
            frames.append(
                _values(seconds, values).with_columns(
                    series=pl.lit(series), _component=pl.lit(production_type)
                )
            )
        combined = _window(_stamp(pl.concat(frames)), start, end) if frames else pl.DataFrame()
        if combined.is_empty():
            return empty_frame("fundamentals")
        # Total wind needs both components, once each: missing offshore is not zero output.
        components = pl.when(pl.col("series") == "wind").then(2).otherwise(1)
        forecasts = (
            combined.group_by("ts_utc", "series")
            .agg(
                pl.col("_value").sum().alias("forecast_mw"),
                pl.col("_component").n_unique().alias("_components"),
                pl.len().alias("_rows"),
            )
            .filter(
                (pl.col("_components") == components) & (pl.col("_rows") == pl.col("_components"))
            )
        )
        return self._finish(forecasts, zone, sorted(stamps), "series", "forecast_mw")

    def fetch_installed_power(self, zone: Zone, *, time_step: str = "yearly") -> pl.DataFrame:
        """Installed capacity per technology: the full current series, labelled by period end.

        A "planned" series is a policy target and stays a distinct row; only battery
        energy capacity is in GWh. Monthly data is documented as Germany-only.
        """
        if time_step not in ("yearly", "monthly"):
            raise ValueError(f"time_step must be 'yearly' or 'monthly', got {time_step!r}")
        country = _country(zone)
        payload = self._get("/installed_power", {"country": country, "time_step": time_step})
        labels = [str(value) for value in (payload.get("time") or [])]
        rows = []
        for entry in payload.get("production_types") or []:
            name = str(entry.get("name", ""))
            for label, value in zip(labels, entry.get("data") or [], strict=False):
                if value is not None:
                    rows.append(
                        {
                            "country": country.upper(),
                            "time_step": time_step,
                            "period": label,
                            "as_of": _period_end(label, time_step),
                            "technology": name,
                            "value": float(value),
                            "unit": "GWh" if name == "Battery storage (capacity)" else "GW",
                            "is_planned": "planned" in name.lower(),
                            "source": self.name,
                        }
                    )
        return pl.DataFrame(rows, schema=CAPACITY_SCHEMA).sort("technology", "as_of")

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        with http_client() as client:
            payload = fetch_json(f"{_BASE}{path}", client=client, params=params)
        if not isinstance(payload, dict):
            raise UpstreamError(f"unexpected payload type {type(payload).__name__} from {path}")
        if payload.get("deprecated"):
            # A warning, not a failure: the endpoint keeps serving through a grace period.
            log.warning("Energy-Charts marks %s as deprecated", path)
        return payload


def _country(zone: Zone) -> str:
    country = zone.source_keys.get("energy_charts_country")
    if not country:
        raise UpstreamError(f"zone {zone.code} has no 'energy_charts_country' in source_keys")
    return country


def _as_date(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).date().isoformat()


def _values(seconds: list[int], values: list[Any]) -> pl.DataFrame:
    n = min(len(seconds), len(values))
    frame = pl.DataFrame(
        {"_epoch": seconds[:n], "_value": values[:n]},
        schema={"_epoch": pl.Int64, "_value": pl.Float64},
    )
    return frame.drop_nulls("_value")


def _stamp(frame: pl.DataFrame) -> pl.DataFrame:
    """The ``_epoch`` seconds column as the canonical UTC timestamp."""
    utc = pl.from_epoch(pl.col("_epoch"), time_unit="s").dt.replace_time_zone("UTC")
    return frame.with_columns(utc.cast(UTC_DATETIME).alias("ts_utc")).drop("_epoch")


def _window(frame: pl.DataFrame, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
    return frame.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))


def _period_end(label: str, time_step: str) -> dt.date:
    """The date an /installed_power label ends: ``"YYYY"`` yearly, ``"MM.YYYY"`` monthly."""
    if time_step == "yearly":
        return dt.date(int(label), 12, 31)
    month, year = (int(part) for part in label.split("."))
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _currency(unit: str, zone: Zone) -> str:
    """The ISO currency of a unit such as ``'EUR / MWh'``; anything else is a parsing error."""
    head = unit.split("/")[0].strip().upper()
    if head != zone.currency or unit.split("/")[-1].strip().lower() != "mwh":
        raise UpstreamError(f"unexpected price unit {unit!r} for {zone.code}")
    return head


def _resolution_table(seconds: list[int], zone: Zone) -> pl.DataFrame:
    """Each timestamp's interval length, measured on the full axis, per local delivery day.

    Products change only at day boundaries, so a gap never stretches an interval and a
    day holding one edge timestamp takes its neighbour's cadence.
    """
    axis = _stamp(pl.DataFrame({"_epoch": seconds}, schema={"_epoch": pl.Int64})).sort("ts_utc")
    if axis["ts_utc"].n_unique() != axis.height:
        raise UpstreamError("duplicate timestamps in Energy-Charts response")
    day = pl.col("ts_utc").dt.convert_time_zone(zone.timezone).dt.date()
    axis = axis.with_columns(
        day.alias("_day"), pl.col("ts_utc").diff().over(day).dt.total_minutes().alias("_step")
    )
    cadence = (
        axis.group_by("_day")
        .agg(
            pl.col("_step").min().alias("resolution_min"),
            pl.col("_step").drop_nulls().alias("_steps"),
        )
        .sort("_day")
    )
    unsupported = cadence.filter(
        pl.col("resolution_min").is_not_null() & ~pl.col("resolution_min").is_in([15, 60])
    )
    if unsupported.height:
        raise UpstreamError(f"unsupported timestamp cadence on {unsupported['_day'].to_list()[:3]}")
    # Every gap in a day must be a whole number of its intervals, or the day mixes products.
    irregular = cadence.filter(
        pl.col("_steps").list.eval(pl.element() % pl.element().min() != 0).list.any()
    )
    if irregular.height:
        raise UpstreamError(f"irregular timestamp spacing on {irregular['_day'].to_list()[:3]}")
    cadence = cadence.with_columns(pl.col("resolution_min").forward_fill().backward_fill())
    if cadence["resolution_min"].null_count():
        raise UpstreamError("cannot measure timestamp cadence from a single observation")
    resolved = axis.join(cadence.select("_day", "resolution_min"), on="_day")
    misaligned = pl.col("ts_utc").dt.epoch("s") % (pl.col("resolution_min") * 60) != 0
    if resolved.filter(misaligned).height:
        raise UpstreamError("timestamps are not aligned to their interval boundaries")
    return resolved.select("ts_utc", pl.col("resolution_min").cast(pl.Int16))
