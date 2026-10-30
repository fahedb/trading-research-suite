"""Machine roots. Explicit configuration only — nothing is derived from CWD."""

from __future__ import annotations

import os
from pathlib import Path


def _root(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).resolve()


_SUITE_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_ROOT = Path.home() / ".local" / "share" / "trading-research-suite"
RESEARCH_REPO = _root("TRE_RESEARCH_REPO", str(_SUITE_ROOT))
RUNS_ROOT = _root("TRE_RUNS_ROOT", str(_LOCAL_ROOT / "runs"))
DATA_ROOT = _root("TRE_DATA_ROOT", str(_LOCAL_ROOT / "data"))
VIEWS_ROOT = _root("TRE_VIEWS_ROOT", str(_LOCAL_ROOT / "views"))


def _split_policies() -> tuple[Path, ...]:
    """Return the explicit policy allowlist, preserving the legacy override."""
    legacy = os.environ.get("TRE_SPLIT_POLICY")
    if legacy:
        return (Path(legacy).resolve(),)
    configured = os.environ.get("TRE_SPLIT_POLICIES")
    if configured:
        return tuple(Path(item).resolve() for item in configured.split(os.pathsep) if item)
    policy_root = RESEARCH_REPO / "config" / "split-policies"
    return (
        policy_root / "adr-0012-v1.json",
        policy_root / "adr-0012-btc-v1.json",
    )


SPLIT_POLICIES = _split_policies()
# Compatibility alias for callers that deliberately configure only one policy.
SPLIT_POLICY = SPLIT_POLICIES[0]
AUDIT_PATH = RUNS_ROOT / "audit.jsonl"
INDEX_PATH = RUNS_ROOT / "index" / "runs.sqlite3"

# The Phase-5 index schema this reader is written against. A mismatch is a hard
# refusal rather than a silent wrong answer (ADR-0017: the index is derived and
# disposable, so its schema is not a stable contract for outside consumers).
INDEX_SCHEMA_VERSION = "1"

PROJECT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = PROJECT / "prototype"
