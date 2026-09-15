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
import math
from pathlib import Path
from typing import TypedDict

import polars as pl

from gpa import store
from gpa.battery import DEFAULT_MODELS
from gpa.calendar import hours_in_local_day
from gpa.metrics import load as load_metrics
from gpa.metrics import mix as mix_metrics
from gpa.metrics import price as price_metrics
from gpa.zones import ZONES, Zone

__all__ = ["DEFAULT_OUTPUT", "export_all", "site_root"]

_BATTERY_TABLES: tuple[str, ...] = (
    "battery_monthly",
    "battery_dispatch_example",
    "battery_summary",
    "battery_risk",
    "battery_comparisons",
    "battery_coverage",
    "battery_costs",
    "battery_sensitivities",
)
"""Economic dispatch rows, the scoreboard and :func:`gpa.battery_study.evaluate`'s
richer research tables, all from the one frozen forecast snapshot."""

_BATTERY_MODEL_NAMES: tuple[str, ...] = DEFAULT_MODELS
_BATTERY_DURATIONS_MWH: tuple[float, ...] = (1.0, 2.0, 4.0)

log = logging.getLogger(__name__)

DEFAULT_OUTPUT = "site/data"


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
        "daily_load": _daily_load(),
        "generation_mix": _generation_mix(),
        "freshness": _freshness(),
    }
    from gpa import quality
    from gpa.forecast import snapshot

    tables["data_quality"] = quality.report()
    saved = snapshot.read()
    if saved is None:
        raise RuntimeError(
            "no frozen forecast snapshot under data/experiments/. The public export "
            "reads a committed release, not a live recompute that would drift as the "
            "store grows. Run `gpa backtest --zone DE-LU --save-snapshot`, commit the "
            "new data/experiments/<id>/ and current.json, then retry `gpa export`."
        )
    metadata, frames = saved
    runs = {"runs": [metadata]}
    for suffix in ("scores", "daily", "predictions", "coefficients"):
        result = frames[suffix]
        if suffix == "predictions":
            result = result.drop("ts_utc").with_columns(pl.col(pl.Float64).round(2))
        tables[f"forecast_{suffix}"] = result.with_columns(pl.lit(metadata["zone"]).alias("zone"))

    tables.update(_battery_tables(tables.get("forecast_predictions", pl.DataFrame())))

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

    (destination / "forecast.json").write_text(
        json.dumps(runs, indent=2, default=str), encoding="utf-8"
    )
    written["forecast.json"] = len(runs["runs"])

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
                expected_json = json.loads(expected_path.read_text(encoding="utf-8"))
                actual_json = json.loads((actual / name).read_text(encoding="utf-8"))
                if not _json_values_close(expected_json, actual_json):
                    detail = "; ".join(_json_diff(expected_json, actual_json))
                    differences.append(f"{name} ({detail})" if detail else name)
            else:
                try:
                    expected = pl.read_parquet(expected_path)
                    observed = pl.read_parquet(actual / name)
                    if expected.columns:
                        expected = expected.sort(expected.columns)
                    assert_frame_equal(expected, observed, check_row_order=True)
                except (AssertionError, pl.exceptions.PolarsError) as exc:
                    differences.append(f"{name} ({exc})")
    return differences


def _json_diff(expected: object, actual: object, path: str = "$") -> list[str]:
    """Dotted-path descriptions of where two JSON values first disagree.

    Bounded to a handful of entries: this is for a human reading a failed
    `gpa export --check`, not an exhaustive report.
    """
    if _json_values_close(expected, actual):
        return []
    if isinstance(expected, dict) and isinstance(actual, dict) and expected.keys() == actual.keys():
        out: list[str] = []
        for key in expected:
            out.extend(_json_diff(expected[key], actual[key], f"{path}.{key}"))
            if len(out) >= 5:
                break
        return out[:5]
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        out = []
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            out.extend(_json_diff(e, a, f"{path}[{i}]"))
            if len(out) >= 5:
                break
        return out[:5]
    return [f"{path}: expected {expected!r}, got {actual!r}"]


def _json_values_close(expected: object, actual: object) -> bool:
    """Compare parsed JSON, tolerating the float noise a different platform's
    BLAS or LightGBM build introduces into the forecast benchmark.

    Every other exported table already tolerates this: Parquet comparisons go
    through :func:`polars.testing.assert_frame_equal`, which allows a relative
    tolerance by default. Exact ``!=`` on JSON held forecast metrics to a
    stricter, bit-identical standard that a fitted model cannot promise across
    a Windows workstation and a Linux CI runner even with a fixed seed.
    """
    if isinstance(expected, float) and isinstance(actual, float):
        return math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-9)
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _json_values_close(expected[k], actual[k]) for k in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            _json_values_close(e, a) for e, a in zip(expected, actual, strict=True)
        )
    return expected == actual


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
        result = builder(frame, zone)
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


def _daily_load() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        daily = load_metrics.daily_energy(frame, zone).rename({"local_date": "date"})
        return _drop_incomplete_trailing_day(daily, zone, "hours_observed")

    return _for_each("load", build)


