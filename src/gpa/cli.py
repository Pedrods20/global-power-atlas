"""Command line interface for the data, forecast and portfolio workflows.

The main portfolio commands are:

``gpa zones``      inspect the registry
``gpa ingest``     fetch a recent window, for the daily scheduled run
``gpa backfill``   fetch a long history, for the one-off initial load
``gpa validate``   re-check everything on disk against the schema contracts
``gpa stats``      report what the store holds
``gpa backtest``   walk-forward price forecast, scored against naive baselines
``gpa issue``      issue a feature-only forecast and retain its evidence
``gpa reconcile``  attach observed prices to issued forecasts
``gpa battery``    evaluate reconciled forecasts through the battery dispatch

Exit codes matter because a scheduled workflow reads them: 0 when every target
succeeded or was cleanly skipped, 1 when any target failed.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import polars as pl
import typer
from dotenv import load_dotenv

from gpa import __version__, pipeline, store
from gpa.schema import SchemaError, SchemaErrors
from gpa.schema import validate as validate_frame
from gpa.zones import ZONES, get_zone

load_dotenv()
DEFAULT_TRACKING_DIR = Path(".gpa/mlflow")

app = typer.Typer(
    name="gpa",
    help="Global Power Atlas: ingest and inspect wholesale electricity market data.",
    no_args_is_help=True,
    add_completion=False,
)

# Polars tables use Unicode box drawing; redirected Windows consoles may
# otherwise fail before printing a result. This changes only our CLI streams.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ZoneOption = Annotated[
    list[str] | None,
    typer.Option("--zone", "-z", help="Zone code; repeat for several. Default: all."),
]
DatasetOption = Annotated[
    list[str] | None,
    typer.Option("--dataset", "-d", help="price, load or generation. Default: all."),
]
VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Log adapter detail.")]


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # HTTPX logs can include provider query strings; keep them out of normal output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _report(results: list[pipeline.IngestResult]) -> None:
    for result in results:
        typer.echo(str(result))

    counts = pipeline.summarise(results)
    typer.echo("")
    typer.echo(
        f"written {counts['written']} | empty {counts['empty']} | "
        f"skipped {counts['skipped']} | failed {counts['failed']}"
    )

    if counts["skipped"]:
        typer.secho(
            "Some targets were skipped for a missing credential. "
            "They will start collecting as soon as the key is set.",
            fg=typer.colors.YELLOW,
        )
    if counts["failed"]:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def zones(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Include notes.")] = False,
) -> None:
    """List the registered market zones."""
    rows = [
        {
            "code": z.code,
            "name": z.name,
            "region": z.region.value,
            "timezone": z.timezone,
            "market_dst": "yes" if z.observes_market_dst else "no",
            "currency": z.currency,
            "peak_block": z.peak.label,
            "datasets": ", ".join(f"{k}:{v}" for k, v in sorted(z.sources.items())),
        }
        for z in ZONES
    ]
    with pl.Config(tbl_rows=-1, tbl_width_chars=200, fmt_str_lengths=60):
        typer.echo(str(pl.DataFrame(rows)))

    if verbose:
        for z in ZONES:
            typer.echo("")
            typer.secho(f"{z.code} - {z.name}", bold=True)
            typer.echo(f"  {z.notes}")
            if z.peak.note:
                typer.secho(f"  Caveat: {z.peak.note}", fg=typer.colors.YELLOW)


@app.command()
def ingest(
    zone: ZoneOption = None,
    dataset: DatasetOption = None,
    days: Annotated[int, typer.Option(help="Trailing days to refetch.")] = 7,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Fetch but write nothing.")] = False,
    verbose: VerboseOption = False,
) -> None:
    """Fetch a recent window and upsert it into the store.

    Refetching a trailing window rather than only yesterday is deliberate:
    providers publish on a lag and revise afterwards, and the store upserts, so
    overlapping runs converge on the restated figures.
    """
    _configure_logging(verbose)
    results = pipeline.ingest(zone, dataset, lookback_days=days, dry_run=dry_run)
    _report(results)


@app.command()
def backfill(
    zone: ZoneOption = None,
    dataset: DatasetOption = None,
    years: Annotated[float, typer.Option(help="Years of history to load.")] = 2.0,
    days: Annotated[int | None, typer.Option(help="Days of history; overrides --years.")] = None,
    chunk_days: Annotated[int, typer.Option(help="Days fetched per request.")] = 60,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Fetch but write nothing.")] = False,
    verbose: VerboseOption = False,
) -> None:
    """Load a long history in chunks, writing each chunk as it arrives.

    Interrupting a backfill keeps whatever already landed, so it can simply be
    run again to finish.
    """
    _configure_logging(verbose)
    lookback = days if days is not None else max(1, round(years * 365))
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=lookback)

    typer.echo(
        f"Backfilling {start.date().isoformat()} to {end.date().isoformat()} "
        f"({lookback} days) in {chunk_days}-day chunks."
    )
    results = pipeline.ingest(
        zone, dataset, start=start, end=end, chunk_days=chunk_days, dry_run=dry_run
    )
    _report(results)


@app.command()
def validate(verbose: VerboseOption = False) -> None:
    """Re-check every stored partition against its schema contract.

    This is what catches a file that was written by an older version of an
    adapter, or hand-edited, or corrupted by an interrupted write.
    """
    _configure_logging(verbose)

    failures = 0
    checked = 0
    for dataset in store.DATASETS:
        root = store.dataset_dir(dataset)
        if not root.is_dir():
            continue
        for path in sorted(root.glob("zone=*/*.parquet")):
            checked += 1
            try:
                validate_frame(pl.read_parquet(path), dataset)
            except (SchemaError, SchemaErrors) as exc:
                failures += 1
                typer.secho(f"FAIL {path}", fg=typer.colors.RED)
                typer.echo(f"     {str(exc).strip().splitlines()[0][:200]}")
            except Exception as exc:
                failures += 1
                typer.secho(f"FAIL {path}: {type(exc).__name__}: {exc}", fg=typer.colors.RED)

    if checked == 0:
        typer.secho("No partitions found. Run 'gpa backfill' first.", fg=typer.colors.YELLOW)
        return

    if failures:
        typer.secho(f"{failures} of {checked} partitions failed.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(f"All {checked} partitions valid.", fg=typer.colors.GREEN)


@app.command()
def stats() -> None:
    """Report row counts, coverage and freshness per dataset and zone."""
    frame = store.coverage()
    if frame.is_empty():
        typer.secho("Store is empty. Run 'gpa backfill' first.", fg=typer.colors.YELLOW)
        return

    now = dt.datetime.now(dt.UTC)
    display = frame.with_columns(
        ((pl.lit(now) - pl.col("last_ts_utc")).dt.total_minutes() / 60.0)
        .round(1)
        .alias("age_hours"),
        (pl.col("bytes") / 1024).round(0).cast(pl.Int64).alias("kb"),
    ).select(
        "dataset", "zone", "rows", "partitions", "kb", "first_ts_utc", "last_ts_utc", "age_hours"
    )

    with pl.Config(tbl_rows=-1, tbl_width_chars=200):
        typer.echo(str(display))

    typer.echo("")
    typer.echo(f"Total rows: {frame['rows'].sum():,}")
    typer.echo(f"Total size: {frame['bytes'].sum() / 1024 / 1024:.1f} MB")


@app.command()
def freshness(
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict", help="Exit non-zero when a scheduled series is stale."
        ),
    ] = True,
) -> None:
    """Report how stale each series is against its declared rule.

    The scheduled workflow runs this so that a silent stall becomes a visible
    failure. A run that fetched nothing still exits zero if no adapter raised,
    which is exactly the case this catches.

    A series declared manual reports but never fails the run: nobody can fix
    it from a cron job, and failing nightly would train people to ignore the
    failure. No active series is manual today.
    """
    from gpa import freshness as freshness_module

    reports = freshness_module.check()
    if not reports:
        typer.secho("No zones declare a source.", fg=typer.colors.YELLOW)
        return

    for report in reports:
        colour = None
        if report.blocking:
            colour = typer.colors.RED
        elif report.stale or report.missing:
            colour = typer.colors.YELLOW
        typer.secho(str(report), fg=colour)

    blocking = [r for r in reports if r.blocking]
    reminders = [r for r in reports if (r.stale or r.missing) and not r.blocking]

    typer.echo("")
    if reminders:
        typer.secho(
            f"{len(reminders)} manually refreshed series need attention:", fg=typer.colors.YELLOW
        )
        for report in reminders:
            typer.echo(f"  {report.zone} {report.dataset}: {report.rule.reason}")

    if not blocking:
        typer.secho(
            f"All {len(reports)} series are within their freshness rules.", fg=typer.colors.GREEN
        )
        return

    typer.secho(f"{len(blocking)} series are stale:", fg=typer.colors.RED)
    for report in blocking:
        typer.echo(f"  {report.zone} {report.dataset}: {report.rule.reason}")

    if strict:
        raise typer.Exit(code=1)


@app.command()
def audit() -> None:
    """Check interval integrity and report internal missing time for each fuel."""
    from gpa import quality

    result = quality.report()
    with pl.Config(tbl_rows=-1, tbl_width_chars=160):
        typer.echo(result)
    if result.filter(pl.col("invalid") > 0).height:
        raise typer.Exit(code=1)


@app.command()
def export(
    output: Annotated[
        str | None, typer.Option("--output", "-o", help="Destination directory.")
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Check committed tables without changing them.")
    ] = False,
    verbose: VerboseOption = False,
) -> None:
    """Build the aggregated tables the static site reads.

    Run this after ingesting and before building the site. It reduces the
    hourly store to a few hundred kilobytes of per-zone aggregates.
    """
    _configure_logging(verbose)
    from pathlib import Path

    from gpa.export import check_exports, export_all, site_root

    destination = Path(output) if output else site_root()
    if check:
        differences = check_exports(destination)
        if differences:
            typer.echo("Outdated exports: " + ", ".join(differences))
            raise typer.Exit(1)
        typer.echo("Exported tables match the committed observations.")
        return
    written = export_all(destination)

    for name, rows in sorted(written.items()):
        typer.echo(f"{name:26s} {rows:>10,d} rows")

    total_bytes = sum(p.stat().st_size for p in destination.glob("*") if p.is_file())
    typer.echo("")
    typer.secho(
        f"Wrote {len(written)} files to {destination} ({total_bytes / 1024:.0f} KB).",
        fg=typer.colors.GREEN,
    )


@app.command()
def backtest(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to forecast.")] = "DE-LU",
    scope: Annotated[
        str, typer.Option("--scope", help="overall, block, regime, year, hour or all.")
    ] = "overall",
    min_train_days: Annotated[
        int, typer.Option(help="Usable days required before the first fit.")
    ] = 0,
    validation_days: Annotated[
        int, typer.Option(help="Days reserved for choosing the ridge penalty.")
    ] = 0,
    alpha: Annotated[
        float | None, typer.Option(help="Fix the ridge penalty instead of selecting it.")
    ] = None,
    window: Annotated[
        int | None, typer.Option(help="Rolling training window in days. Default: expanding.")
    ] = None,
    tune_lightgbm: Annotated[
        bool,
        typer.Option(
            "--tune-lightgbm/--no-tune-lightgbm",
            help="Choose LightGBM tree settings on the pre-test validation window.",
        ),
    ] = True,
    lightgbm_refit_days: Annotated[
        int,
        typer.Option(
            help="LightGBM refit cadence in days; use 1 operationally and larger values for long diagnostics."
        ),
    ] = 1,
    price_only: Annotated[
        bool,
        typer.Option(
            help="Run a long price-only stress test without requiring old load/generation history."
        ),
    ] = False,
    track: Annotated[bool, typer.Option(help="Record this comparison in local MLflow.")] = False,
    tracking_dir: Annotated[
        Path, typer.Option(help="Local MLflow database and artifacts.")
    ] = DEFAULT_TRACKING_DIR,
    save_snapshot: Annotated[
        bool,
        typer.Option(
            "--save-snapshot",
            help="Freeze this run as a content-addressed experiment under data/experiments/.",
        ),
    ] = False,
    verbose: VerboseOption = False,
) -> None:
    """Walk-forward backtest of the day-ahead price forecast.

    Every model is refitted at each step on an expanding origin and scored on
    hours that all of them could forecast, so the columns compare like with
    like. Skill is reported both against the declared reference baseline and
    against whichever baseline actually won the bucket, which is the harder and
    more honest of the two.
    """
    _configure_logging(verbose)
    from gpa.forecast import backtest as harness

    if scope not in ("overall", "block", "regime", "year", "hour", "all"):
        raise typer.BadParameter("scope must be overall, block, regime, year, hour or all")
    if min_train_days < 0 or validation_days < 0:
        raise typer.BadParameter("training and validation days cannot be negative")
    if lightgbm_refit_days < 1:
        raise typer.BadParameter("lightgbm refit cadence must be positive")
    result = harness.run(
        zone,
        min_train_days=min_train_days or harness.MIN_TRAIN_DAYS,
        validation_days=validation_days or harness.VALIDATION_DAYS,
        alpha=alpha,
        window=window,
        tune_lightgbm=tune_lightgbm,
        lightgbm_refit_days=lightgbm_refit_days,
        include_actual_features=not price_only,
    )

    if track:
        from gpa.forecast.tracking import track_result

        run_id = track_result(result, tracking_dir)
        typer.echo(f"MLflow run: {run_id} ({tracking_dir.resolve()})")

    if save_snapshot:
        from gpa.forecast import snapshot

        try:
            path = snapshot.save(result)
        except FileExistsError as exc:
            typer.secho(str(exc), fg=typer.colors.YELLOW)
        else:
            typer.secho(f"Snapshot saved: {path}", fg=typer.colors.GREEN)

    typer.secho(f"{result.zone.code} day-ahead hourly price", bold=True)
    typer.echo(
        "Retrospective development benchmark; latest revised data, not a prospective result."
    )
    typer.echo(
        f"  train from {result.train_start}, validate from {result.validation_start}, "
        f"test {result.test_start} to {result.test_end} ({result.test_days} days)"
    )
    typer.echo(
        f"  {len(result.features)} features, ridge penalty {result.alpha:g}, "
        f"reference {harness.REFERENCE_MODEL}"
    )
    typer.echo("")

    scopes = ("overall", "block", "regime", "year", "hour") if scope == "all" else (scope,)
    shown = result.scores.filter(pl.col("scope").is_in(list(scopes)))
    if shown.is_empty():
        typer.secho(f"No such scope: {scope!r}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    with pl.Config(tbl_rows=-1, tbl_width_chars=220, float_precision=2):
        typer.echo(
            str(
                shown.select(
                    "scope",
                    "bucket",
                    "model",
                    "n",
                    "mae",
                    "rmse",
                    "bias",
                    "mean_pinball",
                    "skill_pct",
                    "skill_vs_best_baseline_pct",
                )
            )
        )

    # Always print where the model is weakest, whether or not it lost outright.
    # A scoreboard that only reports the aggregate is how a model with no skill
    # in the hours that matter gets published as a success.
    typer.echo("")
    fitted = result.scores.filter(~pl.col("model").is_in(list(result.baselines))).sort(
        "skill_vs_best_baseline_pct"
    )
    losing = fitted.filter(pl.col("skill_vs_best_baseline_pct") <= 0.0)
    if losing.is_empty():
        typer.secho(
            "Fitted models beat every baseline in every bucket. Weakest margins:",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"Fitted models lost to a naive baseline in {losing.height} model/bucket pairs:",
            fg=typer.colors.YELLOW,
        )
    for row in fitted.head(3).iter_rows(named=True):
        typer.echo(
            f"  {row['model']} {row['scope']} {row['bucket']}: "
            f"{row['skill_vs_best_baseline_pct']:+.1f}% against the best baseline, "
            f"MAE {row['mae']:.2f}"
        )


@app.command()
def issue(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to forecast.")] = "DE-LU",
    delivery_date: Annotated[
        str | None,
        typer.Option(help="Delivery date YYYY-MM-DD. Default: tomorrow in market time."),
    ] = None,
    model: Annotated[str, typer.Option(help="ridge or lightgbm.")] = "ridge",
    allow_late: Annotated[
        bool, typer.Option(help="Allow a late issue for diagnostics; labels it in the ledger.")
    ] = False,
    output: Annotated[
        Path | None, typer.Option(help="Issue ledger root. Default: data/forecast_issues.")
    ] = None,
) -> None:
    """Issue tomorrow's feature-only forecast and persist its evidence.

    Every delivery hour is retained, including explicit abstentions when a
    required feature or enough training history is unavailable.
    """
    from gpa.forecast import ledger
    from gpa.forecast.boosting import LightGBM
    from gpa.forecast.models import Ridge
    from gpa.forecast.panel import build_panel

    market = get_zone(zone)
    if delivery_date is None:
        local_today = dt.datetime.now(ZoneInfo(market.timezone)).date()
        target_date = local_today + dt.timedelta(days=1)
    else:
        try:
            target_date = dt.date.fromisoformat(delivery_date)
        except ValueError as exc:
            raise typer.BadParameter("delivery date must be YYYY-MM-DD") from exc

    prices = store.read("price", market.code)
    load = store.read("load", market.code) if market.has("load") else None
    generation = store.read("generation", market.code) if market.has("generation") else None
    prepared = build_panel(
        prices,
        market,
        load=load,
        generation=generation,
        delivery_date=target_date,
    )
    forecaster: Ridge | LightGBM
    if model == "ridge":
        forecaster = Ridge(alpha=1.0)
    elif model == "lightgbm":
        forecaster = LightGBM()
    else:
        raise typer.BadParameter("model must be ridge or lightgbm")

    issued_at = dt.datetime.now(dt.UTC)
    frame = ledger.issue(
        prepared,
        forecaster,
        target_date,
        issued_at=issued_at,
        allow_late=allow_late,
    )
    issued = frame.filter(pl.col("status") == "issued").height
    abstained = frame.height - issued
    if issued == 0:
        typer.secho(
            f"No forecasts available for {market.code} {target_date}; "
            "the issue was not persisted. Check the pre-gate input window.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    path = ledger.append(frame, root=output or ledger.DEFAULT_ROOT)
    typer.secho(
        f"Issued {market.code} {target_date}: {issued} forecasts, "
        f"{abstained} abstentions -> {path}",
        fg=typer.colors.GREEN if abstained == 0 else typer.colors.YELLOW,
    )


@app.command()
def reconcile(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to reconcile.")] = "DE-LU",
    output: Annotated[
        Path | None, typer.Option(help="Issue ledger root. Default: data/forecast_issues.")
    ] = None,
) -> None:
    """Attach observed hourly prices to every stored issue for a zone.

    Reconciliation is idempotent: it rewrites the same zone/month partitions,
    preserving the original issue timestamp and input fingerprint while moving
    rows from ``issued`` to ``scored`` or ``issued_waiting_for_actual``.
    """
    from gpa.forecast import ledger

    market = get_zone(zone)
    root = output or ledger.DEFAULT_ROOT
    issues = ledger.read(root=root, zone=market.code)
    if issues.is_empty():
        typer.secho(f"No issue records for {market.code} under {root}.", fg=typer.colors.YELLOW)
        return

    prices = store.read("price", market.code)
    reconciled = ledger.reconcile(issues, prices, market)
    months = (
        reconciled.with_columns(pl.col("delivery_date").dt.strftime("%Y-%m").alias("_month"))[
            "_month"
        ]
        .unique()
        .sort()
        .to_list()
    )
    for month in months:
        ledger.append(
            reconciled.filter(pl.col("delivery_date").dt.strftime("%Y-%m") == month),
            root=root,
        )

    counts = {
        row["status"]: row["len"]
        for row in reconciled.group_by("status").len().iter_rows(named=True)
    }
    typer.echo(
        f"Reconciled {market.code}: {reconciled.height} rows | "
        + " | ".join(f"{status}={counts[status]}" for status in sorted(counts))
    )


@app.command("battery")
def battery_value(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to evaluate.")] = "DE-LU",
    ledger_root: Annotated[
        Path | None, typer.Option(help="Issue ledger root. Default: data/forecast_issues.")
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(help="Optional directory for dispatch and summary Parquet files."),
    ] = None,
    horizon_steps: Annotated[
        int, typer.Option(help="Rolling optimisation horizon in market intervals.")
    ] = 24,
) -> None:
    """Evaluate reconciled forecasts as constrained battery dispatch.

    The command is intentionally separate from ``export``: prospective ledger
    evaluation can be persisted without changing the monthly dashboard.
    """
    from gpa import battery as battery_module
    from gpa.forecast import ledger

    market = get_zone(zone)
    root = ledger_root or ledger.DEFAULT_ROOT
    issues = ledger.read(root=root, zone=market.code)
    if issues.is_empty():
        typer.secho(f"No issue records for {market.code} under {root}.", fg=typer.colors.YELLOW)
        return
    reconciled = ledger.reconcile(issues, store.read("price", market.code), market)
    scored = reconciled.filter(pl.col("status") == "scored").select(
        "model",
        pl.col("delivery_date").alias("local_date"),
        "local_hour",
        "forecast",
        "actual",
    )
    if scored.is_empty():
        typer.secho("No complete scored delivery rows yet.", fg=typer.colors.YELLOW)
        return

    result = battery_module.backtest_predictions(
        scored,
        model_names=("ridge", "lightgbm"),
        durations_mwh=(1.0, 4.0),
        horizon_steps=horizon_steps,
    )
    if output is not None:
        destination = Path(output)
        destination.mkdir(parents=True, exist_ok=True)
        result.dispatch.write_parquet(destination / f"{market.code}_dispatch.parquet")
        result.summary.write_parquet(destination / f"{market.code}_summary.parquet")
        typer.echo(f"Wrote battery evaluation to {destination}.")
    with pl.Config(tbl_rows=-1, tbl_width_chars=180, float_precision=2):
        typer.echo(str(result.summary))


@app.command()
def query(
    sql: Annotated[str, typer.Argument(help="SQL over views named price, load and generation.")],
) -> None:
    """Run a DuckDB query against the store.

    Example:
        gpa query "SELECT zone, avg(price) FROM price GROUP BY 1"
    """
    con = store.connect()
    try:
        with pl.Config(tbl_rows=50, tbl_width_chars=200):
            typer.echo(str(con.sql(sql).pl()))
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover
    app()
