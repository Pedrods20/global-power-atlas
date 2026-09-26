"""The small tables the static site reads, from the store and the frozen release.

The browser never touches the hourly store. Tables are canonically sorted, and
``gpa export --check`` rebuilds them to prove the committed copies still match.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import polars as pl
from polars.testing import assert_frame_equal

from gpa import reference, store
from gpa.battery import DEFAULT_MODELS
from gpa.metrics import mix as mix_metrics
from gpa.metrics import price as price_metrics
from gpa.zones import Zone, get_zone

__all__ = ["check_exports", "export_all", "release_predictions", "site_root"]

ZONE = get_zone("DE-LU")
_DURATIONS_MWH = (1.0, 2.0, 4.0)
_FORECAST_PAGE_WEEKS = 12

_CORRELATION_PAIRS = (
    ("solar_capacity_gw", "solar_capture_rate", "Solar capacity vs. solar capture rate"),
    ("solar_capacity_gw", "negative_pct", "Solar capacity vs. negative-price frequency"),
    ("wind_capacity_gw", "wind_capture_rate", "Wind capacity vs. wind capture rate"),
    (
        "solar_capacity_gw",
        "spread_pct_of_price",
        "Solar capacity vs. on/off-peak spread (% of average price)",
    ),
)
"""The spread is taken as a share of the year's price, or the gas shock alone would dominate."""

_COMPETITION_STRATEGIES = ("perfect_foresight", "ridge", "lightgbm")
"""The ceiling tests whether competition shrinks the opportunity; the models show what is captured."""

_PLANNED_TO_REALISED = {
    "Solar planned (EEG 2023)": ("Solar AC", "Solar DC"),
    "Wind onshore planned (EEG 2023)": ("Wind onshore",),
    "Wind offshore planned (WindSeeG)": ("Wind offshore",),
}
"""EEG 2023's solar convention is unverified, so its target is read against both AC and DC."""

_MEASURED = ("load", "generation")
"""What ``data_as_of`` reads: day-ahead prices run past now and would date the page in the future."""


def site_root() -> Path:
    return Path(__file__).resolve().parents[2] / "site" / "data"


def export_all(output: Path | None = None) -> dict[str, int]:
    """Write every site table; returns row counts by file name."""
    destination = Path(output or site_root())
    destination.mkdir(parents=True, exist_ok=True)
    prices = store.read("price", ZONE.code)
    generation = store.read("generation", ZONE.code)
    capacity = reference.read("capacity")
    metadata, frames = _release()
    predictions = _rounded_predictions(metadata, frames)
    price_cutoff = _annual_fit_cutoff(prices, ZONE)
    yearly = _capacity_price_yearly(prices, generation, capacity)
    battery = _battery_tables(predictions)
    margin = _battery_margin_yearly(battery["battery_monthly"], capacity)
    release_cutoff = _annual_fit_cutoff(frames["predictions"], get_zone(str(metadata["zone"])))
    tables = {
        "daily_prices": _daily_prices(prices),
        "generation_mix": _tag(
            mix_metrics.generation_mix(generation, ZONE).rename({"period": "month"})
        ),
        "capacity": _tag(capacity),
        "cannibalisation": _cannibalisation(prices, generation),
        "price_shape": _price_shape(prices),
        "fundamentals_ablation": reference.read("fundamentals_ablation"),
        "capacity_price_yearly": yearly,
        "capacity_price_correlation": _capacity_price_correlation(yearly, price_cutoff),
        "capacity_extrapolation_flags": _capacity_extrapolation_flags(capacity, price_cutoff),
        "forecast_scores": _tag(frames["scores"]),
        "forecast_daily": _tag(frames["daily"]),
        "forecast_preview": _forecast_page_predictions(predictions),
        **battery,
        "battery_margin_yearly": margin,
        "battery_competition_correlation": _battery_competition_correlation(margin, release_cutoff),
    }
    written = {}
    for name, frame in tables.items():
        _canonical_sort(_stringify_dates(frame)).write_parquet(
            destination / f"{name}.parquet", compression="zstd", statistics=True
        )
        written[f"{name}.parquet"] = frame.height
    for name, payload in (
        ("data_as_of.json", {"data_as_of": _data_as_of()}),
        ("forecast.json", {"runs": [metadata]}),
    ):
        (destination / name).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        written[name] = 1
    return written


