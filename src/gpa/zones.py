"""Canonical registry of wholesale electricity market zones.

Everything in this project derives from this module. A *zone* is the smallest
unit at which a price is formed: here, a European day-ahead bidding zone.

The registry holds one market, Germany-Luxembourg, because it is the only one
this study analyses. France, Spain and Brazil were collected as historical
context until 23 September 2026 and were retired because no page, forecast or
valuation used them; US, Australian, Japanese and CCEE series went on
2026-09-13. Their code and data remain recoverable from Git history.

``timezone`` is the IANA zone in which the market defines its own trading day
and its peak/off-peak blocks. Bucketing anything by UTC calendar day is always
wrong and this project never does it. See ``gpa.calendar``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ZONES", "PeakBlock", "Zone", "get_zone"]


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
    """

    label: str
    start_hour: int
    end_hour: int
    weekdays: tuple[int, ...]

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


@dataclass(frozen=True, slots=True)
class Zone:
    """A single price-forming zone.

    Attributes:
        code: Stable identifier. Used as the Parquet partition key, so it must
            never change once data has been written under it.
        name: Display name.
        timezone: IANA zone defining the market trading day and blocks.
        currency: ISO 4217 code the source publishes prices in.
        peak: On-peak block definition.
        sources: Dataset name to source identifier, e.g.
            ``{"price": "energy_charts", "load": "energy_charts"}``. A dataset
            absent from this mapping is simply not collected for the zone.
        source_keys: Per-source upstream identifiers, e.g. the Energy-Charts
            country code.
        notes: Editorial context shown on the site.
    """

    code: str
    name: str
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

ZONES: tuple[Zone, ...] = (
    Zone(
        code="DE-LU",
        name="Germany-Luxembourg",
        timezone="Europe/Berlin",
        currency="EUR",
        peak=EUROPEAN_PEAKLOAD,
        # Day-ahead load/wind/solar forecasts feed the fundamentals ablation and
        # the prospective `ridge_da` arm.
        sources={
            "price": "energy_charts",
            "load": "energy_charts",
            "generation": "energy_charts",
            "fundamentals": "energy_charts",
        },
        source_keys={"energy_charts_country": "de"},
        notes=(
            "The deepest power market in Europe and the reference for continental "
            "price formation. Nuclear phase-out completed in April 2023, leaving a "
            "system where wind and solar set price for a large and growing share of "
            "hours, and where negative prices are routine rather than exceptional."
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
