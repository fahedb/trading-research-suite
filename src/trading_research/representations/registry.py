"""Plugin registry for representation model implementations."""

from trading_research.core.interfaces import RepresentationModel
from trading_research.core.registry import Registry

__all__ = ["REPRESENTATION_MODELS"]

REPRESENTATION_MODELS: Registry[RepresentationModel] = Registry("representation model")
