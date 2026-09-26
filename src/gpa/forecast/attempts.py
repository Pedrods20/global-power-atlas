"""Append-only attempt events, and the daily denominator the pilot is judged on."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, cast

import polars as pl

from gpa.calendar import delivery_hours
from gpa.forecast import ledger
from gpa.forecast.provenance import default_model, utc
from gpa.zones import get_zone

STATUSES = {"issued", "partial", "abstained", "late", "failed", "already_issued"}
"""``already_issued`` closes a backstop run against the canonical issue already on record."""

_UNVERIFIED = (
    ("started", "incomplete_attempt"),
    ("issued", "unverified_issue"),
    ("partial", "partial"),
    ("abstained", "abstained"),
    ("late", "late"),
    ("failed", "failed"),
)
"""How a day without a verified issue is labelled: the first state any attempt reached."""


def _path(root: Path, identifier: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", identifier):
        raise ValueError("invalid attempt identifier")
    return Path(root) / "attempts" / identifier


def read(root: Path, identifier: str) -> dict[str, Any]:
    path = _path(root, identifier)
    event = cast(dict[str, Any], json.loads((path / "started.json").read_text(encoding="utf-8")))
    completion = path / "completed.json"
    if completion.exists():
        event.update(json.loads(completion.read_text(encoding="utf-8")))
    else:
        event["status"] = "started"
    return event


def _create(path: Path, event: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as file:
        json.dump(event, file, indent=2, sort_keys=True)


def start(
    root: Path,
    identifier: str,
    *,
    zone: str,
    model: str,
    delivery_date: dt.date,
    started_at: dt.datetime | None = None,
    origin: str = "manual",
) -> dict[str, Any]:
    """Record an attempt before any input is read; a repeated start must match the first."""
    get_zone(zone)
    default_model(model)
    if origin not in {"manual", "schedule", "workflow_dispatch"}:
        raise ValueError("unknown attempt origin")
    event = {
        "attempt_id": identifier,
        "zone": zone,
        "model": model,
        "delivery_date": delivery_date.isoformat(),
        "origin": origin,
        "started_at": utc(started_at or ledger.now_utc()).isoformat(),
    }
    path = _path(root, identifier)
    if (path / "started.json").exists():
        existing = read(root, identifier)
        if any(existing[key] != event[key] for key in ("zone", "model", "delivery_date", "origin")):
            raise ValueError("attempt identity conflicts with original start")
        return existing
    path.mkdir(parents=True, exist_ok=True)
    _create(path / "started.json", event)
    return read(root, identifier)


def finish(
    root: Path,
    identifier: str,
    *,
    status: str,
    completed_at: dt.datetime | None = None,
    issue_id: str | None = None,
    error_type: str | None = None,
    if_open: bool = False,
) -> dict[str, Any]:
    """Close an attempt once; errors are stored as class names, never raw provider messages."""
    if status not in STATUSES:
        raise ValueError("invalid attempt completion status")
    existing = read(root, identifier)
    if existing["status"] != "started":
        if if_open:
            return existing
        raise ValueError("attempt is already completed; start a new attempt for a retry")
    stamp = utc(completed_at or ledger.now_utc())
    if stamp < dt.datetime.fromisoformat(existing["started_at"]):
        raise ValueError("completion clock precedes attempt start")
    if error_type is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,119}", error_type):
        raise ValueError("error_type must be a short class or stage code")
    if issue_id is not None and not re.fullmatch(r"[a-f0-9]{64}", issue_id):
        raise ValueError("invalid issue identifier")
    event = {
        "status": status,
        "completed_at": stamp.isoformat(),
        "issue_id": issue_id,
        "error_type": error_type,
    }
    _create(_path(root, identifier) / "completed.json", event)
    return read(root, identifier)


def report(
    root: Path,
    *,
    start_date: dt.date,
    end_date: dt.date,
    zone: str = "DE-LU",
    model: str = "ridge",
) -> pl.DataFrame:
    """One row per expected delivery day, attempted or not.

    The requested window is the denominator and must be chosen before reading the
    result. A day counts only when an attempt references its canonical verified issue.
    """
    if start_date > end_date:
        raise ValueError("report start must not follow end")
    market = get_zone(zone)
    default_model(model)
    events = [
        event
        for event in (
            read(root, path.parent.name)
            for path in sorted(Path(root).joinpath("attempts").glob("*/started.json"))
        )
        if event["zone"] == zone and event["model"] == model
    ]
    selected = ledger.canonical(ledger.read(root=root, zone=zone), root=root).filter(
        pl.col("model") == model
    )
    rows = []
    for offset in range((end_date - start_date).days + 1):
        day = start_date + dt.timedelta(days=offset)
        attempts = [event for event in events if event["delivery_date"] == day.isoformat()]
        chosen = selected.filter(pl.col("delivery_date") == day)
        identifier = chosen["issue_id"][0] if not chosen.is_empty() else None
        verified = bool(identifier) and any(
            event.get("issue_id") == identifier and event["status"] == "issued"
            for event in attempts
        )
        states = {event["status"] for event in attempts}
        rows.append(
            {
                "delivery_date": day,
                "zone": zone,
                "model": model,
                "expected_hours": delivery_hours(day, market).height,
                "attempts": len(attempts),
                "scheduled_attempts": sum(event["origin"] == "schedule" for event in attempts),
                "failed_attempts": sum(event["status"] == "failed" for event in attempts),
                "status": "issued"
                if verified
                else next(
                    (label for state, label in _UNVERIFIED if state in states), "missing_attempt"
                ),
                "eligible": verified,
                "issue_id": identifier if verified else None,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "delivery_date": pl.Date,
            "zone": pl.String,
            "model": pl.String,
            "expected_hours": pl.UInt32,
            "attempts": pl.UInt32,
            "scheduled_attempts": pl.UInt32,
            "failed_attempts": pl.UInt32,
            "status": pl.String,
            "eligible": pl.Boolean,
            "issue_id": pl.String,
        },
    )
