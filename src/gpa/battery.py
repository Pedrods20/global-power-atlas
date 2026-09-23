"""Price-taking, full-day battery schedules settled on observed prices.

One charge-then-discharge episode is permitted per local day, with identical
initial and terminal SOC. Optimization uses only the supplied forecast, never
settlement prices. This is a discretized day-ahead experiment, not intraday
trading or a general multi-cycle optimizer.

UTC delivery timestamps and explicit interval durations support physical DST
and quarter-hour accounting. Legacy clock-hour forecasts are accepted only on
ordinary 24-hour days: averaged repeated hours cannot recover physical trades.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from typing import Final, cast
from zoneinfo import ZoneInfo

import polars as pl

__all__ = [
    "DEFAULT_MODELS",
    "BatteryBacktestResult",
    "BatterySpec",
    "backtest_predictions",
    "dispatch",
    "summarize",
]

DEFAULT_MODELS: Final = (
    "naive_previous_day",
    "naive_previous_week",
    "naive_similar_day",
    "ridge",
    "lightgbm",
)

_INPUT_SCHEMA: Final = pl.Schema(
    {
        "ts_utc": pl.Datetime("us", "UTC"),
        "local_date": pl.Date(),
        "local_hour": pl.Int8(),
        "forecast": pl.Float64(),
        "actual": pl.Float64(),
        "duration_hours": pl.Float64(),
    }
)
_OUTPUT_SCHEMA: Final = pl.Schema(_INPUT_SCHEMA)
_OUTPUT_SCHEMA.update(
    {
        "action_mwh": pl.Float64(),
        "charge_mwh": pl.Float64(),
        "discharge_mwh": pl.Float64(),
        "battery_throughput_mwh": pl.Float64(),
        "soc_mwh": pl.Float64(),
        "gross_revenue_eur": pl.Float64(),
        "operating_cost_eur": pl.Float64(),
        "degradation_cost_eur": pl.Float64(),
        "profit_eur": pl.Float64(),
        "strategy": pl.String(),
        "power_mw": pl.Float64(),
        "energy_mwh": pl.Float64(),
    }
)
_COVERAGE_SCHEMA: Final = pl.Schema(
    {
        "model": pl.String,
        "candidate_days": pl.UInt32,
        "complete_days": pl.UInt32,
        "common_days": pl.UInt32,
        "excluded_days": pl.UInt32,
    }
)


SpecKwargs = dict[str, float]
"""Overrides for :class:`BatterySpec` fields, by field name."""


@dataclass(frozen=True, slots=True)
class BatterySpec:
    """Physical and economic assumptions for one battery experiment.

    Both cost rates apply to absolute grid-side energy (charge plus discharge).
    Equivalent cycles use battery-side throughput / (2 * nameplate capacity).
    ``max_cycles_per_day`` bounds a single charge-then-discharge episode, and
    ``max_episodes_per_day`` bounds how many of those a day may contain, so the
    daily cycle ceiling is their product. The default pair is the one-cycle
    benchmark; two episodes is the pattern a German day-ahead battery actually
    runs once solar carves a midday trough between the two demand peaks.
    Cost rates are assumptions, not calibrated market or investment costs.
    SOC resolution can materially limit dispatch: quarter-hour studies need
    a finer grid than the default 0.25 MWh hourly benchmark (for example 0.05).
    """

    power_mw: float = 1.0
    energy_mwh: float = 4.0
    round_trip_efficiency: float = 0.90
    soc_step_mwh: float = 0.25
    variable_cost_eur_mwh: float = 0.0
    degradation_cost_eur_mwh: float = 0.0
    initial_soc_mwh: float = 0.0
    max_cycles_per_day: float = 1.0
    max_episodes_per_day: float = 1.0

    def __post_init__(self) -> None:
        if any(not math.isfinite(getattr(self, field.name)) for field in fields(self)):
            raise ValueError("battery assumptions must be finite")
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
        if int(self.max_episodes_per_day) != self.max_episodes_per_day:
            raise ValueError("max_episodes_per_day must be a whole number of episodes")
        if self.max_episodes_per_day < 1:
            raise ValueError("max_episodes_per_day must be at least one")
        if not 0.0 <= self.initial_soc_mwh <= self.energy_mwh:
            raise ValueError("initial_soc_mwh must be inside the battery capacity")
        for value, label in (
            (self.energy_mwh, "energy_mwh"),
            (self.initial_soc_mwh, "initial_soc_mwh"),
        ):
            steps = value / self.soc_step_mwh
            if not math.isclose(steps, round(steps), rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(f"{label} must lie on the SOC grid")

    @property
    def charge_efficiency(self) -> float:
        return math.sqrt(self.round_trip_efficiency)

    @property
    def discharge_efficiency(self) -> float:
        return math.sqrt(self.round_trip_efficiency)


@dataclass(frozen=True, slots=True)
class BatteryBacktestResult:
    """Common-sample economics and explicit day-level coverage counts."""

    dispatch: pl.DataFrame
    summary: pl.DataFrame
    coverage: pl.DataFrame


def dispatch(
    frame: pl.DataFrame,
    spec: BatterySpec,
    *,
    strategy: str,
    horizon_steps: int | None = None,
    timezone: str = "Europe/Berlin",
) -> pl.DataFrame:
    """Choose one full-day schedule, then settle it against actual prices.

    UTC timestamps identify delivery intervals; duration_hours defaults to 1.
    Without timestamps only ordinary, unique 24-clock-hour days are eligible.
    Missing forecasts or physical gaps exclude the whole day. Duplicate,
    overlapping, non-finite or mislabelled intervals raise instead of being
    silently repaired. Actuals may be null for an unsettled schedule.

    horizon_steps is retained as an optional compatibility guard: a provided
    value must cover every interval of each eligible day. A shorter rolling
    horizon is not a valid full-day auction schedule and is rejected.
    """
    if not strategy:
        raise ValueError("strategy must not be empty")
    if horizon_steps is not None and horizon_steps < 1:
        raise ValueError("horizon_steps must be positive")
    prepared = _complete_days(_prepare(frame, timezone), timezone)
    rows: list[dict[str, object]] = []
    for day in prepared.partition_by("local_date", maintain_order=True):
        if horizon_steps is not None and horizon_steps < day.height:
            raise ValueError("horizon_steps must cover the full delivery day")
        schedule = _schedule(day["forecast"].to_list(), day["duration_hours"].to_list(), spec)
        previous_soc = spec.initial_soc_mwh
        for current, (soc, grid_mwh) in zip(day.iter_rows(named=True), schedule, strict=True):
            actual = _float_or_none(current["actual"])
            charge, discharge = max(0.0, -grid_mwh), max(0.0, grid_mwh)
            operating = (charge + discharge) * spec.variable_cost_eur_mwh
            degradation = (charge + discharge) * spec.degradation_cost_eur_mwh
            gross = grid_mwh * actual if actual is not None else None
            rows.append(
                {
                    **current,
                    "action_mwh": grid_mwh,
                    "charge_mwh": charge,
                    "discharge_mwh": discharge,
                    "battery_throughput_mwh": abs(soc - previous_soc),
                    "soc_mwh": soc,
                    "gross_revenue_eur": gross,
                    "operating_cost_eur": operating,
                    "degradation_cost_eur": degradation,
                    "profit_eur": gross - operating - degradation if gross is not None else None,
                    "strategy": strategy,
                    "power_mw": spec.power_mw,
                    "energy_mwh": spec.energy_mwh,
                }
            )
            previous_soc = soc
    return pl.DataFrame(rows, schema=_OUTPUT_SCHEMA).sort("ts_utc")


def summarize(dispatch_frame: pl.DataFrame) -> pl.DataFrame:
    """Aggregate economics without disguising missing settlement as zero P&L."""
    keys = ["strategy", "power_mw", "energy_mwh"]
    schema = {
        "strategy": pl.String,
        "power_mw": pl.Float64,
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
        dispatch_frame.group_by(keys)
        .agg(
            pl.col("local_date").n_unique().cast(pl.UInt32).alias("days"),
            pl.col("profit_eur").count().cast(pl.UInt32).alias("intervals"),
            *[
                pl.when(pl.col(column).null_count() == 0)
                .then(pl.col(column).sum())
                .otherwise(None)
                .alias(column)
                for column in (
                    "profit_eur",
                    "gross_revenue_eur",
                    "operating_cost_eur",
                    "degradation_cost_eur",
                )
            ],
            pl.col("battery_throughput_mwh").sum().alias("throughput_mwh"),
        )
        .with_columns(
            (pl.col("throughput_mwh") / (2 * pl.col("energy_mwh"))).alias("equivalent_cycles")
        )
    )
    perfect = grouped.filter(pl.col("strategy") == "perfect_foresight").select(
        "power_mw", "energy_mwh", pl.col("profit_eur").alias("_perfect_profit")
    )
    return (
        grouped.join(perfect, on=["power_mw", "energy_mwh"], how="left")
        .with_columns(
            pl.when(pl.col("_perfect_profit") > 0)
            .then(pl.col("profit_eur") / pl.col("_perfect_profit"))
            .otherwise(None)
            .alias("capture_vs_perfect")
        )
        .select(list(schema))
        .sort(["energy_mwh", "power_mw", "strategy"])
    )


def backtest_predictions(
    predictions: pl.DataFrame,
    *,
    model_names: Iterable[str] = DEFAULT_MODELS,
    durations_mwh: Sequence[float] = (1.0, 2.0, 4.0),
    horizon_steps: int | None = None,
    spec_kwargs: SpecKwargs | None = None,
    timezone: str = "Europe/Berlin",
) -> BatteryBacktestResult:
    """Compare all requested models on identical complete, settled days.

    Missing requested models and conflicting settlement prices/durations raise.
    Coverage records candidate, eligible and common day counts for each model.
    Capacity values are MWh, not hours unless power is exactly 1 MW.
    """
    required = {"model", "local_date", "local_hour", "forecast", "actual"}
    if missing := required - set(predictions.columns):
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    names = tuple(model_names)
    if (
        not names
        or len(set(names)) != len(names)
        or any(not n or n in {"no_trade", "perfect_foresight"} for n in names)
    ):
        raise ValueError("model_names must be distinct nonempty forecast model names")
    if not durations_mwh or len(set(durations_mwh)) != len(durations_mwh):
        raise ValueError("durations_mwh must contain distinct positive capacities")
    specs = [
        BatterySpec(energy_mwh=float(capacity), **(spec_kwargs or {})) for capacity in durations_mwh
    ]
    if predictions.is_empty():
        empty = pl.DataFrame(schema=_OUTPUT_SCHEMA)
        return BatteryBacktestResult(empty, summarize(empty), pl.DataFrame(schema=_COVERAGE_SCHEMA))
    if missing_models := set(names) - set(predictions["model"].unique().to_list()):
        raise ValueError(f"missing requested models: {sorted(missing_models)}")

    prepared: dict[str, pl.DataFrame] = {}
    complete: dict[str, pl.DataFrame] = {}
    candidate_counts: dict[str, int] = {}
    for name in names:
        frame = predictions.filter(pl.col("model") == name)
        candidate_counts[name] = frame["local_date"].n_unique()
        prepared[name] = _prepare(frame, timezone)
        eligible = _complete_days(prepared[name], timezone)
        settled = eligible.group_by("local_date").agg(
            pl.col("actual").null_count().alias("_missing")
        )
        complete[name] = eligible.join(
            settled.filter(pl.col("_missing") == 0).select("local_date"), on="local_date"
        )

    observations = pl.concat(list(prepared.values()))
    conflicts = (
        observations.group_by("ts_utc")
        .agg(
            pl.col("actual").drop_nulls().n_unique().alias("_actuals"),
            pl.col("duration_hours").n_unique().alias("_durations"),
        )
        .filter((pl.col("_actuals") > 1) | (pl.col("_durations") > 1))
    )
    if not conflicts.is_empty():
        raise ValueError("inconsistent actual prices or interval durations between models")
    common = set(complete[names[0]]["local_date"].to_list())
    for name in names[1:]:
        common.intersection_update(complete[name]["local_date"].to_list())
    # Complete days can have different resolutions: compare physical keys too.
    for day in list(common):
        keys = [
            set(prepared[name].filter(pl.col("local_date") == day)["ts_utc"].to_list())
            for name in names
        ]
        if any(key != keys[0] for key in keys[1:]):
            common.remove(day)
    coverage = pl.DataFrame(
        [
            {
                "model": name,
                "candidate_days": candidate_counts[name],
                "complete_days": complete[name]["local_date"].n_unique(),
                "common_days": len(common),
                "excluded_days": candidate_counts[name] - len(common),
            }
            for name in names
        ],
        schema=_COVERAGE_SCHEMA,
    )
    sample = {
        name: complete[name].filter(pl.col("local_date").is_in(sorted(common))) for name in names
    }
    outputs: list[pl.DataFrame] = []
    for spec in specs:
        actuals = sample[names[0]]
        outputs.append(
            dispatch(
                actuals.with_columns(pl.col("actual").alias("forecast")),
                spec,
                strategy="perfect_foresight",
                horizon_steps=horizon_steps,
                timezone=timezone,
            )
        )
        outputs.append(
            dispatch(
                actuals.with_columns(pl.lit(0.0).alias("forecast")),
                spec,
                strategy="no_trade",
                horizon_steps=horizon_steps,
                timezone=timezone,
            )
        )
        for name in names:
            outputs.append(
                dispatch(
                    sample[name],
                    spec,
                    strategy=name,
                    horizon_steps=horizon_steps,
                    timezone=timezone,
                )
            )
    combined = pl.concat(outputs).sort(["energy_mwh", "power_mw", "ts_utc", "strategy"])
    return BatteryBacktestResult(combined, summarize(combined), coverage)


def _day_bounds(day: dt.date, timezone: str) -> tuple[dt.datetime, dt.datetime]:
    zone = ZoneInfo(timezone)
    return (
        dt.datetime.combine(day, dt.time(), zone).astimezone(dt.UTC),
        dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), zone).astimezone(dt.UTC),
    )


def _prepare(frame: pl.DataFrame, timezone: str) -> pl.DataFrame:
    if missing := {"local_date", "local_hour", "forecast"} - set(frame.columns):
        raise ValueError(f"dispatch frame missing columns: {sorted(missing)}")
    prepared = frame.with_columns(
        pl.col("local_date").cast(pl.Date),
        pl.col("forecast").cast(pl.Float64),
        pl.col("actual").cast(pl.Float64)
        if "actual" in frame.columns
        else pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.col("duration_hours").cast(pl.Float64)
        if "duration_hours" in frame.columns
        else pl.lit(1.0).alias("duration_hours"),
    )
    if prepared.select(
        pl.any_horizontal(pl.col("local_date").is_null(), pl.col("local_hour").is_null()).any()
    ).item():
        raise ValueError("local delivery labels must not be null")
    if prepared.filter(
        ~pl.col("local_hour").is_between(0, 23)
        | (pl.col("local_hour") != pl.col("local_hour").floor())
    ).height:
        raise ValueError("local_hour must be an integer in 0..23")
    prepared = prepared.with_columns(pl.col("local_hour").cast(pl.Int8))
    for column in ("forecast", "actual", "duration_hours"):
        if prepared.filter(pl.col(column).is_not_null() & ~pl.col(column).is_finite()).height:
            raise ValueError(f"{column} must be finite when present")
    if prepared.filter(pl.col("duration_hours").is_null() | (pl.col("duration_hours") <= 0)).height:
        raise ValueError("duration_hours must be finite and positive")
    if "ts_utc" not in prepared.columns:
        if prepared.select(pl.struct("local_date", "local_hour").is_duplicated().any()).item():
            raise ValueError("duplicate clock-hour rows require unique UTC delivery timestamps")
        valid_days = [
            day
            for day in prepared["local_date"].unique().to_list()
            if _day_bounds(day, timezone)[1] - _day_bounds(day, timezone)[0]
            == dt.timedelta(hours=24)
        ]
        prepared = prepared.filter(pl.col("local_date").is_in(valid_days))
        stamps = [
            dt.datetime.combine(day, dt.time(hour), ZoneInfo(timezone)).astimezone(dt.UTC)
            for day, hour in prepared.select("local_date", "local_hour").iter_rows()
        ]
        prepared = prepared.with_columns(
            pl.Series("ts_utc", stamps, dtype=pl.Datetime("us", "UTC"))
        )
    else:
        dtype = prepared.schema["ts_utc"]
        if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
            raise ValueError("ts_utc must contain timezone-aware delivery timestamps")
        prepared = prepared.with_columns(
            pl.col("ts_utc").dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC"))
        )
    if prepared["ts_utc"].null_count():
        raise ValueError("ts_utc must not be null")
    if prepared["ts_utc"].is_duplicated().any():
        raise ValueError("duplicate delivery timestamps")
    local = pl.col("ts_utc").dt.convert_time_zone(timezone)
    if prepared.filter(
        (local.dt.date() != pl.col("local_date")) | (local.dt.hour() != pl.col("local_hour"))
    ).height:
        raise ValueError("UTC timestamps do not match local delivery labels")
    prepared = prepared.select(list(_INPUT_SCHEMA)).sort("ts_utc")
    stamps = prepared["ts_utc"].to_list()
    ends = [
        stamp + dt.timedelta(hours=hours)
        for stamp, hours in zip(stamps, prepared["duration_hours"], strict=True)
    ]
    if any(end > stamp for end, stamp in zip(ends[:-1], stamps[1:], strict=True)):
        raise ValueError("overlapping delivery intervals")
    return prepared.cast(_INPUT_SCHEMA)


def _complete_days(frame: pl.DataFrame, timezone: str) -> pl.DataFrame:
    valid: list[dt.date] = []
    for day in frame.partition_by("local_date", maintain_order=True):
        date = day["local_date"][0]
        start, end = _day_bounds(date, timezone)
        stamps = day["ts_utc"].to_list()
        ends = [
            stamp + dt.timedelta(hours=hours)
            for stamp, hours in zip(stamps, day["duration_hours"], strict=True)
        ]
        if (
            stamps[0] == start
            and ends[-1] == end
            and not day["forecast"].null_count()
            and all(a == b for a, b in zip(ends[:-1], stamps[1:], strict=True))
        ):
            valid.append(date)
    return frame.filter(pl.col("local_date").is_in(valid)).sort("ts_utc")


def _schedule(
    prices: list[float], durations: list[float], spec: BatterySpec
) -> list[tuple[float, float]]:
    """Backward DP, then one forward execution of the preselected daily policy.

    The phase dimension is what makes an episode an episode. Phase ``2i`` means
    the schedule is inside episode ``i`` and may still charge; phase ``2i+1``
    means it has begun discharging that episode. Charging from an odd phase
    opens the next episode, and is refused once ``max_episodes_per_day`` are
    spent, so a day holds at most that many charge-then-discharge episodes and
    nothing is left to an implicit tie-break.

    Within one episode, terminal SOC equal to initial SOC makes throughput
    exactly twice (peak SOC - initial SOC), so bounding that peak enforces the
    per-episode cycle budget without a third DP state. That equivalence is
    per-episode, not per-day: with several episodes the daily ceiling is the
    product of the two bounds, which is why both are declared. A non-grid-aligned
    budget is rounded down, never exceeded.
    """
    grid = spec.soc_step_mwh
    initial = round(spec.initial_soc_mwh / grid)
    peak = min(
        round(spec.energy_mwh / grid),
        initial + math.floor(spec.max_cycles_per_day * spec.energy_mwh / grid + 1e-9),
    )
    phases = 2 * int(spec.max_episodes_per_day)
    values = [[-math.inf] * (peak + 1) for _ in range(phases)]
    for phase in range(phases):
        values[phase][initial] = 0.0
    policies: list[list[list[tuple[int, float, int] | None]]] = []
    for signal, duration in reversed(list(zip(prices, durations, strict=True))):
        next_values = [[-math.inf] * (peak + 1) for _ in range(phases)]
        policy: list[list[tuple[int, float, int] | None]] = [
            [None] * (peak + 1) for _ in range(phases)
        ]
        for phase in range(phases):
            discharging = phase % 2 == 1
            for state in range(initial, peak + 1):
                for next_state, action in _actions(state, duration, spec, initial, peak):
                    if action < 0 and discharging and phase + 1 >= phases:
                        continue
                    if action > 0:
                        next_phase = phase + 1 if not discharging else phase
                    elif action < 0 and discharging:
                        next_phase = phase + 1
                    else:
                        next_phase = phase
                    value = action * signal - abs(action) * (
                        spec.variable_cost_eur_mwh + spec.degradation_cost_eur_mwh
                    )
                    value += values[next_phase][next_state]
                    if value > next_values[phase][state]:
                        next_values[phase][state] = value
                        policy[phase][state] = (next_state, action, next_phase)
        values = next_values
        policies.append(policy)
    state, phase = initial, 0
    schedule: list[tuple[float, float]] = []
    for policy in reversed(policies):
        chosen = policy[phase][state]
        if chosen is None:
            raise RuntimeError("no feasible full-day battery schedule")
        state, action, phase = chosen
        schedule.append((state * grid, action))
    return schedule


def _actions(
    state: int, duration: float, spec: BatterySpec, minimum: int, maximum: int
) -> list[tuple[int, float]]:
    max_grid = spec.power_mw * duration
    grid = spec.soc_step_mwh
    charge = min(maximum - state, math.floor(max_grid * spec.charge_efficiency / grid + 1e-9))
    discharge = min(state - minimum, math.floor(max_grid / spec.discharge_efficiency / grid + 1e-9))
    return [
        (state, 0.0),
        *[(state + step, -step * grid / spec.charge_efficiency) for step in range(1, charge + 1)],
        *[
            (state - step, step * grid * spec.discharge_efficiency)
            for step in range(1, discharge + 1)
        ],
    ]


def _float_or_none(value: object) -> float | None:
    return None if value is None else float(cast(float, value))
