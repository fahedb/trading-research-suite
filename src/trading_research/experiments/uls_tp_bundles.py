"""Build the frozen seven-arm ULS TP selector from forward-only TRAIN runs."""

from __future__ import annotations

import argparse as _argparse
import hashlib as _hashlib
import io as _io
import json as _json
import math as _math
import re as _re
import sys as _sys
import time as _time
import typing as _typing
from collections import Counter as _Counter
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
    _FrozenReference,
    _json_bytes,
    _load_frozen_state,
    _open_npz,
    _publish_new_bundle,
    _read_bundle_manifest,
    _sha256_bytes,
    _sha256_file,
    _verify_bundle_files,
)
from trading_research.experiments.uls_entry_bundles import (
    _Observation,
    _read_feature_artifact,
    _read_observations,
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
from trading_research.models.uls_entry_selector import (
    _TARGET_NAMES as _ENTRY_TARGET_NAMES,
)
from trading_research.models.uls_tp_selector import (
    _ENTRY_TARGET,
    _ROLE_TARGET,
    _SELECTOR_PREFIX,
    _TARGET_NAMES,
    _TP_PIPS,
    _TP_TARGET_NAMES,
    _entry_payload,
    _predict_prefixed,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue

__all__: list[str] = []

_SWEEP_ID = "uls-entry-tp-oof-train-v1"
_TRAIN_DATASET = {
    "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
    "manifest_sha256": "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
    "role": "train",
}
_ENTRY_MODEL_PARAMS: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "max_depth": 8,
    "max_features": 0.75,
    "min_samples_leaf": 96,
    "n_estimators": 64,
    "random_seed": 20_260_826,
}
_SELECTOR_PARAMS: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "entry_max_depth": 8,
    "entry_max_features": 0.75,
    "entry_min_samples_leaf": 96,
    "entry_n_estimators": 64,
    "entry_random_seed": 20_260_826,
    "selector_max_depth": 6,
    "selector_max_features": 0.75,
    "selector_min_samples_leaf": 12,
    "selector_n_estimators": 96,
    "selector_random_seed": 20_260_826,
}
_TRAINING_SPEC: _Mapping[str, _ParamValue] = {
    "entry_fit": "exact_frozen_uls_entry_selector_v1_fit_rows",
    "selector_fit_fraction": 0.85,
    "selector_split": "unique_decision_keys_chronological_v1",
    "purge": "max_arm_exit_reaches_test_boundary_v1",
    "complete_cases_only": True,
    "sample_identity": "feature_origin_intent_key_direction_v1",
    "tie_break": "lowest_tp_first_v1",
    "weighting": "one_per_unique_deployment_episode_v1",
}
_TARGET_SPEC_PARAMS: _Mapping[str, _ParamValue] = {
    "arm_take_profit_pips": tuple(float(value) for value in _TP_PIPS),
    "complete_cases_only": True,
    "cost_semantics": "unverified",
    "stop_loss_pips": 800.0,
    "target_column": "net_pnl",
}
_TARGET_SPEC_ID = "matched_oof_tp_net_pnl_7arm_v1"
_BUNDLE_SCHEMA = "research-training-bundle-manifest-v1"
_CORPUS_FORMAT = "uls-entry-tp-corpus-v1"
_REPORT_SCHEMA = "uls-entry-tp-training-report-v1"
_SOURCE_SCHEMA = "uls-entry-tp-source-manifest-v1"


@_dataclass(frozen=True, slots=True)
class _ArmRun:
    fold: int
    tp_pips: int
    source: _SourceRun
    resolved_config: dict[str, object]


@_dataclass(frozen=True, slots=True)
class _TpCorpus:
    decision_keys: _npt.NDArray[_np.int64]
    directions: _npt.NDArray[_np.int8]
    max_exit_keys: _npt.NDArray[_np.int64]
    sample_ids: _npt.NDArray[_np.uint8]
    origin_ids: _npt.NDArray[_np.uint8]
    inputs: dict[str, _npt.NDArray[_np.float64]]
    targets: dict[str, _npt.NDArray[_np.float64]]
    weights: _npt.NDArray[_np.float64]
    source_manifest: dict[str, object]
    counts: dict[str, object]


