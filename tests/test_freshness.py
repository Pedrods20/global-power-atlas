"""Freshness tests.

The point of this module is to turn a silent stall into a visible failure. A
scheduled run that fetched nothing still exits zero if no adapter raised, so
without this the provider could go dark for a week and the site would keep
serving last week's numbers as though they were current.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from gpa import store
from gpa.freshness import DEFAULT_RULE, FreshnessReport, check
from gpa.zones import ZONES

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


def report(lag_hours: float | None) -> FreshnessReport:
    return FreshnessReport("ZZ", "price", lag_hours, DEFAULT_RULE)


def test_the_rule_carries_a_reason() -> None:
    """A limit nobody can explain is a limit nobody will trust at 3am."""
    assert DEFAULT_RULE.reason.strip()


def test_a_fresh_series_is_not_stale() -> None:
    assert report(5.0).stale is False


def test_a_series_past_the_limit_is_stale() -> None:
    assert report(50.0).stale is True


def test_a_series_with_no_data_at_all_is_missing() -> None:
    entry = report(None)

    assert entry.missing is True
    assert entry.stale is False


# --- check() against a store ------------------------------------------------


def test_check_covers_every_declared_series_even_when_never_ingested() -> None:
    """A series that has never been stored must report, not vanish.

    Building the report from stored files alone would omit exactly the case
    worth catching: a source that has produced nothing since it was added.
    """
    reports = check(now=NOW)
    declared = sum(len(zone.sources) for zone in ZONES)

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


def test_missing_series_sort_before_merely_stale_ones() -> None:
    write_price("DE-LU", NOW - dt.timedelta(hours=40))

    reports = check(now=NOW)

    assert reports[0].missing is True
