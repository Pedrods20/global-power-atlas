"""Energy-Charts adapter (Fraunhofer ISE), covering European bidding zones.

Energy-Charts republishes ENTSO-E and SMARD data through an open API that needs
no credentials, which is why it carries the European zone here while an ENTSO-E
Transparency token is being obtained. The underlying figures are the same ones
ENTSO-E publishes; the licence is CC BY 4.0 and attribution is on the
methodology page.

Two quirks of this provider are handled explicitly:

``end`` is inclusive
    Asking for 1 August to 2 August returns both days in full. Rows are
    filtered to the caller's half-open window after parsing.

Resolution changes without notice
    German data moved from hourly to quarter-hourly. The interval is measured
    from the returned timestamps rather than assumed.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any, Final

import polars as pl

from gpa.capacity import CAPACITY_COLUMNS
from gpa.schema import UTC_DATETIME
from gpa.schema import empty_frame as _empty
from gpa.sources.base import UpstreamError, fetch_json, http_client
from gpa.zones import Zone

__all__ = ["DERIVED_SERIES", "FUEL_MAP", "LOAD_SERIES", "EnergyChartsSource"]

_BASE = "https://api.energy-charts.info"

FUEL_MAP: Final[dict[str, str]] = {
    "Hydro Run-of-River": "hydro",
    "Hydro water reservoir": "hydro",
    "Hydro pumped storage": "hydro_pumped_storage",
    "Hydro pumped storage consumption": "hydro_pumped_storage",
    "Biomass": "biomass",
    "Fossil brown coal / lignite": "coal",
    "Fossil hard coal": "coal",
    "Fossil peat": "coal",
    "Fossil oil": "oil",
    "Fossil oil shale": "oil",
    "Fossil coal-derived gas": "gas",
    "Fossil gas": "gas",
    "Geothermal": "geothermal",
    "Nuclear": "nuclear",
    "Waste": "waste",
    "Wind offshore": "wind",
    "Wind onshore": "wind",
    "Solar": "solar",
    "Battery Storage (Power)": "battery",
    "Battery": "battery",
    "Battery Consumption": "battery",
    "Other renewables": "other",
    "Others": "other",
    "Other": "other",
    "Cross border electricity trading": "imports",
}
"""Upstream series name to canonical fuel.

Several upstream names collapse onto one canonical fuel, so rows are summed
after mapping. Pumped storage generation and its separately reported
consumption both map to ``hydro_pumped_storage``; because consumption is
published as a negative number, summing them yields net storage output, which
is the honest figure and keeps the fuel out of renewable share.
"""

LOAD_SERIES: Final[frozenset[str]] = frozenset({"Load", "Load (incl. self-consumption)"})
"""Series carrying system load rather than generation."""

DERIVED_SERIES: Final[frozenset[str]] = frozenset(
    {
        "Residual load",
        "Renewable share of load",
        "Renewable share of generation",
    }
)
"""Series that are already-computed indicators, not measurements.

