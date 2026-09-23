"""The modelling panel: the target, and only what was knowable before it.

Everything that decides whether this forecast is honest happens in this module.
A walk-forward loop that splits correctly still leaks if a feature carries
information from after the forecast was made, and that mistake is invisible in
the error metrics: it simply makes them good. So availability is enforced once,
here, and the backtest downstream never sees a raw observation.

**The target.** The day-ahead price for one market-local hour. The store holds
intervals, not hours, and Germany moved the day-ahead auction from hourly to
quarter-hourly products during the covered period, so an hour's price is the
duration-weighted mean of whatever intervals the provider published for it. It
is a mean and never a sum. Only complete hours enter the model; hourly-spaced
records carrying a fifteen-minute duration during a provider transition are
excluded rather than silently reinterpreted.

**The information set.** Day-ahead auctions clear the day before delivery. The
German auction closes at 12:00 CET on D-1 and settles all of day D at once, so
a forecaster standing at that gate knows:

- every day-ahead price through the end of D-1, because D-1 cleared at the
  previous day's auction and has been public for twenty-four hours;
- realised load and generation through the end of D-2. Actuals are published
  within hours, so part of D-1 is known too, but only the part before the gate.
  Rather than model a partial day, the whole of D-1 is withheld from the
  actuals-derived features. That is conservative, and conservative is the only
  safe direction here;
- the calendar, which is known indefinitely ahead.

What a real desk would also have and this project does not store is the
transmission operators' own day-ahead wind, solar and load *forecasts* for day
D. Those are the single strongest inputs to a day-ahead price model. Their
absence is the largest known weakness of the model built on this panel, and it
is a data gap rather than a modelling choice. Substituting the realised wind and
solar of day D would close it and would also be leakage, so it is not done.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

import polars as pl

from gpa.calendar import attach_local_time
from gpa.forecast.fundamentals import FUNDAMENTAL_FEATURES
from gpa.forecast.fundamentals import attach as attach_fundamentals
from gpa.zones import Zone

__all__ = [
    "ACTUALS_LAG_DAYS",
    "CALENDAR_FEATURES",
    "PRICE_LAG_DAYS",
    "RESIDUAL_LOAD_FUELS",
    "Panel",
    "build_panel",
    "hourly_mean",
    "hourly_residual_load",
    "load_panel",
]

PRICE_LAG_DAYS: Final[tuple[int, ...]] = (1, 2, 3, 7)
"""Same-hour price lags. All of these cleared before the gate closes on D-1."""

ACTUALS_LAG_DAYS: Final[tuple[int, ...]] = (2,)
"""Lag applied to realised load and generation, in days.

Two rather than one. Actual load and generation are published within hours, so
most of D-1 is in fact known at a midday gate, but not the evening, and a
feature that is available for some hours of the training set and not others is
worse than one that is uniformly late.
"""

RESIDUAL_LOAD_FUELS: Final[tuple[str, ...]] = ("wind", "solar")
"""The only generation fuels the panel ever reads.

Named here, once, so a snapshot can archive just these rows rather than every
fuel the store holds and still reproduce this panel exactly. Widening this
tuple is a modelling change; it must be paired with widening what a snapshot
keeps, and :func:`gpa.forecast.provenance.save_snapshot` checks that pairing
rather than assuming it.
"""

CALENDAR_FEATURES: Final[tuple[str, ...]] = (
    "dow_2",
    "dow_3",
    "dow_4",
    "dow_5",
    "dow_6",
    "dow_7",
    "year_sin",
    "year_cos",
)
"""Day-of-week dummies with Monday as the base level, plus annual seasonality.

The annual pair is a sine and cosine of position in the year rather than month
dummies: it is two coefficients instead of eleven, and it does not put a step
between the thirty-first of one month and the first of the next.
"""

_SIMILAR_DAY_USES_YESTERDAY: Final[tuple[int, ...]] = (2, 3, 4, 5)
"""ISO weekdays whose best naive predictor is the previous day: Tuesday to Friday.

