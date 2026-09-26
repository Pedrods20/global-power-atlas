"""The modelling panel: the target, and only what was knowable at the D-1 noon gate.

The target is one market-local hour's day-ahead price, the duration-weighted mean
of the intervals published for it; only complete hours enter. At the gate a bidder
knows every price through D-1 and, conservatively, realised load and generation
through D-2. Leakage is prevented here, once, so no model downstream sees a raw
observation.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Final

import polars as pl

from gpa.calendar import INTERVAL_HOURS, attach_local_time, delivery_hours
from gpa.forecast.fundamentals import FUNDAMENTAL_FEATURES
from gpa.forecast.fundamentals import attach as attach_fundamentals
from gpa.zones import Zone

__all__ = [
    "RESIDUAL_LOAD_FUELS",
    "Panel",
    "build_panel",
    "hourly_mean",
    "hourly_residual_load",
    "load_panel",
]

PRICE_LAG_DAYS: Final = (1, 2, 3, 7)
"""Same-hour price lags; all cleared before the gate."""

ACTUALS_LAG_DAYS: Final = (2,)
"""Realised load and generation are uniformly two days late: D-1's evening is unknown at noon."""

RESIDUAL_LOAD_FUELS: Final = ("wind", "solar")
"""The only generation fuels the panel reads, and so the only ones an issue snapshot archives."""

CALENDAR_FEATURES: Final = (
    "dow_2",
    "dow_3",
    "dow_4",
    "dow_5",
    "dow_6",
    "dow_7",
    "year_sin",
    "year_cos",
)
"""Weekday dummies (Monday base) and an annual sine pair: two terms, no month-edge steps."""

_SIMILAR_DAY_USES_YESTERDAY: Final = (2, 3, 4, 5)
"""Tuesday to Friday repeat yesterday; Monday and the weekend repeat last week."""


@dataclass(frozen=True, slots=True)
class Panel:
    """One row per market-local hour with a target; features may be null, rows drop at fit."""

    zone: Zone
    frame: pl.DataFrame
    features: tuple[str, ...]
    target: str = "price"
    similar_day: str = "price_similar_day"

    def complete(self) -> pl.DataFrame:
        """Rows whose target and every feature are present."""
        return self.frame.drop_nulls(subset=[self.target, *self.features])


def _weighted(value: str, weight: pl.Expr) -> pl.Expr:
    return (pl.col(value) * weight).sum() / weight.sum()


def hourly_mean(frame: pl.DataFrame, zone: Zone, column: str) -> pl.DataFrame:
    """One duration-weighted value per local clock hour, from complete contiguous UTC hours.

    The repeated autumn hour averages its two UTC hours (``covered_hours`` 2), so this
    is a clock-hour benchmark, not two traded intervals. A provider gap never becomes data.
    """
    schema = pl.Schema(
        {
            "local_date": pl.Date(),
            "local_hour": pl.Int8(),
            "ts_utc": pl.Datetime("us", "UTC"),
            column: pl.Float64(),
            "covered_hours": pl.Float64(),
        }
    )
    if frame.is_empty():
        return pl.DataFrame(schema=schema)
    intervals = (
        frame.sort("ts_utc")
        .with_columns(
            pl.col("ts_utc").dt.truncate("1h").alias("_hour"),
            (pl.col("ts_utc") + pl.duration(minutes=pl.col("resolution_min"))).alias("_end"),
        )
        .with_columns(pl.col("_end").shift(1).over("_hour").alias("_previous_end"))
    )
    complete = (
        intervals.group_by("_hour")
        .agg(
            pl.col("ts_utc").min().alias("_first"),
            pl.col("_end").max().alias("_last"),
            _weighted(column, INTERVAL_HOURS).alias(column),
            INTERVAL_HOURS.sum().alias("covered_hours"),
            (pl.col("_previous_end").is_null() | (pl.col("ts_utc") == pl.col("_previous_end")))
            .all()
            .alias("_contiguous"),
            pl.col(column).is_not_null().all().alias("_valid"),
        )
        .filter(
            pl.col("_contiguous")
            & pl.col("_valid")
            & (pl.col("_first") == pl.col("_hour"))
            & (pl.col("_last") == pl.col("_hour") + pl.duration(hours=1))
            & ((pl.col("covered_hours") - 1.0).abs() < 1e-9)
        )
        .select(pl.col("_hour").alias("ts_utc"), column, "covered_hours")
    )
    clock = pl.col("local_date").cast(pl.Datetime("us")) + pl.duration(hours=pl.col("local_hour"))
    expected = (
        clock.dt.replace_time_zone(zone.timezone, ambiguous="latest")
        - clock.dt.replace_time_zone(zone.timezone, ambiguous="earliest")
    ).dt.total_hours() + 1
    return (
        attach_local_time(complete, zone)
        .group_by("local_date", "local_hour")
        .agg(
            pl.col("ts_utc").min(),
            _weighted(column, pl.col("covered_hours")).alias(column),
            pl.col("covered_hours").sum(),
        )
        .filter(pl.col("covered_hours") == expected)
        .select(*schema)
        .sort("local_date", "local_hour")
    )