They are dropped rather than stored. This project recomputes renewable share
from the mix so the definition is ours and is documented, and so it stays
consistent across markets whose providers disagree about what counts.
"""


_INSTALLED_POWER_GWH_TECHNOLOGIES: Final[frozenset[str]] = frozenset({"Battery storage (capacity)"})
"""The one /installed_power technology reported in GWh; every other one is GW."""


_FORECAST_PRODUCTION_TYPES: Final[dict[str, str]] = {
    "load": "load",
    "wind_onshore": "wind",
    "wind_offshore": "wind",
    "solar": "solar",
}
"""Energy-Charts ``production_type`` values to fetch, and the canonical
fundamental series each maps to. Onshore and offshore wind are summed into
one ``wind`` series, matching how ``generation`` already combines them."""


class EnergyChartsSource:
    """Fetch price, load and generation for a European bidding zone."""

    name: str = "energy_charts"
    datasets: tuple[str, ...] = ("price", "load", "generation", "fundamentals")
    # No documented cap, but the public_power endpoint returns about twenty
    # series at quarter-hourly resolution and starts timing out well before a
    # quarter's worth of data. Sixty days is comfortably inside that.
    max_window_days: int | None = 60

    def fetch(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        if dataset not in self.datasets:
            raise ValueError(f"{self.name} cannot produce dataset {dataset!r}")

        if dataset == "price":
            return self._fetch_price(zone, start, end)
        if dataset == "fundamentals":
            return self._fetch_fundamentals(zone, start, end)
        return self._fetch_power(zone, dataset, start, end)

    # --- price ------------------------------------------------------------

    def _fetch_price(self, zone: Zone, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        bzn = zone.source_keys.get("energy_charts_bzn") or zone.code
        payload = self._get(
            "/price",
            {"bzn": bzn, "start": _as_date(start), "end": _as_date(end)},
        )

        seconds = payload.get("unix_seconds") or []
        prices = payload.get("price") or []
        if not seconds:
            return _empty("price")

        unit = str(payload.get("unit", ""))
        currency = _currency_from_unit(unit, zone)

        frame = pl.DataFrame(
            {"_epoch": seconds[: len(prices)], "price": prices[: len(seconds)]},
            schema={"_epoch": pl.Int64, "price": pl.Float64},
        )
        frame = (
            _stamp(frame).join(_resolution_table(seconds, zone), on="ts_utc").drop_nulls("price")
        )
        frame = _window(frame, start, end)
        if frame.is_empty():
            return _empty("price")

        return frame.with_columns(
            pl.lit(zone.code).alias("zone"),
            pl.lit(currency).alias("currency"),
            pl.lit(self.name).alias("source"),
        ).select("zone", "ts_utc", "resolution_min", "price", "currency", "source")

    # --- load and generation ---------------------------------------------

    def _fetch_power(
        self,
        zone: Zone,
        dataset: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> pl.DataFrame:
        country = zone.source_keys.get("energy_charts_country")
        if not country:
            raise UpstreamError(f"zone {zone.code} has no 'energy_charts_country' in source_keys")

        payload = self._get(
            "/public_power",
            {"country": country, "start": _as_date(start), "end": _as_date(end)},
        )

        seconds = payload.get("unix_seconds") or []
        series = payload.get("production_types") or []
        if not seconds or not series:
            return _empty(dataset)

        wanted = LOAD_SERIES if dataset == "load" else None
        # These are alternative definitions, not additive areas. Prefer the
        # public-power load corresponding to the generation boundary.
        labels = {str(entry.get("name", "")) for entry in series}
        load_label = "Load" if "Load" in labels else "Load (incl. self-consumption)"
        # France publishes battery output and charging as two signed series;
        # other countries publish one net series. Both at once would double count.
        if "Battery Storage (Power)" in labels and labels & {"Battery", "Battery Consumption"}:
            raise UpstreamError("net and gross battery series published together")
        frames: list[pl.DataFrame] = []

        for entry in series:
            label = str(entry.get("name", ""))
            if label in DERIVED_SERIES:
                continue
            if wanted is not None and label != load_label:
                continue
            if wanted is None and label in LOAD_SERIES:
                continue
            if wanted is None and label not in FUEL_MAP:
                raise UpstreamError(
                    f"unrecognised generation series {label!r}; review its units and meaning"
                )

            values = entry.get("data") or []
            n = min(len(seconds), len(values))
            if n == 0:
                continue

            chunk = pl.DataFrame(
                {"_epoch": seconds[:n], "_value": values[:n]},
                schema={"_epoch": pl.Int64, "_value": pl.Float64},
            ).drop_nulls("_value")

            if wanted is None:
                chunk = chunk.with_columns(pl.lit(FUEL_MAP[label]).alias("fuel"))
            frames.append(chunk)

        if not frames:
            return _empty(dataset)

        combined = _window(_stamp(pl.concat(frames, how="vertical")), start, end)
        if combined.is_empty():
            return _empty(dataset)

        resolutions = _resolution_table(seconds, zone)

        if dataset == "load":
            out = combined.group_by("ts_utc").agg(pl.col("_value").sum().alias("load_mw"))
            return (
                out.join(resolutions, on="ts_utc")
                .with_columns(
                    pl.lit(zone.code).alias("zone"),
                    pl.lit(self.name).alias("source"),
                )
                .select("zone", "ts_utc", "resolution_min", "load_mw", "source")
                .sort("ts_utc")
            )

        # Several upstream names share a canonical fuel, so sum after mapping.
        out = combined.group_by(["ts_utc", "fuel"]).agg(pl.col("_value").sum().alias("gen_mw"))
        return (
            out.join(resolutions, on="ts_utc")
            .with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "fuel", "gen_mw", "source")
            .sort(["ts_utc", "fuel"])
        )

    # --- fundamentals -------------------------------------------------------

    def _fetch_fundamentals(self, zone: Zone, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
        """Day-ahead load/wind/solar forecasts, not realised values.

        This adapter reports only what the provider returns; it does not
        record a publication vintage because the ``/public_power_forecast``
        endpoint exposes none. Assigning an eligible-before-the-gate vintage
        for a historical backfill is a research-policy decision, made in
        :mod:`gpa.forecast.fundamentals`, not here.
        """
        country = zone.source_keys.get("energy_charts_country")
        if not country:
            raise UpstreamError(f"zone {zone.code} has no 'energy_charts_country' in source_keys")

        frames: list[pl.DataFrame] = []
        all_seconds: set[int] = set()
        for production_type, series in _FORECAST_PRODUCTION_TYPES.items():
            payload = self._get(
                "/public_power_forecast",
                {
                    "country": country,
                    "production_type": production_type,
                    "forecast_type": "day-ahead",
                    "start": _as_date(start),
                    "end": _as_date(end),
                },
            )
            seconds = payload.get("unix_seconds") or []
            values = payload.get("forecast_values") or []
            n = min(len(seconds), len(values))
            if n == 0:
                continue
            all_seconds.update(seconds[:n])
            chunk = (
                pl.DataFrame(
                    {"_epoch": seconds[:n], "_value": values[:n]},
                    schema={"_epoch": pl.Int64, "_value": pl.Float64},
                )
                .drop_nulls("_value")
                .with_columns(
                    pl.lit(series).alias("series"),
                    pl.lit(production_type).alias("_component"),
                )
            )
            frames.append(chunk)

        if not frames:
            return _empty("fundamentals")

        combined = _window(_stamp(pl.concat(frames, how="vertical")), start, end)
        if combined.is_empty():
            return _empty("fundamentals")

        # Total wind needs both components at each timestamp. Missing offshore
        # is missing information, not zero production. Reject duplicates too.
        out = (
            combined.group_by(["ts_utc", "series"])
            .agg(
                pl.col("_value").sum().alias("forecast_mw"),
                pl.col("_component").n_unique().alias("_components"),
                pl.len().alias("_rows"),
            )
            .filter(
                (pl.col("_components") == pl.when(pl.col("series") == "wind").then(2).otherwise(1))
                & (pl.col("_rows") == pl.col("_components"))
            )
        )
        resolutions = _resolution_table(sorted(all_seconds), zone)
        return (
            out.join(resolutions, on="ts_utc")
            .with_columns(
                pl.lit(zone.code).alias("zone"),
                pl.lit(self.name).alias("source"),
            )
            .select("zone", "ts_utc", "resolution_min", "series", "forecast_mw", "source")
            .sort(["ts_utc", "series"])
        )

    # --- installed capacity -------------------------------------------------

    def fetch_installed_power(self, zone: Zone, *, time_step: str = "yearly") -> pl.DataFrame:
        """Installed capacity by technology: a period series, not a settlement series.

        Unlike every other method here, this takes no ``start``/``end`` window:
        the provider always returns its complete current series in one
        response, and a "planned" series is a government build-out target,
        not a measurement, so there is nothing to page through. ``time``
        labels the *end* of each period per the endpoint's own documentation,
        not an interval start; the returned ``as_of`` records that same
        convention as a real date. Monthly granularity is documented by the
        provider as Germany-only.

        Args:
            zone: Supplies the Energy-Charts country code.
            time_step: ``"yearly"`` or ``"monthly"``.

        Returns:
            Long-format rows matching :data:`gpa.capacity.CAPACITY_COLUMNS`:
            country, time_step, period (the provider's raw label), as_of,
            technology (the provider's raw name -- kept apart from this
            project's canonical fuel taxonomy, because a "planned" and a
            realised series for the same technology are different rows here,
            not one fuel bucket the way generation combines them), value,
            unit ("GW", or "GWh" for battery storage energy capacity),
            is_planned, source. Empty with that shape if the provider returns
            nothing.
        """
        if time_step not in ("yearly", "monthly"):
            raise ValueError(f"time_step must be 'yearly' or 'monthly', got {time_step!r}")
        country = zone.source_keys.get("energy_charts_country")
        if not country:
            raise UpstreamError(f"zone {zone.code} has no 'energy_charts_country' in source_keys")

        payload = self._get("/installed_power", {"country": country, "time_step": time_step})
        labels = [str(value) for value in (payload.get("time") or [])]
        series = payload.get("production_types") or []
        if not labels or not series:
            return pl.DataFrame(schema=CAPACITY_COLUMNS)

        rows: list[dict[str, object]] = []
        for entry in series:
            name = str(entry.get("name", ""))
            values = entry.get("data") or []
            unit = "GWh" if name in _INSTALLED_POWER_GWH_TECHNOLOGIES else "GW"
            is_planned = "planned" in name.lower()
            n = min(len(labels), len(values))
            for label, value in zip(labels[:n], values[:n], strict=True):
                if value is None:
                    continue
                rows.append(
                    {
                        "country": country.upper(),
                        "time_step": time_step,
                        "period": label,
                        "as_of": _period_end(label, time_step),
                        "technology": name,
                        "value": float(value),
                        "unit": unit,
                        "is_planned": is_planned,
                        "source": self.name,
                    }
                )
        if not rows:
            return pl.DataFrame(schema=CAPACITY_COLUMNS)
        return pl.DataFrame(rows, schema=CAPACITY_COLUMNS).sort(["technology", "as_of"])

    # --- helpers ----------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        with http_client() as client:
            payload = fetch_json(f"{_BASE}{path}", client=client, params=params)
        if not isinstance(payload, dict):
            raise UpstreamError(f"unexpected payload type {type(payload).__name__} from {path}")
        if payload.get("deprecated"):
            # Surfaced as a warning rather than a failure: the endpoint keeps
            # serving for a grace period, and a scheduled job should not break
            # on the day a deprecation flag flips.
            import logging

            logging.getLogger(__name__).warning("Energy-Charts marks %s as deprecated", path)
        return payload


def _as_date(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).date().isoformat()


def _stamp(frame: pl.DataFrame) -> pl.DataFrame:
    """Turn the ``_epoch`` seconds column into the canonical UTC timestamp."""
    return frame.with_columns(
        pl.from_epoch(pl.col("_epoch"), time_unit="s")
        .dt.replace_time_zone("UTC")
        .cast(UTC_DATETIME)
        .alias("ts_utc")
    ).drop("_epoch")


def _window(frame: pl.DataFrame, start: dt.datetime, end: dt.datetime) -> pl.DataFrame:
    """Trim to the caller's half-open window; the provider's end is inclusive."""
    return frame.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") < end))


def _period_end(label: str, time_step: str) -> dt.date:
    """The real calendar date a /installed_power period label ends on.

    Confirmed live against the provider on 15 September 2026: yearly labels
    are a bare ``"YYYY"``, monthly labels are ``"MM.YYYY"`` (e.g.
    ``"02.2024"``) -- not the ISO ``"YYYY-MM"`` this project uses everywhere
    else, so it is not safe to assume.
    """
    if time_step == "yearly":
        return dt.date(int(label), 12, 31)
    month_text, year_text = label.split(".")
    year, month = int(year_text), int(month_text)
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _currency_from_unit(unit: str, zone: Zone) -> str:
    """Read the ISO currency out of a unit string such as ``'EUR / MWh'``."""
    head = unit.split("/")[0].strip().upper()
    if head != zone.currency or unit.split("/")[-1].strip().lower() != "mwh":
        raise UpstreamError(f"unexpected price unit {unit!r} for {zone.code}")
    return head


def _resolution_table(seconds: list[int], zone: Zone) -> pl.DataFrame:
    """Measure cadence on the complete timestamp axis, separately per delivery day.

    Resolution changes take effect on a market-local delivery day. Null values
    and requested-window edges must not change that day's cadence. Missing
    timestamps do not extend the preceding interval; sparse ambiguous days fail.
    """
    axis = _stamp(pl.DataFrame({"_epoch": seconds}, schema={"_epoch": pl.Int64})).sort("ts_utc")
    if axis["ts_utc"].n_unique() != axis.height:
        raise UpstreamError("duplicate timestamps in Energy-Charts response")
    axis = axis.with_columns(
        pl.col("ts_utc").dt.convert_time_zone(zone.timezone).dt.date().alias("_day"),
        pl.col("ts_utc")
        .diff()
        .over(pl.col("ts_utc").dt.convert_time_zone(zone.timezone).dt.date())
        .dt.total_minutes()
        .alias("_step"),
    )
    cadence = (
        axis.group_by("_day")
        .agg(
            pl.col("_step").min().alias("resolution_min"),
            pl.col("_step").drop_nulls().alias("_steps"),
        )
        .sort("_day")
    )
    unsupported = cadence.filter(
        pl.col("resolution_min").is_not_null() & ~pl.col("resolution_min").is_in([15, 60])
    )
    if unsupported.height:
        raise UpstreamError(f"unsupported timestamp cadence on {unsupported['_day'].to_list()[:3]}")
    # Every gap inside a day must be a whole number of that day's intervals,
    # otherwise the day mixes products and no single duration describes it.
    irregular = cadence.filter(
        pl.col("_steps").list.eval(pl.element() % pl.element().min() != 0).list.any()
    )
    if irregular.height:
        raise UpstreamError(f"irregular timestamp spacing on {irregular['_day'].to_list()[:3]}")
    # A day holding a single timestamp (a response edge) has no measurable
    # cadence. Products change only at delivery-day boundaries, so it takes the
    # adjacent day's; a response with no measurable day at all is rejected.
    cadence = cadence.with_columns(pl.col("resolution_min").forward_fill().backward_fill())
    if cadence["resolution_min"].null_count():
        raise UpstreamError("cannot measure timestamp cadence from a single observation")
    resolved = axis.join(cadence.select("_day", "resolution_min"), on="_day")
    if resolved.filter(
        pl.col("ts_utc").dt.epoch("s") % (pl.col("resolution_min") * 60) != 0
    ).height:
        raise UpstreamError("timestamps are not aligned to their interval boundaries")
    return resolved.select("ts_utc", pl.col("resolution_min").cast(pl.Int16))