def release_predictions() -> pl.DataFrame:
    """Every prediction of the frozen release, rounded exactly as the battery tables read them."""
    return _rounded_predictions(*_release())


def check_exports(destination: Path | None = None) -> list[str]:
    """Rebuild every table and list those whose values differ from the committed copy."""
    target = destination or site_root()
    differences = []
    with tempfile.TemporaryDirectory(prefix="gpa-export-") as temp:
        for name in export_all(Path(temp)):
            expected, actual = target / name, Path(temp) / name
            if not expected.exists():
                differences.append(name)
            elif name.endswith(".json"):
                if not _close(_json(expected), _json(actual)):
                    differences.append(name)
            else:
                try:
                    assert_frame_equal(
                        _canonical_sort(pl.read_parquet(expected)),
                        _canonical_sort(pl.read_parquet(actual)),
                    )
                except (AssertionError, pl.exceptions.PolarsError) as exc:
                    differences.append(f"{name} ({exc})")
    return differences


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _close(expected: object, actual: object) -> bool:
    """JSON equality tolerating the float noise another platform's BLAS or LightGBM adds."""
    if isinstance(expected, float) and isinstance(actual, float):
        return math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-9)
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _close(expected[key], actual[key]) for key in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(map(_close, expected, actual))
    return expected == actual


def _release() -> tuple[dict[str, object], dict[str, pl.DataFrame]]:
    from gpa.forecast import snapshot

    saved = snapshot.read()
    if saved is None:
        raise RuntimeError(
            "no frozen forecast snapshot under data/experiments/: the site reads a committed "
            "release, not a live recompute. Run `gpa backtest --save-snapshot`, commit it, retry."
        )
    return saved


def _rounded_predictions(
    metadata: dict[str, object], frames: dict[str, pl.DataFrame]
) -> pl.DataFrame:
    return (
        frames["predictions"]
        .drop("ts_utc")
        .with_columns(pl.col(pl.Float64).round(2), pl.lit(metadata["zone"]).alias("zone"))
    )


def _tag(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.lit(ZONE.code).alias("zone")) if not frame.is_empty() else frame


def _canonical_sort(frame: pl.DataFrame) -> pl.DataFrame:
    """Sort by exact columns first, floats last, so summation noise cannot reorder rows."""
    floats = [name for name, dtype in frame.schema.items() if dtype in (pl.Float32, pl.Float64)]
    exact = [name for name in frame.columns if name not in floats]
    return frame.sort([*exact, *floats]) if frame.columns else frame


def _stringify_dates(frame: pl.DataFrame) -> pl.DataFrame:
    """Dates as ISO strings: Arrow date types reach JavaScript differently across builds."""
    if frame.is_empty():
        return frame
    return frame.with_columns(
        [
            pl.col(name).dt.strftime("%Y-%m-%d" if dtype == pl.Date else "%Y-%m-%dT%H:%M:%SZ")
            for name, dtype in frame.schema.items()
            if dtype == pl.Date or isinstance(dtype, pl.Datetime)
        ]
    )


def _daily_prices(prices: pl.DataFrame) -> pl.DataFrame:
    blocks = price_metrics.block_prices(prices, ZONE, period="day").rename({"period": "date"})
    if blocks.is_empty():
        return pl.DataFrame()
    gap = pl.col("date").str.to_date().diff().dt.total_days().fill_null(1) > 1
    return _tag(
        blocks.sort("date").with_columns(
            pl.lit(ZONE.currency).alias("currency"), gap.cum_sum().alias("segment")
        )
    )


