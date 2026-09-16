"""Leakage-safe fundamental forecast inputs.

SMARD exposes German TSO forecasts for demand, wind and solar, while the
curated store currently contains only realised series.  This module defines a
small interchange format for those snapshots and selects the latest snapshot
that was published before the day-ahead gate.  Historical panels can therefore
use archived vintages when available, and a live issue can use the same code.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Final
from zoneinfo import ZoneInfo

import polars as pl

from gpa.calendar import attach_local_time
from gpa.zones import Zone

__all__ = [
    "FUNDAMENTAL_FEATURES",
    "SMARD_FORECAST_FILTERS",
    "attach",
    "combine_smard_series",
    "empty",
    "from_smard_series",
    "from_store",
    "from_store_observed",
    "normalize",
]

FUNDAMENTAL_FEATURES: Final[tuple[str, ...]] = (
    "da_load_forecast",
    "da_wind_forecast",
    "da_solar_forecast",
    "da_residual_load_forecast",
    "da_forecast_age_hours",
)
"""Names added to a modelling panel when fundamental snapshots are supplied."""

SMARD_FORECAST_FILTERS: Final[dict[str, int]] = {
    "wind_onshore": 123,
    "solar": 125,
    "wind_offshore": 3791,
}
"""Public SMARD chart filters for the three renewable forecast series."""

_REQUIRED = {
    "zone",
    "ts_utc",
    "published_at",
    "load_forecast_mw",
    "wind_forecast_mw",
    "solar_forecast_mw",
}
_FLOATS = ("load_forecast_mw", "wind_forecast_mw", "solar_forecast_mw")


def empty() -> pl.DataFrame:
    """Return an empty fundamental snapshot frame with its public contract."""
    return pl.DataFrame(
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime(time_unit="us", time_zone="UTC"),
            "published_at": pl.Datetime(time_unit="us", time_zone="UTC"),
            "load_forecast_mw": pl.Float64,
            "wind_forecast_mw": pl.Float64,
            "solar_forecast_mw": pl.Float64,
        }
    )


def normalize(frame: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Validate and normalise archived TSO forecast snapshots.

    ``ts_utc`` is the delivery interval start.  ``published_at`` is when the
    snapshot became available, not when it was downloaded.  Keeping those two
    instants separate is what makes an as-of join possible.
    """
    missing = _REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"fundamentals are missing columns: {sorted(missing)}")
    out = frame.select(sorted(_REQUIRED)).with_columns(
        pl.col("zone").cast(pl.String),
        pl.col("ts_utc").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        pl.col("published_at").cast(pl.Datetime(time_unit="us", time_zone="UTC")),
        *(pl.col(column).cast(pl.Float64, strict=False) for column in _FLOATS),
    )
    zones = out["zone"].unique().to_list()
    if zones and zones != [zone.code]:
        raise ValueError(f"fundamentals expect zone {zone.code}, got {sorted(zones)}")
    if out.select((pl.col("published_at") > pl.col("ts_utc")).any()).item():
        # A publication timestamp after delivery is allowed; it is simply not
        # eligible for the D-1 issue.  Do not reject it: the same frame may be
        # useful for auditing late revisions.
        pass
    return out


