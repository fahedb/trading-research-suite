#!/usr/bin/env python3
"""Generate the frozen non-interruptible 300-vector ULS+PUSH TRAIN sweep."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from generate_uls_push_tp_training_sweep import (  # type: ignore[import-not-found]
    build_request as _source_request,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-non-interruptible-300x4-tp-v1.json"
)
SWEEP_ID = "uls-push-v7-train-non-interruptible-300x4-tp-v1"
EXECUTION_ID = "bracket_non_interruptible_v1"


def build_request() -> dict[str, object]:
    """Return the original paired design with only execution semantics replaced."""
    request = cast("dict[str, object]", _source_request())
    request["sweep_id"] = SWEEP_ID
    base_config = cast("dict[str, object]", request["base_config"])
    execution = cast("dict[str, object]", base_config["execution"])
    execution["execution_id"] = EXECUTION_ID
    return request


def _render(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def main() -> int:
    """Write the request or verify that the checked-in copy is current."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="refuse unless the checked-in request matches deterministic generation",
    )
    args = parser.parse_args()
    rendered = _render(build_request())
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated sweep request: {OUTPUT_PATH}")
        return 0
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
