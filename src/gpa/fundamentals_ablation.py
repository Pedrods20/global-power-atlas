"""Pre-auction fundamentals ablation: a labelled diagnostic, not the published forecast.

This compares the published information set against the same walk-forward
protocol with day-ahead load/wind/solar forecasts added, on the identical
frozen evaluation window. It deliberately never touches
:mod:`gpa.forecast.snapshot` or ``data/experiments/current.json``:
``snapshot.save()`` unconditionally repoints the published release at
whatever it just saved, so running this ablation through that path would
silently promote it -- adopting these features as a new frozen release is a
separate, undecided decision (see the roadmap), not a side effect of showing
the comparison on the site. Results live under their own small
``data/reference/`` artifact instead, produced by ``gpa fundamentals-ablation``
and read by ``gpa.export``, never by the routine walk-forward backtest.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from gpa.capacity import reference_root
from gpa.store import atomic_parquet

__all__ = ["ABLATION_COLUMNS", "path", "read", "write"]

ABLATION_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
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
"""One row per (model, include_fundamentals): the overall-scope scorecard only.

Not a full snapshot -- no predictions, coefficients or checksums -- because
this is a labelled diagnostic comparison, not a candidate release.
"""


def path() -> Path:
    """The one file this module reads and writes."""
    return reference_root() / "fundamentals_ablation" / "scores.parquet"


def write(frame: pl.DataFrame) -> Path:
    """Replace the stored ablation comparison wholesale with ``frame``."""
    destination = path()
    if frame.is_empty():
        return destination
    ordered = frame.select(list(ABLATION_COLUMNS)).sort(["model", "include_fundamentals"])
    atomic_parquet(ordered, destination)
    return destination


def read() -> pl.DataFrame:
    """Read the stored ablation comparison, or an empty frame with its contract."""
    destination = path()
    if not destination.exists():
        return pl.DataFrame(schema=ABLATION_COLUMNS)
    return pl.read_parquet(destination)
