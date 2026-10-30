"""Evaluation and statistical analysis of completed research runs.

Implementations register themselves with this package's registry so an
experiment can select one by name. This package depends only on
``trading_research.core``.
"""

import trading_research.evaluation.reference as _reference  # noqa: F401
import trading_research.evaluation.trade_reference as _trade_reference  # noqa: F401
from trading_research.evaluation.registry import EVALUATORS, TRADE_EVALUATORS

__all__ = ["EVALUATORS", "TRADE_EVALUATORS"]
