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
from typing import Final, TypedDict

import polars as pl

from gpa import store
from gpa.battery import DEFAULT_MODELS
from gpa.calendar import hours_in_local_day
from gpa.metrics import load as load_metrics
from gpa.metrics import mix as mix_metrics
from gpa.metrics import price as price_metrics
from gpa.zones import ZONES, Zone, get_zone

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
_FORECAST_PAGE_WEEKS = 12
_CANNIBALISATION_FUELS: tuple[str, ...] = ("solar", "wind")

_CORRELATION_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("solar_capacity_gw", "solar_capture_rate", "Solar capacity vs. solar capture rate"),
    ("solar_capacity_gw", "negative_pct", "Solar capacity vs. negative-price frequency"),
    ("wind_capacity_gw", "wind_capture_rate", "Wind capacity vs. wind capture rate"),
    (
        "solar_capacity_gw",
        "spread_pct_of_price",
        "Solar capacity vs. on/off-peak spread (% of average price)",
    ),
)
"""(x column, y column, label) pairs correlated by :func:`_capacity_price_correlation`.

``spread_pct_of_price`` rather than the raw EUR spread: the raw spread scales
with the overall price level, so 2021-2022's fuel-price shock alone would
dominate a level correlation without a single extra megawatt of capacity
being built. Normalising by the year's average price does not remove the
shock entirely, but it stops the correlation from mostly just measuring gas
prices.
"""

_BATTERY_COMPETITION_STRATEGIES: Final[tuple[str, ...]] = ("perfect_foresight", "ridge", "lightgbm")
"""Strategies correlated against the German battery fleet in step 4.

``perfect_foresight`` isolates the market-structural arbitrage opportunity --
the maximum spread extractable that period -- from forecast skill, which
matters specifically because the question here is whether competition is
shrinking the opportunity itself, not whether either model got better or
worse at capturing it. ``ridge`` and ``lightgbm`` sit alongside it so the
realistic, forecast-dependent margin is visible too, not only the ceiling.
"""

