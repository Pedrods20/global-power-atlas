"""CAISO OASIS adapter for Californian day-ahead locational marginal prices.

OASIS is the only one of the three large US markets whose price is reachable
without a credential. ERCOT returns 403 to automated clients across every host
it publishes on, and PJM's Data Miner requires a registered subscription key,
so both remain unimplemented and are recorded as such in ``TODO.md``. EIA-930,
which already supplies US load and generation here, publishes no price at all.

Three properties of this API drive the implementation:

Responses are zipped CSV.
    ``resultformat=6`` returns a single-entry zip archive. The archive name
    carries the query and version, which is useful when debugging but is not
    relied upon.

Rate limiting arrives as **200 OK with an HTML body**.
    Exceeding the acceptable-use policy does not produce 429. It produces a
    successful-looking response whose body is an HTML paragraph asking for a
    five-second pause. The shared retry layer cannot see that, so this adapter
    detects it directly and paces its own requests. Treating it as data would
    silently write an empty month.

An empty result is an XML document inside the zip, not an empty CSV.
    When a window has no data, OASIS still answers 200 with a valid archive,
    but the entry is an ``OASISReport`` XML carrying ``ERR_CODE 1000, No data
    returned for the specified selection``. Feeding that to a CSV parser
    produces a frame whose only column is ``<?xml version="1.0"...``, which is
    how this first showed up. It is a normal outcome, not a failure: a
    day-ahead window that runs past the last published session has nothing in
    it by definition.

Windows are capped at 31 days, counted as calendar days touched.
    OASIS rejects a longer one with ``ERR_CODE 1004``. The cap here is 30, not
    31, because a backfill starting at an arbitrary time of day produces a
    31-day window that spans 32 distinct calendar days, and OASIS counts those
    rather than the elapsed duration. Thirty always fits.

Price basis: the day-ahead market, so that California is comparable with the
European and Japanese day-ahead series already stored here rather than against
a real-time index.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import time
import zipfile
from typing import Final

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import USER_AGENT, UpstreamError, http_client
from gpa.zones import Zone

__all__ = ["LMP_COMPONENTS", "CaisoSource", "parse_oasis_lmp"]

_BASE: Final = "https://oasis.caiso.com/oasisapi/SingleZip"

_THROTTLE_MARKER: Final = "Acceptable Use Policy"
"""Text CAISO returns, inside a 200 response, when requests come too fast."""

_MIN_REQUEST_INTERVAL: Final = 6.0
"""Seconds between requests.

CAISO asks for five. Six leaves margin, and a two-year backfill is 24 requests,
so the pacing costs well under three minutes in total.
"""

_MAX_THROTTLE_RETRIES: Final = 5

LMP_COMPONENTS: Final[dict[str, str]] = {
    "LMP": "total",
    "MCE": "energy",
    "MCC": "congestion",
    "MCL": "loss",
    "MGHG": "greenhouse_gas",
}
"""OASIS component code to a readable name.

A Californian LMP decomposes into energy, congestion, loss and a greenhouse-gas
term that exists because California prices carbon into dispatch through its
cap-and-trade programme. Only the total is stored today, since the canonical
price table holds one number per interval, but the decomposition is what a
congestion or basis analysis would need and the codes are recorded here so that
work does not have to rediscover them.
"""

_PRICE_COMPONENT: Final = "LMP"

_NO_DATA_ERR_CODE: Final = "1000"
"""OASIS error code meaning the selection matched nothing.

