"""Command line interface for the data, forecast and portfolio workflows.

The main portfolio commands are:

``gpa zones``      inspect the registry
``gpa ingest``     fetch a recent window, for the daily scheduled run
``gpa backfill``   fetch a long history, for the one-off initial load
``gpa capacity``   fetch installed renewable/storage capacity, a period series
``gpa validate``   re-check everything on disk against the schema contracts
``gpa stats``      report what the store holds
``gpa backtest``   walk-forward price forecast, scored against naive baselines
``gpa fundamentals-ablation``  compare with/without fundamentals, a labelled diagnostic
``gpa issue``      issue a feature-only forecast and retain its evidence
``gpa reconcile``  attach observed prices to issued forecasts
``gpa battery``    evaluate reconciled forecasts through the battery dispatch
``gpa battery-study`` evaluate a local retrospective prediction snapshot
``gpa probe-fundamentals`` log how much of tomorrow's day-ahead forecasts is published

Exit codes matter because a scheduled workflow reads them: 0 when every target
succeeded, 1 when any target failed.
"""

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

from gpa import __version__, pipeline, store
from gpa.schema import SchemaError, SchemaErrors
from gpa.schema import validate as validate_frame
from gpa.zones import ZONES, get_zone

DEFAULT_TRACKING_DIR = Path(".gpa/mlflow")

