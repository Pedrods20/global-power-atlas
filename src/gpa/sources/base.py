"""Shared plumbing for source adapters.

Every adapter implements :class:`Source` and returns frames in the canonical
shape defined by :mod:`gpa.schema`. Adapters are responsible for three things
and nothing else:

1. Fetching bytes from an upstream provider.
2. Translating that provider's idea of a timestamp into a UTC instant marking
   the *start* of a settlement interval.
3. Mapping the provider's fuel vocabulary onto :data:`gpa.schema.FUELS`.

Analysis never happens in an adapter, and an adapter never writes to disk.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from collections.abc import Iterator, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx
import polars as pl

from gpa.schema import UTC_DATETIME
from gpa.zones import Zone

__all__ = [
    "USER_AGENT",
    "MissingCredential",
    "Source",
    "SourceError",
    "UpstreamError",
    "fetch_json",
    "fetch_text",
    "http_client",
    "infer_resolution_minutes",
    "month_range",
    "require_env",
]

log = logging.getLogger(__name__)

USER_AGENT = "global-power-atlas/0.1 (+https://github.com/Pedrods20/global-power-atlas)"

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 6
_BACKOFF_BASE_SECONDS = 1.5
_THROTTLE_BACKOFF_SECONDS = 20.0
"""Base delay after a 429.

Rate limits are per-minute on the providers used here, so backing off in
seconds is pointless: a backfill that issues a dozen requests in a row has to
wait out the window. Energy-Charts in particular throttles a long backfill hard
and recovers within about a minute.
"""

_MAX_BACKOFF_SECONDS = 120.0


class SourceError(RuntimeError):
    """Base class for every adapter failure."""


class MissingCredential(SourceError):
    """A required API key is not configured.

    Raised rather than returning empty so that a misconfigured run is loud. The
    CLI catches it and skips the zone with a warning, which keeps the keyless
    sources working while a key is being obtained.
    """


class UpstreamError(SourceError):
    """The provider returned something unusable after retries."""


@runtime_checkable
class Source(Protocol):
    """The contract every adapter satisfies."""

    name: str
    """Stable identifier, matching the values in ``Zone.sources``."""

    datasets: tuple[str, ...]
    """Datasets this adapter can produce."""

    max_window_days: int | None
    """Largest window the provider will serve in one request, if it caps one.

    Providers differ sharply here and the limits are not documented in one
    place. OpenElectricity rejects anything over 32 days at hourly resolution
    outright; Energy-Charts accepts a long window but times out serving it;
    AEMO and ONS publish whole files per month and per year, so a longer window
    costs no extra requests at all. Declaring the cap here lets the pipeline
    size its chunks per source instead of guessing one number for everything.

    ``None`` means the adapter imposes no limit of its own.
    """

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        """Return canonical rows for ``dataset`` over ``[start, end)``.

        Args:
            zone: The zone to fetch, supplying upstream identifiers via
                ``zone.source_keys``.
            dataset: One of ``self.datasets``.
            start: Inclusive UTC-aware lower bound.
            end: Exclusive UTC-aware upper bound.

        Returns:
            A frame matching the dataset's schema. Empty if the provider has no
            data for the window, which is a normal outcome and not an error.
        """
        ...


def require_env(name: str, *, source: str) -> str:
    """Read a required environment variable.

    Raises:
        MissingCredential: If unset or blank, with the registration URL when
            one is known.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        hint = _REGISTRATION_HINTS.get(name, "")
        suffix = f" Register at {hint}." if hint else ""
        raise MissingCredential(f"{source} requires the {name} environment variable.{suffix}")
    return value


_REGISTRATION_HINTS = {
    "EIA_API_KEY": "https://www.eia.gov/opendata/register.php",
    "ENTSOE_API_KEY": "https://transparency.entsoe.eu/ (request the token by email)",
    "OPENELECTRICITY_API_KEY": "https://platform.openelectricity.org.au/",
}


def http_client(**kwargs: Any) -> httpx.Client:
    """An HTTP client with this project's defaults.

    Timeouts are explicit because the default of no timeout turns a slow
    provider into a hung scheduled job.
    """
    kwargs.setdefault("timeout", httpx.Timeout(30.0, connect=10.0, read=120.0))
    kwargs.setdefault("follow_redirects", True)
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    return httpx.Client(headers=headers, **kwargs)


