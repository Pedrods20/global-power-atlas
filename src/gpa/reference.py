"""Reference tables outside the interval store, replaced wholesale on every write."""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from gpa.store import atomic_parquet

CAPACITY_SCHEMA = pl.Schema(
    {
        "country": pl.String,
        "time_step": pl.String,
        "period": pl.String,
        "as_of": pl.Date,
        "technology": pl.String,
        "value": pl.Float64,
        "unit": pl.String,
        "is_planned": pl.Boolean,
        "source": pl.String,
    }
)
"""Installed capacity: ``period`` is the provider's label, ``as_of`` the date it ends.

A "planned" series is a policy target, not a measurement, so it is a separate row.
"""

ABLATION_SCHEMA = pl.Schema(
    {
        "zone": pl.String,
        "model": pl.String,
        "include_fundamentals": pl.Boolean,
        "n": pl.Int64,
        "mae": pl.Float64,
        "rmse": pl.Float64,
        "skill_vs_best_baseline_pct": pl.Float64,
        "test_start": pl.String,
        "test_end": pl.String,
    }
)
"""Overall scores with and without fundamentals: a labelled diagnostic, never a release."""

_TABLES = {
    "capacity": (
        "capacity/installed_power.parquet",
        CAPACITY_SCHEMA,
        ["country", "time_step", "technology", "as_of"],
    ),
    "fundamentals_ablation": (
        "fundamentals_ablation/scores.parquet",
        ABLATION_SCHEMA,
        ["model", "include_fundamentals"],
    ),
}


def path(name: str) -> Path:
    default = Path(__file__).resolve().parents[2] / "data" / "reference"
    return Path(os.environ.get("GPA_REFERENCE_ROOT") or default) / _TABLES[name][0]


def read(name: str) -> pl.DataFrame:
    file = path(name)
    return pl.read_parquet(file) if file.exists() else pl.DataFrame(schema=_TABLES[name][1])


def write(name: str, frame: pl.DataFrame) -> Path:
    """Replace the table: a provider re-serves its full series, and a revised target may vanish."""
    _, schema, keys = _TABLES[name]
    file = path(name)
    if not frame.is_empty():
        atomic_parquet(frame.select(schema.names()).sort(keys), file)
    return file
