"""Credential-free SMARD adapter for the German historical backfill.

SMARD's public chart API publishes weekly JSON files.  This adapter is kept
separate from the regular refresh source because it is intentionally slower and
specialised to DE-LU, but it gives the forecasting work a long, official price
and fundamentals history without depending on the rate-limited Energy-Charts
mirror.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

import polars as pl

from gpa.schema import empty_frame
from gpa.sources.base import UpstreamError, fetch_json, http_client
from gpa.zones import Zone

__all__ = ["SMARD_FILTERS", "SmardSource"]

_BASE = "https://www.smard.de/app/chart_data"
_REGION = "DE"
_WEEK = dt.timedelta(days=7)

SMARD_FILTERS: Final[dict[str, int]] = {
    "price": 4169,
    "load": 410,
    "lignite": 1223,
    "nuclear": 1224,
    "wind_offshore": 1225,
    "hydro": 1226,
    "other_conventional": 1227,
    "other_renewable": 1228,
    "biomass": 4066,
    "wind_onshore": 4067,
    "solar": 4068,
    "coal": 4069,
    "gas": 4071,
    "hydro_pumped_storage": 4070,
}

_FUELS: Final[dict[str, str]] = {
    key: value
    for key, value in (
        ("lignite", "coal"),
        ("nuclear", "nuclear"),
        ("wind_offshore", "wind"),
        ("hydro", "hydro"),
        ("other_conventional", "other"),
        ("other_renewable", "other"),
        ("biomass", "biomass"),
        ("wind_onshore", "wind"),
        ("solar", "solar"),
        ("coal", "coal"),
        ("gas", "gas"),
        ("hydro_pumped_storage", "hydro_pumped_storage"),
    )
}


class SmardSource:
    """Fetch official German market data from SMARD's chart endpoint."""

    name = "smard"
    datasets: tuple[str, ...] = ("price", "load", "generation")
    max_window_days: int | None = 7

    def fetch(self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        if zone.code != "DE-LU":
            raise UpstreamError("SMARD historical adapter currently supports DE-LU only")
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")
        resolution = "hour" if dataset == "price" else "quarterhour"
        if dataset == "price":
            return self._fetch_price(start, end, resolution)

        frames: list[pl.DataFrame] = []
        for series, filter_id in SMARD_FILTERS.items():
            if series == "price":
                continue
            values = self._series(filter_id, start, end, resolution)
            if values.is_empty():
                continue
            if dataset == "load" and series == "load":
                frames.append(values.with_columns((pl.col("value") * 4.0).alias("load_mw")))
            elif dataset == "generation" and series in _FUELS:
                frames.append(
                    values.with_columns(
                        pl.lit(_FUELS[series]).alias("fuel"),
                        (pl.col("value") * 4.0).alias("gen_mw"),
                    )
                )
        if not frames:
            return empty_frame(dataset)
        if dataset == "load":
            return (
                pl.concat(frames, how="vertical_relaxed")
                .select("ts_utc", "load_mw")
                .unique("ts_utc", keep="last")
                .with_columns(
                    pl.lit(zone.code).alias("zone"),
                    pl.lit(15).cast(pl.Int16).alias("resolution_min"),
                    pl.lit(self.name).alias("source"),
                )
                .select("zone", "ts_utc", "resolution_min", "load_mw", "source")
                .sort("ts_utc")
            )
        return (
            pl.concat(frames, how="vertical_relaxed")
            .select("ts_utc", "fuel", "gen_mw")
            .unique(["ts_utc", "fuel"], keep="last")
            .with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(15).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "fuel", "gen_mw", "source")
            .sort(["ts_utc", "fuel"])
        )

    def _fetch_price(self, start: dt.datetime, end: dt.datetime, resolution: str) -> pl.DataFrame:
        values = self._series(SMARD_FILTERS["price"], start, end, resolution)
        if values.is_empty():
            return empty_frame("price")
        return (
            values.with_columns(
                pl.lit("DE-LU").alias("zone"),
                pl.lit(60).cast(pl.Int16).alias("resolution_min"),
                pl.lit("EUR").alias("currency"),
                pl.lit(self.name).alias("source"),
            )
            .rename({"value": "price"})
            .select("zone", "ts_utc", "resolution_min", "price", "currency", "source")
            .sort("ts_utc")
        )

    def _series(
        self, filter_id: int, start: dt.datetime, end: dt.datetime, resolution: str
    ) -> pl.DataFrame:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("SMARD fetch bounds must be timezone-aware")
        start_ms = int(start.astimezone(dt.UTC).timestamp() * 1000)
        end_ms = int(end.astimezone(dt.UTC).timestamp() * 1000)
        with http_client() as client:
            index = fetch_json(
                f"{_BASE}/{filter_id}/{_REGION}/index_{resolution}.json", client=client
            )
            timestamps = index.get("timestamps", []) if isinstance(index, dict) else []
            if not isinstance(timestamps, list):
                raise UpstreamError("SMARD index did not contain a timestamp list")
            chunks = [
                int(timestamp)
                for timestamp in timestamps
                if int(timestamp) < end_ms
                and int(timestamp) + int(_WEEK.total_seconds() * 1000) > start_ms
            ]
            rows: list[tuple[int, float]] = []
            for timestamp in chunks:
                payload = fetch_json(
                    f"{_BASE}/{filter_id}/{_REGION}/{filter_id}_{_REGION}_{resolution}_{timestamp}.json",
                    client=client,
                )
                rows.extend(_parse_series(payload))
        if not rows:
            return pl.DataFrame(schema={"ts_utc": pl.Datetime("us", "UTC"), "value": pl.Float64})
        return (
            pl.DataFrame(
                rows,
                schema={"_epoch_ms": pl.Int64, "value": pl.Float64},
                orient="row",
            )
            .with_columns(
                pl.from_epoch(pl.col("_epoch_ms"), time_unit="ms")
                .dt.replace_time_zone("UTC")
                .cast(pl.Datetime("us", "UTC"))
                .alias("ts_utc")
            )
            .drop("_epoch_ms")
            .filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
            .unique("ts_utc", keep="last")
            .sort("ts_utc")
        )


def _parse_series(payload: Any) -> list[tuple[int, float]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        raise UpstreamError("SMARD time-series response did not contain a series list")
    rows: list[tuple[int, float]] = []
    for item in payload["series"]:
        value = item.get("value") if isinstance(item, dict) else item
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            continue
        timestamp, observation = value
        if timestamp is None or observation is None:
            continue
        rows.append((int(timestamp), float(observation)))
    return rows
