"""Partitioned Parquet store: ``data/curated/<dataset>/zone=<ZONE>/<YYYY-MM>.parquet``.

Monthly files keep a daily run to one small rewrite. Writes are upserts on each
dataset's natural key, because providers restate observations after publishing.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import tempfile
from pathlib import Path

import polars as pl

from gpa.schema import SCHEMAS, empty_frame, validate

__all__ = [
    "DATASETS",
    "atomic_parquet",
    "atomic_write",
    "coverage",
    "curated_root",
    "dataset_dir",
    "last_ingested",
    "read",
    "write",
]

DATASETS: tuple[str, ...] = tuple(SCHEMAS)

_KEYS: dict[str, list[str]] = {
    "price": ["zone", "ts_utc"],
    "load": ["zone", "ts_utc"],
    "generation": ["zone", "ts_utc", "fuel"],
    "fundamentals": ["zone", "ts_utc", "series"],
}


def curated_root() -> Path:
    """``data/curated`` beside the package, or ``GPA_DATA_ROOT`` for tests."""
    default = Path(__file__).resolve().parents[2] / "data" / "curated"
    return Path(os.environ.get("GPA_DATA_ROOT") or default)


def dataset_dir(dataset: str) -> Path:
    if dataset not in SCHEMAS:
        raise KeyError(f"unknown dataset {dataset!r}; valid datasets are: {', '.join(SCHEMAS)}")
    return curated_root() / dataset


def atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` through a temporary file beside it, so it is never left truncated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".gpa-", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_parquet(frame: pl.DataFrame, path: Path) -> None:
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd", statistics=True)
    atomic_write(path, buffer.getvalue())


def write(frame: pl.DataFrame, dataset: str, *, validate_first: bool = True) -> list[Path]:
    """Upsert ``frame``; the incoming row wins on the natural key. Returns the files touched.

    UTC-month partitioning is a file layout only; every analysis keys on local time.
    """
    directory = dataset_dir(dataset)
    if frame.is_empty():
        return []
    if validate_first:
        frame = validate(frame, dataset)
    key = _KEYS[dataset]
    written = []
    month = pl.col("ts_utc").dt.strftime("%Y-%m").alias("_month")
    for (zone, stamp), chunk in frame.with_columns(month).group_by(
        "zone", "_month", maintain_order=True
    ):
        path = directory / f"zone={zone}" / f"{stamp}.parquet"
        incoming = chunk.drop("_month")
        merged = (
            pl.concat([pl.read_parquet(path), incoming], how="vertical_relaxed")
            if path.exists()
            else incoming
        )
        atomic_parquet(merged.unique(subset=key, keep="last").sort(key), path)
        written.append(path)
    return sorted(written)


def last_ingested(dataset: str, zone: str) -> dt.datetime | None:
    """The latest stored instant: the ingestion checkpoint, read from the files themselves."""
    files = sorted((dataset_dir(dataset) / f"zone={zone}").glob("*.parquet"))
    if not files:
        return None
    latest = pl.scan_parquet(files).select(pl.col("ts_utc").max()).collect().item()
    return latest.astimezone(dt.UTC) if isinstance(latest, dt.datetime) else None


def read(dataset: str, zone: str | None = None) -> pl.DataFrame:
    """One zone's rows, or every zone's, sorted by the natural key; empty keeps the schema."""
    paths = sorted(dataset_dir(dataset).glob(f"zone={zone or '*'}/*.parquet"))
    if not paths:
        return empty_frame(dataset)
    return pl.scan_parquet(paths).collect().sort(_KEYS[dataset])


def coverage() -> pl.DataFrame:
    """Rows, first and last instant, partitions and bytes per dataset and zone."""
    rows = []
    for dataset in DATASETS:
        for zone_dir in sorted(dataset_dir(dataset).glob("zone=*")):
            files = sorted(zone_dir.glob("*.parquet"))
            if not files:
                continue
            stamps = pl.scan_parquet(files).select("ts_utc").collect()["ts_utc"]
            rows.append(
                {
                    "dataset": dataset,
                    "zone": zone_dir.name.removeprefix("zone="),
                    "rows": stamps.len(),
                    "first_ts_utc": stamps.min(),
                    "last_ts_utc": stamps.max(),
                    "partitions": len(files),
                    "bytes": sum(file.stat().st_size for file in files),
                }
            )
    schema = pl.Schema(
        {
            "dataset": pl.String(),
            "zone": pl.String(),
            "rows": pl.Int64(),
            "first_ts_utc": pl.Datetime("us", "UTC"),
            "last_ts_utc": pl.Datetime("us", "UTC"),
            "partitions": pl.Int64(),
            "bytes": pl.Int64(),
        }
    )
    return pl.DataFrame(rows, schema=schema).sort("dataset", "zone")
