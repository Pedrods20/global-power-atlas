"""Ingestion orchestration.

Turns "collect these zones over this window" into validated rows on disk, and
reports what happened per zone and dataset so that a scheduled run leaves an
auditable trail rather than a silent success.

The guiding rule is that one zone failing must never stop the others. A source
whose credential is missing, or whose provider is having a bad day, degrades to
a skipped or failed outcome for that zone alone. A run is only a failure if
every requested target failed.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from gpa import store
from gpa.schema import SchemaError, SchemaErrors, validate
from gpa.sources import MissingCredential, SourceError, get_source
from gpa.zones import ZONES, Zone, get_zone

__all__ = ["IngestResult", "Outcome", "ingest", "resolve_targets", "summarise"]

log = logging.getLogger(__name__)

# Providers publish on a lag and revise afterwards, so a daily run re-fetches a
# trailing window rather than only yesterday. The store upserts, so overlapping
# runs converge instead of duplicating.
DEFAULT_LOOKBACK_DAYS = 7

# Requesting several years in one call times out on most providers and produces
# an unhelpfully large failure. Backfills are cut into chunks and each chunk is
# written before the next is fetched, so an interrupted backfill keeps whatever
# it already completed.
BACKFILL_CHUNK_DAYS = 60


class Outcome(StrEnum):
    """What happened for one zone and dataset."""

    WRITTEN = "written"
    EMPTY = "empty"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Result of ingesting one dataset for one zone."""

    zone: str
    dataset: str
    source: str
    outcome: Outcome
    rows: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Whether this target completed without error. Empty counts as fine."""
        return self.outcome in (Outcome.WRITTEN, Outcome.EMPTY, Outcome.SKIPPED)

    def __str__(self) -> str:
        head = f"{self.zone:9s} {self.dataset:11s} via {self.source:16s} {self.outcome.value:8s}"
        if self.outcome is Outcome.WRITTEN:
            return f"{head} {self.rows:>8,d} rows"
        return f"{head} {self.detail}" if self.detail else head


def resolve_targets(
    zones: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
) -> list[tuple[Zone, str, str]]:
    """Expand a zone and dataset selection into concrete work items.

    Args:
        zones: Zone codes, or ``None`` for every registered zone.
        datasets: Dataset names, or ``None`` for every dataset a zone declares.

    Returns:
        ``(zone, dataset, source_name)`` triples, skipping combinations a zone
        does not declare a source for.

    Raises:
        KeyError: If a requested zone code is not registered.
    """
    selected = [get_zone(code) for code in zones] if zones else list(ZONES)
    wanted = set(datasets) if datasets else None

    targets: list[tuple[Zone, str, str]] = []
    for zone in selected:
        for dataset, source_name in zone.sources.items():
            if wanted is not None and dataset not in wanted:
                continue
            targets.append((zone, dataset, source_name))
    return targets


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
    """Fetch, validate and store a window for the selected targets.

    Args:
        zones: Zone codes, or ``None`` for all.
        datasets: Dataset names, or ``None`` for all a zone declares.
        start: Inclusive UTC-aware lower bound. Defaults to ``lookback_days``
            before ``end``.
        end: Exclusive UTC-aware upper bound. Defaults to now.
        lookback_days: Window length when ``start`` is not given.
        chunk_days: Maximum days fetched per request.
        dry_run: Fetch and validate but write nothing. Used to prove an adapter
            works without touching the repository.

    Returns:
        One result per target, in the order they were attempted.

    Raises:
        ValueError: If the window is empty or a bound is naive.
        KeyError: If a requested zone is not registered.
    """
    end = end or dt.datetime.now(dt.UTC)
    start = start or end - dt.timedelta(days=lookback_days)

    for name, bound in (("start", start), ("end", end)):
        if bound.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")
    if start >= end:
        raise ValueError(f"start {start.isoformat()} must precede end {end.isoformat()}")

    results: list[IngestResult] = []

    for zone, dataset, source_name in resolve_targets(zones, datasets):
        try:
            source = get_source(source_name)
        except KeyError as exc:
            results.append(
                IngestResult(zone.code, dataset, source_name, Outcome.FAILED, detail=str(exc))
            )
            continue

        try:
            rows = _ingest_one(source, zone, dataset, start, end, chunk_days, dry_run)
        except MissingCredential as exc:
            log.warning("skipping %s %s: %s", zone.code, dataset, exc)
            results.append(
                IngestResult(zone.code, dataset, source_name, Outcome.SKIPPED, detail=str(exc))
            )
        except (SchemaError, SchemaErrors) as exc:
            log.error("%s %s failed validation: %s", zone.code, dataset, exc)
            results.append(
                IngestResult(
                    zone.code,
                    dataset,
                    source_name,
                    Outcome.FAILED,
                    detail=f"schema violation: {_first_line(exc)}",
                )
            )
        except SourceError as exc:
            log.error("%s %s failed: %s", zone.code, dataset, exc)
            results.append(
                IngestResult(zone.code, dataset, source_name, Outcome.FAILED, detail=str(exc))
            )
        except Exception as exc:
            log.exception("%s %s raised an unexpected error", zone.code, dataset)
            results.append(
                IngestResult(
                    zone.code,
                    dataset,
                    source_name,
                    Outcome.FAILED,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            outcome = Outcome.WRITTEN if rows else Outcome.EMPTY
            results.append(IngestResult(zone.code, dataset, source_name, outcome, rows=rows))

    return results


def _ingest_one(
    source: object,
    zone: Zone,
    dataset: str,
    start: dt.datetime,
    end: dt.datetime,
    chunk_days: int,
    dry_run: bool,
) -> int:
    """Fetch one target in chunks, writing each chunk before fetching the next.

    The chunk is the smaller of the caller's request and whatever the source
    declares it can serve, because those limits are real: Energy-Charts times
    out on a long window.
    """
    cap = getattr(source, "max_window_days", None)
    effective = min(chunk_days, cap) if cap else chunk_days

    written = 0
    chunk = dt.timedelta(days=max(1, effective))
    cursor = start

    while cursor < end:
        stop = min(cursor + chunk, end)
        frame = source.fetch(zone, dataset, cursor, stop)  # type: ignore[attr-defined]
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
    """Count results by outcome, for logging and for a workflow exit code."""
    counts = dict.fromkeys((o.value for o in Outcome), 0)
    for result in results:
        counts[result.outcome.value] += 1
    return counts
