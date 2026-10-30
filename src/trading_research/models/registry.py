"""Plugin registry for research model implementations."""

from trading_research.core.interfaces import CorpusResearchModel, ResearchModel
from trading_research.core.registry import Registry

__all__ = ["CORPUS_RESEARCH_MODELS", "RESEARCH_MODELS"]

RESEARCH_MODELS: Registry[ResearchModel] = Registry("research model")
CORPUS_RESEARCH_MODELS: Registry[CorpusResearchModel] = Registry("corpus research model")
