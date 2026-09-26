"""Retrospective battery economics on a common sample, with exploratory uncertainty.

The best naive is picked in-sample, so it is a diagnostic, not a deployable rule;
cost rates are explicit assumptions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import polars as pl

from gpa.battery import DEFAULT_MODELS, BatterySpec, SpecKwargs, backtest_predictions

BASELINES: Final = DEFAULT_MODELS[:3]
_KEYS: Final = ["strategy", "power_mw", "energy_mwh"]
_TABLES: Final = ("predictions", "dispatch", "summary", "coverage", "daily", "risk", "comparisons")
_SOURCES: Final = ("battery.py", "battery_study.py")
# Read at import, not after a long study during which the workspace could change.
_SOURCE_SNAPSHOT: Final = {name: Path(__file__).with_name(name).read_bytes() for name in _SOURCES}
_MIN_BLOCKS: Final = 8
_RISK_SCHEMA: Final = {
    "strategy": pl.String,
    "power_mw": pl.Float64,
    "energy_mwh": pl.Float64,
    "days": pl.UInt32,
    "profit_eur": pl.Float64,
    "profit_eur_mw": pl.Float64,
    "worst_day_eur": pl.Float64,
    "loss_days": pl.UInt32,
    "max_drawdown_eur": pl.Float64,
    "worst_observed_month": pl.String,
    "worst_observed_month_eur": pl.Float64,
    "worst_month_observed_days": pl.UInt32,
    "top_5_days_share_positive_margin": pl.Float64,
    "best_naive": pl.String,
    "best_naive_selection": pl.String,
    "incremental_vs_best_naive_eur_mw": pl.Float64,
}
_COMPARISON_SCHEMA: Final = {
    "strategy": pl.String,
    "baseline": pl.String,
    "power_mw": pl.Float64,
    "energy_mwh": pl.Float64,
    "paired_days": pl.UInt32,
    "calendar_blocks": pl.UInt32,
    "block_days": pl.UInt32,
    "incremental_eur_mw": pl.Float64,
    "mean_daily_incremental_eur_mw": pl.Float64,
    "underperform_days": pl.UInt32,
    "top_5_days_share_positive_incremental": pl.Float64,
    "incremental_without_best_5_days_eur_mw": pl.Float64,
    "ci_low_eur_mw": pl.Float64,
    "ci_high_eur_mw": pl.Float64,
    "status": pl.String,
}


@dataclass(frozen=True, slots=True)
class BatteryStudy:
    dispatch: pl.DataFrame
    summary: pl.DataFrame
    coverage: pl.DataFrame
    daily: pl.DataFrame
    risk: pl.DataFrame
    comparisons: pl.DataFrame
    assumptions: dict[str, Any]


def _validate_daily(daily: pl.DataFrame) -> pl.DataFrame:
    required = {*_KEYS, "local_date", "profit_eur"}
    if missing := required - set(daily.columns):
        raise ValueError(f"daily economics missing columns: {sorted(missing)}")
    ordered = daily.with_columns(pl.col("local_date").cast(pl.Date)).sort([*_KEYS, "local_date"])
    if ordered.select(pl.any_horizontal(pl.col(sorted(required)).is_null()).any()).item():
        raise ValueError("daily economics must be fully settled and labelled")
    if ordered.filter(
        ~pl.all_horizontal(pl.col("profit_eur", "power_mw", "energy_mwh").is_finite())
        | (pl.col("power_mw") <= 0)
        | (pl.col("energy_mwh") <= 0)
    ).height:
        raise ValueError("daily economics must be finite with positive power and capacity")
    if ordered.select(pl.struct([*_KEYS, "local_date"]).is_duplicated().any()).item():
        raise ValueError("duplicate strategy/asset/day economics")
    for asset in ordered.partition_by(["power_mw", "energy_mwh"]):
        dates = [set(model["local_date"]) for model in asset.partition_by("strategy")]
        if any(days != dates[0] for days in dates[1:]):
            raise ValueError("all strategies must use the same complete days")
    return ordered


def _assets(daily: pl.DataFrame) -> Iterator[dict[str, pl.DataFrame]]:
    """Each asset's validated daily margins, keyed by strategy."""
    for asset in _validate_daily(daily).partition_by(["power_mw", "energy_mwh"]):
        yield {part["strategy"][0]: part for part in asset.partition_by("strategy")}


