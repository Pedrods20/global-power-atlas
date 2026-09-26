"""Ingestion: fetch, validate and store each zone and dataset; one failure never stops the rest."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import partial

from gpa import store
from gpa.schema import SchemaError, SchemaErrors, validate
from gpa.sources import Source, SourceError, get_source
from gpa.zones import ZONES, Zone, get_zone

__all__ = [
    "PUBLISHED_AHEAD_DAYS",
    "IngestResult",
    "Outcome",
    "ingest",
    "now_utc",
    "resolve_targets",
    "resolve_window",
    "summarise",
]

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7
"""Refetched behind the checkpoint: providers publish late and revise, and upserts converge."""

PUBLISHED_AHEAD_DAYS: dict[str, int] = {"price": 2, "fundamentals": 2}
"""Days past now worth requesting: tomorrow's prices and forecasts exist before tomorrow does."""

BACKFILL_CHUNK_DAYS = 60
"""Each chunk is written as it lands, so an interrupted backfill keeps what it has."""


class Outcome(StrEnum):
    WRITTEN = "written"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What happened for one zone and dataset."""

    zone: str
    dataset: str
    source: str
    outcome: Outcome
    rows: int = 0
    detail: str = ""
    start: dt.datetime | None = None
    end: dt.datetime | None = None

    def __str__(self) -> str:
        head = f"{self.zone:9s} {self.dataset:11s} via {self.source:16s} {self.outcome.value:8s}"
        if self.outcome is Outcome.WRITTEN:
            body = f"{head} {self.rows:>8,d} rows"
        else:
            body = f"{head} {self.detail}" if self.detail else head
        if self.start is not None and self.end is not None:
            body = f"{body} [{self.start:%Y-%m-%d %H:%M} to {self.end:%Y-%m-%d %H:%M} UTC]"
        return body


def resolve_targets(
    zones: Sequence[str] | None = None, datasets: Sequence[str] | None = None
) -> list[tuple[Zone, str, str]]:
    """``(zone, dataset, source)`` for every selected pair a zone declares a source for."""
    selected = [get_zone(code) for code in zones] if zones else list(ZONES)
    return [
        (zone, dataset, source)
        for zone in selected
        for dataset, source in zone.sources.items()
        if not datasets or dataset in datasets
    ]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def resolve_window(
    zone: Zone,
    dataset: str,
    *,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    """The window one target fetches: from the stored checkpoint, minus the overlap.

    A run after missed schedules therefore resumes from what the store holds. Holes
    older than the overlap are invisible here; ``gpa backfill`` repairs them.
    """
    reference = now or now_utc()
    ceiling = end if end is not None else reference
    if end is None:
        end = reference + dt.timedelta(days=PUBLISHED_AHEAD_DAYS.get(dataset, 0))
    if start is None:
        last = store.last_ingested(dataset, zone.code)
        start = (ceiling if last is None else min(ceiling, last)) - dt.timedelta(days=lookback_days)
    return start, end


def ingest(
    zones: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
    *,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    chunk_days: int = BACKFILL_CHUNK_DAYS,
    dry_run: bool = False,
) -> list[IngestResult]:
    """Fetch, validate and store each target's window; ``dry_run`` writes nothing."""
    for name, bound in (("start", start), ("end", end)):
        if bound is not None and bound.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")
    if start is not None and end is not None and start >= end:
        raise ValueError(f"start {start.isoformat()} must precede end {end.isoformat()}")
    if lookback_days < 0:
        raise ValueError("lookback_days cannot be negative")

    reference = now_utc()
    results: list[IngestResult] = []
    for zone, dataset, source_name in resolve_targets(zones, datasets):
        try:
            source = get_source(source_name)
        except KeyError as exc:
            results.append(
                IngestResult(zone.code, dataset, source_name, Outcome.FAILED, detail=str(exc))
            )
            continue
        window_start, window_end = resolve_window(
            zone, dataset, start=start, end=end, lookback_days=lookback_days, now=reference
        )
        record = partial(
            IngestResult, zone.code, dataset, source_name, start=window_start, end=window_end
        )
        if window_start >= window_end:
            results.append(record(Outcome.FAILED, detail="resolved window is empty"))
            continue
        try:
            rows = _ingest_one(source, zone, dataset, window_start, window_end, chunk_days, dry_run)
        except (SchemaError, SchemaErrors) as exc:
            log.error("%s %s failed validation: %s", zone.code, dataset, exc)
            results.append(record(Outcome.FAILED, detail=f"schema violation: {_first_line(exc)}"))
        except SourceError as exc:
            log.error("%s %s failed: %s", zone.code, dataset, exc)
            results.append(record(Outcome.FAILED, detail=str(exc)))
        except Exception as exc:
            log.exception("%s %s raised an unexpected error", zone.code, dataset)
            results.append(record(Outcome.FAILED, detail=f"{type(exc).__name__}: {exc}"))
        else:
            results.append(record(Outcome.WRITTEN if rows else Outcome.EMPTY, rows=rows))
    return results


def _ingest_one(
    source: Source,
    zone: Zone,
    dataset: str,
    start: dt.datetime,
    end: dt.datetime,
    chunk_days: int,
    dry_run: bool,
) -> int:
    """Fetch in chunks no longer than the source serves, writing each before the next."""
    cap = source.max_window_days
    chunk = dt.timedelta(days=max(1, min(chunk_days, cap) if cap else chunk_days))
    written = 0
    cursor = start
    while cursor < end:
        stop = min(cursor + chunk, end)
        frame = source.fetch(zone, dataset, cursor, stop)
        if not frame.is_empty():
            frame = validate(frame, dataset)
            if not dry_run:
                store.write(frame, dataset, validate_first=False)
            written += frame.height
        cursor = stop
    return written


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200]


def summarise(results: Iterable[IngestResult]) -> dict[str, int]:
    """Counts by outcome, for the log and the workflow exit code."""
    counts = dict.fromkeys((outcome.value for outcome in Outcome), 0)
    for result in results:
        counts[result.outcome.value] += 1
    return counts
