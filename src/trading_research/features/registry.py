"""Plugin registry for feature provider implementations."""

from trading_research.core.interfaces import FeatureProvider
from trading_research.core.registry import Registry

__all__ = ["FEATURE_PROVIDERS"]

FEATURE_PROVIDERS: Registry[FeatureProvider] = Registry("feature provider")