_PLANNED_TO_REALISED_TECHNOLOGY: Final[dict[str, tuple[str, ...]]] = {
    "Solar planned (EEG 2023)": ("Solar AC", "Solar DC"),
    "Wind onshore planned (EEG 2023)": ("Wind onshore",),
    "Wind offshore planned (WindSeeG)": ("Wind offshore",),
}
"""Government target series to the realised series it should be read against.

Solar maps to both AC and DC realised series because it is not independently
verified which convention EEG 2023's target uses; showing both rather than
guessing one keeps that uncertainty visible instead of hidden in a single
number.
"""

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

    capacity_price_yearly = _capacity_price_yearly()
    price_before_year = _annual_fit_cutoff(store.read("price", "DE-LU"), get_zone("DE-LU"))
    tables: dict[str, pl.DataFrame] = {
        "daily_prices": _daily_prices(),
        "daily_load": _daily_load(),
        "generation_mix": _generation_mix(),
        "capacity": _capacity(),
        "cannibalisation": _cannibalisation(),
        "fundamentals_ablation": _fundamentals_ablation(),
        "capacity_price_yearly": capacity_price_yearly,
        "capacity_price_correlation": _capacity_price_correlation(
            capacity_price_yearly, before_year=price_before_year
        ),
        "capacity_extrapolation_flags": _capacity_extrapolation_flags(
            before_year=price_before_year
        ),
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
    full_predictions = pl.DataFrame()
    for suffix in ("scores", "daily", "predictions", "coefficients"):
        result = frames[suffix]
        if suffix == "predictions":
            result = result.drop("ts_utc").with_columns(pl.col(pl.Float64).round(2))
            full_predictions = result.with_columns(pl.lit(metadata["zone"]).alias("zone"))
            tables["forecast_preview"] = _forecast_page_predictions(full_predictions)
        tables[f"forecast_{suffix}"] = result.with_columns(pl.lit(metadata["zone"]).alias("zone"))

    battery_tables = _battery_tables(full_predictions)
    tables.update(battery_tables)
    battery_margin_yearly = _battery_margin_yearly(battery_tables["battery_monthly"])
    tables["battery_margin_yearly"] = battery_margin_yearly
    tables["battery_competition_correlation"] = _battery_competition_correlation(
        battery_margin_yearly,
        before_year=_annual_fit_cutoff(frames["predictions"], get_zone(str(metadata["zone"]))),
    )

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


def _capacity() -> pl.DataFrame:
    """Installed DE renewable/storage capacity, tagged for the DE-LU page.

    Reads the reference-data series :mod:`gpa.capacity` fetched from Energy-
    Charts' ``/installed_power`` (``gpa capacity``), not the interval store.
    Tagged with the DE-LU zone code for the site's existing per-zone filter,
    while the underlying ``country`` column (always "DE") stays visible so the
    DE-LU-vs-Germany-only distinction is never hidden from a reader.
    """
    from gpa import capacity as capacity_module

    frame = capacity_module.read()
    if frame.is_empty():
        return frame
    return frame.with_columns(pl.lit("DE-LU").alias("zone"))


def _fundamentals_ablation() -> pl.DataFrame:
    """The pre-auction fundamentals ablation, a labelled diagnostic.

    Reads ``gpa.fundamentals_ablation`` (``gpa fundamentals-ablation``), a
    small reference file kept entirely separate from
    ``data/experiments/``/``current.json`` -- this is a comparison against
    the published release's identical protocol, not a candidate to replace
    it. Empty until that command has been run once.
    """
    from gpa import fundamentals_ablation as ablation_module

    return ablation_module.read()


def _cannibalisation() -> pl.DataFrame:
    """Yearly generation-weighted capture price/rate for DE-LU solar and wind.

    Wires :func:`gpa.metrics.price.capture_rate`, built and tested earlier but
    never connected to the export pipeline, against the price and generation
    already in the store. This is the empirical cannibalisation measure the
    capacity/renewables scenario study is built around.
    """
    zone = get_zone("DE-LU")
    if not (zone.has("price") and zone.has("generation")):
        return pl.DataFrame()
    prices = store.read("price", zone.code)
    generation = store.read("generation", zone.code)
    if prices.is_empty() or generation.is_empty():
        return pl.DataFrame()

    frames = [
        price_metrics.capture_rate(prices, generation, zone, fuel=fuel, period="year").with_columns(
            pl.lit(fuel).alias("fuel")
        )
        for fuel in _CANNIBALISATION_FUELS
    ]
    frames = [frame for frame in frames if not frame.is_empty()]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed").with_columns(pl.lit(zone.code).alias("zone"))


def _capacity_price_yearly() -> pl.DataFrame:
    """One row per year: realised solar/wind capacity paired with price-shape metrics.

    Anchored on years the store actually has price coverage for (2019
    onward), not on the capacity series' longer history, since a correlation
    needs both sides present. The current year is real but partial (not yet
    a full 12 months), which is left in this descriptive table -- only
    :func:`_capacity_price_correlation` excludes it from a fitted statistic.
    """
    zone = get_zone("DE-LU")
    if not (zone.has("price") and zone.has("generation")):
        return pl.DataFrame()
    prices = store.read("price", zone.code)
    generation = store.read("generation", zone.code)
    if prices.is_empty() or generation.is_empty():
        return pl.DataFrame()

    from gpa import capacity as capacity_module

    yearly_cap = capacity_module.read().filter(
        (pl.col("time_step") == "yearly") & (~pl.col("is_planned"))
    )
    if yearly_cap.is_empty():
        return pl.DataFrame()

    solar_gw = yearly_cap.filter(pl.col("technology") == "Solar AC").select(
        pl.col("period").alias("year"), pl.col("value").alias("solar_capacity_gw")
    )
    wind_gw = (
        yearly_cap.filter(pl.col("technology").is_in(["Wind onshore", "Wind offshore"]))
        .group_by("period")
        .agg(pl.col("value").sum().alias("wind_capacity_gw"))
        .rename({"period": "year"})
    )
    spread = price_metrics.block_prices(prices, zone, period="year").select(
        pl.col("period").alias("year"),
        "spread",
        (pl.col("spread") / pl.col("all_hours") * 100.0).alias("spread_pct_of_price"),
    )
    negative = (
        price_metrics.negative_price_summary(prices, zone)
        .with_columns(pl.col("local_month").str.slice(0, 4).alias("year"))
        .group_by("year")
        .agg(pl.col("negative_hours").sum(), pl.col("observed_hours").sum())
        .with_columns(
            (pl.col("negative_hours") / pl.col("observed_hours") * 100.0).alias("negative_pct")
        )
        .select("year", "negative_pct")
    )
    solar_capture = price_metrics.capture_rate(
        prices, generation, zone, fuel="solar", period="year"
    ).select(pl.col("period").alias("year"), pl.col("capture_rate").alias("solar_capture_rate"))
    wind_capture = price_metrics.capture_rate(
        prices, generation, zone, fuel="wind", period="year"
    ).select(pl.col("period").alias("year"), pl.col("capture_rate").alias("wind_capture_rate"))

    combined = (
        spread.join(solar_gw, on="year", how="inner")
        .join(wind_gw, on="year", how="left")
        .join(negative, on="year", how="left")
        .join(solar_capture, on="year", how="left")
        .join(wind_capture, on="year", how="left")
        .sort("year")
    )
    if combined.is_empty():
        return combined
    return combined.with_columns(pl.lit(zone.code).alias("zone"))


def _annual_fit_cutoff(intervals: pl.DataFrame, zone: Zone) -> int | None:
    """Exclusive year bound from the final input interval, never the export clock.

    Price intervals carry their own resolution; frozen predictions are hourly.
    Ending at local January 1 includes the preceding year. This only removes
    a trailing partial year, not gaps or incomplete leading years within a study.
    """
    if intervals.is_empty():
        return None
    minutes = pl.col("resolution_min") if "resolution_min" in intervals.columns else pl.lit(60)
    boundary = intervals.select(
        (pl.col("ts_utc") + pl.duration(minutes=minutes))
        .max()
        .dt.convert_time_zone(zone.timezone)
        .dt.year()
    ).item()
    return int(boundary)


def _capacity_price_correlation(yearly: pl.DataFrame, *, before_year: int | None) -> pl.DataFrame:
    """Pearson correlation for each pair in :data:`_CORRELATION_PAIRS`.

    Excludes years at or after the input-derived cutoff: a part-year point (built from
    fewer months than the rest) is not comparable to a complete one and would
    distort a correlation computed on only seven-odd points to begin with.

    This is a small-n, shared-time-trend correlation, not a causal estimate:
    with about seven annual points, most series that both trend over the
    sample period will correlate whether or not one drives the other. The
    exact fitted year range is carried in every row so a reader can see how
    little data underlies the coefficient, and so nobody downstream
    mistakes it for something it is not.
    """
    if yearly.is_empty() or before_year is None:
        return pl.DataFrame()
    complete = yearly.filter(pl.col("year") < str(before_year))
    if complete.is_empty():
        return pl.DataFrame()

    rows: list[dict[str, object]] = []
    for x, y, label in _CORRELATION_PAIRS:
        pair = complete.select("year", x, y).filter(pl.col(x).is_finite() & pl.col(y).is_finite())
        if pair.height < 3:
            continue
        r = pair.select(pl.corr(x, y)).item()
        if r is None or not math.isfinite(r):
            continue
        rows.append(
            {
                "x": x,
                "y": y,
                "label": label,
                "n": pair.height,
                "pearson_r": r,
                "fitted_year_min": pair["year"].min(),
                "fitted_year_max": pair["year"].max(),
            }
        )
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).with_columns(pl.lit("DE-LU").alias("zone"))


