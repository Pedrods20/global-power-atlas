"""When the next delivery day's day-ahead forecasts appear: observed, never inferred.

Regulation (EU) 543/2013, Art. 14(1)(d), only requires wind and solar by 18:00 on
D-1, after the noon gate. Whether a copy is public earlier decides if ``ridge_da``
can ever issue and if the ablation's noon vintage is fair; each probe logs coverage
and its distance from the gate, and a series of probes brackets the answer.
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
"""Plain columns, so the log diffs line by line; ``minutes_before_gate`` goes negative after it."""

Fetch = Callable[[Zone, str, dt.datetime, dt.datetime], pl.DataFrame]


def check(zone: Zone, *, now: dt.datetime, fetch: Fetch) -> pl.DataFrame:
    """Per series, the clock hours of tomorrow served at ``now``, against the day's real hours."""
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