@_dataclass(frozen=True, slots=True)
class _EntryCorpus:
    reference: dict[str, object]
    manifest: dict[str, object]
    inputs: dict[str, _npt.NDArray[_np.float64]]
    target: _npt.NDArray[_np.float64]
    decision_keys: _npt.NDArray[_np.int64]
    max_exit_keys: _npt.NDArray[_np.int64]
    weights: _npt.NDArray[_np.float64]
    fitted_state_sha256: str
    fitted_state_identity_key: str
    selection_threshold: float


def _build(
    *,
    runs_root: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
    entry_bundle_dir: _Path,
    oof_freeze_dir: _Path,
) -> dict[str, object]:
    _assert_worker_environment()
    commit = _clean_commit(repository_root)
    dependency_sha = _sha256_file(dependency_lock_path)
    platform = _PlatformIdentity(commit, False, None)
    entry = _load_entry_corpus(entry_bundle_dir)
    first = _construct_tp_corpus(
        runs_root=runs_root,
        oof_freeze_dir=oof_freeze_dir,
        dependency_sha=dependency_sha,
    )
    second = _construct_tp_corpus(
        runs_root=runs_root,
        oof_freeze_dir=oof_freeze_dir,
        dependency_sha=dependency_sha,
    )
    _assert_equal_tp_corpus(first, second)
    fit, test, boundary = _selector_masks(first)
    model_params = {**_SELECTOR_PARAMS, "selection_threshold": entry.selection_threshold}
    combined_inputs, combined_targets, combined_weights, entry_fit_rows = _combined_fit_inputs(
        entry=entry,
        tp=first,
        selector_fit=fit,
    )
    model = _CORPUS_MODELS.create("uls_entry_tp_forest_v1", **model_params)
    plan = _snapshot_corpus_model(model)
    corpus_inputs = _make_corpus_model_inputs(
        inputs=combined_inputs,
        targets=combined_targets,
        weights=combined_weights,
    )
    first_payload = _fit_corpus_model_payload(plan, inputs=corpus_inputs)
    second_payload = _fit_corpus_model_payload(plan, inputs=corpus_inputs)
    _assert_equal_payload(first_payload, second_payload)
    entry_state = _load_frozen_state(
        runs_root=runs_root,
        reference=_FrozenReference(
            entry_bundle_dir.name,
            _sha256_file(entry_bundle_dir / "manifest.json"),
            entry.fitted_state_identity_key,
            entry.fitted_state_sha256,
        ),
        expected_model_id="uls_entry_forest_v1",
        expected_model_version=1,
        expected_model_params={
            **_ENTRY_MODEL_PARAMS,
            "selection_threshold": entry.selection_threshold,
        },
    )
    _assert_equal_payload(_entry_payload(first_payload), entry_state.payload)
    qualification = _qualification(first, test=test, payload=first_payload, boundary=boundary)
    source_bytes = _json_bytes(first.source_manifest)
    source_sha = _sha256_bytes(source_bytes)
    corpus_bytes = _encode_corpus(
        entry=entry,
        tp=first,
        selector_fit=fit,
        boundary=boundary,
        entry_fit_rows=entry_fit_rows,
    )
    corpus_sha = _sha256_bytes(corpus_bytes)
    identity_document = {
        "format": "uls-entry-tp-corpus-identity-v1",
        "builder": {"plugin_id": "uls_entry_tp_corpus_builder_v1", "version": 1},
        "source_manifest_sha256": source_sha,
        "corpus_sha256": corpus_sha,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "target_spec": {
            "plugin_id": _TARGET_SPEC_ID,
            "version": 1,
            "params": _TARGET_SPEC_PARAMS,
        },
        "training_spec": _TRAINING_SPEC,
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
        training_spec=_TRAINING_SPEC,
        platform_identity=platform,
        dependency_lock_sha256=dependency_sha,
        comparison_tolerance=plan._comparison_tolerance,
    )
    state = _bind_fitted_state(state_identity, first_payload)
    fitted_bytes = _encode_fitted_state(state_identity, state)
    report = {
        "schema": _REPORT_SCHEMA,
        "corpus_identity_key": corpus_identity,
        "corpus_sha256": corpus_sha,
        "fitted_state_identity_key": state.identity_key(),
        "fitted_state_sha256": _sha256_bytes(fitted_bytes),
        "counts": first.counts,
        "selector_fit_rows": int(_np.count_nonzero(fit)),
        "selector_test_rows": int(_np.count_nonzero(test)),
        "entry_fit_rows": entry_fit_rows,
        "test_boundary": boundary,
        "qualification": qualification,
        "solver": {**model_params, "n_jobs": 1},
        "determinism": {
            "tp_corpus_double_build_equal": True,
            "combined_fit_payload_equal": True,
            "entry_substate_exactly_matches_frozen_gate": True,
        },
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "complete_cases_only": True,
            "validation_already_used_by_entry_study": True,
        },
    }
    artifacts = {
        "corpus.npz": corpus_bytes,
        "fitted-state.npz": fitted_bytes,
        "source-manifest.json": source_bytes,
        "training-report.json": _json_bytes(report),
    }
    hashes = {name: _sha256_bytes(value) for name, value in sorted(artifacts.items())}
    bundle_id = f"uls-entry-tp-selector-v1-{corpus_identity[:20]}"
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
            raise _ContractViolationError("existing ULS TP selector bundle differs")
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


