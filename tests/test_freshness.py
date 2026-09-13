"""Freshness rule tests.

The point of this module is to turn a silent stall into a visible failure. A
scheduled run that fetched nothing still exits zero if no adapter raised, so
without this a provider could go dark for a week and the site would keep
serving last week's numbers as though they were current.

The rules are per zone and dataset on purpose, because the providers publish at
genuinely different speeds. These tests fix the two behaviours that make that
worth doing: a slow provider must not be flagged for being slow, and a fast one
must not be excused by the slow one's allowance.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from gpa import freshness, store
from gpa.freshness import DEFAULT_RULE, RULES, FreshnessReport, FreshnessRule, check, rule_for

NOW = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    return tmp_path


def write_price(zone: str, last: dt.datetime) -> None:
    store.write(
        pl.DataFrame(
            {
                "zone": [zone],
                "ts_utc": [last],
                "resolution_min": [60],
                "price": [42.0],
                "currency": ["EUR"],
                "source": ["test"],
            },
            schema={
                "zone": pl.String,
                "ts_utc": pl.Datetime("us", "UTC"),
                "resolution_min": pl.Int16,
                "price": pl.Float64,
                "currency": pl.String,
                "source": pl.String,
            },
        ),
        "price",
    )


def report(lag_hours: float | None, rule: FreshnessRule) -> FreshnessReport:
    return FreshnessReport("ZZ", "price", lag_hours, rule)


# --- Rule selection ---------------------------------------------------------


def test_an_undeclared_series_falls_back_to_the_default() -> None:
    assert rule_for("DE-LU", "price") is DEFAULT_RULE


def test_brazilian_generation_is_allowed_its_provider_lag() -> None:
    """ONS trails real time by about two days and no faster source exists.

    Flagging it nightly would be flagging the provider's publication schedule,
    which nobody can act on, so the rule accommodates it explicitly.
    """
    rule = rule_for("BR-SIN", "generation")

    assert rule.max_lag_hours == 96.0
    assert rule is not DEFAULT_RULE
    assert "ONS" in rule.reason


def test_brazilian_load_is_not_given_the_generation_allowance() -> None:
    """Load comes from a different ONS endpoint that stays within the hour.

    Sharing the generation rule would let the fast feed go dark for four days
    unnoticed, which is exactly the failure the per-series design prevents.
    """
    assert rule_for("BR-SIN", "load") is DEFAULT_RULE
    assert rule_for("BR-SIN", "load").max_lag_hours < rule_for("BR-SIN", "generation").max_lag_hours


def test_us_generation_is_allowed_the_eia_restatement_delay() -> None:
    for zone in ("ERCOT", "PJM", "CAISO"):
        assert rule_for(zone, "generation").max_lag_hours == 48.0


def test_every_declared_rule_carries_a_reason() -> None:
    """A rule nobody can explain is a rule nobody will trust at 3am."""
    for (zone, dataset), rule in RULES.items():
        assert rule.reason.strip(), f"{zone} {dataset} has no reason"


# --- Blocking behaviour -----------------------------------------------------


def test_a_fresh_series_is_neither_stale_nor_blocking() -> None:
    entry = report(5.0, DEFAULT_RULE)

    assert entry.stale is False
    assert entry.blocking is False


def test_a_stale_scheduled_series_blocks_the_run() -> None:
    entry = report(50.0, DEFAULT_RULE)

    assert entry.stale is True
    assert entry.blocking is True


def test_a_stale_manual_series_reports_without_blocking() -> None:
    """Nobody can refresh CCEE from a cron job.

    Failing the nightly run for something the run cannot fix would train people
    to ignore the failure, which costs more than the staleness does.
    """
    manual = FreshnessRule(24.0, "refreshed by hand", manual=True)
    entry = report(100.0, manual)

    assert entry.stale is True
    assert entry.blocking is False


def test_a_series_with_no_data_at_all_is_missing_and_blocking() -> None:
    entry = report(None, DEFAULT_RULE)

    assert entry.missing is True
    assert entry.blocking is True


def test_the_ccee_zones_are_declared_manual() -> None:
    for zone in ("BR-SECO", "BR-NE"):
        assert rule_for(zone, "price").manual is True


# --- check() against a store ------------------------------------------------


def test_check_covers_every_declared_series_even_when_never_ingested() -> None:
    """A series that has never been stored must report, not vanish.

    Building the report from stored files alone would omit exactly the case
    worth catching: a source that has produced nothing since it was added.
    """
    reports = check(now=NOW)
    declared = sum(len(zone.sources) for zone in _zones())

    assert len(reports) == declared
    assert all(r.missing for r in reports)


def test_check_measures_the_lag_of_a_stored_series() -> None:
    write_price("DE-LU", NOW - dt.timedelta(hours=6))

    entry = next(r for r in check(now=NOW) if r.zone == "DE-LU" and r.dataset == "price")

    assert entry.lag_hours == pytest.approx(6.0)
    assert entry.stale is False


def test_check_flags_a_stored_series_past_its_limit() -> None:
    write_price("DE-LU", NOW - dt.timedelta(hours=40))

    entry = next(r for r in check(now=NOW) if r.zone == "DE-LU" and r.dataset == "price")

    assert entry.stale is True
    assert entry.blocking is True


def test_missing_series_sort_before_merely_stale_ones() -> None:
    write_price("DE-LU", NOW - dt.timedelta(hours=40))

    reports = check(now=NOW)

    assert reports[0].missing is True


def test_ordering_is_relative_to_each_rule_not_absolute_age() -> None:
    """Ten hours past a 36-hour rule is worse than ten past a 96-hour one.

    Sorting on raw age would put the slow-by-design Brazilian generation series
    above a European feed that has genuinely stopped.
    """
    lenient = report(106.0, FreshnessRule(96.0, "slow provider"))
    strict = report(46.0, DEFAULT_RULE)

    assert strict.lag_hours is not None and lenient.lag_hours is not None
    assert strict.lag_hours < lenient.lag_hours
    assert strict.lag_hours / strict.rule.max_lag_hours > (
        lenient.lag_hours / lenient.rule.max_lag_hours
    )


def test_to_frame_renders_an_empty_report_with_the_right_schema() -> None:
    frame = freshness.to_frame([])

    assert frame.is_empty()
    assert "last_ts_utc" in frame.columns
    assert "max_lag_hours" in frame.columns


def test_to_frame_publishes_the_instant_and_the_limit_but_never_the_age() -> None:
    """Age depends on the clock, so exporting it would break determinism.

    `gpa export --check` compares committed tables against the store and fails
    the build on a mismatch. A column holding hours-since-now changes every
    second, so it would fail every run, and the number would be wrong within
    the hour anyway. The site computes age when a reader opens the page.
    """
    entry = FreshnessReport("DE-LU", "price", 6.0, DEFAULT_RULE, NOW - dt.timedelta(hours=6))
    frame = freshness.to_frame([entry])

    assert "lag_hours" not in frame.columns
    assert frame["last_ts_utc"][0] == "2026-09-13T06:00:00Z"
    assert frame["max_lag_hours"][0] == DEFAULT_RULE.max_lag_hours


def test_to_frame_is_stable_across_calls() -> None:
    """Two exports of an unchanged store must produce identical bytes."""
    entry = FreshnessReport("DE-LU", "price", 6.0, DEFAULT_RULE, NOW)

    assert freshness.to_frame([entry]).equals(freshness.to_frame([entry]))


def _zones():  # type: ignore[no-untyped-def]
    from gpa.zones import ZONES

    return ZONES
