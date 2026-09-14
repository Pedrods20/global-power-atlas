"""Source adapters, and the registry that resolves a zone to one.

Adding a provider is three steps: write the adapter, register it here, and
point a zone's ``sources`` mapping at its name. Nothing else in the project
needs to change.
"""

from __future__ import annotations

from gpa.sources.base import (
    MissingCredential,
    Source,
    SourceError,
    UpstreamError,
)
from gpa.sources.energy_charts import EnergyChartsSource
from gpa.sources.ons import OnsSource
from gpa.sources.smard import SmardSource

__all__ = [
    "REGISTRY",
    "EnergyChartsSource",
    "MissingCredential",
    "OnsSource",
    "SmardSource",
    "Source",
    "SourceError",
    "UpstreamError",
    "get_source",
]

REGISTRY: dict[str, Source] = {
    source.name: source
    for source in (
        EnergyChartsSource(),
        OnsSource(),
        SmardSource(),
    )
}
"""Source name to a ready-to-use adapter instance.

Adapters are stateless, so one shared instance per provider is safe.
"""


def get_source(name: str) -> Source:
    """Return the adapter registered under ``name``.

    Raises:
        KeyError: If no adapter matches, listing the registered names.
    """
    try:
        return REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown source {name!r}; registered sources are: {valid}") from None
