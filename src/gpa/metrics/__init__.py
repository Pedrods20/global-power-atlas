"""Market analytics over the canonical tables.

Each module owns one family of questions:

``load``
    Demand shape: daily profiles, load duration curves, load factor.
``price``
    Price behaviour: blocks and spreads, duration curves, negative prices,
    tail statistics, realised volatility and capture rates.
``mix``
    Supply composition: generation mix, renewable share, carbon intensity.
``spreads``
    Thermal margin: spark, dark and clean spreads.

Every function takes the canonical frames from :mod:`gpa.store`, keys on
market-local time through :mod:`gpa.calendar`, and returns a plain polars frame.
None of them fetch, cache or write.
"""

from __future__ import annotations

from gpa.metrics import load, mix, price, spreads

__all__ = ["load", "mix", "price", "spreads"]
