"""The ``gpa`` command line. Exit codes are what the scheduled workflows read."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import sys
import uuid
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import polars as pl
import typer

from gpa import pipeline, reference, store
from gpa.schema import SchemaError, SchemaErrors
from gpa.schema import validate as validate_frame
from gpa.zones import Zone, get_zone

app = typer.Typer(
    name="gpa",
    help="German power market research: ingest, forecast and value DE-LU day-ahead prices.",
    no_args_is_help=True,
    add_completion=False,
)

# Polars prints box-drawing characters that a redirected Windows console rejects.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ZoneOption = Annotated[str, typer.Option("--zone", "-z", help="Market zone.")]
ZonesOption = Annotated[
    list[str] | None, typer.Option("--zone", "-z", help="Zone code; repeat for several.")
]
DatasetOption = Annotated[
    list[str] | None,
    typer.Option("--dataset", "-d", help="price, load, generation or fundamentals. Default: all."),
]
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Fetch but write nothing.")]
VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Log adapter detail.")]
LedgerOption = Annotated[
    Path | None, typer.Option(help="Issue ledger root. Default: data/forecast_issues.")
]


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # HTTPX logs carry provider query strings; keep them out of normal output.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _print(frame: pl.DataFrame, *, width: int, precision: int | None = None) -> None:
    with pl.Config(tbl_rows=-1, tbl_width_chars=width, float_precision=precision):
        typer.echo(str(frame))


def _report(results: list[pipeline.IngestResult]) -> None:
    for result in results:
        typer.echo(str(result))
    counts = pipeline.summarise(results)
    typer.echo(
        f"\nwritten {counts['written']} | empty {counts['empty']} | failed {counts['failed']}"
    )
    if counts["failed"]:
        raise typer.Exit(1)


def _tomorrow(zone: Zone) -> dt.date:
    from gpa.forecast import ledger

    return ledger.now_utc().astimezone(ZoneInfo(zone.timezone)).date() + dt.timedelta(days=1)


@app.command()
def ingest(
    zone: ZonesOption = None,
    dataset: DatasetOption = None,
    days: Annotated[
        int, typer.Option(min=0, help="Days refetched behind the stored checkpoint.")
    ] = 7,
    dry_run: DryRunOption = False,
    verbose: VerboseOption = False,
) -> None:
    """Catch up from each series' stored checkpoint, refetching a revision overlap."""
    _configure_logging(verbose)
    _report(pipeline.ingest(zone, dataset, lookback_days=days, dry_run=dry_run))


@app.command()
def backfill(
    zone: ZonesOption = None,
    dataset: DatasetOption = None,
    years: Annotated[float, typer.Option(help="Years of history to load.")] = 2.0,
    chunk_days: Annotated[int, typer.Option(help="Days fetched per request.")] = 60,
    dry_run: DryRunOption = False,
    verbose: VerboseOption = False,
) -> None:
    """Load a long history in chunks; an interrupted run keeps what already landed."""
    _configure_logging(verbose)
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=max(1, round(years * 365)))
    typer.echo(f"Backfilling {start:%Y-%m-%d} to {end:%Y-%m-%d} in {chunk_days}-day chunks.")
    _report(
        pipeline.ingest(zone, dataset, start=start, end=end, chunk_days=chunk_days, dry_run=dry_run)
    )


