"""Energy-Charts adapter (Fraunhofer ISE), covering European bidding zones.

Energy-Charts republishes ENTSO-E and SMARD data through an open API that needs
no credentials, which is why it carries the European zone here while an ENTSO-E
Transparency token is being obtained. The underlying figures are the same ones
ENTSO-E publishes; the licence is CC BY 4.0 and attribution is on the
methodology page.

Two quirks of this provider are handled explicitly:

``end`` is inclusive
    Asking for 1 August to 2 August returns both days in full. Rows are
    filtered to the caller's half-open window after parsing.

Resolution changes without notice
    German data moved from hourly to quarter-hourly. The interval is measured
    from the returned timestamps rather than assumed.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

import polars as pl

from gpa.schema import UTC_DATETIME
from gpa.schema import empty_frame as _empty
from gpa.sources.base import UpstreamError, fetch_json, http_client, infer_resolution_minutes
from gpa.zones import Zone

__all__ = ["DERIVED_SERIES", "FUEL_MAP", "LOAD_SERIES", "EnergyChartsSource"]

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
    "Others": "other",
    "Other": "other",
    "Cross border electricity trading": "imports",
}
"""Upstream series name to canonical fuel.

Several upstream names collapse onto one canonical fuel, so rows are summed
after mapping. Pumped storage generation and its separately reported
consumption both map to ``hydro_pumped_storage``; because consumption is
published as a negative number, summing them yields net storage output, which
is the honest figure and keeps the fuel out of renewable share.
"""

LOAD_SERIES: Final[frozenset[str]] = frozenset({"Load", "Load (incl. self-consumption)"})
"""Series carrying system load rather than generation."""

DERIVED_SERIES: Final[frozenset[str]] = frozenset(
    {
        "Residual load",
        "Renewable share of load",
        "Renewable share of generation",
    }
)
"""Series that are already-computed indicators, not measurements.

They are dropped rather than stored. This project recomputes renewable share
from the mix so the definition is ours and is documented, and so it stays
consistent across markets whose providers disagree about what counts.
"""


class EnergyChartsSource:
    """Fetch price, load and generation for a European bidding zone."""

    name = "energy_charts"
    datasets = ("price", "load", "generation")
    # No documented cap, but the public_power endpoint returns about twenty
    # series at quarter-hourly resolution and starts timing out well before a
    # quarter's worth of data. Sixty days is comfortably inside that.
    max_window_days = 60

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")

        if dataset == "price":
            return self._fetch_price(zone, start, end)
        return self._fetch_power(zone, dataset, start, end)

    # --- price ------------------------------------------------------------

    def _fetch_price(self, zone: Zone, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        bzn = zone.source_keys.get("energy_charts_bzn") or zone.code
        payload = self._get(
            "/price",
            {"bzn": bzn, "start": _as_date(start), "end": _as_date(end)},
        )

        seconds = payload.get("unix_seconds") or []
        prices = payload.get("price") or []
        if not seconds:
            return _empty("price")

        unit = str(payload.get("unit", ""))
        currency = _currency_from_unit(unit, zone)

        frame = pl.DataFrame(
            {"_epoch": seconds[: len(prices)], "price": prices[: len(seconds)]},
            schema={"_epoch": pl.Int64, "price": pl.Float64},
        )
        frame = _stamp(frame).drop_nulls("price")
        frame = _window(frame, start, end)
        if frame.is_empty():
            return _empty("price")

        return frame.with_columns(
            pl.lit(zone.code).alias("zone"),
            pl.lit(infer_resolution_minutes(frame["ts_utc"]))
            .cast(pl.Int16)
            .alias("resolution_min"),
            pl.lit(currency).alias("currency"),
            pl.lit(self.name).alias("source"),
        ).select("zone", "ts_utc", "resolution_min", "price", "currency", "source")

    # --- load and generation ---------------------------------------------

    def _fetch_power(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        country = zone.source_keys.get("energy_charts_country")
        if not country:
            raise UpstreamError(f"zone {zone.code} has no 'energy_charts_country' in source_keys")

        payload = self._get(
            "/public_power",
            {"country": country, "start": _as_date(start), "end": _as_date(end)},
        )

        seconds = payload.get("unix_seconds") or []
        series = payload.get("production_types") or []
        if not seconds or not series:
            return _empty(dataset)

        wanted = LOAD_SERIES if dataset == "load" else None
        frames: list[pl.DataFrame] = []

        for entry in series:
            label = str(entry.get("name", ""))
            if label in DERIVED_SERIES:
                continue
            if wanted is not None and label not in wanted:
                continue
            if wanted is None and (label in LOAD_SERIES or label not in FUEL_MAP):
                continue

            values = entry.get("data") or []
            n = min(len(seconds), len(values))
            if n == 0:
                continue

            chunk = pl.DataFrame(
                {"_epoch": seconds[:n], "_value": values[:n]},
                schema={"_epoch": pl.Int64, "_value": pl.Float64},
            ).drop_nulls("_value")

            if wanted is None:
                chunk = chunk.with_columns(pl.lit(FUEL_MAP[label]).alias("fuel"))
            frames.append(chunk)

        if not frames:
            return _empty(dataset)

        combined = _window(_stamp(pl.concat(frames, how="vertical")), start, end)
        if combined.is_empty():
            return _empty(dataset)

        resolution = infer_resolution_minutes(combined["ts_utc"].unique())

        if dataset == "load":
            # Only one load series should survive the filter, but summing is
            # harmless and guards against the provider splitting it.
            out = combined.group_by("ts_utc").agg(pl.col("_value").sum().alias("load_mw"))
            return (
                out.with_columns(
                    pl.lit(zone.code).alias("zone"),
                    pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                    pl.lit(self.name).alias("source"),
                )
                .select("zone", "ts_utc", "resolution_min", "load_mw", "source")
                .sort("ts_utc")
            )

        # Several upstream names share a canonical fuel, so sum after mapping.
        out = combined.group_by(["ts_utc", "fuel"]).agg(pl.col("_value").sum().alias("gen_mw"))
        return (
            out.with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "fuel", "gen_mw", "source")
            .sort(["ts_utc", "fuel"])
        )

    # --- helpers ----------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        with http_client() as client:
            payload = fetch_json(f"{_BASE}{path}", client=client, params=params)
        if not isinstance(payload, dict):
            raise UpstreamError(f"unexpected payload type {type(payload).__name__} from {path}")
        if payload.get("deprecated"):
            # Surfaced as a warning rather than a failure: the endpoint keeps
            # serving for a grace period, and a scheduled job should not break
            # on the day a deprecation flag flips.
            import logging

            logging.getLogger(__name__).warning("Energy-Charts marks %s as deprecated", path)
        return payload


def _as_date(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).date().isoformat()


def _stamp(frame: pl.DataFrame) -> pl.DataFrame:
    """Turn the ``_epoch`` seconds column into the canonical UTC timestamp."""
    return frame.with_columns(
        pl.from_epoch(pl.col("_epoch"), time_unit="s")
        .dt.replace_time_zone("UTC")
        .cast(UTC_DATETIME)
        .alias("ts_utc")
    ).drop("_epoch")


def _window(frame: pl.DataFrame, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
    """Trim to the caller's half-open window; the provider's end is inclusive."""
    return frame.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))


def _currency_from_unit(unit: str, zone: Zone) -> str:
    """Read the ISO currency out of a unit string such as ``'EUR / MWh'``."""
    head = unit.split("/")[0].strip().upper()
    return head if len(head) == 3 and head.isalpha() else zone.currency
