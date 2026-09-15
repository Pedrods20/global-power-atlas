"""DE-LU installed capacity: a period series, not a market interval series.

A capacity figure describes an asset stock at a point in time, not average
power over a settlement interval, and a "planned" figure is a government
target, not a measurement. Neither fits `gpa.schema.SCHEMAS`/`gpa.store`'s
`(zone, ts_utc, resolution_min)` contract built for price/load/generation/
fundamentals, so this module keeps its own small schema and its own storage
path under `data/reference/`, deliberately outside `data/curated/`.

Rows come from `gpa.sources.energy_charts.EnergyChartsSource.fetch_installed_power`,
which does the provider-specific parsing; this module only knows how to store
and read the result it produces.
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from gpa.store import atomic_parquet

__all__ = ["CAPACITY_COLUMNS", "capacity_path", "read", "reference_root", "write"]

_ENV_ROOT = "GPA_REFERENCE_ROOT"

CAPACITY_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
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
"""One row per (country, time_step, technology, period).

``period`` is the provider's raw label ("2024" or "2024-03"); ``as_of`` is
that same period's *end* as a real date, matching the endpoint's own
documented convention that a value describes the stock at the end of the
labelled period. ``technology`` keeps the provider's raw name rather than
this project's canonical fuel taxonomy, because a "planned" and a realised
series for the same technology must stay distinct rows, not collapse into
one fuel bucket the way generation does. ``is_planned`` makes that
distinction queryable without parsing the name.
"""


def reference_root() -> Path:
    """Root of the reference-data tree.

    Defaults to ``data/reference`` beside the package's repository root, and
    can be redirected with the ``GPA_REFERENCE_ROOT`` environment variable so
    tests can point at a temporary tree, mirroring ``store.curated_root``.
    """
    override = os.environ.get(_ENV_ROOT)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "reference"


def capacity_path() -> Path:
    """Path of the one Parquet file this module reads and writes."""
    return reference_root() / "capacity" / "installed_power.parquet"


def write(frame: pl.DataFrame) -> Path:
    """Replace the stored capacity series wholesale with ``frame``.

    Unlike ``store.write``'s upsert, this does not merge with what is already
    on disk. The provider always returns its complete current series in one
    response, a "planned" figure can legitimately disappear from a later
    response when a target is revised, and there is no natural key across
    ``time_step``/``technology``/``period`` that a partial re-fetch could
    safely merge into. Re-deriving the whole file from the latest fetch is
    simpler and cannot accumulate a row a policy revision removed.

    Args:
        frame: Rows matching :data:`CAPACITY_COLUMNS`.

    Returns:
        The path written. Unchanged, and nothing written, if ``frame`` is empty.
    """
    path = capacity_path()
    if frame.is_empty():
        return path
    ordered = frame.select(list(CAPACITY_COLUMNS)).sort(
        ["country", "time_step", "technology", "as_of"]
    )
    atomic_parquet(ordered, path)
    return path


def read() -> pl.DataFrame:
    """Read the stored capacity series.

    Returns:
        An empty frame with :data:`CAPACITY_COLUMNS` if nothing has been
        fetched yet, so callers never have to special-case a missing file.
    """
    path = capacity_path()
    if not path.exists():
        return pl.DataFrame(schema=CAPACITY_COLUMNS)
    return pl.read_parquet(path)
