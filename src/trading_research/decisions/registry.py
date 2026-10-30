"""Plugin registry for decision policy implementations."""

from trading_research.core.interfaces import DecisionPolicy
from trading_research.core.registry import Registry

__all__ = ["DECISION_POLICIES"]

DECISION_POLICIES: Registry[DecisionPolicy] = Registry("decision policy")
