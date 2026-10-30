"""Versioned forward outcomes; the only research layer allowed to read forward."""

from trading_research.outcomes.reference import ForwardLogReturn as _ForwardLogReturn
from trading_research.outcomes.registry import OUTCOME_SPECS

__all__ = ["OUTCOME_SPECS"]

OUTCOME_SPECS.add("forward_log_return_v1", _ForwardLogReturn)
