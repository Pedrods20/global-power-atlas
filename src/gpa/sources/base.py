"""The adapter contract and a patient HTTP client.

An adapter fetches bytes, turns the provider's timestamps into UTC interval starts
and maps its fuel names onto :data:`gpa.schema.FUELS`; it never analyses or writes.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Protocol, runtime_checkable

import httpx
import polars as pl

from gpa.zones import Zone

__all__ = [
    "USER_AGENT",
    "Source",
    "SourceError",
    "UpstreamError",
    "fetch_json",
    "http_client",
]

log = logging.getLogger(__name__)

USER_AGENT = "german-power-research/0.1 (+https://github.com/Pedrods20/german-power-research)"

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 6
_BACKOFF_BASE_SECONDS = 1.5
_THROTTLE_BACKOFF_SECONDS = 20.0
"""Base delay after a 429: limits are per minute, so a few seconds only burns retries."""

_MAX_BACKOFF_SECONDS = 120.0


class SourceError(RuntimeError):
    """Any adapter failure."""


class UpstreamError(SourceError):
    """The provider returned something unusable after retries."""


@runtime_checkable
class Source(Protocol):
    """What an adapter provides; ``name`` matches the values in ``Zone.sources``."""

    name: str
    datasets: tuple[str, ...]
    max_window_days: int | None
    """The longest window the provider serves reliably in one request; ``None`` for no cap."""

    def fetch(self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        """Canonical rows over ``[start, end)``; empty is a normal outcome, not an error."""
        ...


def http_client(**kwargs: Any) -> httpx.Client:
    """A client with explicit timeouts: the default of none turns a slow provider into a hung job."""
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
    try:
        return float(header)
    except ValueError:  # Absent, or an HTTP date: the throttle schedule is close enough.
        return _THROTTLE_BACKOFF_SECONDS * attempt


def fetch_json(url: str, *, client: httpx.Client | None = None, **kwargs: Any) -> Any:
    """GET and decode JSON, retrying transient failures; anything else is an ``UpstreamError``."""
    response = _request("GET", url, client=client, **kwargs)
    if response.status_code >= 400:
        raise UpstreamError(f"{response.status_code} from {url}: {response.text[:200]}")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(f"non-JSON body from {url}: {response.text[:200]}") from exc
