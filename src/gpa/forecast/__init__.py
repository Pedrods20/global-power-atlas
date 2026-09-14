"""Short-term day-ahead price forecasting, and the harness that judges it.

The point of this package is not the model. It is the evidence that the model is
worth anything, which is a different and harder artefact to build. Two years of
validated hourly history exist in the store, so the question "does this beat
repeating last week?" can be answered rather than asserted, and answering it
needs a walk-forward backtest, naive baselines that are taken seriously, and
error metrics reported where the error actually matters.

The modules divide along the four places a price forecast usually goes wrong:

``panel``
    What was knowable, and when. The target is defined precisely and every
    feature is lagged past the auction's gate closure. Leakage is prevented
    here, once, so that nothing downstream has to be trusted with it.
``models``
    Three naive baselines and a per-hour ridge regression. The baselines are the
    bar, not a formality.
``backtest``
    An expanding origin, refitted every day, with the ridge penalty chosen on a
    validation window that closes before the test period opens.
``scoring``
    MAE and RMSE in currency per megawatt hour, never a percentage error on a
    series that crosses zero, split by market block and by price regime because
    the aggregate hides the hours anyone cares about.

The evaluation harness is reusable on purpose. The next front adds regulatory
document retrieval, and the only honest way to decide whether a regulatory
signal is worth anything is to put it through this and see whether a backtested
error metric moves.
"""

from __future__ import annotations

from gpa.forecast import backtest, fundamentals, ledger, linalg, models, panel, scoring
from gpa.forecast.backtest import BacktestResult, InsufficientHistory, run
from gpa.forecast.panel import Panel, build_panel, load_panel

__all__ = [
    "BacktestResult",
    "InsufficientHistory",
    "Panel",
    "backtest",
    "build_panel",
    "fundamentals",
    "ledger",
    "linalg",
    "load_panel",
    "models",
    "panel",
    "run",
    "scoring",
]
