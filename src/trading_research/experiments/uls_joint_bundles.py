"""Build one frozen joint ULS entry/TP model from the non-interruptible corpus."""

from __future__ import annotations

import argparse as _argparse
import io as _io
import math as _math
import re as _re
import sys as _sys
import time as _time
import typing as _typing
from collections import Counter as _Counter
from collections import defaultdict as _defaultdict
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

import numpy as _np

from trading_research.artifacts.container import _canonical_json, _validate_zip_envelope
from trading_research.artifacts.fitted import (
    _bind_fitted_state,
    _CorpusFittedStateIdentity,
    _encode_fitted_state,
)
from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.artifacts.identity import (
    _canonical_identity_json,
    _identity_key_from_canonical,
)
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import _make_corpus_model_inputs
from trading_research.experiments.resources import _assert_worker_environment
from trading_research.experiments.runs import RunState as _RunState
from trading_research.experiments.runs import _scan_run_projections
from trading_research.experiments.sweeps import _read_sweep_plan
from trading_research.experiments.training_bundles import (
    _assert_equal_payload,
    _begin_bundle_lifecycle,
    _bundle_reference_document,
    _clean_commit,
    _decoded_params,
    _exact_mapping,
    _fail_bundle_lifecycle,
    _json_bytes,
    _publish_new_bundle,
    _read_bundle_manifest,
    _sha256_bytes,
    _sha256_file,
    _verify_bundle_files,
)
from trading_research.experiments.uls_entry_bundles import (
    _read_feature_artifact,
    _read_observations,
    _sample_ids,
    _SourceRun,
)
from trading_research.models import CORPUS_RESEARCH_MODELS as _CORPUS_MODELS
from trading_research.models.execution import (
    _fit_corpus_model_payload,
    _snapshot_corpus_model,
)
from trading_research.models.reference import _ULS_EVENT_FEATURE, _ULS_VALUE_FEATURES
from trading_research.models.uls_entry_selector import (
    _CONTEXT_FEATURE_NAMES,
    _INPUT_NAMES,
    _predictor_rows_from_arrays,
)
from trading_research.models.uls_joint_selector import (
    _TARGET_NAMES,
    _TP_PIPS,
    _forest_predict,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
    from trading_research.core.research_frames import FittedPayload as _FittedPayload

__all__: list[str] = []

_SOURCE_SWEEP_ID = "uls-push-v7-train-non-interruptible-300x4-tp-v1"
_SOURCE_SPEC_DIGEST = "4e1d44228bb8744c9f85da2750072b8cd9ca694f29ff45b1c45c2970c9abccf7"
_SOURCE_EXECUTION_ID = "bracket_non_interruptible_v1"
_TRAIN_DATASET = {
    "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
    "manifest_sha256": "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
    "role": "train",
}
_STRATEGY_COUNT = 300
_EXPECTED_CHILDREN = _STRATEGY_COUNT * len(_TP_PIPS)
_MODEL_BASE_PARAMS: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "max_depth": 8,
    "max_features": 0.75,
    "min_samples_leaf": 128,
    "n_estimators": 96,
    "random_seed": 20_260_826,
}
_TRAINING_SPEC: _Mapping[str, _ParamValue] = {
    "fit_fraction": 0.70,
    "calibration_fraction": 0.15,
    "test_fraction": 0.15,
    "split": "unique_decision_keys_chronological_v1",
    "purge": "max_arm_exit_reaches_next_boundary_v1",
    "sample_identity": "feature_origin_intent_key_direction_v1",
    "complete_cases_only": True,
    "weighting": "inverse_episode_frequency_v1",
    "deployment_configuration_metadata": (
        "best_local_fixed_arm_total_net_pnl_on_fit_min_100_rows_v1"
    ),
    "fixed_comparator_selection": "best_fixed_arm_total_net_pnl_across_all_configurations_v2",
    "threshold_candidates": "nonnegative_all_configuration_max_prediction_quantiles_v2",
    "threshold_objective": "all_configuration_dynamic_total_net_pnl_on_calibration_v2",
    "threshold_empty_fallback": "zero_hurdle_sparse_selection_v1",
    "tie_break": "lowest_tp_first_v1",
    "no_trade_value": 0.0,
}
_TARGET_SPEC_PARAMS: _Mapping[str, _ParamValue] = {
    "arm_take_profit_pips": tuple(float(value) for value in _TP_PIPS),
    "stop_loss_pips": 800.0,
    "target_column": "net_pnl",
    "complete_cases_only": True,
    "boundary_forced_close_excluded": True,
    "execution_id": _SOURCE_EXECUTION_ID,
    "cost_semantics": "unverified",
}
_TARGET_SPEC_ID = "matched_joint_tp_net_pnl_4arm_v1"
_SOURCE_SCHEMA = "uls-joint-action-source-manifest-v1"
_CORPUS_FORMAT = "uls-joint-action-corpus-v1"
_REPORT_SCHEMA = "uls-joint-action-training-report-v1"
_BUNDLE_SCHEMA = "research-training-bundle-manifest-v1"


