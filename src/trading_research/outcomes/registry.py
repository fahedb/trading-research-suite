"""Plugin registry for versioned outcome specifications."""

from trading_research.core.interfaces import OutcomeSpec
from trading_research.core.registry import Registry

__all__ = ["OUTCOME_SPECS"]

OUTCOME_SPECS: Registry[OutcomeSpec] = Registry("outcome spec")
