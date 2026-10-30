"""Plugin registries for the two closed evaluation planes."""

from trading_research.core.interfaces import Evaluator, TradeEvaluator
from trading_research.core.registry import Registry

__all__ = ["EVALUATORS", "TRADE_EVALUATORS"]

EVALUATORS: Registry[Evaluator] = Registry("evaluator")
TRADE_EVALUATORS: Registry[TradeEvaluator] = Registry("trade evaluator")