app = typer.Typer(
    name="gpa",
    help="German power market research: ingest, forecast and value DE-LU day-ahead prices.",
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
    typer.Option("--dataset", "-d", help="price, load, generation or fundamentals. Default: all."),
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
    typer.echo(f"written {counts['written']} | empty {counts['empty']} | failed {counts['failed']}")
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


@app.command()
def ingest(
    zone: ZoneOption = None,
    dataset: DatasetOption = None,
    days: Annotated[
        int,
        typer.Option(
            min=0,
            help="Revision overlap: days refetched behind the stored checkpoint, or behind "
            "now when nothing is stored yet.",
        ),
    ] = 7,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Fetch but write nothing.")] = False,
    verbose: VerboseOption = False,
) -> None:
    """Catch up from each target's stored checkpoint and upsert into the store.

    Each zone and dataset resumes from the latest instant it already holds, so
    a run after missed schedules recovers the whole gap. ``--days`` of overlap
    are refetched because providers publish on a lag and revise afterwards, and
    the store upserts. Day-ahead prices are requested past now, because the
    next delivery day is published before it starts.
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
def capacity(
    zone: Annotated[
        str, typer.Option("--zone", "-z", help="Zone to fetch installed capacity for.")
    ] = "DE-LU",
    verbose: VerboseOption = False,
) -> None:
    """Fetch installed renewable/storage capacity by technology, yearly and monthly.

    A period series, not a market interval series: stored separately under
    data/reference/capacity/, outside the price/load/generation/fundamentals
    store. Re-running replaces the file with the provider's full current
    series rather than upserting, since a "planned" figure can legitimately
    disappear when a policy target is revised.
    """
    _configure_logging(verbose)
    from gpa import capacity as capacity_module
    from gpa.sources.energy_charts import EnergyChartsSource

    market = get_zone(zone)
    if "energy_charts_country" not in market.source_keys:
        typer.secho(f"{zone} has no Energy-Charts country code.", fg=typer.colors.RED)
        raise typer.Exit(1)

    source = EnergyChartsSource()
    yearly = source.fetch_installed_power(market, time_step="yearly")
    monthly = source.fetch_installed_power(market, time_step="monthly")
    combined = pl.concat([yearly, monthly], how="vertical_relaxed")
    if combined.is_empty():
        typer.secho("No capacity data returned.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    path = capacity_module.write(combined)
    technologies = combined["technology"].n_unique()
    typer.secho(
        f"Wrote {combined.height} rows ({technologies} technologies) to {path}.",
        fg=typer.colors.GREEN,
    )


@app.command("probe-fundamentals")
def probe_fundamentals(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to probe.")] = "DE-LU",
    output: Annotated[Path, typer.Option(help="CSV log the check is appended to.")] = Path(
        "data/probes/fundamentals_publication.csv"
    ),
    verbose: VerboseOption = False,
) -> None:
    """Record how much of tomorrow's day-ahead forecasts the provider serves now.

    Appends one row per series with the check time and its distance from the
    day-ahead gate. Run repeatedly across a day, the log brackets when the
    delivery day's forecasts are first published -- which decides whether the
    prospective ``ridge_da`` arm can ever issue before the gate.
    """
    _configure_logging(verbose)
    from gpa import probe
    from gpa.sources import get_source

    market = get_zone(zone)
    if not market.has("fundamentals"):
        typer.secho(f"{market.code} does not collect fundamentals.", fg=typer.colors.RED)
        raise typer.Exit(1)
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
    """Report how stale each series is against the freshness rule.

    The scheduled workflow runs this so that a silent stall becomes a visible
    failure. A run that fetched nothing still exits zero if no adapter raised,
    which is exactly the case this catches.
    """
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
    include_fundamentals: Annotated[
        bool,
        typer.Option(
            help="Add day-ahead load/wind/solar forecast features, where the zone collects them. "
            "An ablation input, not part of the published feature set."
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
        include_fundamentals=include_fundamentals,
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


@app.command("fundamentals-ablation")
def fundamentals_ablation(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to compare.")] = "DE-LU",
    verbose: VerboseOption = False,
) -> None:
    """Compare the published information set against one with fundamentals added.

    Runs the identical walk-forward protocol twice, at every default
    (min_train_days, validation_days, alpha selection, window, LightGBM
    tuning), once without and once with day-ahead load/wind/solar forecast
    features -- the same protocol the published release uses, so the only
    difference between the two runs is that one flag. Writes only the
    overall-scope scorecard to data/reference/fundamentals_ablation/, never
    a full snapshot: this never calls gpa.forecast.snapshot.save() and never
    touches data/experiments/current.json. Adopting these features as a new
    frozen release is a separate decision this command does not make.
    """
    _configure_logging(verbose)
    from gpa import fundamentals_ablation as ablation_module
    from gpa.forecast import backtest as harness

    market = get_zone(zone)
    rows: list[pl.DataFrame] = []
    for include_fundamentals in (False, True):
        typer.echo(f"Running with include_fundamentals={include_fundamentals}...")
        result = harness.run(market, include_fundamentals=include_fundamentals)
        overall = result.scores.filter(
            (pl.col("scope") == "overall") & pl.col("model").is_in(["ridge", "lightgbm"])
        )
        rows.append(
            overall.select("model", "n", "mae", "rmse", "skill_vs_best_baseline_pct").with_columns(
                pl.lit(market.code).alias("zone"),
                pl.lit(include_fundamentals).alias("include_fundamentals"),
                pl.lit(str(result.test_start)).alias("test_start"),
                pl.lit(str(result.test_end)).alias("test_end"),
            )
        )
    combined = pl.concat(rows, how="vertical_relaxed")
    destination = ablation_module.write(combined)
    with pl.Config(tbl_rows=-1, tbl_width_chars=160, float_precision=2):
        typer.echo(str(combined))
    typer.secho(f"Wrote {combined.height} rows to {destination}.", fg=typer.colors.GREEN)


@app.command()
def issue(
    zone: Annotated[str, typer.Option("--zone", "-z", help="Zone to forecast.")] = "DE-LU",
    delivery_date: Annotated[
        str | None,
        typer.Option(help="Delivery date YYYY-MM-DD. Default: tomorrow in market time."),
    ] = None,
    model: Annotated[
        str,
        typer.Option(
            help=(
                "ridge (published set), ridge_da (adds day-ahead fundamentals), "
                "lightgbm or an existing naive comparator."
            )
        ),
    ] = "ridge",
    allow_late: Annotated[
        bool, typer.Option(help="Allow a late issue for diagnostics; labels it in the ledger.")
    ] = False,
    output: Annotated[
        Path | None, typer.Option(help="Issue ledger root. Default: data/forecast_issues.")
    ] = None,
    attempt_id: Annotated[
        str | None,
        typer.Option(help="Reuse a workflow start event; otherwise create a manual attempt."),
    ] = None,
) -> None:
    """Issue tomorrow's feature-only forecast and persist its evidence.

    Every delivery hour is retained, including explicit abstentions when a
    required feature or enough training history is unavailable.

    The chosen model fixes the information set: ``ridge`` issues from the
    published set, ``ridge_da`` from that set plus the day-ahead operator
    forecasts. Running both daily is what makes the prospective ledger an
    experiment rather than a single unlabelled record.
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
    if delivery_date is None:
        local_today = ledger.now_utc().astimezone(ZoneInfo(market.timezone)).date()
        target_date = (
            dt.date.fromisoformat(existing["delivery_date"])
            if existing
            else local_today + dt.timedelta(days=1)
        )
    else:
        try:
            target_date = dt.date.fromisoformat(delivery_date)
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
        # The backstop slot exists for days the first slot missed. On a day it
        # did not miss, the backstop usually arrives after the gate -- GitHub
        # delivered both slots about five and a half hours late on 23 September
        # 2026 -- and refusing to backdate is then correct but beside the point:
        # the day is on record. It is closed against that issue instead, so a
        # red run keeps meaning a delivery day with no forecast.
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
        for dataset in ("price", "load", "generation", "fundamentals"):
            if market.has(dataset):
                sources[dataset] = store.read(dataset, market.code)
                observations[dataset] = ledger.now_utc()
        # The information set follows the model identity, never the weather of
        # the moment. An earlier version attached fundamentals whenever the
        # provider happened to have published the delivery day, which made one
        # model name mean two different experiments on different days and left
        # the ledger unable to score either: `canonical` keys on
        # (zone, model, delivery_date), so the two arms collided. `ridge` is the
        # published information set, always. `ridge_da` is that set plus the
        # day-ahead operator forecasts, always -- and on a day the provider has
        # not published the delivery day in time, it abstains and records that,
        # which is the honest outcome for a frozen information set rather than a
        # silent substitution. See provenance.uses_fundamentals.
        snapshots = None
        if provenance.uses_fundamentals(model):
            if "fundamentals" not in sources:
                raise typer.BadParameter(
                    f"{market.code} does not collect fundamentals; {model} cannot be issued"
                )
            snapshots = fundamentals.from_store_prospective(
                market,
                delivery_date=target_date,
                retrieved_at=observations["fundamentals"],
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
            f"Forecast attempt {identifier} {status}: {type(exc).__name__}. Evidence retained under {root}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from exc
    available = frame.height - frame["forecast"].null_count()
    status = (
        "late"
        if not frame["eligible"].all()
        else "abstained"
        if available == 0
        else "partial"
        if available < frame.height
        else "issued"
    )
    attempts.finish(root, identifier, status=status, issue_id=frame["issue_id"][0])
    typer.secho(
        f"{market.code} {target_date}: {status}, {available} forecasts, "
        f"{frame.height - available} abstentions; immutable evidence -> {root / 'issues' / frame['issue_id'][0]}",
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
    output: Annotated[Path | None, typer.Option(help="Ledger and attempt root.")] = None,
) -> None:
    """Track attempts before ingestion and report an explicit daily denominator."""
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
            with pl.Config(tbl_rows=-1, tbl_width_chars=200):
                typer.echo(str(report))
            typer.echo(
                f"Eligible deliveries: {report['eligible'].sum()}/{report.height}; requested window is the denominator."
            )
            return
        if attempt_id is None:
            raise ValueError("start and finish require --attempt-id")
        if action == "start":
            day = (
                dt.date.fromisoformat(delivery_date)
                if delivery_date
                else ledger.now_utc().astimezone(ZoneInfo(market.timezone)).date()
                + dt.timedelta(days=1)
            )
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
        int | None, typer.Option(help="Optional guard; must cover the full delivery day.")
    ] = None,
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
    # Keep the declared clock-hour benchmark here. Passing the first timestamp
    # of an averaged autumn hour would pretend to recover physical trades.
    scored = (
        ledger.canonical(reconciled, root=root)
        .filter(pl.col("status") == "scored")
        .select(
            "model",
            pl.col("delivery_date").alias("local_date"),
            "local_hour",
            "forecast",
            "actual",
        )
    )
    if scored.is_empty():
        typer.secho("No complete scored delivery rows yet.", fg=typer.colors.YELLOW)
        return

    result = battery_module.backtest_predictions(
        scored,
        model_names=tuple(sorted(scored["model"].unique().to_list())),
        durations_mwh=(1.0, 2.0, 4.0),
        horizon_steps=horizon_steps,
        timezone=market.timezone,
    )
    if output is not None:
        destination = Path(output)
        destination.mkdir(parents=True, exist_ok=True)
        result.dispatch.write_parquet(destination / f"{market.code}_dispatch.parquet")
        result.summary.write_parquet(destination / f"{market.code}_summary.parquet")
        result.coverage.write_parquet(destination / f"{market.code}_coverage.parquet")
        typer.echo(f"Wrote battery evaluation to {destination}.")
    with pl.Config(tbl_rows=-1, tbl_width_chars=180, float_precision=2):
        typer.echo(str(result.summary))


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
        float, typer.Option(min=0, help="Assumed EUR per absolute grid MWh; not a market estimate.")
    ] = 0.0,
    degradation_cost: Annotated[
        float, typer.Option(min=0, help="Assumed EUR per absolute grid MWh; not a market estimate.")
    ] = 0.0,
    block_days: Annotated[
        int, typer.Option(min=1, help="Calendar block length for paired bootstrap.")
    ] = 7,
    resamples: Annotated[
        int, typer.Option(min=100, help="Exploratory bootstrap resamples.")
    ] = 2000,
    seed: Annotated[int, typer.Option(min=0, help="Deterministic bootstrap seed.")] = 20260914,
) -> None:
    """Compare five forecasts and 1/2/4 MWh batteries on shared settled days.

    Reads the frozen release's predictions, rounded exactly as the site's battery
    tables read them, or a supplied Parquet; no ingestion, live issuance, model
    training or public-site export. Costs must be calibrated before asset use.
    """
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
    with pl.Config(tbl_rows=-1, tbl_width_chars=180, float_precision=2):
        typer.echo(str(result.coverage))
        typer.echo(
            str(
                result.risk.select(
                    "strategy",
                    "energy_mwh",
                    "days",
                    "profit_eur_mw",
                    "best_naive",
                    "incremental_vs_best_naive_eur_mw",
                )
            )
        )


if __name__ == "__main__":  # pragma: no cover
    app()