Distinguished from every other code because it is an ordinary result, not a
fault: a window past the last published day-ahead session simply has no rows.
"""

_REQUIRED_COLUMNS: Final = (
    "INTERVALSTARTTIME_GMT",
    "LMP_TYPE",
    "MW",
    "NODE",
)


class CaisoSource:
    """Fetch day-ahead hub prices for a CAISO zone."""

    name: str = "caiso"
    datasets: tuple[str, ...] = ("price",)
    # OASIS caps a request at 31 calendar days touched, not 31 elapsed days, so
    # a window starting mid-afternoon spans 32 and is rejected. Thirty fits from
    # any starting time.
    max_window_days: int | None = 30

    def __init__(self) -> None:
        self._last_request: float = 0.0

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset != "price":
            raise ValueError("CAISO OASIS supplies price only; load and generation come from EIA")

        node = zone.source_keys.get("caiso_node", "").strip()
        if not node:
            raise UpstreamError(f"zone {zone.code} has no 'caiso_node' in source_keys")

        market = zone.source_keys.get("caiso_market", "DAM").strip().upper()
        raw = self._download(node, market, start, end)
        if raw is None:
            return empty_frame("price")

        frame = parse_oasis_lmp(raw, zone.code, node)
        frame = frame.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
        return frame if not frame.is_empty() else empty_frame("price")

    def _download(self, node: str, market: str, start: dt.datetime, end: dt.datetime) -> str | None:
        """Fetch one window, pacing requests and retrying an HTML throttle reply."""
        params = {
            "queryname": "PRC_LMP",
            "version": "1",
            "resultformat": "6",
            "market_run_id": market,
            "node": node,
            "startdatetime": _oasis_stamp(start),
            "enddatetime": _oasis_stamp(end),
        }

        with http_client(headers={"User-Agent": USER_AGENT}) as client:
            for attempt in range(1, _MAX_THROTTLE_RETRIES + 1):
                self._pace()
                response = client.get(_BASE, params=params)

                if response.status_code >= 400:
                    raise UpstreamError(
                        f"{response.status_code} from CAISO OASIS for {node}: {response.text[:200]}"
                    )

                body = response.content
                if body[:2] == b"PK":
                    return _unzip(body)

                # A 200 carrying HTML is the throttle reply, not data.
                text = response.text
                if _THROTTLE_MARKER in text:
                    wait = _MIN_REQUEST_INTERVAL * attempt
                    time.sleep(wait)
                    continue

                raise UpstreamError(f"CAISO OASIS returned a non-zip body for {node}: {text[:200]}")

        raise UpstreamError(
            f"CAISO OASIS kept throttling {node} after {_MAX_THROTTLE_RETRIES} attempts"
        )

    def _pace(self) -> None:
        """Wait out the remainder of the minimum interval since the last call."""
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request = time.monotonic()


def _oasis_stamp(moment: dt.datetime) -> str:
    """Render an instant the way OASIS expects it, as UTC with an explicit offset."""
    return moment.astimezone(dt.UTC).strftime("%Y%m%dT%H:%M-0000")


def _unzip(payload: bytes) -> str | None:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = archive.namelist()
    if not names:
        return None
    return archive.read(names[0]).decode("utf-8", errors="replace")


def _read_oasis_error(raw: str) -> tuple[str | None, str | None]:
    """Pull the error code and description out of an OASISReport XML body.

    Parsed with a narrow regular expression rather than an XML parser: the only
    thing needed is two leaf values from a namespaced document, and the document
    is never trusted for anything else.
    """
    code = re.search(r"<m:ERR_CODE>([^<]*)</m:ERR_CODE>", raw)
    description = re.search(r"<m:ERR_DESC>([^<]*)</m:ERR_DESC>", raw)
    return (
        code.group(1).strip() if code else None,
        description.group(1).strip() if description else None,
    )


def parse_oasis_lmp(raw: str, zone_code: str, node: str) -> pl.DataFrame:
    """Parse an OASIS PRC_LMP CSV into canonical price rows.

    Keeps only the ``LMP`` component. The file interleaves five components for
    every interval, so taking every row would store the congestion and loss
    terms as though each were a price.

    Exposed for tests, which run it against a recorded fixture rather than the
    live API.
    """
    if raw.lstrip().startswith("<?xml"):
        code, description = _read_oasis_error(raw)
        if code == _NO_DATA_ERR_CODE:
            return empty_frame("price")
        raise UpstreamError(
            f"CAISO OASIS returned error {code or 'unknown'} for {node}: {description or raw[:200]}"
        )

    frame = pl.read_csv(io.StringIO(raw), infer_schema_length=0)

    missing = set(_REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise UpstreamError(
            f"CAISO OASIS CSV is missing expected columns: {sorted(missing)}. "
            f"Columns present: {frame.columns}"
        )

    frame = frame.filter(pl.col("LMP_TYPE").str.strip_chars() == _PRICE_COMPONENT)
    if frame.is_empty():
        return empty_frame("price")

    parsed = frame.select(
        # Already UTC and interval-starting, so only the offset needs stripping.
        pl.col("INTERVALSTARTTIME_GMT")
        .str.strip_chars()
        .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%:z", strict=False)
        .dt.convert_time_zone("UTC")
        .cast(UTC_DATETIME)
        .alias("ts_utc"),
        pl.col("MW").cast(pl.Float64, strict=False).alias("price"),
    ).drop_nulls(["ts_utc", "price"])

    if parsed.is_empty():
        return empty_frame("price")

    # OASIS returns rows unordered and occasionally repeats one on a window
    # boundary, so deduplicate before the store's own upsert sees them.
    parsed = parsed.unique(subset=["ts_utc"], keep="last").sort("ts_utc")

    return parsed.with_columns(
        pl.lit(zone_code).alias("zone"),
        pl.lit(60, dtype=pl.Int16).alias("resolution_min"),
        pl.lit("USD").alias("currency"),
        pl.lit("caiso").alias("source"),
    ).select("zone", "ts_utc", "resolution_min", "price", "currency", "source")