def attach(panel: pl.DataFrame, fundamentals: pl.DataFrame, zone: Zone) -> pl.DataFrame:
    """Attach the latest eligible fundamental snapshot to each panel row.

    The eligibility gate is noon on D-1 in the market timezone.  No future
    snapshot can enter a target row, even if the fundamentals file contains
    later revisions for the same delivery interval.
    """
    if fundamentals.is_empty():
        return panel.with_columns(
            *(pl.lit(None, dtype=pl.Float64).alias(name) for name in FUNDAMENTAL_FEATURES)
        )
    snapshots = normalize(fundamentals, zone)
    keys = (
        panel.select("local_date", "local_hour")
        .unique()
        .with_columns(
            pl.struct("local_date")
            .map_elements(
                lambda value: _gate(value["local_date"], zone),
                return_dtype=pl.Datetime("us", "UTC"),
            )
            .alias("_gate_utc")
        )
    )
    snapshots = (
        attach_local_time(snapshots, zone)
        .with_columns(
            (
                pl.col("load_forecast_mw")
                - pl.col("wind_forecast_mw")
                - pl.col("solar_forecast_mw")
            ).alias("da_residual_load_forecast")
        )
        .join(keys, on=["local_date", "local_hour"], how="inner")
        .filter(pl.col("published_at") <= pl.col("_gate_utc"))
        .sort("published_at")
        .unique(subset=["local_date", "local_hour"], keep="last")
        .with_columns(
            ((pl.col("_gate_utc") - pl.col("published_at")).dt.total_hours()).alias(
                "da_forecast_age_hours"
            )
        )
        .with_columns(
            pl.col("load_forecast_mw").alias("da_load_forecast"),
            pl.col("wind_forecast_mw").alias("da_wind_forecast"),
            pl.col("solar_forecast_mw").alias("da_solar_forecast"),
        )
        .select("local_date", "local_hour", *FUNDAMENTAL_FEATURES)
    )
    return panel.join(snapshots, on=["local_date", "local_hour"], how="left")


def _hourly_wide(zone: Zone) -> pl.DataFrame | None:
    """Whole clock hours from the curated archive, before any vintage is assigned.

    ``gpa.sources.energy_charts`` stores the honest day-ahead forecast values
    with no publication vintage, because the provider exposes none, at their
    native resolution, which has been quarter-hourly throughout the archive
    (unlike ``price``/``load``/``generation``, which changed resolution later).
    This averages sub-hourly rows into one duration-weighted value per clock
    hour, dropping a clock hour outright rather than averaging a partial one
    (:func:`attach` keys on whole clock hours and has no notion of a partial
    one).

    Returns ``None`` when the archive holds nothing for the zone, so each caller
    can return its own correctly typed empty frame.
    """
    from gpa import store

    raw = store.read("fundamentals", zone.code)
    if raw.is_empty():
        return None

    hourly = (
        raw.with_columns(pl.col("ts_utc").dt.truncate("1h").alias("_hour"))
        .group_by(["zone", "_hour", "series"])
        .agg(
            (
                (pl.col("forecast_mw") * pl.col("resolution_min")).sum()
                / pl.col("resolution_min").sum()
            ).alias("forecast_mw"),
            pl.col("resolution_min").sum().alias("_minutes"),
        )
        .filter(pl.col("_minutes") == 60)
        .drop("_minutes")
        .rename({"_hour": "ts_utc"})
    )

    wide = hourly.pivot(on="series", index=["zone", "ts_utc"], values="forecast_mw")
    for series in ("load", "wind", "solar"):
        if series not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(series))
    wide = wide.rename(
        {"load": "load_forecast_mw", "wind": "wind_forecast_mw", "solar": "solar_forecast_mw"}
    )
    return attach_local_time(wide, zone)


def from_store(zone: Zone) -> pl.DataFrame:
    """Build the wide snapshot :func:`attach` expects from the curated store.

    This is the *retrospective* path. It assigns ``published_at`` as this
    project's own research-policy assumption: each row is treated as available
    at the market gate for its own delivery day
    (:func:`gpa.forecast.ledger.market_gate`'s definition, reimplemented here to
    avoid a circular import against ``ledger`` -> ``panel`` ->
    ``fundamentals``), not an observed publication instant. That makes
    ``da_forecast_age_hours`` a constant zero for every historically backfilled
    row, which is why these features are published only as a labelled ablation
    and never folded into the frozen release.

    A live, prospective issue must not use this function. It records an observed
    retrieval instant instead; see :func:`from_store_observed`.
    """
    local = _hourly_wide(zone)
    if local is None:
        return empty()

    # One gate per distinct delivery day, not per row: a multi-year hourly
    # frame has far fewer unique dates than rows, and _gate() is Python-level.
    gates = (
        local.select("local_date")
        .unique()
        .with_columns(
            pl.struct("local_date")
            .map_elements(
                lambda value: _gate(value["local_date"], zone),
                return_dtype=pl.Datetime("us", "UTC"),
            )
            .alias("published_at")
        )
    )
    return local.join(gates, on="local_date").select(empty().schema.names())


