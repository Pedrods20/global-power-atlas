"""Immutable retrospective input and result snapshots, independent of daily ingestion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

from gpa.forecast.backtest import BacktestResult

ROOT = Path(__file__).resolve().parents[3] / "data" / "experiments"
CURRENT = ROOT / "current.json"
TABLES = ("predictions", "scores", "daily", "coefficients", "alpha_search", "input_panel")


def save(result: BacktestResult) -> Path:
    """Create a new content-addressed run; never rewrite an existing experiment."""
    metadata = result.metadata()
    source_hash = hashlib.sha256()
    sources = Path(__file__).parent
    for path in sorted(sources.glob("*.py")):
        source_hash.update(path.name.encode())
        source_hash.update(path.read_bytes())
    metadata["source_sha256"] = source_hash.hexdigest()
    metadata["alpha_search"] = result.alpha_search.to_dicts()
    identifier = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()[:20]
    path = ROOT / identifier
    if path.exists():
        raise FileExistsError(f"experiment {identifier} already exists")
    path.mkdir(parents=True)
    checksums = {}
    for name in TABLES:
        file = path / f"{name}.parquet"
        getattr(result, name).write_parquet(file, compression="zstd")
        checksums[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    metadata["experiment_id"] = identifier
    metadata["checksums"] = checksums
    (path / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    CURRENT.write_text(json.dumps({"experiment_id": identifier}, indent=2), encoding="utf-8")
    return path


def read() -> tuple[dict[str, object], dict[str, pl.DataFrame]] | None:
    if not CURRENT.exists():
        return None
    identifier = json.loads(CURRENT.read_text(encoding="utf-8"))["experiment_id"]
    if (
        not isinstance(identifier, str)
        or len(identifier) != 20
        or any(c not in "0123456789abcdef" for c in identifier)
    ):
        raise ValueError("invalid experiment identifier")
    path = ROOT / identifier
    metadata = json.loads((path / "run.json").read_text(encoding="utf-8"))
    tables = {}
    for name in TABLES:
        file = path / f"{name}.parquet"
        if hashlib.sha256(file.read_bytes()).hexdigest() != metadata["checksums"][file.name]:
            raise ValueError(f"modified experiment artifact: {file}")
        tables[name] = pl.read_parquet(file)
    actual_hash = hashlib.sha256(
        tables["input_panel"].sort("local_date", "local_hour").write_json().encode()
    ).hexdigest()
    if actual_hash != metadata["input_sha256"]:
        raise ValueError("input snapshot does not match recorded fingerprint")
    return metadata, tables
