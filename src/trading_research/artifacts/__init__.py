"""Pure identities, deterministic verification, and durable artifacts."""

import trading_research.artifacts.fitted as _fitted  # noqa: F401
from trading_research.artifacts.determinism import DeterminismReport, verify_determinism
from trading_research.artifacts.identity import PlatformIdentity, StageIdentity
from trading_research.artifacts.materialization import MaterializationPolicy
from trading_research.artifacts.store import ArtifactStore

__all__ = [
    "ArtifactStore",
    "DeterminismReport",
    "MaterializationPolicy",
    "PlatformIdentity",
    "StageIdentity",
    "verify_determinism",
]
