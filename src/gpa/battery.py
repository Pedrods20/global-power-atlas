"""Price-taking battery schedules: optimise on the forecast, settle on the actual price.

One full-day schedule per local day, ending where it started, with at most
``max_episodes_per_day`` charge-then-discharge episodes. UTC timestamps and interval
durations carry DST and quarter-hours; clock-hour input is accepted on 24-hour days only.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from typing import Final
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
_OUTPUT_SCHEMA: Final = pl.Schema(
    {
        **_INPUT_SCHEMA,
        **dict.fromkeys(
            (
                "action_mwh",
                "charge_mwh",
                "discharge_mwh",
                "battery_throughput_mwh",
                "soc_mwh",
                "gross_revenue_eur",
                "operating_cost_eur",
                "degradation_cost_eur",
                "profit_eur",
            ),
            pl.Float64(),
        ),
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
_MONEY = ("profit_eur", "gross_revenue_eur", "operating_cost_eur", "degradation_cost_eur")

SpecKwargs = dict[str, float]
"""Overrides for :class:`BatterySpec` fields, by name."""


@dataclass(frozen=True, slots=True)
class BatterySpec:
    """Physical and cost assumptions; costs apply to absolute grid energy, in and out.

    ``max_cycles_per_day`` bounds one episode and ``max_episodes_per_day`` how many a
    day holds. Quarter-hour studies need a finer ``soc_step_mwh`` than the hourly 0.25.
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
        if not 0.0 < self.soc_step_mwh <= self.energy_mwh:
            raise ValueError("soc_step_mwh must be positive and no larger than capacity")
        if self.variable_cost_eur_mwh < 0.0 or self.degradation_cost_eur_mwh < 0.0:
            raise ValueError("battery costs cannot be negative")
        if not 0.0 < self.max_cycles_per_day <= 1.0:
            raise ValueError("max_cycles_per_day must be in (0, 1]")
        if (
            self.max_episodes_per_day < 1
            or int(self.max_episodes_per_day) != self.max_episodes_per_day
        ):
            raise ValueError("max_episodes_per_day must be a whole number of at least one")
        if not 0.0 <= self.initial_soc_mwh <= self.energy_mwh:
            raise ValueError("initial_soc_mwh must be inside the battery capacity")
        for label in ("energy_mwh", "initial_soc_mwh"):
            steps = getattr(self, label) / self.soc_step_mwh
            if not math.isclose(steps, round(steps), rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(f"{label} must lie on the SOC grid")

    @property
    def leg_efficiency(self) -> float:
        """Charging and discharging each lose the square root of the round-trip loss."""
        return math.sqrt(self.round_trip_efficiency)


@dataclass(frozen=True, slots=True)
class BatteryBacktestResult:
    """Common-sample economics and explicit day-level coverage counts."""

    dispatch: pl.DataFrame
    summary: pl.DataFrame
    coverage: pl.DataFrame


def dispatch(
    frame: pl.DataFrame, spec: BatterySpec, *, strategy: str, timezone: str = "Europe/Berlin"
) -> pl.DataFrame:
    """Choose each day's schedule from ``forecast`` alone, then settle it on ``actual``.

    A day with a missing forecast or a physical gap is excluded whole; duplicate,
    overlapping or mislabelled intervals raise. ``actual`` may be null when unsettled.
    """
    if not strategy:
        raise ValueError("strategy must not be empty")
    rows = []
    for day in _complete_days(_prepare(frame, timezone), timezone).partition_by("local_date"):
        schedule = _schedule(day["forecast"].to_list(), day["duration_hours"].to_list(), spec)
        previous = spec.initial_soc_mwh
        for current, (soc, grid) in zip(day.iter_rows(named=True), schedule, strict=True):
            charge, discharge = max(0.0, -grid), max(0.0, grid)
            operating = (charge + discharge) * spec.variable_cost_eur_mwh
            degradation = (charge + discharge) * spec.degradation_cost_eur_mwh
            gross = None if current["actual"] is None else grid * float(current["actual"])
            rows.append(
                {
                    **current,
                    "action_mwh": grid,
                    "charge_mwh": charge,
                    "discharge_mwh": discharge,
                    "battery_throughput_mwh": abs(soc - previous),
                    "soc_mwh": soc,
                    "gross_revenue_eur": gross,
                    "operating_cost_eur": operating,
                    "degradation_cost_eur": degradation,
                    "profit_eur": None if gross is None else gross - operating - degradation,
                    "strategy": strategy,
                    "power_mw": spec.power_mw,
                    "energy_mwh": spec.energy_mwh,
                }
            )
            previous = soc
    return pl.DataFrame(rows, schema=_OUTPUT_SCHEMA).sort("ts_utc")


def summarize(dispatch_frame: pl.DataFrame) -> pl.DataFrame:
    """Economics per strategy and asset; missing settlement stays missing, never zero."""
    schema = {
        "strategy": pl.String,
        "power_mw": pl.Float64,
        "energy_mwh": pl.Float64,
        "days": pl.UInt32,
        "intervals": pl.UInt32,
        **dict.fromkeys(_MONEY, pl.Float64),
        "throughput_mwh": pl.Float64,
        "equivalent_cycles": pl.Float64,
        "capture_vs_perfect": pl.Float64,
    }
    if dispatch_frame.is_empty():
        return pl.DataFrame(schema=schema)
    grouped = (
        dispatch_frame.group_by("strategy", "power_mw", "energy_mwh")
        .agg(
            pl.col("local_date").n_unique().cast(pl.UInt32).alias("days"),
            pl.col("profit_eur").count().cast(pl.UInt32).alias("intervals"),
            *[
                pl.when(pl.col(column).null_count() == 0).then(pl.col(column).sum()).alias(column)
                for column in _MONEY
            ],
            pl.col("battery_throughput_mwh").sum().alias("throughput_mwh"),
        )
        .with_columns(
            (pl.col("throughput_mwh") / (2 * pl.col("energy_mwh"))).alias("equivalent_cycles")
        )
    )
    perfect = grouped.filter(pl.col("strategy") == "perfect_foresight").select(
        "power_mw", "energy_mwh", pl.col("profit_eur").alias("_perfect")
    )
    return (
        grouped.join(perfect, on=["power_mw", "energy_mwh"], how="left")
        .with_columns(
            pl.when(pl.col("_perfect") > 0)
            .then(pl.col("profit_eur") / pl.col("_perfect"))
            .alias("capture_vs_perfect")
        )
        .select(*schema)
        .sort("energy_mwh", "power_mw", "strategy")
    )


def backtest_predictions(
    predictions: pl.DataFrame,
    *,
    model_names: Iterable[str] = DEFAULT_MODELS,
    durations_mwh: Sequence[float] = (1.0, 2.0, 4.0),
    spec_kwargs: SpecKwargs | None = None,
    timezone: str = "Europe/Berlin",
) -> BatteryBacktestResult:
    """Dispatch every model, perfect foresight and no trade on identical settled days."""
    if missing := {"model", "local_date", "local_hour", "forecast", "actual"} - set(
        predictions.columns
    ):
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
    specs = [BatterySpec(energy_mwh=float(size), **(spec_kwargs or {})) for size in durations_mwh]
    if predictions.is_empty():
        empty = pl.DataFrame(schema=_OUTPUT_SCHEMA)
        return BatteryBacktestResult(empty, summarize(empty), pl.DataFrame(schema=_COVERAGE_SCHEMA))
    if missing_models := set(names) - set(predictions["model"].unique().to_list()):
        raise ValueError(f"missing requested models: {sorted(missing_models)}")

    prepared, complete, candidates = {}, {}, {}
    for name in names:
        frame = predictions.filter(pl.col("model") == name)
        candidates[name] = frame["local_date"].n_unique()
        prepared[name] = _prepare(frame, timezone)
        eligible = _complete_days(prepared[name], timezone)
        settled = eligible.group_by("local_date").agg(pl.col("actual").null_count().alias("_n"))
        complete[name] = eligible.join(
            settled.filter(pl.col("_n") == 0).select("local_date"), on="local_date"
        )

    conflicts = (
        pl.concat(list(prepared.values()))
        .group_by("ts_utc")
        .agg(
            pl.col("actual").drop_nulls().n_unique().alias("_actuals"),
            pl.col("duration_hours").n_unique().alias("_durations"),
        )
        .filter((pl.col("_actuals") > 1) | (pl.col("_durations") > 1))
    )
    if not conflicts.is_empty():
        raise ValueError("inconsistent actual prices or interval durations between models")
    # Complete days can differ in resolution between models: compare physical keys too.
    stamps = [
        dict(prepared[name].group_by("local_date").agg(pl.col("ts_utc").sort()).iter_rows())
        for name in names
    ]
    common = set.intersection(*(set(complete[name]["local_date"].to_list()) for name in names))
    common = {day for day in common if all(s[day] == stamps[0][day] for s in stamps[1:])}
    coverage = pl.DataFrame(
        [
            {
                "model": name,
                "candidate_days": candidates[name],
                "complete_days": complete[name]["local_date"].n_unique(),
                "common_days": len(common),
                "excluded_days": candidates[name] - len(common),
            }
            for name in names
        ],
        schema=_COVERAGE_SCHEMA,
    )
    sample = {
        name: complete[name].filter(pl.col("local_date").is_in(sorted(common))) for name in names
    }
    reference = sample[names[0]]
    strategies = {
        "perfect_foresight": reference.with_columns(pl.col("actual").alias("forecast")),
        "no_trade": reference.with_columns(pl.lit(0.0).alias("forecast")),
        **sample,
    }
    combined = pl.concat(
        dispatch(frame, spec, strategy=name, timezone=timezone)
        for spec in specs
        for name, frame in strategies.items()
    ).sort("energy_mwh", "power_mw", "ts_utc", "strategy")
    return BatteryBacktestResult(combined, summarize(combined), coverage)


def _day_bounds(day: dt.date, timezone: str) -> tuple[dt.datetime, dt.datetime]:
    zone = ZoneInfo(timezone)
    start, end = (
        dt.datetime.combine(d, dt.time(), zone).astimezone(dt.UTC)
        for d in (day, day + dt.timedelta(days=1))
    )
    return start, end


def _prepare(frame: pl.DataFrame, timezone: str) -> pl.DataFrame:
    """Validate delivery labels and intervals; derive UTC stamps for clock-hour input."""
    if missing := {"local_date", "local_hour", "forecast"} - set(frame.columns):
        raise ValueError(f"dispatch frame missing columns: {sorted(missing)}")
    optional = {"actual": pl.lit(None, dtype=pl.Float64), "duration_hours": pl.lit(1.0)}
    prepared = frame.with_columns(
        pl.col("local_date").cast(pl.Date),
        pl.col("forecast").cast(pl.Float64),
        *[
            (pl.col(name).cast(pl.Float64) if name in frame.columns else default).alias(name)
            for name, default in optional.items()
        ],
    )
    if prepared.select(
        pl.any_horizontal(pl.col("local_date", "local_hour").is_null()).any()
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
        # Averaged repeated hours cannot recover physical trades: 24-hour days only.
        bounds = {day: _day_bounds(day, timezone) for day in prepared["local_date"].unique()}
        days = [
            day for day, (start, end) in bounds.items() if end - start == dt.timedelta(hours=24)
        ]
        prepared = prepared.filter(pl.col("local_date").is_in(days))
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
        s + dt.timedelta(hours=h) for s, h in zip(stamps, prepared["duration_hours"], strict=True)
    ]
    if any(end > start for end, start in zip(ends[:-1], stamps[1:], strict=True)):
        raise ValueError("overlapping delivery intervals")
    return prepared.cast(_INPUT_SCHEMA)


def _complete_days(frame: pl.DataFrame, timezone: str) -> pl.DataFrame:
    """Days whose intervals tile the whole local day and all carry a forecast."""
    valid = []
    for day in frame.partition_by("local_date"):
        start, end = _day_bounds(day["local_date"][0], timezone)
        stamps = day["ts_utc"].to_list()
        ends = [
            s + dt.timedelta(hours=h) for s, h in zip(stamps, day["duration_hours"], strict=True)
        ]
        if (
            stamps[0] == start
            and ends[-1] == end
            and not day["forecast"].null_count()
            and ends[:-1] == stamps[1:]
        ):
            valid.append(day["local_date"][0])
    return frame.filter(pl.col("local_date").is_in(valid)).sort("ts_utc")


def _schedule(
    prices: list[float], durations: list[float], spec: BatterySpec
) -> list[tuple[float, float]]:
    """Backward dynamic programme over (phase, SOC), then one forward pass of its policy.

    Phase ``2i`` is inside episode ``i`` and may still charge; ``2i+1`` has begun
    discharging it. Charging from an odd phase opens the next episode, refused once
    the episodes are spent. Equal start and end SOC make an episode's throughput twice
    its peak rise, so capping the peak enforces the per-episode cycle budget.
    """
    grid = spec.soc_step_mwh
    initial = round(spec.initial_soc_mwh / grid)
    peak = min(
        round(spec.energy_mwh / grid),
        initial + math.floor(spec.max_cycles_per_day * spec.energy_mwh / grid + 1e-9),
    )
    phases = 2 * int(spec.max_episodes_per_day)
    cost = spec.variable_cost_eur_mwh + spec.degradation_cost_eur_mwh
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
                    # A phase ends when the trade changes direction: charge to discharge
                    # closes the charging leg, discharge to charge opens the next episode.
                    next_phase = phase + 1 if action and (action > 0) != discharging else phase
                    value = action * signal - abs(action) * cost + values[next_phase][next_state]
                    if value > next_values[phase][state]:
                        next_values[phase][state] = value
                        policy[phase][state] = (next_state, action, next_phase)
        values = next_values
        policies.append(policy)
    state, phase = initial, 0
    schedule = []
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
    """Reachable SOC steps and their grid energy: negative charges, positive discharges."""
    limit, grid, leg = spec.power_mw * duration, spec.soc_step_mwh, spec.leg_efficiency
    charge = min(maximum - state, math.floor(limit * leg / grid + 1e-9))
    discharge = min(state - minimum, math.floor(limit / leg / grid + 1e-9))
    return [
        (state, 0.0),
        *[(state + step, -step * grid / leg) for step in range(1, charge + 1)],
        *[(state - step, step * grid * leg) for step in range(1, discharge + 1)],
    ]
