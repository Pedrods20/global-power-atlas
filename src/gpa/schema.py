"""Schema contracts for the canonical fact tables.

Every source adapter must return one of these three shapes, validated at the
boundary before anything is written to Parquet. A source that changes upstream
fails here, loudly, with the offending column named, instead of quietly
poisoning a chart three layers downstream.

All three tables are long format, keyed on ``(zone, ts_utc)`` plus a dataset
specific discriminator. Long format costs a little storage and buys the ability
to add a fuel, a zone or a currency without a migration.

Units are fixed by contract and stated in the column description:

- ``price`` is money per MWh in ``currency``. It may be zero or negative.
  Negative prices are data, not errors, and nothing in this project filters
  them out.
- ``load_mw`` and ``gen_mw`` are average power in megawatts over the interval
  that *begins* at ``ts_utc`` and lasts ``resolution_min`` minutes. Converting
  to energy is always ``mw * resolution_min / 60``, never an assumed hour.
"""

from __future__ import annotations

from typing import Final

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaError, SchemaErrors

__all__ = [
    "FUELS",
    "FUNDAMENTALS_SCHEMA",
    "FUNDAMENTAL_SERIES",
    "GENERATION_SCHEMA",
    "LOAD_SCHEMA",
    "PRICE_SCHEMA",
    "SCHEMAS",
    "UTC_DATETIME",
    "SchemaError",
    "SchemaErrors",
    "empty_frame",
    "validate",
]

UTC_DATETIME: Final = pl.Datetime(time_unit="us", time_zone="UTC")
"""The one timestamp dtype this project stores. Microsecond, UTC-aware."""

FUELS: Final[tuple[str, ...]] = (
    "coal",
    "gas",
    "oil",
    "nuclear",
    "hydro",
    "hydro_pumped_storage",
    "wind",
    "solar",
    "biomass",
    "geothermal",
    "waste",
    "battery",
    "other",
    "imports",
)
"""Canonical fuel taxonomy every source maps into.

Pumped storage is kept apart from hydro because it is a load as often as it is
a generator, and folding it into hydro overstates renewable share. ``imports``
is a net interchange bucket, not a fuel, and is excluded from mix shares.
"""


_RESOLUTIONS: Final[tuple[int, ...]] = (1, 5, 15, 30, 60)
"""Interval lengths any supported market publishes, in minutes."""


def _zone_column() -> pa.Column:
    return pa.Column(
        pl.String,
        nullable=False,
        checks=pa.Check.str_length(min_value=2, max_value=32),
        description="Zone code from gpa.zones.ZONES; the Parquet partition key.",
    )


def _timestamp_column() -> pa.Column:
    return pa.Column(
        UTC_DATETIME,
        nullable=False,
        description="Start of the settlement interval, UTC-aware.",
    )


def _resolution_column() -> pa.Column:
    return pa.Column(
        pl.Int16,
        nullable=False,
        checks=pa.Check.isin(_RESOLUTIONS),
        description="Interval length in minutes. Never assume 60.",
    )


def _source_column() -> pa.Column:
    return pa.Column(
        pl.String,
        nullable=False,
        description="Source adapter that produced the row, for provenance.",
    )


PRICE_SCHEMA: Final = pa.DataFrameSchema(
    name="prices",
    columns={
        "zone": _zone_column(),
        "ts_utc": _timestamp_column(),
        "resolution_min": _resolution_column(),
        "price": pa.Column(
            pl.Float64,
            nullable=False,
            checks=pa.Check.in_range(-10_000.0, 100_000.0),
            description=(
                "Clearing price per MWh in `currency`. Zero and negative values "
                "are valid observations. The range check only rejects values no "
                "real market has produced, to catch a unit or parsing error."
            ),
        ),
        "currency": pa.Column(
            pl.String,
            nullable=False,
            checks=pa.Check.str_length(3, 3),
            description="ISO 4217 code the price is denominated in.",
        ),
        "source": _source_column(),
    },
    unique=["zone", "ts_utc"],
    strict=True,
    coerce=True,
)

LOAD_SCHEMA: Final = pa.DataFrameSchema(
    name="load",
    columns={
        "zone": _zone_column(),
        "ts_utc": _timestamp_column(),
        "resolution_min": _resolution_column(),
        "load_mw": pa.Column(
            pl.Float64,
            nullable=False,
            checks=pa.Check.in_range(0.0, 2_000_000.0),
            description="Average system load in MW over the interval.",
        ),
        "source": _source_column(),
    },
    unique=["zone", "ts_utc"],
    strict=True,
    coerce=True,
)