def _drop_incomplete_trailing_day(
    daily: pl.DataFrame, zone: Zone, hours_column: str
) -> pl.DataFrame:
    """Drop the last row if it covers fewer hours than its own local day has.

    A day still being ingested reads as a collapse in the underlying quantity
    if it is charted like every complete day before it. Comparing against
    :func:`gpa.calendar.hours_in_local_day` rather than a flat 24 means a
    genuinely short daylight-saving day is not mistaken for a partial one and
    trimmed by mistake.
    """
    if daily.is_empty():
        return daily
    last = daily.tail(1).row(0, named=True)
    if last[hours_column] < hours_in_local_day(zone, last["date"]):
        return daily.head(daily.height - 1)
    return daily


def _generation_mix() -> pl.DataFrame:
    def build(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
        return mix_metrics.generation_mix(frame, zone, period="month").rename({"period": "month"})

    return _for_each("generation", build)


def _battery_tables(predictions: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Freeze the site's battery economics from the same forecast snapshot.

    ``battery_study.evaluate`` is a pure function of ``predictions``, so once
    that table comes from the frozen forecast snapshot, calling it here instead
    of the bare dispatch backtest freezes the battery numbers too, and exposes
    the same risk/comparison/coverage tables the local research studies get.
    A short compatibility guard (``horizon_steps=24``) that the previous bare
    call passed is not available through ``evaluate``; it only ever re-asserted
    that these are ordinary 24-hour DE-LU days, which the underlying dispatch
    already requires to include every interval of the day regardless.
    """
    from gpa.battery_sensitivity import scenario_tables
    from gpa.battery_study import evaluate

    if predictions.is_empty():
        return {name: pl.DataFrame() for name in _BATTERY_TABLES}

    tag = pl.lit("DE-LU").alias("zone")

    base = evaluate(
        predictions, model_names=_BATTERY_MODEL_NAMES, durations_mwh=_BATTERY_DURATIONS_MWH
    )
    costs, sensitivities = scenario_tables(
        predictions, base, model_names=_BATTERY_MODEL_NAMES, durations_mwh=_BATTERY_DURATIONS_MWH
    )

    # The page draws monthly cumulative margin and one example day; shipping
    # every interval of every strategy, duration and day would make the browser
    # materialize over a million rows on a multi-year sample.
    monthly = base.daily.group_by(
        "strategy",
        "power_mw",
        "energy_mwh",
        pl.col("local_date").dt.strftime("%Y-%m").alias("month"),
    ).agg(pl.len().cast(pl.UInt32).alias("days"), pl.col("profit_eur").sum())
    example = base.dispatch.filter(pl.col("local_date") == pl.col("local_date").max())

    return {
        "battery_monthly": monthly.with_columns(tag),
        "battery_dispatch_example": example.with_columns(tag),
        "battery_summary": base.summary.with_columns(tag),
        "battery_risk": base.risk.with_columns(tag),
        "battery_comparisons": base.comparisons.with_columns(tag),
        "battery_coverage": base.coverage.with_columns(tag),
        "battery_costs": costs.with_columns(tag),
        "battery_sensitivities": sensitivities.with_columns(tag),
    }


def _freshness() -> pl.DataFrame:
    """The last observed instant and freshness limit for each declared series.

    Publishes the instant rather than a measured age, so the export stays
    deterministic and the reader's browser computes the age at the moment they
    look. A baked-in age would be wrong within the hour and would make
    ``gpa export --check`` fail on every run.

    Published so a reader can see the age of what they are looking at instead
    of assuming every series is equally current. That matters most for Brazilian
    generation, which trails real time by about two days for reasons that belong
    to ONS rather than to this project.
    """
    from gpa import freshness as freshness_module

    return freshness_module.to_frame(freshness_module.check())


# --- Overview --------------------------------------------------------------


_MEASURED_DATASETS = ("load", "generation")
"""Datasets that describe what has actually happened, for ``data_as_of``.

Price is excluded on purpose: since day-ahead prices are ingested up to
:data:`gpa.pipeline.PUBLISHED_AHEAD_DAYS` past now, its own latest timestamp
can be a delivery hour that has not happened yet. Labelling the page "as of"
that instant would read as data from the future. Each zone's own price
coverage is still shown separately in ``datasets.price.last`` below.
"""


def _overview() -> Overview:
    """Zone metadata plus a freshness snapshot, for the landing page."""
    coverage = store.coverage()
    # Metadata must depend on the stored observations, not the build clock.
    # Otherwise every CI export dirties zones.json even when no data changed.
    # polars types `.max()` as a broad union, so narrow it rather than casting:
    # an unexpected dtype should read as "unknown" instead of crashing on
    # `.isoformat()` at build time.
    measured = coverage.filter(pl.col("dataset").is_in(_MEASURED_DATASETS))
    raw_as_of = measured["last_ts_utc"].max() if not measured.is_empty() else None
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
                "observes_market_dst": zone.observes_market_dst,
                "currency": zone.currency,
                "peak_block": zone.peak.label,
                "peak_note": zone.peak.note,
                "notes": zone.notes,
                "datasets": datasets,
            }
        )

    return {"data_as_of": data_as_of.isoformat() if data_as_of else None, "zones": entries}
