"""Store tests: partitioning, upsert semantics and schema enforcement.

Upsert behaviour is the important one. Power data is revised after publication,
so re-ingesting a window has to replace what is already stored rather than
append a second copy of it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from gpa import store
from gpa.schema import SchemaErrors, empty_frame, validate


@pytest.fixture(autouse=True)
def temporary_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the store to a temporary tree so tests never touch the repo."""
    monkeypatch.setenv("GPA_DATA_ROOT", str(tmp_path))
    return tmp_path


def price_rows(
    values: list[float],
    *,
    zone: str = "DE-LU",
    start: dt.datetime = dt.datetime(2026, 6, 15, tzinfo=dt.UTC),
) -> pl.DataFrame:
    stamps = [start + dt.timedelta(hours=i) for i in range(len(values))]
    return pl.DataFrame(
        {
            "zone": [zone] * len(values),
            "ts_utc": stamps,
            "resolution_min": [60] * len(values),
            "price": values,
            "currency": ["EUR"] * len(values),
            "source": ["test"] * len(values),
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "price": pl.Float64,
            "currency": pl.String,
            "source": pl.String,
        },
    )


# --- Round trip ------------------------------------------------------------


def test_write_then_read_round_trips() -> None:
    frame = price_rows([10.0, 20.0, 30.0])
    store.write(frame, "price")

    back = store.read("price")
    assert back.height == 3
    assert back["price"].to_list() == [10.0, 20.0, 30.0]


def test_partitions_are_keyed_by_zone_and_month() -> None:
    store.write(price_rows([1.0], start=dt.datetime(2026, 6, 30, 23, tzinfo=dt.UTC)), "price")
    store.write(price_rows([2.0], start=dt.datetime(2026, 7, 1, 0, tzinfo=dt.UTC)), "price")

    assert store.available_months("price", "DE-LU") == ["2026-06", "2026-07"]
    assert store.partition_path("price", "DE-LU", "2026-06").exists()


def test_reading_an_empty_store_returns_the_right_schema() -> None:
    frame = store.read("price")
    assert frame.is_empty()
    assert frame.columns == empty_frame("price").columns


# --- Upsert ----------------------------------------------------------------


def test_rewriting_the_same_window_replaces_rather_than_duplicates() -> None:
    """Providers revise. A second run over the same window must converge."""
    store.write(price_rows([10.0, 20.0, 30.0]), "price")
    store.write(price_rows([11.0, 21.0, 31.0]), "price")

    back = store.read("price")
    assert back.height == 3
    assert back["price"].to_list() == [11.0, 21.0, 31.0]


def test_overlapping_windows_merge_without_gaps() -> None:
    start = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    store.write(price_rows([1.0, 2.0, 3.0], start=start), "price")
    store.write(price_rows([3.5, 4.0, 5.0], start=start + dt.timedelta(hours=2)), "price")

    back = store.read("price")
    assert back.height == 5
    assert back["price"].to_list() == [1.0, 2.0, 3.5, 4.0, 5.0]


def test_generation_upsert_keys_on_fuel_as_well() -> None:
    moment = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    frame = pl.DataFrame(
        {
            "zone": ["DE-LU", "DE-LU"],
            "ts_utc": [moment, moment],
            "resolution_min": [60, 60],
            "fuel": ["wind", "solar"],
            "gen_mw": [100.0, 200.0],
            "source": ["test", "test"],
        },
        schema={
            "zone": pl.String,
            "ts_utc": pl.Datetime("us", "UTC"),
            "resolution_min": pl.Int16,
            "fuel": pl.String,
            "gen_mw": pl.Float64,
            "source": pl.String,
        },
    )
    store.write(frame, "generation")
    store.write(frame, "generation")

    back = store.read("generation")
    assert back.height == 2
    assert sorted(back["fuel"].to_list()) == ["solar", "wind"]


# --- Filtering -------------------------------------------------------------


def test_read_filters_by_zone() -> None:
    store.write(price_rows([1.0], zone="DE-LU"), "price")
    store.write(price_rows([2.0], zone="FR"), "price")

    assert store.read("price", "DE-LU").height == 1
    assert store.read("price", ["DE-LU", "FR"]).height == 2


def test_read_window_is_half_open() -> None:
    start = dt.datetime(2026, 6, 15, tzinfo=dt.UTC)
    store.write(price_rows([1.0, 2.0, 3.0], start=start), "price")

    window = store.read("price", start=start, end=start + dt.timedelta(hours=2))
    assert window["price"].to_list() == [1.0, 2.0]


def test_read_rejects_naive_bounds() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        store.read("price", start=dt.datetime(2026, 6, 15))


# --- Validation ------------------------------------------------------------


def test_write_rejects_a_frame_that_breaks_the_contract() -> None:
    bad = price_rows([10.0]).with_columns(pl.lit("EURO").alias("currency"))
    with pytest.raises(SchemaErrors):
        store.write(bad, "price")


def test_write_rejects_an_unknown_dataset() -> None:
    with pytest.raises(KeyError, match="valid datasets are"):
        store.write(price_rows([1.0]), "prices")


def test_negative_prices_pass_validation() -> None:
    """The contract must accept the values the market actually produces."""
    validate(price_rows([-42.0, 0.0, 500.0]), "price")


def test_writing_an_empty_frame_is_a_no_op() -> None:
    assert store.write(empty_frame("price"), "price") == []


# --- Reporting -------------------------------------------------------------


def test_coverage_reports_range_and_size() -> None:
    store.write(price_rows([1.0, 2.0, 3.0]), "price")
    coverage = store.coverage()

    row = coverage.row(0, named=True)
    assert row["dataset"] == "price"
    assert row["zone"] == "DE-LU"
    assert row["rows"] == 3
    assert row["bytes"] > 0


def test_duckdb_view_exposes_the_partition_key_as_a_column() -> None:
    store.write(price_rows([10.0, 20.0], zone="DE-LU"), "price")
    store.write(price_rows([30.0], zone="FR"), "price")

    con = store.connect()
    try:
        result = con.sql("SELECT zone, count(*) AS n FROM price GROUP BY 1 ORDER BY 1").fetchall()
    finally:
        con.close()

    assert result == [("DE-LU", 2), ("FR", 1)]


def test_connect_skips_datasets_with_no_files() -> None:
    """A view over a missing directory would error on first use."""
    store.write(price_rows([1.0]), "price")
    con = store.connect()
    try:
        views = {row[0] for row in con.sql("SHOW TABLES").fetchall()}
    finally:
        con.close()
    assert "price" in views
    assert "generation" not in views