def _capacity_extrapolation_flags(*, before_year: int | None) -> pl.DataFrame:
    """Whether each 2030 policy target lies above anything DE-LU has realised.

    Answers the plan's own requirement directly, as a table rather than a
    prose caveat: the correlation in :func:`_capacity_price_correlation` is
    fitted only on realised capacity levels, and a scenario that walks a
    technology out to its government 2030 target is an extrapolation beyond
    that fitted range whenever this table says ``exceeds_realised_max`` is
    true. Excludes the input's trailing partial year from "realised", the same choice
    :func:`_capacity_price_correlation` makes, so a not-yet-complete year
    cannot masquerade as this technology's realised ceiling.
    """
    from gpa import capacity as capacity_module

    cap = capacity_module.read()
    if cap.is_empty() or before_year is None:
        return pl.DataFrame()

    realised = (
        cap.filter(
            (pl.col("time_step") == "yearly")
            & (~pl.col("is_planned"))
            & (pl.col("period") < str(before_year))
        )
        .group_by("technology")
        .agg(
            pl.col("value").max().alias("realised_max_gw"),
            pl.col("period").sort_by("value", descending=True).first().alias("realised_max_year"),
        )
    )
    planned_2030 = cap.filter(
        (pl.col("time_step") == "yearly") & pl.col("is_planned") & (pl.col("period") == "2030")
    ).select(
        pl.col("technology").alias("planned_technology"), pl.col("value").alias("planned_2030_gw")
    )

    rows: list[pl.DataFrame] = []
    for planned_technology, realised_technologies in _PLANNED_TO_REALISED_TECHNOLOGY.items():
        planned_row = planned_2030.filter(pl.col("planned_technology") == planned_technology)
        if planned_row.is_empty():
            continue
        matches = realised.filter(pl.col("technology").is_in(list(realised_technologies)))
        if matches.is_empty():
            continue
        rows.append(matches.join(planned_row, how="cross"))

    if not rows:
        return pl.DataFrame()
    return (
        pl.concat(rows, how="vertical_relaxed")
        .with_columns(
            (pl.col("planned_2030_gw") > pl.col("realised_max_gw")).alias("exceeds_realised_max"),
            pl.lit("DE-LU").alias("zone"),
        )
        .select(
            "planned_technology",
            "technology",
            "realised_max_gw",
            "realised_max_year",
            "planned_2030_gw",
            "exceeds_realised_max",
            "zone",
        )
        .sort(["planned_technology", "technology"])
    )


