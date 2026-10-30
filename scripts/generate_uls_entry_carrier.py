#!/usr/bin/env python3
"""Generate the exact TRAIN carrier for the ULS entry-model context artifact."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from trading_research.experiments.config import _config_from_document
from trading_research.experiments.sweeps import _expand_request

REPOSITORY_ROOT = Path(__file__).parents[1]
SOURCE_SWEEP = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-owner-200x4-brackets-v1.json"
)
OUTPUT = (
    REPOSITORY_ROOT
    / "config"
    / "experiments"
    / "uls-push-entry-context-carrier-train-v1.json"
)
BEST_CHILD_ORDINAL = 344


def _document() -> dict[str, object]:
    request = json.loads(SOURCE_SWEEP.read_text(encoding="utf-8"))
    children = dict(_expand_request(request))
    source = deepcopy(children[BEST_CHILD_ORDINAL])
    features = source["features"]
    if not isinstance(features, list) or len(features) != 1:
        raise RuntimeError("source child feature inventory changed")
    features.append(
        {
            "name": "uls_market_context_v1",
            "version": 1,
            "params": {},
        }
    )
    source["policies"] = [{"name": "abstain_v1", "version": 1, "params": {}}]
    source["trade_evaluators"] = []
    return _config_from_document(source).document()


def _encoded(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True) + "\n"


def main() -> int:
    """Write the deterministic carrier config or verify its checked-in bytes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = _encoded(_document())
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("generated ULS entry carrier config is stale")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
