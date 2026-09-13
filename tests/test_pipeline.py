"""Ingestion orchestration tests.

``pipeline.py`` is the resilience story of the daily scheduled run. It decides
what becomes skipped, failed, written or empty, and whether one bad provider
stops the rest of the world from updating. None of that was covered before, so
these tests exercise it against fake sources rather than the network.

The governing rule under test: one zone failing must never stop the others. A
scheduled run is only a failure if a target genuinely failed, and a missing
credential is not a failure.
"""

from __future__ import annotations

import datetime as dt
from itertools import pairwise
from pathlib import Path

import polars as pl
import pytest

from gpa import pipeline, store
from gpa.pipeline import Outcome
from gpa.schema import empty_frame
from gpa.sources.base import MissingCredential, UpstreamError
from gpa.zones import BR_PONTA, Region, Zone

START = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
END = dt.datetime(2026, 6, 8, tzinfo=dt.UTC)
HOURS = 7 * 24


@pytest.fixture(autouse=True)
def temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the store so no test can touch the committed repository data."""
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    return tmp_path


def load_rows(zone: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
    """A valid load frame spanning the requested window, hourly."""
    stamps = pl.datetime_range(start, end, "1h", time_zone="UTC", eager=True, closed="left")
    return pl.DataFrame(
        {
            "zone": [zone] * stamps.len(),
            "ts_utc": stamps,
            "resolution_min": [60] * stamps.len(),
            "load_mw": [1000.0 + i for i in range(stamps.len())],
            "source": ["fake"] * stamps.len(),
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "load_mw": pl.Float64,
            "source": pl.String,
        },
    )


class FakeSource:
    """A source whose behaviour each test dictates."""

    name: str = "fake"
    datasets: tuple[str, ...] = ("load",)
    max_window_days: int | None = None

    def __init__(self, behaviour: str = "ok", max_window_days: int | None = None) -> None:
        self.behaviour = behaviour
        self.max_window_days = max_window_days
        self.calls: list[tuple[dt.datetime, dt.datetime]] = []

    def fetch(self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        self.calls.append((start, end))
        if self.behaviour == "missing_credential":
            raise MissingCredential("FAKE_API_KEY is not set")
        if self.behaviour == "upstream":
            raise UpstreamError("provider returned 503")
        if self.behaviour == "boom":
            raise RuntimeError("an error nobody anticipated")
        if self.behaviour == "empty":
            return empty_frame("load")
        if self.behaviour == "bad_schema":
            return load_rows(zone.code, start, end).with_columns(
                pl.lit("not a number").alias("load_mw")
            )
        return load_rows(zone.code, start, end)


def fake_zone(code: str = "ZZ-TEST", source: str = "fake") -> Zone:
    return Zone(
        code=code,
        name=f"Test zone {code}",
        country="ZZ",
        region=Region.EUROPE,
        operator="Test",
        timezone="Europe/Berlin",
        currency="EUR",
        peak=BR_PONTA,
        sources={"load": source},
        source_keys={},
    )


def install(
    monkeypatch: pytest.MonkeyPatch,
    zones: list[Zone],
    sources: dict[str, FakeSource],
) -> None:
    """Point the registry and the zone list at the fakes for one test."""
    monkeypatch.setattr(pipeline, "ZONES", tuple(zones))
    monkeypatch.setattr(pipeline, "get_zone", {z.code: z for z in zones}.__getitem__)
    monkeypatch.setattr(pipeline, "get_source", sources.__getitem__)


# --- Outcome classification -------------------------------------------------


def test_a_successful_fetch_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    zone = fake_zone()
    install(monkeypatch, [zone], {"fake": FakeSource("ok")})

    results = pipeline.ingest(start=START, end=END)

    assert len(results) == 1
    assert results[0].outcome is Outcome.WRITTEN
    assert results[0].rows == HOURS
    assert store.read("load", zone.code).height == HOURS


def test_an_empty_provider_response_is_empty_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider with nothing for the window is a normal outcome."""
    install(monkeypatch, [fake_zone()], {"fake": FakeSource("empty")})

    results = pipeline.ingest(start=START, end=END)

    assert results[0].outcome is Outcome.EMPTY
    assert results[0].ok is True


def test_a_missing_credential_is_skipped_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured key must not fail the scheduled run.

    ERCOT sat in exactly this state for days while an EIA key was obtained, and
    the daily ingest had to keep updating every other market throughout.
    """
    install(monkeypatch, [fake_zone()], {"fake": FakeSource("missing_credential")})

    results = pipeline.ingest(start=START, end=END)

    assert results[0].outcome is Outcome.SKIPPED
    assert results[0].ok is True
    assert "FAKE_API_KEY" in results[0].detail


def test_an_upstream_error_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, [fake_zone()], {"fake": FakeSource("upstream")})

    results = pipeline.ingest(start=START, end=END)

    assert results[0].outcome is Outcome.FAILED
    assert results[0].ok is False
    assert "503" in results[0].detail


def test_a_schema_violation_is_failed_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that changed shape must fail loudly, not store bad rows."""
    zone = fake_zone()
    install(monkeypatch, [zone], {"fake": FakeSource("bad_schema")})

    results = pipeline.ingest(start=START, end=END)

    assert results[0].outcome is Outcome.FAILED
    assert "schema" in results[0].detail.lower()
    assert store.read("load", zone.code).is_empty()


