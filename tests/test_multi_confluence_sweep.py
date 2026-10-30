"""Freeze the owner-directed 300-vector, two-bracket confluence TRAIN sweep."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from trading_research.experiments.config import _config_from_document, load_resolved_config
from trading_research.experiments.sweeps import _expand_request
from trading_research.features.reference import MultiConfluenceM15V1

REPOSITORY_ROOT = Path(__file__).parents[1]
BASE_CONFIG_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "experiments"
    / "multi-confluence-m15-train-plane-b-tp400-sl500-v1.json"
)
REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-train-300x2-tp400-tp800-sl500-v1.json"
)
BTC_REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-v1.json"
)
BTC_V2_REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-fivefold-v2.json"
)
PORTFOLIO_REQUEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-xau-portfolio-replay-6x2-v1.json"
)
PORTFOLIO_SOURCE_CHILD_ORDINALS = (
    0,
    300,
    75,
    375,
    100,
    400,
    160,
    460,
    197,
    497,
    289,
    589,
)
TP1200_CONFIG_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "experiments"
    / "multi-confluence-m15-train-vector160-tp1200-sl500-v1.json"
)
VARYING_PARAMS = frozenset(
    {
        "atr_length",
        "ema_fast_length",
        "ema_slow_length",
        "level_atr_range",
        "liquidity_lookback",
        "minimum_score",
        "pivot_left",
        "pivot_right",
        "rsi_length",
        "rsi_long_level",
        "rsi_short_level",
        "trend_atr_range",
    }
)


def _decode(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"__float_hex__"}:
        return float.fromhex(cast("str", value["__float_hex__"]))
    if isinstance(value, dict):
        return {name: _decode(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _provider(params_document: object) -> MultiConfluenceM15V1:
    params = cast("dict[str, object]", _decode(params_document))
    params["level_grid_seconds"] = tuple(cast("list[int]", params["level_grid_seconds"]))
    params["level_phase_offset_seconds"] = tuple(
        cast("list[int]", params["level_phase_offset_seconds"])
    )
    return MultiConfluenceM15V1(**cast("Any", params))


def test_generated_multi_confluence_sweep_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate_multi_confluence_sweep.py", "--check"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )


def test_multi_confluence_sweep_freezes_300_unique_vectors_and_two_brackets() -> None:
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    assert request["schema"] == "research-sweep-request-v1"
    assert request["sweep_id"] == (
        "multi-confluence-m15-train-300x2-tp400-tp800-sl500-v1"
    )
    assert request["combination"] == "zip"
    assert sha256(REQUEST_PATH.read_bytes()).hexdigest() == (
        "c351b4942c6dad8dcb8b2abea0ae7a57a0ade2c9e575a127ae8925a426060db6"
    )

    base = load_resolved_config(BASE_CONFIG_PATH)
    assert sha256(base.canonical_json().encode()).hexdigest() == (
        "ffefe5b0f33122cf91285d3d8ba3077fa2a4de94a9240ba5ead7f215149da1e6"
    )
    base_document = base.document()
    base_features = cast("list[dict[str, object]]", base_document["features"])
    base_params = cast("dict[str, object]", base_features[0]["params"])

    expanded = _expand_request(request)
    assert len(expanded) == 600
    strategy_blocks: list[list[str]] = [[], []]
    observed_brackets: Counter[tuple[float, float]] = Counter()
    observed_values: dict[str, set[object]] = {name: set() for name in VARYING_PARAMS}

    for ordinal, document in expanded:
        resolved_document = _config_from_document(document).document()
        assert resolved_document["dataset"] == base_document["dataset"]
        assert resolved_document["bounds"] == base_document["bounds"]
        assert resolved_document["folds"] == {
            "embargo_rows": 0,
            "eval_rows": 153883,
            "fit_rows": 3,
            "purge_override": None,
            "step_rows": 153883,
        }
        assert resolved_document["evaluation_outcomes"] == []
        assert resolved_document["evaluators"] == []
        assert resolved_document["access_reason"] is None
        assert resolved_document["declared_lockbox_evaluation"] is False

        features = cast("list[dict[str, object]]", resolved_document["features"])
        feature = features[0]
        assert feature["name"] == "multi_confluence_m15_v1"
        params_document = cast("dict[str, object]", feature["params"])
        provider = _provider(params_document)
        assert provider.window.max_prior_rows <= 645_120
        assert {
            name for name in params_document if params_document[name] != base_params[name]
        } <= VARYING_PARAMS
        strategy_blocks[ordinal // 300].append(
            json.dumps(params_document, sort_keys=True, separators=(",", ":"))
        )
        if ordinal < 300:
            decoded = cast("dict[str, object]", _decode(params_document))
            for name in VARYING_PARAMS:
                observed_values[name].add(decoded[name])

        execution = cast("dict[str, object]", resolved_document["execution"])
        assert execution["execution_id"] == "bracket_non_interruptible_v1"
        binding = cast("dict[str, object]", execution["cost_binding"])
        assert binding["cost_semantics"] == "unverified"
        assert binding["execution_price_model"] == "recorded_path_unverified"
        take_profit = _decode(execution["take_profit_pips"])
        stop_loss = _decode(execution["stop_loss_pips"])
        assert isinstance(take_profit, float)
        assert isinstance(stop_loss, float)
        observed_brackets[(take_profit, stop_loss)] += 1

    assert strategy_blocks[0][0] == json.dumps(
        base_params, sort_keys=True, separators=(",", ":")
    )
    assert len(set(strategy_blocks[0])) == 300
    assert strategy_blocks[1] == strategy_blocks[0]
    assert observed_brackets == Counter({(400.0, 500.0): 300, (800.0, 500.0): 300})
    assert observed_values == {
        "atr_length": {7, 14, 21, 28},
        "ema_fast_length": {20, 50, 100},
        "ema_slow_length": {100, 200},
        "level_atr_range": {0.30, 0.45, 0.60, 0.75, 0.90},
        "liquidity_lookback": {10, 20, 30, 40, 60},
        "minimum_score": {3, 4, 5},
        "pivot_left": {2, 3, 5},
        "pivot_right": {2, 3, 5},
        "rsi_length": {7, 14, 21, 28},
        "rsi_long_level": {35, 40, 45, 48},
        "rsi_short_level": {52, 55, 60, 65},
        "trend_atr_range": {0.25, 0.50, 0.75, 1.00},
    }
    assert (
        len(
            {
                json.dumps(document, sort_keys=True, separators=(",", ":"))
                for _ordinal, document in expanded
            }
        )
        == 600
    )


def test_btc_sweep_reuses_strategy_vectors_with_btc_native_execution() -> None:
    source = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    request = json.loads(BTC_REQUEST_PATH.read_text(encoding="utf-8"))
    assert sha256(BTC_REQUEST_PATH.read_bytes()).hexdigest() == (
        "a84705b76e69749cd33841d709b76809a680bf31a20cc13fd70a3e1b3d67a9a2"
    )
    assert request["sweep_id"] == (
        "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-v1"
    )

    source_documents = dict(_expand_request(source))
    expanded = _expand_request(request)
    assert len(expanded) == 600
    brackets: Counter[tuple[float, float]] = Counter()
    for ordinal, document in expanded:
        resolved = _config_from_document(document).document()
        source_resolved = _config_from_document(source_documents[ordinal]).document()
        assert resolved["features"] == source_resolved["features"]
        assert resolved["model"] == source_resolved["model"]
        assert resolved["policies"] == source_resolved["policies"]
        assert resolved["dataset"] == {
            "dataset_id": "BTCUSD_S_M1_TRAIN_V1",
            "manifest_sha256": (
                "73454e8dacf36b233144f1d7b7866ec30cf0c60f7a47efd366c43bc63b632b9b"
            ),
            "role": "train",
        }
        assert resolved["split_policy"] == {
            "adr": "ADR-0012",
            "policy_version": "btc-v1",
            "policy_sha256": (
                "3896cd9bedfe9357279c02df4904cda92d698b2d079c65e7bed6455503d55899"
            ),
        }
        assert resolved["bounds"] == {
            "context_start": 1_577_923_200,
            "eval_start": 1_619_598_600,
            "eval_end": 1_704_066_960,
        }
        assert resolved["folds"] == {
            "embargo_rows": 0,
            "eval_rows": 279_421,
            "fit_rows": 3,
            "purge_override": None,
            "step_rows": 279_421,
        }
        execution = cast("dict[str, object]", resolved["execution"])
        binding = cast("dict[str, object]", execution["cost_binding"])
        assert _decode(binding) == {
            "binding_id": "unverified-audit-input-v1",
            "binding_digest": None,
            "cost_semantics": "unverified",
            "execution_price_model": "recorded_path_unverified",
            "contract_size": 1.0,
            "price_units_per_pip": 0.01,
            "account_currency_per_quote_currency": 1.0,
            "spread_pips": 600.0,
            "commission_per_lot": 0.0,
            "commission_basis": "per_round_turn",
        }
        brackets[
            (
                cast("float", _decode(execution["take_profit_pips"])),
                cast("float", _decode(execution["stop_loss_pips"])),
            )
        ] += 1

    assert brackets == Counter(
        {(18_000.0, 22_500.0): 300, (36_000.0, 22_500.0): 300}
    )


def test_btc_v2_changes_only_the_corrected_five_fold_geometry() -> None:
    v1 = json.loads(BTC_REQUEST_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(BTC_V2_REQUEST_PATH.read_text(encoding="utf-8"))
    assert sha256(BTC_V2_REQUEST_PATH.read_bytes()).hexdigest() == (
        "89e2b2ee9836745de734ca7a7573c6f69a8268486cd8b1d485903e1d29c244e7"
    )
    assert v2["sweep_id"] == (
        "mc-m15-btc-train-300x2-tp18000-tp36000-sl22500-5fold-v2"
    )
    v1["sweep_id"] = v2["sweep_id"]
    v1["base_config"]["folds"]["eval_rows"] = 279_420
    v1["base_config"]["folds"]["step_rows"] = 279_420
    assert v2 == v1
    assert len(_expand_request(v2)) == 600

    # Evaluation axis: three technical fit rows followed by five equal folds,
    # with exactly two tail rows unused.
    evaluation_axis_rows = 1_397_105
    assert evaluation_axis_rows - 2 == 3 + 5 * 279_420


def test_portfolio_replay_is_exactly_twelve_existing_xau_children() -> None:
    source = dict(_expand_request(json.loads(REQUEST_PATH.read_text(encoding="utf-8"))))
    request = json.loads(PORTFOLIO_REQUEST_PATH.read_text(encoding="utf-8"))
    assert request["schema"] == "research-sweep-request-v1"
    assert request["sweep_id"] == (
        "multi-confluence-m15-xau-portfolio-replay-6x2-v1"
    )
    assert request["combination"] == "zip"
    assert sha256(PORTFOLIO_REQUEST_PATH.read_bytes()).hexdigest() == (
        "5a30034645b57eea12e2f4829bf901fe80309734f63ae7c9dbe38ae33a8f55d0"
    )

    expanded = _expand_request(request)
    assert len(expanded) == 12
    assert [document for _ordinal, document in expanded] == [
        source[ordinal] for ordinal in PORTFOLIO_SOURCE_CHILD_ORDINALS
    ]
    assert len(
        {
            json.dumps(document, sort_keys=True, separators=(",", ":"))
            for _ordinal, document in expanded
        }
    ) == 12

    scores: Counter[int] = Counter()
    brackets: Counter[tuple[float, float]] = Counter()
    for _ordinal, document in expanded:
        resolved = _config_from_document(document).document()
        dataset = cast("dict[str, object]", resolved["dataset"])
        assert dataset["dataset_id"] == "XAUUSD_S_M1_TRAIN_V1"
        assert dataset["role"] == "train"
        assert resolved["access_reason"] is None
        assert resolved["declared_lockbox_evaluation"] is False
        feature = cast("list[dict[str, object]]", resolved["features"])[0]
        params = cast("dict[str, object]", _decode(feature["params"]))
        scores[cast("int", params["minimum_score"])] += 1
        execution = cast("dict[str, object]", resolved["execution"])
        brackets[
            (
                cast("float", _decode(execution["take_profit_pips"])),
                cast("float", _decode(execution["stop_loss_pips"])),
            )
        ] += 1
    assert scores == Counter({4: 6, 3: 4, 5: 2})
    assert brackets == Counter({(400.0, 500.0): 6, (800.0, 500.0): 6})


def test_vector160_tp1200_followup_changes_only_take_profit() -> None:
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    source_documents = dict(_expand_request(request))
    expected = _config_from_document(source_documents[460]).document()
    execution = cast("dict[str, object]", expected["execution"])
    execution["take_profit_pips"] = {"__float_hex__": "0x1.2c00000000000p+10"}

    followup = load_resolved_config(TP1200_CONFIG_PATH)
    assert followup.document() == expected
    assert sha256(followup.canonical_json().encode()).hexdigest() == (
        "e37b39f7d5e09453d685b89970b9a25b84a0b68567af97c2c9331144521d5681"
    )
