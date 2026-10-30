"""Feature calculation, isolated from all model and strategy logic.

Implementations register themselves with this package's registry so an
experiment can select one by name. This package depends only on
``trading_research.core``.
"""

from trading_research.features import reference as _reference  # noqa: F401
from trading_research.features.registry import FEATURE_PROVIDERS

__all__ = ["FEATURE_PROVIDERS"]
