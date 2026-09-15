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
from types import SimpleNamespace

import polars as pl
import pytest
from typer.testing import CliRunner

from gpa import store
from gpa.cli import app
from gpa.forecast import snapshot
from gpa.forecast.backtest import stable_hash
from tests.test_battery import DAY
from tests.test_battery import predictions as battery_predictions

runner = CliRunner()


@pytest.fixture(autouse=True)
def temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    # The frozen release is committed repository data too, not just the store.
    monkeypatch.setattr(snapshot, "ROOT", tmp_path / "experiments")
    monkeypatch.setattr(snapshot, "CURRENT", tmp_path / "experiments" / "current.json")
    return tmp_path


@pytest.fixture
def release(temporary_store: Path) -> None:
    """A tiny valid frozen release: two complete days for all five forecasts."""
    predictions = battery_predictions(days=(DAY, DAY + dt.timedelta(days=1)))
    panel = predictions.select("local_date", "local_hour").unique()
    tables = {
        "predictions": predictions,
        "scores": pl.DataFrame({"scope": ["overall"], "model": ["ridge"], "mae": [1.0]}),
        "daily": pl.DataFrame({"local_date": [DAY], "model": ["ridge"], "mae": [1.0]}),
        "coefficients": pl.DataFrame({"feature": ["price_d1"], "coefficient": [1.0]}),
        "alpha_search": pl.DataFrame({"alpha": [0.1], "n": [1], "mae": [1.0]}),
        "input_panel": panel,
    }
    fingerprint = stable_hash(panel.sort("local_date", "local_hour"))
    metadata = {"zone": "DE-LU", "input_sha256": fingerprint}
    snapshot.save(SimpleNamespace(metadata=lambda: dict(metadata), **tables))


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


