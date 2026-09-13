"""EIA adapter for United States balancing authorities and ISO/RTOs.

The US Energy Information Administration's v2 API serves hourly demand and
hourly generation by fuel type for every balancing authority in the country
through one endpoint family, which is why a single adapter covers ERCOT, PJM,
CAISO, MISO, NYISO, ISO-NE and SPP. It requires a free API key, requested at
https://www.eia.gov/opendata/register.php and issued immediately by email.

Three things about this API shape the implementation:

Periods are UTC when ``frequency=hourly``.
    EIA also offers ``local-hourly``, which this project deliberately does not
    use. Taking UTC and converting through the zone's IANA timezone keeps one
    conversion path for every market instead of trusting each provider's idea
    of local time.

Results are paginated at 5000 rows.
    A month of hourly fuel-type data for one respondent exceeds that, so
    requests page until the reported total is reached.

Demand is published in megawatthours per hourly period.
    Over a 60-minute interval that is numerically identical to average
    megawatts, and this project stores megawatts. The conversion is explicit
    rather than implicit, so it stays correct if EIA ever publishes a
    sub-hourly frequency.

Note that EIA revises these series for days after first publication. The store
upserts on ``(zone, ts_utc)``, so re-running a window picks up restatements
instead of duplicating them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import UpstreamError, fetch_json, http_client, require_env
from gpa.zones import Zone

__all__ = ["FUEL_MAP", "EiaSource"]

_BASE = "https://api.eia.gov/v2/electricity/rto"
_PAGE_SIZE = 5000
_MAX_PAGES = 200

FUEL_MAP: Final[dict[str, str]] = {
    "COL": "coal",
    "NG": "gas",
    "OIL": "oil",
    "NUC": "nuclear",
    "WAT": "hydro",
    "PS": "hydro_pumped_storage",
    "WND": "wind",
    "SUN": "solar",
    "GEO": "geothermal",
    "BIO": "biomass",
    "OTH": "other",
    "BAT": "battery",
    "UNK": "other",
}
"""EIA fuel type code to canonical fuel.

``PS`` is pumped storage and is kept out of hydro. ``SUN`` covers utility-scale
solar only; EIA's hourly fuel-type series does not include behind-the-meter
rooftop output, which matters when comparing a US solar share against a market
whose operator does report rooftop, and the methodology page says so.
"""


class EiaSource:
    """Fetch hourly load and generation by fuel for a US balancing authority."""

    name: str = "eia"
    datasets: tuple[str, ...] = ("load", "generation")
    # Paginated rather than capped, but a year at a time keeps any single
    # failure small enough to retry cheaply.
    max_window_days: int | None = 365

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")

        respondent = zone.source_keys.get("eia_respondent", "").strip().upper()
        if not respondent:
            raise UpstreamError(f"zone {zone.code} has no 'eia_respondent' in source_keys")

        api_key = require_env("EIA_API_KEY", source="EIA")

        if dataset == "load":
            route, extra = "region-data", {"facets[type][]": "D"}
        else:
            route, extra = "fuel-type-data", {}

        records = self._paginate(route, respondent, api_key, start, end, extra)
        if not records:
            return empty_frame(dataset)

        frame = pl.DataFrame(records, infer_schema_length=None)
        if "period" not in frame.columns or "value" not in frame.columns:
            raise UpstreamError(
                f"EIA {route} response is missing 'period' or 'value'. "
                f"Columns present: {frame.columns}"
            )

        frame = frame.select(
            # EIA stamps an hourly period as 'YYYY-MM-DDTHH' in UTC, marking the
            # start of the hour.
            pl.col("period")
            .cast(pl.String)
            .str.replace(r"^(\d{4}-\d{2}-\d{2}T\d{2})$", "${1}:00")
            .str.to_datetime(format="%Y-%m-%dT%H:%M", strict=False)
            .dt.replace_time_zone("UTC")
            .cast(UTC_DATETIME)
            .alias("ts_utc"),
            pl.col("value").cast(pl.Float64, strict=False).alias("_value"),
            *(
                [pl.col("fueltype").cast(pl.String).alias("_fueltype")]
                if dataset == "generation"
                else []
            ),
        ).drop_nulls(["ts_utc", "_value"])

        frame = frame.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
        if frame.is_empty():
            return empty_frame(dataset)

        # Hourly MWh over a 60-minute interval equals average MW.
        resolution = 60
        to_mw = pl.col("_value") * (60.0 / resolution)

        if dataset == "load":
            out = (
                frame.group_by("ts_utc")
                .agg(to_mw.sum().alias("load_mw"))
                .with_columns(
                    pl.lit(zone.code).alias("zone"),
                    pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                    pl.lit(self.name).alias("source"),
                )
            )
            return out.select("zone", "ts_utc", "resolution_min", "load_mw", "source").sort(
                "ts_utc"
            )

        out = (
            frame.with_columns(
                pl.col("_fueltype")
                .str.strip_chars()
                .str.to_uppercase()
                .replace_strict(FUEL_MAP, default="other")
                .alias("fuel")
            )
            .group_by(["ts_utc", "fuel"])
            .agg(to_mw.sum().alias("gen_mw"))
            .with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            )
        )
        return out.select("zone", "ts_utc", "resolution_min", "fuel", "gen_mw", "source").sort(
            ["ts_utc", "fuel"]
        )

    def _paginate(
        self,
        route: str,
        respondent: str,
        api_key: str,
        start: dt.datetime,
        end: dt.datetime,
        extra: dict[str, str],
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "api_key": api_key,
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": respondent,
            "start": start.astimezone(dt.UTC).strftime("%Y-%m-%dT%H"),
            "end": end.astimezone(dt.UTC).strftime("%Y-%m-%dT%H"),
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": _PAGE_SIZE,
            **extra,
        }

        records: list[dict[str, Any]] = []
        with http_client() as client:
            for page in range(_MAX_PAGES):
                payload = fetch_json(
                    f"{_BASE}/{route}/data/",
                    client=client,
                    params={**params, "offset": page * _PAGE_SIZE},
                )
                response = (payload or {}).get("response") or {}
                batch = response.get("data") or []
                records.extend(batch)

                total = int(response.get("total") or 0)
                if len(batch) < _PAGE_SIZE or len(records) >= total:
                    break
            else:
                raise UpstreamError(
                    f"EIA {route} exceeded {_MAX_PAGES} pages; narrow the request window"
                )

        return records
