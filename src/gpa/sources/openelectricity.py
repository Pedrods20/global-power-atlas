"""OpenElectricity adapter for NEM generation by fuel technology.

OpenElectricity is the successor to OpenNEM, run by The Superpower Institute.
Its v4 API serves regional generation grouped by fuel technology without
credentials, which fills the one gap AEMO's public price-and-demand archive
leaves: that archive carries price and demand but no fuel breakdown.

Australia is therefore sourced from two providers on purpose. Price and load
come from the market operator directly, and only the fuel split comes from a
third party.

Two details of the v4 payload matter:

Battery appears three times.
    ``battery`` is net output, and ``battery_charging`` and
    ``battery_discharging`` are its two halves. Keeping all three would count
    the same megawatts three times, so only the net series is taken.

Timestamps are AEST offsets, already interval-starting.
    Unlike the AEMO archive these need no shift, only conversion to UTC.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import UpstreamError, fetch_json, http_client, infer_resolution_minutes
from gpa.zones import Zone

__all__ = ["FUELTECH_MAP", "IGNORED_FUELTECHS", "OpenElectricitySource"]

_BASE = "https://api.openelectricity.org.au/v4/data/network"

FUELTECH_MAP: Final[dict[str, str]] = {
    "coal": "coal",
    "gas": "gas",
    "distillate": "oil",
    "bioenergy": "biomass",
    "hydro": "hydro",
    "pumps": "hydro_pumped_storage",
    "wind": "wind",
    "solar": "solar",
    "battery": "battery",
    "nuclear": "nuclear",
}
"""OpenElectricity fuel technology group to canonical fuel.

``distillate`` is diesel and light fuel oil, which the NEM runs only as peaking
and emergency plant. ``pumps`` is pumped-storage pumping load, reported as
consumption, and maps to the pumped-storage bucket rather than to hydro so that
it stays out of renewable share.
"""

IGNORED_FUELTECHS: Final[frozenset[str]] = frozenset({"battery_charging", "battery_discharging"})
"""Components of the net ``battery`` series, dropped to avoid triple counting."""


class OpenElectricitySource:
    """Fetch generation by fuel for a NEM region."""

    name = "openelectricity"
    datasets = ("generation",)
    # The API rejects anything over 32 days at hourly resolution with a 400.
    # Thirty-one leaves room for the timezone shift at the window's edges.
    max_window_days = 31

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")

        region = zone.source_keys.get("opennem_region", "").strip().upper()
        network = zone.source_keys.get("opennem_network", "NEM").strip().upper()
        if not region:
            raise UpstreamError(f"zone {zone.code} has no 'opennem_region' in source_keys")

        rows = _collect(
            self._request(network, start, end),
            region=region,
        )
        if not rows:
            return empty_frame(dataset)

        frame = (
            pl.DataFrame(rows, schema={"_ts": pl.String, "fuel": pl.String, "gen_mw": pl.Float64})
            .with_columns(
                # The offset is always present and always AEST, but polars
                # refuses to infer a format when the data carries a timezone,
                # so it is stated explicitly.
                pl.col("_ts")
                .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%:z", strict=False)
                .dt.convert_time_zone("UTC")
                .cast(UTC_DATETIME)
                .alias("ts_utc")
            )
            .drop("_ts")
            .drop_nulls(["ts_utc", "gen_mw"])
            .filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
            .group_by(["ts_utc", "fuel"])
            .agg(pl.col("gen_mw").sum())
        )
        if frame.is_empty():
            return empty_frame(dataset)

        resolution = infer_resolution_minutes(frame["ts_utc"].unique())
        return (
            frame.with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "fuel", "gen_mw", "source")
            .sort(["ts_utc", "fuel"])
        )

    def _request(self, network: str, start: dt.datetime, end: dt.datetime) -> Any:
        # The API expects naive local timestamps and interprets them in network
        # time, which for the NEM is AEST.
        params = {
            "metrics": "power",
            "interval": "1h",
            "primary_grouping": "network_region",
            "secondary_grouping": "fueltech_group",
            "date_start": start.astimezone(dt.timezone(dt.timedelta(hours=10)))
            .replace(tzinfo=None)
            .isoformat(timespec="seconds"),
            "date_end": end.astimezone(dt.timezone(dt.timedelta(hours=10)))
            .replace(tzinfo=None)
            .isoformat(timespec="seconds"),
        }
        with http_client() as client:
            return fetch_json(f"{_BASE}/{network}", client=client, params=params)


def _collect(payload: Any, *, region: str) -> list[dict[str, object]]:
    """Flatten the v4 response into canonical rows.

    Exposed for tests, which run it against a recorded fixture.
    """
    if not isinstance(payload, dict):
        raise UpstreamError(f"unexpected payload type {type(payload).__name__}")
    if payload.get("success") is False:
        raise UpstreamError(f"OpenElectricity reported failure: {payload.get('error')}")

    rows: list[dict[str, object]] = []
    for block in payload.get("data") or []:
        for series in block.get("results") or []:
            columns = series.get("columns") or {}
            if str(columns.get("region", "")).upper() != region:
                continue

            group = str(columns.get("fueltech_group", ""))
            if group in IGNORED_FUELTECHS:
                continue
            fuel = FUELTECH_MAP.get(group)
            if fuel is None:
                # An unmapped technology is bucketed rather than dropped, so a
                # new fueltech shows up in the mix instead of silently vanishing.
                fuel = "other"

            for point in series.get("data") or []:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                timestamp, value = point[0], point[1]
                if value is None:
                    continue
                rows.append({"_ts": str(timestamp), "fuel": fuel, "gen_mw": float(value)})

    return rows
