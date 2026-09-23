"""HTTP layer tests: retries, throttling and the shared helpers.

``sources/base.py`` is not plumbing. It decides how long to wait after a 429,
how many times to retry a flaky provider, and when to give up, and every
adapter inherits that behaviour. A two-year Energy-Charts backfill fails
outright without it, which is exactly what happened before the throttle
handling was added.

These run against an ``httpx.MockTransport`` rather than the network, and sleep
is patched out so the suite stays fast.
"""

from __future__ import annotations

import httpx
import pytest

from gpa.sources import base
from gpa.sources.base import (
    UpstreamError,
    fetch_json,
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of waiting them out."""
    slept: list[float] = []
    monkeypatch.setattr(base.time, "sleep", slept.append)
    return slept


def client_returning(*responses: httpx.Response) -> httpx.Client:
    """A client that answers with each response in turn, repeating the last."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Retry behaviour --------------------------------------------------------


def test_a_successful_response_is_returned_without_retrying(no_sleep: list[float]) -> None:
    with client_returning(httpx.Response(200, json={"ok": True})) as client:
        assert fetch_json("https://example.test/data", client=client) == {"ok": True}
    assert no_sleep == []


def test_a_transient_server_error_is_retried_then_succeeds(no_sleep: list[float]) -> None:
    with client_returning(
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ) as client:
        assert fetch_json("https://example.test/data", client=client) == {"ok": True}
    assert len(no_sleep) == 1


def test_retries_are_exhausted_and_then_raise(no_sleep: list[float]) -> None:
    with (
        client_returning(httpx.Response(500)) as client,
        pytest.raises(UpstreamError, match="after"),
    ):
        fetch_json("https://example.test/data", client=client)

    assert len(no_sleep) == base._MAX_ATTEMPTS - 1


def test_a_client_error_is_not_retried(no_sleep: list[float]) -> None:
    """A 400 means the request was wrong; repeating it cannot help."""
    with (
        client_returning(httpx.Response(400, text="bad request")) as client,
        pytest.raises(UpstreamError, match="400"),
    ):
        fetch_json("https://example.test/data", client=client)

    assert no_sleep == []


def test_a_non_json_body_raises_rather_than_returning_garbage() -> None:
    with (
        client_returning(httpx.Response(200, text="<html>maintenance</html>")) as client,
        pytest.raises(UpstreamError, match="non-JSON"),
    ):
        fetch_json("https://example.test/data", client=client)


# --- Throttling -------------------------------------------------------------


def test_a_429_waits_far_longer_than_an_ordinary_error(no_sleep: list[float]) -> None:
    """Provider rate limits are per-minute, so a two-second backoff is useless.

    Energy-Charts throttles a long backfill hard and recovers in about a
    minute, which is why 429 gets its own much longer schedule.
    """
    with client_returning(httpx.Response(429), httpx.Response(200, json={})) as throttled:
        fetch_json("https://example.test/data", client=throttled)
    throttle_delay = no_sleep[0]

    no_sleep.clear()
    with client_returning(httpx.Response(503), httpx.Response(200, json={})) as flaky:
        fetch_json("https://example.test/data", client=flaky)
    ordinary_delay = no_sleep[0]

    assert throttle_delay > ordinary_delay * 5


def test_a_retry_after_header_is_honoured(no_sleep: list[float]) -> None:
    """The server's own instruction beats any schedule we invent."""
    with client_returning(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={}),
    ) as client:
        fetch_json("https://example.test/data", client=client)

    assert no_sleep[0] == pytest.approx(7.0)


def test_an_http_date_retry_after_falls_back_to_the_schedule(no_sleep: list[float]) -> None:
    """The header may carry a date; that must not crash the run."""
    with client_returning(
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        httpx.Response(200, json={}),
    ) as client:
        fetch_json("https://example.test/data", client=client)

    assert no_sleep[0] > 0


def test_backoff_is_capped(no_sleep: list[float]) -> None:
    """An unbounded exponential would stall a scheduled job for hours."""
    with client_returning(httpx.Response(429)) as client, pytest.raises(UpstreamError):
        fetch_json("https://example.test/data", client=client)

    assert max(no_sleep) <= base._MAX_BACKOFF_SECONDS
