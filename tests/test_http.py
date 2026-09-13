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

import datetime as dt

import httpx
import polars as pl
import pytest

from gpa.sources import base
from gpa.sources.base import (
    MissingCredential,
    UpstreamError,
    fetch_json,
    fetch_text,
    infer_resolution_minutes,
    month_range,
    require_env,
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


# --- fetch_text -------------------------------------------------------------


def test_fetch_text_returns_the_body() -> None:
    with client_returning(httpx.Response(200, text="a;b;c")) as client:
        assert fetch_text("https://example.test/file.csv", client=client) == "a;b;c"


def test_a_missing_file_can_be_allowed(no_sleep: list[float]) -> None:
    """A month or year the archive has not published is expected, not an error."""
    with client_returning(httpx.Response(404)) as client:
        assert (
            fetch_text("https://example.test/2030.csv", client=client, allow_missing=True) is None
        )


def test_a_missing_file_raises_when_not_allowed() -> None:
    with (
        client_returning(httpx.Response(404)) as client,
        pytest.raises(UpstreamError, match="404"),
    ):
        fetch_text("https://example.test/2030.csv", client=client)


# --- Credentials ------------------------------------------------------------


def test_a_configured_credential_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_KEY", "abc123")
    assert require_env("DEMO_KEY", source="Demo") == "abc123"


def test_a_missing_credential_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_KEY", raising=False)
    with pytest.raises(MissingCredential, match="DEMO_KEY"):
        require_env("DEMO_KEY", source="Demo")


def test_a_blank_credential_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty secret in CI is a misconfiguration, not a valid key."""
    monkeypatch.setenv("DEMO_KEY", "   ")
    with pytest.raises(MissingCredential):
        require_env("DEMO_KEY", source="Demo")


def test_a_known_credential_includes_its_registration_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENTSOE_API_KEY", raising=False)
    with pytest.raises(MissingCredential, match=r"transparency\.entsoe\.eu"):
        require_env("ENTSOE_API_KEY", source="ENTSO-E")


# --- month_range ------------------------------------------------------------


def test_month_range_covers_both_end_months() -> None:
    months = list(
        month_range(
            dt.datetime(2026, 1, 15, tzinfo=dt.UTC),
            dt.datetime(2026, 3, 2, tzinfo=dt.UTC),
        )
    )
    assert months == [(2026, 1), (2026, 2), (2026, 3)]


def test_month_range_crosses_a_year_boundary() -> None:
    months = list(
        month_range(
            dt.datetime(2025, 11, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
        )
    )
    assert months == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_month_range_of_a_single_month_yields_one_entry() -> None:
    months = list(
        month_range(
            dt.datetime(2026, 5, 3, tzinfo=dt.UTC),
            dt.datetime(2026, 5, 28, tzinfo=dt.UTC),
        )
    )
    assert months == [(2026, 5)]


# --- Resolution inference ---------------------------------------------------


def test_resolution_snaps_to_a_supported_interval() -> None:
    """Providers publish slightly irregular stamps; the answer must still be valid."""
    stamps = pl.Series(
        "ts",
        [
            dt.datetime(2026, 1, 1, 0, 0),
            dt.datetime(2026, 1, 1, 0, 29),
            dt.datetime(2026, 1, 1, 1, 1),
            dt.datetime(2026, 1, 1, 1, 30),
        ],
    )
    assert infer_resolution_minutes(stamps) == 30


def test_resolution_ignores_ordering() -> None:
    ordered = [dt.datetime(2026, 1, 1) + dt.timedelta(hours=i) for i in range(6)]
    assert infer_resolution_minutes(pl.Series("ts", list(reversed(ordered)))) == 60


def test_resolution_accepts_a_plain_sequence() -> None:
    stamps = [dt.datetime(2026, 1, 1) + dt.timedelta(minutes=5 * i) for i in range(10)]
    assert infer_resolution_minutes(stamps) == 5
