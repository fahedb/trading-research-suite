"""Decision policies translating model output into target-exposure intent.

Implementations register themselves with this package's registry so an
experiment can select one by name. This package depends only on
``trading_research.core``.
"""

import trading_research.decisions.reference as _reference  # noqa: F401
from trading_research.decisions.registry import DECISION_POLICIES

__all__ = ["DECISION_POLICIES"]
