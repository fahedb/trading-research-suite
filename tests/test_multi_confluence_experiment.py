"""Pre-registration guard for the Multi Confluence M15 TRAIN Plane-A trial."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import cast

from trading_research.core.features import canonical_params_json
from trading_research.experiments.config import load_resolved_config
from trading_research.features.reference import MultiConfluenceM15V1


def test_multi_confluence_m15_train_protocol_is_fully_preregistered() -> None:
    repository_root = Path(__file__).parents[1]
    config = load_resolved_config(
        repository_root
        / "config"
        / "experiments"
        / "multi-confluence-m15-train-plane-a-v1.json"
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
        "eval_start": 1635374280,
    }

    features = cast("list[dict[str, object]]", document["features"])
    assert features == [
        {
            "name": "multi_confluence_m15_v1",
            "params": json.loads(canonical_params_json(MultiConfluenceM15V1().params)),
            "version": 1,
        }
    ]
    assert MultiConfluenceM15V1().window.max_prior_rows == 645_120
    assert document["representation"] is None
    assert document["model"] == {
        "name": "multi_confluence_gate_v1",
        "params": {},
        "version": 1,
    }
    assert document["training_outcome"] is None

    outcomes = cast("list[dict[str, object]]", document["evaluation_outcomes"])
    assert [item["params"]["horizon_rows"] for item in outcomes] == [240, 60, 720]  # type: ignore[index]
    assert all(
        item == {
            "name": "forward_log_return_v1",
            "params": {
                "boundary_policy": "nan",
                "decision_delay_rows": 1,
                "horizon_rows": horizon,
                "reference_price_column": "close",
            },
            "version": 1,
        }
        for item, horizon in zip(outcomes, (240, 60, 720), strict=True)
    )
    assert document["policies"] == [
        {
            "name": "threshold_v1",
            "params": {
                "output_column": "score",
                "threshold": {"__float_hex__": "0x0.0p+0"},
            },
            "version": 1,
        }
    ]
    assert document["evaluators"] == [
        {
            "name": "sign_accuracy_v1",
            "params": {
                "min_effective_samples": 1,
                "outcome_column": "target.forward_log_return",
                "output_column": "score",
            },
            "version": 1,
        },
        {
            "name": "rank_ic_v1",
            "params": {
                "min_effective_samples": 2,
                "outcome_column": "target.forward_log_return",
                "output_column": "score",
            },
            "version": 1,
        },
    ]
    assert document["execution"] is None
    assert document["trade_evaluators"] == []
    assert document["folds"] == {
        "embargo_rows": 0,
        "eval_rows": 153883,
        "fit_rows": 3,
        "purge_override": None,
        "step_rows": 153883,
    }
    assert document["seed"] is None
    assert document["access_reason"] is None
    assert document["declared_lockbox_evaluation"] is False

    assert sha256(config.canonical_json().encode()).hexdigest() == (
        "400474e29edf34c494595691b88b1e8d7b2d712e196875c9a19acc79c8b3eac1"
    )
