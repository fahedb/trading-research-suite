"""Research models producing generic, non-directional output.

Implementations register themselves with this package's registry so an
experiment can select one by name. This package depends only on
``trading_research.core``.
"""

import trading_research.models.reference as _reference  # noqa: F401
import trading_research.models.uls_entry_selector as _uls_entry_selector  # noqa: F401
import trading_research.models.uls_joint_selector as _uls_joint_selector  # noqa: F401
import trading_research.models.uls_tp_selector as _uls_tp_selector  # noqa: F401
import trading_research.models.zone_selector as _zone_selector  # noqa: F401
from trading_research.models.registry import CORPUS_RESEARCH_MODELS, RESEARCH_MODELS

__all__ = ["CORPUS_RESEARCH_MODELS", "RESEARCH_MODELS"]