def price_series(start: dt.datetime, days: int) -> pl.DataFrame:
    """`days` of clean, continuous clock-hour DE-LU prices from `start`."""
    stamps = pl.datetime_range(
        start, start + dt.timedelta(days=days), "1h", time_zone="UTC", eager=True, closed="left"
    )
    hours = stamps.len()
    return pl.DataFrame(
        {
            "zone": ["DE-LU"] * hours,
            "ts_utc": stamps,
            "resolution_min": [60] * hours,
            "price": [30.0 + (i % 24) + 0.1 * (i // 24) for i in range(hours)],
            "currency": ["EUR"] * hours,
            "source": ["test"] * hours,
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


def test_export_writes_the_site_tables(populated: Path, release: None, tmp_path: Path) -> None:
    destination = tmp_path / "site-data"

    result = runner.invoke(app, ["export", "--output", str(destination)])

    assert result.exit_code == 0, result.exception
    assert (destination / "zones.json").exists()
    assert (destination / "daily_prices.parquet").exists()
    assert (destination / "battery_sensitivities.parquet").exists()


def test_export_check_passes_when_the_tables_match(
    populated: Path, release: None, tmp_path: Path
) -> None:
    destination = tmp_path / "site-data"
    assert runner.invoke(app, ["export", "--output", str(destination)]).exit_code == 0

    result = runner.invoke(app, ["export", "--check", "--output", str(destination)])

    assert result.exit_code == 0


def test_export_check_exits_non_zero_when_the_tables_are_stale(
    populated: Path, release: None, tmp_path: Path
) -> None:
    """CI fails the build on this, which is what stops a stale site shipping.

    Ingesting without re-exporting leaves the committed site tables describing
    data that no longer matches the store. The first export must succeed, or a
    failed export would make this pass for the wrong reason.
    """
    destination = tmp_path / "site-data"
    assert runner.invoke(app, ["export", "--output", str(destination)]).exit_code == 0

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


def test_reconcile_empty_ledger_is_a_noop(tmp_path: Path) -> None:
    result = runner.invoke(app, ["reconcile", "--zone", "DE-LU", "--output", str(tmp_path)])

    assert result.exit_code == 0
    assert "no issue records" in result.stdout.lower()


# --- issue, forecast-attempt and battery (issuance provenance) --------------


def test_forecast_attempt_start_finish_and_report_round_trip(tmp_path: Path) -> None:
    start = runner.invoke(
        app,
        [
            "forecast-attempt",
            "start",
            "--attempt-id",
            "att-1",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--origin",
            "schedule",
            "--delivery-date",
            "2026-07-01",
            "--output",
            str(tmp_path),
        ],
    )
    assert start.exit_code == 0, start.stdout
    assert "started" in start.stdout.lower()

    finish = runner.invoke(
        app,
        [
            "forecast-attempt",
            "finish",
            "--attempt-id",
            "att-1",
            "--status",
            "failed",
            "--output",
            str(tmp_path),
        ],
    )
    assert finish.exit_code == 0, finish.stdout
    assert "failed" in finish.stdout.lower()

    report = runner.invoke(
        app,
        [
            "forecast-attempt",
            "report",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--start-date",
            "2026-07-01",
            "--end-date",
            "2026-07-01",
            "--output",
            str(tmp_path),
        ],
    )
    assert report.exit_code == 0, report.stdout
    assert "failed" in report.stdout.lower()
    assert "eligible deliveries: 0/1" in report.stdout.lower()


def test_issue_registers_and_finalizes_an_abstained_attempt_on_insufficient_history(
    populated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`populated` has only one day of prices, so every clock hour of the next
    day must abstain rather than fabricate a forecast, and the attempt started
    for the run must be finalized as abstained rather than left open. The
    clock is pinned pre-gate on the issue day so the run does not instead fail
    as late/outside its issue window for reasons unrelated to this test."""
    monkeypatch.setattr(
        "gpa.forecast.ledger.now_utc", lambda: dt.datetime(2026, 6, 1, 8, tzinfo=dt.UTC)
    )
    ledger_root = tmp_path / "issues"

    result = runner.invoke(
        app,
        [
            "issue",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--delivery-date",
            "2026-06-02",
            "--attempt-id",
            "att-abstain",
            "--output",
            str(ledger_root),
        ],
    )

    assert result.exit_code == 1
    assert "abstained" in result.stdout.lower()

    report = runner.invoke(
        app,
        [
            "forecast-attempt",
            "report",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--start-date",
            "2026-06-02",
            "--end-date",
            "2026-06-02",
            "--output",
            str(ledger_root),
        ],
    )
    assert report.exit_code == 0, report.stdout
    assert "abstained" in report.stdout.lower()
    assert "eligible deliveries: 0/1" in report.stdout.lower()


def test_issue_inherits_delivery_date_from_a_pre_started_attempt(
    populated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gpa.forecast.ledger.now_utc", lambda: dt.datetime(2026, 6, 1, 8, tzinfo=dt.UTC)
    )
    ledger_root = tmp_path / "issues"
    start = runner.invoke(
        app,
        [
            "forecast-attempt",
            "start",
            "--attempt-id",
            "att-reuse",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--origin",
            "workflow_dispatch",
            "--delivery-date",
            "2026-06-02",
            "--output",
            str(ledger_root),
        ],
    )
    assert start.exit_code == 0, start.stdout

    result = runner.invoke(
        app,
        [
            "issue",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--attempt-id",
            "att-reuse",
            "--output",
            str(ledger_root),
        ],
    )

    assert result.exit_code == 1
    assert "2026-06-02" in result.stdout
    assert "abstained" in result.stdout.lower()


def test_issue_reconcile_and_battery_agree_on_the_canonical_forecast(
    temporary_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end CLI happy path: enough clean history that Ridge does not
    abstain, a full day settled, and battery reading it back through the same
    canonical selection reconcile and issue rely on."""
    monkeypatch.setattr("gpa.forecast.provenance.MIN_TRAIN_ROWS", 5)
    monkeypatch.setattr(
        "gpa.forecast.ledger.now_utc", lambda: dt.datetime(2026, 6, 30, 8, tzinfo=dt.UTC)
    )
    delivery = dt.date(2026, 7, 1)
    history_start = dt.datetime.combine(delivery - dt.timedelta(days=35), dt.time(), dt.UTC)
    store.write(price_series(history_start, days=36), "price")
    ledger_root = tmp_path / "issues"

    issued = runner.invoke(
        app,
        [
            "issue",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--delivery-date",
            str(delivery),
            "--attempt-id",
            "att-ok",
            "--output",
            str(ledger_root),
        ],
    )
    assert issued.exit_code == 0, issued.stdout
    assert "issued" in issued.stdout.lower()

    reconciled = runner.invoke(app, ["reconcile", "--zone", "DE-LU", "--output", str(ledger_root)])
    assert reconciled.exit_code == 0, reconciled.stdout
    assert "scored=24" in reconciled.stdout.lower()

    battery_result = runner.invoke(
        app, ["battery", "--zone", "DE-LU", "--ledger-root", str(ledger_root)]
    )
    assert battery_result.exit_code == 0, battery_result.stdout
    assert "ridge" in battery_result.stdout.lower()


def test_a_same_morning_ingest_lets_issue_forecast_every_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduled order: refresh, then issue before the gate. With prices
    capped at the refresh instant, D-1 was incomplete and every hour abstained."""
    from gpa import pipeline
    from tests.test_pipeline import PublishedPrices

    now = dt.datetime(2026, 6, 30, 8, tzinfo=dt.UTC)
    monkeypatch.setattr(pipeline, "now_utc", lambda: now)
    monkeypatch.setattr(pipeline, "get_source", lambda name: PublishedPrices(now))
    monkeypatch.setattr("gpa.forecast.ledger.now_utc", lambda: now)
    monkeypatch.setattr("gpa.forecast.provenance.MIN_TRAIN_ROWS", 5)
    ledger_root = tmp_path / "ledger"

    ingested = runner.invoke(
        app, ["ingest", "--zone", "DE-LU", "--dataset", "price", "--days", "40"]
    )
    assert ingested.exit_code == 0, ingested.stdout
    assert store.last_ingested("price", "DE-LU") == dt.datetime(2026, 6, 30, 21, tzinfo=dt.UTC)

    issued = runner.invoke(
        app,
        [
            "issue",
            "--zone",
            "DE-LU",
            "--model",
            "ridge",
            "--delivery-date",
            "2026-07-01",
            "--attempt-id",
            "att-same-morning",
            "--output",
            str(ledger_root),
        ],
    )
    assert issued.exit_code == 0, issued.stdout
    assert "issued, 24 forecasts, 0 abstentions" in issued.stdout


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
