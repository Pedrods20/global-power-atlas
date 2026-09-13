"""ONS adapter for the Brazilian National Interconnected System.

Brazil is served by two ONS endpoints on purpose, because they do not carry the
same publication lag.

Generation comes from the hourly energy balance, one semicolon-delimited CSV per
calendar year on a public S3 bucket. That file is republished several times a
day but its contents trail real time by roughly two days, and no faster ONS
source for generation by technology exists. The lag is the provider's, not this
adapter's, and it is documented on the methodology page rather than hidden.

Load comes from the verified-load API instead, which carries the same two years
of history at half-hourly resolution but stays within about an hour of real
time. Using the balance file for load as well would throw away two days of
demand for no reason.

Four Brazil-specific points are handled here:

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
    implementation of this project shipped. The load API applies the same rule:
    it returns rows with ``val_cargaglobal`` of exactly zero for intervals it has
    not yet measured, and those are dropped rather than recorded as no demand.

The two endpoints stamp intervals differently.
    The balance file stamps each hour at its *start*. The verified-load API
    stamps each half-hour at its *end*: local day D runs from ``00:30`` to
    ``24:00``, which the API writes as the next day's ``03:00Z``. Load rows are
    shifted back thirty minutes so every stored ``ts_utc`` is an interval start.
    Both conventions were verified on 2026-09-13 against the raw payload and
    against the solar-noon centroid of generation; see ``docs/DATA-AUDIT.md``.

Dates in requests are Brasilia dates.
    The load API filters on ``dat_referencia`` and each yearly balance file
    covers a local calendar year, so windows are widened by one day on each
    side before trimming to the caller's UTC window. Otherwise the first three
    UTC hours of each requested window would fall between two requests.

The load API's own national aggregate is unusable.
    Requesting ``cod_areacarga=SIN`` returns timestamps with every value zeroed.
    National load is therefore summed from the four submarket areas, and a
    timestamp is only emitted when all four reported, because summing three
    would understate national demand without any sign that it had happened.
    Note the Southeast area code is ``SECO``; the older ``SE`` now returns an
    empty list rather than an error.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Final

import polars as pl

from gpa.schema import UTC_DATETIME, empty_frame
from gpa.sources.base import (
    UpstreamError,
    fetch_json,
    fetch_text,
    http_client,
)
from gpa.zones import Zone

__all__ = ["FUEL_COLUMNS", "LOAD_AREAS", "SUBSYSTEMS", "OnsSource"]

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

_LOAD_API: Final = "https://apicarga.ons.org.br/prd/cargaverificada"

LOAD_AREAS: Final[tuple[str, ...]] = ("SECO", "S", "NE", "N")
"""Submarket area codes of the verified-load API, which together make up the SIN.