def hourly_residual_load(load: pl.DataFrame, generation: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Hourly load minus wind and solar: the demand the thermal stack is dispatched against."""
    schema = pl.Schema(
        {
            "local_date": pl.Date,
            "local_hour": pl.Int8,
            "residual_mw": pl.Float64,
            "covered_hours": pl.Float64,
        }
    )
    variable = generation.filter(pl.col("fuel").is_in(RESIDUAL_LOAD_FUELS))
    if load.is_empty() or variable.is_empty():
        return pl.DataFrame(schema=schema)
    # Sum the fuels per interval before weighting; both must be present, once each.
    combined = (
        variable.group_by("ts_utc")
        .agg(
            pl.col("gen_mw").sum(),
            pl.col("resolution_min").first(),
            pl.col("resolution_min").n_unique().alias("_resolutions"),
            pl.col("fuel").n_unique().alias("_fuels"),
            pl.len().alias("_rows"),
            pl.col("gen_mw").is_not_null().all().alias("_valid"),
        )
        .filter(
            (pl.col("_fuels") == 2)
            & (pl.col("_rows") == 2)
            & (pl.col("_resolutions") == 1)
            & pl.col("_valid")
        )
    )
    keys = ["local_date", "local_hour", "ts_utc", "covered_hours"]
    return (
        hourly_mean(load, zone, "load_mw")
        .join(hourly_mean(combined, zone, "gen_mw"), on=keys)
        .with_columns((pl.col("load_mw") - pl.col("gen_mw")).alias("residual_mw"))
        .select(*schema)
        .sort("local_date", "local_hour")
    )


def build_panel(
    prices: pl.DataFrame,
    zone: Zone,
    *,
    load: pl.DataFrame | None = None,
    generation: pl.DataFrame | None = None,
    fundamentals: pl.DataFrame | None = None,
    delivery_date: dt.date | None = None,
) -> Panel:
    """Assemble the target and its lawful features.

    ``delivery_date`` adds that day's hours as keys with a null target, for a live issue.
    Fundamentals are optional wide snapshots; only the latest published before each
    row's own gate is used.
    """
    for frame in (prices, load, generation, fundamentals):
        if frame is not None and not frame.is_empty():
            codes = frame["zone"].unique().to_list()
            if codes != [zone.code]:
                raise ValueError(f"build_panel expects zone {zone.code}, got {sorted(codes)}")

    hourly = hourly_mean(prices, zone, "price")
    if hourly.is_empty():
        return Panel(zone, hourly.rename({"covered_hours": "target_covered_hours"}), ())
    panel = hourly.rename({"covered_hours": "target_covered_hours"})
    if delivery_date is not None:
        # Keys for the day being issued, with a null target; features read only history.
        grid = delivery_hours(delivery_date, zone).select(
            "local_date",
            "local_hour",
            pl.col("delivery_start_utc").alias("ts_utc"),
            pl.lit(None, dtype=pl.Float64).alias("price"),
            pl.col("delivery_duration_hours").alias("target_covered_hours"),
        )
        panel = pl.concat(
            [panel.filter(pl.col("local_date") != delivery_date), grid], how="vertical_relaxed"
        )

    features: list[str] = []
    prices_by_hour = hourly.select("local_date", "local_hour", "price")
    for days in PRICE_LAG_DAYS:
        panel = panel.join(
            _shift_days(prices_by_hour, days).rename({"price": f"price_d{days}"}),
            on=["local_date", "local_hour"],
            how="left",
        )
        features.append(f"price_d{days}")

    daily = (
        hourly.group_by("local_date")
        .agg(
            pl.col("covered_hours").sum().alias("_hours"),
            _weighted("price", pl.col("covered_hours")).alias("_mean"),
            pl.col("price").min().alias("_min"),
            pl.col("price").max().alias("_max"),
            pl.col("price").sort_by("local_hour").last().alias("_end"),
        )
        .filter(pl.col("_hours") == _day_hours(zone))
    )
    for days, wanted in ((1, ("_mean", "_min", "_max", "_end")), (7, ("_mean",))):
        panel = panel.join(
            _shift_days(daily.select("local_date", *wanted), days).rename(
                {name: f"price_d{days}{name}" for name in wanted}
            ),
            on="local_date",
            how="left",
        )
        features.extend(f"price_d{days}{name}" for name in wanted)

    if load is not None and generation is not None:
        residual = hourly_residual_load(load, generation, zone)
        if not residual.is_empty():
            panel, added = _attach_residual_features(panel, residual, zone)
            features.extend(added)
    if fundamentals is not None:
        panel = attach_fundamentals(panel, fundamentals, zone)
        features.extend(FUNDAMENTAL_FEATURES)
    panel = _attach_calendar(panel).with_columns(
        pl.when(pl.col("dow").is_in(list(_SIMILAR_DAY_USES_YESTERDAY)))
        .then(pl.col("price_d1"))
        .otherwise(pl.col("price_d7"))
        .alias("price_similar_day")
    )
    features.extend(CALENDAR_FEATURES)
    return Panel(zone, panel.sort("local_date", "local_hour"), tuple(features))


def load_panel(zone: Zone, *, include_fundamentals: bool = False) -> Panel:
    """Build the panel from the store; fundamentals are the labelled ablation input."""
    from gpa import store
    from gpa.forecast import fundamentals

    return build_panel(
        store.read("price", zone.code),
        zone,
        load=store.read("load", zone.code),
        generation=store.read("generation", zone.code),
        fundamentals=(
            fundamentals.from_store(zone)
            if include_fundamentals and zone.has("fundamentals")
            else None
        ),
    )


def _shift_days(frame: pl.DataFrame, days: int) -> pl.DataFrame:
    """Re-date forward by calendar days, so "same hour yesterday" is a clock statement."""
    return frame.with_columns(pl.col("local_date") + pl.duration(days=days))


def _attach_residual_features(
    panel: pl.DataFrame, residual: pl.DataFrame, zone: Zone
) -> tuple[pl.DataFrame, list[str]]:
    daily = (
        residual.group_by("local_date")
        .agg(
            pl.col("covered_hours").sum().alias("_hours"),
            _weighted("residual_mw", pl.col("covered_hours")).alias("_mean"),
        )
        .filter(pl.col("_hours") == _day_hours(zone))
        .drop("_hours")
    )
    added: list[str] = []
    for days in ACTUALS_LAG_DAYS:
        hourly_name, daily_name = f"residual_d{days}", f"residual_d{days}_mean"
        panel = panel.join(
            _shift_days(residual.drop("covered_hours"), days).rename({"residual_mw": hourly_name}),
            on=["local_date", "local_hour"],
            how="left",
        ).join(_shift_days(daily, days).rename({"_mean": daily_name}), on="local_date", how="left")
        added.extend((hourly_name, daily_name))
    return panel, added


def _day_hours(zone: Zone) -> pl.Expr:
    midnight = pl.col("local_date").cast(pl.Datetime("us"))
    start = midnight.dt.replace_time_zone(zone.timezone)
    end = (midnight + pl.duration(days=1)).dt.replace_time_zone(zone.timezone)
    return (end - start).dt.total_hours()


def _attach_calendar(panel: pl.DataFrame) -> pl.DataFrame:
    angle = (pl.col("local_date").dt.ordinal_day() - 1).cast(pl.Float64) * (2.0 * math.pi / 365.25)
    return panel.with_columns(
        pl.col("local_date").dt.weekday().cast(pl.Int8).alias("dow")
    ).with_columns(
        *[(pl.col("dow") == day).cast(pl.Float64).alias(f"dow_{day}") for day in range(2, 8)],
        angle.sin().alias("year_sin"),
        angle.cos().alias("year_cos"),
    )