@app.command()
def capacity(zone: ZoneOption = "DE-LU", verbose: VerboseOption = False) -> None:
    """Replace the installed-capacity reference series, yearly and monthly."""
    _configure_logging(verbose)
    from gpa.sources.energy_charts import EnergyChartsSource

    source, market = EnergyChartsSource(), get_zone(zone)
    frame = pl.concat(
        [source.fetch_installed_power(market, time_step=step) for step in ("yearly", "monthly")],
        how="vertical_relaxed",
    )
    if frame.is_empty():
        typer.secho("No capacity data returned.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    path = reference.write("capacity", frame)
    typer.secho(
        f"Wrote {frame.height} rows ({frame['technology'].n_unique()} technologies) to {path}.",
        fg=typer.colors.GREEN,
    )


@app.command("probe-fundamentals")
def probe_fundamentals(
    zone: ZoneOption = "DE-LU",
    output: Annotated[Path, typer.Option(help="CSV log the check is appended to.")] = Path(
        "data/probes/fundamentals_publication.csv"
    ),
    verbose: VerboseOption = False,
) -> None:
    """Log how much of tomorrow's day-ahead forecasts the provider already serves."""
    _configure_logging(verbose)
    from gpa import probe
    from gpa.sources import get_source

    market = get_zone(zone)
    source = get_source(market.sources["fundamentals"])
    frame = probe.check(market, now=pipeline.now_utc(), fetch=source.fetch)
    probe.append(frame, output)
    row = frame.row(0, named=True)
    covered = ", ".join(
        f"{r['series']} {r['hours_covered']}/{r['hours_expected']}"
        for r in frame.iter_rows(named=True)
    )
    margin = row["minutes_before_gate"]
    relative = f"{margin} min before" if margin >= 0 else f"{-margin} min after"
    typer.echo(
        f"{market.code} {row['delivery_date']} at {row['checked_at_utc']} "
        f"({relative} the gate): {covered} -> {output}"
    )


@app.command()
def validate(verbose: VerboseOption = False) -> None:
    """Re-check every stored partition against its schema contract."""
    _configure_logging(verbose)
    checked = failures = 0
    for dataset in store.DATASETS:
        for path in sorted(store.dataset_dir(dataset).glob("zone=*/*.parquet")):
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
    elif failures:
        typer.secho(f"{failures} of {checked} partitions failed.", fg=typer.colors.RED)
        raise typer.Exit(1)
    else:
        typer.secho(f"All {checked} partitions valid.", fg=typer.colors.GREEN)


@app.command()
def stats() -> None:
    """Rows, coverage and age per dataset and zone."""
    frame = store.coverage()
    if frame.is_empty():
        typer.secho("Store is empty. Run 'gpa backfill' first.", fg=typer.colors.YELLOW)
        return
    age = (pl.lit(dt.datetime.now(dt.UTC)) - pl.col("last_ts_utc")).dt.total_minutes() / 60.0
    _print(
        frame.select(
            "dataset",
            "zone",
            "rows",
            "partitions",
            (pl.col("bytes") / 1024).round(0).cast(pl.Int64).alias("kb"),
            "first_ts_utc",
            "last_ts_utc",
            age.round(1).alias("age_hours"),
        ),
        width=200,
    )
    typer.echo(f"\nTotal rows: {frame['rows'].sum():,}")
    typer.echo(f"Total size: {frame['bytes'].sum() / 1024 / 1024:.1f} MB")


@app.command()
def freshness(
    strict: Annotated[
        bool, typer.Option("--strict/--no-strict", help="Exit non-zero when a series is stale.")
    ] = True,
) -> None:
    """Report how stale each series is, so a silent stall becomes a visible failure."""
    from gpa import freshness as freshness_module

    reports = freshness_module.check()
    overdue = [r for r in reports if r.stale or r.missing]
    for report in reports:
        typer.secho(str(report), fg=typer.colors.RED if report in overdue else None)
    typer.echo("")
    if not overdue:
        typer.secho(
            f"All {len(reports)} series are within their freshness rule.", fg=typer.colors.GREEN
        )
        return
    typer.secho(f"{len(overdue)} series are stale:", fg=typer.colors.RED)
    for report in overdue:
        typer.echo(f"  {report.zone} {report.dataset}: {report.rule.reason}")
    if strict:
        raise typer.Exit(1)


@app.command()
def audit() -> None:
    """Check interval integrity and report missing time per series."""
    from gpa import quality

    result = quality.report()
    _print(result, width=160)
    if result.filter(pl.col("invalid") > 0).height:
        raise typer.Exit(1)


@app.command()
def export(
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Destination directory.")
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Compare with the committed tables instead of writing.")
    ] = False,
    verbose: VerboseOption = False,
) -> None:
    """Build the small tables the static site reads."""
    _configure_logging(verbose)
    from gpa.export import check_exports, export_all, site_root

    destination = output or site_root()
    if check:
        if differences := check_exports(destination):
            typer.echo("Outdated exports: " + ", ".join(differences))
            raise typer.Exit(1)
        typer.echo("Exported tables match the committed observations.")
        return
    written = export_all(destination)
    for name, rows in sorted(written.items()):
        typer.echo(f"{name:26s} {rows:>10,d} rows")
    size = sum(p.stat().st_size for p in destination.glob("*") if p.is_file())
    typer.secho(
        f"\nWrote {len(written)} files to {destination} ({size / 1024:.0f} KB).",
        fg=typer.colors.GREEN,
    )