GENERATION_SCHEMA: Final = pa.DataFrameSchema(
    name="generation",
    columns={
        "zone": _zone_column(),
        "ts_utc": _timestamp_column(),
        "resolution_min": _resolution_column(),
        "fuel": pa.Column(
            pl.String,
            nullable=False,
            checks=pa.Check.isin(FUELS),
            description="Canonical fuel. Unmapped upstream codes become 'other'.",
        ),
        "gen_mw": pa.Column(
            pl.Float64,
            nullable=False,
            checks=pa.Check.in_range(-200_000.0, 2_000_000.0),
            description=(
                "Average output in MW over the interval. Negative is legitimate "
                "for pumped storage charging, battery charging and net imports."
            ),
        ),
        "source": _source_column(),
    },
    unique=["zone", "ts_utc", "fuel"],
    strict=True,
    coerce=True,
)

FUNDAMENTAL_SERIES: Final[tuple[str, ...]] = ("load", "wind", "solar")
"""Canonical day-ahead forecast series this project stores.

Wind is onshore and offshore combined at ingestion, matching how the
``generation`` dataset already combines them into one canonical ``wind`` fuel.
"""

FUNDAMENTALS_SCHEMA: Final = pa.DataFrameSchema(
    name="fundamentals",
    columns={
        "zone": _zone_column(),
        "ts_utc": _timestamp_column(),
        "resolution_min": _resolution_column(),
        "series": pa.Column(
            pl.String,
            nullable=False,
            checks=pa.Check.isin(FUNDAMENTAL_SERIES),
            description="Which day-ahead forecast series this row carries.",
        ),
        "forecast_mw": pa.Column(
            pl.Float64,
            nullable=False,
            checks=pa.Check.in_range(-50_000.0, 2_000_000.0),
            description=(
                "Forecast average power in MW over the interval. A small negative "
                "wind or solar forecast is a legitimate model output, not an error. "
                "This is the day-ahead forecast as currently held by the provider; "
                "the provider exposes no publication timestamp, so this table alone "
                "does not certify when a given value became available -- see "
                "gpa.forecast.fundamentals for how eligibility before the market "
                "gate is assigned and documented."
            ),
        ),
        "source": _source_column(),
    },
    unique=["zone", "ts_utc", "series"],
    strict=True,
    coerce=True,
)

SCHEMAS: Final[dict[str, pa.DataFrameSchema]] = {
    "price": PRICE_SCHEMA,
    "load": LOAD_SCHEMA,
    "generation": GENERATION_SCHEMA,
    "fundamentals": FUNDAMENTALS_SCHEMA,
}
"""Dataset name to contract. The keys are the dataset names used in
``Zone.sources`` and as the top-level directory in ``data/curated``."""


def empty_frame(dataset: str) -> pl.DataFrame:
    """An empty frame carrying the exact dtypes ``dataset`` declares.

    Adapters return this when a provider has no data for a window, so callers
    never have to distinguish "nothing found" from "wrong shape", and a
    concatenation with a populated frame keeps its dtypes.
    """
    try:
        schema = SCHEMAS[dataset]
    except KeyError:
        valid = ", ".join(sorted(SCHEMAS))
        raise KeyError(f"unknown dataset {dataset!r}; valid datasets are: {valid}") from None
    return pl.DataFrame(schema={name: col.dtype.type for name, col in schema.columns.items()})


def validate(frame: pl.DataFrame, dataset: str, *, lazy: bool = True) -> pl.DataFrame:
    """Validate ``frame`` against the contract for ``dataset``.

    Args:
        frame: The candidate table.
        dataset: One of the keys of :data:`SCHEMAS`.
        lazy: Collect every failure before raising rather than stopping at the
            first. Keep this on; a source that broke usually broke in more than
            one column and seeing all of them at once saves a round trip.

    Returns:
        The validated frame, with columns coerced to the contract dtypes and
        ordered as the contract declares.

    Raises:
        KeyError: If ``dataset`` is not a known dataset.
        SchemaErrors: If validation fails and ``lazy`` is true.
        SchemaError: If validation fails and ``lazy`` is false.
    """
    try:
        schema = SCHEMAS[dataset]
    except KeyError:
        valid = ", ".join(sorted(SCHEMAS))
        raise KeyError(f"unknown dataset {dataset!r}; valid datasets are: {valid}") from None

    validated = schema.validate(frame, lazy=lazy)
    return validated.select(list(schema.columns))