def _cannibalisation(prices: pl.DataFrame, generation: pl.DataFrame) -> pl.DataFrame:
    frames = [
        price_metrics.capture_rate(prices, generation, ZONE, fuel=fuel, period="year").with_columns(
            pl.lit(fuel).alias("fuel")
        )
        for fuel in ("solar", "wind")
    ]
    frames = [frame for frame in frames if not frame.is_empty()]
    return _tag(pl.concat(frames)) if frames else pl.DataFrame()


def _capacity_price_yearly(
    prices: pl.DataFrame, generation: pl.DataFrame, capacity: pl.DataFrame
) -> pl.DataFrame:
    """Realised solar and wind capacity per year beside the price-shape metrics.

    The block spread and the within-day range point in opposite directions here;
    carrying both is what lets the site show the divergence. The current partial
    year stays in this descriptive table and is excluded only from fitted statistics.
    """
    realised = capacity.filter((pl.col("time_step") == "yearly") & ~pl.col("is_planned"))
    if prices.is_empty() or generation.is_empty() or realised.is_empty():
        return pl.DataFrame()
    year = pl.col("period").alias("year")
    solar = realised.filter(pl.col("technology") == "Solar AC").select(
        year, pl.col("value").alias("solar_capacity_gw")
    )
    wind = (
        realised.filter(pl.col("technology").is_in(["Wind onshore", "Wind offshore"]))
        .group_by("period")
        .agg(pl.col("value").sum().alias("wind_capacity_gw"))
        .select(year, "wind_capacity_gw")
    )
    spread = price_metrics.block_prices(prices, ZONE, period="year").select(
        year,
        "spread",
        (pl.col("spread") / pl.col("all_hours") * 100.0).alias("spread_pct_of_price"),
        pl.col("all_hours").alias("baseload_price"),
    )
    intraday = price_metrics.intraday_spread(prices, ZONE, period="year").select(
        year,
        pl.col("mean_spread").alias("intraday_spread"),
        pl.col("median_spread").alias("intraday_spread_median"),
        pl.col("n_days").alias("intraday_n_days"),
    )
    negative = price_metrics.negative_share(prices, ZONE).select(year, "negative_pct")
    captures = [
        price_metrics.capture_rate(prices, generation, ZONE, fuel=fuel, period="year").select(
            year, pl.col("capture_rate").alias(f"{fuel}_capture_rate")
        )
        for fuel in ("solar", "wind")
    ]
    combined = spread.join(solar, on="year")
    for part in (wind, negative, *captures, intraday):
        combined = combined.join(part, on="year", how="left")
    if combined.is_empty():
        return combined
    return _tag(
        combined.sort("year").with_columns(
            # Scaled by the year's own baseload, so a level shock and a shape change differ.
            (pl.col("intraday_spread") / pl.col("baseload_price") * 100.0).alias(
                "intraday_spread_pct_of_price"
            )
        )
    )


def _price_shape(prices: pl.DataFrame) -> pl.DataFrame:
    """Mean price per local hour by year, also as a percent of that year's baseload."""
    shape = price_metrics.hourly_shape(prices, ZONE, period="year")
    if shape.is_empty():
        return pl.DataFrame()
    baseload = price_metrics.block_prices(prices, ZONE, period="year").select(
        "period", pl.col("all_hours").alias("baseload_price")
    )
    return _tag(
        shape.join(baseload, on="period", how="left")
        .with_columns((pl.col("price") / pl.col("baseload_price") * 100.0).alias("pct_of_baseload"))
        .rename({"period": "year"})
        .select(
            "year",
            "local_hour",
            "price",
            "pct_of_baseload",
            "baseload_price",
            "observed_hours",
            "n_intervals",
        )
        .sort("year", "local_hour")
    )


