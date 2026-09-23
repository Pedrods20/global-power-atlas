"""Leakage-safe fundamental forecast inputs.

German TSO forecasts of demand, wind and solar are collected for DE-LU into the
curated ``fundamentals`` dataset, today from Energy-Charts'
``/public_power_forecast``; :class:`gpa.sources.smard.SmardSource` implements the
same series independently but no zone is routed to it yet. This module defines a
small interchange format for those snapshots and selects, for each panel row,
the latest snapshot published before that row's own day-ahead gate.

What the archive cannot supply is a publication vintage, and the three builders
below differ only in the vintage they assign, which is the whole reason these
features are published as a labelled ablation rather than folded into the frozen
release:

- :func:`from_store` assigns each row the gate of its own delivery day, a stated
  research policy, for the retrospective ablation.
- :func:`from_store_observed` assigns one observed retrieval instant to every
  row. Honest, and correct only where no training history is needed.
- :func:`from_store_prospective` is what a live issue uses: the policy vintage
  across the training history, the observed instant on the delivery day alone.
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
    "FUNDAMENTAL_COLUMNS",
    "FUNDAMENTAL_FEATURES",
    "FUNDAMENTAL_METADATA",
    "SMARD_FORECAST_FILTERS",
    "attach",
    "combine_smard_series",
    "empty",
    "from_smard_series",
    "from_store",
    "from_store_observed",
    "from_store_prospective",
    "normalize",
]

FUNDAMENTAL_FEATURES: Final[tuple[str, ...]] = (
    "da_load_forecast",
    "da_wind_forecast",
    "da_solar_forecast",
    "da_residual_load_forecast",
)
"""Names a modelling panel fits when fundamental snapshots are supplied."""

FUNDAMENTAL_METADATA: Final[tuple[str, ...]] = ("da_forecast_age_hours",)
"""Attached beside the features as recorded evidence, and never fitted.

The age is the interval between the snapshot's vintage and the delivery day's
gate, so it is identically zero wherever the vintage *is* the gate -- which is
every row of the retrospective research policy (:func:`from_store`) and every
training row of the prospective mixed vintage (:func:`from_store_prospective`).
A column that is constant across a training window carries no information:
:func:`gpa.forecast.linalg.ridge_from_moments` already excludes it and returns a
zero coefficient, so fitting it was harmless but also meaningless, and offering
it as a feature invited the reader to believe the model had learned something
from a measured retrieval lag. It is kept on the panel because an auditor
checking what an issue knew should be able to read the age off the evidence.
"""

FUNDAMENTAL_COLUMNS: Final[tuple[str, ...]] = FUNDAMENTAL_FEATURES + FUNDAMENTAL_METADATA
"""Every column :func:`attach` adds, fitted or not."""

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
            *(pl.lit(None, dtype=pl.Float64).alias(name) for name in FUNDAMENTAL_COLUMNS)
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
        .select("local_date", "local_hour", *FUNDAMENTAL_COLUMNS)
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

    A live, prospective issue must not use this function for the day it is
    forecasting: it can observe its own retrieval instant there. It does still
    use this policy vintage across its training history, where no observed
    instant exists; :func:`from_store_prospective` combines the two.
    """
    local = _hourly_wide(zone)
    if local is None:
        return empty()
    return local.join(_gates(local, zone, "published_at"), on="local_date").select(
        empty().schema.names()
    )


def from_store_observed(zone: Zone, *, retrieved_at: dt.datetime) -> pl.DataFrame:
    """The same archive rows, stamped with an instant that was actually observed.

    This is the *observed* counterpart to :func:`from_store`, and the difference
    between them is the whole reason these features are not in the published
    retrospective release. ``retrieved_at`` is when this run read the forecast,
    so it is a real, varying instant rather than a policy constant:
    :func:`attach` measures ``da_forecast_age_hours`` from it, and a snapshot
    read after the gate is correctly dropped instead of being assumed eligible.

    It is an upper bound on the provider's own publication time, since the value
    was published at or before the moment this project retrieved it. That
    direction is the safe one: a later stamp can only make a row less eligible,
    never more.

    Applying it to a whole archive is what a caller must not do when the result
    has to be fitted: one present-day stamp fails every historical row's gate and
    leaves no training history at all. :func:`from_store_prospective` is the
    builder a live issue wants; this one is the primitive underneath it and the
    right choice only where no history is needed.

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


def from_store_prospective(
    zone: Zone, *, delivery_date: dt.date, retrieved_at: dt.datetime
) -> pl.DataFrame:
    """The vintage a live issue can actually defend, for a panel that must train.

    :func:`from_store_observed` is the honest primitive, and handing it to a
    modelling panel is still wrong, because it stamps the *whole* archive with
    one instant. :func:`attach` keeps a snapshot only when its vintage precedes
    that row's own D-1 gate, and every historical gate is in the past, so a
    single present-day stamp erases the fundamental features from all of
    history. The model then has nothing to fit and every delivery hour abstains
    -- which is precisely what the first version of the prospective arm did.

    This splits the two roles the vintage plays. Training rows keep the
    research-policy gate :func:`from_store` assigns, because there the vintage
    only decides which rows may be *fitted*. The delivery day carries
    ``retrieved_at``, because there it decides what the issued forecast is
    allowed to know -- and that is the claim a reader would challenge. A
    retrieval after the delivery day's gate is still refused rather than
    backdated, so the arm abstains on a day the provider published too late
    instead of quietly issuing from a later information set.

    The assigned vintage therefore survives in the fit and nowhere else, which
    is a disclosed limitation of this arm rather than a hidden one: see the
    Methodology page.

    Args:
        zone: Supplies the market timezone and the store partition.
        delivery_date: The market-local day being forecast.
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
    return (
        local.join(_gates(local, zone, "_policy_vintage"), on="local_date")
        .with_columns(
            pl.when(pl.col("local_date") == delivery_date)
            .then(pl.lit(stamp).cast(pl.Datetime(time_unit="us", time_zone="UTC")))
            .otherwise(pl.col("_policy_vintage"))
            .alias("published_at")
        )
        .select(empty().schema.names())
    )


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


def _gates(frame: pl.DataFrame, zone: Zone, name: str) -> pl.DataFrame:
    """One D-1 gate per distinct delivery day present in ``frame``.

    Per day rather than per row: a multi-year hourly frame holds far fewer
    unique dates than rows, and :func:`_gate` is Python-level.
    """
    return (
        frame.select("local_date")
        .unique()
        .with_columns(
            pl.struct("local_date")
            .map_elements(
                lambda value: _gate(value["local_date"], zone),
                return_dtype=pl.Datetime("us", "UTC"),
            )
            .alias(name)
        )
    )


def _gate(day: dt.date, zone: Zone) -> dt.datetime:
    local = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(12), ZoneInfo(zone.timezone))
    return local.astimezone(dt.UTC)
