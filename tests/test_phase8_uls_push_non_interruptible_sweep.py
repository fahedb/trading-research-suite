"""Freeze the non-interruptible 300-vector, four-TP ULS+PUSH TRAIN sweep."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import cast

from trading_research.experiments.config import _config_from_document
from trading_research.experiments.sweeps import _expand_request

REPOSITORY_ROOT = Path(__file__).parents[1]
REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-non-interruptible-300x4-tp-v1.json"
)
SOURCE_REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-joint-model-300x4-tp-v1.json"
)


def _decode(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"__float_hex__"}:
        return float.fromhex(cast("str", value["__float_hex__"]))
    return value


def test_generated_non_interruptible_sweep_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate_uls_push_non_interruptible_tp_sweep.py", "--check"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )


def test_non_interruptible_sweep_changes_only_execution_identity() -> None:
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    source = json.loads(SOURCE_REQUEST_PATH.read_text(encoding="utf-8"))
    assert request["sweep_id"] == "uls-push-v7-train-non-interruptible-300x4-tp-v1"
    comparable = json.loads(json.dumps(request))
    comparable["sweep_id"] = source["sweep_id"]
    comparable["base_config"]["execution"]["execution_id"] = (
        source["base_config"]["execution"]["execution_id"]
    )
    assert comparable == source

    expanded = _expand_request(request)
    assert len(expanded) == 1_200
    brackets: Counter[tuple[float, float]] = Counter()
    for _ordinal, document in expanded:
        resolved = _config_from_document(document).document()
        assert cast("dict[str, object]", resolved["dataset"])["role"] == "train"
        assert resolved["evaluation_outcomes"] == []
        assert resolved["evaluators"] == []
        execution = cast("dict[str, object]", resolved["execution"])
        assert execution["execution_id"] == "bracket_non_interruptible_v1"
        brackets[
            (
                cast("float", _decode(execution["take_profit_pips"])),
                cast("float", _decode(execution["stop_loss_pips"])),
            )
        ] += 1
    assert brackets == Counter(
        {
            (200.0, 800.0): 300,
            (400.0, 800.0): 300,
            (600.0, 800.0): 300,
            (800.0, 800.0): 300,
        }
    )
