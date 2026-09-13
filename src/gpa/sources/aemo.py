"""AEMO adapter for the Australian National Electricity Market.

AEMO publishes an aggregated price and demand archive as one CSV per region per
calendar month, with no credentials and several years of history. This is the
market operator's own file, not a redistribution, which is why it is preferred
here over third-party mirrors.

Two NEM conventions drive the implementation and both are easy to get wrong:

Settlement timestamps are interval-*ending*.
    A row stamped 00:05 describes the interval from 00:00 to 00:05. This
    project stores interval-*starting* instants, so the resolution is subtracted
    from every timestamp. Skipping this shifts the entire series forward by one
    interval, which quietly misaligns price against generation and moves the
    evening peak.

Market time is AEST all year.
    AEMO settles every NEM region on Australian Eastern Standard Time, UTC+10,
    and never applies daylight saving, even for regions whose civil clocks do.
    ``Australia/Brisbane`` is therefore the correct market timezone for New
    South Wales, and the zone registry records ``Australia/Sydney`` separately
    as the civil zone.

Resolution changed in October 2021, when the NEM moved from 30-minute to
5-minute settlement. It is measured per file rather than assumed, so a backfill
spanning the transition stays correct on both sides of it.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Final

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import (
    UpstreamError,
    fetch_text,
    http_client,
    infer_resolution_minutes,
    month_range,
)
from gpa.zones import Zone

__all__ = ["NEM_MARKET_TIMEZONE", "NEM_REGIONS", "AemoSource"]

_ARCHIVE = (
    # The www host is used directly; the bare apex redirects there, and a
    # backfill issues one request per region-month, so skipping the redirect
    # halves the round trips.
    "https://www.aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{month}_{region}.csv"
)

NEM_MARKET_TIMEZONE: Final = "Australia/Brisbane"
"""AEST year-round. Brisbane is used because Queensland never observes DST."""

NEM_REGIONS: Final[frozenset[str]] = frozenset({"NSW1", "QLD1", "SA1", "TAS1", "VIC1"})

_REQUIRED = ("REGION", "SETTLEMENTDATE", "TOTALDEMAND", "RRP")


class AemoSource:
    """Fetch regional reference price and total demand for a NEM region."""

    name: str = "aemo"
    datasets: tuple[str, ...] = ("price", "load")
    # The archive is one whole file per region-month, so a longer window costs
    # no extra requests and there is nothing to cap.
    max_window_days: int | None = None

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")

        region = zone.source_keys.get("aemo_region", "").strip().upper()
        if region not in NEM_REGIONS:
            raise UpstreamError(
                f"zone {zone.code} has aemo_region={region!r}; "
                f"expected one of {sorted(NEM_REGIONS)}"
            )

        frames: list[pl.DataFrame] = []
        with http_client() as client:
            for year, month in month_range(start, end):
                url = _ARCHIVE.format(month=f"{year}{month:02d}", region=region)
                raw = fetch_text(url, client=client, allow_missing=True)
                if raw is None:
                    # A month outside the archive's coverage, which is expected
                    # at both ends of a backfill window.
                    continue
                parsed = _parse_archive(raw)
                if not parsed.is_empty():
                    frames.append(parsed)

        if not frames:
            return empty_frame(dataset)

        combined = (
            pl.concat(frames, how="vertical").unique(subset=["ts_utc"], keep="last").sort("ts_utc")
        )
        combined = combined.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
        if combined.is_empty():
            return empty_frame(dataset)

        resolution = infer_resolution_minutes(combined["ts_utc"])

        if dataset == "price":
            out = combined.select("ts_utc", pl.col("RRP").alias("price")).drop_nulls("price")
            return out.with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(zone.currency).alias("currency"),
                pl.lit(self.name).alias("source"),
            ).select("zone", "ts_utc", "resolution_min", "price", "currency", "source")

        out = combined.select("ts_utc", pl.col("TOTALDEMAND").alias("load_mw")).drop_nulls(
            "load_mw"
        )
        return out.with_columns(
            pl.lit(zone.code).alias("zone"),
            pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
            pl.lit(self.name).alias("source"),
        ).select("zone", "ts_utc", "resolution_min", "load_mw", "source")


def _parse_archive(raw: str) -> pl.DataFrame:
    """Parse one monthly archive file into UTC interval-starting rows.

    Exposed for tests, which run it against a recorded fixture covering both the
    30-minute and 5-minute settlement eras.
    """
    frame = pl.read_csv(io.StringIO(raw), infer_schema_length=0)

    missing = set(_REQUIRED) - set(frame.columns)
    if missing:
        raise UpstreamError(
            f"AEMO archive is missing expected columns: {sorted(missing)}. "
            f"Columns present: {frame.columns}"
        )

    frame = frame.select(
        pl.col("SETTLEMENTDATE")
        .str.strip_chars()
        .str.to_datetime(format="%Y/%m/%d %H:%M:%S", strict=False)
        .alias("_ending_local"),
        pl.col("RRP").str.strip_chars().cast(pl.Float64, strict=False),
        pl.col("TOTALDEMAND").str.strip_chars().cast(pl.Float64, strict=False),
    ).drop_nulls("_ending_local")

    if frame.is_empty():
        return frame.select(
            pl.lit(None).cast(UTC_DATETIME).alias("ts_utc"),
            pl.lit(None).cast(pl.Float64).alias("RRP"),
            pl.lit(None).cast(pl.Float64).alias("TOTALDEMAND"),
        )

    # Measure the interval on the raw ending stamps, then shift back by it to
    # obtain the interval start. AEST never shifts, so plain arithmetic on the
    # naive local stamps is safe before attaching the timezone.
    minutes = infer_resolution_minutes(
        frame["_ending_local"].dt.replace_time_zone("UTC"), default=5
    )

    return frame.select(
        (pl.col("_ending_local") - pl.duration(minutes=minutes))
        .dt.replace_time_zone(NEM_MARKET_TIMEZONE)
        .dt.convert_time_zone("UTC")
        .cast(UTC_DATETIME)
        .alias("ts_utc"),
        pl.col("RRP"),
        pl.col("TOTALDEMAND"),
    )
