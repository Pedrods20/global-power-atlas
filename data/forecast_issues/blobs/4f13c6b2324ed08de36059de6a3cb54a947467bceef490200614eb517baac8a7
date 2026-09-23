"""Canonical registry of wholesale electricity market zones.

Everything in this project derives from this module. A *zone* is the smallest
unit at which a price is formed or a schedule is settled: a European bidding
zone (DE-LU) or a national interconnected system (BR-SIN).

The registry is deliberately narrow. It carries only markets whose history can
be refreshed by the scheduled job from public, credential-free interfaces, so
that every series feeding the forecasting and retrieval work stays current and
auditable. US, Australian, Japanese and CCEE series were retired on 2026-09-13
for that reason; their code and data remain recoverable from Git history.

``timezone`` is the IANA zone in which the market defines its own trading day
and its peak/off-peak blocks. Bucketing anything by UTC calendar day is always
wrong and this project never does it. See ``gpa.calendar``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "ZONES",
    "PeakBlock",
    "Region",
    "Zone",
    "get_zone",
    "zones_for_source",
    "zones_in_region",
]


class Region(StrEnum):
    """Continental grouping used for navigation and aggregation."""

    EUROPE = "Europe"
    SOUTH_AMERICA = "South America"


@dataclass(frozen=True, slots=True)
class PeakBlock:
    """A market's on-peak definition, in *market* local time.

    Hours are stored as the half-open interval of hour-*beginning* values so
    that they compose cleanly with timestamp arithmetic. European peakload,
    08:00 to 20:00, is ``start_hour=8, end_hour=20``.

    Attributes:
        label: Human-readable name used in documentation and on the site.
        start_hour: First hour-beginning included in the block, 0-23.
        end_hour: First hour-beginning *excluded* from the block, 1-24.
        weekdays: ISO weekday numbers included, Monday=1 through Sunday=7.
        note: Caveat shown on the methodology page. Used where the block is a
            regulatory or tariff construct rather than a traded product.
    """

    label: str
    start_hour: int
    end_hour: int
    weekdays: tuple[int, ...]
    note: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.start_hour <= 23:
            raise ValueError(f"start_hour must be 0-23, got {self.start_hour}")
        if not 1 <= self.end_hour <= 24:
            raise ValueError(f"end_hour must be 1-24, got {self.end_hour}")
        if self.start_hour >= self.end_hour:
            raise ValueError(
                f"start_hour {self.start_hour} must precede end_hour {self.end_hour}; "
                "blocks that wrap past midnight are not supported"
            )
        if not self.weekdays:
            raise ValueError("weekdays must not be empty")
        if not all(1 <= d <= 7 for d in self.weekdays):
            raise ValueError(f"weekdays must be ISO 1-7, got {self.weekdays}")


# --- Standard blocks -------------------------------------------------------

EUROPEAN_PEAKLOAD = PeakBlock(
    label="European peakload (08:00-20:00 CET, Mon-Fri)",
    start_hour=8,
    end_hour=20,
    weekdays=(1, 2, 3, 4, 5),
)
"""EEX/EPEX Peakload. Excludes Saturday and ignores public holidays."""

BR_PONTA = PeakBlock(
    label="Brazilian ponta (18:00-21:00, Mon-Fri)",
    start_hour=18,
    end_hour=21,
    weekdays=(1, 2, 3, 4, 5),
    note=(
        "Brazil has settled a genuinely hourly PLD since January 2021, so it has "
        "no traded peak block. The ponta window is a distribution-tariff construct "
        "whose exact hours each distributor sets locally; 18:00-21:00 is the common "
        "case and is used here only to make Brazil comparable with other markets. "
        "Do not read it as a wholesale product."
    ),
)


@dataclass(frozen=True, slots=True)
class Zone:
    """A single price-forming zone.

    Attributes:
        code: Stable identifier. Used as the Parquet partition key, so it must
            never change once data has been written under it.
        name: Display name.
        country: ISO 3166-1 alpha-2, or a hyphenated pair for a shared zone.
        region: Continental grouping.
        operator: System or market operator responsible for the zone.
        timezone: IANA zone defining the market trading day and blocks.
        currency: ISO 4217 code the source publishes prices in.
        peak: On-peak block definition.
        sources: Dataset name to source identifier, e.g.
            ``{"price": "energy_charts", "load": "energy_charts"}``. A dataset
            absent from this mapping is simply not collected for the zone.
        source_keys: Per-source upstream identifiers, e.g. the Energy-Charts
            country code or the ONS subsystem.
        notes: Editorial context shown on the site.
    """

    code: str
    name: str
    country: str
    region: Region
    operator: str
    timezone: str
    currency: str
    peak: PeakBlock
    sources: dict[str, str] = field(default_factory=dict)
    source_keys: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    @property
    def observes_market_dst(self) -> bool:
        """Whether the market clock observes daylight saving in the current year."""
        import datetime as dt
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(self.timezone)
        year = dt.datetime.now(dt.UTC).year
        return any(dt.datetime(year, month, 1, tzinfo=tz).dst() for month in range(1, 13))

    def has(self, dataset: str) -> bool:
        """Whether ``dataset`` is collected for this zone."""
        return dataset in self.sources


# --- The registry ----------------------------------------------------------

_ENERGY_CHARTS = {"price": "energy_charts", "load": "energy_charts", "generation": "energy_charts"}

ZONES: tuple[Zone, ...] = (
    Zone(
        code="DE-LU",
        name="Germany-Luxembourg",
        country="DE-LU",
        region=Region.EUROPE,
        operator="50Hertz / Amprion / TenneT DE / TransnetBW",
        timezone="Europe/Berlin",
        currency="EUR",
        peak=EUROPEAN_PEAKLOAD,
        # Day-ahead load/wind/solar forecasts, for the forecast model's
        # fundamentals ablation. Not fetched for FR/ES: this is a per-market
        # research feature, not a general historical-context series.
        sources=dict(_ENERGY_CHARTS) | {"fundamentals": "energy_charts"},
        source_keys={"energy_charts_country": "de", "entsoe_eic": "10Y1001A1001A82H"},
        notes=(
            "The deepest power market in Europe and the reference for continental "
            "price formation. Nuclear phase-out completed in April 2023, leaving a "
            "system where wind and solar set price for a large and growing share of "
            "hours, and where negative prices are routine rather than exceptional."
        ),
    ),
    *(
        Zone(
            code=code,
            name=name,
            country=code,
            region=Region.EUROPE,
            operator=operator,
            timezone=timezone,
            currency="EUR",
            peak=EUROPEAN_PEAKLOAD,
            sources=dict(_ENERGY_CHARTS),
            source_keys={"energy_charts_country": code.lower(), "energy_charts_bzn": code},
            notes=(
                "National generation and load, plus day-ahead bidding-zone prices, "
                "redistributed by Energy-Charts."
            ),
        )
        for code, name, operator, timezone in (
            ("FR", "France", "RTE", "Europe/Paris"),
            ("ES", "Spain", "Red Electrica", "Europe/Madrid"),
        )
    ),
    Zone(
        code="BR-SIN",
        name="Brazil (SIN)",
        country="BR",
        region=Region.SOUTH_AMERICA,
        operator="Operador Nacional do Sistema Eletrico",
        timezone="America/Sao_Paulo",
        currency="BRL",
        peak=BR_PONTA,
        sources={"load": "ons", "generation": "ons"},
        source_keys={"ons_subsystem": "SIN"},
        notes=(
            "A continent-scale hydro-dominated system operated as a single optimised "
            "cascade, where price is a model output rather than an auction clearing. "
            "Reservoir storage substitutes for the fuel-cost stack that sets price "
            "elsewhere, so scarcity shows up as a stored-energy problem first."
        ),
    ),
)

_BY_CODE: dict[str, Zone] = {z.code: z for z in ZONES}

if len(_BY_CODE) != len(ZONES):  # pragma: no cover - guards a registry typo
    raise RuntimeError("duplicate zone code in ZONES")


def get_zone(code: str) -> Zone:
    """Return the zone registered under ``code``.

    Raises:
        KeyError: If no zone matches, with the valid codes in the message.
    """
    try:
        return _BY_CODE[code]
    except KeyError:
        valid = ", ".join(sorted(_BY_CODE))
        raise KeyError(f"unknown zone {code!r}; registered zones are: {valid}") from None


def zones_in_region(region: Region) -> tuple[Zone, ...]:
    """All zones in a continental region, in registry order."""
    return tuple(z for z in ZONES if z.region is region)


def zones_for_source(source: str, dataset: str | None = None) -> tuple[Zone, ...]:
    """All zones served by ``source``, optionally narrowed to one dataset.

    Args:
        source: Source identifier such as ``"ons"`` or ``"energy_charts"``.
        dataset: If given, only zones collecting that dataset from the source.
    """
    if dataset is not None:
        return tuple(z for z in ZONES if z.sources.get(dataset) == source)
    return tuple(z for z in ZONES if source in z.sources.values())
