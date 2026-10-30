"""Immutable file-truth training bundles for the Phase-7 Zone pilot."""

from __future__ import annotations

import argparse as _argparse
import hashlib as _hashlib
import io as _io
import json as _json
import math as _math
import os as _os
import re as _re
import shutil as _shutil
import subprocess as _subprocess
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
    _decode_fitted_state,
    _encode_fitted_state,
    _params_document,
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
from trading_research.models import CORPUS_RESEARCH_MODELS as _CORPUS_MODELS
from trading_research.models.execution import (
    _fit_corpus_model_payload,
    _snapshot_corpus_model,
)
from trading_research.models.zone_selector import (
    _CONTEXT_FEATURE_NAMES,
    _INPUT_NAMES,
    _MARKET_INPUT_NAMES,
    _NUMERIC_PARAM_NAMES,
    _PARAM_INPUT_NAMES,
    _TARGET_NAMES,
    _V2_INPUT_NAMES,
    _forest_predict,
    _parameter_values,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
    from trading_research.core.research_frames import FittedPayload as _FittedPayload
    from trading_research.core.research_frames import FittedState as _FittedState
    from trading_research.models.execution import _CorpusModelPlan

__all__: list[str] = []

_BUNDLE_SCHEMA = "research-training-bundle-manifest-v1"
_STATE_SCHEMA = "research-training-bundle-state-v1"
_SOURCE_SCHEMA = "zone-selector-source-manifest-v1"
_CORPUS_FORMAT = "zone-selector-corpus-v1"
_REPORT_SCHEMA = "zone-selector-training-report-v1"
_TARGET_SPEC_ID = "matched_bracket_gross_r_v1"
_ZONE_PARAMETER_NAMES = (*_NUMERIC_PARAM_NAMES, "confirmation_mode")
_TARGET_SPEC_PARAMS: _Mapping[str, _ParamValue] = {
    "arm_distances": (2.0, 4.0, 8.0),
    "complete_cases_only": True,
    "risk_distance": 4.0,
}
_TRAINING_SPEC: _Mapping[str, _ParamValue] = {
    "qualification_fit_fraction": 0.8,
    "qualification_split": "unique_decision_keys_chronological_v1",
    "purge": "max_arm_exit_reaches_holdout_boundary_v1",
    "ridge_lambda": 1.0,
    "sample_identity": "feature_origin_digest_opening_intent_key_direction_v1",
    "tie_break": "value_0_value_1_value_2_v1",
    "weighting": "inverse_episode_frequency_v1",
}
_EXPECTED_COMPLETE = 92_330
_EXPECTED_INCOMPLETE = 15_210
_EXPECTED_CONFIGS = 100
_EXPECTED_TRADES = {2.0: 107_369, 4.0: 99_207, 8.0: 92_482}
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = _re.compile(r"^[0-9a-f]{40,64}$")
_BUNDLE_ID = _re.compile(
    r"^(?:zone-tp-selector-v[12]|uls-entry-(?:selector|oof)-v1|"
    r"uls-entry-tp-selector-v1|uls-joint-action-selector-v1)-[0-9a-f]{20}$"
)
_V2_SOURCE_SCHEMA = "zone-selector-source-manifest-v2"
_V2_CORPUS_FORMAT = "zone-selector-corpus-v2"
_V2_REPORT_SCHEMA = "zone-selector-training-report-v2"
_V2_MODEL_PARAMS: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "max_depth": 8,
    "max_features": 0.75,
    "min_samples_leaf": 64,
    "n_estimators": 96,
    "random_seed": 20_260_823,
}
_V2_TRAINING_SPEC: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "max_depth": 8,
    "max_features": 0.75,
    "min_samples_leaf": 64,
    "n_estimators": 96,
    "qualification_fit_fraction": 0.8,
    "qualification_split": "unique_decision_keys_chronological_v1",
    "purge": "max_arm_exit_reaches_holdout_boundary_v1",
    "random_seed": 20_260_823,
    "sample_identity": "feature_origin_digest_opening_intent_key_direction_v1",
    "tie_break": "value_0_value_1_value_2_v1",
    "weighting": "inverse_episode_frequency_v1",
}


@_dataclass(frozen=True, slots=True)
class _SourceRun:
    run_id: str
    fingerprint: str
    distance: float
    config_digest: str
    config_params: dict[str, object]
    execution_config: dict[str, object]
    run_dir: _Path
    feature_name: str
    feature_sha256: str
    intent_files: tuple[tuple[str, str], ...]
    trade_files: tuple[tuple[str, str], ...]


@_dataclass(frozen=True, slots=True)
class _TradeObservation:
    intent_key: int
    direction: int
    entry_key: int
    exit_key: int
    entry_price: float
    exit_price: float


@_dataclass(frozen=True, slots=True)
class _CorpusProduct:
    source_manifest: dict[str, object]
    corpus_identity_key: str
    sample_ids: _npt.NDArray[_np.uint8]
    feature_origin_ids: _npt.NDArray[_np.uint8]
    decision_keys: _npt.NDArray[_np.int64]
    directions: _npt.NDArray[_np.int8]
    max_exit_keys: _npt.NDArray[_np.int64]
    weights: _npt.NDArray[_np.float64]
    inputs: dict[str, _npt.NDArray[_np.float64]]
    targets: dict[str, _npt.NDArray[_np.float64]]
    counts: dict[str, object]


@_dataclass(frozen=True, slots=True)
class _FrozenReference:
    bundle_id: str
    manifest_sha256: str
    fitted_state_identity_key: str
    fitted_state_artifact_sha256: str


def _build_zone_selector_bundle(
    *,
    runs_root: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
) -> dict[str, object]:
    """Build, double-check, fit, and atomically publish the fixed Phase-7 bundle."""
    _assert_worker_environment()
    for path, label in (
        (runs_root, "runs_root"),
        (repository_root, "repository_root"),
        (dependency_lock_path, "dependency_lock_path"),
    ):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    commit = _clean_commit(repository_root)
    dependency_sha = _sha256_file(dependency_lock_path)
    platform = _PlatformIdentity(commit, False, None)
    first = _construct_zone_corpus(
        runs_root=runs_root,
        platform=platform,
        dependency_sha256=dependency_sha,
    )
    second = _construct_zone_corpus(
        runs_root=runs_root,
        platform=platform,
        dependency_sha256=dependency_sha,
    )
    _assert_equal_corpus(first, second)
    source_bytes = _json_bytes(first.source_manifest)
    corpus_bytes = _encode_corpus(first)
    source_sha = _sha256_bytes(source_bytes)
    corpus_sha = _sha256_bytes(corpus_bytes)
    bundle_id = f"zone-tp-selector-v1-{first.corpus_identity_key[:20]}"
    bundle_root = runs_root / "training-bundles"
    destination = bundle_root / bundle_id
    is_new = not destination.exists()
    if is_new:
        _begin_bundle_lifecycle(bundle_root=bundle_root, bundle_id=bundle_id)
    try:
        return _fit_and_publish_bundle(
            corpus=first,
            source_bytes=source_bytes,
            corpus_bytes=corpus_bytes,
            source_sha=source_sha,
            corpus_sha=corpus_sha,
            platform=platform,
            dependency_sha=dependency_sha,
            commit=commit,
            destination=destination,
            is_new=is_new,
        )
    except BaseException:
        if is_new:
            _fail_bundle_lifecycle(destination)
        raise


def _build_zone_selector_v2_bundle(
    *,
    runs_root: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
) -> dict[str, object]:
    """Build the frozen nonlinear V2 bundle from TRAIN-only file truth."""
    _assert_worker_environment()
    for path, label in (
        (runs_root, "runs_root"),
        (repository_root, "repository_root"),
        (dependency_lock_path, "dependency_lock_path"),
    ):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    commit = _clean_commit(repository_root)
    dependency_sha = _sha256_file(dependency_lock_path)
    platform = _PlatformIdentity(commit, False, None)
    first = _construct_zone_v2_corpus(
        runs_root=runs_root,
        platform=platform,
        dependency_sha256=dependency_sha,
    )
    second = _construct_zone_v2_corpus(
        runs_root=runs_root,
        platform=platform,
        dependency_sha256=dependency_sha,
    )
    _assert_equal_corpus_for_inputs(first, second, input_names=_V2_INPUT_NAMES)
    source_bytes = _json_bytes(first.source_manifest)
    corpus_bytes = _encode_corpus_for_inputs(
        first,
        input_names=_V2_INPUT_NAMES,
        corpus_format=_V2_CORPUS_FORMAT,
    )
    source_sha = _sha256_bytes(source_bytes)
    corpus_sha = _sha256_bytes(corpus_bytes)
    bundle_id = f"zone-tp-selector-v2-{first.corpus_identity_key[:20]}"
    bundle_root = runs_root / "training-bundles"
    destination = bundle_root / bundle_id
    is_new = not destination.exists()
    if is_new:
        _begin_bundle_lifecycle(bundle_root=bundle_root, bundle_id=bundle_id)
    try:
        return _fit_and_publish_v2_bundle(
            corpus=first,
            source_bytes=source_bytes,
            corpus_bytes=corpus_bytes,
            source_sha=source_sha,
            corpus_sha=corpus_sha,
            platform=platform,
            dependency_sha=dependency_sha,
            commit=commit,
            destination=destination,
            is_new=is_new,
        )
    except BaseException:
        if is_new:
            _fail_bundle_lifecycle(destination)
        raise


