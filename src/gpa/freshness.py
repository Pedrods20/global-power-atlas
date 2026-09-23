"""Freshness: how stale each series is allowed to get before it is wrong.

The scheduled ingest can fail, or the provider can quietly stop publishing, and
a run that fetches nothing still exits zero if no adapter raised. This module
turns that silent stall into a visible failure.

Every series comes from Energy-Charts and lands within hours, so one rule covers
them all; it carries the reason for its value, because a limit that cannot be
explained is one nobody trusts when it fires.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from gpa import store
from gpa.zones import ZONES

__all__ = ["DEFAULT_RULE", "FreshnessReport", "FreshnessRule", "check"]


@dataclass(frozen=True, slots=True)
class FreshnessRule:
    """How old a series may get, and why that number."""

    max_lag_hours: float
    reason: str


DEFAULT_RULE: Final = FreshnessRule(
    max_lag_hours=36.0,
    reason=(
        "Energy-Charts feeds land within twelve hours. Thirty-six absorbs one missed "
        "run plus the provider's own publication delay without crying wolf."
    ),
)


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    """One series' age measured against its rule."""

    zone: str
    dataset: str
    lag_hours: float | None
    rule: FreshnessRule

    @property
    def missing(self) -> bool:
        """Whether the series holds no observations at all."""
        return self.lag_hours is None

    @property
    def stale(self) -> bool:
        """Whether the series is older than its rule allows."""
        return self.lag_hours is not None and self.lag_hours > self.rule.max_lag_hours

    def __str__(self) -> str:
        age = "no data" if self.lag_hours is None else f"{self.lag_hours:.1f}h"
        limit = f"limit {self.rule.max_lag_hours:.0f}h"
        state = "STALE" if self.stale else "ok"
        if self.missing:
            state = "MISSING"
        return f"{self.zone:9s} {self.dataset:11s} {age:>9s}  {limit:<12s} {state}"


def check(now: dt.datetime | None = None) -> list[FreshnessReport]:
    """Measure every declared series against its rule.

    Covers every dataset each zone declares a source for, so a series that has
    never been ingested reports as missing rather than being absent from the
    output. A gap that produces no row is exactly the case a coverage report
    built only from stored files would overlook.

    Args:
        now: Reference instant, for tests. Defaults to the current time.

    Returns:
        One report per declared series, ordered worst first so that the top of
        a long list is the part worth reading.
    """
    reference = now or dt.datetime.now(dt.UTC)
    coverage = store.coverage()

    latest: dict[tuple[str, str], dt.datetime] = {}
    if not coverage.is_empty():
        for row in coverage.iter_rows(named=True):
            if row["last_ts_utc"] is not None:
                latest[(row["zone"], row["dataset"])] = row["last_ts_utc"]

    reports: list[FreshnessReport] = []
    for zone in ZONES:
        for dataset in zone.sources:
            last = latest.get((zone.code, dataset))
            lag = None if last is None else (reference - last).total_seconds() / 3600
            reports.append(FreshnessReport(zone.code, dataset, lag, DEFAULT_RULE))

    # Missing first, then the most overdue, so the top of a long list is the
    # part worth reading.
    def severity(report: FreshnessReport) -> tuple[int, float]:
        return (0, 0.0) if report.lag_hours is None else (1, -report.lag_hours)

    return sorted(reports, key=severity)
