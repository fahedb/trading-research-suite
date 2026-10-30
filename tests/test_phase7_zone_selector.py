"""Phase-7 corpus, ridge, adaptive-policy, and frozen-config contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import textwrap
import zipfile
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest

import trading_research.experiments.orchestration as orchestration
import trading_research.experiments.training_bundles as training_bundles
from trading_research.artifacts.container import _ArtifactContainerError, _canonical_json
from trading_research.artifacts.fitted import (
    _bind_fitted_state,
    _CorpusFittedStateIdentity,
    _decode_fitted_state,
    _encode_fitted_state,
)
from trading_research.artifacts.identity import PlatformIdentity
from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    CorpusModelInputs,
    DatasetIdentity,
    FittedPayload,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewSpan,
)
from trading_research.core.research import _make_corpus_model_inputs
from trading_research.core.research_frames import _make_model_output_frame
from trading_research.decisions import DECISION_POLICIES
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import _execute_frozen_fold
from trading_research.experiments.resources import _THREAD_CLAMPS
from trading_research.experiments.runs import RunState
from trading_research.experiments.sweeps import _expand_request
from trading_research.experiments.training_bundles import (
    _begin_bundle_lifecycle,
    _build_zone_selector_bundle,
    _fail_bundle_lifecycle,
    _FrozenReference,
    _lifecycle_bytes,
    _load_frozen_state,
    _publish_new_bundle,
    _transition_bundle_state,
    _verify_bundle_files,
)
from trading_research.models import CORPUS_RESEARCH_MODELS
from trading_research.models.execution import (
    _fit_corpus_model_payload,
    _snapshot_corpus_model,
)
from trading_research.models.zone_selector import _FEATURE_NAMES, _INPUT_NAMES, _TARGET_NAMES
from trading_research.ops.phase7_validation import (
    _assert_frozen_execution_identity,
    _assert_full_child_pairing,
    _family_summary,
    _feature_params,
    _freeze_validation_specs,
    _normalize_validation_metrics,
    _verify_freeze_directory,
)

if TYPE_CHECKING:
    from pathlib import Path


def _corpus(rows: int = 6) -> CorpusModelInputs:
    axis = np.linspace(-1.0, 1.0, rows, dtype=np.float64)
    inputs = {
        name: (
            axis + float(index) / 10.0
            if index < 4
            else np.full(rows, float(index), dtype=np.float64)
        )
        for index, name in enumerate(_INPUT_NAMES)
    }
    targets = {
        name: (float(index + 1) * axis + float(index)).astype(np.float64)
        for index, name in enumerate(_TARGET_NAMES)
    }
    return _make_corpus_model_inputs(
        inputs=inputs,
        targets=targets,
        weights=np.linspace(0.5, 1.5, rows, dtype=np.float64),
    )


def _view() -> ViewSpan:
    domain = ClockDomain("phase7.test")
    source = DatasetIdentity("TRAIN_PHASE7", "1" * 64)

    def key(value: int) -> NativeTimeKey:
        return NativeTimeKey(value, TimeUnit.SECONDS, domain)

    return ViewSpan((source,), TimeUnit.SECONDS, domain, key(10), key(10), key(12))


def test_corpus_model_inputs_are_platform_only_detached_and_read_only() -> None:
    with pytest.raises(TypeError, match="corpus platform"):
        CorpusModelInputs()

    source = np.array([1.0, 2.0], dtype=np.float64)
    corpus = _make_corpus_model_inputs(
        inputs={"x": source},
        targets={"value": np.array([3.0, 4.0], dtype=np.float64)},
        weights=np.ones(2, dtype=np.float64),
    )
    source[0] = 99.0
    assert corpus.input("x").tolist() == [1.0, 2.0]
    assert not corpus.input("x").flags.writeable
    assert not corpus.target("value").flags.writeable
    assert not corpus.weights.flags.writeable
    with pytest.raises(ContractViolationError, match="strictly positive"):
        _make_corpus_model_inputs(
            inputs={"x": np.ones(2, dtype=np.float64)},
            targets={"value": np.ones(2, dtype=np.float64)},
            weights=np.array([1.0, 0.0], dtype=np.float64),
        )


def test_zone_ridge_fit_is_exact_and_constant_columns_use_unit_scale() -> None:
    model = CORPUS_RESEARCH_MODELS.create("zone_ridge_selector_v1", ridge_lambda=1.0)
    plan = _snapshot_corpus_model(model)
    first = _fit_corpus_model_payload(plan, inputs=_corpus())
    second = _fit_corpus_model_payload(plan, inputs=_corpus())

    assert first.metadata == second.metadata
    assert first.array_names == ("coefficients", "input_means", "input_scales")
    assert all(np.array_equal(first.array(name), second.array(name)) for name in first.array_names)
    assert np.all(first.array("input_scales")[4:] == 1.0)
    assert first.array("coefficients").shape == (len(_INPUT_NAMES) + 1, 3)


def test_oversized_run_frame_uses_deterministic_bounded_deflate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _make_model_output_frame(
        view=_view(),
        keys=np.array([10, 11, 12], dtype=np.int64),
        columns={"score": np.array([1.0, 0.0, -1.0], dtype=np.float64)},
        origins={"score": StageOrigin("test_stage", 1, {})},
    )
    captured: list[bytes] = []

    def capture(**kwargs: object) -> None:
        captured.append(cast("bytes", kwargs["payload"]))

    monkeypatch.setattr(orchestration, "_RUN_FILE_LIMIT_BYTES", 1)
    monkeypatch.setattr(orchestration, "_record_run_payload", capture)
    persist = orchestration._persist_column_frame
    for _iteration in range(2):
        persist(
            runtime=cast("Any", SimpleNamespace(_runs_root=tmp_path)),
            run_id="0" * 26,
            name="model-output.npz",
            kind="model_output",
            identity_key="1" * 64,
            frame=frame,
        )

    assert captured[0] == captured[1]
    with zipfile.ZipFile(io.BytesIO(captured[0])) as archive:
        assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in archive.infolist())
        assert sum(info.file_size for info in archive.infolist()) < 536_870_912
    with np.load(io.BytesIO(captured[0]), allow_pickle=False) as archive:
        assert archive["column_0000"].tolist() == [1.0, 0.0, -1.0]


def test_corpus_model_rejects_an_ambiguous_ordinary_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    model = CORPUS_RESEARCH_MODELS.create("zone_ridge_selector_v1", ridge_lambda=1.0)
    monkeypatch.setattr(type(model), "fit", lambda *args: None, raising=False)

    with pytest.raises(ContractViolationError, match="fit mode is ambiguous"):
        _snapshot_corpus_model(model)


def test_adaptive_policy_uses_first_choice_on_exact_ties_and_emits_fixed_stop() -> None:
    view = _view()
    origin = StageOrigin("zone_ridge_selector_v1", 1, {"ridge_lambda": 1.0})
    columns = {
        "score": np.array([1.0, -1.0, 1.0], dtype=np.float64),
        "value_0": np.array([0.0, 0.0, 1.0], dtype=np.float64),
        "value_1": np.array([0.0, 2.0, 1.0], dtype=np.float64),
        "value_2": np.array([0.0, 1.0, 1.0], dtype=np.float64),
    }
    output = _make_model_output_frame(
        view=view,
        keys=np.array([10, 11, 12], dtype=np.int64),
        columns=columns,
        origins=dict.fromkeys(columns, origin),
    )
    policy = DECISION_POLICIES.create(
        "zone_adaptive_bracket_exposure_v1",
        stop_loss_distance=4.0,
        value_0_distance=2.0,
        value_1_distance=4.0,
        value_2_distance=8.0,
    )
    intent = policy.decide(output)

    assert intent.target_exposure.tolist() == [1.0, -1.0, 1.0]
    assert intent.take_profit_distance is not None
    assert intent.stop_loss_distance is not None
    assert intent.take_profit_distance.tolist() == [2.0, 4.0, 2.0]
    assert intent.stop_loss_distance.tolist() == [4.0, 4.0, 4.0]


def test_corpus_fitted_identity_round_trips_without_weakening_view_fit_format() -> None:
    identity = _CorpusFittedStateIdentity(
        plugin_id="zone_ridge_selector_v1",
        plugin_version=1,
        plugin_params={"ridge_lambda": 1.0},
        inference_feature_names=("zt_event_signed",),
        corpus_identity_key="2" * 64,
        corpus_artifact_sha256="3" * 64,
        source_manifest_sha256="4" * 64,
        target_spec_id="matched_bracket_gross_r_v1",
        target_spec_version=1,
        target_spec_params={"risk_distance": 4.0},
        training_spec={"ridge_lambda": 1.0},
        platform_identity=PlatformIdentity("a" * 40, False, None),
        dependency_lock_sha256="5" * 64,
        comparison_tolerance=None,
    )
    state = _bind_fitted_state(
        identity,
        FittedPayload(
            arrays={"coefficient": np.array([1.25], dtype=np.float64)},
            metadata={"state_schema": "test"},
        ),
    )
    encoded = _encode_fitted_state(identity, state)
    restored = _decode_fitted_state(encoded, expected_identity_key=identity.identity_key())

    assert restored.identity_key() == identity.identity_key()
    assert restored.payload.metadata == {"state_schema": "test"}
    assert restored.payload.array("coefficient").tolist() == [1.25]
    with pytest.raises(_ArtifactContainerError):
        _decode_fitted_state(
            encoded[:-8] + b"corrupt!", expected_identity_key=identity.identity_key()
        )


def _v2_document() -> dict[str, object]:
    return {
        "schema": "research-experiment-config-v2",
        "integrity_mode": "full",
        "dataset": {
            "dataset_id": "VALIDATION_PHASE7",
            "manifest_sha256": "1" * 64,
            "role": "validation",
        },
        "split_policy": {
            "adr": "ADR-0012",
            "policy_version": "v1",
            "policy_sha256": "2" * 64,
        },
        "bounds": {"context_start": 10, "eval_start": 10, "eval_end": 12},
        "features": [{"name": "zone_trader_v13", "version": 1, "params": {}}],
        "representation": None,
        "model": {
            "name": "zone_ridge_selector_v1",
            "version": 1,
            "params": {"ridge_lambda": {"__float_hex__": (1.0).hex()}},
        },
        "training_outcome": None,
        "evaluation_outcomes": [],
        "policies": [
            {
                "name": "zone_adaptive_bracket_exposure_v1",
                "version": 1,
                "params": {
                    "stop_loss_distance": {"__float_hex__": (4.0).hex()},
                    "value_0_distance": {"__float_hex__": (2.0).hex()},
                    "value_1_distance": {"__float_hex__": (4.0).hex()},
                    "value_2_distance": {"__float_hex__": (8.0).hex()},
                },
            }
        ],
        "evaluators": [],
        "execution": None,
        "trade_evaluators": [],
        "fit_mode": {
            "mode": "frozen_state_v1",
            "bundle_id": "zone-tp-selector-v1-" + "3" * 20,
            "bundle_manifest_sha256": "4" * 64,
            "fitted_state_identity_key": "5" * 64,
            "fitted_state_artifact_sha256": "6" * 64,
        },
        "seed": None,
        "access_reason": None,
        "declared_lockbox_evaluation": False,
    }


def test_v2_config_has_one_frozen_span_reference_and_no_fold_or_training_channel() -> None:
    config = _config_from_document(_v2_document())
    document = config.document()

    assert document["schema"] == "research-experiment-config-v2"
    assert "folds" not in document
    assert document["fit_mode"] == _v2_document()["fit_mode"]
    assert json.loads(config.canonical_json()) == document

    leaked = _v2_document()
    leaked["training_outcome"] = {
        "name": "forward_log_return_v1",
        "version": 1,
        "params": {
            "boundary_policy": "nan",
            "decision_delay_rows": 1,
            "horizon_rows": 1,
            "reference_price_column": "close",
        },
    }
    with pytest.raises(ContractViolationError, match="cannot declare training_outcome"):
        _config_from_document(leaked)


def test_frozen_fold_loads_and_infers_without_any_fit_call() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(_execute_frozen_fold)))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert {
        "_snapshot_corpus_model",
        "_load_frozen_state",
        "_execute_inference_and_simulation",
    } <= calls
    assert {
        "_fit_model",
        "_fit_representation",
        "_compute_training_targets",
        "_fit_corpus_model_payload",
    }.isdisjoint(calls)


def _zone_params(index: int) -> dict[str, object]:
    return {
        "anchor_grid_seconds": 3600,
        "anchor_phase_offset_seconds": 0,
        "bar_grid_seconds": 300,
        "bar_phase_offset_seconds": 0,
        "break_buffer_atr": {"__float_hex__": (0.5).hex()},
        "confirmation_mode": "rejection_wick",
        "max_bars_wait": 6,
        "max_base_atr": {"__float_hex__": (0.8).hex()},
        "max_base_body": {"__float_hex__": (0.5).hex()},
        "max_zone_age_rows": 5000,
        "max_zones": 15 + index,
        "min_fvg_atr": {"__float_hex__": (0.25).hex()},
        "min_move_atr": {"__float_hex__": (1.2).hex()},
        "minimum_wick": {"__float_hex__": (0.35).hex()},
        "reward_risk_target": {"__float_hex__": (2.0).hex()},
        "stop_buffer_atr": {"__float_hex__": (0.5).hex()},
        "use_demand_supply": False,
        "use_imbalance": False,
        "use_volume_zones": True,
        "vol_len": 20,
        "vol_mult": {"__float_hex__": (2.0).hex()},
        "w_atr": 300,
    }


def _evaluation_execution() -> dict[str, object]:
    return {
        "cost_binding": {
            "account_currency_per_quote_currency": {"__float_hex__": (1.0).hex()},
            "binding_digest": None,
            "binding_id": "unverified-audit-input-v1",
            "commission_basis": "per_round_turn",
            "commission_per_lot": {"__float_hex__": (6.0).hex()},
            "contract_size": {"__float_hex__": (100.0).hex()},
            "cost_semantics": "unverified",
            "execution_price_model": "recorded_path_unverified",
            "price_units_per_pip": {"__float_hex__": (0.01).hex()},
            "spread_pips": {"__float_hex__": (20.0).hex()},
        },
        "execution_id": "bracket_single_position_v1",
        "lot_size": {"__float_hex__": (0.01).hex()},
        "stop_loss_pips": None,
        "take_profit_pips": None,
        "version": 1,
    }


def _fake_bundle(tmp_path: Path, *, study_version: str = "v1") -> Path:
    bundle = tmp_path / (f"zone-tp-selector-{study_version}-" + "0" * 20)
    bundle.mkdir()
    configurations: list[dict[str, object]] = []
    for index in range(100):
        params = _zone_params(index)
        digest = hashlib.sha256(_canonical_json(params).encode("ascii")).hexdigest()
        row: dict[str, object] = {
            "feature_config_digest": digest,
            "feature_origin_digest": f"{index + 1:064x}",
            "params": params,
        }
        if study_version == "v2":
            row["context_origin_digest"] = f"{index + 101:064x}"
        configurations.append(row)
    configurations.sort(key=lambda item: cast("str", item["feature_config_digest"]))
    source = {
        "schema": f"zone-selector-source-manifest-{study_version}",
        "configurations": configurations,
        "evaluation_execution": _evaluation_execution(),
    }
    payloads = {
        "corpus.npz": b"corpus",
        "fitted-state.npz": b"fitted",
        "source-manifest.json": (
            json.dumps(source, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii"),
        "training-report.json": b"{}\n",
    }
    for name, payload in payloads.items():
        (bundle / name).write_bytes(payload)
    lifecycle = _lifecycle_bytes(("PLANNED", "RUNNING", "COMPLETE"))
    (bundle / "lifecycle.jsonl").write_bytes(lifecycle)
    bundle_id = bundle.name
    manifest = {
        "schema": "research-training-bundle-manifest-v1",
        "bundle_id": bundle_id,
        "state": "COMPLETE",
        "files": {
            name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()
        },
        "lifecycle_sha256": hashlib.sha256(lifecycle).hexdigest(),
        "corpus_identity_key": "1" * 64,
        "fitted_state_identity_key": "2" * 64,
        "platform": {"git_commit": "a" * 40, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": "3" * 64,
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    (bundle / "state.json").write_text(
        json.dumps(
            {
                "schema": "research-training-bundle-state-v1",
                "bundle_id": bundle_id,
                "state": "COMPLETE",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    return bundle


def _valid_frozen_bundle(tmp_path: Path) -> tuple[Path, _FrozenReference]:
    bundle = _fake_bundle(tmp_path)
    identity = _CorpusFittedStateIdentity(
        plugin_id="zone_ridge_selector_v1",
        plugin_version=1,
        plugin_params={"ridge_lambda": 1.0},
        inference_feature_names=_FEATURE_NAMES,
        corpus_identity_key="1" * 64,
        corpus_artifact_sha256="2" * 64,
        source_manifest_sha256="3" * 64,
        target_spec_id="matched_bracket_gross_r_v1",
        target_spec_version=1,
        target_spec_params={"risk_distance": 4.0},
        training_spec={"ridge_lambda": 1.0},
        platform_identity=PlatformIdentity("a" * 40, False, None),
        dependency_lock_sha256="4" * 64,
        comparison_tolerance=None,
    )
    coefficients = np.zeros((len(_INPUT_NAMES) + 1, 3), dtype=np.float64)
    state = _bind_fitted_state(
        identity,
        FittedPayload(
            arrays={
                "coefficients": coefficients,
                "input_means": np.zeros(len(_INPUT_NAMES), dtype=np.float64),
                "input_scales": np.ones(len(_INPUT_NAMES), dtype=np.float64),
            },
            metadata={
                "input_schema": ",".join(_INPUT_NAMES),
                "state_schema": "zone_ridge_selector_v1",
                "target_schema": ",".join(_TARGET_NAMES),
            },
        ),
    )
    fitted = _encode_fitted_state(identity, state)
    (bundle / "fitted-state.npz").write_bytes(fitted)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="ascii"))
    manifest["fitted_state_identity_key"] = identity.identity_key()
    manifest["files"]["fitted-state.npz"] = hashlib.sha256(fitted).hexdigest()
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    (bundle / "manifest.json").write_bytes(manifest_bytes)
    return bundle, _FrozenReference(
        bundle.name,
        hashlib.sha256(manifest_bytes).hexdigest(),
        identity.identity_key(),
        hashlib.sha256(fitted).hexdigest(),
    )


def test_validation_freeze_expands_four_exact_paired_100_child_families_and_hashes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "validation-freeze"
    freeze = _freeze_validation_specs(bundle_dir=_fake_bundle(tmp_path), output_dir=output)

    assert freeze["expected_total_children"] == 400
    expected_digests = cast("list[str]", freeze["configuration_order"])
    families = cast("dict[str, dict[str, object]]", freeze["families"])
    for family in ("dynamic", "fixed_200", "fixed_400", "fixed_800"):
        row = families[family]
        request_file = cast("str", row["request_file"])
        request = json.loads((output / request_file).read_text(encoding="ascii"))
        children = _expand_request(request)
        assert len(children) == 100
        assert [
            hashlib.sha256(
                _canonical_json(_feature_params(child)).encode("ascii")
            ).hexdigest()
            for _ordinal, child in children
        ] == expected_digests

    dynamic = json.loads(
        (output / cast("str", families["dynamic"]["request_file"])).read_text(
            encoding="ascii"
        )
    )
    assert "folds" not in dynamic["base_config"]
    assert dynamic["base_config"]["training_outcome"] is None
    assert dynamic["base_config"]["fit_mode"]["mode"] == "frozen_state_v1"

    request_path = output / cast("str", families["dynamic"]["request_file"])
    request_path.chmod(0o644)
    request_path.write_bytes(request_path.read_bytes() + b" ")
    with pytest.raises(ContractViolationError, match="hash differs"):
        _verify_freeze_directory(output)


def test_v2_validation_freeze_pairs_zone_context_and_forest_in_every_family(
    tmp_path: Path,
) -> None:
    output = tmp_path / "validation-freeze-v2"
    freeze = _freeze_validation_specs(
        bundle_dir=_fake_bundle(tmp_path, study_version="v2"),
        output_dir=output,
    )

    assert freeze["schema"] == "zone-selector-validation-freeze-v2"
    assert freeze["study_version"] == "v2"
    families = cast("dict[str, dict[str, object]]", freeze["families"])
    for family in ("dynamic", "fixed_200", "fixed_400", "fixed_800"):
        row = families[family]
        assert cast("str", row["request_file"]).endswith(f"-{family.replace('_', '-')}-v2.json")
        request = json.loads(
            (output / cast("str", row["request_file"])).read_text(encoding="ascii")
        )
        assert len(request["axes"]) == 2 * len(training_bundles._ZONE_PARAMETER_NAMES)
        children = _expand_request(request)
        assert len(children) == 100
        for _ordinal, child in children:
            raw_features = cast("list[dict[str, object]]", child["features"])
            zone, context = raw_features
            assert zone["name"] == "zone_trader_v13"
            assert context["name"] == "zone_context_v1"
            assert zone["params"] == context["params"]
            assert child["model"] == {
                "name": "zone_forest_selector_v2",
                "version": 1,
                "params": {
                    "bootstrap": False,
                    "max_depth": 8,
                    "max_features": {"__float_hex__": (0.75).hex()},
                    "min_samples_leaf": 64,
                    "n_estimators": 96,
                    "random_seed": 20_260_823,
                },
            }


def test_frozen_loader_accepts_only_complete_verified_bundle_truth(tmp_path: Path) -> None:
    runs_root = tmp_path.resolve()
    bundle_root = runs_root / "training-bundles"
    bundle_root.mkdir()
    bundle, reference = _valid_frozen_bundle(bundle_root)
    state = _load_frozen_state(
        runs_root=runs_root,
        reference=reference,
        expected_model_id="zone_ridge_selector_v1",
        expected_model_version=1,
        expected_model_params={"ridge_lambda": 1.0},
    )
    assert state.identity_key() == reference.fitted_state_identity_key
    bad_artifact = _FrozenReference(
        reference.bundle_id,
        reference.manifest_sha256,
        reference.fitted_state_identity_key,
        "0" * 64,
    )
    with pytest.raises(ContractViolationError, match="artifact digest differs"):
        _load_frozen_state(
            runs_root=runs_root,
            reference=bad_artifact,
            expected_model_id="zone_ridge_selector_v1",
            expected_model_version=1,
            expected_model_params={"ridge_lambda": 1.0},
        )

    (bundle / "state.json").write_text(
        json.dumps(
            {
                "schema": "research-training-bundle-state-v1",
                "bundle_id": bundle.name,
                "state": "RUNNING",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    with pytest.raises(ContractViolationError, match="state is malformed"):
        _load_frozen_state(
            runs_root=runs_root,
            reference=reference,
            expected_model_id="zone_ridge_selector_v1",
            expected_model_version=1,
            expected_model_params={"ridge_lambda": 1.0},
        )

    missing = _FrozenReference(
        "zone-tp-selector-v1-" + "9" * 20,
        "8" * 64,
        "7" * 64,
        "6" * 64,
    )
    with pytest.raises(ContractViolationError, match="cache is not authority"):
        _load_frozen_state(
            runs_root=runs_root,
            reference=missing,
            expected_model_id="zone_ridge_selector_v1",
            expected_model_version=1,
            expected_model_params={"ridge_lambda": 1.0},
        )


def test_validation_report_pairing_refuses_any_non_feature_config_change(
    tmp_path: Path,
) -> None:
    output = tmp_path / "validation-freeze"
    freeze = _freeze_validation_specs(bundle_dir=_fake_bundle(tmp_path), output_dir=output)
    families = cast("dict[str, dict[str, object]]", freeze["families"])
    request = json.loads(
        (output / cast("str", families["dynamic"]["request_file"])).read_text(
            encoding="ascii"
        )
    )
    planned = tuple(child for _ordinal, child in _expand_request(request))
    _assert_full_child_pairing(planned, frozen_request=request)
    changed = tuple(json.loads(json.dumps(child)) for child in planned)
    changed[0]["bounds"]["eval_end"] -= 60

    with pytest.raises(ContractViolationError, match="full frozen request config"):
        _assert_full_child_pairing(changed, frozen_request=request)


def test_bundle_worker_refuses_unclamped_blas_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _THREAD_CLAMPS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ContractViolationError, match="thread clamps"):
        _build_zone_selector_bundle(
            runs_root=tmp_path.resolve(),
            repository_root=tmp_path.resolve(),
            dependency_lock_path=(tmp_path / "uv.lock").resolve(),
        )


def test_bundle_lifecycle_records_planned_running_complete_or_failed(tmp_path: Path) -> None:
    root = (tmp_path / "bundles").resolve()
    complete_id = "zone-tp-selector-v1-" + "1" * 20
    _begin_bundle_lifecycle(bundle_root=root, bundle_id=complete_id)
    complete = root / complete_id
    assert json.loads((complete / "state.json").read_text())["state"] == "RUNNING"
    _transition_bundle_state(complete, "COMPLETE")
    assert json.loads((complete / "state.json").read_text())["state"] == "COMPLETE"
    lifecycle_states = [
        json.loads(line)["state"]
        for line in (complete / "lifecycle.jsonl").read_text().splitlines()
    ]
    assert lifecycle_states == [
        "PLANNED",
        "RUNNING",
        "COMPLETE",
    ]

    failed_id = "zone-tp-selector-v1-" + "2" * 20
    _begin_bundle_lifecycle(bundle_root=root, bundle_id=failed_id)
    failed = root / failed_id
    _fail_bundle_lifecycle(failed)
    assert json.loads((failed / "state.json").read_text())["state"] == "FAILED"
    assert json.loads((failed / "manifest.json").read_text())["state"] == "FAILED"


def test_bundle_registration_collision_never_fails_the_existing_owner(tmp_path: Path) -> None:
    root = (tmp_path / "bundles").resolve()
    bundle_id = "zone-tp-selector-v1-" + "6" * 20
    _begin_bundle_lifecycle(bundle_root=root, bundle_id=bundle_id)
    bundle = root / bundle_id
    state_before = (bundle / "state.json").read_bytes()
    lifecycle_before = (bundle / "lifecycle.jsonl").read_bytes()

    with pytest.raises(ContractViolationError, match="target already exists"):
        _begin_bundle_lifecycle(bundle_root=root, bundle_id=bundle_id)

    assert (bundle / "state.json").read_bytes() == state_before
    assert json.loads(state_before)["state"] == "RUNNING"
    assert (bundle / "lifecycle.jsonl").read_bytes() == lifecycle_before
    assert lifecycle_before == _lifecycle_bytes(("PLANNED", "RUNNING"))
    assert not (bundle / "manifest.json").exists()


def test_bundle_failure_after_complete_manifest_reconciles_terminal_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "bundles").resolve()
    bundle_id = "zone-tp-selector-v1-" + "3" * 20
    _begin_bundle_lifecycle(bundle_root=root, bundle_id=bundle_id)
    bundle = root / bundle_id
    complete_manifest: dict[str, object] = {
        "schema": "research-training-bundle-manifest-v1",
        "bundle_id": bundle_id,
        "state": "COMPLETE",
    }
    original_transition = training_bundles._transition_bundle_state

    def fail_complete_transition(destination: Path, state: str) -> None:
        if state == "COMPLETE":
            raise OSError("injected failure after COMPLETE manifest publication")
        original_transition(destination, state)

    monkeypatch.setattr(
        training_bundles,
        "_transition_bundle_state",
        fail_complete_transition,
    )
    with pytest.raises(OSError, match="injected failure"):
        _publish_new_bundle(
            destination=bundle,
            artifacts={},
            manifest=complete_manifest,
            manifest_bytes=(
                json.dumps(complete_manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii"),
        )
    assert json.loads((bundle / "manifest.json").read_text())["state"] == "COMPLETE"

    _fail_bundle_lifecycle(bundle)
    assert json.loads((bundle / "state.json").read_text())["state"] == "FAILED"
    assert json.loads((bundle / "manifest.json").read_text())["state"] == "FAILED"
    assert (bundle / "lifecycle.jsonl").read_bytes() == _lifecycle_bytes(
        ("PLANNED", "RUNNING", "FAILED")
    )


def test_bundle_failure_during_complete_state_replace_reconciles_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "bundles").resolve()
    bundle_id = "zone-tp-selector-v1-" + "4" * 20
    _begin_bundle_lifecycle(bundle_root=root, bundle_id=bundle_id)
    bundle = root / bundle_id
    complete_manifest: dict[str, object] = {
        "schema": "research-training-bundle-manifest-v1",
        "bundle_id": bundle_id,
        "state": "COMPLETE",
    }
    original_replace = training_bundles._atomic_replace_bytes
    injected = False

    def fail_complete_state_replace(path: Path, payload: bytes) -> None:
        nonlocal injected
        if path.name == "state.json" and b'"state":"COMPLETE"' in payload and not injected:
            injected = True
            raise OSError("injected COMPLETE state replacement failure")
        original_replace(path, payload)

    monkeypatch.setattr(
        training_bundles,
        "_atomic_replace_bytes",
        fail_complete_state_replace,
    )
    with pytest.raises(OSError, match="state replacement failure"):
        _publish_new_bundle(
            destination=bundle,
            artifacts={},
            manifest=complete_manifest,
            manifest_bytes=(
                json.dumps(complete_manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii"),
        )
    assert (bundle / "lifecycle.jsonl").read_bytes() == _lifecycle_bytes(
        ("PLANNED", "RUNNING", "COMPLETE")
    )

    _fail_bundle_lifecycle(bundle)
    assert json.loads((bundle / "state.json").read_text())["state"] == "FAILED"
    assert json.loads((bundle / "manifest.json").read_text())["state"] == "FAILED"
    assert (bundle / "lifecycle.jsonl").read_bytes() == _lifecycle_bytes(
        ("PLANNED", "RUNNING", "FAILED")
    )


def test_bundle_failure_after_complete_state_reconciles_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "bundles").resolve()
    bundle_id = "zone-tp-selector-v1-" + "5" * 20
    _begin_bundle_lifecycle(bundle_root=root, bundle_id=bundle_id)
    bundle = root / bundle_id
    complete_manifest: dict[str, object] = {
        "schema": "research-training-bundle-manifest-v1",
        "bundle_id": bundle_id,
        "state": "COMPLETE",
    }

    def fail_post_transition_verification(
        _destination: Path,
        _manifest: dict[str, object],
    ) -> None:
        raise OSError("injected post-transition verification failure")

    monkeypatch.setattr(
        training_bundles,
        "_verify_bundle_files",
        fail_post_transition_verification,
    )
    with pytest.raises(OSError, match="post-transition verification failure"):
        _publish_new_bundle(
            destination=bundle,
            artifacts={},
            manifest=complete_manifest,
            manifest_bytes=(
                json.dumps(complete_manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii"),
        )
    assert json.loads((bundle / "state.json").read_text())["state"] == "COMPLETE"

    _fail_bundle_lifecycle(bundle)
    assert json.loads((bundle / "state.json").read_text())["state"] == "FAILED"
    assert json.loads((bundle / "manifest.json").read_text())["state"] == "FAILED"
    assert (bundle / "lifecycle.jsonl").read_bytes() == _lifecycle_bytes(
        ("PLANNED", "RUNNING", "FAILED")
    )


def test_complete_bundle_verification_requires_exact_lifecycle_sequence(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path)
    malformed = _lifecycle_bytes(("COMPLETE",))
    (bundle / "lifecycle.jsonl").write_bytes(malformed)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["lifecycle_sha256"] = hashlib.sha256(malformed).hexdigest()

    with pytest.raises(ContractViolationError, match="lifecycle sequence differs"):
        _verify_bundle_files(bundle, manifest)


def test_validation_execution_identity_must_equal_freeze(tmp_path: Path) -> None:
    output = tmp_path / "validation-freeze"
    freeze = _freeze_validation_specs(bundle_dir=_fake_bundle(tmp_path), output_dir=output)
    platform = cast("dict[str, object]", freeze["bundle_platform"])
    dependency = cast("str", freeze["dependency_lock_sha256"])
    _assert_frozen_execution_identity(
        platform=platform,
        dependency_lock_sha256=dependency,
        freeze=freeze,
    )

    changed_platform = {**platform, "git_commit": "b" * 40}
    with pytest.raises(ContractViolationError, match="differs from the freeze"):
        _assert_frozen_execution_identity(
            platform=changed_platform,
            dependency_lock_sha256=dependency,
            freeze=freeze,
        )
    with pytest.raises(ContractViolationError, match="differs from the freeze"):
        _assert_frozen_execution_identity(
            platform=platform,
            dependency_lock_sha256="0" * 64,
            freeze=freeze,
        )


def test_zero_trade_validation_metrics_preserve_primary_zero_and_undefined_rates() -> None:
    names = {
        "trade_count",
        "gross_pnl",
        "total_cost",
        "net_pnl",
        "win_rate",
        "ambiguous_exit_rate",
        "gapped_exit_rate",
        "boundary_forced_close_rate",
        "gross_r_total",
        "gross_r_per_trade",
        "gross_r_max_drawdown",
        "choice_0_rate",
        "choice_1_rate",
        "choice_2_rate",
    }
    raw = {name: float("nan") for name in names}
    raw["trade_count"] = 0.0
    normalized = _normalize_validation_metrics(raw)

    assert normalized["gross_r_total"] == 0.0
    assert normalized["gross_r_max_drawdown"] == 0.0
    assert normalized["gross_r_per_trade"] is None
    assert normalized["win_rate"] is None
    assert normalized["choice_0_rate"] is None

    malformed = dict(raw)
    malformed["gross_r_total"] = 0.0
    with pytest.raises(ContractViolationError, match="unexpected defined metric"):
        _normalize_validation_metrics(malformed)
    nonempty = dict(raw)
    nonempty["trade_count"] = 1.0
    with pytest.raises(ContractViolationError, match="non-empty validation run"):
        _normalize_validation_metrics(nonempty)


def test_family_summary_counts_empty_configurations_without_biasing_rates() -> None:
    empty = {
        "trade_count": 0.0,
        "gross_r_total": 0.0,
        "gross_r_per_trade": None,
        "gross_r_max_drawdown": 0.0,
        "choice_0_rate": None,
        "choice_1_rate": None,
        "choice_2_rate": None,
        "ambiguous_exit_rate": None,
        "gapped_exit_rate": None,
        "boundary_forced_close_rate": None,
    }
    active = {
        "trade_count": 2.0,
        "gross_r_total": 3.0,
        "gross_r_per_trade": 1.5,
        "gross_r_max_drawdown": 1.0,
        "choice_0_rate": 0.5,
        "choice_1_rate": 0.5,
        "choice_2_rate": 0.0,
        "ambiguous_exit_rate": 0.0,
        "gapped_exit_rate": 0.5,
        "boundary_forced_close_rate": 0.0,
    }
    summary = _family_summary([{"metrics": empty}, {"metrics": active}])

    assert summary["mean_total_gross_r"] == 1.5
    assert summary["mean_gross_r_per_trade"] == 1.5
    assert summary["mean_choice_0_rate"] == 0.5
    assert summary["zero_trade_configurations"] == 1
    assert summary["defined_rate_configurations"] == 1


def test_corpus_discovery_refuses_scan_corruption_and_wrong_train_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        training_bundles,
        "_scan_run_projections",
        lambda *args, **kwargs: SimpleNamespace(_errors=(object(),), _projections=()),
    )
    with pytest.raises(ContractViolationError, match="reported corruption"):
        training_bundles._discover_primary_sources(tmp_path.resolve())

    projection = SimpleNamespace(
        _manifest=object(),
        _record=SimpleNamespace(_state=RunState.COMPLETE, _canonical=True),
        _resolved_config={
            "schema": "research-experiment-config-v1",
            "dataset": {
                "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
                "manifest_sha256": "f" * 64,
                "role": "train",
            },
        },
    )
    monkeypatch.setattr(
        training_bundles,
        "_scan_run_projections",
        lambda *args, **kwargs: SimpleNamespace(_errors=(), _projections=(projection,)),
    )
    with pytest.raises(ContractViolationError, match="dataset identity differs"):
        training_bundles._discover_primary_sources(tmp_path.resolve())