def _top_five_share(values: npt.NDArray[np.float64]) -> float | None:
    """Share of the positive total carried by the five best days: ex-post concentration."""
    positive = values[values > 0]
    return float(np.sort(positive)[-5:].sum() / positive.sum()) if positive.size else None


def daily_margins(dispatch: pl.DataFrame) -> pl.DataFrame:
    """Daily settlement and activity; missing settlement is an error, never a zero."""
    if dispatch["profit_eur"].null_count():
        raise ValueError("economic study requires fully settled dispatch")
    money = ("gross_revenue_eur", "operating_cost_eur", "degradation_cost_eur", "profit_eur")
    return (
        dispatch.group_by([*_KEYS, "local_date"])
        .agg(
            pl.len().cast(pl.UInt32).alias("intervals"),
            pl.col("duration_hours").sum().alias("delivery_hours"),
            pl.col(*money, "battery_throughput_mwh").sum(),
        )
        .with_columns(
            (pl.col("battery_throughput_mwh") / (2 * pl.col("energy_mwh"))).alias(
                "equivalent_cycles"
            )
        )
        .sort([*_KEYS, "local_date"])
    )


def risk_metrics(daily: pl.DataFrame, *, baselines: Sequence[str] = BASELINES) -> pl.DataFrame:
    """Observed-sample downside, and the margin over the in-sample best naive.

    Drawdown starts from a zero balance; monthly totals cover observed days only,
    and their day counts are published beside them.
    """
    rows = []
    for models in _assets(daily):
        candidates = sorted(name for name in baselines if name in models)
        best = max(candidates, key=lambda n: float(models[n]["profit_eur"].sum()), default=None)
        for name, frame in models.items():
            values = frame["profit_eur"].to_numpy()
            cumulative = np.r_[0.0, np.cumsum(values)]
            worst = (
                frame.group_by(pl.col("local_date").dt.strftime("%Y-%m").alias("month"))
                .agg(pl.col("profit_eur").sum(), pl.len().alias("days"))
                .sort("profit_eur", "month")
                .row(0, named=True)
            )
            power, profit = float(frame["power_mw"][0]), float(values.sum())
            rows.append(
                {
                    "strategy": name,
                    "power_mw": power,
                    "energy_mwh": float(frame["energy_mwh"][0]),
                    "days": len(values),
                    "profit_eur": profit,
                    "profit_eur_mw": profit / power,
                    "worst_day_eur": float(values.min()),
                    "loss_days": int((values < 0).sum()),
                    "max_drawdown_eur": float(
                        (np.maximum.accumulate(cumulative) - cumulative).max()
                    ),
                    "worst_observed_month": worst["month"],
                    "worst_observed_month_eur": worst["profit_eur"],
                    "worst_month_observed_days": worst["days"],
                    "top_5_days_share_positive_margin": _top_five_share(values),
                    "best_naive": best,
                    "best_naive_selection": "retrospective_in_sample" if best else None,
                    "incremental_vs_best_naive_eur_mw": (
                        (profit - float(models[best]["profit_eur"].sum())) / power if best else None
                    ),
                }
            )
    return pl.DataFrame(rows, schema=_RISK_SCHEMA).sort("energy_mwh", "power_mw", "strategy")


