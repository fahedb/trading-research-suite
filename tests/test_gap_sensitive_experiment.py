"""Pre-registration guard for the Gap Sensitive Levels H1 TRAIN simulation."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import cast

from trading_research.core.features import canonical_params_json
from trading_research.experiments.config import load_resolved_config
from trading_research.features.reference import GapSensitiveLevelsH1V1


def test_gap_sensitive_h1_train_protocol_is_fully_preregistered() -> None:
    repository_root = Path(__file__).parents[1]
    config = load_resolved_config(
        repository_root
        / "config"
        / "experiments"
        / "gap-sensitive-levels-h1-train-plane-b-v1.json"
    )
    document = config.document()

    assert document["integrity_mode"] == "full"
    assert document["dataset"] == {
        "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
        "manifest_sha256": (
            "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e"
        ),
        "role": "train",
    }
    assert document["split_policy"] == {
        "adr": "ADR-0012",
        "policy_sha256": (
            "b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb"
        ),
        "policy_version": "v1",
    }
    assert document["bounds"] == {
        "context_start": 1577926800,
        "eval_end": 1703894160,
        "eval_start": 1588751700,
    }

    features = cast("list[dict[str, object]]", document["features"])
    assert features == [
        {
            "name": "gap_sensitive_levels_h1_v1",
            "params": json.loads(
                canonical_params_json(GapSensitiveLevelsH1V1().params)
            ),
            "version": 1,
        }
    ]
    assert GapSensitiveLevelsH1V1().window.max_prior_rows == 120_780
    assert document["representation"] is None
    assert document["model"] == {
        "name": "gap_sensitive_gate_v1",
        "params": {},
        "version": 1,
    }
    assert document["training_outcome"] is None
    assert document["evaluation_outcomes"] == []
    assert document["policies"] == [
        {
            "name": "event_exposure_v1",
            "params": {},
            "version": 1,
        }
    ]
    assert document["evaluators"] == []

    execution = cast("dict[str, object]", document["execution"])
    assert execution["execution_id"] == "bracket_single_position_v1"
    assert execution["version"] == 1
    assert execution["lot_size"] == {"__float_hex__": "0x1.47ae147ae147bp-7"}
    assert execution["take_profit_pips"] is None
    assert execution["stop_loss_pips"] is None
    binding = cast("dict[str, object]", execution["cost_binding"])
    assert binding["binding_id"] == "unverified-audit-input-v1"
    assert binding["binding_digest"] is None
    assert binding["cost_semantics"] == "unverified"
    assert binding["execution_price_model"] == "recorded_path_unverified"
    assert binding["spread_pips"] == {"__float_hex__": "0x1.4000000000000p+4"}
    assert binding["commission_per_lot"] == {
        "__float_hex__": "0x1.8000000000000p+2"
    }

    assert document["trade_evaluators"] == [
        {
            "name": "trade_summary_v1",
            "params": {"min_effective_trades": 1},
            "version": 1,
        },
        {
            "name": "equity_drawdown_v1",
            "params": {"min_effective_rows": 1},
            "version": 1,
        },
    ]
    assert document["folds"] == {
        "embargo_rows": 0,
        "eval_rows": 258751,
        "fit_rows": 3,
        "purge_override": None,
        "step_rows": 258751,
    }
    assert document["seed"] is None
    assert document["access_reason"] is None
    assert document["declared_lockbox_evaluation"] is False
    assert sha256(config.canonical_json().encode()).hexdigest() == (
        "b5c1de675ef4ee96f8fd2b0467ca355ee087c34dc4b41516419ba0057f2715b5"
    )