def _load_entry_corpus(bundle: _Path) -> _EntryCorpus:
    manifest = _read_bundle_manifest(bundle)
    _verify_bundle_files(bundle, manifest)
    files = _exact_mapping(manifest.get("files"), label="entry bundle files")
    fitted_sha = files.get("fitted-state.npz")
    if type(fitted_sha) is not str:
        raise _ContractViolationError("entry fitted state digest is malformed")
    report = _json.loads((bundle / "training-report.json").read_text(encoding="ascii"))
    if type(report) is not dict or type(report.get("selection_threshold")) is not float:
        raise _ContractViolationError("entry training report is malformed")
    metadata, archive = _open_npz(bundle / "corpus.npz")
    try:
        input_names = metadata.get("input_names")
        target_names = metadata.get("target_names")
        if (
            metadata.get("format") != "uls-entry-corpus-v1"
            or type(input_names) is not list
            or tuple(input_names) != _INPUT_NAMES
            or type(target_names) is not list
            or tuple(target_names) != _ENTRY_TARGET_NAMES
        ):
            raise _ContractViolationError("entry corpus schema differs")
        inputs = {
            name: _np.array(archive[f"input_{index:04d}"], dtype=_np.float64, copy=True)
            for index, name in enumerate(_INPUT_NAMES)
        }
        target = _np.array(archive["target_0000"], dtype=_np.float64, copy=True)
        decision_keys = _np.array(archive["decision_keys"], dtype=_np.int64, copy=True)
        max_exit_keys = _np.array(archive["max_exit_keys"], dtype=_np.int64, copy=True)
        weights = _np.array(archive["weights"], dtype=_np.float64, copy=True)
    finally:
        archive.close()
    reference = {
        "bundle_id": bundle.name,
        "bundle_manifest_sha256": _sha256_file(bundle / "manifest.json"),
        "fitted_state_identity_key": manifest["fitted_state_identity_key"],
        "fitted_state_artifact_sha256": fitted_sha,
    }
    return _EntryCorpus(
        reference,
        manifest,
        inputs,
        target,
        decision_keys,
        max_exit_keys,
        weights,
        fitted_sha,
        _typing.cast("str", manifest["fitted_state_identity_key"]),
        _typing.cast("float", report["selection_threshold"]),
    )