def from_store_observed(zone: Zone, *, retrieved_at: dt.datetime) -> pl.DataFrame:
    """The same archive rows, stamped with an instant that was actually observed.

    This is the *prospective* counterpart to :func:`from_store`, and the
    difference between them is the whole reason these features are not in the
    published retrospective release. ``retrieved_at`` is when this run read the
    forecast, so it is a real, varying instant rather than a policy constant:
    :func:`attach` measures ``da_forecast_age_hours`` from it, and a snapshot
    read after the gate is correctly dropped instead of being assumed eligible.

    It is an upper bound on the provider's own publication time, since the value
    was published at or before the moment this project retrieved it. That
    direction is the safe one: a later stamp can only make a row less eligible,
    never more.

    Args:
        zone: Supplies the market timezone and the store partition.
        retrieved_at: Timezone-aware instant at which this run read the archive.

    Raises:
        ValueError: If ``retrieved_at`` is naive, which would leave the vintage
            unanchored and silently comparable against UTC gates.
    """
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")

    local = _hourly_wide(zone)
    if local is None:
        return empty()

    stamp = retrieved_at.astimezone(dt.UTC)
    return local.with_columns(
        pl.lit(stamp).cast(pl.Datetime(time_unit="us", time_zone="UTC")).alias("published_at")
    ).select(empty().schema.names())


def from_smard_series(
    payload: object,
    *,
    zone: Zone,
    series: str,
    published_at: dt.datetime,
) -> pl.DataFrame:
    """Parse one public SMARD forecast response into snapshot rows.

    The endpoint returns ``{"series": [[epoch_ms, value], ...]}``.  The caller
    supplies the publication time captured at issue, because SMARD's historical
    chart archive does not provide a reliable publication vintage for every
    old response.
    """
    if series not in SMARD_FORECAST_FILTERS and series != "load":
        raise ValueError(f"unknown SMARD forecast series {series!r}")
    if published_at.tzinfo is None:
        raise ValueError("published_at must be timezone-aware")
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        raise ValueError("SMARD forecast response must contain a series list")
    rows: list[dict[str, object]] = []
    for item in payload["series"]:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        timestamp, value = item
        if timestamp is None or value is None:
            continue
        rows.append(
            {
                "zone": zone.code,
                "ts_utc": dt.datetime.fromtimestamp(float(timestamp) / 1000, dt.UTC),
                "published_at": published_at.astimezone(dt.UTC),
                "load_forecast_mw": float(value) if series == "load" else None,
                "wind_forecast_mw": float(value) if series == "wind_onshore" else None,
                "solar_forecast_mw": float(value) if series == "solar" else None,
            }
        )
    return pl.DataFrame(rows, schema=empty().schema) if rows else empty()


def combine_smard_series(frames: Sequence[pl.DataFrame]) -> pl.DataFrame:
    """Combine parsed SMARD series into one wide snapshot per interval."""
    nonempty = [frame for frame in frames if not frame.is_empty()]
    if not nonempty:
        return empty()
    return (
        pl.concat(nonempty, how="vertical_relaxed")
        .group_by(["zone", "ts_utc", "published_at"])
        .agg(*(pl.col(column).drop_nulls().first().alias(column) for column in _FLOATS))
        .select(empty().schema.names())
        .sort(["ts_utc", "published_at"])
    )


def _gate(day: dt.date, zone: Zone) -> dt.datetime:
    local = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(12), ZoneInfo(zone.timezone))
    return local.astimezone(dt.UTC)
