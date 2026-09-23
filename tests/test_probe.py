"""The fundamentals publication probe: coverage arithmetic and the log it keeps.

No test reaches the network. The provider is replaced by a function serving a
chosen part of the delivery day, because what is under test is how coverage and
the distance from the gate are measured, not the adapter.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from gpa import probe
from gpa.cli import app
from gpa.zones import Zone, get_zone

DE = get_zone("DE-LU")


def serving(*series: str, every: str = "15m"):
    """A fetch that serves the whole requested window for ``series`` only."""

    def fetch(zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        assert dataset == "fundamentals"
        stamps = pl.datetime_range(start, end, every, time_zone="UTC", eager=True, closed="left")
        frames = [
            pl.DataFrame({"ts_utc": stamps, "series": [name] * stamps.len()}) for name in series
        ]
        empty = pl.DataFrame(schema={"ts_utc": pl.Datetime("us", "UTC"), "series": pl.String})
        return pl.concat(frames) if frames else empty

    return fetch


def test_a_morning_check_with_nothing_published_measures_its_margin_to_the_gate() -> None:
    """The 23 September 2026 run, replayed: 07:49Z, delivery 24 September, gate
    at 10:00Z under summer time, and no day-ahead series served yet."""
    now = dt.datetime(2026, 9, 23, 7, 49, tzinfo=dt.UTC)

    rows = probe.check(DE, now=now, fetch=serving())

    assert rows["series"].to_list() == ["load", "wind", "solar"]
    assert rows["delivery_date"].unique().to_list() == ["2026-09-24"]
    assert rows["hours_expected"].unique().to_list() == [24]
    assert rows["hours_covered"].to_list() == [0, 0, 0]
    assert rows["minutes_before_gate"].unique().to_list() == [131]


def test_each_series_is_measured_on_its_own() -> None:
    """``ridge_da`` needs all three; a missing wind series must show as missing
    even when load and solar are complete."""
    now = dt.datetime(2026, 9, 23, 9, 30, tzinfo=dt.UTC)

    rows = probe.check(DE, now=now, fetch=serving("load", "solar"))

    covered = dict(zip(rows["series"], rows["hours_covered"], strict=True))
    assert covered == {"load": 24, "wind": 0, "solar": 24}


def test_hourly_and_quarter_hour_series_count_the_same_hours() -> None:
    now = dt.datetime(2026, 9, 23, 9, 30, tzinfo=dt.UTC)

    quarter = probe.check(DE, now=now, fetch=serving("load", every="15m"))
    hourly = probe.check(DE, now=now, fetch=serving("load", every="1h"))

    assert quarter["hours_covered"][0] == hourly["hours_covered"][0] == 24


def test_the_autumn_clock_change_expects_twenty_five_hours_and_a_winter_gate() -> None:
    """Delivery on 25 October 2026 has a repeated hour, and its gate at noon on
    the 24th is still summer time: 10:00Z."""
    now = dt.datetime(2026, 10, 24, 6, 0, tzinfo=dt.UTC)

    rows = probe.check(DE, now=now, fetch=serving("load", "wind", "solar"))

    assert rows["hours_expected"].unique().to_list() == [25]
    assert rows["hours_covered"].to_list() == [25, 25, 25]
    assert rows["minutes_before_gate"].unique().to_list() == [240]


def test_a_check_after_the_gate_is_recorded_as_negative_margin() -> None:
    now = dt.datetime(2026, 9, 23, 16, 0, tzinfo=dt.UTC)

    rows = probe.check(DE, now=now, fetch=serving("load", "wind", "solar"))

    assert rows["minutes_before_gate"].unique().to_list() == [-360]


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        probe.check(DE, now=dt.datetime(2026, 9, 23, 8), fetch=serving())


def test_the_log_writes_its_header_once_and_reads_back_typed(tmp_path: Path) -> None:
    log = tmp_path / "probes" / "fundamentals_publication.csv"
    early = probe.check(DE, now=dt.datetime(2026, 9, 23, 7, 49, tzinfo=dt.UTC), fetch=serving())
    later = probe.check(
        DE, now=dt.datetime(2026, 9, 23, 16, 0, tzinfo=dt.UTC), fetch=serving("load")
    )

    probe.append(early, log)
    probe.append(later, log)

    assert log.read_text(encoding="utf-8").count("checked_at_utc") == 1
    back = probe.read(log)
    assert back.height == 6
    assert back.schema == probe.SCHEMA
    assert back["hours_covered"].to_list() == [0, 0, 0, 24, 0, 0]


def test_an_absent_log_reads_as_empty(tmp_path: Path) -> None:
    assert probe.read(tmp_path / "missing.csv").is_empty()


def test_the_command_appends_one_row_per_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gpa import pipeline

    class Served:
        name = "served"
        fetch = staticmethod(serving("load", "wind"))

    monkeypatch.setattr(pipeline, "now_utc", lambda: dt.datetime(2026, 9, 23, 9, 0, tzinfo=dt.UTC))
    monkeypatch.setattr("gpa.sources.get_source", lambda name: Served())
    log = tmp_path / "log.csv"

    result = CliRunner().invoke(app, ["probe-fundamentals", "--output", str(log)])

    assert result.exit_code == 0, result.stdout
    assert "60 min before the gate" in result.stdout
    assert "wind 24/24" in result.stdout
    assert "solar 0/24" in result.stdout
    assert probe.read(log).height == 3
