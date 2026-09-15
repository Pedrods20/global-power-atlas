"""Fixed diagnostic stresses for the static DE-LU portfolio, not a fitted policy.

Prices and model parameters are frozen. Signal attenuation never reads actuals.
Calendar downtime represents a battery unavailable for an entire known day,
with zero initial/terminal SOC; it does not simulate a mid-cycle forced outage.
The 85% efficiency reference is NREL ATB 2024, not a German asset calibration:
https://atb.nlr.gov/electricity/2024/utility-scale_battery_storage
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from gpa.battery import DEFAULT_MODELS, summarize
from gpa.battery_study import (
    BatteryStudy,
    daily_margins,
    evaluate,
    paired_comparisons,
    risk_metrics,
)


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    variable: float = 0.0
    degradation: float = 0.0
    efficiency: float = 0.9
    retained_signal: float = 1.0
    outage_every_days: int = 0


# Registered in the roadmap before running these scenarios. No selection on P&L.
SCENARIOS = (
    Scenario("base"),
    Scenario("cost_2_3", variable=2.0, degradation=3.0),
    Scenario("cost_5_10", variable=5.0, degradation=10.0),
    Scenario("efficiency_85", efficiency=0.85),
    Scenario("signal_50", retained_signal=0.5),
    Scenario("calendar_downtime", outage_every_days=20),
    Scenario("combined", 2.0, 3.0, 0.85, 0.5, 20),
)
OUTAGE_ANCHOR = dt.date(2025, 1, 1)
_KEYS = ["strategy", "power_mw", "energy_mwh"]
_ACTIVITY = (
    "action_mwh",
    "charge_mwh",
    "discharge_mwh",
    "battery_throughput_mwh",
    "soc_mwh",
    "gross_revenue_eur",
    "operating_cost_eur",
    "degradation_cost_eur",
    "profit_eur",
)


def weaken_signal(predictions: pl.DataFrame, retained_signal: float) -> pl.DataFrame:
    """Shrink fitted-model forecasts toward D-1; preserve actuals and naive rows.

    Physical timestamps are the join key where available, including DST hours.
    A missing D-1 forecast remains missing; common-sample eligibility handles it.
    """
    if not math.isfinite(retained_signal) or not 0 <= retained_signal <= 1:
        raise ValueError("retained_signal must be finite and between zero and one")
    keys = ["ts_utc"] if "ts_utc" in predictions.columns else ["local_date", "local_hour"]
    baseline = predictions.filter(pl.col("model") == "naive_previous_day").select(
        *keys, pl.col("forecast").alias("_reference")
    )
    if baseline.is_empty():
        raise ValueError("previous-day forecasts are required for signal attenuation")
    return (
        predictions.join(baseline, on=keys, how="left", validate="m:1")
        .with_columns(
            pl.when(pl.col("model").is_in(["ridge", "lightgbm"]))
            .then(
                pl.col("_reference") + retained_signal * (pl.col("forecast") - pl.col("_reference"))
            )
            .otherwise(pl.col("forecast"))
            .alias("forecast")
        )
        .drop("_reference")
        .select(predictions.columns)
    )


def calendar_outages(dispatch: pl.DataFrame, every_days: int = 20) -> pl.DataFrame:
    """Zero physical activity and settlement, not observations or sample days."""
    if every_days < 1:
        raise ValueError("every_days must be positive")
    unavailable = (
        (pl.col("local_date").cast(pl.Date) - pl.lit(OUTAGE_ANCHOR)).dt.total_days() % every_days
    ) == 0
    return dispatch.with_columns(
        pl.when(unavailable).then(0.0).otherwise(pl.col(c)).alias(c) for c in _ACTIVITY
    )


def scenario_tables(
    predictions: pl.DataFrame,
    base: BatteryStudy,
    *,
    model_names: Sequence[str] = DEFAULT_MODELS,
    durations_mwh: Sequence[float] = (1.0, 2.0, 4.0),
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Cost rows and full stress scorecards on identical eligible sample days.

    Base must be the zero-cost/default-physics study of these same predictions.
    The export owns that call; explicit checks reject accidental sample drift.
    All figures are unannualized, including the removal-of-best-days diagnostic.
    """
    costs: list[pl.DataFrame] = []
    stresses: list[pl.DataFrame] = []
    base_days = set(base.daily["local_date"].to_list())
    for scenario in SCENARIOS:
        study = base
        if scenario.name not in {"base", "calendar_downtime"}:
            inputs = (
                weaken_signal(predictions, scenario.retained_signal)
                if scenario.retained_signal != 1
                else predictions
            )
            study = evaluate(
                inputs,
                model_names=model_names,
                durations_mwh=durations_mwh,
                spec_kwargs={
                    "round_trip_efficiency": scenario.efficiency,
                    "variable_cost_eur_mwh": scenario.variable,
                    "degradation_cost_eur_mwh": scenario.degradation,
                },
            )
        dispatched = study.dispatch
        daily, risk, comparisons, summary = (
            study.daily,
            study.risk,
            study.comparisons,
            study.summary,
        )
        if scenario.outage_every_days:
            dispatched = calendar_outages(dispatched, scenario.outage_every_days)
            daily = daily_margins(dispatched)
            risk, comparisons, summary = (
                risk_metrics(daily),
                paired_comparisons(daily),
                summarize(dispatched),
            )
        if set(daily["local_date"].to_list()) != base_days:
            raise ValueError("sensitivity changed the common eligible sample")
        days = daily.select("local_date").unique()
        outage_days = (
            days.filter(
                (
                    (pl.col("local_date") - pl.lit(OUTAGE_ANCHOR)).dt.total_days()
                    % scenario.outage_every_days
                )
                == 0
            ).height
            if scenario.outage_every_days
            else 0
        )
        # Join the corresponding fixed comparator's diagnostics, never a daily oracle.
        selected = comparisons.rename({"baseline": "best_naive"}).drop("incremental_eur_mw")
        rows = risk.join(
            summary.select(
                *_KEYS,
                "gross_revenue_eur",
                "operating_cost_eur",
                "degradation_cost_eur",
                "equivalent_cycles",
            ),
            on=_KEYS,
            validate="1:1",
        ).join(selected, on=[*_KEYS, "best_naive"], how="left", validate="1:1")
        rows = rows.with_columns(
            pl.lit(scenario.name).alias("scenario"),
            pl.lit(scenario.variable).alias("variable_cost_eur_mwh"),
            pl.lit(scenario.degradation).alias("degradation_cost_eur_mwh"),
            pl.lit(scenario.efficiency).alias("round_trip_efficiency"),
            pl.lit(scenario.retained_signal).alias("retained_signal"),
            pl.lit(outage_days).alias("unavailable_days"),
            pl.lit(len(base_days) - outage_days).alias("available_days"),
            pl.lit(min(base_days).isoformat()).alias("sample_start"),
            pl.lit(max(base_days).isoformat()).alias("sample_end"),
            pl.lit("retrospective_diagnostic_not_calibrated_asset_economics").alias(
                "evidence_status"
            ),
        )
        stresses.append(rows)
        if scenario.name in {"base", "cost_2_3", "cost_5_10"}:
            costs.append(
                rows.select(
                    *_KEYS,
                    "profit_eur",
                    "gross_revenue_eur",
                    "operating_cost_eur",
                    "degradation_cost_eur",
                    "variable_cost_eur_mwh",
                    "degradation_cost_eur_mwh",
                    "best_naive",
                    "incremental_vs_best_naive_eur_mw",
                )
            )
    return pl.concat(costs), pl.concat(stresses)
