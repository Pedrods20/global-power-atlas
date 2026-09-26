"""Contracts for the stored fact tables, enforced at the adapter boundary.

Long format keyed on ``(zone, ts_utc)`` plus a discriminator. Power is the average
over the interval that starts at ``ts_utc`` and lasts ``resolution_min`` minutes, so
energy is always ``mw * resolution_min / 60``. Negative prices are data.
"""

from __future__ import annotations

from typing import Final

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaError, SchemaErrors

__all__ = [
    "FUELS",
    "FUNDAMENTAL_SERIES",
    "SCHEMAS",
    "UTC_DATETIME",
    "SchemaError",
    "SchemaErrors",
    "empty_frame",
    "validate",
]

UTC_DATETIME: Final = pl.Datetime(time_unit="us", time_zone="UTC")

FUELS: Final = (
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
"""Pumped storage stays apart from hydro; ``imports`` is net interchange, never a mix share."""

FUNDAMENTAL_SERIES: Final = ("load", "wind", "solar")
"""Day-ahead forecast series; wind is onshore plus offshore, as in ``generation``."""


def _value(dtype: type[pl.DataType], low: float, high: float) -> pa.Column:
    """A non-null value whose range only rejects unit or parsing errors, not markets."""
    return pa.Column(dtype, nullable=False, checks=pa.Check.in_range(low, high))


def _table(name: str, unique: list[str], **columns: pa.Column) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        name=name,
        columns={
            "zone": pa.Column(pl.String, nullable=False, checks=pa.Check.str_length(2, 32)),
            "ts_utc": pa.Column(UTC_DATETIME, nullable=False),
            "resolution_min": pa.Column(
                pl.Int16, nullable=False, checks=pa.Check.isin((1, 5, 15, 30, 60))
            ),
            **columns,
            "source": pa.Column(pl.String, nullable=False),
        },
        unique=unique,
        strict=True,
        coerce=True,
    )


SCHEMAS: Final = {
    "price": _table(
        "prices",
        ["zone", "ts_utc"],
        price=_value(pl.Float64, -10_000.0, 100_000.0),
        currency=pa.Column(pl.String, nullable=False, checks=pa.Check.str_length(3, 3)),
    ),
    "load": _table("load", ["zone", "ts_utc"], load_mw=_value(pl.Float64, 0.0, 2_000_000.0)),
    "generation": _table(
        "generation",
        ["zone", "ts_utc", "fuel"],
        fuel=pa.Column(pl.String, nullable=False, checks=pa.Check.isin(FUELS)),
        # Negative is legitimate for storage charging and net imports.
        gen_mw=_value(pl.Float64, -200_000.0, 2_000_000.0),
    ),
    "fundamentals": _table(
        "fundamentals",
        ["zone", "ts_utc", "series"],
        series=pa.Column(pl.String, nullable=False, checks=pa.Check.isin(FUNDAMENTAL_SERIES)),
        # A small negative wind or solar forecast is a legitimate model output.
        forecast_mw=_value(pl.Float64, -50_000.0, 2_000_000.0),
    ),
}
"""Dataset name to contract: the names in ``Zone.sources`` and under ``data/curated``."""


def _schema(dataset: str) -> pa.DataFrameSchema:
    try:
        return SCHEMAS[dataset]
    except KeyError:
        raise KeyError(
            f"unknown dataset {dataset!r}; valid datasets are: {', '.join(SCHEMAS)}"
        ) from None


def empty_frame(dataset: str) -> pl.DataFrame:
    """An empty frame with the dataset's exact dtypes, so concatenation keeps them."""
    return pl.DataFrame(
        schema={name: col.dtype.type for name, col in _schema(dataset).columns.items()}
    )


def validate(frame: pl.DataFrame, dataset: str) -> pl.DataFrame:
    """Coerce and check ``frame``, collecting every failure before raising; columns in order."""
    schema = _schema(dataset)
    return schema.validate(frame, lazy=True).select(list(schema.columns))
