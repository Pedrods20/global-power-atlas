"""Canonical registry of wholesale electricity market zones.

Everything in this project derives from this module. A *zone* is the smallest
unit at which a price is formed and a schedule is settled. Depending on the
market that is an ISO/RTO hub (ERCOT), a bidding zone (DE-LU), a subsystem
(BR-SIN) or a NEM region (AU-NSW1).

Two timezone fields exist on purpose, and the distinction is not cosmetic:

``timezone``
    The IANA zone in which the market defines its own trading day and its
    peak/off-peak blocks. This is what all bucketing and block assignment uses.

``civil_timezone``
    The IANA zone civil life actually runs on. It differs from ``timezone``
    only where a market deliberately refuses daylight saving. The Australian
    NEM is the canonical case: AEMO settles every region on Australian Eastern
    Standard Time all year, so a Victorian summer trading interval is stamped
    an hour away from the clock on a Melbourne wall.

Bucketing anything by UTC calendar day is always wrong and this project never
does it. See ``gpa.calendar``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "ZONES",
    "HolidayCalendar",
    "PeakBlock",
    "Region",
    "Zone",
    "get_zone",
    "zones_for_source",
    "zones_in_region",
]


class Region(StrEnum):
    """Continental grouping used for navigation and aggregation."""

    NORTH_AMERICA = "North America"
    EUROPE = "Europe"
    SOUTH_AMERICA = "South America"
    OCEANIA = "Oceania"


class HolidayCalendar(StrEnum):
    """Which holiday set removes a day from the on-peak block."""

    NERC = "NERC"
    """The six NERC holidays used by every North American power market."""

    NONE = "none"
    """No holiday exclusion; the block is purely a weekday/hour rule."""


@dataclass(frozen=True, slots=True)
class PeakBlock:
    """A market's on-peak definition, in *market* local time.

    Hours follow the hour-ending (HE) convention used by power markets, but are
    stored here as the half-open interval of hour-*beginning* values so that
    they compose cleanly with timestamp arithmetic. NERC on-peak is HE0700
    through HE2200, which is hours beginning 06:00 through 21:00 inclusive, so
    it is stored as ``start_hour=6, end_hour=22``.

    Attributes:
        label: Human-readable name used in documentation and on the site.
        start_hour: First hour-beginning included in the block, 0-23.
        end_hour: First hour-beginning *excluded* from the block, 1-24.
        weekdays: ISO weekday numbers included, Monday=1 through Sunday=7.
        holidays: Holiday calendar that removes an otherwise-eligible day.
        note: Caveat shown on the methodology page. Used where the block is a
            regulatory or tariff construct rather than a traded product.
    """

    label: str
    start_hour: int
    end_hour: int
    weekdays: tuple[int, ...]
    holidays: HolidayCalendar = HolidayCalendar.NONE
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

NERC_ON_PEAK = PeakBlock(
    label="NERC on-peak (HE0700-HE2200, Mon-Sat, ex-NERC holidays)",
    start_hour=6,
    end_hour=22,
    weekdays=(1, 2, 3, 4, 5, 6),
    holidays=HolidayCalendar.NERC,
)
"""The North American standard. Note it includes Saturday, which surprises
people who assume a five-day block."""

EUROPEAN_PEAKLOAD = PeakBlock(
    label="European peakload (08:00-20:00 CET, Mon-Fri)",
    start_hour=8,
    end_hour=20,
    weekdays=(1, 2, 3, 4, 5),
)
"""EEX/EPEX Peakload. Unlike NERC it excludes Saturday and ignores holidays."""

AEMO_PEAK = PeakBlock(
    label="AEMO peak (07:00-22:00 AEST, Mon-Fri)",
    start_hour=7,
    end_hour=22,
    weekdays=(1, 2, 3, 4, 5),
)
"""Australian business-day peak, expressed in NEM market time (AEST)."""

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
        source_keys: Per-source upstream identifiers, e.g. the EIA respondent
            code or the ENTSO-E EIC area code.
        civil_timezone: IANA zone civil life runs on, when it differs from
            ``timezone``. ``None`` means they are the same.
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
    civil_timezone: str | None = None
    notes: str = ""

    @property
    def market_timezone(self) -> str:
        """Alias for ``timezone``, for call sites where the intent needs saying."""
        return self.timezone

    @property
    def observes_market_dst(self) -> bool:
        """Whether market time and civil time agree on daylight saving."""
        return self.civil_timezone is None

    def has(self, dataset: str) -> bool:
        """Whether ``dataset`` is collected for this zone."""
        return dataset in self.sources


# --- The registry ----------------------------------------------------------
#
# Phase 0 deliberately carries one zone per continent so the whole pipeline can
# be proven end to end before breadth is added. Additional zones are cheap once
# their source adapter exists: append here and the CLI, the store and the site
# pick them up with no further change.

ZONES: tuple[Zone, ...] = (
    Zone(
        code="ERCOT",
        name="ERCOT (Texas)",
        country="US",
        region=Region.NORTH_AMERICA,
        operator="Electric Reliability Council of Texas",
        timezone="America/Chicago",
        currency="USD",
        peak=NERC_ON_PEAK,
        sources={"load": "eia", "generation": "eia"},
        source_keys={"eia_respondent": "ERCO"},
        notes=(
            "Energy-only market with no capacity payment, the largest installed wind "
            "fleet in the United States, and a scarcity-pricing mechanism that lets "
            "the offer cap drive extreme summer settlements."
        ),
    ),
    Zone(
        code="DE-LU",
        name="Germany-Luxembourg",
        country="DE-LU",
        region=Region.EUROPE,
        operator="50Hertz / Amprion / TenneT DE / TransnetBW",
        timezone="Europe/Berlin",
        currency="EUR",
        peak=EUROPEAN_PEAKLOAD,
        sources={"price": "energy_charts", "load": "energy_charts", "generation": "energy_charts"},
        source_keys={"energy_charts_country": "de", "entsoe_eic": "10Y1001A1001A82H"},
        notes=(
            "The deepest power market in Europe and the reference for continental "
            "price formation. Nuclear phase-out completed in April 2023, leaving a "
            "system where wind and solar set price for a large and growing share of "
            "hours, and where negative prices are routine rather than exceptional."
        ),
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
    Zone(
        code="AU-NSW1",
        name="Australia NEM - New South Wales",
        country="AU",
        region=Region.OCEANIA,
        operator="Australian Energy Market Operator",
        timezone="Australia/Brisbane",
        civil_timezone="Australia/Sydney",
        currency="AUD",
        peak=AEMO_PEAK,
        # Price and demand come from AEMO's own public archive rather than a
        # redistributor; only the fuel split needs a third party, because that
        # archive does not carry one.
        sources={"price": "aemo", "load": "aemo", "generation": "openelectricity"},
        source_keys={
            "aemo_region": "NSW1",
            "opennem_region": "NSW1",
            "opennem_network": "NEM",
        },
        notes=(
            "Five-minute settlement since October 2021, the shortest dispatch and "
            "settlement interval of any major market, over a fleet retiring coal "
            "faster than it is replacing it. Market time is AEST year-round, which "
            "is why this zone carries a separate civil timezone."
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
        source: Source identifier such as ``"eia"`` or ``"energy_charts"``.
        dataset: If given, only zones collecting that dataset from the source.
    """
    if dataset is not None:
        return tuple(z for z in ZONES if z.sources.get(dataset) == source)
    return tuple(z for z in ZONES if source in z.sources.values())
