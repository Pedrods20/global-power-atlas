"""Source adapters and the registry that resolves a zone's source names to them."""

from __future__ import annotations

from gpa.sources.base import Source, SourceError, UpstreamError
from gpa.sources.energy_charts import EnergyChartsSource

__all__ = [
    "REGISTRY",
    "EnergyChartsSource",
    "Source",
    "SourceError",
    "UpstreamError",
    "get_source",
]

REGISTRY: dict[str, Source] = {source.name: source for source in (EnergyChartsSource(),)}
"""One shared instance per provider: adapters are stateless."""


def get_source(name: str) -> Source:
    try:
        return REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown source {name!r}; registered sources are: {valid}") from None