def _annual_fit_cutoff(intervals: pl.DataFrame, zone: Zone) -> int | None:
    """The year the input's last interval ends in: an exclusive bound that drops a partial year."""
    if intervals.is_empty():
        return None
    minutes = pl.col("resolution_min") if "resolution_min" in intervals.columns else pl.lit(60)
    end = (pl.col("ts_utc") + pl.duration(minutes=minutes)).max()
    return int(intervals.select(end.dt.convert_time_zone(zone.timezone).dt.year()).item())


def _complete_years(yearly: pl.DataFrame, before_year: int | None) -> pl.DataFrame:
    if yearly.is_empty() or before_year is None:
        return pl.DataFrame()
    return yearly.filter(pl.col("year") < str(before_year))


def _correlations(
    complete: pl.DataFrame, specs: Sequence[tuple[Mapping[str, object], str, str, pl.Expr | None]]
) -> pl.DataFrame:
    """Pearson r per spec, with the exact fitted year range beside it.

    About seven annual points that share a trend: a description, never a causal
    estimate, and fewer than three points are not fitted at all.
    """
    rows = []
    for labels, x, y, subset in specs if not complete.is_empty() else []:
        pair = (complete if subset is None else complete.filter(subset)).select("year", x, y)
        pair = pair.filter(pl.col(x).is_finite() & pl.col(y).is_finite())
        r = pair.select(pl.corr(x, y)).item() if pair.height >= 3 else None
        if r is not None and math.isfinite(r):
            rows.append(
                {
                    **labels,
                    "n": pair.height,
                    "pearson_r": r,
                    "fitted_year_min": pair["year"].min(),
                    "fitted_year_max": pair["year"].max(),
                }
            )
    return _tag(pl.DataFrame(rows)) if rows else pl.DataFrame()


def _capacity_price_correlation(yearly: pl.DataFrame, before_year: int | None) -> pl.DataFrame:
    specs = [({"x": x, "y": y, "label": label}, x, y, None) for x, y, label in _CORRELATION_PAIRS]
    return _correlations(_complete_years(yearly, before_year), specs)


def _battery_competition_correlation(yearly: pl.DataFrame, before_year: int | None) -> pl.DataFrame:
    specs = [
        (
            {"strategy": strategy, "energy_mwh": duration},
            "battery_power_gw",
            "eur_per_mw_day",
            (pl.col("strategy") == strategy) & (pl.col("energy_mwh") == duration),
        )
        for strategy in _COMPETITION_STRATEGIES
        for duration in _DURATIONS_MWH
    ]
    return _correlations(_complete_years(yearly, before_year), specs)