def _construct_tp_corpus(
    *,
    runs_root: _Path,
    oof_freeze_dir: _Path,
    dependency_sha: str,
) -> _TpCorpus:
    freeze_bytes = (oof_freeze_dir / "manifest.json").read_bytes()
    freeze = _json.loads(freeze_bytes)
    if (
        type(freeze) is not dict
        or freeze.get("schema") != "uls-entry-tp-oof-freeze-v1"
        or freeze.get("sweep_id") != _SWEEP_ID
        or freeze.get("expected_children") != 14
    ):
        raise _ContractViolationError("OOF freeze manifest differs")
    frozen_platform = _source_platform(freeze)
    frozen_dependency = freeze.get("dependency_lock_sha256")
    if frozen_dependency != dependency_sha:
        raise _ContractViolationError("OOF source dependency differs from selector build")
    runs = _discover_runs(
        runs_root,
        platform=frozen_platform,
        dependency_sha=dependency_sha,
    )
    rows: list[
        tuple[
            bytes,
            bytes,
            int,
            int,
            int,
            _npt.NDArray[_np.float64],
            tuple[float, ...],
        ]
    ] = []
    raw_counts = _Counter[int]()
    availability = _Counter[int]()
    boundary_excluded = 0
    source_entries: list[dict[str, object]] = []
    for fold in range(2):
        arms = {run.tp_pips: run for run in runs if run.fold == fold}
        if set(arms) != set(_TP_PIPS):
            raise _ContractViolationError("OOF fold lacks one TP arm")
        first_source = arms[_TP_PIPS[0]].source
        uls = _read_feature_artifact(
            first_source,
            required_names=(_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES),
            expected_provider="uls_push_v7",
        )
        context = _read_feature_artifact(
            first_source,
            required_names=_CONTEXT_FEATURE_NAMES,
            expected_provider="uls_market_context_v1",
        )
        keys = _typing.cast("_npt.NDArray[_np.int64]", uls["keys"])
        context_keys = _typing.cast("_npt.NDArray[_np.int64]", context["keys"])
        if not _np.array_equal(keys, context_keys):
            raise _ContractViolationError("OOF ULS and context axes differ")
        columns = {
            **_typing.cast("dict[str, _npt.NDArray[_np.float64]]", uls["columns"]),
            **_typing.cast("dict[str, _npt.NDArray[_np.float64]]", context["columns"]),
        }
        observations: dict[int, dict[tuple[int, int], _Observation]] = {}
        for tp in _TP_PIPS:
            arm = arms[tp]
            arm_uls = _read_feature_artifact(
                arm.source,
                required_names=(_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES),
                expected_provider="uls_push_v7",
            )
            arm_context = _read_feature_artifact(
                arm.source,
                required_names=_CONTEXT_FEATURE_NAMES,
                expected_provider="uls_market_context_v1",
            )
            arm_uls_keys = _typing.cast("_npt.NDArray[_np.int64]", arm_uls["keys"])
            arm_context_keys = _typing.cast(
                "_npt.NDArray[_np.int64]", arm_context["keys"]
            )
            if (
                not _np.array_equal(arm_uls_keys, keys)
                or not _np.array_equal(arm_context_keys, keys)
                or _canonical_json(arm_uls["origin"]) != _canonical_json(uls["origin"])
                or _canonical_json(arm_context["origin"]) != _canonical_json(context["origin"])
            ):
                raise _ContractViolationError("OOF TP arms have different feature evidence")
            observed = _read_observations(
                arm.source,
                feature_keys=keys,
                events=columns[_ULS_EVENT_FEATURE],
            )
            observations[tp] = {(item.intent_key, item.direction): item for item in observed}
            raw_counts[tp] += len(observed)
            source_entries.append(_source_entry(arm))
        all_samples = set().union(*(set(value) for value in observations.values()))
        complete = set.intersection(*(set(value) for value in observations.values()))
        for identity in all_samples:
            availability[sum(identity in observations[tp] for tp in _TP_PIPS)] += 1
        origin_digest = _typing.cast("bytes", uls["origin_digest"])
        params = _exact_mapping(
            _exact_mapping(uls["origin"], label="OOF ULS origin").get("params"),
            label="OOF ULS params",
        )
        for intent_key, direction in sorted(complete):
            arm_rows = tuple(observations[tp][(intent_key, direction)] for tp in _TP_PIPS)
            if any(item.exit_reason == 4 for item in arm_rows):
                boundary_excluded += 1
                continue
            position = int(_np.searchsorted(keys, intent_key))
            if position >= keys.size or int(keys[position]) != intent_key:
                raise _ContractViolationError("OOF opening intent is unavailable in features")
            predictor = _predictor_rows_from_arrays(
                columns=columns,
                positions=_np.asarray([position], dtype=_np.int64),
                params=_decoded_params(dict(params)),
                expected_directions=_np.asarray([direction], dtype=_np.int8),
            )[0]
            preimage = (
                origin_digest
                + int(intent_key).to_bytes(8, "big", signed=True)
                + int(direction).to_bytes(1, "big", signed=True)
            )
            rows.append(
                (
                    _hashlib.sha256(preimage).digest(),
                    origin_digest,
                    intent_key,
                    direction,
                    max(item.exit_key for item in arm_rows),
                    predictor,
                    tuple(item.net_pnl for item in arm_rows),
                )
            )
    rows.sort(key=lambda item: (item[2], item[3]))
    if not rows:
        raise _ContractViolationError("OOF TP corpus has no complete rows")
    sample_ids = _np.vstack([_np.frombuffer(item[0], dtype=_np.uint8) for item in rows])
    if _np.unique(sample_ids, axis=0).shape[0] != len(rows):
        raise _ContractViolationError("OOF TP corpus contains duplicate samples")
    predictors = _np.vstack([item[5] for item in rows]).astype(_np.float64, copy=False)
    target_matrix = _np.asarray([item[6] for item in rows], dtype=_np.float64)
    decision_keys = _np.asarray([item[2] for item in rows], dtype=_np.int64)
    directions = _np.asarray([item[3] for item in rows], dtype=_np.int8)
    max_exit_keys = _np.asarray([item[4] for item in rows], dtype=_np.int64)
    origin_ids = _np.vstack([_np.frombuffer(item[1], dtype=_np.uint8) for item in rows])
    inputs = {name: predictors[:, index] for index, name in enumerate(_INPUT_NAMES)}
    targets = {
        name: target_matrix[:, index] for index, name in enumerate(_TP_TARGET_NAMES)
    }
    incomplete = sum(
        count for available_arms, count in availability.items() if available_arms < len(_TP_PIPS)
    )
    counts: dict[str, object] = {
        "raw_trades_by_tp": {str(tp): raw_counts[tp] for tp in _TP_PIPS},
        "complete_rows_before_boundary_exclusion": len(rows) + boundary_excluded,
        "boundary_forced_close_excluded": boundary_excluded,
        "retained_complete_rows": len(rows),
        "incomplete_episodes_excluded": incomplete,
        "availability": {str(key): value for key, value in sorted(availability.items())},
    }
    source_manifest: dict[str, object] = {
        "schema": _SOURCE_SCHEMA,
        "dataset": _TRAIN_DATASET,
        "oof_freeze_manifest_sha256": _sha256_bytes(freeze_bytes),
        "oof_freeze": freeze,
        "source_sweep": _SWEEP_ID,
        "source_runs": source_entries,
        "selection": {
            "tp_pips": list(_TP_PIPS),
            "stop_loss_pips": 800,
            "complete_cases_only": True,
            "boundary_forced_close_excluded": True,
            "cost_semantics": "unverified",
        },
    }
    return _TpCorpus(
        decision_keys,
        directions,
        max_exit_keys,
        sample_ids,
        origin_ids,
        inputs,
        targets,
        _np.ones(len(rows), dtype=_np.float64),
        source_manifest,
        counts,
    )