def _battery_margin_yearly(monthly: pl.DataFrame) -> pl.DataFrame:
    """One row per (year, strategy, duration): arbitrage margin paired with the German battery fleet.

    Reuses ``battery_monthly`` -- already built once from the frozen forecast
    snapshot for the site's own battery page -- rather than re-running the
    dispatch engine. Aggregated to yearly and normalised to EUR/MW/day
    (``power_mw`` is always 1.0 in that table, so ``profit_eur`` is already
    per rated MW; dividing by days controls for a partial year), for the same
    reason step 3 fitted yearly rather than monthly: the German battery
    fleet's growth is a smooth trend, but monthly arbitrage profit carries its
    own weather- and price-driven seasonality unrelated to fleet size, and
    untangling that properly needs a seasonal control this step does not
    build.
    """
    if monthly.is_empty():
        return pl.DataFrame()

    from gpa import capacity as capacity_module

    fleet_raw = capacity_module.read().filter(
        (pl.col("time_step") == "yearly")
        & (~pl.col("is_planned"))
        & pl.col("technology").is_in(["Battery storage (power)", "Battery storage (capacity)"])
    )
    if fleet_raw.is_empty():
        return pl.DataFrame()

    fleet = fleet_raw.pivot(on="technology", index="period", values="value")
    for technology in ("Battery storage (power)", "Battery storage (capacity)"):
        if technology not in fleet.columns:
            fleet = fleet.with_columns(pl.lit(None, dtype=pl.Float64).alias(technology))
    fleet = fleet.rename(
        {
            "period": "year",
            "Battery storage (power)": "battery_power_gw",
            "Battery storage (capacity)": "battery_energy_gwh",
        }
    )

    margin = (
        monthly.filter(pl.col("strategy").is_in(_BATTERY_COMPETITION_STRATEGIES))
        .with_columns(pl.col("month").str.slice(0, 4).alias("year"))
        .group_by(["year", "strategy", "energy_mwh"])
        .agg(pl.col("profit_eur").sum(), pl.col("days").sum())
        .with_columns((pl.col("profit_eur") / pl.col("days")).alias("eur_per_mw_day"))
    )

    combined = margin.join(fleet, on="year", how="inner").sort(["year", "strategy", "energy_mwh"])
    if combined.is_empty():
        return combined
    return combined.with_columns(pl.lit("DE-LU").alias("zone"))


