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
"""Base delay after a 429.

Rate limits are per-minute on the providers used here, so backing off in
seconds is pointless: a backfill that issues a dozen requests in a row has to
wait out the window. Energy-Charts in particular throttles a long backfill hard
and recovers within about a minute.
"""

_MAX_BACKOFF_SECONDS = 120.0


class SourceError(RuntimeError):
    """Base class for every adapter failure."""


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

    Energy-Charts accepts a long window but times out serving it, so the cap is
    declared here and the pipeline sizes its chunks from it.

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
