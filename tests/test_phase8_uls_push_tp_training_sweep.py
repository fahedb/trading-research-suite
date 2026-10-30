"""Freeze the 300-vector, four-TP ULS + PUSH TRAIN sweep."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

from trading_research.experiments.config import _config_from_document
from trading_research.experiments.sweeps import _expand_request
from trading_research.features.reference import UlsPushV7

REPOSITORY_ROOT = Path(__file__).parents[1]
REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-joint-model-300x4-tp-v1.json"
)
PREVIOUS_REQUEST_PATH = (
    REPOSITORY_ROOT / "config" / "sweeps" / "uls-push-v7-train-owner-200x4-brackets-v1.json"
)


def _decode(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"__float_hex__"}:
        return float.fromhex(cast("str", value["__float_hex__"]))
    if isinstance(value, dict):
        return {name: _decode(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _strategy_documents(request: dict[str, object], block_size: int) -> list[str]:
    documents: list[str] = []
    for ordinal, document in _expand_request(request):
        if ordinal >= block_size:
            break
        document_mapping = cast("dict[str, Any]", document)
        params = document_mapping["features"][0]["params"]
        documents.append(json.dumps(params, sort_keys=True, separators=(",", ":")))
    return documents


def test_generated_uls_push_tp_training_sweep_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate_uls_push_tp_training_sweep.py", "--check"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )


def test_sweep_freezes_300_unique_vectors_across_four_tp_levels() -> None:
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    assert request["schema"] == "research-sweep-request-v1"
    assert request["sweep_id"] == "uls-push-v7-train-joint-model-300x4-tp-v1"
    assert request["combination"] == "zip"

    expanded = _expand_request(request)
    assert len(expanded) == 1_200
    strategy_blocks: list[list[str]] = [[], [], [], []]
    observed_brackets: Counter[tuple[float, float]] = Counter()
    observed_engines: Counter[str] = Counter()

    for ordinal, document in expanded:
        resolved = _config_from_document(document)
        resolved_document = resolved.document()
        dataset = cast("dict[str, object]", resolved_document["dataset"])
        assert dataset["role"] == "train"
        assert resolved_document["access_reason"] is None
        assert resolved_document["declared_lockbox_evaluation"] is False
        assert resolved_document["evaluation_outcomes"] == []
        assert resolved_document["evaluators"] == []

        features = cast("list[dict[str, object]]", resolved_document["features"])
        feature = features[0]
        assert feature["name"] == "uls_push_v7"
        params_document = feature["params"]
        params = cast("dict[str, object]", _decode(params_document))
        UlsPushV7(**cast("Any", params))
        strategy_blocks[ordinal // 300].append(
            json.dumps(params_document, sort_keys=True, separators=(",", ":"))
        )
        if ordinal < 300:
            engine_mode = params["engine_mode"]
            assert isinstance(engine_mode, str)
            observed_engines[engine_mode] += 1

        execution = cast("dict[str, object]", resolved_document["execution"])
        take_profit = _decode(execution["take_profit_pips"])
        stop_loss = _decode(execution["stop_loss_pips"])
        assert isinstance(take_profit, float)
        assert isinstance(stop_loss, float)
        observed_brackets[(take_profit, stop_loss)] += 1

    assert len(set(strategy_blocks[0])) == 300
    assert strategy_blocks[1:] == [strategy_blocks[0]] * 3
    assert observed_engines == Counter({"merged": 150, "parallel": 150})
    assert observed_brackets == Counter(
        {
            (200.0, 800.0): 300,
            (400.0, 800.0): 300,
            (600.0, 800.0): 300,
            (800.0, 800.0): 300,
        }
    )
    assert len({json.dumps(doc, sort_keys=True) for _ordinal, doc in expanded}) == 1_200


def test_first_200_vectors_preserve_the_previous_search_family() -> None:
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    previous = json.loads(PREVIOUS_REQUEST_PATH.read_text(encoding="utf-8"))
    assert _strategy_documents(request, 300)[:200] == _strategy_documents(previous, 200)