The API exposes ``SIN`` too, but it answers with every value zeroed, so national
load has to be summed from these four. The Southeast is ``SECO`` here; the
``SE`` code the older ONS endpoints used now returns an empty list rather than
an error, which is the quietest possible way for a feed to break.
"""

_LOAD_API_RESOLUTION: Final = 30
"""Minutes per verified-load interval."""

_LOAD_API_CHUNK_DAYS: Final = 30
"""Days requested per call. The API answers a longer span, but one bad request
in a two-year backfill is cheaper to retry at this size."""


class OnsSource:
    """Fetch load and generation for a Brazilian subsystem."""

    name: str = "ons"
    datasets: tuple[str, ...] = ("load", "generation")
    # One whole file per calendar year, so a longer window is strictly cheaper
    # than several short ones.
    max_window_days: int | None = None

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

        if dataset == "load" and subsystem == "SIN":
            # Never silently mix hourly balance load into the half-hourly
            # verified-load series: upserts would leave overlapping intervals.
            return self._fetch_load_api(zone, start, end)

        frames: list[pl.DataFrame] = []
        with http_client() as client:
            # Local time trails UTC, so a window opening at UTC midnight on
            # 1 January begins in the previous year's file.
            for year in range((start - dt.timedelta(days=1)).year, end.year + 1):
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

        resolution = 60  # The official hourly subsystem balance contract.

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

    def _fetch_load_api(
        self,
        zone: Zone,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        """National load summed from the four submarket areas of the load API.

        A transport failure propagates; no alternative load definition is used.
        """
        collected: list[pl.DataFrame] = []
        with http_client() as client:
            # Padding by a local day on each side covers the rows whose local
            # reference date differs from their UTC date.
            first = start - dt.timedelta(days=1)
            last = end + dt.timedelta(days=1)
            cursor = first
            step = dt.timedelta(days=_LOAD_API_CHUNK_DAYS)
            while cursor < last:
                stop = min(cursor + step, last)
                for area in LOAD_AREAS:
                    payload = fetch_json(
                        _LOAD_API,
                        client=client,
                        params={
                            "dat_inicio": cursor.astimezone(dt.UTC).date().isoformat(),
                            "dat_fim": stop.astimezone(dt.UTC).date().isoformat(),
                            "cod_areacarga": area,
                        },
                    )
                    parsed = _parse_load_api(payload, area)
                    if not parsed.is_empty():
                        collected.append(parsed)
                cursor = stop

        if not collected:
            return empty_frame("load")

        national = _aggregate_load_areas(pl.concat(collected, how="vertical"))
        national = national.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))
        if national.is_empty():
            return empty_frame("load")

        resolution = _LOAD_API_RESOLUTION
        return (
            national.with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(resolution).cast(pl.Int16).alias("resolution_min"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "load_mw", "source")
            .sort("ts_utc")
        )


def _parse_load_api(payload: object, area: str) -> pl.DataFrame:
    """Turn one area's load-API response into ``ts_utc``/``area``/``load_mw`` rows.

    Exposed for tests, which run it against a recorded fixture.
    """
    if not isinstance(payload, list) or not payload:
        return pl.DataFrame(
            schema={"ts_utc": UTC_DATETIME, "area": pl.String, "load_mw": pl.Float64}
        )

    frame = pl.DataFrame(payload, infer_schema_length=None)
    for column in ("din_referenciautc", "val_cargaglobal"):
        if column not in frame.columns:
            raise UpstreamError(
                f"ONS load API response is missing {column!r}. Columns present: {frame.columns}"
            )

    return (
        frame.select(
            pl.col("din_referenciautc")
            .cast(pl.String)
            .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.3fZ", strict=False)
            .dt.replace_time_zone("UTC")
            .cast(UTC_DATETIME)
            .alias("ts_utc"),
            pl.lit(area).alias("area"),
            pl.col("val_cargaglobal").cast(pl.Float64, strict=False).alias("load_mw"),
        )
        .drop_nulls(["ts_utc", "load_mw"])
        # The API stamps the end of each half-hour; store the interval start.
        .with_columns(pl.col("ts_utc") - pl.duration(minutes=_LOAD_API_RESOLUTION))
        # A value of exactly zero means the interval has not been measured yet,
        # not that the area drew no power. Keeping it would drag the national
        # sum down and, worse, look like a real observation.
        .filter(pl.col("load_mw") > 0)
    )


def _aggregate_load_areas(frame: pl.DataFrame) -> pl.DataFrame:
    """Sum the submarket areas into national load, requiring all four.

    A timestamp reported by only some areas is dropped. Summing three of four
    would understate national demand by roughly the missing area's share while
    looking like an ordinary observation, which is far worse than a gap.
    """
    if frame.is_empty():
        return pl.DataFrame(schema={"ts_utc": UTC_DATETIME, "load_mw": pl.Float64})

    return (
        frame.unique(subset=["ts_utc", "area"], keep="last")
        .group_by("ts_utc")
        .agg(
            pl.col("load_mw").sum().alias("load_mw"),
            pl.col("area").n_unique().alias("_areas"),
        )
        .filter(pl.col("_areas") == len(LOAD_AREAS))
        .drop("_areas")
        .sort("ts_utc")
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
