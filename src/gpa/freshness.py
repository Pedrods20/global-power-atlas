"""Freshness rules: how stale each series is allowed to get before it is wrong.

The scheduled ingest can fail, or one provider can quietly stop publishing,
and until now nothing surfaced either. A run that fetches nothing still exits
zero if no adapter raised.

A single global threshold does not work here, because the providers do not
publish at the same speed and the differences are legitimate. Brazilian
generation trails real time by about two days because the ONS hourly balance
does; EIA restates US generation on roughly a day's delay; European and
Australian feeds arrive within hours. A threshold loose enough for the slowest
would never catch the fastest going dark.

So every rule is declared per zone and dataset, each with the reason for its
value. A rule that cannot be explained is a rule nobody will trust when it
fires at three in the morning.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

import polars as pl

from gpa import store
from gpa.zones import ZONES

__all__ = [
    "DEFAULT_RULE",
    "RULES",
    "FreshnessReport",
    "FreshnessRule",
    "check",
    "rule_for",
]


@dataclass(frozen=True, slots=True)
class FreshnessRule:
    """How old a series may get, and why that number.

    Attributes:
        max_lag_hours: Age beyond which the series counts as stale.
        reason: Why this value rather than another. Shown when the rule fires.
        manual: Whether the series is refreshed by hand rather than by the
            scheduled job. A manual series that is stale is a reminder, not a
            pipeline failure, so it never fails the run.
    """

    max_lag_hours: float
    reason: str
    manual: bool = False


DEFAULT_RULE: Final = FreshnessRule(
    max_lag_hours=36.0,
    reason=(
        "Most feeds here land within twelve hours. Thirty-six absorbs one missed "
        "daily run plus a provider's own publication delay without crying wolf."
    ),
)

RULES: Final[dict[tuple[str, str], FreshnessRule]] = {
    # ONS republishes its hourly balance several times a day, but the contents
    # trail real time by roughly two days and no faster source for Brazilian
    # generation by technology exists. See the methodology's publication-lag
    # section.
    ("BR-SIN", "generation"): FreshnessRule(
        max_lag_hours=96.0,
        reason="ONS publishes the hourly balance about two days behind real time.",
    ),
    # EIA-930 restates hourly generation for days after first publication and
    # lands roughly a day behind. Demand arrives noticeably sooner.
    ("ERCOT", "generation"): FreshnessRule(
        max_lag_hours=48.0, reason="EIA-930 generation lands about a day behind."
    ),
    ("PJM", "generation"): FreshnessRule(
        max_lag_hours=48.0, reason="EIA-930 generation lands about a day behind."
    ),
    ("CAISO", "generation"): FreshnessRule(
        max_lag_hours=48.0, reason="EIA-930 generation lands about a day behind."
    ),
    # CCEE refuses automated clients, so the four PLD zones are imported by hand
    # from official CSVs and are deliberately excluded from the daily cron.
    # Staleness here is expected drift, not a broken pipeline, so it reports
    # without failing. Thirty days is long enough not to nag and short enough
    # that a forgotten refresh still surfaces.
    ("BR-SECO", "price"): FreshnessRule(
        max_lag_hours=24.0 * 30,
        reason="CCEE blocks automated download; PLD is refreshed manually.",
        manual=True,
    ),
    ("BR-NE", "price"): FreshnessRule(
        max_lag_hours=24.0 * 30,
        reason="CCEE blocks automated download; PLD is refreshed manually.",
        manual=True,
    ),
}


def rule_for(zone: str, dataset: str) -> FreshnessRule:
    """The rule governing one series, falling back to :data:`DEFAULT_RULE`."""
    return RULES.get((zone, dataset), DEFAULT_RULE)


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    """One series' age measured against its rule."""

    zone: str
    dataset: str
    lag_hours: float | None
    rule: FreshnessRule
    last_ts_utc: dt.datetime | None = None

    @property
    def missing(self) -> bool:
        """Whether the series holds no observations at all."""
        return self.lag_hours is None

    @property
    def stale(self) -> bool:
        """Whether the series is older than its rule allows."""
        return self.lag_hours is not None and self.lag_hours > self.rule.max_lag_hours

    @property
    def blocking(self) -> bool:
        """Whether this should fail a scheduled run.

        A manually refreshed series never blocks: nobody can fix it from a cron
        job, so failing the run every night would train people to ignore the
        failure.
        """
        return (self.stale or self.missing) and not self.rule.manual

    def __str__(self) -> str:
        age = "no data" if self.lag_hours is None else f"{self.lag_hours:.1f}h"
        limit = f"limit {self.rule.max_lag_hours:.0f}h"
        state = "MANUAL" if self.rule.manual and self.stale else ("STALE" if self.stale else "ok")
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
            reports.append(
                FreshnessReport(zone.code, dataset, lag, rule_for(zone.code, dataset), last)
            )

    # Missing first, then the most overdue relative to its own limit, so that a
    # series 10 hours past a 12-hour rule outranks one 10 hours past a 96-hour
    # rule.
    def severity(report: FreshnessReport) -> tuple[int, float]:
        if report.missing:
            return (0, 0.0)
        assert report.lag_hours is not None
        return (1, -(report.lag_hours / report.rule.max_lag_hours))

    return sorted(reports, key=severity)


def to_frame(reports: list[FreshnessReport]) -> pl.DataFrame:
    """Render reports as a frame for the site export.

    Carries the last observed instant and the limit, but deliberately **not**
    the measured age. Age is a function of the clock, so baking it into a
    committed file would make the export non-deterministic and would freeze a
    number that is wrong within the hour. The site computes it when a reader
    opens the page, which is both reproducible here and more accurate there.
    """
    schema = {
        "zone": pl.String,
        "dataset": pl.String,
        "last_ts_utc": pl.String,
        "max_lag_hours": pl.Float64,
        "manual": pl.Boolean,
    }
    if not reports:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(
        [
            {
                "zone": r.zone,
                "dataset": r.dataset,
                "last_ts_utc": (
                    r.last_ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if r.last_ts_utc else None
                ),
                "max_lag_hours": r.rule.max_lag_hours,
                "manual": r.rule.manual,
            }
            for r in reports
        ],
        schema=schema,
    )
