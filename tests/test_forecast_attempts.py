"""Operational denominators include failed, abstained and entirely missing runs."""

import datetime as dt
import json

import polars as pl
import pytest
from typer.testing import CliRunner

from gpa.cli import app
from gpa.forecast import attempts, ledger, provenance
from tests.test_ledger import DAY, STAMP, full_panel, record


def start(root, identifier="test-1", **kwargs):
    return attempts.start(
        root, identifier, zone="DE-LU", model="ridge", delivery_date=DAY, started_at=STAMP, **kwargs
    )


def test_failed_attempt_remains_in_the_operational_denominator(tmp_path):
    start(tmp_path, origin="schedule")
    attempts.finish(
        tmp_path, "test-1", status="failed", error_type="UpstreamError", completed_at=STAMP
    )
    report = attempts.report(tmp_path, start_date=DAY, end_date=DAY + dt.timedelta(days=1))
    assert report["status"].to_list() == ["failed", "missing_attempt"]
    assert report["attempts"].to_list() == [1, 0]
    assert report["eligible"].to_list() == [False, False]


def test_workflow_fallback_does_not_replace_a_completed_attempt(tmp_path):
    start(tmp_path)
    attempts.finish(tmp_path, "test-1", status="abstained", completed_at=STAMP)
    attempts.finish(tmp_path, "test-1", status="failed", completed_at=STAMP, if_open=True)
    assert attempts.read(tmp_path, "test-1")["status"] == "abstained"
    with pytest.raises(ValueError, match="completed"):
        attempts.finish(tmp_path, "test-1", status="issued", completed_at=STAMP)


def test_started_but_interrupted_attempt_is_visible(tmp_path):
    start(tmp_path)
    row = attempts.report(tmp_path, start_date=DAY, end_date=DAY).row(0, named=True)
    assert row["status"] == "incomplete_attempt"
    assert not row["eligible"]


def test_success_requires_verified_issuance_not_a_claim_in_a_log(tmp_path):
    start(tmp_path)
    attempts.finish(tmp_path, "test-1", status="issued", issue_id="a" * 64, completed_at=STAMP)
    result = attempts.report(tmp_path, start_date=DAY, end_date=DAY)
    assert not result["eligible"].item()
    assert result["status"].item() == "unverified_issue"


def test_valid_issue_and_retry_keep_one_scheduled_day(tmp_path):
    start(tmp_path, origin="schedule")
    first = record(tmp_path)
    attempts.finish(
        tmp_path, "test-1", status="issued", issue_id=first["issue_id"][0], completed_at=STAMP
    )
    start(tmp_path, "retry")
    later = record(tmp_path, stamp=STAMP.replace(hour=10))
    attempts.finish(
        tmp_path,
        "retry",
        status="issued",
        issue_id=later["issue_id"][0],
        completed_at=STAMP.replace(hour=10),
    )
    row = attempts.report(tmp_path, start_date=DAY, end_date=DAY).row(0, named=True)
    assert row["attempts"] == 2
    assert row["scheduled_attempts"] == 1
    assert row["eligible"]
    assert row["issue_id"] == first["issue_id"][0]


def test_attempt_paths_and_completion_clock_are_validated(tmp_path):
    with pytest.raises(ValueError, match="identifier"):
        start(tmp_path, "../escape")
    start(tmp_path)
    with pytest.raises(ValueError, match="clock"):
        attempts.finish(
            tmp_path, "test-1", status="failed", completed_at=STAMP - dt.timedelta(seconds=1)
        )


def test_cli_persists_failure_before_input_loading(tmp_path, monkeypatch):
    from gpa import store

    monkeypatch.setattr(ledger, "now_utc", lambda: STAMP)

    def unavailable(*args, **kwargs):
        raise RuntimeError("secret-looking-text-should-not-be-persisted")

    monkeypatch.setattr(store, "read", unavailable)
    result = CliRunner().invoke(
        app,
        [
            "issue",
            "--delivery-date",
            str(DAY),
            "--output",
            str(tmp_path),
            "--attempt-id",
            "early-failure",
        ],
    )
    assert result.exit_code == 1
    event = attempts.read(tmp_path, "early-failure")
    assert event["status"] == "failed"
    assert event["error_type"] == "RuntimeError"
    assert "secret-looking" not in json.dumps(event)


def test_cli_persists_all_abstentions_and_nonzero_exit(tmp_path, monkeypatch):
    from gpa import store
    from gpa.forecast import panel

    monkeypatch.setattr(ledger, "now_utc", lambda: STAMP)
    monkeypatch.setattr(store, "read", lambda *args, **kwargs: pl.DataFrame())
    monkeypatch.setattr(panel, "build_panel", lambda *args, **kwargs: full_panel())
    # The frozen 270-row training floor exceeds this small fixture's history.
    result = CliRunner().invoke(
        app,
        [
            "issue",
            "--delivery-date",
            str(DAY),
            "--output",
            str(tmp_path),
            "--attempt-id",
            "abstention",
        ],
    )
    assert result.exit_code == 1
    assert ledger.read(root=tmp_path).height == 24
    assert attempts.read(tmp_path, "abstention")["status"] == "abstained"


def test_frozen_configuration_matches_documented_ridge_selection():
    assert provenance.default_model("ridge").alpha == 0.1
    assert provenance.MIN_TRAIN_ROWS == 270


def test_cli_attempt_lifecycle_and_missing_slot_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "now_utc", lambda: STAMP)
    runner = CliRunner()
    common = ["--output", str(tmp_path)]
    one = runner.invoke(
        app,
        [
            "forecast-attempt",
            "start",
            "--attempt-id",
            "workflow",
            "--delivery-date",
            str(DAY),
            *common,
        ],
    )
    assert one.exit_code == 0, one.output
    two = runner.invoke(
        app,
        [
            "forecast-attempt",
            "finish",
            "--attempt-id",
            "workflow",
            "--status",
            "failed",
            "--if-open",
            *common,
        ],
    )
    assert two.exit_code == 0, two.output
    report = runner.invoke(
        app,
        [
            "forecast-attempt",
            "report",
            "--start-date",
            str(DAY),
            "--end-date",
            str(DAY + dt.timedelta(days=1)),
            *common,
        ],
    )
    assert report.exit_code == 0, report.output
    assert "missing_attempt" in report.output
