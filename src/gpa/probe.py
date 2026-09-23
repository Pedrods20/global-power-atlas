"""When a provider's day-ahead forecasts for the next delivery day appear.

The fundamentals archive carries no publication vintage (see
:mod:`gpa.forecast.fundamentals`), and the rule that governs it points the wrong
way for this project: Commission Regulation (EU) 543/2013, Article 14(1)(d),
requires day-ahead wind and solar forecasts by 18:00 Brussels time on D-1, six
hours *after* the 12:00 day-ahead gate. Whether a public copy nevertheless
exists before the gate decides two things -- whether the prospective ``ridge_da``
arm can ever issue, and whether the labelled ablation's assigned noon vintage is
a fair research policy or an optimistic one.

That is a question of fact, so this module answers it by observation. Each probe
records, per series, how much of the next delivery day the provider serves at
the instant of the check and how far that instant sits from the gate. A series
of probes brackets the publication time; nothing here infers one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import polars as pl

from gpa.zones import Zone

SERIES: Final[tuple[str, ...]] = ("load", "wind", "solar")
"""The fundamentals series ``ridge_da`` needs; all three must cover the day."""

SCHEMA: Final = pl.Schema(
    {
        "checked_at_utc": pl.String,
        "zone": pl.String,
        "delivery_date": pl.String,
        "series": pl.String,
        "hours_expected": pl.Int64,
        "hours_covered": pl.Int64,
        "minutes_before_gate": pl.Int64,
    }
)
"""Plain columns, so the log reads as a CSV and diffs line by line.

``minutes_before_gate`` is negative once the gate has passed.
"""

Fetch = Callable[[Zone, str, dt.datetime, dt.datetime], pl.DataFrame]


def check(zone: Zone, *, now: dt.datetime, fetch: Fetch) -> pl.DataFrame:
    """One row per series for tomorrow's delivery day, as served at ``now``.

    Coverage counts clock hours with at least one value, against the hours the
    local day actually has (23 or 25 on a clock change), so a quarter-hour and
    an hourly series are measured on the same scale.
    """
    from gpa.forecast.ledger import market_gate

    if now.tzinfo is None:
        raise ValueError("the probe clock must be timezone-aware")
    tz = ZoneInfo(zone.timezone)
    delivery = now.astimezone(tz).date() + dt.timedelta(days=1)
    start = dt.datetime.combine(delivery, dt.time(), tz).astimezone(dt.UTC)
    end = dt.datetime.combine(delivery + dt.timedelta(days=1), dt.time(), tz).astimezone(dt.UTC)
    expected = int((end - start).total_seconds() // 3600)
    served = fetch(zone, "fundamentals", start, end).filter(
        (pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end)
    )
    gate = market_gate(delivery, zone)
    rows = []
    for series in SERIES:
        hours = served.filter(pl.col("series") == series)["ts_utc"].dt.truncate("1h").n_unique()
        rows.append(
            {
                "checked_at_utc": now.astimezone(dt.UTC).isoformat(timespec="seconds"),
                "zone": zone.code,
                "delivery_date": delivery.isoformat(),
                "series": series,
                "hours_expected": expected,
                "hours_covered": hours,
                "minutes_before_gate": int((gate - now).total_seconds() // 60),
            }
        )
    return pl.DataFrame(rows, schema=SCHEMA)


def append(frame: pl.DataFrame, path: Path) -> Path:
    """Add rows to the log; the header is written once, with the first rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as file:
        frame.select(SCHEMA.names()).write_csv(file, include_header=fresh)
    return path


def read(path: Path) -> pl.DataFrame:
    """The whole log, typed; empty when nothing has been probed yet."""
    if not path.exists() or path.stat().st_size == 0:
        return pl.DataFrame(schema=SCHEMA)
    return pl.read_csv(path, schema=SCHEMA)
