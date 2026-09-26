"""Day-ahead operator forecasts of load, wind and solar, attached only where knowable.

The provider exposes no publication vintage, so one is assigned. The retrospective
ablation treats every row as published at its own D-1 noon gate. A live issue keeps
that policy for training rows and stamps its delivery day with the instant it read
the archive, so the arm abstains, on the record, when that was after the gate.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

import polars as pl

from gpa.calendar import attach_local_time
from gpa.zones import Zone

__all__ = [
    "FUNDAMENTAL_COLUMNS",
    "FUNDAMENTAL_FEATURES",
    "attach",
    "empty",
    "from_store",
    "from_store_prospective",
    "normalize",
]

FUNDAMENTAL_FEATURES: Final = (
    "da_load_forecast",
    "da_wind_forecast",
    "da_solar_forecast",
    "da_residual_load_forecast",
)
FUNDAMENTAL_COLUMNS: Final = (*FUNDAMENTAL_FEATURES, "da_forecast_age_hours")
"""The age is evidence, never fitted: it is constant wherever the vintage is the gate."""

_FORECASTS = ("load_forecast_mw", "wind_forecast_mw", "solar_forecast_mw")
_SCHEMA = pl.Schema(
    {
        "zone": pl.String(),
        "ts_utc": pl.Datetime("us", "UTC"),
        "published_at": pl.Datetime("us", "UTC"),
        **dict.fromkeys(_FORECASTS, pl.Float64()),
    }
)


def _gate(zone: Zone) -> pl.Expr:
    """Noon market time on the day before ``local_date``, in UTC."""
    noon = (pl.col("local_date") - pl.duration(days=1)).cast(pl.Datetime("us")) + pl.duration(
        hours=12
    )
    return noon.dt.replace_time_zone(zone.timezone).dt.convert_time_zone("UTC")


def empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMA)


def normalize(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Validate snapshots: ``ts_utc`` is the delivery start, ``published_at`` when it was public."""
    if missing := set(_SCHEMA) - set(frame.columns):
        raise ValueError(f"fundamentals are missing columns: {sorted(missing)}")
    out = frame.select(sorted(_SCHEMA)).with_columns(
        pl.col("zone").cast(pl.String),
        pl.col("ts_utc", "published_at").cast(pl.Datetime("us", "UTC")),
        pl.col(_FORECASTS).cast(pl.Float64, strict=False),
    )
    zones = out["zone"].unique().to_list()
    if zones and zones != [zone.code]:
        raise ValueError(f"fundamentals expect zone {zone.code}, got {sorted(zones)}")
    return out


def attach(panel: pl.DataFrame, fundamentals: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Attach, per panel hour, the latest snapshot published by that row's own gate."""
    if fundamentals.is_empty():
        return panel.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(name) for name in FUNDAMENTAL_COLUMNS]
        )
    keys = (
        panel.select("local_date", "local_hour").unique().with_columns(_gate(zone).alias("_gate"))
    )
    latest = (
        attach_local_time(normalize(fundamentals, zone), zone)
        .join(keys, on=["local_date", "local_hour"])
        .filter(pl.col("published_at") <= pl.col("_gate"))
        # The repeated autumn clock hour holds two UTC hours: average them, as the target does.
        .group_by("local_date", "local_hour", "_gate", "published_at")
        .agg(pl.col(_FORECASTS).mean())
        .group_by("local_date", "local_hour")
        .agg(pl.all().sort_by("published_at").last())
    )
    load, wind, solar = (pl.col(name) for name in _FORECASTS)
    return panel.join(
        latest.select(
            "local_date",
            "local_hour",
            load.alias("da_load_forecast"),
            wind.alias("da_wind_forecast"),
            solar.alias("da_solar_forecast"),
            (load - wind - solar).alias("da_residual_load_forecast"),
            (pl.col("_gate") - pl.col("published_at"))
            .dt.total_hours()
            .alias("da_forecast_age_hours"),
        ),
        on=["local_date", "local_hour"],
        how="left",
    )


def _hourly_wide(zone: Zone) -> pl.DataFrame | None:
    """Whole clock hours of the archive, one column per series; ``None`` when empty."""
    from gpa import store

    raw = store.read("fundamentals", zone.code)
    if raw.is_empty():
        return None
    hourly = (
        raw.group_by("zone", pl.col("ts_utc").dt.truncate("1h"), "series")
        .agg(
            (
                (pl.col("forecast_mw") * pl.col("resolution_min")).sum()
                / pl.col("resolution_min").sum()
            ),
            pl.col("resolution_min").sum().alias("_minutes"),
        )
        .filter(pl.col("_minutes") == 60)
        .pivot(on="series", index=["zone", "ts_utc"], values="forecast_mw")
    )
    names = {series: f"{series}_forecast_mw" for series in ("load", "wind", "solar")}
    hourly = hourly.with_columns(
        [pl.lit(None, dtype=pl.Float64).alias(s) for s in names if s not in hourly.columns]
    )
    return attach_local_time(hourly.rename(names), zone)


def from_store(zone: Zone) -> pl.DataFrame:
    """The retrospective path: every row published at its own delivery day's gate."""
    local = _hourly_wide(zone)
    if local is None:
        return empty()
    return local.with_columns(_gate(zone).alias("published_at")).select(_SCHEMA.names())


def from_store_prospective(
    zone: Zone, *, delivery_date: dt.date, retrieved_at: dt.datetime
) -> pl.DataFrame:
    """Training rows keep the policy vintage; the delivery day carries ``retrieved_at``.

    One present-day stamp on the whole archive would erase every historical row at its
    own gate and leave nothing to fit; a retrieval after the gate is never backdated.
    """
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    local = _hourly_wide(zone)
    if local is None:
        return empty()
    stamp = pl.lit(retrieved_at.astimezone(dt.UTC)).cast(pl.Datetime("us", "UTC"))
    vintage = pl.when(pl.col("local_date") == delivery_date).then(stamp).otherwise(_gate(zone))
    return local.with_columns(vintage.alias("published_at")).select(_SCHEMA.names())