@app.command()
def backtest(
    zone: ZoneOption = "DE-LU",
    scope: Annotated[
        str, typer.Option(help="overall, block, regime, year, hour or all.")
    ] = "overall",
    include_fundamentals: Annotated[
        bool, typer.Option(help="Add day-ahead load/wind/solar forecasts: the labelled ablation.")
    ] = False,
    save_snapshot: Annotated[
        bool, typer.Option("--save-snapshot", help="Freeze this run under data/experiments/.")
    ] = False,
    verbose: VerboseOption = False,
) -> None:
    """Walk-forward backtest against the naive baselines, on hours every model forecast."""
    _configure_logging(verbose)
    from gpa.forecast import backtest as harness

    scopes = ["overall", "block", "regime", "year", "hour"]
    if scope not in [*scopes, "all"]:
        raise typer.BadParameter(f"scope must be one of {', '.join(scopes)} or all")
    result = harness.run(zone, include_fundamentals=include_fundamentals)
    if save_snapshot:
        from gpa.forecast import snapshot

        try:
            typer.secho(f"Snapshot saved: {snapshot.save(result)}", fg=typer.colors.GREEN)
        except FileExistsError as exc:
            typer.secho(str(exc), fg=typer.colors.YELLOW)

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
        f"reference {harness.REFERENCE_MODEL}\n"
    )
    shown = result.scores.filter(pl.col("scope").is_in(scopes if scope == "all" else [scope]))
    columns = ["scope", "bucket", "model", "n", "mae", "rmse", "bias", "mean_pinball"]
    _print(
        shown.select(*columns, "skill_pct", "skill_vs_best_baseline_pct"), width=220, precision=2
    )

    # Always show where the fitted models are weakest: an aggregate alone can hide a loss.
    fitted = result.scores.filter(~pl.col("model").is_in(list(result.baselines))).sort(
        "skill_vs_best_baseline_pct"
    )
    losing = fitted.filter(pl.col("skill_vs_best_baseline_pct") <= 0.0).height
    typer.secho(
        f"\nFitted models lost to a naive baseline in {losing} model/bucket pairs:"
        if losing
        else "\nFitted models beat every baseline in every bucket. Weakest margins:",
        fg=typer.colors.YELLOW if losing else typer.colors.GREEN,
    )
    for row in fitted.head(3).iter_rows(named=True):
        typer.echo(
            f"  {row['model']} {row['scope']} {row['bucket']}: "
            f"{row['skill_vs_best_baseline_pct']:+.1f}% against the best baseline, "
            f"MAE {row['mae']:.2f}"
        )


@app.command("fundamentals-ablation")
def fundamentals_ablation(zone: ZoneOption = "DE-LU", verbose: VerboseOption = False) -> None:
    """Score the published protocol with and without fundamentals; never a new release."""
    _configure_logging(verbose)
    from gpa.forecast import backtest as harness

    market = get_zone(zone)
    rows = []
    for include in (False, True):
        typer.echo(f"Running with include_fundamentals={include}...")
        result = harness.run(market, include_fundamentals=include)
        rows.append(
            result.scores.filter(
                (pl.col("scope") == "overall") & pl.col("model").is_in(["ridge", "lightgbm"])
            )
            .select("model", "n", "mae", "rmse", "skill_vs_best_baseline_pct")
            .with_columns(
                zone=pl.lit(market.code),
                include_fundamentals=pl.lit(include),
                test_start=pl.lit(str(result.test_start)),
                test_end=pl.lit(str(result.test_end)),
            )
        )
    combined = pl.concat(rows, how="vertical_relaxed")
    destination = reference.write("fundamentals_ablation", combined)
    _print(combined, width=160, precision=2)
    typer.secho(f"Wrote {combined.height} rows to {destination}.", fg=typer.colors.GREEN)