def _source_platform(freeze: dict[str, object]) -> _PlatformIdentity:
    raw = _exact_mapping(freeze.get("platform"), label="OOF source platform")
    commit = raw.get("git_commit")
    dirty = raw.get("git_dirty")
    diff_sha = raw.get("diff_sha256")
    if (
        type(commit) is not str
        or dirty is not False
        or diff_sha is not None
    ):
        raise _ContractViolationError("OOF source platform must be one clean commit")
    return _PlatformIdentity(commit, False, None)


def _discover_runs(
    runs_root: _Path,
    *,
    platform: _PlatformIdentity,
    dependency_sha: str,
) -> tuple[_ArmRun, ...]:
    plan = _read_sweep_plan(runs_root, _SWEEP_ID)
    expected = {
        child._experiment_fingerprint: child._child_ordinal for child in plan._children
    }
    if len(expected) != 14:
        raise _ContractViolationError("OOF source sweep must contain exactly 14 children")
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    selected: dict[int, _ArmRun] = {}
    for projection in scan._projections:
        manifest = projection._manifest
        config = projection._resolved_config
        record = projection._record
        if (
            manifest is None
            or config is None
            or record._state is not _RunState.COMPLETE
            or record._canonical is not True
            or manifest.experiment_fingerprint not in expected
        ):
            continue
        if (
            manifest.platform_identity != platform
            or manifest.dependency_lock_sha256 != dependency_sha
        ):
            raise _ContractViolationError("OOF source run platform differs from selector build")
        ordinal = expected[manifest.experiment_fingerprint]
        dataset = _exact_mapping(config.get("dataset"), label="OOF dataset")
        if dataset != _TRAIN_DATASET:
            raise _ContractViolationError("OOF source run is not exact TRAIN")
        execution = _exact_mapping(config.get("execution"), label="OOF execution")
        tp = _decoded_float(execution.get("take_profit_pips"))
        sl = _decoded_float(execution.get("stop_loss_pips"))
        expected_tp = _TP_PIPS[ordinal % len(_TP_PIPS)]
        if tp != float(expected_tp) or sl != 800.0:
            raise _ContractViolationError("OOF source bracket differs")
        features = config.get("features")
        if type(features) is not list or len(features) != 2:
            raise _ContractViolationError("OOF source features differ")
        uls = _exact_mapping(features[0], label="OOF ULS feature")
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
        if "features.npz" not in files or len(intent_files) != 1 or len(trade_files) != 1:
            raise _ContractViolationError("OOF source artifacts are incomplete")
        source = _SourceRun(
            ordinal,
            manifest.run_id,
            manifest.experiment_fingerprint,
            runs_root / "runs" / manifest.run_id,
            dict(_exact_mapping(uls.get("params"), label="OOF ULS params")),
            dict(execution),
            files["features.npz"],
            intent_files,
            trade_files,
        )
        selected[ordinal] = _ArmRun(ordinal // len(_TP_PIPS), expected_tp, source, config)
    if set(selected) != set(range(14)):
        raise _ContractViolationError("canonical OOF source inventory is incomplete")
    return tuple(selected[index] for index in range(14))


def _source_entry(run: _ArmRun) -> dict[str, object]:
    return {
        "fold": run.fold,
        "tp_pips": run.tp_pips,
        "run_id": run.source.run_id,
        "experiment_fingerprint": run.source.fingerprint,
        "artifacts": {
            "features.npz": run.source.feature_sha256,
            **dict(run.source.intent_files),
            **dict(run.source.trade_files),
        },
    }


def _decoded_float(value: object) -> float | None:
    if type(value) is not dict or set(value) != {"__float_hex__"}:
        return None
    encoded = value.get("__float_hex__")
    return float.fromhex(encoded) if type(encoded) is str else None


def _selector_masks(
    corpus: _TpCorpus,
) -> tuple[_npt.NDArray[_np.bool_], _npt.NDArray[_np.bool_], int]:
    unique = _np.unique(corpus.decision_keys)
    boundary = int(unique[_math.floor(unique.size * 0.85)])
    fit = (corpus.decision_keys < boundary) & (corpus.max_exit_keys < boundary)
    test = corpus.decision_keys >= boundary
    if _np.count_nonzero(fit) < 30 or _np.count_nonzero(test) < 10:
        raise _ContractViolationError("ULS TP chronological split is too small")
    return fit, test, boundary


def _combined_fit_inputs(
    *,
    entry: _EntryCorpus,
    tp: _TpCorpus,
    selector_fit: _npt.NDArray[_np.bool_],
) -> tuple[
    dict[str, _npt.NDArray[_np.float64]],
    dict[str, _npt.NDArray[_np.float64]],
    _npt.NDArray[_np.float64],
    int,
]:
    unique = _np.unique(entry.decision_keys)
    boundary = int(unique[_math.floor(unique.size * 0.70)])
    entry_fit = (entry.decision_keys < boundary) & (entry.max_exit_keys < boundary)
    entry_rows = int(_np.count_nonzero(entry_fit))
    selector_rows = int(_np.count_nonzero(selector_fit))
    inputs = {
        name: _np.concatenate((entry.inputs[name][entry_fit], tp.inputs[name][selector_fit]))
        for name in _INPUT_NAMES
    }
    targets: dict[str, _npt.NDArray[_np.float64]] = {
        _ROLE_TARGET: _np.concatenate(
            (
                _np.zeros(entry_rows, dtype=_np.float64),
                _np.ones(selector_rows, dtype=_np.float64),
            )
        ),
        _ENTRY_TARGET: _np.concatenate(
            (entry.target[entry_fit], _np.zeros(selector_rows, dtype=_np.float64))
        ),
    }
    targets.update(
        {
            name: _np.concatenate(
                (_np.zeros(entry_rows, dtype=_np.float64), tp.targets[name][selector_fit])
            )
            for name in _TP_TARGET_NAMES
        }
    )
    weights = _np.concatenate((entry.weights[entry_fit], tp.weights[selector_fit]))
    return inputs, targets, weights, entry_rows


def _qualification(
    corpus: _TpCorpus,
    *,
    test: _npt.NDArray[_np.bool_],
    payload: object,
    boundary: int,
) -> dict[str, object]:
    checked = _typing.cast("_typing.Any", payload)
    matrix = _np.column_stack([corpus.inputs[name][test] for name in _INPUT_NAMES])
    prediction = _predict_prefixed(
        checked,
        matrix,
        prefix=_SELECTOR_PREFIX,
        output_count=len(_TP_PIPS),
    )
    target = _np.column_stack([corpus.targets[name][test] for name in _TP_TARGET_NAMES])
    chosen = _np.argmax(prediction, axis=1)
    dynamic = target[_np.arange(target.shape[0]), chosen]
    fixed_totals = _np.sum(target, axis=0, dtype=_np.float64)
    counts = _np.bincount(chosen, minlength=len(_TP_PIPS))
    best_fixed_index = int(_np.argmax(fixed_totals))
    return {
        "test_boundary": boundary,
        "rows": int(target.shape[0]),
        "dynamic_total_net_pnl": float(_np.sum(dynamic, dtype=_np.float64)),
        "fixed_total_net_pnl": {
            str(tp): float(fixed_totals[index]) for index, tp in enumerate(_TP_PIPS)
        },
        "best_fixed_tp_pips": _TP_PIPS[best_fixed_index],
        "best_fixed_total_net_pnl": float(fixed_totals[best_fixed_index]),
        "dynamic_minus_best_fixed_net_pnl": float(
            _np.sum(dynamic, dtype=_np.float64) - fixed_totals[best_fixed_index]
        ),
        "dynamic_choice_counts": {
            str(tp): int(counts[index]) for index, tp in enumerate(_TP_PIPS)
        },
    }


def _encode_corpus(
    *,
    entry: _EntryCorpus,
    tp: _TpCorpus,
    selector_fit: _npt.NDArray[_np.bool_],
    boundary: int,
    entry_fit_rows: int,
) -> bytes:
    combined_inputs, combined_targets, weights, _rows = _combined_fit_inputs(
        entry=entry,
        tp=tp,
        selector_fit=selector_fit,
    )
    metadata = {
        "format": _CORPUS_FORMAT,
        "format_version": 1,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "entry_fit_rows": entry_fit_rows,
        "selector_fit_rows": int(_np.count_nonzero(selector_fit)),
        "selector_test_boundary": boundary,
    }
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        "__meta__": _np.frombuffer(_canonical_json(metadata).encode("ascii"), dtype=_np.uint8),
        "weights": weights,
        "tp_decision_keys": tp.decision_keys,
        "tp_directions": tp.directions,
        "tp_max_exit_keys": tp.max_exit_keys,
        "tp_sample_ids": tp.sample_ids,
        "tp_origin_ids": tp.origin_ids,
    }
    arrays.update(
        {f"input_{index:04d}": combined_inputs[name] for index, name in enumerate(_INPUT_NAMES)}
    )
    arrays.update(
        {
            f"target_{index:04d}": combined_targets[name]
            for index, name in enumerate(_TARGET_NAMES)
        }
    )
    output = _io.BytesIO()
    _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **arrays)
    payload = output.getvalue()
    _validate_zip_envelope(payload, tuple(arrays))
    return payload


