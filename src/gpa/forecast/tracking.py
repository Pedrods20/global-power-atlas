"""Explicit local MLflow tracking; no service, account or remote upload needed."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import platform
import subprocess
import tempfile
from pathlib import Path

from gpa.forecast.backtest import BacktestResult
from gpa.forecast.boosting import LightGBM


def track_result(result: BacktestResult, root: Path) -> str:
    """Log one complete comparison, its input panel and source snapshot.

    SQLite and artifacts are placed under the explicitly selected local root.
    A failed artifact/metric write marks the run FAILED and propagates the error.
    """
    try:
        mlflow = importlib.import_module("mlflow")
    except ImportError as exc:
        raise RuntimeError('Install tracking support with pip install -e ".[tracking]"') from exc
    destination = root.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    client = mlflow.tracking.MlflowClient(
        tracking_uri=f"sqlite:///{(destination / 'mlflow.db').as_posix()}"
    )
    experiment_name = f"gpa-{result.zone.code}-retrospective"
    experiment = client.get_experiment_by_name(experiment_name)
    experiment_id = (
        experiment.experiment_id
        if experiment is not None
        else client.create_experiment(
            experiment_name, artifact_location=(destination / "artifacts").as_uri()
        )
    )
    repo = Path(__file__).resolve().parents[3]
    source_files = sorted((repo / "src" / "gpa").rglob("*.py"))
    source_files.append(repo / "pyproject.toml")
    source_hash = hashlib.sha256()
    for path in source_files:
        source_hash.update(path.relative_to(repo).as_posix().encode())
        source_hash.update(path.read_bytes())
    tags = {
        "mlflow.runName": f"{result.zone.code} {result.test_start} to {result.test_end}",
        "evaluation": "retrospective",
        "input_sha256": result.input_sha256,
        "source_sha256": source_hash.hexdigest(),
        "git_commit": _git(repo, "rev-parse", "HEAD"),
        "git_dirty": str(bool(_git(repo, "status", "--porcelain"))).lower(),
    }
    run_id = str(client.create_run(experiment_id, tags=tags).info.run_id)
    try:
        metadata = result.metadata()
        for key in (
            "zone",
            "alpha",
            "min_train_days",
            "validation_days",
            "window",
            "test_start",
            "test_end",
        ):
            client.log_param(run_id, key, str(metadata[key]))
        client.log_param(run_id, "models", ",".join(result.models))
        for key, value in {**LightGBM().parameters(), "num_boost_round": 100}.items():
            client.log_param(run_id, f"lightgbm.{key}", str(value))
        for row in result.scores.iter_rows(named=True):
            for metric in (
                "mae",
                "rmse",
                "bias",
                "mean_pinball",
                "skill_vs_best_baseline_pct",
                "n",
                "n_interval",
                "interval_coverage_pct",
                "interval_mean_width",
            ):
                value = row[metric]
                if value is not None and math.isfinite(float(value)):
                    client.log_metric(
                        run_id,
                        f"{row['model']}.{row['scope']}.{row['bucket']}.{metric}",
                        float(value),
                    )
        with tempfile.TemporaryDirectory(prefix="gpa-tracking-") as directory:
            output = Path(directory)
            (output / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            versions = {
                name: importlib.metadata.version(name)
                for name in ("lightgbm", "numpy", "polars", "scikit-learn", "mlflow")
            }
            versions.update(python=platform.python_version(), platform=platform.platform())
            (output / "environment.json").write_text(
                json.dumps(versions, indent=2), encoding="utf-8"
            )
            for name in (
                "predictions",
                "scores",
                "daily",
                "coefficients",
                "alpha_search",
                "input_panel",
            ):
                getattr(result, name).write_parquet(output / f"{name}.parquet")
            for path in source_files:
                target = output / "source" / path.relative_to(repo)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
            client.log_artifacts(run_id, str(output))
        client.set_terminated(run_id, status="FINISHED")
    except Exception:
        client.set_terminated(run_id, status="FAILED")
        raise
    return run_id


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
