"""Build the aggregated tables the static site reads.

The site never touches the raw store. It reads a handful of small, pre-computed
Parquet and JSON files written here, which keeps the published bundle to a few
hundred kilobytes instead of the tens of megabytes the hourly store holds.

This runs as an explicit build step rather than as an Observable Framework data
loader. Loaders would be the idiomatic choice, but they invoke an interpreter
the framework picks, which differs between a Windows workstation and a Linux
CI runner. An explicit step behaves identically in both and can be run and
debugged on its own.

Every table carries a ``zone`` column so the site can filter client-side
without a second request.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import TypedDict

import polars as pl

from gpa import benchmarks, store
from gpa.metrics import load as load_metrics
from gpa.metrics import mix as mix_metrics
from gpa.metrics import price as price_metrics
from gpa.zones import ZONES, Zone, get_zone

__all__ = ["DEFAULT_OUTPUT", "export_all", "site_root"]

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = "site/data"

# The duration curves are downsampled for the browser. Two thousand points is
# past the pixel resolution of any chart the site draws, so the curve's shape,
# including both tails, survives intact.
_CURVE_POINTS = 2000


class Overview(TypedDict):
    """Shape of ``zones.json``, the one site file that is not Parquet.

    Declared so that callers can index it without the result widening to
    ``object``, and so the site's contract is stated in one place.
    """

    data_as_of: str | None
    zones: list[dict[str, object]]


def site_root() -> Path:
    """Repository-relative default output directory."""
    return Path(__file__).resolve().parents[2] / "site" / "data"


def export_all(output: Path | None = None) -> dict[str, int]:
    """Write every site table, returning row counts by file name.

    Args:
        output: Destination directory. Defaults to ``site/data`` in the repo.

    Returns:
        File name to row count, for logging and for the CLI summary.
    """
    destination = Path(output) if output is not None else site_root()
    destination.mkdir(parents=True, exist_ok=True)

    tables: dict[str, pl.DataFrame] = {
        "daily_prices": _daily_prices(),
        "price_duration": _price_duration(),
        "negative_prices": _negative_prices(),
        "volatility": _volatility(),
        "daily_load": _daily_load(),
        "load_profile": _load_profile(),
        "load_factor": _load_factor(),
        "generation_mix": _generation_mix(),
        "carbon_intensity": _carbon_intensity(),
        "capture_rates": _capture_rates(),
        "freshness": _freshness(),
        "europe_spreads": _europe_spreads(),
    }

    written: dict[str, int] = {}
    for name, frame in tables.items():
        path = destination / f"{name}.parquet"
        result = _stringify_dates(frame)
        if result.columns:
            result = result.sort(result.columns)
        result.write_parquet(path, compression="zstd", statistics=True)
        written[path.name] = frame.height

    overview = _overview()
    (destination / "zones.json").write_text(
        json.dumps(overview, indent=2, default=str), encoding="utf-8"
    )
    written["zones.json"] = len(overview["zones"])

    return written


def check_exports(destination: Path | None = None) -> list[str]:
    """Compare tables by values, independently of Parquet writer metadata."""
    import tempfile

    from polars.testing import assert_frame_equal

    target = destination or site_root()
    differences = []
    with tempfile.TemporaryDirectory(prefix="gpa-export-") as temp:
        actual = Path(temp)
        for name in export_all(actual):
            expected_path = target / name
            if not expected_path.exists():
                differences.append(name)
                continue
            if name.endswith(".json"):
                if json.loads(expected_path.read_text(encoding="utf-8")) != json.loads(
                    (actual / name).read_text(encoding="utf-8")
                ):
                    differences.append(name)
            else:
                try:
                    expected = pl.read_parquet(expected_path)
                    observed = pl.read_parquet(actual / name)
                    if expected.columns:
                        expected = expected.sort(expected.columns)
                    assert_frame_equal(expected, observed, check_row_order=True)
                except (AssertionError, pl.exceptions.PolarsError):
                    differences.append(name)
    return differences


def _stringify_dates(frame: pl.DataFrame) -> pl.DataFrame:
    """Render date and datetime columns as ISO strings.

    Arrow's date32 and timestamp types reach the browser as different
    JavaScript values depending on the Arrow build, which makes chart code
    fragile in a way that is tedious to debug. ISO strings are unambiguous, and
    the size cost is negligible on tables this small.
    """
    if frame.is_empty():
        return frame

    casts = [
        pl.col(name).dt.strftime("%Y-%m-%d").alias(name)
        if dtype == pl.Date
        else pl.col(name).dt.strftime("%Y-%m-%dT%H:%M:%SZ").alias(name)
        for name, dtype in frame.schema.items()
        if dtype == pl.Date or isinstance(dtype, pl.Datetime)
    ]
    return frame.with_columns(casts) if casts else frame


# --- Per-zone helpers ------------------------------------------------------


def _for_each(dataset: str, builder) -> pl.DataFrame:  # type: ignore[no-untyped-def]
    """Apply ``builder`` to each zone's slice and stack the results.

    A zone with no data for the dataset is skipped rather than contributing an
    empty frame, so a market still waiting on a credential simply does not
    appear rather than breaking the concatenation.
    """
    frames: list[pl.DataFrame] = []
    for zone in ZONES:
        if not zone.has(dataset):
            continue
        frame = store.read(dataset, zone.code)
        if frame.is_empty():
            continue
        try:
            result = builder(frame, zone)
        except Exception:
            log.exception("export failed for %s %s", zone.code, dataset)
            continue
        if result.is_empty():
            continue
        frames.append(result.with_columns(pl.lit(zone.code).alias("zone")))

    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def _daily_prices() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        blocks = price_metrics.block_prices(frame, zone, period="day")
        return (
            blocks.rename({"period": "date"})
            .sort("date")
            .with_columns(
                pl.lit(zone.currency).alias("currency"),
                (pl.col("date").str.to_date().diff().dt.total_days().fill_null(1) > 1)
                .cum_sum()
                .alias("segment"),
            )
        )

    return _for_each("price", build)


def _price_duration() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return price_metrics.duration_curve(frame, points=_CURVE_POINTS)

    return _for_each("price", build)


def _negative_prices() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return price_metrics.negative_price_summary(frame, zone)

    return _for_each("price", build)


def _volatility() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return price_metrics.realised_volatility(frame, zone, window=30).rename(
            {"local_date": "date"}
        )

    return _for_each("price", build)


def _daily_load() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return load_metrics.daily_energy(frame, zone).rename({"local_date": "date"})

    return _for_each("load", build)


def _load_profile() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        # Normalised so that markets of very different size share an axis.
        profile = load_metrics.daily_profile(frame, zone)
        mean = profile["avg_load_mw"].mean()
        return profile.with_columns(
            (pl.col("avg_load_mw") / pl.lit(mean)).alias("normalised")
            if mean
            else pl.lit(None, dtype=pl.Float64).alias("normalised")
        )

    return _for_each("load", build)


def _load_factor() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return load_metrics.load_factor(frame, zone, period="month").rename({"period": "month"})

    return _for_each("load", build)


def _generation_mix() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return mix_metrics.generation_mix(frame, zone, period="month").rename({"period": "month"})

    return _for_each("generation", build)


def _carbon_intensity() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        operational = mix_metrics.carbon_intensity(frame, zone, basis="operational")
        lifecycle = mix_metrics.carbon_intensity(frame, zone, basis="lifecycle")
        renewable = mix_metrics.renewable_share(frame, zone, period="month")
        combined = pl.concat([operational, lifecycle], how="vertical")
        return combined.join(
            renewable.select("period", "renewable_pct"), on="period", how="left"
        ).rename({"period": "month"})

    return _for_each("generation", build)


def _capture_rates() -> pl.DataFrame:
    """Capture rates for the two technologies whose revenue erodes with build-out."""
    frames: list[pl.DataFrame] = []
    for zone in ZONES:
        if not (zone.has("price") and zone.has("generation")):
            continue
        prices = store.read("price", zone.code)
        generation = store.read("generation", zone.code)
        if prices.is_empty() or generation.is_empty():
            continue

        for fuel in ("solar", "wind"):
            try:
                result = price_metrics.capture_rate(
                    prices, generation, zone, fuel=fuel, period="month"
                )
            except Exception:
                log.exception("capture rate failed for %s %s", zone.code, fuel)
                continue
            if result.is_empty():
                continue
            frames.append(
                result.rename({"period": "month"}).with_columns(
                    pl.lit(zone.code).alias("zone"), pl.lit(fuel).alias("fuel")
                )
            )

    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def _europe_spreads() -> pl.DataFrame:
    path = benchmarks.reference_path()
    prices = store.read("price", "DE-LU")
    if not path.exists() or prices.is_empty():
        return pl.DataFrame()
    monthly_power = price_metrics.block_prices(prices, get_zone("DE-LU"), period="month")
    return benchmarks.calculate_spreads(pl.read_parquet(path), monthly_power)


def _freshness() -> pl.DataFrame:
    """The last observed instant and freshness limit for each declared series.

    Publishes the instant rather than a measured age, so the export stays
    deterministic and the reader's browser computes the age at the moment they
    look. A baked-in age would be wrong within the hour and would make
    ``gpa export --check`` fail on every run.

    Published so a reader can see the age of what they are looking at instead
    of assuming every series is equally current. That matters most for the two
    CCEE zones, which are refreshed by hand because the provider blocks
    automated clients, and for Brazilian generation, which trails real time by
    about two days for reasons that belong to ONS rather than to this project.
    """
    from gpa import freshness as freshness_module

    return freshness_module.to_frame(freshness_module.check())


# --- Overview --------------------------------------------------------------


def _overview() -> Overview:
    """Zone metadata plus a freshness snapshot, for the landing page."""
    coverage = store.coverage()
    # Metadata must depend on the stored observations, not the build clock.
    # Otherwise every CI export dirties zones.json even when no data changed.
    # polars types `.max()` as a broad union, so narrow it rather than casting:
    # an unexpected dtype should read as "unknown" instead of crashing on
    # `.isoformat()` at build time.
    raw_as_of = coverage["last_ts_utc"].max() if not coverage.is_empty() else None
    data_as_of = raw_as_of if isinstance(raw_as_of, dt.datetime) else None

    entries: list[dict[str, object]] = []
    for zone in ZONES:
        datasets: dict[str, object] = {}
        for dataset in ("price", "load", "generation"):
            if not zone.has(dataset):
                continue
            row = coverage.filter((pl.col("zone") == zone.code) & (pl.col("dataset") == dataset))
            if row.is_empty():
                datasets[dataset] = {"status": "pending", "source": zone.sources[dataset]}
                continue

            record = row.row(0, named=True)
            datasets[dataset] = {
                "status": "ok",
                "source": zone.sources[dataset],
                "rows": record["rows"],
                "first": record["first_ts_utc"],
                "last": record["last_ts_utc"],
            }

        entries.append(
            {
                "code": zone.code,
                "name": zone.name,
                "country": zone.country,
                "region": zone.region.value,
                "operator": zone.operator,
                "timezone": zone.timezone,
                "civil_timezone": zone.civil_timezone,
                "observes_market_dst": zone.observes_market_dst,
                "currency": zone.currency,
                "peak_block": zone.peak.label,
                "peak_note": zone.peak.note,
                "notes": zone.notes,
                "datasets": datasets,
            }
        )

    return {"data_as_of": data_as_of.isoformat() if data_as_of else None, "zones": entries}
