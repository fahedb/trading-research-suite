#!/usr/bin/env python3
"""Generate the frozen 300-vector ULS + PUSH four-TP TRAIN sweep."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_uls_push_sweep import _float, _strategy_vectors  # type: ignore[import-not-found]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = (
    REPOSITORY_ROOT / "config" / "experiments" / "uls-push-v7-train-plane-b-tp400-sl400-v1.json"
)
OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-joint-model-300x4-tp-v1.json"
)
SWEEP_ID = "uls-push-v7-train-joint-model-300x4-tp-v1"
TAKE_PROFITS = (200.0, 400.0, 600.0, 800.0)
STOP_LOSS = 800.0
STRATEGY_COUNT = 300


def build_request() -> dict[str, object]:
    """Return the complete deterministic sweep request document."""
    base_config = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    base_params = base_config["features"][0]["params"]
    vectors = _strategy_vectors(base_params, count=STRATEGY_COUNT)
    parameter_names = sorted(
        name for name in base_params if any(vector[name] != base_params[name] for vector in vectors)
    )
    expanded_vectors = [vector for _take_profit in TAKE_PROFITS for vector in vectors]
    axes = [
        {
            "json_pointer": f"/features/0/params/{name}",
            "values": [vector[name] for vector in expanded_vectors],
        }
        for name in parameter_names
    ]
    axes.extend(
        (
            {
                "json_pointer": "/execution/take_profit_pips",
                "values": [
                    _float(take_profit)
                    for take_profit in TAKE_PROFITS
                    for _vector in vectors
                ],
            },
            {
                "json_pointer": "/execution/stop_loss_pips",
                "values": [_float(STOP_LOSS) for _vector in expanded_vectors],
            },
        )
    )
    return {
        "schema": "research-sweep-request-v1",
        "sweep_id": SWEEP_ID,
        "base_config": base_config,
        "axes": axes,
        "combination": "zip",
    }


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
