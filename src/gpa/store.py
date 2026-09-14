"""Partitioned Parquet store with DuckDB query access.

The store is a directory of Parquet files, committed to the repository:

    data/curated/<dataset>/zone=<ZONE>/<YYYY-MM>.parquet

Partitioning by zone and month keeps each file small enough that a daily
incremental run rewrites only the current month, which in turn keeps git
history readable and the repository small. Months in the past are effectively
append-only.

Writes are upserts on the dataset's natural key. Power data is revised: providers
can restate observations, and ONS republishes its yearly file continuously.
Re-ingesting a window therefore has to replace what is
already there rather than duplicate it, and the last writer for a given key
wins.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import duckdb
import polars as pl

from gpa.schema import SCHEMAS, empty_frame, validate

__all__ = [
    "DATASETS",
    "available_months",
    "connect",
    "coverage",
    "curated_root",
    "dataset_dir",
    "partition_path",
    "read",
    "write",
]

DATASETS: tuple[str, ...] = tuple(SCHEMAS)

_KEYS: dict[str, tuple[str, ...]] = {
    "price": ("zone", "ts_utc"),
    "load": ("zone", "ts_utc"),
    "generation": ("zone", "ts_utc", "fuel"),
}
"""Natural key per dataset, used to deduplicate on upsert."""

_ENV_ROOT = "GPA_DATA_ROOT"


def curated_root() -> Path:
    """Root of the curated store.

    Defaults to ``data/curated`` beside the package's repository root, and can
    be redirected with the ``GPA_DATA_ROOT`` environment variable so that tests
    and the site build can point at a temporary or alternate tree.
    """
    override = os.environ.get(_ENV_ROOT)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "curated"


def dataset_dir(dataset: str) -> Path:
    """Directory holding every partition of ``dataset``."""
    _check_dataset(dataset)
    return curated_root() / dataset


def partition_path(dataset: str, zone: str, month: str) -> Path:
    """Path of one partition file.

    Args:
        dataset: One of :data:`DATASETS`.
        zone: Zone code.
        month: ``YYYY-MM``.
    """
    _check_dataset(dataset)
    return dataset_dir(dataset) / f"zone={zone}" / f"{month}.parquet"


def _check_dataset(dataset: str) -> None:
    if dataset not in SCHEMAS:
        valid = ", ".join(sorted(SCHEMAS))
        raise KeyError(f"unknown dataset {dataset!r}; valid datasets are: {valid}")


def _with_month(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col("ts_utc").dt.strftime("%Y-%m").alias("_month"))


def write(frame: pl.DataFrame, dataset: str, *, validate_first: bool = True) -> list[Path]:
    """Upsert ``frame`` into the store, returning the partitions touched.

    The frame is validated, split by zone and UTC month, merged with whatever
    each partition already holds, deduplicated on the dataset's natural key
    keeping the incoming row, and written back sorted.

    Partitioning uses the UTC month purely as a physical file-layout choice. It
    is never an analytical grouping; every analysis keys on market-local time
    via :mod:`gpa.calendar`.

    Args:
        frame: Rows in the canonical shape for ``dataset``.
        dataset: One of :data:`DATASETS`.
        validate_first: Run the schema contract before writing. Only turn this
            off when the caller has already validated.

    Returns:
        Paths written, in sorted order. Empty if ``frame`` is empty.
    """
    _check_dataset(dataset)
    if frame.is_empty():
        return []

    if validate_first:
        frame = validate(frame, dataset)

    key = list(_KEYS[dataset])
    written: list[Path] = []

    for (zone, month), chunk in _with_month(frame).group_by(
        ["zone", "_month"], maintain_order=True
    ):
        path = partition_path(dataset, str(zone), str(month))
        path.parent.mkdir(parents=True, exist_ok=True)

        incoming = chunk.drop("_month")
        if path.exists():
            existing = pl.read_parquet(path)
            # Incoming rows come last so that unique(keep="last") prefers them,
            # which is what makes this an upsert rather than an append.
            merged = pl.concat([existing, incoming], how="vertical_relaxed")
        else:
            merged = incoming

        merged = merged.unique(subset=key, keep="last").sort(key)
        atomic_parquet(merged, path)
        written.append(path)

    return sorted(written)


def atomic_parquet(frame: pl.DataFrame, path: Path) -> None:
    """Replace a complete partition on the same filesystem; never truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".gpa-", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        frame.write_parquet(temporary, compression="zstd", statistics=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def available_months(dataset: str, zone: str) -> list[str]:
    """Months stored for a zone, ascending, as ``YYYY-MM`` strings."""
    directory = dataset_dir(dataset) / f"zone={zone}"
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.parquet"))


def read(
    dataset: str,
    zones: str | Iterable[str] | None = None,
    *,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    columns: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Read rows from the store.

    Args:
        dataset: One of :data:`DATASETS`.
        zones: A zone code, an iterable of them, or ``None`` for all.
        start: Inclusive lower bound on ``ts_utc``. Must be timezone-aware.
        end: Exclusive upper bound on ``ts_utc``. Must be timezone-aware.
        columns: Subset of columns to return.

    Returns:
        A frame sorted by the dataset's natural key. Empty with the correct
        schema if nothing matches, so callers never have to special-case it.

    Raises:
        ValueError: If ``start`` or ``end`` is naive.
    """
    _check_dataset(dataset)

    for name, bound in (("start", start), ("end", end)):
        if bound is not None and bound.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")

    if isinstance(zones, str):
        wanted = [zones]
    elif zones is None:
        wanted = None
    else:
        wanted = list(zones)

    paths: list[Path] = []
    root = dataset_dir(dataset)
    if root.is_dir():
        for zone_dir in sorted(root.glob("zone=*")):
            code = zone_dir.name.removeprefix("zone=")
            if wanted is not None and code not in wanted:
                continue
            paths.extend(sorted(zone_dir.glob("*.parquet")))

    if not paths:
        return _empty(dataset, columns)

    lazy = pl.scan_parquet(paths)
    if start is not None:
        lazy = lazy.filter(pl.col("ts_utc") >= start)
    if end is not None:
        lazy = lazy.filter(pl.col("ts_utc") < end)
    if columns is not None:
        lazy = lazy.select(list(columns))

    frame = lazy.collect()
    sort_key = [c for c in _KEYS[dataset] if c in frame.columns]
    return frame.sort(sort_key) if sort_key else frame


def _empty(dataset: str, columns: Sequence[str] | None) -> pl.DataFrame:
    frame = empty_frame(dataset)
    return frame.select(list(columns)) if columns is not None else frame


def connect(datasets: Iterable[str] | None = None) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with one view per dataset.

    Each view is a ``read_parquet`` over the dataset's partitions with
    ``hive_partitioning`` enabled, so ``zone`` is available as a real column and
    DuckDB prunes partitions from a ``WHERE zone = ...`` clause.

    A dataset with no files on disk is skipped rather than creating a view that
    errors on first use.

    Example:
        >>> con = connect()
        >>> con.sql("SELECT zone, count(*) FROM price GROUP BY 1").fetchall()
    """
    con = duckdb.connect()
    for dataset in datasets or DATASETS:
        _check_dataset(dataset)
        root = dataset_dir(dataset)
        if not root.is_dir() or not any(root.rglob("*.parquet")):
            continue
        pattern = (root / "zone=*" / "*.parquet").as_posix()
        con.execute(
            f"CREATE VIEW {dataset} AS "
            f"SELECT * FROM read_parquet('{pattern}', hive_partitioning = true)"
        )
    return con


def coverage() -> pl.DataFrame:
    """Summarise what the store currently holds.

    Returns one row per dataset and zone with the row count, the first and last
    observed instant, and the number of monthly partitions. This is what the
    ``gpa stats`` command prints and what the site uses to show data freshness.
    """
    rows: list[dict[str, object]] = []
    for dataset in DATASETS:
        root = dataset_dir(dataset)
        if not root.is_dir():
            continue
        for zone_dir in sorted(root.glob("zone=*")):
            files = sorted(zone_dir.glob("*.parquet"))
            if not files:
                continue
            code = zone_dir.name.removeprefix("zone=")
            frame = pl.scan_parquet(files).select("ts_utc").collect()
            rows.append(
                {
                    "dataset": dataset,
                    "zone": code,
                    "rows": frame.height,
                    "first_ts_utc": frame["ts_utc"].min(),
                    "last_ts_utc": frame["ts_utc"].max(),
                    "partitions": len(files),
                    "bytes": sum(f.stat().st_size for f in files),
                }
            )

    if not rows:
        return pl.DataFrame(
            schema={
                "dataset": pl.String,
                "zone": pl.String,
                "rows": pl.Int64,
                "first_ts_utc": pl.Datetime(time_unit="us", time_zone="UTC"),
                "last_ts_utc": pl.Datetime(time_unit="us", time_zone="UTC"),
                "partitions": pl.Int64,
                "bytes": pl.Int64,
            }
        )
    return pl.DataFrame(rows).sort(["dataset", "zone"])
