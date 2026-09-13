"""Command line interface tests.

The CLI is what the scheduled workflow actually invokes, so its exit codes are
load-bearing: the ingest job decides whether to fail a run from them, and the
CI job gates the build on ``validate`` and ``export --check``. A command that
silently returns zero on a broken store would let bad data reach the site.

Every test runs against a temporary store via ``GPA_DATA_ROOT`` and never
touches the network or the committed repository data.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from gpa import store
from gpa.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def populated(temporary_store: Path) -> Path:
    """A store holding one valid day of prices for one real zone."""
    start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    stamps = pl.datetime_range(
        start, start + dt.timedelta(days=1), "1h", time_zone="UTC", eager=True, closed="left"
    )
    frame = pl.DataFrame(
        {
            "zone": ["DE-LU"] * stamps.len(),
            "ts_utc": stamps,
            "resolution_min": [60] * stamps.len(),
            "price": [40.0 + i for i in range(stamps.len())],
            "currency": ["EUR"] * stamps.len(),
            "source": ["test"] * stamps.len(),
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "price": pl.Float64,
            "currency": pl.String,
            "source": pl.String,
        },
    )
    store.write(frame, "price")
    return temporary_store


# --- Commands that only read the registry -----------------------------------


def test_version_prints_the_package_version() -> None:
    from gpa import __version__

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_zones_lists_the_registry() -> None:
    result = runner.invoke(app, ["zones"])

    assert result.exit_code == 0
    assert "DE-LU" in result.stdout
    assert "BR-SIN" in result.stdout


def test_zones_verbose_adds_the_editorial_notes() -> None:
    plain = runner.invoke(app, ["zones"])
    verbose = runner.invoke(app, ["zones", "--verbose"])

    assert verbose.exit_code == 0
    assert len(verbose.stdout) > len(plain.stdout)


# --- stats ------------------------------------------------------------------


def test_stats_on_an_empty_store_succeeds_with_a_warning() -> None:
    """An empty store is a fresh clone, not an error."""
    result = runner.invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "empty" in result.stdout.lower()


def test_stats_reports_the_stored_rows(populated: Path) -> None:
    result = runner.invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "DE-LU" in result.stdout


# --- validate ---------------------------------------------------------------


def test_validate_passes_on_a_clean_store(populated: Path) -> None:
    result = runner.invoke(app, ["validate"])

    assert result.exit_code == 0
    assert "valid" in result.stdout.lower()


def test_validate_on_an_empty_store_succeeds_with_a_warning() -> None:
    result = runner.invoke(app, ["validate"])

    assert result.exit_code == 0
    assert "no partitions" in result.stdout.lower()


def test_validate_exits_non_zero_on_a_corrupt_partition(populated: Path) -> None:
    """CI gates the build on this, so a broken partition must fail the command.

    A file written by an older adapter, hand-edited, or left half-written by an
    interrupted run has to stop the pipeline rather than reach the site.
    """
    partition = next((populated / "price").glob("zone=*/*.parquet"))
    pl.DataFrame({"nonsense": [1, 2, 3]}).write_parquet(partition)

    result = runner.invoke(app, ["validate"])

    assert result.exit_code == 1
    assert "fail" in result.stdout.lower()


# --- export -----------------------------------------------------------------


def test_export_writes_the_site_tables(populated: Path, tmp_path: Path) -> None:
    destination = tmp_path / "site-data"

    result = runner.invoke(app, ["export", "--output", str(destination)])

    assert result.exit_code == 0
    assert (destination / "zones.json").exists()
    assert (destination / "daily_prices.parquet").exists()


def test_export_check_passes_when_the_tables_match(populated: Path, tmp_path: Path) -> None:
    destination = tmp_path / "site-data"
    runner.invoke(app, ["export", "--output", str(destination)])

    result = runner.invoke(app, ["export", "--check", "--output", str(destination)])

    assert result.exit_code == 0


def test_export_check_exits_non_zero_when_the_tables_are_stale(
    populated: Path, tmp_path: Path
) -> None:
    """CI fails the build on this, which is what stops a stale site shipping.

    Ingesting without re-exporting leaves the committed site tables describing
    data that no longer matches the store.
    """
    destination = tmp_path / "site-data"
    runner.invoke(app, ["export", "--output", str(destination)])

    extra = dt.datetime(2026, 6, 2, tzinfo=dt.UTC)
    store.write(
        pl.DataFrame(
            {
                "zone": ["DE-LU"],
                "ts_utc": [extra],
                "resolution_min": [60],
                "price": [999.0],
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

    result = runner.invoke(app, ["export", "--check", "--output", str(destination)])

    assert result.exit_code == 1


# --- query ------------------------------------------------------------------


def test_query_runs_sql_against_the_store(populated: Path) -> None:
    result = runner.invoke(app, ["query", "SELECT count(*) AS n FROM price"])

    assert result.exit_code == 0
    assert "24" in result.stdout


# --- ingest and backfill ----------------------------------------------------


def test_ingest_dry_run_reaches_no_provider_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the wiring from CLI flags into the pipeline without network.

    Every source is replaced, so a failure here is the CLI's, not a provider's.
    """
    from gpa import pipeline
    from gpa.schema import empty_frame
    from gpa.zones import Zone

    class Offline:
        name: str = "offline"
        datasets: tuple[str, ...] = ("price", "load", "generation")
        max_window_days: int | None = None

        def fetch(
            self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime
        ) -> pl.DataFrame:
            return empty_frame(dataset)

    monkeypatch.setattr(pipeline, "get_source", lambda name: Offline())

    result = runner.invoke(app, ["ingest", "--zone", "DE-LU", "--days", "1", "--dry-run"])

    assert result.exit_code == 0
    assert "empty" in result.stdout.lower()
    assert store.read("price", "DE-LU").is_empty()


def test_ingest_exits_non_zero_when_a_target_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scheduled workflow reads this exit code to decide whether to alert."""
    from gpa import pipeline
    from gpa.sources.base import UpstreamError
    from gpa.zones import Zone

    class Broken:
        name: str = "broken"
        datasets: tuple[str, ...] = ("price", "load", "generation")
        max_window_days: int | None = None

        def fetch(
            self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime
        ) -> pl.DataFrame:
            raise UpstreamError("provider returned 503")

    monkeypatch.setattr(pipeline, "get_source", lambda name: Broken())

    result = runner.invoke(app, ["ingest", "--zone", "DE-LU", "--days", "1"])

    assert result.exit_code == 1
    assert "failed" in result.stdout.lower()


def test_ingest_rejects_an_unknown_zone() -> None:
    result = runner.invoke(app, ["ingest", "--zone", "NOWHERE", "--days", "1"])

    assert result.exit_code != 0


def test_backfill_reports_the_window_it_will_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from gpa import pipeline
    from gpa.schema import empty_frame
    from gpa.zones import Zone

    class Offline:
        name: str = "offline"
        datasets: tuple[str, ...] = ("price", "load", "generation")
        max_window_days: int | None = None

        def fetch(
            self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime
        ) -> pl.DataFrame:
            return empty_frame(dataset)

    monkeypatch.setattr(pipeline, "get_source", lambda name: Offline())

    result = runner.invoke(app, ["backfill", "--zone", "DE-LU", "--days", "3", "--dry-run"])

    assert result.exit_code == 0
    assert "Backfilling" in result.stdout