Monday, Saturday and Sunday take the same weekday one week earlier instead. This
is the standard seasonal naive of the electricity price forecasting literature,
and it exists because the three days at the weekend boundary have demand shapes
that the adjacent day does not share.
"""

_INTERVAL_HOURS = pl.col("resolution_min").cast(pl.Float64) / 60.0


@dataclass(frozen=True, slots=True)
class Panel:
    """A modelling frame plus the names of the columns that may be used as inputs.

    Attributes:
        zone: The market the panel describes.
        frame: One row per market-local hour that has a target price, sorted by
            date then hour. Feature columns may be null where the history needed
            to form them is absent; rows are dropped at fit time rather than
            here, so that the panel keeps the full target series.
        features: Columns the regression may read. Every one of them is derived
            only from information available at the forecast gate.
        target: Column holding the realised price.
        similar_day: Column holding the seasonal naive forecast.
    """

    zone: Zone
    frame: pl.DataFrame
    features: tuple[str, ...]
    target: str = "price"
    similar_day: str = "price_similar_day"

    @property
    def dates(self) -> list[dt.date]:
        """Distinct local dates present, ascending."""
        return self.frame.get_column("local_date").unique().sort().to_list()

    def complete(self) -> pl.DataFrame:
        """Rows whose target and every feature are present."""
        return self.frame.drop_nulls(subset=[self.target, *self.features])


def hourly_mean(frame: pl.DataFrame, zone: Zone, column: str) -> pl.DataFrame:
    """Reduce an interval series to one duration-weighted value per local hour.

    Weighting by ``resolution_min`` rather than counting rows is what makes a
    mixed-resolution series safe to aggregate, and this project has one: the
    German day-ahead price is hourly for the first year of the window and
    quarter-hourly for the second.

    On the autumn clock change two UTC hours share a local hour, and they are
    combined into a single weighted mean whose ``covered_hours`` reports two.
    This defines a clock-hour benchmark, not a separate forecast of the two
    traded delivery intervals. Both occurrences must be present. The spring
    change simply has no row for the hour that did not exist.

    Args:
        frame: Rows carrying ``ts_utc``, ``resolution_min`` and ``column``.
        zone: Supplies the market timezone.
        column: Value column to average.

    Returns:
        Columns ``local_date``, ``local_hour``, ``ts_utc`` (the first instant in
        the hour), ``column`` and ``covered_hours``.
    """
    schema = pl.Schema(
        {
            "local_date": pl.Date(),
            "local_hour": pl.Int8(),
            "ts_utc": pl.Datetime(time_unit="us", time_zone="UTC"),
            column: pl.Float64(),
            "covered_hours": pl.Float64(),
        }
    )
    if frame.is_empty():
        return pl.DataFrame(schema=schema)

    # Require a complete, contiguous UTC hour before combining clock hours.
    # A lone quarter-hour is not an observed hourly mean. Do not infer missing
    # duration from the next timestamp: a provider gap would then become data.
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
            ((pl.col(column) * _INTERVAL_HOURS).sum() / _INTERVAL_HOURS.sum()).alias(column),
            _INTERVAL_HOURS.sum().alias("covered_hours"),
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
        .group_by(["local_date", "local_hour"])
        .agg(
            pl.col("ts_utc").min().alias("ts_utc"),
            (
                (pl.col(column) * pl.col("covered_hours")).sum() / pl.col("covered_hours").sum()
            ).alias(column),
            pl.col("covered_hours").sum(),
        )
        .filter(pl.col("covered_hours") == expected)
        .select("local_date", "local_hour", "ts_utc", column, "covered_hours")
        .sort(["local_date", "local_hour"])
    )


def hourly_residual_load(load: pl.DataFrame, generation: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Residual load per local hour: demand that wind and solar did not serve.

    This is the series that actually sets price in a market with a large
    variable fleet. Demand alone does not: a high-demand hour with a gale behind
    it clears cheaply, and the thermal stack is dispatched against what is left
    over rather than against the total.

    Returns:
        Columns ``local_date``, ``local_hour`` and ``residual_mw``. Empty if
        either input is empty, or if the two never share an hour.
    """
    schema = {
        "local_date": pl.Date,
        "local_hour": pl.Int8,
        "residual_mw": pl.Float64,
        "covered_hours": pl.Float64,
    }
    if load.is_empty() or generation.is_empty():
        return pl.DataFrame(schema=schema)

    variable = generation.filter(pl.col("fuel").is_in(RESIDUAL_LOAD_FUELS))
    if variable.is_empty():
        return pl.DataFrame(schema=schema)

    # Fuels are separate rows on the same interval, so they are summed before
    # the hourly weighting; averaging first would divide by the interval count
    # of every fuel rather than of the hour.
    combined = (
        variable.group_by("ts_utc")
        .agg(
            pl.col("gen_mw").sum().alias("gen_mw"),
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

    hourly_load = hourly_mean(load, zone, "load_mw")
    hourly_variable = hourly_mean(combined, zone, "gen_mw")

    return (
        hourly_load.join(
            hourly_variable, on=["local_date", "local_hour", "ts_utc", "covered_hours"], how="inner"
        )
        .with_columns((pl.col("load_mw") - pl.col("gen_mw")).alias("residual_mw"))
        .select("local_date", "local_hour", "residual_mw", "covered_hours")
        .sort(["local_date", "local_hour"])
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
    """Assemble the target and its lawful features from canonical frames.

    Args:
        prices: Rows matching the ``price`` schema for a single zone.
        zone: Supplies the market timezone and calendar.
        load: Rows matching the ``load`` schema, for the residual-load features.
        generation: Rows matching the ``generation`` schema, likewise. Residual
            features are produced only when both are supplied and overlap.
        fundamentals: Optional wide snapshots of day-ahead load, wind and solar
            forecasts. Each row must carry ``published_at`` as well as ``ts_utc``;
            only the latest snapshot published before the market gate is used.

    Returns:
        A :class:`Panel`. The residual-load features are absent from
        ``features`` when the zone cannot support them, so a price-only market
        such as Tokyo still yields a usable panel rather than an error.

    Raises:
        ValueError: If ``prices`` carries more than one zone.
    """
    for frame in (prices, load, generation, fundamentals):
        if frame is not None and not frame.is_empty():
            codes = frame.get_column("zone").unique().to_list()
            if codes != [zone.code]:
                raise ValueError(f"build_panel expects zone {zone.code}, got {sorted(codes)}")

    hourly = hourly_mean(prices, zone, "price")
    if hourly.is_empty():
        return Panel(zone=zone, frame=_empty_panel(), features=())

    panel = hourly.rename({"covered_hours": "target_covered_hours"})
    if delivery_date is not None:
        # These are modelling keys, not fabricated provider prices. Target
        # values remain null and all feature joins still read observed history.
        timezone = ZoneInfo(zone.timezone)
        start = dt.datetime.combine(delivery_date, dt.time(), timezone).astimezone(dt.UTC)
        stop = dt.datetime.combine(
            delivery_date + dt.timedelta(days=1), dt.time(), timezone
        ).astimezone(dt.UTC)
        grid = pl.DataFrame(
            {"ts_utc": pl.datetime_range(start, stop, interval="1h", closed="left", eager=True)}
        )
        grid = (
            attach_local_time(grid, zone)
            .group_by("local_date", "local_hour")
            .agg(pl.col("ts_utc").min(), pl.len().cast(pl.Float64).alias("target_covered_hours"))
            .with_columns(pl.lit(None, dtype=pl.Float64).alias("price"))
        )
        panel = pl.concat(
            [panel.filter(pl.col("local_date") != delivery_date), grid.select(panel.columns)],
            how="vertical_relaxed",
        )
    features: list[str] = []

    for days in PRICE_LAG_DAYS:
        panel = panel.join(
            _shift_days(hourly.select("local_date", "local_hour", "price"), days).rename(
                {"price": f"price_d{days}"}
            ),
            on=["local_date", "local_hour"],
            how="left",
        )
        features.append(f"price_d{days}")

    daily = (
        hourly.group_by("local_date")
        .agg(
            pl.col("covered_hours").sum().alias("_hours"),
            (
                (pl.col("price") * pl.col("covered_hours")).sum() / pl.col("covered_hours").sum()
            ).alias("_mean"),
            pl.col("price").min().alias("_min"),
            pl.col("price").max().alias("_max"),
            pl.col("price").sort_by("local_hour").last().alias("_end"),
        )
        .filter(pl.col("_hours") == _day_hours(zone))
        .drop("_hours")
    )

    for days, wanted in ((1, ("_mean", "_min", "_max", "_end")), (7, ("_mean",))):
        renamed = daily.select(
            (pl.col("local_date") + pl.duration(days=days)).alias("local_date"),
            *[pl.col(name).alias(f"price_d{days}{name}") for name in wanted],
        )
        panel = panel.join(renamed, on="local_date", how="left")
        features.extend(f"price_d{days}{name}" for name in wanted)

    if load is not None and generation is not None:
        residual = hourly_residual_load(load, generation, zone)
        if not residual.is_empty():
            panel, added = _attach_residual_features(panel, residual, zone)
            features.extend(added)

    if fundamentals is not None:
        panel = attach_fundamentals(panel, fundamentals, zone)
        features.extend(FUNDAMENTAL_FEATURES)

    panel = _attach_calendar(panel)
    features.extend(CALENDAR_FEATURES)

    panel = panel.with_columns(
        pl.when(pl.col("dow").is_in(list(_SIMILAR_DAY_USES_YESTERDAY)))
        .then(pl.col("price_d1"))
        .otherwise(pl.col("price_d7"))
        .alias("price_similar_day")
    )

    return Panel(
        zone=zone,
        frame=panel.sort(["local_date", "local_hour"]),
        features=tuple(features),
    )


def load_panel(
    zone: Zone, *, include_actuals: bool = True, include_fundamentals: bool = False
) -> Panel:
    """Build the panel from the curated store.

    ``include_actuals=False`` is useful for long price-only stress tests when
    older load/generation vintages are not available. It deliberately removes
    residual-load features rather than forward-filling them across the gap.

    ``include_fundamentals=True`` adds the day-ahead load/wind/solar forecast
    features from :func:`gpa.forecast.fundamentals.from_store`, for zones that
    collect them. Off by default: this is an ablation input, not part of the
    frozen model's published feature set until a reviewed decision adopts it.
    """
    from gpa import store

    prices = store.read("price", zone.code)
    load = store.read("load", zone.code) if include_actuals and zone.has("load") else None
    generation = (
        store.read("generation", zone.code) if include_actuals and zone.has("generation") else None
    )
    fundamentals = None
    if include_fundamentals and zone.has("fundamentals"):
        from gpa.forecast import fundamentals as fundamentals_module

        fundamentals = fundamentals_module.from_store(zone)
    return build_panel(prices, zone, load=load, generation=generation, fundamentals=fundamentals)


# --- Internals -------------------------------------------------------------


def _shift_days(frame: pl.DataFrame, days: int) -> pl.DataFrame:
    """Re-date ``frame`` forward, so joining on the new date reads ``days`` back.

    The shift is on the market-local calendar date, not on the instant, which is
    the whole point: "the same hour yesterday" is a clock statement, and across
    a daylight saving boundary it is not twenty-four hours ago.
    """
    return frame.with_columns((pl.col("local_date") + pl.duration(days=days)).alias("local_date"))


def _attach_residual_features(
    panel: pl.DataFrame, residual: pl.DataFrame, zone: Zone
) -> tuple[pl.DataFrame, list[str]]:
    added: list[str] = []
    daily = (
        residual.group_by("local_date")
        .agg(
            pl.col("covered_hours").sum().alias("_hours"),
            (
                (pl.col("residual_mw") * pl.col("covered_hours")).sum()
                / pl.col("covered_hours").sum()
            ).alias("_mean"),
        )
        .filter(pl.col("_hours") == _day_hours(zone))
        .drop("_hours")
    )

    for days in ACTUALS_LAG_DAYS:
        hourly_name = f"residual_d{days}"
        panel = panel.join(
            _shift_days(residual.drop("covered_hours"), days).rename({"residual_mw": hourly_name}),
            on=["local_date", "local_hour"],
            how="left",
        )
        daily_name = f"residual_d{days}_mean"
        panel = panel.join(
            _shift_days(daily, days).rename({"_mean": daily_name}),
            on="local_date",
            how="left",
        )
        added.extend((hourly_name, daily_name))

    return panel, added


def _day_hours(zone: Zone) -> pl.Expr:
    midnight = pl.col("local_date").cast(pl.Datetime("us"))
    start = midnight.dt.replace_time_zone(zone.timezone)
    end = (midnight + pl.duration(days=1)).dt.replace_time_zone(zone.timezone)
    return (end - start).dt.total_hours()


def _attach_calendar(panel: pl.DataFrame) -> pl.DataFrame:
    """Day-of-week dummies and an annual sine pair, from the local date alone."""
    angle = (pl.col("local_date").dt.ordinal_day() - 1).cast(pl.Float64) * (2.0 * math.pi / 365.25)
    return panel.with_columns(
        pl.col("local_date").dt.weekday().cast(pl.Int8).alias("dow"),
    ).with_columns(
        *[(pl.col("dow") == day).cast(pl.Float64).alias(f"dow_{day}") for day in range(2, 8)],
        angle.sin().alias("year_sin"),
        angle.cos().alias("year_cos"),
    )


def _empty_panel() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "local_date": pl.Date,
            "local_hour": pl.Int8,
            "ts_utc": pl.Datetime(time_unit="us", time_zone="UTC"),
            "price": pl.Float64,
            "target_covered_hours": pl.Float64,
        }
    )
