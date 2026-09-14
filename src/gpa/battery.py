"""Constrained battery dispatch for the DE-LU forecast study.

This module deliberately keeps the economic experiment small and inspectable.
At every interval a dynamic program chooses the first action of a rolling
horizon using only the supplied signal.  The action is then settled against the
observed price, so forecast error becomes economic error rather than a chart
annotation.

The current published forecast is an hourly clock-hour benchmark.  The
optimizer accepts an explicit ``duration_hours`` column and therefore keeps the
power limit correct when a quarter-hour forecast is introduced later.  A
quarter-hour run should use a finer ``soc_step_mwh`` than the default hourly
study.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, cast

import polars as pl

__all__ = [
    "BatteryBacktestResult",
    "BatterySpec",
    "backtest_predictions",
    "dispatch",
    "summarize",
]

_OUTPUT_SCHEMA: Final[pl.Schema] = pl.Schema(
    {
        "local_date": pl.Date,
        "local_hour": pl.Int8,
        "forecast": pl.Float64,
        "actual": pl.Float64,
        "action_mwh": pl.Float64,
        "charge_mwh": pl.Float64,
        "discharge_mwh": pl.Float64,
        "battery_throughput_mwh": pl.Float64,
        "soc_mwh": pl.Float64,
        "gross_revenue_eur": pl.Float64,
        "operating_cost_eur": pl.Float64,
        "degradation_cost_eur": pl.Float64,
        "profit_eur": pl.Float64,
        "strategy": pl.String,
        "power_mw": pl.Float64,
        "energy_mwh": pl.Float64,
        "duration_hours": pl.Float64,
    }
)


@dataclass(frozen=True, slots=True)
class BatterySpec:
    """Physical and economic assumptions for one battery experiment."""

    power_mw: float = 1.0
    energy_mwh: float = 4.0
    round_trip_efficiency: float = 0.90
    soc_step_mwh: float = 0.25
    variable_cost_eur_mwh: float = 0.0
    degradation_cost_eur_mwh: float = 0.0
    initial_soc_mwh: float = 0.0
    max_cycles_per_day: float = 1.0

    def __post_init__(self) -> None:
        if self.power_mw <= 0.0 or self.energy_mwh <= 0.0:
            raise ValueError("battery power and energy must be positive")
        if not 0.0 < self.round_trip_efficiency <= 1.0:
            raise ValueError("round_trip_efficiency must be in (0, 1]")
        if self.soc_step_mwh <= 0.0 or self.soc_step_mwh > self.energy_mwh:
            raise ValueError("soc_step_mwh must be positive and no larger than capacity")
        if self.variable_cost_eur_mwh < 0.0 or self.degradation_cost_eur_mwh < 0.0:
            raise ValueError("battery costs cannot be negative")
        if not 0.0 < self.max_cycles_per_day <= 1.0:
            raise ValueError("max_cycles_per_day must be in (0, 1]")
        if not 0.0 <= self.initial_soc_mwh <= self.energy_mwh:
            raise ValueError("initial_soc_mwh must be inside the battery capacity")
        steps = self.energy_mwh / self.soc_step_mwh
        if not math.isclose(steps, round(steps), abs_tol=1e-9):
            raise ValueError("energy_mwh must be divisible by soc_step_mwh")
        if not math.isclose(
            self.initial_soc_mwh / self.soc_step_mwh,
            round(self.initial_soc_mwh / self.soc_step_mwh),
            abs_tol=1e-9,
        ):
            raise ValueError("initial_soc_mwh must lie on the SOC grid")

    @property
    def charge_efficiency(self) -> float:
        """One-way efficiency applied to energy entering the battery."""
        return math.sqrt(self.round_trip_efficiency)

    @property
    def discharge_efficiency(self) -> float:
        """One-way efficiency applied to energy leaving the battery."""
        return math.sqrt(self.round_trip_efficiency)


@dataclass(frozen=True, slots=True)
class BatteryBacktestResult:
    """Dispatch rows and the economic scoreboard derived from them."""

    dispatch: pl.DataFrame
    summary: pl.DataFrame


def dispatch(
    frame: pl.DataFrame,
    spec: BatterySpec,
    *,
    strategy: str,
    horizon_steps: int = 24,
) -> pl.DataFrame:
    """Dispatch a battery against forecast signals and settle on actual prices.

    ``frame`` must have ``local_date``, ``local_hour`` and ``forecast``.  An
    ``actual`` column is required for economic settlement but may contain nulls
    for a live, not-yet-reconciled path.  Only complete local days are used;
    incomplete DST or provider days are not silently filled.

    Strategies are labels, not alternate information sets. ``perfect_foresight``
    is implemented by passing realised prices as ``forecast``. It is therefore
    an upper bound under the same rolling horizon and physical constraints, not
    a claim that a real trader could have known the future.
    """
    if not strategy:
        raise ValueError("strategy must not be empty")
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be positive")
    prepared = _prepare(frame)
    if prepared.is_empty():
        return pl.DataFrame(schema=_OUTPUT_SCHEMA)

    rows: list[dict[str, object]] = []
    for block in _contiguous_blocks(prepared):
        soc = spec.initial_soc_mwh
        current_date: dt.date | None = None
        phase = 0
        for index in range(block.height):
            day = block["local_date"][index]
            if day != current_date:
                current_date = day
                phase = 0
            remaining_day = block.filter(pl.col("local_date") == day).filter(
                pl.col("local_hour") >= block["local_hour"][index]
            )
            horizon = remaining_day.head(horizon_steps)
            signal = horizon["forecast"].to_list()
            durations = horizon["duration_hours"].to_list()
            terminal = spec.initial_soc_mwh if horizon.height == remaining_day.height else None
            next_soc, grid_mwh, next_phase = _first_action(
                soc,
                phase,
                signal,
                durations,
                spec,
                terminal_soc_mwh=terminal,
            )
            current = block.row(index, named=True)
            actual = _float_or_none(current["actual"])
            duration = float(current["duration_hours"])
            charge = max(0.0, -grid_mwh)
            discharge = max(0.0, grid_mwh)
            battery_throughput = (
                charge * spec.charge_efficiency + discharge / spec.discharge_efficiency
            )
            operating = (charge + discharge) * spec.variable_cost_eur_mwh
            degradation = (charge + discharge) * spec.degradation_cost_eur_mwh
            gross = grid_mwh * actual if actual is not None else None
            profit = gross - operating - degradation if gross is not None else None
            rows.append(
                {
                    "local_date": current["local_date"],
                    "local_hour": current["local_hour"],
                    "forecast": current["forecast"],
                    "actual": actual,
                    "action_mwh": grid_mwh,
                    "charge_mwh": charge,
                    "discharge_mwh": discharge,
                    "battery_throughput_mwh": battery_throughput,
                    "soc_mwh": next_soc,
                    "gross_revenue_eur": gross,
                    "operating_cost_eur": operating,
                    "degradation_cost_eur": degradation,
                    "profit_eur": profit,
                    "strategy": strategy,
                    "power_mw": spec.power_mw,
                    "energy_mwh": spec.energy_mwh,
                    "duration_hours": duration,
                }
            )
            soc = next_soc
            phase = next_phase

    if not rows:
        return pl.DataFrame(schema=_OUTPUT_SCHEMA)
    return pl.DataFrame(rows).cast(_OUTPUT_SCHEMA).sort(["local_date", "local_hour"])


def summarize(dispatch_frame: pl.DataFrame) -> pl.DataFrame:
    """Aggregate dispatch economics and capture versus constrained foresight."""
    schema = {
        "strategy": pl.String,
        "energy_mwh": pl.Float64,
        "days": pl.UInt32,
        "intervals": pl.UInt32,
        "profit_eur": pl.Float64,
        "gross_revenue_eur": pl.Float64,
        "operating_cost_eur": pl.Float64,
        "degradation_cost_eur": pl.Float64,
        "throughput_mwh": pl.Float64,
        "equivalent_cycles": pl.Float64,
        "capture_vs_perfect": pl.Float64,
    }
    if dispatch_frame.is_empty():
        return pl.DataFrame(schema=schema)

    grouped = (
        dispatch_frame.group_by(["strategy", "energy_mwh"])
        .agg(
            pl.col("local_date").n_unique().cast(pl.UInt32).alias("days"),
            pl.col("profit_eur").count().cast(pl.UInt32).alias("intervals"),
            pl.col("profit_eur").sum().alias("profit_eur"),
            pl.col("gross_revenue_eur").sum().alias("gross_revenue_eur"),
            pl.col("operating_cost_eur").sum().alias("operating_cost_eur"),
            pl.col("degradation_cost_eur").sum().alias("degradation_cost_eur"),
            pl.col("battery_throughput_mwh").sum().alias("throughput_mwh"),
        )
        .with_columns(
            (pl.col("throughput_mwh") / (2.0 * pl.col("energy_mwh"))).alias("equivalent_cycles")
        )
    )
    perfect = grouped.filter(pl.col("strategy") == "perfect_foresight").select(
        pl.col("energy_mwh"), pl.col("profit_eur").alias("_perfect_profit")
    )
    return (
        grouped.join(perfect, on="energy_mwh", how="left")
        .with_columns(
            pl.when(pl.col("_perfect_profit") > 0.0)
            .then(pl.col("profit_eur") / pl.col("_perfect_profit"))
            .otherwise(None)
            .alias("capture_vs_perfect")
        )
        .drop("_perfect_profit")
        .select(list(schema))
        .sort(["energy_mwh", "strategy"])
    )


def backtest_predictions(
    predictions: pl.DataFrame,
    *,
    model_names: Iterable[str] = ("ridge", "lightgbm", "naive_previous_week"),
    durations_mwh: Sequence[float] = (1.0, 4.0),
    horizon_steps: int = 24,
    spec_kwargs: dict[str, float] | None = None,
) -> BatteryBacktestResult:
    """Run forecast-guided, no-trade and constrained-foresight strategies.

    The input is the common-sample output of the price backtest. Each model is
    evaluated only on its own complete days, and all economic settlement uses
    the shared ``actual`` column. ``spec_kwargs`` is intentionally explicit so
    changing degradation or efficiency becomes visible in a reproducible run.
    """
    required = {"model", "local_date", "local_hour", "forecast", "actual"}
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    if not durations_mwh or any(duration <= 0.0 for duration in durations_mwh):
        raise ValueError("durations_mwh must contain positive capacities")
    kwargs = spec_kwargs or {}
    outputs: list[pl.DataFrame] = []
    for duration in durations_mwh:
        spec = BatterySpec(energy_mwh=float(duration), **kwargs)
        actuals = _complete_days(
            predictions.select("local_date", "local_hour", "actual").unique(
                ["local_date", "local_hour"], keep="first"
            )
        )
        outputs.append(
            dispatch(
                actuals.with_columns(pl.col("actual").alias("forecast")),
                spec,
                strategy="perfect_foresight",
                horizon_steps=horizon_steps,
            )
        )
        outputs.append(
            dispatch(
                actuals.with_columns(pl.lit(0.0).alias("forecast")),
                spec,
                strategy="no_trade",
                horizon_steps=horizon_steps,
            ).with_columns(
                pl.lit(0.0).alias("action_mwh"),
                pl.lit(0.0).alias("charge_mwh"),
                pl.lit(0.0).alias("discharge_mwh"),
                pl.lit(0.0).alias("soc_mwh"),
                pl.lit(0.0).alias("gross_revenue_eur"),
                pl.lit(0.0).alias("operating_cost_eur"),
                pl.lit(0.0).alias("degradation_cost_eur"),
                pl.lit(0.0).alias("profit_eur"),
            )
        )
        for model in model_names:
            candidate = _complete_days(predictions.filter(pl.col("model") == model))
            if candidate.is_empty():
                continue
            outputs.append(dispatch(candidate, spec, strategy=model, horizon_steps=horizon_steps))
    if not outputs:
        empty = pl.DataFrame(schema=_OUTPUT_SCHEMA)
        return BatteryBacktestResult(empty, summarize(empty))
    combined = pl.concat(outputs, how="vertical_relaxed").sort(
        ["energy_mwh", "local_date", "local_hour", "strategy"]
    )
    return BatteryBacktestResult(combined, summarize(combined))


def _prepare(frame: pl.DataFrame) -> pl.DataFrame:
    missing = {"local_date", "local_hour", "forecast"} - set(frame.columns)
    if missing:
        raise ValueError(f"dispatch frame missing columns: {sorted(missing)}")
    prepared = frame
    if "actual" not in prepared.columns:
        prepared = prepared.with_columns(pl.lit(None, dtype=pl.Float64).alias("actual"))
    if "duration_hours" not in prepared.columns:
        prepared = prepared.with_columns(pl.lit(1.0).alias("duration_hours"))
    return (
        prepared.select("local_date", "local_hour", "forecast", "actual", "duration_hours")
        .with_columns(
            pl.col("local_date").cast(pl.Date),
            pl.col("local_hour").cast(pl.Int8),
            pl.col("forecast").cast(pl.Float64),
            pl.col("actual").cast(pl.Float64),
            pl.col("duration_hours").cast(pl.Float64),
        )
        .drop_nulls(["forecast", "duration_hours"])
        .filter(pl.col("duration_hours") > 0.0)
        .sort(["local_date", "local_hour"])
    )


def _complete_days(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    valid = (
        frame.group_by("local_date")
        .agg(
            pl.col("local_hour").n_unique().alias("_n"),
            pl.col("local_hour").min().alias("_min"),
            pl.col("local_hour").max().alias("_max"),
        )
        .filter((pl.col("_n") == 24) & (pl.col("_min") == 0) & (pl.col("_max") == 23))
        .select("local_date")
    )
    return frame.join(valid, on="local_date", how="inner").sort(["local_date", "local_hour"])


def _contiguous_blocks(frame: pl.DataFrame) -> list[pl.DataFrame]:
    complete = _complete_days(frame)
    if complete.is_empty():
        return []
    days = complete.get_column("local_date").unique().sort().to_list()
    blocks: list[pl.DataFrame] = []
    start = 0
    for index in range(1, len(days)):
        if days[index] - days[index - 1] != dt.timedelta(days=1):
            blocks.append(complete.filter(pl.col("local_date").is_in(days[start:index])))
            start = index
    blocks.append(complete.filter(pl.col("local_date").is_in(days[start:])))
    return blocks


def _first_action(
    soc: float,
    phase: int,
    prices: list[object],
    durations: list[object],
    spec: BatterySpec,
    *,
    terminal_soc_mwh: float | None,
) -> tuple[float, float, int]:
    grid = spec.soc_step_mwh
    states = round(spec.energy_mwh / grid)
    initial = round(soc / grid)
    values = [-math.inf] * (states + 1)
    phase_values = [values.copy(), values.copy()]
    if terminal_soc_mwh is None:
        phase_values = [[0.0] * (states + 1), [0.0] * (states + 1)]
    else:
        terminal = round(terminal_soc_mwh / grid)
        phase_values = [[-math.inf] * (states + 1), [-math.inf] * (states + 1)]
        phase_values[0][terminal] = 0.0
        phase_values[1][terminal] = 0.0
    policies: list[list[list[tuple[int, float, int] | None]]] = []

    for price, duration in reversed(list(zip(prices, durations, strict=True))):
        signal = _float_or_none(price)
        if signal is None:
            signal = 0.0
        hours = float(cast(float, duration))
        next_values = [[-math.inf] * (states + 1), [-math.inf] * (states + 1)]
        policy: list[list[tuple[int, float, int] | None]] = [
            [None] * (states + 1),
            [None] * (states + 1),
        ]
        for current_phase in (0, 1):
            for state in range(states + 1):
                current_soc = state * grid
                for next_state, grid_mwh in _actions(state, current_soc, hours, spec, states):
                    if current_phase == 1 and grid_mwh < 0.0:
                        continue
                    next_phase = 1 if grid_mwh > 0.0 else current_phase
                    action_cost = spec.variable_cost_eur_mwh + spec.degradation_cost_eur_mwh
                    value = grid_mwh * signal - abs(grid_mwh) * action_cost
                    value += phase_values[next_phase][next_state]
                    if value > next_values[current_phase][state]:
                        next_values[current_phase][state] = value
                        policy[current_phase][state] = (next_state, grid_mwh, next_phase)
        phase_values = next_values
        policies.append(policy)

    chosen = policies[-1][phase][initial] if policies else None
    if chosen is None:
        return soc, 0.0, phase
    next_state, action, next_phase = chosen
    return next_state * grid, action, next_phase


def _actions(
    state: int,
    soc: float,
    duration: float,
    spec: BatterySpec,
    states: int,
) -> list[tuple[int, float]]:
    grid = spec.soc_step_mwh
    eta_c = spec.charge_efficiency
    eta_d = spec.discharge_efficiency
    max_grid = spec.power_mw * duration
    max_charge_steps = math.floor(min(spec.energy_mwh - soc, max_grid * eta_c) / grid + 1e-9)
    max_discharge_steps = math.floor(min(soc, max_grid / eta_d) / grid + 1e-9)
    actions = [(state, 0.0)]
    for steps in range(1, max_charge_steps + 1):
        actions.append((state + steps, -(steps * grid) / eta_c))
    for steps in range(1, min(max_discharge_steps, state) + 1):
        actions.append((state - steps, steps * grid * eta_d))
    return [(next_state, action) for next_state, action in actions if 0 <= next_state <= states]


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    return float(cast(float, value))