def paired_comparisons(
    daily: pl.DataFrame,
    *,
    baselines: Sequence[str] = BASELINES,
    block_days: int = 7,
    resamples: int = 2000,
    seed: int = 20260914,
) -> pl.DataFrame:
    """Exploratory 95% intervals for each model's paired margin over each naive.

    Non-overlapping calendar blocks from the first sample day are resampled whole,
    missing dates are never imputed, and each resample's daily mean is scaled back to
    the observed length. Eight blocks of eight block-lengths are required. The
    intervals are conditional and uncorrected for multiple comparisons.
    """
    if block_days < 1 or resamples < 100 or seed < 0:
        raise ValueError("block_days >= 1, resamples >= 100 and seed >= 0 are required")
    rows = []
    for models in _assets(daily):
        for baseline in sorted(set(baselines) & set(models)):
            reference = models[baseline]
            dates = reference["local_date"].to_list()
            _, codes = np.unique(
                [(day - dates[0]).days // block_days for day in dates], return_inverse=True
            )
            counts = np.bincount(codes)
            blocks = len(counts)
            for name in sorted(set(models) - set(baselines)):
                frame = models[name]
                power = float(frame["power_mw"][0])
                delta = (
                    frame["profit_eur"].to_numpy() - reference["profit_eur"].to_numpy()
                ) / power
                low: float | None = None
                high: float | None = None
                status = "insufficient_blocks" if blocks < _MIN_BLOCKS else "insufficient_days"
                if blocks >= _MIN_BLOCKS and len(delta) >= _MIN_BLOCKS * block_days:
                    totals = np.bincount(codes, weights=delta)
                    draws = np.random.default_rng(seed).integers(
                        0, blocks, size=(resamples, blocks)
                    )
                    margins = totals[draws].sum(axis=1) / counts[draws].sum(axis=1) * len(delta)
                    low, high = map(float, np.quantile(margins, [0.025, 0.975]))
                    status = "exploratory"
                best_five = np.sort(delta[delta > 0])[-5:].sum()
                rows.append(
                    {
                        "strategy": name,
                        "baseline": baseline,
                        "power_mw": power,
                        "energy_mwh": float(frame["energy_mwh"][0]),
                        "paired_days": len(delta),
                        "calendar_blocks": blocks,
                        "block_days": block_days,
                        "incremental_eur_mw": float(delta.sum()),
                        "mean_daily_incremental_eur_mw": float(delta.mean()),
                        "underperform_days": int((delta < 0).sum()),
                        "top_5_days_share_positive_incremental": _top_five_share(delta),
                        "incremental_without_best_5_days_eur_mw": float(delta.sum() - best_five),
                        "ci_low_eur_mw": low,
                        "ci_high_eur_mw": high,
                        "status": status,
                    }
                )
    return pl.DataFrame(rows, schema=_COMPARISON_SCHEMA).sort(
        "energy_mwh", "power_mw", "strategy", "baseline"
    )


def _prediction_hash(predictions: pl.DataFrame) -> str:
    """Fingerprint of the exact supplied values and types, not a rounded presentation."""
    keys = (
        ["model", "ts_utc"]
        if "ts_utc" in predictions.columns
        else ["model", "local_date", "local_hour"]
    )
    frame = predictions.select(sorted(predictions.columns)).sort(keys)
    types = json.dumps({name: str(dtype) for name, dtype in frame.schema.items()}, sort_keys=True)
    return hashlib.sha256((types + frame.write_json()).encode()).hexdigest()


def evaluate(
    predictions: pl.DataFrame,
    *,
    model_names: Sequence[str] = DEFAULT_MODELS,
    durations_mwh: Sequence[float] = (1.0, 2.0, 4.0),
    spec_kwargs: SpecKwargs | None = None,
    block_days: int = 7,
    resamples: int = 2000,
    seed: int = 20260914,
) -> BatteryStudy:
    """Run a DE-LU study from supplied retrospective predictions, without IO."""
    if "zone" in predictions.columns and set(predictions["zone"].unique().to_list()) != {"DE-LU"}:
        raise ValueError("this study requires a single DE-LU prediction sample")
    result = backtest_predictions(
        predictions, model_names=model_names, durations_mwh=durations_mwh, spec_kwargs=spec_kwargs
    )
    if result.dispatch.is_empty():
        raise ValueError("no shared complete settled days; inspect input coverage first")
    daily = daily_margins(result.dispatch)
    start, end = daily["local_date"].min(), daily["local_date"].max()
    assert isinstance(start, dt.date) and isinstance(end, dt.date)
    assumptions: dict[str, Any] = {
        "zone": "DE-LU",
        "timezone": "Europe/Berlin",
        "model_names": list(model_names),
        "durations_mwh": list(durations_mwh),
        "spec_kwargs": dict(spec_kwargs or {}),
        "battery_specs": [
            asdict(BatterySpec(energy_mwh=float(size), **(spec_kwargs or {})))
            for size in durations_mwh
        ],
        "prediction_sha256": _prediction_hash(predictions),
        "input_role": "supplied_retrospective_predictions",
        "sample_start": start.isoformat(),
        "sample_end": end.isoformat(),
        "sample_days": daily["local_date"].n_unique(),
        "schedule": "full_day_one_charge_then_discharge_episode",
        "cost_basis": "absolute_grid_mwh_charge_plus_discharge",
        "cost_status": "user_assumptions_not_market_estimates",
        "initial_terminal_soc": "equal_each_day",
        "bootstrap": "paired_nonoverlapping_calendar_blocks",
        "block_days": block_days,
        "resamples": resamples,
        "seed": seed,
        "confidence": 0.95,
        "baseline_selection": "all_fixed_naive_pairs; best_naive_is_retrospective_in_sample",
        "limitations": [
            "Development backtest; not prospective or investment returns.",
            "Hourly price averages are not quarter-hour forecasts; ambiguous clock-only DST days excluded.",
            "Supplied prediction snapshot does not reproduce upstream model training or source vintages.",
            "Cost assumptions exclude CAPEX, fixed OPEX, taxes and unmodelled execution costs.",
            "Day-level downside and exploratory intervals are conditional on observed eligible days.",
            "No multiplicity correction, seasonal profitability claim or automatic model selection.",
        ],
    }
    return BatteryStudy(
        result.dispatch,
        result.summary,
        result.coverage,
        daily,
        risk_metrics(daily),
        paired_comparisons(daily, block_days=block_days, resamples=resamples, seed=seed),
        assumptions,
    )


def save_study(result: BatteryStudy, predictions: pl.DataFrame, root: Path) -> Path:
    """Save a new content-addressed study with its inputs and calculation code; never overwrite."""
    if result.assumptions["prediction_sha256"] != _prediction_hash(predictions):
        raise ValueError("study input does not match the evaluated predictions")
    code = b"".join(name.encode() + _SOURCE_SNAPSHOT[name] for name in _SOURCES)
    metadata: dict[str, Any] = {
        "assumptions": result.assumptions,
        "environment": {
            "python": platform.python_version(),
            "polars": pl.__version__,
            "numpy": np.__version__,
        },
        "source_sha256": hashlib.sha256(code).hexdigest(),
    }
    identifier = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()[:20]
    path = Path(root) / identifier
    path.mkdir(parents=True, exist_ok=False)
    checksums = {}
    for name in _TABLES:
        file = path / f"{name}.parquet"
        (predictions if name == "predictions" else getattr(result, name)).write_parquet(
            file, compression="zstd"
        )
        checksums[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    for name, source in _SOURCE_SNAPSHOT.items():
        (path / name).write_bytes(source)
        checksums[name] = hashlib.sha256(source).hexdigest()
    # The manifest is written last, so an interrupted save is visibly incomplete.
    metadata |= {"study_id": identifier, "checksums": checksums}
    (path / "manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )
    return path
