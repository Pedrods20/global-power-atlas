"""Content-addressed experiment snapshots: round-trip, and each way it refuses
bad state rather than silently trusting it."""

from __future__ import annotations

import json

import pytest

from gpa.forecast import backtest, snapshot
from tests.test_forecast import ZONE, small_panel


@pytest.fixture
def result():
    return backtest.run(ZONE, panel=small_panel(), min_train_days=30, validation_days=5, alpha=0.1)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "CURRENT", tmp_path / "current.json")


def test_save_then_read_round_trips_metadata_and_tables(result):
    path = snapshot.save(result)
    assert path.name == json.loads(snapshot.CURRENT.read_text())["experiment_id"]

    metadata, tables = snapshot.read()
    assert metadata["zone"] == "DE-LU"
    assert metadata["input_sha256"] == result.input_sha256
    assert metadata["experiment_id"] == path.name
    assert set(tables) == set(snapshot.TABLES)
    assert tables["scores"].equals(result.scores)


def test_save_refuses_to_overwrite_an_existing_experiment(result):
    snapshot.save(result)
    with pytest.raises(FileExistsError):
        snapshot.save(result)


def test_read_rejects_a_tampered_artifact(result):
    path = snapshot.save(result)
    (path / "scores.parquet").write_bytes(b"not actually parquet")
    with pytest.raises(ValueError, match="modified experiment artifact"):
        snapshot.read()


def test_read_rejects_a_fingerprint_that_no_longer_matches_its_input_panel(result):
    path = snapshot.save(result)
    metadata = json.loads((path / "run.json").read_text())
    metadata["input_sha256"] = "0" * 64
    (path / "run.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="does not match recorded fingerprint"):
        snapshot.read()


def test_read_rejects_a_malformed_current_pointer(tmp_path):
    snapshot.CURRENT.write_text(json.dumps({"experiment_id": "../escape"}))
    with pytest.raises(ValueError, match="invalid experiment identifier"):
        snapshot.read()


def test_read_is_none_when_nothing_has_been_saved():
    assert snapshot.read() is None