def _battery_competition_correlation(
    yearly: pl.DataFrame, *, before_year: int | None
) -> pl.DataFrame:
    """Pearson correlation between the German battery fleet and DE-LU arbitrage margin.

    Same discipline as :func:`_capacity_price_correlation`: the frozen input's
    trailing partial year is excluded from the fit, and every row carries its
    exact fitted range and point count rather than leaving the small sample
    size implicit.
    """
    if yearly.is_empty() or before_year is None:
        return pl.DataFrame()
    complete = yearly.filter(pl.col("year") < str(before_year))
    if complete.is_empty():
        return pl.DataFrame()

    rows: list[dict[str, object]] = []
    for strategy in _BATTERY_COMPETITION_STRATEGIES:
        for duration in _BATTERY_DURATIONS_MWH:
            pair = (
                complete.filter(
                    (pl.col("strategy") == strategy) & (pl.col("energy_mwh") == duration)
                )
                .select("year", "battery_power_gw", "eur_per_mw_day")
                .filter(
                    pl.col("battery_power_gw").is_finite() & pl.col("eur_per_mw_day").is_finite()
                )
            )
            if pair.height < 3:
                continue
            r = pair.select(pl.corr("battery_power_gw", "eur_per_mw_day")).item()
            if r is None or not math.isfinite(r):
                continue
            rows.append(
                {
                    "strategy": strategy,
                    "energy_mwh": duration,
                    "n": pair.height,
                    "pearson_r": r,
                    "fitted_year_min": pair["year"].min(),
                    "fitted_year_max": pair["year"].max(),
                }
            )
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).with_columns(pl.lit("DE-LU").alias("zone"))


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


def _forecast_page_predictions(predictions: pl.DataFrame) -> pl.DataFrame:
    """Keep a small, representative week sample for the browser page.

    The full prediction table remains the input to the battery study above;
    this bounded table only serves the interactive week inspector. Selecting
    evenly spaced Monday weeks preserves coverage across the benchmark while
    avoiding a multi-megabyte browser download on a multi-year release.
    """
    if predictions.is_empty():
        return predictions

    weeks = (
        predictions.select(pl.col("local_date").dt.truncate("1w").alias("week"))
        .unique()
        .sort("week")
    )
    if weeks.height <= _FORECAST_PAGE_WEEKS:
        return predictions

    indices = {
        round(index * (weeks.height - 1) / (_FORECAST_PAGE_WEEKS - 1))
        for index in range(_FORECAST_PAGE_WEEKS)
    }
    selected = weeks.gather(sorted(indices)).get_column("week").to_list()
    return predictions.filter(pl.col("local_date").dt.truncate("1w").is_in(selected))


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