def _assert_equal_tp_corpus(left: _TpCorpus, right: _TpCorpus) -> None:
    pairs: list[
        tuple[_npt.NDArray[_typing.Any], _npt.NDArray[_typing.Any]]
    ] = [
        (left.decision_keys, right.decision_keys),
        (left.directions, right.directions),
        (left.max_exit_keys, right.max_exit_keys),
        (left.sample_ids, right.sample_ids),
        (left.origin_ids, right.origin_ids),
        (left.weights, right.weights),
    ]
    pairs.extend((left.inputs[name], right.inputs[name]) for name in _INPUT_NAMES)
    pairs.extend((left.targets[name], right.targets[name]) for name in _TP_TARGET_NAMES)
    if (
        left.source_manifest != right.source_manifest
        or left.counts != right.counts
        or any(not _np.array_equal(first, second) for first, second in pairs)
    ):
        raise _ContractViolationError("ULS TP corpus double build differs")


def _main(argv: list[str] | None = None) -> int:
    parser = _argparse.ArgumentParser(prog="uls-tp-bundle")
    parser.add_argument("--runs-root", required=True, type=lambda value: _Path(value).resolve())
    parser.add_argument(
        "--repository-root", required=True, type=lambda value: _Path(value).resolve()
    )
    parser.add_argument(
        "--dependency-lock", required=True, type=lambda value: _Path(value).resolve()
    )
    parser.add_argument(
        "--entry-bundle-dir", required=True, type=lambda value: _Path(value).resolve()
    )
    parser.add_argument(
        "--oof-freeze-dir", required=True, type=lambda value: _Path(value).resolve()
    )
    args = parser.parse_args(argv)
    try:
        result = _build(
            runs_root=args.runs_root,
            repository_root=args.repository_root,
            dependency_lock_path=args.dependency_lock,
            entry_bundle_dir=args.entry_bundle_dir,
            oof_freeze_dir=args.oof_freeze_dir,
        )
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2
    _sys.stdout.write(_canonical_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
