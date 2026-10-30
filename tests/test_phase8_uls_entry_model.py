"""Release guards for the frozen ULS entry-gating pilot."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np

from trading_research.core import (
    ClockDomain,
    DatasetRef,
    FeatureFrame,
    FeatureOrigin,
    GapPolicy,
    ModelInputs,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewRole,
    ViewSpan,
    WindowSpec,
)
from trading_research.core.feature_frames import _restore_feature_frame
from trading_research.core.research import _make_corpus_model_inputs
from trading_research.core.research_frames import _make_fitted_state, _make_model_output_frame
from trading_research.decisions import DECISION_POLICIES
from trading_research.experiments.fitting import _infer_corpus_model
from trading_research.experiments.sweeps import _expand_request
from trading_research.features.reference import (
    _ULS_MARKET_CONTEXT_NAMES,
    UlsPushV7,
    _AggregatedStrategyBars,
    _uls_market_context_values,
)
from trading_research.models import CORPUS_RESEARCH_MODELS
from trading_research.models.reference import _ULS_VALUE_FEATURES
from trading_research.models.uls_entry_selector import (
    _CONTEXT_FEATURE_NAMES,
    _FEATURE_NAMES,
    _INPUT_NAMES,
    _TARGET_NAMES,
)
from trading_research.models.uls_joint_selector import (
    _OUTPUT_NAMES as _JOINT_OUTPUT_NAMES,
)
from trading_research.models.uls_joint_selector import (
    _TARGET_NAMES as _JOINT_TARGET_NAMES,
)
from trading_research.models.uls_tp_selector import (
    _ENTRY_TARGET as _TP_ENTRY_TARGET,
)
from trading_research.models.uls_tp_selector import (
    _OUTPUT_NAMES as _TP_OUTPUT_NAMES,
)
from trading_research.models.uls_tp_selector import (
    _ROLE_TARGET as _TP_ROLE_TARGET,
)
from trading_research.models.uls_tp_selector import (
    _TP_TARGET_NAMES,
)
from trading_research.models.uls_tp_selector import _entry_payload as _tp_entry_payload
from trading_research.ops.phase8_entry_validation import _validation_request
from trading_research.ops.phase8_joint_validation import (
    _aggregate_metric,
    _serializable_metrics,
)
from trading_research.ops.phase8_joint_validation import (
    _single_validation_request as _joint_single_validation_request,
)
from trading_research.ops.phase8_joint_validation import (
    _validation_request as _joint_full_validation_request,
)
from trading_research.ops.phase8_tp_validation import (
    _validation_request as _tp_validation_request,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
CARRIER_PATH = (
    REPOSITORY_ROOT / "config" / "experiments" / "uls-push-entry-context-carrier-train-v1.json"
)
SOURCE_SWEEP = (
    REPOSITORY_ROOT / "config" / "sweeps" / "uls-push-v7-train-owner-200x4-brackets-v1.json"
)
NON_INTERRUPTIBLE_SOURCE_SWEEP = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "uls-push-v7-train-non-interruptible-300x4-tp-v1.json"
)


def test_generated_context_carrier_is_exact_best_tp800_train_child() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate_uls_entry_carrier.py", "--check"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    carrier = json.loads(CARRIER_PATH.read_text(encoding="utf-8"))
    source = cast(
        "dict[str, Any]",
        dict(_expand_request(json.loads(SOURCE_SWEEP.read_text(encoding="utf-8"))))[344],
    )
    assert carrier["features"][0] == source["features"][0]
    assert carrier["features"][1] == {
        "name": "uls_market_context_v1",
        "params": {},
        "version": 1,
    }
    assert carrier["dataset"]["role"] == "train"
    assert carrier["execution"] == source["execution"]
    assert carrier["policies"] == [{"name": "abstain_v1", "params": {}, "version": 1}]
    assert carrier["trade_evaluators"] == []


def test_market_context_is_finite_after_warmup_and_future_blind() -> None:
    size = 620
    positions = np.arange(size, dtype=np.float64)
    close = 1_800.0 + 0.02 * positions + np.sin(positions / 11.0)
    opened = close - 0.1 * np.cos(positions / 7.0)
    high = np.maximum(opened, close) + 0.4 + 0.02 * np.sin(positions / 5.0)
    low = np.minimum(opened, close) - 0.4 - 0.02 * np.cos(positions / 5.0)
    volume = 100.0 + np.remainder(positions * 7.0, 31.0)

    def bars(chosen_close: np.ndarray) -> _AggregatedStrategyBars:
        delta = chosen_close - close
        return _AggregatedStrategyBars(
            np.arange(size, dtype=np.int64),
            np.arange(size, dtype=np.int64) * 5,
            opened + delta,
            high + delta,
            low + delta,
            chosen_close,
            volume,
        )

    first = _uls_market_context_values(bars(close))
    changed_close = close.copy()
    changed_close[600:] += np.linspace(1.0, 20.0, size - 600)
    second = _uls_market_context_values(bars(changed_close))
    assert tuple(first) == _ULS_MARKET_CONTEXT_NAMES
    for name in _ULS_MARKET_CONTEXT_NAMES:
        assert np.isfinite(first[name][550])
        np.testing.assert_array_equal(first[name][:600], second[name][:600])


def _model_frame(rows: int = 48) -> tuple[ViewSpan, FeatureFrame]:
    domain = ClockDomain("uls-entry-test.native")
    ref = DatasetRef("ULS_ENTRY_TEST_TRAIN", "a" * 64, ViewRole.TRAIN)
    keys = np.arange(1_000, 1_000 + rows, dtype=np.int64)
    event = np.where(np.arange(rows) % 2 == 0, 1.0, -1.0)
    entry = 1_800.0 + np.arange(rows, dtype=np.float64) / 10.0
    columns: dict[str, np.ndarray] = {
        "up_event_signed": event,
        "up_source": np.ones(rows),
        "up_score": np.full(rows, 65.0),
        "up_entry_ref": entry,
        "up_sweep_extreme": entry - event,
        "up_poi_top": entry + 0.5,
        "up_poi_bot": entry - 0.5,
        "up_struct_level": entry + event,
        "up_opp_liquidity": entry + 2.0 * event,
        "up_atr_exec": np.ones(rows),
        "up_atr_sig": np.full(rows, 1.2),
        "up_liq_price": entry - event,
        "up_liq_touches": np.full(rows, 2.0),
        "up_sweep_dist_atr": np.full(rows, 0.25),
        "up_wick_ratio": np.full(rows, 0.5),
        "up_disp_atr": np.full(rows, 0.75),
        "up_vol_rel": np.full(rows, 1.1),
        "up_vol_rank": np.full(rows, 55.0),
        "up_bos": np.ones(rows),
        "up_tl": np.zeros(rows),
        "up_cisd": np.zeros(rows),
        "up_key_ok": np.ones(rows),
    }
    uls_origin = FeatureOrigin(
        "uls_push_v7", 1, UlsPushV7().params, WindowSpec(28_000, GapPolicy.CROSS)
    )
    context_origin = FeatureOrigin(
        "uls_market_context_v1", 1, {}, WindowSpec(2_569, GapPolicy.CROSS)
    )
    origins = dict.fromkeys(("up_event_signed", *_ULS_VALUE_FEATURES), uls_origin)
    for index, name in enumerate(_CONTEXT_FEATURE_NAMES, start=1):
        columns[name] = 1.0 + np.arange(rows, dtype=np.float64) * index / 1_000.0
        origins[name] = context_origin
    frame = _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=domain,
        source_identities=(ref.canonical_identity,),
        columns=columns,
        origins={name: origins[name] for name in _FEATURE_NAMES},
    )
    view = ViewSpan(
        (ref.canonical_identity,),
        TimeUnit.SECONDS,
        domain,
        NativeTimeKey(int(keys[0]), TimeUnit.SECONDS, domain),
        NativeTimeKey(int(keys[0]), TimeUnit.SECONDS, domain),
        NativeTimeKey(int(keys[-1]), TimeUnit.SECONDS, domain),
    )
    return view, frame


def test_entry_forest_keeps_raw_baseline_direction_when_gate_abstains() -> None:
    train = np.arange(96, dtype=np.float64)
    model = CORPUS_RESEARCH_MODELS.create(
        "uls_entry_forest_v1",
        n_estimators=4,
        max_depth=3,
        min_samples_leaf=2,
        max_features=0.75,
        bootstrap=False,
        random_seed=7,
        selection_threshold=1_000_000.0,
    )
    corpus = _make_corpus_model_inputs(
        inputs={name: np.sin(train / float(index + 2)) for index, name in enumerate(_INPUT_NAMES)},
        targets={_TARGET_NAMES[0]: np.cos(train / 5.0)},
        weights=np.ones(train.size),
    )
    payload = model.fit_corpus(corpus)
    view, frame = _model_frame()
    output = _infer_corpus_model(
        model,
        view=view,
        fitted_state=_make_fitted_state(identity_key="b" * 64, payload=payload),
        inputs=ModelInputs(frame),
    )
    np.testing.assert_array_equal(output.column("value_0"), frame.column("up_event_signed"))
    assert np.isnan(output.column("score")).all()
    assert np.isfinite(output.column("value_1")).all()


def test_validation_request_pairs_one_frozen_model_with_raw_best_baseline() -> None:
    carrier = json.loads(CARRIER_PATH.read_text(encoding="utf-8"))
    source = {
        "deployment": {"feature_params": carrier["features"][0]["params"]},
        "evaluation_execution": carrier["execution"],
    }
    reference: dict[str, object] = {
        "bundle_id": "uls-entry-selector-v1-" + "1" * 20,
        "bundle_manifest_sha256": "2" * 64,
        "fitted_state_identity_key": "3" * 64,
        "fitted_state_artifact_sha256": "4" * 64,
    }
    request = _validation_request(
        source=source,
        reference=reference,
        selection_threshold=-0.25,
    )
    children = _expand_request(request)
    assert len(children) == 2
    expanded = [cast("dict[str, Any]", child) for _ordinal, child in children]
    assert [child["dataset"]["role"] for child in expanded] == [
        "validation",
        "validation",
    ]
    assert [child["policies"][0]["params"]["output_column"] for child in expanded] == [
        "score",
        "value_0",
    ]
    assert expanded[0]["fit_mode"] == expanded[1]["fit_mode"]
    assert expanded[0]["model"] == expanded[1]["model"]
    assert expanded[0]["features"] == expanded[1]["features"]


def test_tp_forest_emits_one_gate_and_seven_row_local_values() -> None:
    train = np.arange(180, dtype=np.float64)
    role = np.ones(train.size, dtype=np.float64)
    role[:120] = 0.0
    model = CORPUS_RESEARCH_MODELS.create(
        "uls_entry_tp_forest_v1",
        entry_n_estimators=4,
        entry_max_depth=3,
        entry_min_samples_leaf=2,
        entry_max_features=0.75,
        selector_n_estimators=4,
        selector_max_depth=3,
        selector_min_samples_leaf=2,
        selector_max_features=0.75,
        bootstrap=False,
        entry_random_seed=7,
        selector_random_seed=8,
        selection_threshold=-1_000_000.0,
    )
    corpus = _make_corpus_model_inputs(
        inputs={name: np.sin(train / float(index + 2)) for index, name in enumerate(_INPUT_NAMES)},
        targets={
            _TP_ROLE_TARGET: role,
            _TP_ENTRY_TARGET: np.cos(train / 5.0),
            **{
                name: np.sin(train / float(index + 3))
                for index, name in enumerate(_TP_TARGET_NAMES)
            },
        },
        weights=np.ones(train.size),
    )
    payload = model.fit_corpus(corpus)
    assert _tp_entry_payload(payload).array("values").ndim == 1
    view, frame = _model_frame()
    output = _infer_corpus_model(
        model,
        view=view,
        fitted_state=_make_fitted_state(identity_key="c" * 64, payload=payload),
        inputs=ModelInputs(frame),
    )
    assert output.column_names == _TP_OUTPUT_NAMES
    np.testing.assert_array_equal(output.column("score"), frame.column("up_event_signed"))
    assert all(np.isfinite(output.column(f"value_{index}")).all() for index in range(7))


def test_adaptive_tp_policy_skips_rejections_and_maps_all_seven_arms() -> None:
    view, _frame = _model_frame(rows=3)
    origin = StageOrigin("uls_entry_tp_forest_v1", 1, {})
    alternatives = {
        f"value_{index}": np.array([float(index == 0), 0.0, float(index == 6)])
        for index in range(7)
    }
    output = _make_model_output_frame(
        view=view,
        keys=np.array([1_000, 1_001, 1_002], dtype=np.int64),
        columns={"score": np.array([1.0, np.nan, -1.0]), **alternatives},
        origins=dict.fromkeys(_TP_OUTPUT_NAMES, origin),
    )
    policy = DECISION_POLICIES.create(
        "adaptive_bracket_exposure_v1",
        stop_loss_distance=8.0,
        **{f"value_{index}_distance": float(index + 2) for index in range(7)},
    )
    intent = policy.decide(output)
    np.testing.assert_array_equal(intent.keys, np.array([1_000, 1_002]))
    np.testing.assert_array_equal(intent.target_exposure, np.array([1.0, -1.0]))
    np.testing.assert_array_equal(intent.take_profit_distance, np.array([2.0, 8.0]))
    np.testing.assert_array_equal(intent.stop_loss_distance, np.array([8.0, 8.0]))


def test_tp_validation_request_pairs_dynamic_with_seven_fixed_arms() -> None:
    carrier = json.loads(CARRIER_PATH.read_text(encoding="utf-8"))
    entry_source = {
        "deployment": {"feature_params": carrier["features"][0]["params"]},
        "evaluation_execution": carrier["execution"],
    }
    reference: dict[str, object] = {
        "bundle_id": "uls-entry-tp-selector-v1-" + "1" * 20,
        "bundle_manifest_sha256": "2" * 64,
        "fitted_state_identity_key": "3" * 64,
        "fitted_state_artifact_sha256": "4" * 64,
    }
    children = _expand_request(
        _tp_validation_request(
            entry_source=entry_source,
            reference=reference,
            selection_threshold=-0.25,
        )
    )
    assert len(children) == 8
    expanded = [cast("dict[str, Any]", child) for _ordinal, child in children]
    assert expanded[0]["policies"][0]["name"] == "adaptive_bracket_exposure_v1"
    assert [child["policies"][0]["name"] for child in expanded[1:]] == [
        "bracket_threshold_v1"
    ] * 7
    assert [
        float.fromhex(child["execution"]["take_profit_pips"]["__float_hex__"])
        for child in expanded
    ] == [800.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0]


def test_joint_forest_emits_abstention_baseline_and_four_values() -> None:
    train = np.arange(180, dtype=np.float64)
    model = CORPUS_RESEARCH_MODELS.create(
        "uls_joint_action_forest_v1",
        n_estimators=4,
        max_depth=3,
        min_samples_leaf=2,
        max_features=0.75,
        bootstrap=False,
        random_seed=7,
        selection_threshold=1_000_000.0,
    )
    corpus = _make_corpus_model_inputs(
        inputs={
            name: np.sin(train / float(index + 2))
            for index, name in enumerate(_INPUT_NAMES)
        },
        targets={
            name: np.cos(train / float(index + 3))
            for index, name in enumerate(_JOINT_TARGET_NAMES)
        },
        weights=np.ones(train.size),
    )
    payload = model.fit_corpus(corpus)
    view, frame = _model_frame()
    output = _infer_corpus_model(
        model,
        view=view,
        fitted_state=_make_fitted_state(identity_key="d" * 64, payload=payload),
        inputs=ModelInputs(frame),
    )
    assert output.column_names == _JOINT_OUTPUT_NAMES
    assert np.isnan(output.column("score")).all()
    np.testing.assert_array_equal(
        output.column("baseline_score"), frame.column("up_event_signed")
    )
    assert all(np.isfinite(output.column(f"value_{index}")).all() for index in range(4))


def test_joint_policy_maps_four_arms_and_skips_abstention() -> None:
    view, _frame = _model_frame(rows=3)
    origin = StageOrigin("uls_joint_action_forest_v1", 1, {})
    alternatives = {
        f"value_{index}": np.array([float(index == 0), 0.0, float(index == 3)])
        for index in range(4)
    }
    output = _make_model_output_frame(
        view=view,
        keys=np.array([1_000, 1_001, 1_002], dtype=np.int64),
        columns={"score": np.array([1.0, np.nan, -1.0]), **alternatives},
        origins=dict.fromkeys(
            ("score", "value_0", "value_1", "value_2", "value_3"), origin
        ),
    )
    policy = DECISION_POLICIES.create(
        "uls_joint_bracket_exposure_v1",
        stop_loss_distance=8.0,
        value_0_distance=2.0,
        value_1_distance=4.0,
        value_2_distance=6.0,
        value_3_distance=8.0,
    )
    intent = policy.decide(output)
    np.testing.assert_array_equal(intent.keys, np.array([1_000, 1_002]))
    np.testing.assert_array_equal(intent.target_exposure, np.array([1.0, -1.0]))
    np.testing.assert_array_equal(intent.take_profit_distance, np.array([2.0, 8.0]))
    np.testing.assert_array_equal(intent.stop_loss_distance, np.array([8.0, 8.0]))


def test_joint_full_validation_covers_300_vectors_without_dynamic_duplicates() -> None:
    source_children = _expand_request(
        json.loads(NON_INTERRUPTIBLE_SOURCE_SWEEP.read_text(encoding="utf-8"))
    )
    source = {
        "configurations": [
            {
                "strategy_ordinal": ordinal,
                "params": cast("dict[str, Any]", source_children[ordinal][1])["features"][0][
                    "params"
                ],
            }
            for ordinal in range(300)
        ],
        "evaluation_execution": cast("dict[str, Any]", source_children[0][1])[
            "execution"
        ],
    }
    reference: dict[str, object] = {
        "bundle_id": "uls-joint-action-selector-v1-" + "1" * 20,
        "bundle_manifest_sha256": "2" * 64,
        "fitted_state_identity_key": "3" * 64,
        "fitted_state_artifact_sha256": "4" * 64,
    }
    solver: dict[str, object] = {
        "bootstrap": False,
        "max_depth": 8,
        "max_features": 0.75,
        "min_samples_leaf": 128,
        "n_estimators": 96,
        "random_seed": 20_260_826,
    }
    children = _expand_request(
        _joint_full_validation_request(
            source=source,
            reference=reference,
            solver=solver,
            selection_threshold=0.25,
        )
    )
    assert len(children) == 1_500
    expanded = [cast("dict[str, Any]", child) for _ordinal, child in children]
    for strategy_ordinal in (0, 149, 299):
        group = expanded[strategy_ordinal * 5 : strategy_ordinal * 5 + 5]
        assert [child["policies"][0]["name"] for child in group] == [
            "uls_joint_bracket_exposure_v1",
            "bracket_threshold_v1",
            "bracket_threshold_v1",
            "bracket_threshold_v1",
            "bracket_threshold_v1",
        ]
        assert [
            float.fromhex(child["execution"]["take_profit_pips"]["__float_hex__"])
            for child in group
        ] == [800.0, 200.0, 400.0, 600.0, 800.0]
        assert {child["execution"]["execution_id"] for child in group} == {
            "bracket_non_interruptible_v1"
        }
        assert len(
            {
                json.dumps(child["features"][0]["params"], sort_keys=True)
                for child in group
            }
        ) == 1


def test_joint_single_validation_uses_only_train_selected_deployment() -> None:
    source_children = _expand_request(
        json.loads(NON_INTERRUPTIBLE_SOURCE_SWEEP.read_text(encoding="utf-8"))
    )
    deployment_ordinal = 141
    configurations = [
        {
            "strategy_ordinal": ordinal,
            "params": cast("dict[str, Any]", source_children[ordinal][1])["features"][0][
                "params"
            ],
        }
        for ordinal in range(300)
    ]
    source = {
        "configurations": configurations,
        "deployment": {
            "strategy_ordinal": deployment_ordinal,
            "feature_params": configurations[deployment_ordinal]["params"],
        },
        "evaluation_execution": cast("dict[str, Any]", source_children[0][1])[
            "execution"
        ],
    }
    reference: dict[str, object] = {
        "bundle_id": "uls-joint-action-selector-v1-" + "1" * 20,
        "bundle_manifest_sha256": "2" * 64,
        "fitted_state_identity_key": "3" * 64,
        "fitted_state_artifact_sha256": "4" * 64,
    }
    solver: dict[str, object] = {
        "bootstrap": False,
        "max_depth": 8,
        "max_features": 0.75,
        "min_samples_leaf": 128,
        "n_estimators": 96,
        "random_seed": 20_260_826,
    }
    children = _expand_request(
        _joint_single_validation_request(
            source=source,
            reference=reference,
            solver=solver,
            selection_threshold=0.25,
        )
    )
    assert len(children) == 1
    child = cast("dict[str, Any]", children[0][1])
    assert child["dataset"]["role"] == "validation"
    assert child["features"][0]["params"] == configurations[deployment_ordinal]["params"]
    assert child["policies"] == [
        {
            "name": "uls_joint_bracket_exposure_v1",
            "params": {
                "stop_loss_distance": {"__float_hex__": 8.0.hex()},
                "value_0_distance": {"__float_hex__": 2.0.hex()},
                "value_1_distance": {"__float_hex__": 4.0.hex()},
                "value_2_distance": {"__float_hex__": 6.0.hex()},
                "value_3_distance": {"__float_hex__": 8.0.hex()},
            },
            "version": 1,
        }
    ]
    assert child["fit_mode"] == {"mode": "frozen_state_v1", **reference}
    assert child["execution"]["execution_id"] == "bracket_non_interruptible_v1"


def test_joint_full_validation_serializes_undefined_rate_as_null() -> None:
    names = (
        "trade_count",
        "gross_pnl",
        "total_cost",
        "net_pnl",
        "win_rate",
        "ambiguous_exit_rate",
        "gapped_exit_rate",
        "boundary_forced_close_rate",
        "max_drawdown",
    )
    summary: dict[str, object] = {
        "entries": [
            {
                "plane": "B",
                "cost_semantics_unverified": True,
                "metrics": [
                    {
                        "name": name,
                        "value_float64_bits": struct.pack(
                            ">d",
                            float("nan")
                            if name in {"net_pnl", "win_rate"}
                            else float(index),
                        ).hex(),
                    }
                    for index, name in enumerate(names[:5])
                ],
            },
            {
                "plane": "B",
                "cost_semantics_unverified": True,
                "metrics": [
                    {
                        "name": name,
                        "value_float64_bits": struct.pack(
                            ">d",
                            float("nan") if name == "max_drawdown" else float(index),
                        ).hex(),
                    }
                    for index, name in enumerate(names[5:], start=5)
                ],
            },
        ]
    }
    metrics = _serializable_metrics(summary)
    assert metrics["win_rate"] is None
    assert metrics["net_pnl"] == 0.0
    assert metrics["max_drawdown"] == 0.0
    rows: list[dict[str, float | None]] = [
        {"max_drawdown": None},
        {"max_drawdown": 4.0},
    ]
    assert _aggregate_metric(rows, name="max_drawdown", median=False) == 4.0