def _capacity_extrapolation_flags(capacity: pl.DataFrame, before_year: int | None) -> pl.DataFrame:
    """Whether each 2030 target lies above the realised maximum the correlations were fitted on."""
    yearly = capacity.filter(pl.col("time_step") == "yearly")
    if yearly.is_empty() or before_year is None:
        return pl.DataFrame()
    realised = (
        yearly.filter(~pl.col("is_planned") & (pl.col("period") < str(before_year)))
        .group_by("technology")
        .agg(
            pl.col("value").max().alias("realised_max_gw"),
            pl.col("period").sort_by("value", descending=True).first().alias("realised_max_year"),
        )
    )
    planned = yearly.filter(pl.col("is_planned") & (pl.col("period") == "2030")).select(
        pl.col("technology").alias("planned_technology"), pl.col("value").alias("planned_2030_gw")
    )
    rows = [
        realised.filter(pl.col("technology").is_in(list(technologies))).join(
            planned.filter(pl.col("planned_technology") == target), how="cross"
        )
        for target, technologies in _PLANNED_TO_REALISED.items()
    ]
    rows = [row for row in rows if not row.is_empty()]
    if not rows:
        return pl.DataFrame()
    return (
        _tag(
            pl.concat(rows).with_columns(
                (pl.col("planned_2030_gw") > pl.col("realised_max_gw")).alias(
                    "exceeds_realised_max"
                )
            )
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
        .sort("planned_technology", "technology")
    )


def _battery_margin_yearly(monthly: pl.DataFrame, capacity: pl.DataFrame) -> pl.DataFrame:
    """Yearly arbitrage margin in EUR/MW/day beside the German battery fleet.

    Yearly, because monthly margins carry weather and price seasonality unrelated to
    fleet size; per day, so a partial year is comparable.
    """
    technologies = {
        "Battery storage (power)": "battery_power_gw",
        "Battery storage (capacity)": "battery_energy_gwh",
    }
    fleet = capacity.filter(
        (pl.col("time_step") == "yearly")
        & ~pl.col("is_planned")
        & pl.col("technology").is_in(list(technologies))
    )
    if monthly.is_empty() or fleet.is_empty():
        return pl.DataFrame()
    present = set(fleet["technology"].to_list())
    fleet = fleet.pivot(on="technology", index="period", values="value").with_columns(
        [pl.lit(None, dtype=pl.Float64).alias(name) for name in technologies if name not in present]
    )
    margin = (
        monthly.filter(pl.col("strategy").is_in(list(_COMPETITION_STRATEGIES)))
        .with_columns(pl.col("month").str.slice(0, 4).alias("year"))
        .group_by("year", "strategy", "energy_mwh")
        .agg(pl.col("profit_eur").sum(), pl.col("days").sum())
        .with_columns((pl.col("profit_eur") / pl.col("days")).alias("eur_per_mw_day"))
    )
    combined = margin.join(fleet.rename({"period": "year", **technologies}), on="year").sort(
        "year", "strategy", "energy_mwh"
    )
    return _tag(combined)


def _battery_tables(predictions: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """The storage page's economics, frozen with the same release as the forecast."""
    from gpa.battery_sensitivity import scenario_tables
    from gpa.battery_study import evaluate

    names = (
        "battery_monthly",
        "battery_dispatch_example",
        "battery_summary",
        "battery_risk",
        "battery_comparisons",
        "battery_coverage",
        "battery_costs",
        "battery_sensitivities",
    )
    if predictions.is_empty():
        return dict.fromkeys(names, pl.DataFrame())
    base = evaluate(predictions, model_names=DEFAULT_MODELS, durations_mwh=_DURATIONS_MWH)
    costs, sensitivities = scenario_tables(
        predictions, base, model_names=DEFAULT_MODELS, durations_mwh=_DURATIONS_MWH
    )
    # The page draws monthly margin and one example day, not a million dispatch rows.
    monthly = base.daily.group_by(
        "strategy",
        "power_mw",
        "energy_mwh",
        pl.col("local_date").dt.strftime("%Y-%m").alias("month"),
    ).agg(pl.len().cast(pl.UInt32).alias("days"), pl.col("profit_eur").sum())
    example = base.dispatch.filter(pl.col("local_date") == pl.col("local_date").max())
    frames = (
        monthly,
        example,
        base.summary,
        base.risk,
        base.comparisons,
        base.coverage,
        costs,
        sensitivities,
    )
    return {name: _tag(frame) for name, frame in zip(names, frames, strict=True)}


def _forecast_page_predictions(predictions: pl.DataFrame) -> pl.DataFrame:
    """Twelve evenly spaced Monday weeks for the page's inspector, not the whole release."""
    week = pl.col("local_date").dt.truncate("1w")
    weeks = predictions.select(week.alias("week")).unique().sort("week")["week"]
    if len(weeks) <= _FORECAST_PAGE_WEEKS:
        return predictions
    last = len(weeks) - 1
    picks = sorted(
        {round(i * last / (_FORECAST_PAGE_WEEKS - 1)) for i in range(_FORECAST_PAGE_WEEKS)}
    )
    return predictions.filter(week.is_in(weeks.gather(picks).to_list()))


def _data_as_of() -> str | None:
    """The latest observed instant, from the store rather than the build clock."""
    measured = store.coverage().filter(pl.col("dataset").is_in(list(_MEASURED)))
    latest = measured["last_ts_utc"].max() if not measured.is_empty() else None
    return latest.isoformat() if isinstance(latest, dt.datetime) else None
