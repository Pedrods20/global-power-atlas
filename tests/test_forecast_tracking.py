"""Exercise MLflow against a temporary local SQLite store and real artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

from gpa.cli import app
from gpa.forecast import backtest, snapshot, tracking
from tests.test_forecast import ZONE, small_panel


@pytest.fixture
def result():
    return backtest.run(ZONE, panel=small_panel(), min_train_days=30, validation_days=5, alpha=0.1)


def test_mlflow_records_comparison_inputs_and_source(tmp_path, result):
    mlflow = pytest.importorskip("mlflow")
    root = tmp_path / "tracking"
    run_id = tracking.track_result(result, root)
    client = mlflow.tracking.MlflowClient(
        tracking_uri=f"sqlite:///{(root / 'mlflow.db').as_posix()}"
    )
    recorded = client.get_run(run_id)
    assert recorded.info.status == "FINISHED"
    assert recorded.data.tags["input_sha256"] == result.input_sha256
    assert recorded.data.params["lightgbm.num_boost_round"] == "100"
    assert "lightgbm.overall.all.mae" in recorded.data.metrics
    assert "ridge.overall.all.mae" in recorded.data.metrics
    artifact = Path(client.download_artifacts(run_id, "input_panel.parquet"))
    assert_frame_equal(pl.read_parquet(artifact), result.input_panel)
    metadata = json.loads(Path(client.download_artifacts(run_id, "run.json")).read_text())
    assert metadata["input_sha256"] == result.input_sha256
    assert Path(client.download_artifacts(run_id, "source/src/gpa/forecast/boosting.py")).is_file()
    assert Path(client.download_artifacts(run_id, "environment.json")).is_file()


def test_tracking_failure_is_not_reported_as_success(tmp_path, monkeypatch, result):
    mlflow = pytest.importorskip("mlflow")

    def fail(*args, **kwargs):
        raise OSError("artifact write failed")

    monkeypatch.setattr(mlflow.tracking.MlflowClient, "log_artifacts", fail)
    with pytest.raises(OSError, match="artifact write"):
        tracking.track_result(result, tmp_path)
    client = mlflow.tracking.MlflowClient(
        tracking_uri=f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}"
    )
    experiment = client.get_experiment_by_name("gpa-DE-LU-retrospective")
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1
    assert runs[0].info.status == "FAILED"


def test_tracking_dependency_is_optional(tmp_path, monkeypatch, result):
    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(tracking.importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="tracking"):
        tracking.track_result(result, tmp_path)


def test_cli_comparison_and_tracking_flag(tmp_path, monkeypatch, result):
    monkeypatch.setattr(backtest, "run", lambda *args, **kwargs: result)
    recorded = []

    def capture(run, root):
        recorded.append((run, root))
        return "local-test-run"

    monkeypatch.setattr(tracking, "track_result", capture)
    response = CliRunner().invoke(
        app, ["backtest", "--track", "--tracking-dir", str(tmp_path), "--scope", "all"]
    )
    assert response.exit_code == 0, response.exception
    assert "local-test-run" in response.stdout
    assert "lightgbm" in response.stdout
    assert "Retrospective" in response.stdout
    assert recorded == [(result, tmp_path)]


@pytest.mark.parametrize(
    "args", [["--scope", "invalid"], ["--min-train-days", "-1"], ["--validation-days", "-1"]]
)
def test_cli_rejects_bad_arguments_before_computation(args, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("backtest should not run")

    monkeypatch.setattr(backtest, "run", unexpected)
    assert CliRunner().invoke(app, ["backtest", *args]).exit_code != 0


def test_cli_save_snapshot_flag_freezes_the_run(tmp_path, monkeypatch, result):
    monkeypatch.setattr(backtest, "run", lambda *args, **kwargs: result)
    monkeypatch.setattr(snapshot, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "CURRENT", tmp_path / "current.json")
    response = CliRunner().invoke(app, ["backtest", "--save-snapshot", "--scope", "all"])
    assert response.exit_code == 0, response.exception
    assert "Snapshot saved" in response.stdout
    metadata, _ = snapshot.read()
    assert metadata["input_sha256"] == result.input_sha256


def test_cli_save_snapshot_warns_rather_than_fails_when_already_saved(
    tmp_path, monkeypatch, result
):
    monkeypatch.setattr(backtest, "run", lambda *args, **kwargs: result)
    monkeypatch.setattr(snapshot, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "CURRENT", tmp_path / "current.json")
    snapshot.save(result)
    response = CliRunner().invoke(app, ["backtest", "--save-snapshot", "--scope", "all"])
    assert response.exit_code == 0, response.exception
    assert "already exists" in response.stdout