@_dataclass(frozen=True, slots=True)
class _ArmRun:
    strategy_ordinal: int
    tp_pips: int
    source: _SourceRun


@_dataclass(frozen=True, slots=True)
class _JointCorpus:
    sample_ids: _npt.NDArray[_np.uint8]
    origin_ids: _npt.NDArray[_np.uint8]
    decision_keys: _npt.NDArray[_np.int64]
    directions: _npt.NDArray[_np.int8]
    max_exit_keys: _npt.NDArray[_np.int64]
    strategy_ordinals: _npt.NDArray[_np.int16]
    weights: _npt.NDArray[_np.float64]
    inputs: dict[str, _npt.NDArray[_np.float64]]
    targets: dict[str, _npt.NDArray[_np.float64]]
    strategy_params: dict[int, dict[str, object]]
    source_manifest: dict[str, object]
    counts: dict[str, object]


def _build(
    *,
    runs_root: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
    context_run_id: str,
) -> dict[str, object]:
    _assert_worker_environment()
    commit = _clean_commit(repository_root)
    dependency_sha = _sha256_file(dependency_lock_path)
    platform = _PlatformIdentity(commit, False, None)
    first = _construct_corpus(
        runs_root=runs_root,
        dependency_sha=dependency_sha,
        context_run_id=context_run_id,
    )
    second = _construct_corpus(
        runs_root=runs_root,
        dependency_sha=dependency_sha,
        context_run_id=context_run_id,
    )
    _assert_equal_corpus(first, second)
    del second

    qualification = _qualify(first)
    threshold = _typing.cast("float", qualification["selection_threshold"])
    deployment_ordinal = _typing.cast("int", qualification["deployment_strategy_ordinal"])
    baseline_tp = _typing.cast("int", qualification["train_selected_fixed_tp_pips"])
    model_params = {**_MODEL_BASE_PARAMS, "selection_threshold": threshold}
    fit, _calibration, _test, first_boundary, second_boundary = _split_masks(first)
    model = _CORPUS_MODELS.create("uls_joint_action_forest_v1", **model_params)
    plan = _snapshot_corpus_model(model)
    payload = _typing.cast("_FittedPayload", qualification["payload"])

    source_manifest = dict(first.source_manifest)
    source_manifest["deployment"] = {
        "strategy_ordinal": deployment_ordinal,
        "feature_params": first.strategy_params[deployment_ordinal],
        "train_selected_fixed_tp_pips": baseline_tp,
    }
    source_bytes = _json_bytes(source_manifest)
    source_sha = _sha256_bytes(source_bytes)
    corpus_bytes = _encode_corpus(
        first,
        first_boundary=first_boundary,
        second_boundary=second_boundary,
        deployment_ordinal=deployment_ordinal,
    )
    corpus_sha = _sha256_bytes(corpus_bytes)
    identity_document = {
        "format": "uls-joint-action-corpus-identity-v1",
        "builder": {"plugin_id": "uls_joint_action_corpus_builder_v1", "version": 1},
        "source_manifest_sha256": source_sha,
        "corpus_sha256": corpus_sha,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "target_spec": {
            "plugin_id": _TARGET_SPEC_ID,
            "version": 1,
            "params": _TARGET_SPEC_PARAMS,
        },
        "training_spec": {**_TRAINING_SPEC, "selection_threshold": threshold},
        "model_params": model_params,
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    corpus_identity = _identity_key_from_canonical(
        _canonical_identity_json(identity_document)
    )
    state_identity = _CorpusFittedStateIdentity(
        plugin_id=plan._origin.plugin_id,
        plugin_version=plan._origin.version,
        plugin_params=plan._origin.params,
        inference_feature_names=plan._required_feature_names,
        corpus_identity_key=corpus_identity,
        corpus_artifact_sha256=corpus_sha,
        source_manifest_sha256=source_sha,
        target_spec_id=_TARGET_SPEC_ID,
        target_spec_version=1,
        target_spec_params=_TARGET_SPEC_PARAMS,
        training_spec={**_TRAINING_SPEC, "selection_threshold": threshold},
        platform_identity=platform,
        dependency_lock_sha256=dependency_sha,
        comparison_tolerance=plan._comparison_tolerance,
    )
    state = _bind_fitted_state(state_identity, payload)
    fitted_bytes = _encode_fitted_state(state_identity, state)
    report = {
        "schema": _REPORT_SCHEMA,
        "corpus_identity_key": corpus_identity,
        "corpus_sha256": corpus_sha,
        "fitted_state_identity_key": state.identity_key(),
        "fitted_state_sha256": _sha256_bytes(fitted_bytes),
        "counts": first.counts,
        "split": {
            "fit_boundary": first_boundary,
            "test_boundary": second_boundary,
            "fit_rows": int(_np.count_nonzero(fit)),
        },
        "qualification": {
            name: value for name, value in qualification.items() if name != "payload"
        },
        "solver": {**model_params, "n_jobs": 1},
        "determinism": {"corpus_double_build_equal": True, "fit_payload_equal": True},
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "complete_cases_only": True,
            "unexecuted_events_are_not_imputed": True,
            "validation_is_reused_development_evidence": True,
        },
    }
    artifacts = {
        "corpus.npz": corpus_bytes,
        "fitted-state.npz": fitted_bytes,
        "source-manifest.json": source_bytes,
        "training-report.json": _json_bytes(report),
    }
    hashes = {name: _sha256_bytes(value) for name, value in sorted(artifacts.items())}
    bundle_id = f"uls-joint-action-selector-v1-{corpus_identity[:20]}"
    destination = runs_root / "training-bundles" / bundle_id
    lifecycle = b"".join(
        _json_bytes(
            {
                "schema": "research-training-bundle-lifecycle-event-v1",
                "ordinal": index,
                "state": lifecycle_state,
            }
        )
        for index, lifecycle_state in enumerate(("PLANNED", "RUNNING", "COMPLETE"))
    )
    manifest: dict[str, object] = {
        "schema": _BUNDLE_SCHEMA,
        "bundle_id": bundle_id,
        "state": "COMPLETE",
        "files": hashes,
        "lifecycle_sha256": _sha256_bytes(lifecycle),
        "corpus_identity_key": corpus_identity,
        "fitted_state_identity_key": state.identity_key(),
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    manifest_bytes = _json_bytes(manifest)
    if destination.exists():
        existing = _read_bundle_manifest(destination)
        if _json_bytes(existing) != manifest_bytes:
            raise _ContractViolationError("existing immutable ULS joint bundle differs")
        _verify_bundle_files(destination, existing)
    else:
        _begin_bundle_lifecycle(bundle_root=destination.parent, bundle_id=bundle_id)
        try:
            _publish_new_bundle(
                destination=destination,
                artifacts=artifacts,
                manifest=manifest,
                manifest_bytes=manifest_bytes,
            )
        except BaseException:
            _fail_bundle_lifecycle(destination)
            raise
    return _bundle_reference_document(
        manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )


def _construct_corpus(
    *,
    runs_root: _Path,
    dependency_sha: str,
    context_run_id: str,
) -> _JointCorpus:
    arms, plan_document = _discover_arms(runs_root, dependency_sha=dependency_sha)
    context_source, context_entry = _discover_context(
        runs_root,
        run_id=context_run_id,
        dependency_sha=dependency_sha,
    )
    context = _read_feature_artifact(
        context_source,
        required_names=_CONTEXT_FEATURE_NAMES,
        expected_provider="uls_market_context_v1",
    )
    context_keys = _typing.cast("_npt.NDArray[_np.int64]", context["keys"])
    context_columns = _typing.cast(
        "dict[str, _npt.NDArray[_np.float64]]", context["columns"]
    )

    predictor_chunks: list[_npt.NDArray[_np.float64]] = []
    key_chunks: list[_npt.NDArray[_np.int64]] = []
    direction_chunks: list[_npt.NDArray[_np.int8]] = []
    exit_chunks: list[_npt.NDArray[_np.int64]] = []
    ordinal_chunks: list[_npt.NDArray[_np.int16]] = []
    sample_chunks: list[_npt.NDArray[_np.uint8]] = []
    origin_chunks: list[_npt.NDArray[_np.uint8]] = []
    target_chunks: list[_npt.NDArray[_np.float64]] = []
    raw_trades = _Counter[int]()
    availability = _Counter[int]()
    boundary_excluded = 0
    raw_events = 0
    strategy_params: dict[int, dict[str, object]] = {}
    configurations: list[dict[str, object]] = []
    source_runs: list[dict[str, object]] = []

    grouped: dict[int, dict[int, _ArmRun]] = _defaultdict(dict)
    for arm in arms:
        grouped[arm.strategy_ordinal][arm.tp_pips] = arm
    if set(grouped) != set(range(_STRATEGY_COUNT)):
        raise _ContractViolationError("ULS joint strategy inventory is incomplete")

    for strategy_ordinal in range(_STRATEGY_COUNT):
        strategy_arms = grouped[strategy_ordinal]
        if set(strategy_arms) != set(_TP_PIPS):
            raise _ContractViolationError("ULS joint strategy lacks one TP arm")
        feature_hashes = {arm.source.feature_sha256 for arm in strategy_arms.values()}
        if len(feature_hashes) != 1:
            raise _ContractViolationError("matched TP arms have different feature artifacts")
        first_source = strategy_arms[_TP_PIPS[0]].source
        uls = _read_feature_artifact(
            first_source,
            required_names=(_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES),
            expected_provider="uls_push_v7",
        )
        keys = _typing.cast("_npt.NDArray[_np.int64]", uls["keys"])
        columns = _typing.cast("dict[str, _npt.NDArray[_np.float64]]", uls["columns"])
        if not _np.array_equal(keys, context_keys):
            raise _ContractViolationError("ULS joint source and context axes differ")
        raw_events += int(_np.count_nonzero(_np.isin(columns[_ULS_EVENT_FEATURE], (-1.0, 1.0))))
        observations: dict[int, dict[tuple[int, int], object]] = {}
        for tp in _TP_PIPS:
            source = strategy_arms[tp].source
            observed = _read_observations(
                source,
                feature_keys=keys,
                events=columns[_ULS_EVENT_FEATURE],
            )
            observations[tp] = {(item.intent_key, item.direction): item for item in observed}
            raw_trades[tp] += len(observed)
            source_runs.append(_source_entry(strategy_arms[tp]))
        all_samples = set().union(*(set(items) for items in observations.values()))
        complete = set.intersection(*(set(items) for items in observations.values()))
        for identity in all_samples:
            availability[sum(identity in observations[tp] for tp in _TP_PIPS)] += 1
        retained: list[tuple[int, int]] = []
        for identity in sorted(complete):
            matched = [_typing.cast("_typing.Any", observations[tp][identity]) for tp in _TP_PIPS]
            if any(item.exit_reason == 4 for item in matched):
                boundary_excluded += 1
            else:
                retained.append(identity)
        params = dict(first_source.config_params)
        strategy_params[strategy_ordinal] = params
        origin_digest = _typing.cast("bytes", uls["origin_digest"])
        configurations.append(
            {
                "strategy_ordinal": strategy_ordinal,
                "feature_origin_digest": origin_digest.hex(),
                "params": params,
            }
        )
        if not retained:
            continue
        decision_keys = _np.asarray([item[0] for item in retained], dtype=_np.int64)
        directions = _np.asarray([item[1] for item in retained], dtype=_np.int8)
        positions = _np.searchsorted(keys, decision_keys).astype(_np.int64, copy=False)
        if _np.any(positions >= keys.size) or not _np.array_equal(keys[positions], decision_keys):
            raise _ContractViolationError("matched ULS opening intent is absent from features")
        predictors = _predictor_rows_from_arrays(
            columns={**columns, **context_columns},
            positions=positions,
            params=_decoded_params(params),
            expected_directions=directions,
        )
        target_rows = _np.asarray(
            [
                [
                    _typing.cast("_typing.Any", observations[tp][identity]).net_pnl
                    for tp in _TP_PIPS
                ]
                for identity in retained
            ],
            dtype=_np.float64,
        )
        max_exits = _np.asarray(
            [
                max(
                    _typing.cast("_typing.Any", observations[tp][identity]).exit_key
                    for tp in _TP_PIPS
                )
                for identity in retained
            ],
            dtype=_np.int64,
        )
        predictor_chunks.append(predictors)
        key_chunks.append(decision_keys)
        direction_chunks.append(directions)
        exit_chunks.append(max_exits)
        ordinal_chunks.append(
            _np.full(len(retained), strategy_ordinal, dtype=_np.int16)
        )
        sample_chunks.append(
            _sample_ids(
                origin_digest=origin_digest,
                decision_keys=decision_keys,
                directions=directions,
            )
        )
        origin_chunks.append(
            _np.broadcast_to(
                _np.frombuffer(origin_digest, dtype=_np.uint8),
                (len(retained), 32),
            ).copy()
        )
        target_chunks.append(target_rows)

    predictors = _np.concatenate(predictor_chunks, axis=0)
    decision_keys = _np.concatenate(key_chunks)
    directions = _np.concatenate(direction_chunks)
    max_exit_keys = _np.concatenate(exit_chunks)
    ordinals = _np.concatenate(ordinal_chunks)
    sample_ids = _np.concatenate(sample_chunks, axis=0)
    origin_ids = _np.concatenate(origin_chunks, axis=0)
    target_matrix = _np.concatenate(target_chunks, axis=0)
    if _np.unique(sample_ids, axis=0).shape[0] != sample_ids.shape[0]:
        raise _ContractViolationError("ULS joint corpus contains duplicate samples")
    frequency = _Counter(zip(decision_keys.tolist(), directions.tolist(), strict=True))
    weights = _np.asarray(
        [
            1.0 / frequency[(int(key), int(direction))]
            for key, direction in zip(decision_keys, directions, strict=True)
        ],
        dtype=_np.float64,
    )
    inputs = {name: predictors[:, index] for index, name in enumerate(_INPUT_NAMES)}
    targets = {name: target_matrix[:, index] for index, name in enumerate(_TARGET_NAMES)}
    incomplete = sum(
        count for arm_count, count in availability.items() if arm_count < len(_TP_PIPS)
    )
    counts: dict[str, object] = {
        "configurations": _STRATEGY_COUNT,
        "raw_events": raw_events,
        "raw_trades_by_tp": {str(tp): raw_trades[tp] for tp in _TP_PIPS},
        "complete_rows_before_boundary_exclusion": int(decision_keys.size) + boundary_excluded,
        "boundary_forced_close_excluded": boundary_excluded,
        "retained_complete_rows": int(decision_keys.size),
        "unique_market_episodes": len(frequency),
        "incomplete_episodes_excluded": incomplete,
        "availability": {str(key): value for key, value in sorted(availability.items())},
    }
    source_manifest: dict[str, object] = {
        "schema": _SOURCE_SCHEMA,
        "dataset": _TRAIN_DATASET,
        "source_sweep": {
            "sweep_id": _SOURCE_SWEEP_ID,
            "spec_digest": _SOURCE_SPEC_DIGEST,
            "platform": plan_document["platform"],
        },
        "context_carrier": context_entry,
        "evaluation_execution": dict(grouped[0][800].source.execution),
        "configurations": configurations,
        "outcome_sources": source_runs,
        "selection": {
            "tp_pips": list(_TP_PIPS),
            "stop_loss_pips": 800,
            "complete_cases_only": True,
            "boundary_forced_close_excluded": True,
            "cost_semantics": "unverified",
        },
    }
    return _JointCorpus(
        sample_ids,
        origin_ids,
        decision_keys,
        directions,
        max_exit_keys,
        ordinals,
        weights,
        inputs,
        targets,
        strategy_params,
        source_manifest,
        counts,
    )


def _discover_arms(
    runs_root: _Path,
    *,
    dependency_sha: str,
) -> tuple[tuple[_ArmRun, ...], dict[str, object]]:
    plan = _read_sweep_plan(runs_root, _SOURCE_SWEEP_ID)
    document = dict(plan._document)
    if (
        document.get("spec_digest") != _SOURCE_SPEC_DIGEST
        or len(plan._children) != _EXPECTED_CHILDREN
    ):
        raise _ContractViolationError("ULS joint source sweep identity differs")
    if document.get("dependency_lock_sha256") != dependency_sha:
        raise _ContractViolationError("ULS joint source dependency differs")
    expected = {
        child._experiment_fingerprint: child._child_ordinal for child in plan._children
    }
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    if scan._errors:
        raise _ContractViolationError("authoritative ULS joint source scan reported corruption")
    selected: dict[int, _ArmRun] = {}
    for projection in scan._projections:
        manifest = projection._manifest
        config = projection._resolved_config
        record = projection._record
        if (
            manifest is None
            or record._state is not _RunState.COMPLETE
            or record._canonical is not True
            or manifest.experiment_fingerprint not in expected
        ):
            continue
        ordinal = expected[manifest.experiment_fingerprint]
        dataset = _exact_mapping(config.get("dataset"), label="ULS joint dataset")
        if dataset != _TRAIN_DATASET:
            raise _ContractViolationError("ULS joint source is not exact TRAIN")
        tp = _TP_PIPS[ordinal // _STRATEGY_COUNT]
        strategy_ordinal = ordinal % _STRATEGY_COUNT
        execution = _exact_mapping(config.get("execution"), label="ULS joint execution")
        if (
            _decoded_float(execution.get("take_profit_pips")) != float(tp)
            or _decoded_float(execution.get("stop_loss_pips")) != 800.0
            or execution.get("execution_id") != _SOURCE_EXECUTION_ID
        ):
            raise _ContractViolationError("ULS joint source bracket differs")
        features = config.get("features")
        if type(features) is not list or len(features) != 1:
            raise _ContractViolationError("ULS joint source feature inventory differs")
        feature = _exact_mapping(features[0], label="ULS joint feature")
        if feature.get("name") != "uls_push_v7" or feature.get("version") != 1:
            raise _ContractViolationError("ULS joint source provider differs")
        files = dict(manifest.artifact_hashes)
        intent_files = tuple(
            (name, digest)
            for name, digest in sorted(files.items())
            if _re.fullmatch(r"fold-[0-9]{4}-intent-0000\.npz", name)
        )
        trade_files = tuple(
            (name, digest)
            for name, digest in sorted(files.items())
            if _re.fullmatch(r"fold-[0-9]{4}-policy-0000-trades\.npz", name)
        )
        if "features.npz" not in files or len(intent_files) != 5 or len(trade_files) != 5:
            raise _ContractViolationError("ULS joint source artifacts are incomplete")
        if ordinal in selected:
            raise _ContractViolationError("multiple canonical runs claim one ULS joint child")
        source = _SourceRun(
            ordinal,
            manifest.run_id,
            manifest.experiment_fingerprint,
            runs_root / "runs" / manifest.run_id,
            dict(_exact_mapping(feature.get("params"), label="ULS joint params")),
            dict(execution),
            files["features.npz"],
            intent_files,
            trade_files,
        )
        selected[ordinal] = _ArmRun(strategy_ordinal, tp, source)
    if set(selected) != set(range(_EXPECTED_CHILDREN)):
        raise _ContractViolationError("canonical ULS joint source inventory is incomplete")
    return tuple(selected[index] for index in range(_EXPECTED_CHILDREN)), document


def _discover_context(
    runs_root: _Path,
    *,
    run_id: str,
    dependency_sha: str,
) -> tuple[_SourceRun, dict[str, object]]:
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    candidates = [item for item in scan._projections if item._record._run_id == run_id]
    if len(candidates) != 1:
        raise _ContractViolationError("exactly one requested context carrier is required")
    projection = candidates[0]
    manifest = projection._manifest
    config = projection._resolved_config
    if (
        manifest is None
        or projection._record._state is not _RunState.COMPLETE
        or projection._record._canonical is not True
        or manifest.dependency_lock_sha256 != dependency_sha
    ):
        raise _ContractViolationError("context carrier is not canonical COMPLETE")
    if _exact_mapping(config.get("dataset"), label="context dataset") != _TRAIN_DATASET:
        raise _ContractViolationError("context carrier is not exact TRAIN")
    features = config.get("features")
    if type(features) is not list or len(features) != 2:
        raise _ContractViolationError("context carrier feature inventory differs")
    context_feature = _exact_mapping(features[1], label="context feature")
    if context_feature != {"name": "uls_market_context_v1", "version": 1, "params": {}}:
        raise _ContractViolationError("context carrier provider differs")
    files = dict(manifest.artifact_hashes)
    feature_sha = files.get("features.npz")
    if type(feature_sha) is not str:
        raise _ContractViolationError("context carrier lacks features.npz")
    first = _exact_mapping(features[0], label="context ULS feature")
    source = _SourceRun(
        -1,
        manifest.run_id,
        manifest.experiment_fingerprint,
        runs_root / "runs" / manifest.run_id,
        dict(_exact_mapping(first.get("params"), label="context ULS params")),
        dict(_exact_mapping(config.get("execution"), label="context execution")),
        feature_sha,
        (),
        (),
    )
    return source, {
        "run_id": manifest.run_id,
        "experiment_fingerprint": manifest.experiment_fingerprint,
        "feature_artifact_sha256": feature_sha,
        "platform": {
            "git_commit": manifest.platform_identity.git_commit,
            "git_dirty": manifest.platform_identity.git_dirty,
            "diff_sha256": manifest.platform_identity.diff_sha256,
        },
    }


def _source_entry(arm: _ArmRun) -> dict[str, object]:
    source = arm.source
    return {
        "strategy_ordinal": arm.strategy_ordinal,
        "tp_pips": arm.tp_pips,
        "run_id": source.run_id,
        "experiment_fingerprint": source.fingerprint,
        "artifacts": {
            "features.npz": source.feature_sha256,
            **dict(source.intent_files),
            **dict(source.trade_files),
        },
    }


def _decoded_float(value: object) -> float | None:
    if type(value) is not dict or set(value) != {"__float_hex__"}:
        return None
    encoded = value.get("__float_hex__")
    return float.fromhex(encoded) if type(encoded) is str else None


def _split_masks(
    corpus: _JointCorpus,
) -> tuple[
    _npt.NDArray[_np.bool_],
    _npt.NDArray[_np.bool_],
    _npt.NDArray[_np.bool_],
    int,
    int,
]:
    unique = _np.unique(corpus.decision_keys)
    first = int(unique[_math.floor(unique.size * 0.70)])
    second = int(unique[_math.floor(unique.size * 0.85)])
    fit = (corpus.decision_keys < first) & (corpus.max_exit_keys < first)
    calibration = (
        (corpus.decision_keys >= first)
        & (corpus.decision_keys < second)
        & (corpus.max_exit_keys < second)
    )
    test = corpus.decision_keys >= second
    if min(_np.count_nonzero(mask) for mask in (fit, calibration, test)) < 100:
        raise _ContractViolationError("ULS joint chronological split is too small")
    return fit, calibration, test, first, second


def _qualify(corpus: _JointCorpus) -> dict[str, object]:
    fit, calibration, test, first, second = _split_masks(corpus)
    provisional = _CORPUS_MODELS.create(
        "uls_joint_action_forest_v1",
        **_MODEL_BASE_PARAMS,
        selection_threshold=0.0,
    )
    plan = _snapshot_corpus_model(provisional)
    fit_inputs = _make_corpus_model_inputs(
        inputs={name: values[fit] for name, values in corpus.inputs.items()},
        targets={name: values[fit] for name, values in corpus.targets.items()},
        weights=corpus.weights[fit],
    )
    first_payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    second_payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    _assert_equal_payload(first_payload, second_payload)
    target_matrix = _np.column_stack([corpus.targets[name] for name in _TARGET_NAMES])

    candidates: list[tuple[float, int, int, int]] = []
    for ordinal in range(_STRATEGY_COUNT):
        selected = fit & (corpus.strategy_ordinals == ordinal)
        rows = int(_np.count_nonzero(selected))
        if rows < 100:
            continue
        totals = _np.sum(target_matrix[selected], axis=0, dtype=_np.float64)
        arm = int(_np.argmax(totals))
        candidates.append((float(totals[arm]), rows, -ordinal, arm))
    if not candidates:
        raise _ContractViolationError("no deployment strategy has enough fit rows")
    _best_total, _best_rows, negative_ordinal, _local_arm = max(candidates)
    deployment_ordinal = -negative_ordinal
    aggregate_fit_totals = _np.sum(target_matrix[fit], axis=0, dtype=_np.float64)
    baseline_arm = int(_np.argmax(aggregate_fit_totals))
    baseline_tp = _TP_PIPS[baseline_arm]

    calibration_matrix = _np.column_stack(
        [corpus.inputs[name][calibration] for name in _INPUT_NAMES]
    )
    calibration_prediction = _forest_predict(first_payload, calibration_matrix)
    calibration_truth = target_matrix[calibration]
    if calibration_truth.shape[0] < 30:
        raise _ContractViolationError("deployment strategy has too few calibration rows")
    chosen = _np.argmax(calibration_prediction, axis=1)
    maximum = _np.max(calibration_prediction, axis=1)
    realized = calibration_truth[_np.arange(calibration_truth.shape[0]), chosen]
    threshold_values = {
        0.0,
        *(
            float(value)
            for value in _np.quantile(
                maximum,
                _np.arange(0.0, 0.95, 0.05, dtype=_np.float64),
            )
            if float(value) >= 0.0
        ),
    }
    minimum = max(30, _math.ceil(0.05 * maximum.size))
    threshold_rows: list[dict[str, object]] = []
    rankings: list[tuple[float, int, float]] = []
    for threshold in sorted(threshold_values):
        accepted = maximum > threshold
        count = int(_np.count_nonzero(accepted))
        if count < minimum:
            continue
        total = float(_np.sum(realized[accepted], dtype=_np.float64))
        threshold_rows.append(
            {
                "threshold": threshold,
                "selected_rows": count,
                "selected_fraction": count / maximum.size,
                "dynamic_total_net_pnl": total,
            }
        )
        rankings.append((total, count, -threshold))
    calibration_fallback = not rankings
    if calibration_fallback:
        threshold = 0.0
        accepted = maximum > threshold
        threshold_rows.append(
            {
                "threshold": threshold,
                "selected_rows": int(_np.count_nonzero(accepted)),
                "selected_fraction": float(_np.mean(accepted)),
                "dynamic_total_net_pnl": float(
                    _np.sum(realized[accepted], dtype=_np.float64)
                ),
                "minimum_trade_rule_satisfied": False,
                "fallback": "no_nonnegative_threshold_met_minimum_trade_rule",
            }
        )
    else:
        threshold = -max(rankings)[2]
    test_prediction = _forest_predict(
        first_payload,
        _np.column_stack([corpus.inputs[name][test] for name in _INPUT_NAMES]),
    )
    test_truth = target_matrix[test]
    internal_test = _segment_report(
        test_prediction,
        test_truth,
        threshold=threshold,
        baseline_arm=baseline_arm,
    )
    return {
        "payload": first_payload,
        "fit_boundary": first,
        "test_boundary": second,
        "fit_rows": int(_np.count_nonzero(fit)),
        "calibration_rows": int(_np.count_nonzero(calibration)),
        "test_rows": int(_np.count_nonzero(test)),
        "deployment_strategy_ordinal": deployment_ordinal,
        "train_selected_fixed_tp_pips": baseline_tp,
        "train_fixed_total_net_pnl": {
            str(tp): float(aggregate_fit_totals[index])
            for index, tp in enumerate(_TP_PIPS)
        },
        "selection_threshold": threshold,
        "calibration_empty_fallback_used": calibration_fallback,
        "threshold_candidates": threshold_rows,
        "calibration": _segment_report(
            calibration_prediction,
            calibration_truth,
            threshold=threshold,
            baseline_arm=baseline_arm,
        ),
        "internal_test": internal_test,
    }


def _segment_report(
    prediction: _npt.NDArray[_np.float64],
    truth: _npt.NDArray[_np.float64],
    *,
    threshold: float,
    baseline_arm: int,
) -> dict[str, object]:
    chosen = _np.argmax(prediction, axis=1)
    accepted = _np.max(prediction, axis=1) > threshold
    realized = truth[_np.arange(truth.shape[0]), chosen]
    fixed = _np.sum(truth, axis=0, dtype=_np.float64)
    counts = _np.bincount(chosen[accepted], minlength=len(_TP_PIPS))
    dynamic = float(_np.sum(realized[accepted], dtype=_np.float64))
    baseline = float(fixed[baseline_arm])
    return {
        "complete_rows": int(truth.shape[0]),
        "dynamic_trade_rows": int(_np.count_nonzero(accepted)),
        "no_trade_rows": int(_np.count_nonzero(~accepted)),
        "dynamic_total_net_pnl": dynamic,
        "fixed_total_net_pnl": {
            str(tp): float(fixed[index]) for index, tp in enumerate(_TP_PIPS)
        },
        "dynamic_minus_train_selected_fixed_net_pnl": dynamic - baseline,
        "dynamic_minus_no_trade_net_pnl": dynamic,
        "dynamic_choice_counts": {
            str(tp): int(counts[index]) for index, tp in enumerate(_TP_PIPS)
        },
    }


def _encode_corpus(
    corpus: _JointCorpus,
    *,
    first_boundary: int,
    second_boundary: int,
    deployment_ordinal: int,
) -> bytes:
    metadata = {
        "format": _CORPUS_FORMAT,
        "format_version": 1,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "counts": corpus.counts,
        "fit_boundary": first_boundary,
        "test_boundary": second_boundary,
        "deployment_strategy_ordinal": deployment_ordinal,
    }
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        "__meta__": _np.frombuffer(_canonical_json(metadata).encode("ascii"), dtype=_np.uint8),
        "sample_ids": corpus.sample_ids,
        "feature_origin_ids": corpus.origin_ids,
        "decision_keys": corpus.decision_keys,
        "directions": corpus.directions,
        "max_exit_keys": corpus.max_exit_keys,
        "strategy_ordinals": corpus.strategy_ordinals,
        "weights": corpus.weights,
    }
    arrays.update(
        {f"input_{index:04d}": corpus.inputs[name] for index, name in enumerate(_INPUT_NAMES)}
    )
    arrays.update(
        {f"target_{index:04d}": corpus.targets[name] for index, name in enumerate(_TARGET_NAMES)}
    )
    output = _io.BytesIO()
    _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **arrays)
    payload = output.getvalue()
    _validate_zip_envelope(payload, tuple(arrays))
    return payload


