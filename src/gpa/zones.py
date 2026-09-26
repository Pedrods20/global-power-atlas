"""The one market this study analyses: Germany-Luxembourg (DE-LU).

``timezone`` defines the market's trading day and blocks; nothing is bucketed by
UTC day. France, Spain and Brazil were retired on 23 September 2026 and remain in
Git history.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ZONES", "PeakBlock", "Zone", "get_zone"]


@dataclass(frozen=True, slots=True)
class PeakBlock:
    """On-peak hours as a half-open range of local hour-beginnings, on ISO weekdays."""

    start_hour: int
    end_hour: int
    weekdays: tuple[int, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.start_hour < self.end_hour <= 24:
            raise ValueError(
                f"peak hours need 0 <= start < end <= 24, got {self.start_hour}-{self.end_hour}"
            )
        if not self.weekdays or not all(1 <= day <= 7 for day in self.weekdays):
            raise ValueError(f"weekdays must be ISO days 1-7, got {self.weekdays}")


EUROPEAN_PEAKLOAD = PeakBlock(start_hour=8, end_hour=20, weekdays=(1, 2, 3, 4, 5))
"""EEX/EPEX Peakload, 08:00-20:00 Monday to Friday; public holidays are not excluded."""


@dataclass(frozen=True, slots=True)
class Zone:
    """A price-forming zone; ``code`` is the Parquet partition key and never changes."""

    code: str
    name: str
    timezone: str
    currency: str
    peak: PeakBlock
    sources: dict[str, str] = field(default_factory=dict)
    source_keys: dict[str, str] = field(default_factory=dict)

    def has(self, dataset: str) -> bool:
        return dataset in self.sources


ZONES: tuple[Zone, ...] = (
    Zone(
        code="DE-LU",
        name="Germany-Luxembourg",
        timezone="Europe/Berlin",
        currency="EUR",
        peak=EUROPEAN_PEAKLOAD,
        sources=dict.fromkeys(("price", "load", "generation", "fundamentals"), "energy_charts"),
        source_keys={"energy_charts_country": "de"},
    ),
)

_BY_CODE = {zone.code: zone for zone in ZONES}


def get_zone(code: str) -> Zone:
    try:
        return _BY_CODE[code]
    except KeyError:
        valid = ", ".join(sorted(_BY_CODE))
        raise KeyError(f"unknown zone {code!r}; registered zones are: {valid}") from None
