"""ONS adapter for the Brazilian National Interconnected System.

ONS publishes an hourly energy balance per subsystem as one semicolon-delimited
CSV per calendar year, on a public S3 bucket with no credentials. A single file
carries both generation by technology and verified load, which is why Brazil
needs only one request per year rather than one per dataset.

Three Brazil-specific points are handled here:

Timestamps are Brasilia time, not UTC.
    ``din_instante`` is local. Brazil abolished daylight saving in 2019, so
    ``America/Sao_Paulo`` has been a constant UTC-3 since then, but files
    covering 2018 and earlier do contain the ambiguous and non-existent hours of
    a DST transition. Those are resolved explicitly rather than left to a
    library default.

The file contains both subsystems and their total.
    Rows are emitted for ``N``, ``NE``, ``S``, ``SE`` and ``SIN``, where ``SIN``
    is the sum of the other four. Mixing them would double-count, so exactly one
    is selected per zone.

Missing values are not zero.
    A blank generation field means the measurement is absent. Parsing it as zero
    understates the technology's share, which is the bug the previous
    implementation of this project shipped.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Final

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import UpstreamError, fetch_text, http_client, infer_resolution_minutes
from gpa.zones import Zone

__all__ = ["FUEL_COLUMNS", "SUBSYSTEMS", "OnsSource"]

_BASE = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset"
_BALANCE = f"{_BASE}/balanco_energia_subsistema_ho/BALANCO_ENERGIA_SUBSISTEMA_{{year}}.csv"

_TIMEZONE: Final = "America/Sao_Paulo"

SUBSYSTEMS: Final[frozenset[str]] = frozenset({"N", "NE", "S", "SE", "SIN"})
"""Valid values of ``id_subsistema``. ``SIN`` is the national total."""

FUEL_COLUMNS: Final[dict[str, str]] = {
    "val_gerhidraulica": "hydro",
    "val_gertermica": "other",
    "val_gereolica": "wind",
    "val_gersolar": "solar",
}
"""CSV column to canonical fuel.

``val_gertermica`` is a single aggregate of everything thermal: gas, coal, oil,
nuclear and biomass are not separated in this file. Mapping it to ``gas`` would
be a guess, so it maps to ``other`` and the methodology page says why. Anyone
computing a Brazilian carbon intensity from this project's data needs to know
the thermal block is unresolved, and silently calling it gas would hide that.
"""

_LOAD_COLUMN: Final = "val_carga"


class OnsSource:
    """Fetch load and generation for a Brazilian subsystem."""

    name = "ons"
    datasets = ("load", "generation")
    # One whole file per calendar year, so a longer window is strictly cheaper
    # than several short ones.
    max_window_days = None

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")

        subsystem = zone.source_keys.get("ons_subsystem", "SIN").strip().upper()
        if subsystem not in SUBSYSTEMS:
            raise UpstreamError(
                f"zone {zone.code} has ons_subsystem={subsystem!r}; "
                f"expected one of {sorted(SUBSYSTEMS)}"
            )

        frames: list[pl.DataFrame] = []
        with http_client() as client:
            for year in range(start.year, end.year + 1):
                raw = fetch_text(_BALANCE.format(year=year), client=client, allow_missing=True)
                if raw is None:
                    # A year the archive has not published, which is normal when
                    # a backfill window opens before the current year's file or
                    # extends past it.
                    continue
                parsed = _parse_balance(raw, subsystem)
                if not parsed.is_empty():
                    frames.append(parsed)

        if not frames:
            return empty_frame(dataset)

        combined = (
            pl.concat(frames, how="vertical")
            .filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
            .unique(subset=["ts_utc"], keep="last")
            .sort("ts_utc")
        )
        if combined.is_empty():
            return empty_frame(dataset)

        resolution = infer_resolution_minutes(combined["ts_utc"])

        if dataset == "load":
            out = combined.select("ts_utc", pl.col(_LOAD_COLUMN).alias("load_mw")).drop_nulls(
                "load_mw"
            )
            return out.with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            ).select("zone", "ts_utc", "resolution_min", "load_mw", "source")

        out = (
            combined.select("ts_utc", *FUEL_COLUMNS)
            .unpivot(index="ts_utc", variable_name="_column", value_name="gen_mw")
            .drop_nulls("gen_mw")
            .with_columns(pl.col("_column").replace_strict(FUEL_COLUMNS).alias("fuel"))
            .group_by(["ts_utc", "fuel"])
            .agg(pl.col("gen_mw").sum())
        )
        return (
            out.with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "fuel", "gen_mw", "source")
            .sort(["ts_utc", "fuel"])
        )


def _parse_balance(raw: str, subsystem: str) -> pl.DataFrame:
    """Parse one yearly balance CSV, keeping a single subsystem.

    Exposed for tests, which run it against a recorded fixture rather than the
    live archive.
    """
    numeric = [*FUEL_COLUMNS, _LOAD_COLUMN]

    frame = pl.read_csv(
        io.StringIO(raw),
        separator=";",
        try_parse_dates=False,
        infer_schema_length=0,  # read everything as text, then coerce explicitly
    )

    missing = {"id_subsistema", "din_instante", *numeric} - set(frame.columns)
    if missing:
        raise UpstreamError(
            f"ONS balance CSV is missing expected columns: {sorted(missing)}. "
            f"Columns present: {frame.columns}"
        )

    frame = frame.filter(pl.col("id_subsistema").str.strip_chars() == subsystem)
    if frame.is_empty():
        return frame.select(
            pl.lit(None).cast(UTC_DATETIME).alias("ts_utc"),
            *[pl.lit(None).cast(pl.Float64).alias(c) for c in numeric],
        )

    return frame.select(
        # Brasilia local time. `ambiguous="earliest"` picks the first pass of a
        # repeated hour and `non_existent="null"` drops the hour that a
        # spring-forward skipped, so pre-2019 files do not silently shift.
        pl.col("din_instante")
        .str.strip_chars()
        .str.to_datetime(format="%Y-%m-%d %H:%M:%S", strict=False)
        .dt.replace_time_zone(_TIMEZONE, ambiguous="earliest", non_existent="null")
        .dt.convert_time_zone("UTC")
        .cast(UTC_DATETIME)
        .alias("ts_utc"),
        # An empty field is missing data, not zero output.
        *[pl.col(c).str.strip_chars().cast(pl.Float64, strict=False).alias(c) for c in numeric],
    ).drop_nulls("ts_utc")