@app.command()
def issue(
    zone: ZoneOption = "DE-LU",
    delivery_date: Annotated[
        str | None, typer.Option(help="Delivery YYYY-MM-DD. Default: tomorrow in market time.")
    ] = None,
    model: Annotated[
        str,
        typer.Option(help="ridge, ridge_da (adds day-ahead fundamentals), lightgbm or a naive."),
    ] = "ridge",
    allow_late: Annotated[
        bool, typer.Option(help="Allow a late issue for diagnostics; labelled in the ledger.")
    ] = False,
    output: LedgerOption = None,
    attempt_id: Annotated[
        str | None,
        typer.Option(help="Reuse a workflow start event; otherwise create a manual attempt."),
    ] = None,
) -> None:
    """Issue one delivery day before the gate and keep its immutable evidence.

    The model fixes the information set: ``ridge_da`` always reads the operators'
    day-ahead forecasts and abstains on the record when they are not yet published.
    """
    from gpa.forecast import attempts, fundamentals, ledger, provenance
    from gpa.forecast.panel import build_panel

    market = get_zone(zone)
    root = output or ledger.DEFAULT_ROOT
    identifier = attempt_id or uuid.uuid4().hex
    existing = None
    if attempt_id is not None:
        with contextlib.suppress(FileNotFoundError):
            existing = attempts.read(root, identifier)
    try:
        if delivery_date:
            target_date = dt.date.fromisoformat(delivery_date)
        elif existing:
            target_date = dt.date.fromisoformat(existing["delivery_date"])
        else:
            target_date = _tomorrow(market)
    except ValueError as exc:
        raise typer.BadParameter("delivery date must be YYYY-MM-DD") from exc

    try:
        forecaster = provenance.default_model(model)
        event = attempts.start(
            root,
            identifier,
            zone=market.code,
            model=model,
            delivery_date=target_date,
            origin=existing["origin"] if existing else "manual",
        )
        if event["status"] != "started":
            raise ValueError("attempt already completed; use a new attempt ID")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    try:
        # A backstop slot that finds the day already issued closes against that
        # issue, so a red run keeps meaning a delivery day with no forecast.
        if not allow_late:
            prior = ledger.canonical_issue(market.code, model, target_date, root=root)
            if not prior.is_empty():
                prior_id = prior["issue_id"][0]
                attempts.finish(root, identifier, status="already_issued", issue_id=prior_id)
                typer.secho(
                    f"{market.code} {target_date}: already issued as {prior_id[:12]} at "
                    f"{prior['issued_at'][0]:%Y-%m-%d %H:%M}Z; attempt recorded, nothing reissued.",
                    fg=typer.colors.GREEN,
                )
                return
        sources: dict[str, pl.DataFrame] = {}
        observations: dict[str, dt.datetime] = {}
        for dataset in store.DATASETS:
            if market.has(dataset):
                sources[dataset] = store.read(dataset, market.code)
                observations[dataset] = ledger.now_utc()
        snapshots = (
            fundamentals.from_store_prospective(
                market, delivery_date=target_date, retrieved_at=observations["fundamentals"]
            )
            if provenance.uses_fundamentals(model)
            else None
        )
        as_of = ledger.now_utc()
        prepared = build_panel(
            sources["price"],
            market,
            load=sources.get("load"),
            generation=sources.get("generation"),
            fundamentals=snapshots,
            delivery_date=target_date,
        )
        frame = ledger.record_issue(
            prepared,
            forecaster,
            target_date,
            root=root,
            allow_late=allow_late,
            input_as_of=as_of,
            min_train_rows=provenance.MIN_TRAIN_ROWS,
            policy_id=provenance.policy_id(model),
            source_frames=sources,
            observed_at=observations,
        )
    except Exception as exc:
        status = "late" if isinstance(exc, ledger.LateIssueError) else "failed"
        attempts.finish(
            root, identifier, status=status, error_type=type(exc).__name__, if_open=True
        )
        typer.secho(
            f"Forecast attempt {identifier} {status}: {type(exc).__name__}. "
            f"Evidence retained under {root}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from exc

    available = frame.height - frame["forecast"].null_count()
    if not frame["eligible"].all():
        status = "late"
    elif available == 0:
        status = "abstained"
    else:
        status = "issued" if available == frame.height else "partial"
    attempts.finish(root, identifier, status=status, issue_id=frame["issue_id"][0])
    typer.secho(
        f"{market.code} {target_date}: {status}, {available} forecasts, "
        f"{frame.height - available} abstentions; immutable evidence -> "
        f"{root / 'issues' / frame['issue_id'][0]}",
        fg=typer.colors.GREEN if status == "issued" else typer.colors.YELLOW,
    )
    if status != "issued":
        raise typer.Exit(1)


@app.command("forecast-attempt")
def forecast_attempt(
    action: Annotated[str, typer.Argument(help="start, finish or report.")],
    attempt_id: Annotated[str | None, typer.Option(help="Stable ID for this attempt.")] = None,
    zone: Annotated[str, typer.Option(help="Market zone.")] = "DE-LU",
    model: Annotated[str, typer.Option(help="Declared forecast model.")] = "ridge",
    delivery_date: Annotated[
        str | None, typer.Option(help="Delivery YYYY-MM-DD; start defaults to tomorrow.")
    ] = None,
    origin: Annotated[str, typer.Option(help="manual, schedule or workflow_dispatch.")] = "manual",
    status: Annotated[str, typer.Option(help="Completion outcome.")] = "failed",
    if_open: Annotated[bool, typer.Option(help="Do not overwrite an existing completion.")] = False,
    start_date: Annotated[
        str | None, typer.Option(help="First expected delivery day in a report.")
    ] = None,
    end_date: Annotated[
        str | None, typer.Option(help="Last expected delivery day in a report.")
    ] = None,
    output: LedgerOption = None,
) -> None:
    """Record attempts before ingestion and report the daily denominator."""
    from gpa.forecast import attempts, ledger

    root = output or ledger.DEFAULT_ROOT
    market = get_zone(zone)
    try:
        if action == "report":
            if start_date is None or end_date is None:
                raise ValueError("report requires --start-date and --end-date")
            report = attempts.report(
                root,
                start_date=dt.date.fromisoformat(start_date),
                end_date=dt.date.fromisoformat(end_date),
                zone=zone,
                model=model,
            )
            _print(report, width=200)
            typer.echo(
                f"Eligible deliveries: {report['eligible'].sum()}/{report.height}; "
                "requested window is the denominator."
            )
            return
        if attempt_id is None:
            raise ValueError("start and finish require --attempt-id")
        if action == "start":
            day = dt.date.fromisoformat(delivery_date) if delivery_date else _tomorrow(market)
            event = attempts.start(
                root, attempt_id, zone=zone, model=model, delivery_date=day, origin=origin
            )
        elif action == "finish":
            event = attempts.finish(root, attempt_id, status=status, if_open=if_open)
        else:
            raise ValueError("action must be start, finish or report")
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Attempt {event['attempt_id']}: {event['status']} ({event['delivery_date']}).")


@app.command()
def reconcile(zone: ZoneOption = "DE-LU", output: LedgerOption = None) -> None:
    """Attach observed prices to every stored issue; idempotent, identity untouched."""
    from gpa.forecast import ledger

    market, root = get_zone(zone), output or ledger.DEFAULT_ROOT
    issues = ledger.read(root=root, zone=market.code)
    if issues.is_empty():
        typer.secho(f"No issue records for {market.code} under {root}.", fg=typer.colors.YELLOW)
        return
    reconciled = ledger.reconcile(issues, store.read("price", market.code), market)
    month = pl.col("delivery_date").dt.strftime("%Y-%m").alias("_month")
    for part in reconciled.with_columns(month).partition_by("_month", include_key=False):
        ledger.append(part, root=root)
    counts = dict(reconciled.group_by("status").len().iter_rows())
    typer.echo(
        f"Reconciled {market.code}: {reconciled.height} rows | "
        + " | ".join(f"{status}={counts[status]}" for status in sorted(counts))
    )


@app.command("battery")
def battery_value(
    zone: ZoneOption = "DE-LU",
    ledger_root: LedgerOption = None,
    output: Annotated[
        Path | None, typer.Option(help="Directory for dispatch, summary and coverage Parquet.")
    ] = None,
) -> None:
    """Dispatch a battery on the canonical, settled prospective forecasts."""
    from gpa.battery import backtest_predictions
    from gpa.forecast import ledger

    market, root = get_zone(zone), ledger_root or ledger.DEFAULT_ROOT
    issues = ledger.read(root=root, zone=market.code)
    if issues.is_empty():
        typer.secho(f"No issue records for {market.code} under {root}.", fg=typer.colors.YELLOW)
        return
    reconciled = ledger.reconcile(issues, store.read("price", market.code), market)
    scored = (
        ledger.canonical(reconciled, root=root)
        .filter(pl.col("status") == "scored")
        .select(
            "model", pl.col("delivery_date").alias("local_date"), "local_hour", "forecast", "actual"
        )
    )
    if scored.is_empty():
        typer.secho("No complete scored delivery rows yet.", fg=typer.colors.YELLOW)
        return
    result = backtest_predictions(
        scored,
        model_names=tuple(sorted(scored["model"].unique().to_list())),
        timezone=market.timezone,
    )
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        for name in ("dispatch", "summary", "coverage"):
            getattr(result, name).write_parquet(output / f"{market.code}_{name}.parquet")
        typer.echo(f"Wrote battery evaluation to {output}.")
    _print(result.summary, width=180, precision=2)


@app.command("battery-study")
def battery_study(
    predictions: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="Predictions Parquet. Default: the frozen release the site is built from.",
        ),
    ] = None,
    output: Annotated[
        Path, typer.Option(help="Local study root; completed runs are never overwritten.")
    ] = Path(".gpa/battery-studies"),
    variable_cost: Annotated[
        float, typer.Option(min=0, help="Assumed EUR per absolute grid MWh.")
    ] = 0.0,
    degradation_cost: Annotated[
        float, typer.Option(min=0, help="Assumed EUR per absolute grid MWh.")
    ] = 0.0,
    block_days: Annotated[int, typer.Option(min=1, help="Bootstrap calendar block length.")] = 7,
    resamples: Annotated[int, typer.Option(min=100, help="Bootstrap resamples.")] = 2000,
    seed: Annotated[int, typer.Option(min=0, help="Bootstrap seed.")] = 20260914,
) -> None:
    """Compare the five forecasts on 1/2/4 MWh batteries over shared settled days."""
    from gpa.battery_study import evaluate, save_study
    from gpa.export import release_predictions

    frame = pl.read_parquet(predictions) if predictions else release_predictions()
    try:
        result = evaluate(
            frame,
            spec_kwargs={
                "variable_cost_eur_mwh": variable_cost,
                "degradation_cost_eur_mwh": degradation_cost,
            },
            block_days=block_days,
            resamples=resamples,
            seed=seed,
        )
        destination = save_study(result, frame, output)
    except (ValueError, FileExistsError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Research study saved to {destination}.")
    typer.echo(
        "Retrospective common-sample margins; costs are assumptions, not investment returns."
    )
    _print(result.coverage, width=180, precision=2)
    risk_columns = ["strategy", "energy_mwh", "days", "profit_eur_mw", "best_naive"]
    _print(
        result.risk.select(*risk_columns, "incremental_vs_best_naive_eur_mw"),
        width=180,
        precision=2,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