def test_an_unanticipated_exception_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug in one adapter must degrade to FAILED, never crash the run."""
    install(monkeypatch, [fake_zone()], {"fake": FakeSource("boom")})

    results = pipeline.ingest(start=START, end=END)

    assert results[0].outcome is Outcome.FAILED
    assert "RuntimeError" in results[0].detail


# --- Isolation between zones ------------------------------------------------


def test_one_failing_zone_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule the whole module exists to guarantee.

    A broken provider, and an unconfigured one, must both leave every other
    market updating normally.
    """
    zones = [
        fake_zone("AA", "fake"),
        fake_zone("BB", "broken"),
        fake_zone("CC", "nokey"),
        fake_zone("DD", "fake"),
    ]
    install(
        monkeypatch,
        zones,
        {
            "fake": FakeSource("ok"),
            "broken": FakeSource("upstream"),
            "nokey": FakeSource("missing_credential"),
        },
    )

    counts = pipeline.summarise(pipeline.ingest(start=START, end=END))

    assert counts == {"written": 2, "empty": 0, "skipped": 1, "failed": 1}
    assert store.read("load", "AA").height == HOURS
    assert store.read("load", "DD").height == HOURS


def test_an_unknown_source_name_fails_only_its_own_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zone = fake_zone(source="typo")

    def raising(name: str) -> FakeSource:
        raise KeyError(f"unknown source {name!r}")

    monkeypatch.setattr(pipeline, "ZONES", (zone,))
    monkeypatch.setattr(pipeline, "get_source", raising)

    results = pipeline.ingest(start=START, end=END)

    assert results[0].outcome is Outcome.FAILED
    assert "typo" in results[0].detail


# --- Chunking ---------------------------------------------------------------


def test_a_source_window_cap_overrides_the_callers_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenElectricity returns 400 above 32 days, so the cap has to win.

    The caller asks for 60-day chunks; the source declares 2. The run must issue
    several short requests rather than one the provider would reject.
    """
    source = FakeSource("ok", max_window_days=2)
    install(monkeypatch, [fake_zone()], {"fake": source})

    pipeline.ingest(start=START, end=END, chunk_days=60)

    assert len(source.calls) == 4
    for call_start, call_end in source.calls:
        assert call_end - call_start <= dt.timedelta(days=2)


def test_an_uncapped_source_uses_the_callers_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    source = FakeSource("ok", max_window_days=None)
    install(monkeypatch, [fake_zone()], {"fake": source})

    pipeline.ingest(start=START, end=END, chunk_days=60)

    assert len(source.calls) == 1
    assert source.calls[0] == (START, END)


def test_chunks_tile_the_window_without_gaps_or_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeSource("ok", max_window_days=3)
    install(monkeypatch, [fake_zone()], {"fake": source})

    pipeline.ingest(start=START, end=END)

    assert source.calls[0][0] == START
    assert source.calls[-1][1] == END
    for (_, previous_end), (next_start, _) in pairwise(source.calls):
        assert previous_end == next_start


# --- Dry run ----------------------------------------------------------------


def test_dry_run_validates_but_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves an adapter works without touching the committed repository."""
    zone = fake_zone()
    install(monkeypatch, [zone], {"fake": FakeSource("ok")})

    results = pipeline.ingest(start=START, end=END, dry_run=True)

    assert results[0].outcome is Outcome.WRITTEN
    assert results[0].rows == HOURS
    assert store.read("load", zone.code).is_empty()


# --- Target selection -------------------------------------------------------


def test_resolve_targets_defaults_to_every_zone_and_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, [fake_zone("AA"), fake_zone("BB")], {"fake": FakeSource()})

    assert len(pipeline.resolve_targets()) == 2


def test_resolve_targets_narrows_by_zone_and_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, [fake_zone("AA"), fake_zone("BB")], {"fake": FakeSource()})

    assert [target[0].code for target in pipeline.resolve_targets(zones=["BB"])] == ["BB"]
    assert pipeline.resolve_targets(datasets=["price"]) == []


# --- Window validation ------------------------------------------------------


def test_a_naive_start_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A naive datetime would be silently assumed UTC and shift every window."""
    install(monkeypatch, [fake_zone()], {"fake": FakeSource()})

    with pytest.raises(ValueError, match="timezone-aware"):
        pipeline.ingest(start=dt.datetime(2026, 6, 1), end=END)


def test_a_naive_end_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, [fake_zone()], {"fake": FakeSource()})

    with pytest.raises(ValueError, match="timezone-aware"):
        pipeline.ingest(start=START, end=dt.datetime(2026, 6, 8))


def test_an_inverted_window_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, [fake_zone()], {"fake": FakeSource()})

    with pytest.raises(ValueError, match="must precede"):
        pipeline.ingest(start=END, end=START)


# --- Reporting --------------------------------------------------------------


def test_summarise_counts_every_outcome_including_zeros() -> None:
    assert pipeline.summarise([]) == {"written": 0, "empty": 0, "skipped": 0, "failed": 0}


def test_result_renders_rows_when_written_and_detail_otherwise() -> None:
    written = pipeline.IngestResult("AA", "load", "fake", Outcome.WRITTEN, rows=1234)
    failed = pipeline.IngestResult("BB", "load", "fake", Outcome.FAILED, detail="503")

    assert "1,234 rows" in str(written)
    assert "503" in str(failed)
