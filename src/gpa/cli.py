"""Command line interface.

Five verbs cover the whole workflow:

``gpa zones``      inspect the registry
``gpa ingest``     fetch a recent window, for the daily scheduled run
``gpa backfill``   fetch a long history, for the one-off initial load
``gpa validate``   re-check everything on disk against the schema contracts
``gpa stats``      report what the store holds

Exit codes matter because a scheduled workflow reads them: 0 when every target
succeeded or was cleanly skipped, 1 when any target failed.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
from typing import Annotated

import polars as pl
import typer
from dotenv import load_dotenv

from gpa import __version__, pipeline, store
from gpa.schema import SchemaError, SchemaErrors
from gpa.schema import validate as validate_frame
from gpa.zones import ZONES

load_dotenv()

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
    # HTTPX INFO includes query strings, including EIA's api_key parameter.
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

    Manually refreshed series, currently the CCEE PLD zones, report but never
    fail the run: nobody can fix those from a cron job, and failing nightly
    would train people to ignore the failure.
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


@app.command("benchmarks")
def update_benchmarks() -> None:
    """Refresh official World Bank, EEX and ECB monthly references."""
    from gpa.benchmarks import reference_path, refresh

    frame = refresh()
    typer.secho(
        f"Wrote {frame.height} aligned months to {reference_path()}.",
        fg=typer.colors.GREEN,
    )


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