def _assert_equal_corpus(left: _JointCorpus, right: _JointCorpus) -> None:
    pairs: list[tuple[_npt.NDArray[_typing.Any], _npt.NDArray[_typing.Any]]] = [
        (left.sample_ids, right.sample_ids),
        (left.origin_ids, right.origin_ids),
        (left.decision_keys, right.decision_keys),
        (left.directions, right.directions),
        (left.max_exit_keys, right.max_exit_keys),
        (left.strategy_ordinals, right.strategy_ordinals),
        (left.weights, right.weights),
    ]
    pairs.extend((left.inputs[name], right.inputs[name]) for name in _INPUT_NAMES)
    pairs.extend((left.targets[name], right.targets[name]) for name in _TARGET_NAMES)
    if (
        left.source_manifest != right.source_manifest
        or left.counts != right.counts
        or left.strategy_params != right.strategy_params
        or any(not _np.array_equal(first, second) for first, second in pairs)
    ):
        raise _ContractViolationError("ULS joint corpus double build differs")


def _absolute_path(value: str) -> _Path:
    if type(value) is not str or not value:
        raise _argparse.ArgumentTypeError("path must be non-empty")
    return _Path(value).expanduser().resolve()


def _main(argv: list[str] | None = None) -> int:
    parser = _argparse.ArgumentParser(prog="uls-joint-bundle")
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--repository-root", required=True, type=_absolute_path)
    parser.add_argument("--dependency-lock", required=True, type=_absolute_path)
    parser.add_argument("--context-run-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = _build(
            runs_root=args.runs_root,
            repository_root=args.repository_root,
            dependency_lock_path=args.dependency_lock,
            context_run_id=args.context_run_id,
        )
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2
    _sys.stdout.write(_canonical_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