def _fit_and_publish_bundle(
    *,
    corpus: _CorpusProduct,
    source_bytes: bytes,
    corpus_bytes: bytes,
    source_sha: str,
    corpus_sha: str,
    platform: _PlatformIdentity,
    dependency_sha: str,
    commit: str,
    destination: _Path,
    is_new: bool,
) -> dict[str, object]:
    model = _CORPUS_MODELS.create("zone_ridge_selector_v1", ridge_lambda=1.0)
    plan = _snapshot_corpus_model(model)
    qualification = _qualification_report(corpus, plan=plan)
    model_inputs = _make_corpus_model_inputs(
        inputs=corpus.inputs,
        targets=corpus.targets,
        weights=corpus.weights,
    )
    payload_first = _fit_corpus_model_payload(plan, inputs=model_inputs)
    payload_second = _fit_corpus_model_payload(plan, inputs=model_inputs)
    _assert_equal_payload(payload_first, payload_second)
    state_identity = _CorpusFittedStateIdentity(
        plugin_id=plan._origin.plugin_id,
        plugin_version=plan._origin.version,
        plugin_params=plan._origin.params,
        inference_feature_names=plan._required_feature_names,
        corpus_identity_key=corpus.corpus_identity_key,
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
    state = _bind_fitted_state(state_identity, payload_first)
    fitted_bytes = _encode_fitted_state(state_identity, state)
    if fitted_bytes != _encode_fitted_state(
        state_identity, _bind_fitted_state(state_identity, payload_second)
    ):
        raise _ContractViolationError("fitted-state double encoding differs")
    fitted_sha = _sha256_bytes(fitted_bytes)
    report: dict[str, object] = {
        "schema": _REPORT_SCHEMA,
        "corpus_identity_key": corpus.corpus_identity_key,
        "corpus_sha256": corpus_sha,
        "determinism": {
            "corpus_double_build_equal": True,
            "fitted_payload_double_fit_equal": True,
            "fitted_state_double_encode_equal": True,
        },
        "fitted_state_identity_key": state.identity_key(),
        "fitted_state_sha256": fitted_sha,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "counts": corpus.counts,
        "qualification": qualification,
        "solver": {
            "algorithm": "weighted_standardized_multi_output_ridge_closed_form_v1",
            "constant_column_scale": 1.0,
            "intercept_penalized": False,
            "ridge_lambda": 1.0,
        },
        "source_manifest_sha256": source_sha,
        "training_spec": _TRAINING_SPEC,
    }
    artifacts: dict[str, bytes] = {
        "corpus.npz": corpus_bytes,
        "fitted-state.npz": fitted_bytes,
        "source-manifest.json": source_bytes,
        "training-report.json": _json_bytes(report),
    }
    artifact_hashes = {name: _sha256_bytes(value) for name, value in sorted(artifacts.items())}
    complete_lifecycle = _lifecycle_bytes(("PLANNED", "RUNNING", "COMPLETE"))
    manifest: dict[str, object] = {
        "schema": _BUNDLE_SCHEMA,
        "bundle_id": destination.name,
        "state": "COMPLETE",
        "files": artifact_hashes,
        "lifecycle_sha256": _sha256_bytes(complete_lifecycle),
        "corpus_identity_key": corpus.corpus_identity_key,
        "fitted_state_identity_key": state.identity_key(),
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    manifest_bytes = _json_bytes(manifest)
    if not is_new:
        existing = _read_bundle_manifest(destination)
        if _json_bytes(existing) != manifest_bytes:
            raise _ContractViolationError("existing immutable bundle differs from rebuilt content")
        _verify_bundle_files(destination, existing)
        return _bundle_reference_document(existing, manifest_sha256=_sha256_bytes(manifest_bytes))
    _publish_new_bundle(
        destination=destination,
        artifacts=artifacts,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )
    return _bundle_reference_document(manifest, manifest_sha256=_sha256_bytes(manifest_bytes))


def _fit_and_publish_v2_bundle(
    *,
    corpus: _CorpusProduct,
    source_bytes: bytes,
    corpus_bytes: bytes,
    source_sha: str,
    corpus_sha: str,
    platform: _PlatformIdentity,
    dependency_sha: str,
    commit: str,
    destination: _Path,
    is_new: bool,
) -> dict[str, object]:
    """Fit, double-check and publish the exact preregistered V2 forest."""
    model = _CORPUS_MODELS.create("zone_forest_selector_v2", **_V2_MODEL_PARAMS)
    plan = _snapshot_corpus_model(model)
    qualification = _qualification_report_v2(corpus, plan=plan)
    model_inputs = _make_corpus_model_inputs(
        inputs=corpus.inputs,
        targets=corpus.targets,
        weights=corpus.weights,
    )
    payload_first = _fit_corpus_model_payload(plan, inputs=model_inputs)
    payload_second = _fit_corpus_model_payload(plan, inputs=model_inputs)
    _assert_equal_payload(payload_first, payload_second)
    state_identity = _CorpusFittedStateIdentity(
        plugin_id=plan._origin.plugin_id,
        plugin_version=plan._origin.version,
        plugin_params=plan._origin.params,
        inference_feature_names=plan._required_feature_names,
        corpus_identity_key=corpus.corpus_identity_key,
        corpus_artifact_sha256=corpus_sha,
        source_manifest_sha256=source_sha,
        target_spec_id=_TARGET_SPEC_ID,
        target_spec_version=1,
        target_spec_params=_TARGET_SPEC_PARAMS,
        training_spec=_V2_TRAINING_SPEC,
        platform_identity=platform,
        dependency_lock_sha256=dependency_sha,
        comparison_tolerance=plan._comparison_tolerance,
    )
    state = _bind_fitted_state(state_identity, payload_first)
    fitted_bytes = _encode_fitted_state(state_identity, state)
    if fitted_bytes != _encode_fitted_state(
        state_identity, _bind_fitted_state(state_identity, payload_second)
    ):
        raise _ContractViolationError("V2 fitted-state double encoding differs")
    fitted_sha = _sha256_bytes(fitted_bytes)
    report: dict[str, object] = {
        "schema": _V2_REPORT_SCHEMA,
        "corpus_identity_key": corpus.corpus_identity_key,
        "corpus_sha256": corpus_sha,
        "determinism": {
            "corpus_double_build_equal": True,
            "fitted_payload_double_fit_equal": True,
            "fitted_state_double_encode_equal": True,
        },
        "fitted_state_identity_key": state.identity_key(),
        "fitted_state_sha256": fitted_sha,
        "input_names": list(_V2_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "counts": corpus.counts,
        "qualification": qualification,
        "solver": {
            "algorithm": "weighted_multi_output_random_forest_v2",
            **_V2_MODEL_PARAMS,
            "n_jobs": 1,
        },
        "source_manifest_sha256": source_sha,
        "training_spec": _V2_TRAINING_SPEC,
    }
    artifacts: dict[str, bytes] = {
        "corpus.npz": corpus_bytes,
        "fitted-state.npz": fitted_bytes,
        "source-manifest.json": source_bytes,
        "training-report.json": _json_bytes(report),
    }
    artifact_hashes = {name: _sha256_bytes(value) for name, value in sorted(artifacts.items())}
    complete_lifecycle = _lifecycle_bytes(("PLANNED", "RUNNING", "COMPLETE"))
    manifest: dict[str, object] = {
        "schema": _BUNDLE_SCHEMA,
        "bundle_id": destination.name,
        "state": "COMPLETE",
        "files": artifact_hashes,
        "lifecycle_sha256": _sha256_bytes(complete_lifecycle),
        "corpus_identity_key": corpus.corpus_identity_key,
        "fitted_state_identity_key": state.identity_key(),
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    manifest_bytes = _json_bytes(manifest)
    if not is_new:
        existing = _read_bundle_manifest(destination)
        if _json_bytes(existing) != manifest_bytes:
            raise _ContractViolationError(
                "existing immutable V2 bundle differs from rebuilt content"
            )
        _verify_bundle_files(destination, existing)
        return _bundle_reference_document(existing, manifest_sha256=_sha256_bytes(manifest_bytes))
    _publish_new_bundle(
        destination=destination,
        artifacts=artifacts,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )
    return _bundle_reference_document(manifest, manifest_sha256=_sha256_bytes(manifest_bytes))


def _publish_new_bundle(
    *,
    destination: _Path,
    artifacts: dict[str, bytes],
    manifest: dict[str, object],
    manifest_bytes: bytes,
) -> None:
    for name, payload in artifacts.items():
        _write_new(destination / name, payload)
    _write_new(destination / "manifest.json", manifest_bytes)
    _transition_bundle_state(destination, "COMPLETE")
    _os.chmod(destination / "lifecycle.jsonl", 0o444)
    _verify_bundle_files(destination, manifest)


def _begin_bundle_lifecycle(*, bundle_root: _Path, bundle_id: str) -> None:
    bundle_root.mkdir(parents=True, exist_ok=True)
    destination = bundle_root / bundle_id
    temporary = bundle_root / f".{bundle_id}.{_os.getpid():x}.register"
    if destination.exists() or temporary.exists():
        raise _ContractViolationError("bundle lifecycle target already exists")
    registered = False
    try:
        temporary.mkdir(mode=0o700)
        _write_mutable_new(
            temporary / "lifecycle.jsonl",
            _lifecycle_bytes(("PLANNED",)),
        )
        _write_new(temporary / "state.json", _bundle_state_bytes(bundle_id, "PLANNED"))
        _os.replace(temporary, destination)
        registered = True
        _transition_bundle_state(destination, "RUNNING")
    except BaseException:
        _shutil.rmtree(temporary, ignore_errors=True)
        if registered:
            _fail_bundle_lifecycle(destination)
        raise


def _transition_bundle_state(bundle: _Path, state: str) -> None:
    current = _read_json(bundle / "state.json")
    observed = current.get("state")
    permitted = {("PLANNED", "RUNNING"), ("RUNNING", "COMPLETE"), ("RUNNING", "FAILED")}
    if (observed, state) not in permitted:
        raise _ContractViolationError("bundle lifecycle transition is invalid")
    _append_lifecycle(bundle / "lifecycle.jsonl", state)
    _atomic_replace_bytes(bundle / "state.json", _bundle_state_bytes(bundle.name, state))


def _fail_bundle_lifecycle(bundle: _Path) -> None:
    try:
        state = _read_json(bundle / "state.json")
        if state.get("state") not in {"PLANNED", "RUNNING", "COMPLETE"}:
            return
        lifecycle = _lifecycle_bytes(("PLANNED", "RUNNING", "FAILED"))
        failure_manifest = _json_bytes(
            {
                "schema": "research-training-bundle-failure-v1",
                "bundle_id": bundle.name,
                "state": "FAILED",
                "failure_reason": "bundle construction failed",
                "lifecycle_sha256": _sha256_bytes(lifecycle),
            }
        )
        manifest_path = bundle / "manifest.json"
        if manifest_path.exists():
            _atomic_replace_bytes(manifest_path, failure_manifest)
        else:
            _write_new(manifest_path, failure_manifest)
        lifecycle_path = bundle / "lifecycle.jsonl"
        _atomic_replace_bytes(lifecycle_path, lifecycle)
        _atomic_replace_bytes(bundle / "state.json", _bundle_state_bytes(bundle.name, "FAILED"))
        if lifecycle_path.read_bytes() != lifecycle:
            raise _ContractViolationError("failed bundle lifecycle history differs")
        _os.chmod(lifecycle_path, 0o444)
    except BaseException:
        # Preserve the original construction exception. Any partially terminalized
        # directory remains fail-closed because it cannot validate as COMPLETE.
        return


def _bundle_state_bytes(bundle_id: str, state: str) -> bytes:
    return _json_bytes({"schema": _STATE_SCHEMA, "bundle_id": bundle_id, "state": state})


def _lifecycle_bytes(states: tuple[str, ...]) -> bytes:
    return b"".join(
        _json_bytes(
            {
                "schema": "research-training-bundle-lifecycle-event-v1",
                "ordinal": ordinal,
                "state": state,
            }
        )
        for ordinal, state in enumerate(states)
    )


def _write_mutable_new(path: _Path, payload: bytes) -> None:
    descriptor = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600)
    try:
        _write_all(descriptor, payload)
        _os.fsync(descriptor)
    finally:
        _os.close(descriptor)


def _append_lifecycle(path: _Path, state: str) -> None:
    current = path.read_bytes()
    ordinal = current.count(b"\n")
    payload = _json_bytes(
        {
            "schema": "research-training-bundle-lifecycle-event-v1",
            "ordinal": ordinal,
            "state": state,
        }
    )
    descriptor = _os.open(path, _os.O_WRONLY | _os.O_APPEND)
    try:
        _write_all(descriptor, payload)
        _os.fsync(descriptor)
    finally:
        _os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = _os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("bundle lifecycle write made no progress")
        remaining = remaining[written:]


def _atomic_replace_bytes(path: _Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{_os.getpid():x}.tmp")
    _write_new(temporary, payload)
    _os.replace(temporary, path)


def _construct_zone_corpus(
    *,
    runs_root: _Path,
    platform: _PlatformIdentity,
    dependency_sha256: str,
) -> _CorpusProduct:
    grouped = _discover_primary_sources(runs_root)
    rows: list[
        tuple[bytes, bytes, int, int, int, _npt.NDArray[_np.float64], tuple[float, ...]]
    ] = []
    raw_counts = _Counter[float]()
    availability_counts = _Counter[int]()
    source_entries: list[dict[str, object]] = []
    configuration_entries: list[dict[str, object]] = []
    evaluation_execution: dict[str, object] | None = None
    for config_digest in sorted(grouped):
        arms = grouped[config_digest]
        feature = _read_feature_artifact(arms[2.0])
        origin_digest = _typing.cast("bytes", feature["origin_digest"])
        origin = _exact_mapping(feature.get("origin"), label="source FeatureOrigin")
        origin_params = _exact_mapping(origin.get("params"), label="FeatureOrigin params")
        if (
            origin.get("provider_id") != "zone_trader_v13"
            or origin.get("version") != 1
            or _canonical_json(origin_params) != _canonical_json(arms[2.0].config_params)
        ):
            raise _ContractViolationError(
                "source config does not match the consumed artifact FeatureOrigin"
            )
        keys = _typing.cast("_npt.NDArray[_np.int64]", feature["keys"])
        feature_columns = _typing.cast("dict[str, _npt.NDArray[_np.float64]]", feature["columns"])
        configuration_entries.append(
            {
                "feature_config_digest": config_digest,
                "feature_origin_digest": origin_digest.hex(),
                "params": arms[2.0].config_params,
            }
        )
        observations: dict[float, dict[tuple[int, int], _TradeObservation]] = {}
        for distance in (2.0, 4.0, 8.0):
            source = arms[distance]
            candidate_execution = dict(source.execution_config)
            candidate_execution["stop_loss_pips"] = None
            candidate_execution["take_profit_pips"] = None
            if evaluation_execution is None:
                evaluation_execution = candidate_execution
            elif _canonical_json(candidate_execution) != _canonical_json(
                evaluation_execution
            ):
                raise _ContractViolationError(
                    "matched sources do not share one execution/cost specification"
                )
            arm_feature = feature if distance == 2.0 else _read_feature_artifact(source)
            _validate_same_feature(arm_feature, feature)
            observed = _read_run_observations(source, feature_keys=keys)
            observations[distance] = observed
            raw_counts[distance] += len(observed)
            source_entries.append(
                _source_entry(
                    source,
                    feature_origin_digest=_typing.cast(
                        "bytes", arm_feature["origin_digest"]
                    ).hex(),
                )
            )
        all_keys = set().union(*(set(value) for value in observations.values()))
        complete = set.intersection(*(set(value) for value in observations.values()))
        for key in all_keys:
            availability_counts[sum(key in observations[arm] for arm in (2.0, 4.0, 8.0))] += 1
        params = arms[2.0].config_params
        if set(params) != set(_ZONE_PARAMETER_NAMES):
            raise _ContractViolationError("source Zone parameter schema differs")
        parameter_values = _parameter_values(_decoded_params(params))
        for intent_key, direction in sorted(complete):
            position = int(_np.searchsorted(keys, intent_key))
            if position >= keys.size or int(keys[position]) != intent_key:
                raise _ContractViolationError("opening intent key is unavailable in features")
            predictor = _predictor_row(
                columns=feature_columns,
                position=position,
                parameter_values=parameter_values,
                expected_direction=direction,
            )
            arm_rows = tuple(observations[arm][(intent_key, direction)] for arm in (2.0, 4.0, 8.0))
            target_values = tuple(
                item.direction * (item.exit_price - item.entry_price) / 4.0 for item in arm_rows
            )
            max_exit = max(item.exit_key for item in arm_rows)
            sample_preimage = (
                origin_digest
                + int(intent_key).to_bytes(8, "big", signed=True)
                + int(direction).to_bytes(1, "big", signed=True)
            )
            rows.append(
                (
                    _hashlib.sha256(sample_preimage).digest(),
                    origin_digest,
                    intent_key,
                    direction,
                    max_exit,
                    predictor,
                    target_values,
                )
            )
    rows.sort(key=lambda item: (item[1], item[2], item[3]))
    if len(rows) != _EXPECTED_COMPLETE:
        raise _ContractViolationError(
            f"complete matched corpus has {len(rows)} rows, expected {_EXPECTED_COMPLETE}"
        )
    incomplete = availability_counts[1] + availability_counts[2]
    if incomplete != _EXPECTED_INCOMPLETE or availability_counts[3] != _EXPECTED_COMPLETE:
        raise _ContractViolationError(
            "matched-arm exclusion counts differ from the frozen inventory"
        )
    if dict(raw_counts) != _EXPECTED_TRADES:
        raise _ContractViolationError("raw TradeLog counts differ from the frozen inventory")
    episode_frequency = _Counter((item[2], item[3]) for item in rows)
    predictors = _np.vstack([item[5] for item in rows]).astype(_np.float64, copy=False)
    target_matrix = _np.asarray([item[6] for item in rows], dtype=_np.float64)
    sample_ids = _np.vstack([_np.frombuffer(item[0], dtype=_np.uint8) for item in rows])
    origin_ids = _np.vstack([_np.frombuffer(item[1], dtype=_np.uint8) for item in rows])
    decision_keys = _np.asarray([item[2] for item in rows], dtype=_np.int64)
    directions = _np.asarray([item[3] for item in rows], dtype=_np.int8)
    max_exit_keys = _np.asarray([item[4] for item in rows], dtype=_np.int64)
    weights = _np.asarray(
        [1.0 / episode_frequency[(item[2], item[3])] for item in rows],
        dtype=_np.float64,
    )
    inputs = {name: predictors[:, index] for index, name in enumerate(_INPUT_NAMES)}
    target_columns = {name: target_matrix[:, index] for index, name in enumerate(_TARGET_NAMES)}
    if evaluation_execution is None:  # pragma: no cover - 300 required sources establish it
        raise AssertionError("corpus sources did not establish an execution specification")
    source_manifest: dict[str, object] = {
        "schema": _SOURCE_SCHEMA,
        "dataset": {
            "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
            "manifest_sha256": "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
            "role": "train",
        },
        "configurations": configuration_entries,
        "evaluation_execution": evaluation_execution,
        "sources": sorted(source_entries, key=lambda item: _typing.cast("str", item["run_id"])),
        "selection": {
            "actions": [2.0, 4.0, 8.0],
            "complete_cases_only": True,
            "configuration_count": len(grouped),
            "feature_origin_digest_count": len(
                {item["feature_origin_digest"] for item in source_entries}
            ),
            "source_run_count": len(source_entries),
        },
    }
    source_sha = _sha256_bytes(_json_bytes(source_manifest))
    identity_document: dict[str, object] = {
        "format": "zone-selector-corpus-identity-v1",
        "builder": {"plugin_id": "zone_selector_corpus_builder_v1", "version": 1},
        "source_manifest_sha256": source_sha,
        "sample_count": len(rows),
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "target_spec": {
            "plugin_id": _TARGET_SPEC_ID,
            "version": 1,
            "params": _TARGET_SPEC_PARAMS,
        },
        "training_spec": _TRAINING_SPEC,
        "platform": {
            "git_commit": platform.git_commit,
            "git_dirty": platform.git_dirty,
            "diff_sha256": platform.diff_sha256,
        },
        "dependency_lock_sha256": dependency_sha256,
        "seed": None,
    }
    identity_key = _identity_key_from_canonical(_canonical_identity_json(identity_document))
    counts: dict[str, object] = {
        "complete_rows": len(rows),
        "configurations": len(grouped),
        "incomplete_excluded": incomplete,
        "one_arm": availability_counts[1],
        "two_arms": availability_counts[2],
        "raw_trades": {str(int(arm * 100.0)): raw_counts[arm] for arm in (2.0, 4.0, 8.0)},
        "unique_episodes": len(episode_frequency),
    }
    return _CorpusProduct(
        source_manifest,
        identity_key,
        sample_ids,
        origin_ids,
        decision_keys,
        directions,
        max_exit_keys,
        weights,
        inputs,
        target_columns,
        counts,
    )


def _construct_zone_v2_corpus(
    *,
    runs_root: _Path,
    platform: _PlatformIdentity,
    dependency_sha256: str,
) -> _CorpusProduct:
    """Join immutable V1 arm outcomes to exact TRAIN context carriers."""
    grouped = _discover_primary_sources(runs_root)
    carriers = _discover_v2_context_sources(
        runs_root,
        expected_digests=frozenset(grouped),
        expected_platform=platform,
        dependency_sha256=dependency_sha256,
    )
    rows: list[
        tuple[bytes, bytes, int, int, int, _npt.NDArray[_np.float64], tuple[float, ...]]
    ] = []
    raw_counts = _Counter[float]()
    availability_counts = _Counter[int]()
    source_entries: list[dict[str, object]] = []
    carrier_entries: list[dict[str, object]] = []
    configuration_entries: list[dict[str, object]] = []
    evaluation_execution: dict[str, object] | None = None
    for config_digest in sorted(grouped):
        arms = grouped[config_digest]
        base_feature = _read_feature_artifact(arms[2.0])
        carrier_source = carriers[config_digest]
        carrier = _read_v2_feature_artifact(carrier_source)
        _validate_v2_carrier(carrier, base_feature)
        origin_digest = _typing.cast("bytes", base_feature["origin_digest"])
        context_origin_digest = _typing.cast("bytes", carrier["context_origin_digest"])
        origin = _exact_mapping(base_feature.get("origin"), label="source FeatureOrigin")
        origin_params = _exact_mapping(origin.get("params"), label="FeatureOrigin params")
        context_origin = _exact_mapping(
            carrier.get("context_origin"), label="context FeatureOrigin"
        )
        if (
            origin.get("provider_id") != "zone_trader_v13"
            or origin.get("version") != 1
            or context_origin.get("provider_id") != "zone_context_v1"
            or context_origin.get("version") != 1
            or _canonical_json(origin_params)
            != _canonical_json(_exact_mapping(context_origin.get("params"), label="context params"))
            or _canonical_json(origin_params) != _canonical_json(arms[2.0].config_params)
        ):
            raise _ContractViolationError("V2 carrier origins do not match the Zone configuration")
        keys = _typing.cast("_npt.NDArray[_np.int64]", carrier["keys"])
        columns = _typing.cast(
            "dict[str, _npt.NDArray[_np.float64]]", carrier["columns"]
        )
        configuration_entries.append(
            {
                "feature_config_digest": config_digest,
                "feature_origin_digest": origin_digest.hex(),
                "context_origin_digest": context_origin_digest.hex(),
                "params": arms[2.0].config_params,
            }
        )
        observations: dict[float, dict[tuple[int, int], _TradeObservation]] = {}
        for distance in (2.0, 4.0, 8.0):
            source = arms[distance]
            candidate_execution = dict(source.execution_config)
            candidate_execution["stop_loss_pips"] = None
            candidate_execution["take_profit_pips"] = None
            if evaluation_execution is None:
                evaluation_execution = candidate_execution
            elif _canonical_json(candidate_execution) != _canonical_json(
                evaluation_execution
            ):
                raise _ContractViolationError("V2 matched sources differ in execution semantics")
            arm_feature = base_feature if distance == 2.0 else _read_feature_artifact(source)
            _validate_same_feature(arm_feature, base_feature)
            observed = _read_run_observations(source, feature_keys=keys)
            observations[distance] = observed
            raw_counts[distance] += len(observed)
            source_entries.append(
                _source_entry(
                    source,
                    feature_origin_digest=_typing.cast(
                        "bytes", arm_feature["origin_digest"]
                    ).hex(),
                )
            )
        carrier_entries.append(
            {
                "run_id": carrier_source.run_id,
                "experiment_fingerprint": carrier_source.fingerprint,
                "feature_config_digest": config_digest,
                "zone_origin_digest": origin_digest.hex(),
                "context_origin_digest": context_origin_digest.hex(),
                "artifact": {
                    "name": carrier_source.feature_name,
                    "sha256": carrier_source.feature_sha256,
                },
            }
        )
        all_keys = set().union(*(set(value) for value in observations.values()))
        complete = set.intersection(*(set(value) for value in observations.values()))
        for key in all_keys:
            availability_counts[sum(key in observations[arm] for arm in (2.0, 4.0, 8.0))] += 1
        params = arms[2.0].config_params
        if set(params) != set(_ZONE_PARAMETER_NAMES):
            raise _ContractViolationError("V2 source Zone parameter schema differs")
        parameter_values = _parameter_values(_decoded_params(params))
        for intent_key, direction in sorted(complete):
            position = int(_np.searchsorted(keys, intent_key))
            if position >= keys.size or int(keys[position]) != intent_key:
                raise _ContractViolationError("V2 opening intent key is unavailable in context")
            predictor = _v2_predictor_row(
                columns=columns,
                position=position,
                parameter_values=parameter_values,
                expected_direction=direction,
            )
            arm_rows = tuple(observations[arm][(intent_key, direction)] for arm in (2.0, 4.0, 8.0))
            target_values = tuple(
                item.direction * (item.exit_price - item.entry_price) / 4.0 for item in arm_rows
            )
            max_exit = max(item.exit_key for item in arm_rows)
            sample_preimage = (
                origin_digest
                + context_origin_digest
                + int(intent_key).to_bytes(8, "big", signed=True)
                + int(direction).to_bytes(1, "big", signed=True)
            )
            rows.append(
                (
                    _hashlib.sha256(sample_preimage).digest(),
                    origin_digest,
                    intent_key,
                    direction,
                    max_exit,
                    predictor,
                    target_values,
                )
            )
    rows.sort(key=lambda item: (item[1], item[2], item[3]))
    if len(rows) != _EXPECTED_COMPLETE:
        raise _ContractViolationError(
            f"V2 complete matched corpus has {len(rows)} rows, expected {_EXPECTED_COMPLETE}"
        )
    incomplete = availability_counts[1] + availability_counts[2]
    if incomplete != _EXPECTED_INCOMPLETE or availability_counts[3] != _EXPECTED_COMPLETE:
        raise _ContractViolationError("V2 matched-arm exclusion counts differ from V1 truth")
    if dict(raw_counts) != _EXPECTED_TRADES:
        raise _ContractViolationError("V2 raw TradeLog counts differ from V1 truth")
    episode_frequency = _Counter((item[2], item[3]) for item in rows)
    predictors = _np.vstack([item[5] for item in rows]).astype(_np.float64, copy=False)
    target_matrix = _np.asarray([item[6] for item in rows], dtype=_np.float64)
    sample_ids = _np.vstack([_np.frombuffer(item[0], dtype=_np.uint8) for item in rows])
    origin_ids = _np.vstack([_np.frombuffer(item[1], dtype=_np.uint8) for item in rows])
    decision_keys = _np.asarray([item[2] for item in rows], dtype=_np.int64)
    directions = _np.asarray([item[3] for item in rows], dtype=_np.int8)
    max_exit_keys = _np.asarray([item[4] for item in rows], dtype=_np.int64)
    weights = _np.asarray(
        [1.0 / episode_frequency[(item[2], item[3])] for item in rows],
        dtype=_np.float64,
    )
    inputs = {name: predictors[:, index] for index, name in enumerate(_V2_INPUT_NAMES)}
    target_columns = {name: target_matrix[:, index] for index, name in enumerate(_TARGET_NAMES)}
    if evaluation_execution is None:  # pragma: no cover - required sources establish it
        raise AssertionError("V2 corpus sources did not establish execution semantics")
    source_manifest: dict[str, object] = {
        "schema": _V2_SOURCE_SCHEMA,
        "dataset": {
            "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
            "manifest_sha256": "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
            "role": "train",
        },
        "configurations": configuration_entries,
        "context_carriers": sorted(
            carrier_entries, key=lambda item: _typing.cast("str", item["run_id"])
        ),
        "evaluation_execution": evaluation_execution,
        "outcome_sources": sorted(
            source_entries, key=lambda item: _typing.cast("str", item["run_id"])
        ),
        "selection": {
            "actions": [2.0, 4.0, 8.0],
            "complete_cases_only": True,
            "configuration_count": len(grouped),
            "context_carrier_count": len(carriers),
            "outcome_source_run_count": len(source_entries),
        },
    }
    source_sha = _sha256_bytes(_json_bytes(source_manifest))
    identity_document: dict[str, object] = {
        "format": "zone-selector-corpus-identity-v2",
        "builder": {"plugin_id": "zone_selector_corpus_builder_v2", "version": 1},
        "source_manifest_sha256": source_sha,
        "sample_count": len(rows),
        "input_names": list(_V2_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "target_spec": {
            "plugin_id": _TARGET_SPEC_ID,
            "version": 1,
            "params": _TARGET_SPEC_PARAMS,
        },
        "training_spec": _V2_TRAINING_SPEC,
        "platform": {
            "git_commit": platform.git_commit,
            "git_dirty": platform.git_dirty,
            "diff_sha256": platform.diff_sha256,
        },
        "dependency_lock_sha256": dependency_sha256,
        "seed": 20_260_823,
    }
    identity_key = _identity_key_from_canonical(_canonical_identity_json(identity_document))
    counts: dict[str, object] = {
        "complete_rows": len(rows),
        "configurations": len(grouped),
        "context_carriers": len(carriers),
        "incomplete_excluded": incomplete,
        "one_arm": availability_counts[1],
        "two_arms": availability_counts[2],
        "raw_trades": {str(int(arm * 100.0)): raw_counts[arm] for arm in (2.0, 4.0, 8.0)},
        "unique_episodes": len(episode_frequency),
    }
    return _CorpusProduct(
        source_manifest,
        identity_key,
        sample_ids,
        origin_ids,
        decision_keys,
        directions,
        max_exit_keys,
        weights,
        inputs,
        target_columns,
        counts,
    )


def _discover_primary_sources(runs_root: _Path) -> dict[str, dict[float, _SourceRun]]:
    candidates: dict[tuple[str, float, str], list[_SourceRun]] = _defaultdict(list)
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    if scan._errors:
        raise _ContractViolationError("authoritative source-run scan reported corruption")
    for projection in scan._projections:
        manifest = projection._manifest
        config = projection._resolved_config
        record = projection._record
        if (
            record._state is not _RunState.COMPLETE
            or record._canonical is not True
            or manifest is None
            or config.get("schema") != "research-experiment-config-v1"
        ):
            continue
        dataset = _exact_mapping(config.get("dataset"), label="source dataset")
        if dataset.get("role") != "train":
            continue
        if dataset.get("dataset_id") != "XAUUSD_S_M1_TRAIN_V1":
            continue
        if (
            dataset.get("manifest_sha256")
            != "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e"
        ):
            raise _ContractViolationError("TRAIN source dataset identity differs")
        features = config.get("features")
        policies = config.get("policies")
        if type(features) is not list or len(features) != 1 or type(policies) is not list:
            continue
        feature = _exact_mapping(features[0], label="source feature")
        if feature.get("name") != "zone_trader_v13" or feature.get("version") != 1:
            continue
        if (
            len(policies) != 1
            or _exact_mapping(policies[0], label="source policy").get("name") != "event_exposure_v1"
        ):
            continue
        distance = _configured_distance(config)
        if distance not in {2.0, 4.0, 8.0}:
            continue
        params = _exact_mapping(feature.get("params"), label="Zone params")
        execution = _exact_mapping(config.get("execution"), label="source execution")
        config_digest = _sha256_bytes(_canonical_json(params).encode("ascii"))
        files = dict(manifest.artifact_hashes)
        feature_name = "features.npz"
        if feature_name not in files:
            raise _ContractViolationError("canonical Zone source is missing features.npz")
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
        if not intent_files or len(intent_files) != len(trade_files):
            raise _ContractViolationError("canonical Zone source has incomplete fold artifacts")
        fingerprint = manifest.experiment_fingerprint
        run_id = manifest.run_id
        if type(fingerprint) is not str or _SHA256.fullmatch(fingerprint) is None:
            raise _ContractViolationError("source fingerprint is malformed")
        directory = runs_root / "runs" / run_id
        if type(run_id) is not str or run_id != directory.name:
            raise _ContractViolationError("source run id is malformed")
        candidates[(config_digest, distance, fingerprint)].append(
            _SourceRun(
                run_id,
                fingerprint,
                distance,
                config_digest,
                dict(params),
                dict(execution),
                directory,
                feature_name,
                files[feature_name],
                intent_files,
                trade_files,
            )
        )
    semantic: dict[tuple[str, float], _SourceRun] = {}
    for (config_digest, distance, _fingerprint), attempts in sorted(candidates.items()):
        signatures = {_source_artifact_signature(item) for item in attempts}
        if len(signatures) != 1:
            raise _ContractViolationError("canonical attempts disagree on consumed artifacts")
        chosen = min(attempts, key=lambda item: item.run_id)
        key = (config_digest, distance)
        if key in semantic:
            raise _ContractViolationError("multiple canonical fingerprints claim one Zone action")
        semantic[key] = chosen
    availability: dict[str, set[float]] = _defaultdict(set)
    for config_digest, distance in semantic:
        availability[config_digest].add(distance)
    complete_digests = sorted(
        digest for digest, arms in availability.items() if arms == {2.0, 4.0, 8.0}
    )
    if len(complete_digests) != _EXPECTED_CONFIGS:
        raise _ContractViolationError(
            f"source discovery found {len(complete_digests)} complete configurations"
        )
    return {
        digest: {distance: semantic[(digest, distance)] for distance in (2.0, 4.0, 8.0)}
        for digest in complete_digests
    }


def _configured_distance(config: dict[str, object]) -> float | None:
    execution = _exact_mapping(config.get("execution"), label="source execution")
    binding = _exact_mapping(execution.get("cost_binding"), label="source cost binding")
    price_units = _float_value(binding.get("price_units_per_pip"))
    take_profit = _float_value(execution.get("take_profit_pips"))
    stop_loss = _float_value(execution.get("stop_loss_pips"))
    if take_profit is None or stop_loss is None or price_units is None:
        return None
    if stop_loss * price_units != 4.0:
        return None
    return take_profit * price_units


def _discover_v2_context_sources(
    runs_root: _Path,
    *,
    expected_digests: frozenset[str],
    expected_platform: _PlatformIdentity,
    dependency_sha256: str,
) -> dict[str, _SourceRun]:
    """Select one canonical two-provider TRAIN carrier per Zone configuration."""
    candidates: dict[tuple[str, str], list[_SourceRun]] = _defaultdict(list)
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    if scan._errors:
        raise _ContractViolationError("authoritative V2 carrier scan reported corruption")
    for projection in scan._projections:
        manifest = projection._manifest
        config = projection._resolved_config
        record = projection._record
        if (
            record._state is not _RunState.COMPLETE
            or record._canonical is not True
            or manifest is None
            or config.get("schema") != "research-experiment-config-v1"
            or manifest.platform_identity != expected_platform
            or manifest.dependency_lock_sha256 != dependency_sha256
        ):
            continue
        dataset = _exact_mapping(config.get("dataset"), label="V2 carrier dataset")
        if (
            dataset.get("role") != "train"
            or dataset.get("dataset_id") != "XAUUSD_S_M1_TRAIN_V1"
        ):
            continue
        if (
            dataset.get("manifest_sha256")
            != "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e"
        ):
            raise _ContractViolationError("V2 carrier TRAIN identity differs")
        raw_features = config.get("features")
        policies = config.get("policies")
        if type(raw_features) is not list or len(raw_features) != 2 or type(policies) is not list:
            continue
        features = {
            _exact_mapping(item, label="V2 carrier feature").get("name"): _exact_mapping(
                item, label="V2 carrier feature"
            )
            for item in raw_features
        }
        if set(features) != {"zone_trader_v13", "zone_context_v1"}:
            continue
        zone = features["zone_trader_v13"]
        context = features["zone_context_v1"]
        if zone.get("version") != 1 or context.get("version") != 1:
            continue
        zone_params = _exact_mapping(zone.get("params"), label="V2 Zone params")
        context_params = _exact_mapping(context.get("params"), label="V2 context params")
        if _canonical_json(zone_params) != _canonical_json(context_params):
            raise _ContractViolationError("V2 carrier providers have different Zone parameters")
        if (
            len(policies) != 1
            or _exact_mapping(policies[0], label="V2 carrier policy").get("name")
            != "event_exposure_v1"
            or _configured_distance(config) != 2.0
        ):
            continue
        config_digest = _sha256_bytes(_canonical_json(zone_params).encode("ascii"))
        files = dict(manifest.artifact_hashes)
        feature_name = "features.npz"
        if feature_name not in files:
            raise _ContractViolationError("canonical V2 carrier lacks features.npz")
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
        if not intent_files or len(intent_files) != len(trade_files):
            raise _ContractViolationError("canonical V2 carrier has incomplete fold artifacts")
        fingerprint = manifest.experiment_fingerprint
        run_id = manifest.run_id
        directory = runs_root / "runs" / run_id
        if (
            type(fingerprint) is not str
            or _SHA256.fullmatch(fingerprint) is None
            or type(run_id) is not str
            or run_id != directory.name
        ):
            raise _ContractViolationError("V2 carrier identity is malformed")
        candidates[(config_digest, fingerprint)].append(
            _SourceRun(
                run_id,
                fingerprint,
                2.0,
                config_digest,
                dict(zone_params),
                dict(_exact_mapping(config.get("execution"), label="V2 carrier execution")),
                directory,
                feature_name,
                files[feature_name],
                intent_files,
                trade_files,
            )
        )
    selected: dict[str, _SourceRun] = {}
    for (config_digest, _fingerprint), attempts in sorted(candidates.items()):
        signatures = {_source_artifact_signature(item) for item in attempts}
        if len(signatures) != 1:
            raise _ContractViolationError("canonical V2 carrier attempts disagree")
        if config_digest in selected:
            raise _ContractViolationError(
                "multiple canonical V2 carrier fingerprints claim one config"
            )
        selected[config_digest] = min(attempts, key=lambda item: item.run_id)
    if frozenset(selected) != expected_digests:
        raise _ContractViolationError(
            "V2 context carrier inventory differs from the 100 primary configurations"
        )
    return selected


def _read_feature_artifact(source: _SourceRun) -> dict[str, object]:
    path = source.run_dir / "artifacts" / source.feature_name
    _verify_file_hash(path, source.feature_sha256)
    metadata, archive = _open_npz(path)
    try:
        if (
            metadata.get("format") != "trading-research-run-frame"
            or metadata.get("kind") != "features"
        ):
            raise _ContractViolationError("source feature artifact has the wrong format")
        documents = metadata.get("columns")
        if type(documents) is not list:
            raise _ContractViolationError("source feature column metadata is malformed")
        member_by_name: dict[str, str] = {}
        origin_by_name: dict[str, object] = {}
        for item in documents:
            document = _exact_mapping(item, label="source feature column")
            name = document.get("name")
            member = document.get("member")
            if type(name) is not str or type(member) is not str:
                raise _ContractViolationError("source feature column mapping is malformed")
            member_by_name[name] = member
            origin_by_name[name] = document.get("origin")
        required = (
            "zt_event_signed",
            "zt_entry_price",
            "zt_stop_price",
            "zt_target_price",
            "zt_zone_top",
            "zt_zone_bottom",
            "zt_zone_kind",
        )
        if any(name not in member_by_name for name in required):
            raise _ContractViolationError("source feature artifact lacks Zone columns")
        first_origin = origin_by_name[required[0]]
        if any(origin_by_name[name] != first_origin for name in required[1:]):
            raise _ContractViolationError("source Zone columns do not share one origin")
        origin_bytes = _canonical_json(first_origin).encode("ascii")
        keys = _np.array(archive["keys"], dtype=_np.int64, copy=True)
        columns = {
            name: _np.array(archive[member_by_name[name]], dtype=_np.float64, copy=True)
            for name in required
        }
    finally:
        archive.close()
    if keys.ndim != 1 or any(values.shape != keys.shape for values in columns.values()):
        raise _ContractViolationError("source feature arrays are not aligned")
    return {
        "artifact_sha256": source.feature_sha256,
        "origin": first_origin,
        "origin_digest": _hashlib.sha256(origin_bytes).digest(),
        "keys": keys,
        "columns": columns,
    }


def _validate_same_feature(candidate: dict[str, object], feature: dict[str, object]) -> None:
    candidate_digest = candidate.get("origin_digest")
    feature_digest = feature.get("origin_digest")
    if type(candidate_digest) is not bytes or type(feature_digest) is not bytes:
        raise _ContractViolationError("source feature origin digest is malformed")
    if candidate_digest != feature_digest:
        raise _ContractViolationError("matched arms do not share one exact FeatureOrigin")
    candidate_keys = _typing.cast("_npt.NDArray[_np.int64]", candidate.get("keys"))
    feature_keys = _typing.cast("_npt.NDArray[_np.int64]", feature.get("keys"))
    candidate_columns = _typing.cast(
        "dict[str, _npt.NDArray[_np.float64]]", candidate.get("columns")
    )
    feature_columns = _typing.cast(
        "dict[str, _npt.NDArray[_np.float64]]", feature.get("columns")
    )
    if not _np.array_equal(candidate_keys, feature_keys) or set(candidate_columns) != set(
        feature_columns
    ):
        raise _ContractViolationError("matched arms do not share one exact feature axis")
    if any(
        not _np.array_equal(candidate_columns[name], feature_columns[name], equal_nan=True)
        for name in feature_columns
    ):
        raise _ContractViolationError("matched arms do not share exact Zone feature values")


def _read_v2_feature_artifact(source: _SourceRun) -> dict[str, object]:
    """Read and verify the exact Zone plus context columns from one carrier."""
    path = source.run_dir / "artifacts" / source.feature_name
    _verify_file_hash(path, source.feature_sha256)
    metadata, archive = _open_npz(path)
    base_names = (
        "zt_event_signed",
        "zt_entry_price",
        "zt_stop_price",
        "zt_target_price",
        "zt_zone_top",
        "zt_zone_bottom",
        "zt_zone_kind",
    )
    required = (*base_names, *_CONTEXT_FEATURE_NAMES)
    try:
        if (
            metadata.get("format") != "trading-research-run-frame"
            or metadata.get("kind") != "features"
        ):
            raise _ContractViolationError("V2 carrier feature artifact has the wrong format")
        documents = metadata.get("columns")
        if type(documents) is not list:
            raise _ContractViolationError("V2 carrier feature metadata is malformed")
        member_by_name: dict[str, str] = {}
        origin_by_name: dict[str, object] = {}
        for item in documents:
            document = _exact_mapping(item, label="V2 carrier feature column")
            name = document.get("name")
            member = document.get("member")
            if type(name) is not str or type(member) is not str:
                raise _ContractViolationError("V2 carrier feature mapping is malformed")
            member_by_name[name] = member
            origin_by_name[name] = document.get("origin")
        if any(name not in member_by_name for name in required):
            raise _ContractViolationError("V2 carrier feature artifact lacks required columns")
        zone_origin = origin_by_name[base_names[0]]
        context_origin = origin_by_name[_CONTEXT_FEATURE_NAMES[0]]
        if any(origin_by_name[name] != zone_origin for name in base_names[1:]):
            raise _ContractViolationError("V2 carrier Zone columns do not share one origin")
        if any(origin_by_name[name] != context_origin for name in _CONTEXT_FEATURE_NAMES[1:]):
            raise _ContractViolationError("V2 carrier context columns do not share one origin")
        keys = _np.array(archive["keys"], dtype=_np.int64, copy=True)
        columns = {
            name: _np.array(archive[member_by_name[name]], dtype=_np.float64, copy=True)
            for name in required
        }
    finally:
        archive.close()
    if keys.ndim != 1 or any(values.shape != keys.shape for values in columns.values()):
        raise _ContractViolationError("V2 carrier feature arrays are not aligned")
    zone_origin_bytes = _canonical_json(zone_origin).encode("ascii")
    context_origin_bytes = _canonical_json(context_origin).encode("ascii")
    return {
        "artifact_sha256": source.feature_sha256,
        "origin": zone_origin,
        "origin_digest": _hashlib.sha256(zone_origin_bytes).digest(),
        "context_origin": context_origin,
        "context_origin_digest": _hashlib.sha256(context_origin_bytes).digest(),
        "keys": keys,
        "columns": columns,
    }


def _validate_v2_carrier(carrier: dict[str, object], base: dict[str, object]) -> None:
    """Prove the added provider did not change the V13 event or geometry truth."""
    carrier_keys = _typing.cast("_npt.NDArray[_np.int64]", carrier.get("keys"))
    base_keys = _typing.cast("_npt.NDArray[_np.int64]", base.get("keys"))
    carrier_columns = _typing.cast(
        "dict[str, _npt.NDArray[_np.float64]]", carrier.get("columns")
    )
    base_columns = _typing.cast("dict[str, _npt.NDArray[_np.float64]]", base.get("columns"))
    if not _np.array_equal(carrier_keys, base_keys):
        raise _ContractViolationError("V2 carrier and V1 source axes differ")
    if any(
        not _np.array_equal(carrier_columns[name], values, equal_nan=True)
        for name, values in base_columns.items()
    ):
        raise _ContractViolationError("V2 carrier changed a V13 event or geometry column")


def _read_run_observations(
    source: _SourceRun,
    *,
    feature_keys: _npt.NDArray[_np.int64],
) -> dict[tuple[int, int], _TradeObservation]:
    observed: dict[tuple[int, int], _TradeObservation] = {}
    for (intent_name, intent_sha), (trade_name, trade_sha) in zip(
        source.intent_files,
        source.trade_files,
        strict=True,
    ):
        intent_path = source.run_dir / "artifacts" / intent_name
        trade_path = source.run_dir / "artifacts" / trade_name
        _verify_file_hash(intent_path, intent_sha)
        _verify_file_hash(trade_path, trade_sha)
        intent_meta, intent_archive = _open_npz(intent_path)
        try:
            if intent_meta.get("format") != "trading-research-intent-frame":
                raise _ContractViolationError("source intent artifact has the wrong format")
            intent_keys = _np.array(intent_archive["keys"], dtype=_np.int64, copy=True)
            exposures = _np.array(intent_archive["target_exposure"], dtype=_np.float64, copy=True)
        finally:
            intent_archive.close()
        trade_meta, trade_archive = _open_npz(trade_path)
        try:
            if trade_meta.get("format") != "trading-research-trade-log":
                raise _ContractViolationError("source trade artifact has the wrong format")
            trade_columns = {
                name: _np.array(trade_archive[name], copy=True)
                for name in (
                    "entry_key",
                    "exit_key",
                    "direction",
                    "entry_price",
                    "exit_price",
                )
            }
        finally:
            trade_archive.close()
        rows = int(trade_columns["entry_key"].size)
        if intent_keys.shape != exposures.shape or any(
            value.shape != (rows,) for value in trade_columns.values()
        ):
            raise _ContractViolationError("source intent or trade columns are misaligned")
        intent_lookup = {
            int(key): float(value) for key, value in zip(intent_keys, exposures, strict=True)
        }
        for index in range(rows):
            entry_key = int(trade_columns["entry_key"][index])
            position = int(_np.searchsorted(feature_keys, entry_key))
            if (
                position <= 0
                or position >= feature_keys.size
                or int(feature_keys[position]) != entry_key
            ):
                raise _ContractViolationError("trade entry is not an observed feature-axis row")
            intent_key = int(feature_keys[position - 1])
            direction = int(trade_columns["direction"][index])
            if intent_lookup.get(intent_key) != float(direction):
                raise _ContractViolationError("trade does not map one-to-one to its opening intent")
            key = (intent_key, direction)
            if key in observed:
                raise _ContractViolationError("one run opened the same Zone sample more than once")
            observed[key] = _TradeObservation(
                intent_key,
                direction,
                entry_key,
                int(trade_columns["exit_key"][index]),
                float(trade_columns["entry_price"][index]),
                float(trade_columns["exit_price"][index]),
            )
    return observed


def _predictor_row(
    *,
    columns: dict[str, _npt.NDArray[_np.float64]],
    position: int,
    parameter_values: _npt.NDArray[_np.float64],
    expected_direction: int,
) -> _npt.NDArray[_np.float64]:
    event = float(columns["zt_event_signed"][position])
    entry = float(columns["zt_entry_price"][position])
    stop = float(columns["zt_stop_price"][position])
    target = float(columns["zt_target_price"][position])
    top = float(columns["zt_zone_top"][position])
    bottom = float(columns["zt_zone_bottom"][position])
    kind = float(columns["zt_zone_kind"][position])
    if event != float(expected_direction) or entry == 0.0 or kind not in {0.0, 1.0, 2.0}:
        raise _ContractViolationError("matched trade does not map back to its Zone feature row")
    market = _np.asarray(
        (
            event,
            abs(entry - stop) / entry,
            abs(target - entry) / entry,
            (top - bottom) / entry,
            event * (entry - ((top + bottom) / 2.0)) / entry,
            float(kind == 0.0),
            float(kind == 1.0),
            float(kind == 2.0),
        ),
        dtype=_np.float64,
    )
    if market.shape != (len(_MARKET_INPUT_NAMES),) or parameter_values.shape != (
        len(_PARAM_INPUT_NAMES),
    ):
        raise AssertionError("Zone predictor schema length drifted")
    result = _np.concatenate((market, parameter_values)).astype(_np.float64, copy=False)
    if not _np.isfinite(result).all():
        raise _ContractViolationError("matched Zone sample has non-finite predictors")
    return result


def _v2_predictor_row(
    *,
    columns: dict[str, _npt.NDArray[_np.float64]],
    position: int,
    parameter_values: _npt.NDArray[_np.float64],
    expected_direction: int,
) -> _npt.NDArray[_np.float64]:
    """Build one exact finite V2 corpus row from a verified carrier artifact."""
    event = float(columns["zt_event_signed"][position])
    entry = float(columns["zt_entry_price"][position])
    stop = float(columns["zt_stop_price"][position])
    target = float(columns["zt_target_price"][position])
    top = float(columns["zt_zone_top"][position])
    bottom = float(columns["zt_zone_bottom"][position])
    kind = float(columns["zt_zone_kind"][position])
    if event != float(expected_direction) or entry == 0.0 or kind not in {0.0, 1.0, 2.0}:
        raise _ContractViolationError("V2 matched trade does not map to its Zone event")
    context = _np.asarray(
        [float(columns[name][position]) for name in _CONTEXT_FEATURE_NAMES],
        dtype=_np.float64,
    )
    market = _np.asarray(
        (
            event,
            abs(entry - stop) / entry,
            abs(target - entry) / entry,
            (top - bottom) / entry,
            event * (entry - ((top + bottom) / 2.0)) / entry,
            float(kind == 0.0),
            float(kind == 1.0),
            float(kind == 2.0),
            *context.tolist(),
        ),
        dtype=_np.float64,
    )
    result = _np.concatenate((market, parameter_values)).astype(_np.float64, copy=False)
    if result.shape != (len(_V2_INPUT_NAMES),) or not _np.isfinite(result).all():
        raise _ContractViolationError("V2 matched Zone sample has invalid predictors")
    return result


def _qualification_report(
    corpus: _CorpusProduct,
    *,
    plan: _CorpusModelPlan,
) -> dict[str, object]:
    unique_keys = _np.unique(corpus.decision_keys)
    split_position = _math.floor(unique_keys.size * 0.8)
    if split_position <= 0 or split_position >= unique_keys.size:
        raise _ContractViolationError("qualification split cannot create two non-empty spans")
    boundary = int(unique_keys[split_position])
    fit_mask = (corpus.decision_keys < boundary) & (corpus.max_exit_keys < boundary)
    holdout_mask = corpus.decision_keys >= boundary
    purged = int(_np.count_nonzero((corpus.decision_keys < boundary) & ~fit_mask))
    fit_inputs = _make_corpus_model_inputs(
        inputs={name: values[fit_mask] for name, values in corpus.inputs.items()},
        targets={name: values[fit_mask] for name, values in corpus.targets.items()},
        weights=corpus.weights[fit_mask],
    )
    payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    matrix = _np.column_stack([corpus.inputs[name][holdout_mask] for name in _INPUT_NAMES])
    truth = _np.column_stack([corpus.targets[name][holdout_mask] for name in _TARGET_NAMES])
    prediction = _predict_payload(payload, matrix)
    selected = _np.argmax(prediction, axis=1)
    chosen = truth[_np.arange(truth.shape[0]), selected]
    oracle = truth.max(axis=1)
    errors = prediction - truth
    return {
        "boundary_decision_key": boundary,
        "fit_rows": int(_np.count_nonzero(fit_mask)),
        "holdout_rows": int(_np.count_nonzero(holdout_mask)),
        "purged_rows": purged,
        "per_arm": {
            name: {
                "mae": float(_np.mean(_np.abs(errors[:, index]))),
                "rmse": float(_np.sqrt(_np.mean(errors[:, index] ** 2))),
                "mean_realized_r": float(_np.mean(truth[:, index])),
            }
            for index, name in enumerate(_TARGET_NAMES)
        },
        "selector": {
            "choice_counts": {
                name: int(_np.count_nonzero(selected == index))
                for index, name in enumerate(_TARGET_NAMES)
            },
            "mean_realized_r": float(_np.mean(chosen)),
            "mean_oracle_regret_r": float(_np.mean(oracle - chosen)),
            "oracle_mean_realized_r": float(_np.mean(oracle)),
        },
    }


def _predict_payload(
    payload: _FittedPayload,
    matrix: _npt.NDArray[_np.float64],
) -> _npt.NDArray[_np.float64]:
    standardized = (matrix - payload.array("input_means")) / payload.array("input_scales")
    design = _np.column_stack((_np.ones(matrix.shape[0], dtype=_np.float64), standardized))
    return _typing.cast(
        "_npt.NDArray[_np.float64]",
        design @ payload.array("coefficients"),
    )


def _qualification_report_v2(
    corpus: _CorpusProduct,
    *,
    plan: _CorpusModelPlan,
) -> dict[str, object]:
    """Run the frozen chronological TRAIN qualification for the V2 forest."""
    unique_keys = _np.unique(corpus.decision_keys)
    split_position = _math.floor(unique_keys.size * 0.8)
    if split_position <= 0 or split_position >= unique_keys.size:
        raise _ContractViolationError("V2 qualification cannot create two non-empty spans")
    boundary = int(unique_keys[split_position])
    fit_mask = (corpus.decision_keys < boundary) & (corpus.max_exit_keys < boundary)
    holdout_mask = corpus.decision_keys >= boundary
    purged = int(_np.count_nonzero((corpus.decision_keys < boundary) & ~fit_mask))
    fit_inputs = _make_corpus_model_inputs(
        inputs={name: values[fit_mask] for name, values in corpus.inputs.items()},
        targets={name: values[fit_mask] for name, values in corpus.targets.items()},
        weights=corpus.weights[fit_mask],
    )
    payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    matrix = _np.column_stack([corpus.inputs[name][holdout_mask] for name in _V2_INPUT_NAMES])
    truth = _np.column_stack([corpus.targets[name][holdout_mask] for name in _TARGET_NAMES])
    prediction = _forest_predict(payload, matrix)
    selected = _np.argmax(prediction, axis=1)
    chosen = truth[_np.arange(truth.shape[0]), selected]
    oracle = truth.max(axis=1)
    errors = prediction - truth
    return {
        "boundary_decision_key": boundary,
        "fit_rows": int(_np.count_nonzero(fit_mask)),
        "holdout_rows": int(_np.count_nonzero(holdout_mask)),
        "purged_rows": purged,
        "per_arm": {
            name: {
                "mae": float(_np.mean(_np.abs(errors[:, index]))),
                "rmse": float(_np.sqrt(_np.mean(errors[:, index] ** 2))),
                "mean_realized_r": float(_np.mean(truth[:, index])),
            }
            for index, name in enumerate(_TARGET_NAMES)
        },
        "selector": {
            "choice_counts": {
                name: int(_np.count_nonzero(selected == index))
                for index, name in enumerate(_TARGET_NAMES)
            },
            "mean_realized_r": float(_np.mean(chosen)),
            "mean_oracle_regret_r": float(_np.mean(oracle - chosen)),
            "oracle_mean_realized_r": float(_np.mean(oracle)),
        },
    }


def _encode_corpus(corpus: _CorpusProduct) -> bytes:
    return _encode_corpus_for_inputs(
        corpus,
        input_names=_INPUT_NAMES,
        corpus_format=_CORPUS_FORMAT,
    )


def _encode_corpus_for_inputs(
    corpus: _CorpusProduct,
    *,
    input_names: tuple[str, ...],
    corpus_format: str,
) -> bytes:
    metadata = {
        "format": corpus_format,
        "format_version": 1,
        "corpus_identity_key": corpus.corpus_identity_key,
        "input_names": list(input_names),
        "target_names": list(_TARGET_NAMES),
        "counts": corpus.counts,
    }
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        "__meta__": _np.frombuffer(_canonical_json(metadata).encode("ascii"), dtype=_np.uint8),
        "sample_ids": corpus.sample_ids,
        "feature_origin_ids": corpus.feature_origin_ids,
        "decision_keys": corpus.decision_keys,
        "directions": corpus.directions,
        "max_exit_keys": corpus.max_exit_keys,
        "weights": corpus.weights,
    }
    arrays.update(
        {f"input_{index:04d}": corpus.inputs[name] for index, name in enumerate(input_names)}
    )
    arrays.update(
        {f"target_{index:04d}": corpus.targets[name] for index, name in enumerate(_TARGET_NAMES)}
    )
    output = _io.BytesIO()
    _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **arrays)
    payload = output.getvalue()
    _validate_zip_envelope(payload, tuple(arrays))
    return payload


def _load_frozen_state(
    *,
    runs_root: _Path,
    reference: _FrozenReference,
    expected_model_id: str,
    expected_model_version: int,
    expected_model_params: _Mapping[str, object],
) -> _FittedState:
    """Resolve and verify one immutable COMPLETE bundle and its exact model state."""
    if type(reference) is not _FrozenReference or _BUNDLE_ID.fullmatch(reference.bundle_id) is None:
        raise _ContractViolationError("frozen-state bundle reference is malformed")
    bundle = runs_root / "training-bundles" / reference.bundle_id
    manifest_path = bundle / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise _ContractViolationError(
            "frozen bundle is unavailable; cache is not authority"
        ) from exc
    if _sha256_bytes(manifest_bytes) != reference.manifest_sha256:
        raise _ContractViolationError("frozen bundle manifest digest differs from config")
    manifest = _read_bundle_manifest(bundle)
    _verify_bundle_files(bundle, manifest)
    if manifest.get("fitted_state_identity_key") != reference.fitted_state_identity_key:
        raise _ContractViolationError("frozen fitted-state identity differs from config")
    fitted_path = bundle / "fitted-state.npz"
    if _sha256_file(fitted_path) != reference.fitted_state_artifact_sha256:
        raise _ContractViolationError("frozen fitted-state artifact digest differs from config")
    metadata, archive = _open_npz(fitted_path)
    archive.close()
    identity = _exact_mapping(metadata.get("fitted_identity"), label="fitted identity")
    plugin = _exact_mapping(identity.get("plugin"), label="fitted plugin")
    expected_params = _params_document(
        expected_model_params,
        label="expected_model_params",
        require_nonempty=False,
    )
    if (
        plugin.get("plugin_id") != expected_model_id
        or plugin.get("version") != expected_model_version
        or _canonical_json(plugin.get("params")) != _canonical_json(expected_params)
    ):
        raise _ContractViolationError("frozen fitted state belongs to a different model")
    return _decode_fitted_state(
        fitted_path.read_bytes(),
        expected_identity_key=reference.fitted_state_identity_key,
    )


def _read_bundle_manifest(bundle: _Path) -> dict[str, object]:
    manifest = _read_json(bundle / "manifest.json")
    if (
        manifest.get("schema") != _BUNDLE_SCHEMA
        or manifest.get("state") != "COMPLETE"
        or manifest.get("bundle_id") != bundle.name
    ):
        raise _ContractViolationError("training bundle is not canonical COMPLETE")
    state = _read_json(bundle / "state.json")
    if state != {"schema": _STATE_SCHEMA, "bundle_id": bundle.name, "state": "COMPLETE"}:
        raise _ContractViolationError("training bundle state is malformed")
    return manifest


def _verify_bundle_files(bundle: _Path, manifest: dict[str, object]) -> None:
    files = _exact_mapping(manifest.get("files"), label="bundle files")
    expected = {"corpus.npz", "fitted-state.npz", "source-manifest.json", "training-report.json"}
    if set(files) != expected:
        raise _ContractViolationError("training bundle file inventory is not closed")
    lifecycle_sha = manifest.get("lifecycle_sha256")
    if type(lifecycle_sha) is not str or _SHA256.fullmatch(lifecycle_sha) is None:
        raise _ContractViolationError("training bundle lifecycle digest is malformed")
    actual = {path.name for path in bundle.iterdir() if path.is_file()}
    if actual != {*expected, "lifecycle.jsonl", "manifest.json", "state.json"}:
        raise _ContractViolationError("training bundle directory contains missing or extra files")
    lifecycle = (bundle / "lifecycle.jsonl").read_bytes()
    if lifecycle != _lifecycle_bytes(("PLANNED", "RUNNING", "COMPLETE")):
        raise _ContractViolationError("training bundle lifecycle sequence differs")
    if _sha256_bytes(lifecycle) != lifecycle_sha:
        raise _ContractViolationError("training bundle lifecycle digest differs")
    for name in sorted(expected):
        digest = files[name]
        if type(digest) is not str:
            raise _ContractViolationError("training bundle file digest is malformed")
        _verify_file_hash(bundle / name, digest)


def _bundle_reference_document(
    manifest: dict[str, object],
    *,
    manifest_sha256: str,
) -> dict[str, object]:
    files = _exact_mapping(manifest["files"], label="bundle files")
    return {
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": manifest_sha256,
        "fitted_state_identity_key": manifest["fitted_state_identity_key"],
        "fitted_state_artifact_sha256": files["fitted-state.npz"],
    }


def _source_entry(
    source: _SourceRun,
    *,
    feature_origin_digest: str,
) -> dict[str, object]:
    return {
        "run_id": source.run_id,
        "experiment_fingerprint": source.fingerprint,
        "feature_config_digest": source.config_digest,
        "feature_origin_digest": feature_origin_digest,
        "distance": source.distance,
        "artifacts": {
            "feature": {"name": source.feature_name, "sha256": source.feature_sha256},
            "intents": [{"name": name, "sha256": sha} for name, sha in source.intent_files],
            "trade_logs": [{"name": name, "sha256": sha} for name, sha in source.trade_files],
        },
    }


def _source_artifact_signature(source: _SourceRun) -> tuple[object, ...]:
    return (source.feature_sha256, source.intent_files, source.trade_files)


def _assert_equal_corpus(left: _CorpusProduct, right: _CorpusProduct) -> None:
    _assert_equal_corpus_for_inputs(left, right, input_names=_INPUT_NAMES)


def _assert_equal_corpus_for_inputs(
    left: _CorpusProduct,
    right: _CorpusProduct,
    *,
    input_names: tuple[str, ...],
) -> None:
    if (
        left.source_manifest != right.source_manifest
        or left.corpus_identity_key != right.corpus_identity_key
        or left.counts != right.counts
    ):
        raise _ContractViolationError("corpus double build metadata differs")
    pairs: list[tuple[_npt.NDArray[_typing.Any], _npt.NDArray[_typing.Any]]] = [
        (left.sample_ids, right.sample_ids),
        (left.feature_origin_ids, right.feature_origin_ids),
        (left.decision_keys, right.decision_keys),
        (left.directions, right.directions),
        (left.max_exit_keys, right.max_exit_keys),
        (left.weights, right.weights),
    ]
    pairs.extend((left.inputs[name], right.inputs[name]) for name in input_names)
    pairs.extend((left.targets[name], right.targets[name]) for name in _TARGET_NAMES)
    if any(not _np.array_equal(first, second) for first, second in pairs):
        raise _ContractViolationError("corpus double build arrays differ")


def _assert_equal_payload(left: _FittedPayload, right: _FittedPayload) -> None:
    if left.metadata != right.metadata or left.array_names != right.array_names:
        raise _ContractViolationError("fitted payload double fit metadata differs")
    if any(not _np.array_equal(left.array(name), right.array(name)) for name in left.array_names):
        raise _ContractViolationError("fitted payload double fit arrays differ")


def _manifest_artifact_files(manifest: dict[str, object]) -> dict[str, str]:
    files = _exact_mapping(manifest.get("files"), label="run files")
    artifacts = _exact_mapping(files.get("artifacts"), label="run artifact files")
    result: dict[str, str] = {}
    for name, digest in artifacts.items():
        if type(name) is not str or type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise _ContractViolationError("run artifact inventory is malformed")
        result[name] = digest
    return result


def _open_npz(path: _Path) -> tuple[dict[str, object], _typing.Any]:
    try:
        archive = _np.load(path, allow_pickle=False)
        raw = archive["__meta__"]
        encoded = raw.tobytes(order="C").decode("ascii")
        metadata = _json.loads(encoded)
    except Exception as exc:
        raise _ContractViolationError(f"cannot decode verified artifact {path}") from exc
    if type(metadata) is not dict or _canonical_json(metadata) != encoded:
        archive.close()
        raise _ContractViolationError("verified artifact metadata is not canonical")
    return _typing.cast("dict[str, object]", metadata), archive


def _decoded_params(params: dict[str, object]) -> dict[str, _ParamValue]:
    def decode(value: object) -> object:
        if type(value) is dict and set(value) == {"__float_hex__"}:
            encoded = value["__float_hex__"]
            if type(encoded) is not str:
                raise _ContractViolationError("encoded float is malformed")
            return float.fromhex(encoded)
        if type(value) is list:
            return tuple(decode(item) for item in value)
        return value

    return _typing.cast(
        "dict[str, _ParamValue]",
        {name: decode(value) for name, value in params.items()},
    )


def _float_value(value: object) -> float | None:
    if value is None:
        return None
    if type(value) is dict and set(value) == {"__float_hex__"}:
        encoded = value["__float_hex__"]
        if type(encoded) is str:
            return float.fromhex(encoded)
    raise _ContractViolationError("resolved numeric config value is malformed")


def _exact_mapping(value: object, *, label: str) -> dict[str, object]:
    if type(value) is not dict or any(type(name) is not str for name in value):
        raise _ContractViolationError(f"{label} must be an exact string-keyed object")
    return _typing.cast("dict[str, object]", value)


def _read_json(path: _Path) -> dict[str, object]:
    try:
        payload = path.read_bytes()
        parsed = _json.loads(payload)
    except (OSError, UnicodeDecodeError, _json.JSONDecodeError) as exc:
        raise _ContractViolationError(f"cannot read strict JSON {path}") from exc
    return _exact_mapping(parsed, label=str(path))


def _json_bytes(document: object) -> bytes:
    return _canonical_identity_json(document).encode("ascii") + b"\n"


def _canonical_document_json(document: object) -> str:
    return _canonical_json(document)


def _sha256_bytes(payload: bytes) -> str:
    return _hashlib.sha256(payload).hexdigest()


def _sha256_file(path: _Path) -> str:
    digest = _hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise _ContractViolationError(f"cannot hash {path}") from exc
    return digest.hexdigest()


def _verify_file_hash(path: _Path, expected: str) -> None:
    if _SHA256.fullmatch(expected) is None or _sha256_file(path) != expected:
        raise _ContractViolationError(f"artifact content hash mismatch: {path}")


def _clean_commit(repository_root: _Path) -> str:
    try:
        status = _subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
        ).stdout
        commit = _subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, _subprocess.CalledProcessError) as exc:
        raise _ContractViolationError("cannot establish clean bundle platform identity") from exc
    if status:
        raise _ContractViolationError("canonical training bundle requires a clean Git worktree")
    if _GIT_COMMIT.fullmatch(commit) is None:
        raise _ContractViolationError("Git commit identity is malformed")
    return commit


def _write_new(path: _Path, payload: bytes) -> None:
    descriptor = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o444)
    try:
        with _os.fdopen(descriptor, "wb", closefd=False) as target:
            target.write(payload)
            target.flush()
            _os.fsync(target.fileno())
    finally:
        _os.close(descriptor)


def _main(argv: list[str] | None = None) -> int:
    parser = _argparse.ArgumentParser(prog="zone-selector-bundle")
    parser.add_argument("--study-version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--runs-root", required=True, type=lambda value: _Path(value).resolve())
    parser.add_argument(
        "--repository-root", required=True, type=lambda value: _Path(value).resolve()
    )
    parser.add_argument(
        "--dependency-lock", required=True, type=lambda value: _Path(value).resolve()
    )
    args = parser.parse_args(argv)
    try:
        builder = (
            _build_zone_selector_bundle
            if args.study_version == "v1"
            else _build_zone_selector_v2_bundle
        )
        reference = builder(
            runs_root=args.runs_root,
            repository_root=args.repository_root,
            dependency_lock_path=args.dependency_lock,
        )
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2
    _sys.stdout.write(_canonical_identity_json(reference) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
