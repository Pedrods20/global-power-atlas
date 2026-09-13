"""Compare curated prices and Brazilian load with independent official publications.

Energy-Charts redistributes ENTSO-E data, so checking it against itself proves
nothing. This script compares the curated store with sources that do not pass
through Energy-Charts:

- SMARD (Bundesnetzagentur) day-ahead prices for DE-LU and France;
- OMIE ``marginalpdbc`` daily files for the Spanish day-ahead price;
- the ONS hourly energy balance for Brazilian load, which is published
  independently of the verified-load API the store uses.

Usage::

    python scripts/audit_external.py [--data-root PATH] [--omie-step-days 7]

Writes ``.gpa/external-audit.json`` and prints a summary. Network responses are
cached under ``.gpa/external-cache`` so a rerun is reproducible offline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".gpa" / "external-cache"
SMARD = "https://www.smard.de/app/chart_data"
SMARD_FILTERS = {"DE-LU": 4169, "FR": 254}
OMIE = "https://www.omie.es/es/file-download"
ONS_BALANCE = (
    "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/"
    "balanco_energia_subsistema_ho/BALANCO_ENERGIA_SUBSISTEMA_{year}.csv"
)
TOLERANCE = 0.011  # EUR/MWh: both publications round to cents.
SWITCH = dt.datetime(2025, 9, 30, 22, tzinfo=dt.UTC)  # 2025-10-01 00:00 CEST

client = httpx.Client(
    timeout=60, follow_redirects=True, headers={"User-Agent": "global-power-atlas data audit"}
)


def get(url: str, params: dict[str, str] | None = None) -> str:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    path = CACHE / (hashlib.sha256(key.encode()).hexdigest() + ".txt")
    if path.exists():
        return path.read_text(encoding="utf-8")
    for attempt in range(6):
        response = client.get(url, params=params)
        if response.status_code not in (429, 500, 502, 503, 504):
            break
        time.sleep(5 * (attempt + 1))
    response.raise_for_status()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    time.sleep(0.3)
    return response.text


def compare(curated: pl.DataFrame, reference: pl.DataFrame, value: str) -> dict[str, object]:
    joined = curated.join(reference, on="ts_utc", how="full", coalesce=True, suffix="_ref")
    both = joined.drop_nulls([value, f"{value}_ref"])
    diff = (both[value] - both[f"{value}_ref"]).abs()
    mismatched = both.filter(diff > TOLERANCE)
    return {
        "curated_rows": curated.height,
        "reference_rows": reference.height,
        "matched_rows": both.height,
        "only_curated": joined.filter(pl.col(f"{value}_ref").is_null()).height,
        "only_reference": joined.filter(pl.col(value).is_null()).height,
        "max_abs_diff": float(diff.max()) if both.height else None,
        "mismatches": mismatched.height,
        "mismatch_examples": mismatched.head(5)
        .with_columns(pl.col("ts_utc").cast(pl.String))
        .to_dicts(),
    }


def smard(zone: str, store: pl.DataFrame) -> dict[str, object]:
    """Weekly SMARD chunks at the resolution the auction actually used."""
    filt = SMARD_FILTERS[zone]
    frames = []
    first, last = store["ts_utc"].min(), store["ts_utc"].max()
    for resolution, minutes in (("hour", 60), ("quarterhour", 15)):
        index = json.loads(get(f"{SMARD}/{filt}/DE-LU/index_{resolution}.json"))["timestamps"]
        for ms in index:
            start = dt.datetime.fromtimestamp(ms / 1000, dt.UTC)
            if start > last or start + dt.timedelta(days=7) < first:  # type: ignore[operator]
                continue
            body = json.loads(get(f"{SMARD}/{filt}/DE-LU/{filt}_DE-LU_{resolution}_{ms}.json"))
            rows = [(p[0], p[1]) for p in body["series"] if p[1] is not None]
            if rows:
                frames.append(
                    pl.DataFrame(
                        rows, schema={"ms": pl.Int64, "price_ref": pl.Float64}, orient="row"
                    ).with_columns(pl.lit(minutes).alias("resolution_ref"))
                )
    reference = (
        pl.concat(frames)
        .with_columns(
            pl.from_epoch("ms", time_unit="ms")
            .dt.replace_time_zone("UTC")
            .dt.cast_time_unit("us")
            .alias("ts_utc")
        )
        .drop("ms")
    )
    # SMARD keeps publishing hourly averages after the 2025-10-01 switch to
    # quarter-hour products, and quarter-hour copies of the earlier hourly
    # auction. Compare each period with the product the auction actually used.
    curated = store.select("ts_utc", "price")
    reference = (
        reference.filter(
            pl.when(pl.col("ts_utc") < SWITCH)
            .then(pl.col("resolution_ref") == 60)
            .otherwise(pl.col("resolution_ref") == 15)
        )
        .filter(pl.col("ts_utc").is_between(first, last))
        .select("ts_utc", "price_ref")
    )
    return compare(curated, reference, "price")


def omie(store: pl.DataFrame, step_days: int) -> dict[str, object]:
    """Spanish price from OMIE daily files: every ``step_days`` plus every DST day."""
    madrid = ZoneInfo("Europe/Madrid")
    days = store.select(
        pl.col("ts_utc").dt.convert_time_zone("Europe/Madrid").dt.date().unique().sort()
    )
    days = days.to_series().to_list()[1:-1]
    chosen = set(days[::step_days])
    for day in days:
        start = dt.datetime.combine(day, dt.time(), madrid)
        stop = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), madrid)
        if (stop - start) != dt.timedelta(days=1) or (day.day == 1 and day.month == 10):
            chosen.add(day)
    rows = []
    for day in sorted(chosen):
        name = f"marginalpdbc_{day:%Y%m%d}.1"
        text = get(OMIE, {"parents": "marginalpdbc", "filename": name})
        lines = [line.split(";") for line in text.splitlines()[1:] if line.count(";") >= 5]
        periods = len(lines)
        hours = int(
            (
                dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), madrid).astimezone(
                    dt.UTC
                )
                - dt.datetime.combine(day, dt.time(), madrid).astimezone(dt.UTC)
            ).total_seconds()
            // 3600
        )
        minutes = 60 * hours // periods
        start = dt.datetime.combine(day, dt.time(), madrid).astimezone(dt.UTC)
        for fields in lines:
            period = int(fields[3])
            rows.append((start + dt.timedelta(minutes=minutes * (period - 1)), float(fields[5])))
    reference = pl.DataFrame(
        rows, schema={"ts_utc": pl.Datetime("us", "UTC"), "price_ref": pl.Float64}, orient="row"
    )
    curated = store.filter(pl.col("ts_utc").is_in(reference["ts_utc"].implode())).select(
        "ts_utc", "price"
    )
    result = compare(curated, reference, "price")
    result["days_checked"] = len(chosen)
    return result


def ons_load(load: pl.DataFrame) -> dict[str, object]:
    """Hourly balance load against the verified-load API, testing interval labelling."""
    frames = []
    years = range(load["ts_utc"].min().year, load["ts_utc"].max().year + 1)  # type: ignore[union-attr]
    for year in years:
        raw = get(ONS_BALANCE.format(year=year))
        frame = pl.read_csv(io.StringIO(raw), separator=";", infer_schema_length=0)
        frames.append(
            frame.filter(pl.col("id_subsistema").str.strip_chars() == "SIN").select(
                pl.col("din_instante")
                .str.to_datetime("%Y-%m-%d %H:%M:%S")
                .dt.replace_time_zone("America/Sao_Paulo")
                .dt.convert_time_zone("UTC")
                .dt.cast_time_unit("us")
                .alias("ts_utc"),
                pl.col("val_carga").cast(pl.Float64, strict=False).alias("balance_mw"),
            )
        )
    balance = pl.concat(frames).drop_nulls()
    results: dict[str, object] = {}
    # The two ONS publications differ in level by a few percent (different load
    # definitions), so compare shape: scale by the median ratio, then measure
    # the error for each candidate offset of the stored series. A correctly
    # stamped store minimises the error at zero.
    scan = []
    for offset in (-60, -30, 0, 30, 60):
        hourly = (
            load.with_columns(
                (pl.col("ts_utc") + pl.duration(minutes=offset)).dt.truncate("1h").alias("hour")
            )
            .group_by("hour")
            .agg(pl.col("load_mw").mean(), pl.len().alias("n"))
            .filter(pl.col("n") == 2)
            .join(balance, left_on="hour", right_on="ts_utc")
        )
        ratio = (hourly["load_mw"] / hourly["balance_mw"]).median()
        rel = ((hourly["load_mw"] / ratio - hourly["balance_mw"]) / hourly["balance_mw"]).abs()  # type: ignore[operator]
        scan.append(
            {
                "offset_min": offset,
                "hours": hourly.height,
                "level_ratio": ratio,
                "median_abs_pct": float(rel.median() * 100),
            }
        )  # type: ignore[operator]
    results["offset_scan"] = scan
    results["best_offset_min"] = min(scan, key=lambda r: r["median_abs_pct"])["offset_min"]
    spacing = (
        load.sort("ts_utc")
        .select(pl.col("ts_utc").diff().dt.total_minutes().alias("m"))
        .drop_nulls()
    )
    results["api_spacing_minutes"] = spacing.group_by("m").len().sort("m").to_dicts()
    return results


# Approximate longitude of each system's solar fleet, degrees east.
SOLAR_LONGITUDE = {"DE-LU": 10.4, "FR": 2.5, "ES": -3.7, "BR-SIN": -44.0}


def solar_clock(generation: pl.DataFrame, longitude: float) -> dict[str, float]:
    """An absolute check of interval labelling that needs no second publisher.

    Solar output is centred on solar noon. If stamps are interval starts, the
    energy centroid is the stamp centroid plus half an interval; if they are
    interval ends, minus half. Months near the equinoxes keep the equation of
    time small. The hypothesis nearer solar noon is the provider's convention.
    """
    solar = generation.filter(
        (pl.col("fuel") == "solar")
        & (pl.col("gen_mw") > 0)
        & pl.col("ts_utc").dt.month().is_in([3, 4, 9, 10])
    )
    hour = pl.col("ts_utc").dt.hour() + pl.col("ts_utc").dt.minute() / 60
    weighted, resolution = solar.select(
        ((hour * pl.col("gen_mw")).sum() / pl.col("gen_mw").sum()),
        pl.col("resolution_min").mode().first(),
    ).row(0)
    noon = 12 - longitude / 15
    as_start = weighted + resolution / 120
    as_end = weighted - resolution / 120
    return {
        "solar_noon_utc_h": round(noon, 2),
        "centroid_if_interval_start_h": round(as_start, 2),
        "centroid_if_interval_end_h": round(as_end, 2),
        "stamps_are_interval_starts": abs(as_start - noon) < abs(as_end - noon),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=str(ROOT / "data" / "curated"))
    parser.add_argument("--omie-step-days", type=int, default=7)
    args = parser.parse_args()
    os.environ["GPA_DATA_ROOT"] = args.data_root
    from gpa import store

    report = {"data_root": args.data_root, "generated_at": dt.datetime.now(dt.UTC).isoformat()}
    for zone in SMARD_FILTERS:
        report[f"{zone} price vs SMARD"] = smard(zone, store.read("price", zone))
    report["ES price vs OMIE"] = omie(store.read("price", "ES"), args.omie_step_days)
    report["BR-SIN load vs ONS balance"] = ons_load(store.read("load", "BR-SIN"))
    for zone, longitude in SOLAR_LONGITUDE.items():
        report[f"{zone} generation solar clock"] = solar_clock(
            store.read("generation", zone), longitude
        )
    out = ROOT / ".gpa" / "external-audit.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