def _request(
    method: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    **kwargs: Any,
) -> httpx.Response:
    owned = client is None
    con = client or http_client()
    try:
        last: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            delay = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))

            try:
                response = con.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last = exc
                log.warning(
                    "%s %s failed (attempt %d/%d): %s", method, url, attempt, _MAX_ATTEMPTS, exc
                )
            else:
                if response.status_code not in _RETRY_STATUS:
                    return response

                last = UpstreamError(f"{response.status_code} from {url}")
                if response.status_code == 429:
                    delay = _throttle_delay(response, attempt)
                log.warning(
                    "%s %s returned %d (attempt %d/%d), waiting %.0fs",
                    method,
                    url,
                    response.status_code,
                    attempt,
                    _MAX_ATTEMPTS,
                    delay,
                )

            if attempt < _MAX_ATTEMPTS:
                time.sleep(min(delay, _MAX_BACKOFF_SECONDS))

        raise UpstreamError(f"{method} {url} failed after {_MAX_ATTEMPTS} attempts") from last
    finally:
        if owned:
            con.close()


def _throttle_delay(response: httpx.Response, attempt: int) -> float:
    """How long to wait after a 429, preferring the server's own instruction."""
    header = response.headers.get("Retry-After", "").strip()
    if header:
        try:
            return float(header)
        except ValueError:
            # The header may carry an HTTP date instead of a number. Falling
            # through to the exponential schedule is close enough and avoids a
            # date parser for a rare case.
            pass
    return _THROTTLE_BACKOFF_SECONDS * attempt


def fetch_json(url: str, *, client: httpx.Client | None = None, **kwargs: Any) -> Any:
    """GET ``url`` and decode JSON, retrying transient failures.

    Raises:
        UpstreamError: On a non-retryable error status, or after retries are
            exhausted, or if the body is not JSON.
    """
    response = _request("GET", url, client=client, **kwargs)
    if response.status_code >= 400:
        raise UpstreamError(f"{response.status_code} from {url}: {response.text[:200]}")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(f"non-JSON body from {url}: {response.text[:200]}") from exc


def fetch_text(
    url: str,
    *,
    client: httpx.Client | None = None,
    allow_missing: bool = False,
    **kwargs: Any,
) -> str | None:
    """GET ``url`` as text.

    Args:
        url: Target.
        client: Reuse an open client when fetching many files.
        allow_missing: Return ``None`` on 404 instead of raising. Used where a
            missing file is expected, such as a monthly archive for a month
            that has not happened yet.

    Raises:
        UpstreamError: On an error status that is not an allowed 404.
    """
    response = _request("GET", url, client=client, **kwargs)
    if response.status_code == 404 and allow_missing:
        return None
    if response.status_code >= 400:
        raise UpstreamError(f"{response.status_code} from {url}: {response.text[:200]}")
    return response.text


def month_range(start: dt.datetime, end: dt.datetime) -> Iterator[tuple[int, int]]:
    """Yield ``(year, month)`` pairs covering ``[start, end)`` inclusive of both ends' months.

    Used by adapters whose provider publishes one file per calendar month.
    """
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def infer_resolution_minutes(
    timestamps: Sequence[dt.datetime] | pl.Series,
    *,
    default: int = 60,
    allowed: tuple[int, ...] = (1, 5, 15, 30, 60),
) -> int:
    """Infer the settlement interval from the spacing between timestamps.

    Providers change resolution without warning: Energy-Charts moved German
    data from hourly to quarter-hourly, and AEMO moved the NEM from 30-minute
    to 5-minute settlement in October 2021. Assuming an hour silently corrupts
    every MW-to-MWh conversion, so the interval is measured rather than assumed.

    The median gap is used so that a single missing observation, or a 23-hour
    daylight-saving day, does not shift the answer. The result is snapped to the
    nearest allowed resolution.

    Args:
        timestamps: At least two instants, in any order.
        default: Returned when fewer than two timestamps are available.
        allowed: Resolutions the schema accepts.

    Returns:
        The inferred interval in minutes.
    """
    series = timestamps if isinstance(timestamps, pl.Series) else pl.Series("ts", list(timestamps))
    if series.len() < 2:
        return default

    deltas = series.sort().diff().drop_nulls()
    if deltas.len() == 0:
        return default

    minutes = deltas.dt.total_seconds() / 60.0
    positive = minutes.filter(minutes > 0)
    if positive.len() == 0:
        return default

    median = float(positive.median())  # type: ignore[arg-type]
    return min(allowed, key=lambda candidate: abs(candidate - median))


def to_utc_column(expr: pl.Expr, *, from_timezone: str | None = None) -> pl.Expr:
    """Normalise a datetime expression to the project's UTC dtype.

    Args:
        expr: A datetime expression, naive or aware.
        from_timezone: IANA zone the naive values are expressed in. Required
            when ``expr`` is naive; ignored when it is already aware.
    """
    if from_timezone is not None:
        expr = expr.dt.replace_time_zone(from_timezone, ambiguous="earliest", non_existent="null")
    return expr.dt.convert_time_zone("UTC").cast(UTC_DATETIME)
