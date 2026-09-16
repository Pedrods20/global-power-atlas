"""The fundamentals-ablation reference artifact: a labelled diagnostic, not a snapshot.

Covers only the storage contract (``gpa.fundamentals_ablation``); the walk-forward
comparison itself reuses ``gpa.forecast.backtest.run``, already tested elsewhere.
"""

from __future__ import annotations

import polars as pl

from gpa import fundamentals_ablation


def _row(model: str, *, include_fundamentals: bool, mae: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "zone": ["DE-LU"],
            "model": [model],
            "include_fundamentals": [include_fundamentals],
            "n": [1000],
            "mae": [mae],
            "rmse": [mae * 1.3],
            "skill_vs_best_baseline_pct": [10.0],
            "test_start": ["2020-01-03"],
            "test_end": ["2026-09-12"],
        },
        schema=fundamentals_ablation.ABLATION_COLUMNS,
    )


def test_write_then_read_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    frame = pl.concat(
        [
            _row("ridge", include_fundamentals=False, mae=22.07),
            _row("ridge", include_fundamentals=True, mae=19.17),
        ]
    )
    destination = fundamentals_ablation.write(frame)
    assert destination.exists()
    result = fundamentals_ablation.read().sort("include_fundamentals")
    assert result["mae"].to_list() == [22.07, 19.17]


def test_write_replaces_rather_than_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    fundamentals_ablation.write(_row("ridge", include_fundamentals=False, mae=22.07))
    fundamentals_ablation.write(_row("lightgbm", include_fundamentals=False, mae=25.56))
    result = fundamentals_ablation.read()
    assert result["model"].to_list() == ["lightgbm"]  # the ridge row is gone, not merged


def test_read_is_empty_before_anything_is_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    result = fundamentals_ablation.read()
    assert result.is_empty()
    assert set(result.columns) == set(fundamentals_ablation.ABLATION_COLUMNS)


def test_write_of_an_empty_frame_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GPA_REFERENCE_ROOT", str(tmp_path))
    fundamentals_ablation.write(pl.DataFrame(schema=fundamentals_ablation.ABLATION_COLUMNS))
    assert not fundamentals_ablation.path().exists()
