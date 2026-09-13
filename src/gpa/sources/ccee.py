"""CCEE hourly PLD by submarket. Never label a submarket price as SIN-wide.

Metadata: https://dadosabertos.ccee.org.br/dataset/pld_horario
Local official CSV downloads can be placed in GPA_CCEE_IMPORT_DIR when the
provider blocks automated access. No observations are synthesised.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from pathlib import Path
from typing import Any

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import UpstreamError, fetch_json, fetch_text
from gpa.zones import Zone

__all__ = ["CceeSource", "parse_ccee"]


class CceeSource:
    name: str = "ccee"
    datasets: tuple[str, ...] = ("price",)
    max_window_days: int | None = None

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset != "price":
            raise ValueError("CCEE PLD supplies only price")
        directory = os.environ.get("GPA_CCEE_IMPORT_DIR")
        frames: list[pl.DataFrame] = []
        if directory:
            for file in sorted(Path(directory).glob("pld_horario_*.csv")):
                frames.append(
                    parse_ccee(
                        file.read_text(encoding="utf-8-sig"),
                        zone.code,
                        zone.source_keys["ccee_submarket"],
                    )
                )
        else:
            payload = fetch_json(
                "https://dadosabertos.ccee.org.br/api/3/action/package_show",
                params={"id": "pld_horario"},
            )
            resources: list[dict[str, Any]] = payload.get("result", {}).get("resources", [])
            for year in range(start.year, end.year + 1):
                resource = next(
                    (r for r in resources if r.get("name", "").lower() == f"pld_horario_{year}"),
                    None,
                )
                if not resource:
                    continue
                raw = fetch_text(resource["url"])
                if raw:
                    frames.append(parse_ccee(raw, zone.code, zone.source_keys["ccee_submarket"]))
        if not frames:
            raise UpstreamError(
                "No CCEE hourly files available; import official CSVs with GPA_CCEE_IMPORT_DIR"
            )
        return (
            pl.concat(frames)
            .filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
            .unique(["zone", "ts_utc"])
            .sort("ts_utc")
        )


def parse_ccee(text: str, zone_code: str, submarket: str) -> pl.DataFrame:
    """Parse one hourly PLD CSV into canonical price rows for one submarket.

    Exposed for tests, which run it against a recorded fixture rather than the
    official download.
    """
    separator = ";" if ";" in text.splitlines()[0] else ","
    frame = pl.read_csv(
        io.StringIO(text.lstrip("\ufeff")), separator=separator, infer_schema_length=0
    )
    required = {"MES_REFERENCIA", "DIA", "HORA", "SUBMERCADO", "PLD_HORA"}
    if not required.issubset(frame.columns):
        raise UpstreamError(f"CCEE CSV missing columns: {sorted(required - set(frame.columns))}")
    frame = frame.filter(
        pl.col("SUBMERCADO").str.strip_chars().str.to_uppercase() == submarket.upper()
    )
    if frame.is_empty():
        return empty_frame("price")
    hour = pl.col("HORA").cast(pl.Int32)
    if frame.filter(~hour.is_between(0, 23)).height:
        raise UpstreamError("CCEE HORA must be 0-23; check the provider's time convention")
    month = pl.col("MES_REFERENCIA").cast(pl.Utf8).str.zfill(6)
    day = pl.col("DIA").cast(pl.Utf8).str.zfill(2)
    return (
        frame.select(
            pl.lit(zone_code).alias("zone"),
            (
                (month + day)
                .str.to_datetime("%Y%m%d", strict=False)
                .dt.replace_time_zone("America/Sao_Paulo")
                + pl.duration(hours=hour)
            )
            .dt.convert_time_zone("UTC")
            .cast(UTC_DATETIME)
            .alias("ts_utc"),
            pl.lit(60, dtype=pl.Int16).alias("resolution_min"),
            pl.col("PLD_HORA")
            .str.replace(",", ".", literal=True)
            .cast(pl.Float64, strict=False)
            .alias("price"),
            pl.lit("BRL").alias("currency"),
            pl.lit("ccee").alias("source"),
        )
        .drop_nulls("price")
        .sort("ts_utc")
    )
