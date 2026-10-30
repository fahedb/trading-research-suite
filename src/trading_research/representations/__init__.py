"""Dense encodings of market state for similarity and embedding approaches.

Implementations register themselves with this package's registry so an
experiment can select one by name. This package depends only on
``trading_research.core``.
"""

import trading_research.representations.reference as _reference  # noqa: F401
from trading_research.representations.registry import REPRESENTATION_MODELS

__all__ = ["REPRESENTATION_MODELS"]
