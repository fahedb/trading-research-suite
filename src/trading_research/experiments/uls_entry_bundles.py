"""Immutable TRAIN-only bundle for the ULS + PUSH entry-gating pilot."""

from __future__ import annotations

import argparse as _argparse
import hashlib as _hashlib
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
    _open_npz,
    _publish_new_bundle,
    _read_bundle_manifest,
    _sha256_bytes,
    _sha256_file,
    _verify_bundle_files,
    _verify_file_hash,
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
    _TARGET_NAMES,
    _forest_predict,
    _predictor_rows_from_arrays,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
    from trading_research.core.research_frames import FittedPayload as _FittedPayload

__all__: list[str] = []

_SOURCE_SWEEP_ID = "uls-push-v7-train-owner-200x4-brackets-v1"
_SOURCE_SWEEP_SHA256 = "79adacb821f8729d07a75cd5101ef6339a06a712ed1428910fabd30af1bdf615"
_SOURCE_ORDINAL_START = 200
_SOURCE_ORDINAL_STOP = 400
_DEPLOYMENT_ORDINAL = 344
_TRAIN_DATASET_ID = "XAUUSD_S_M1_TRAIN_V1"
_TRAIN_DATASET_SHA256 = "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e"
_EXPECTED_CONFIGURATIONS = 200
_EXPECTED_RAW_EVENTS = 961_609
_EXPECTED_RAW_TRADES = 405_150
_EXPECTED_BOUNDARY_EXCLUDED = 344
_EXPECTED_CORPUS_ROWS = 404_806
_EXPECTED_UNIQUE_EPISODES = 61_576
_BUNDLE_SCHEMA = "research-training-bundle-manifest-v1"
_SOURCE_SCHEMA = "uls-entry-source-manifest-v1"
_CORPUS_FORMAT = "uls-entry-corpus-v1"
_REPORT_SCHEMA = "uls-entry-training-report-v1"
_TARGET_SPEC_ID = "recorded_trade_net_pnl_v1"
_MODEL_BASE_PARAMS: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "max_depth": 8,
    "max_features": 0.75,
    "min_samples_leaf": 96,
    "n_estimators": 64,
    "random_seed": 20_260_826,
}
_TARGET_SPEC_PARAMS: _Mapping[str, _ParamValue] = {
    "boundary_forced_close_excluded": True,
    "cost_semantics": "unverified",
    "stop_loss_pips": 800.0,
    "take_profit_pips": 800.0,
    "target_column": "net_pnl",
}
_TRAINING_SPEC: _Mapping[str, _ParamValue] = {
    "calibration_fraction": 0.15,
    "fit_fraction": 0.70,
    "grouping": "intent_key_direction_v1",
    "purge": "exit_reaches_next_boundary_v1",
    "sample_identity": "feature_origin_intent_key_direction_v1",
    "test_fraction": 0.15,
    "threshold_candidates": "deployment_config_prediction_quantiles_0_to_95_step_5_plus_zero_v1",
    "threshold_minimum_calibration_rows": 25,
    "threshold_objective": "deployment_config_total_recorded_net_pnl_v1",
    "weighting": "inverse_episode_frequency_v1",
}
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")


@_dataclass(frozen=True, slots=True)
class _SourceRun:
    ordinal: int
    run_id: str
    fingerprint: str
    run_dir: _Path
    config_params: dict[str, object]
    execution: dict[str, object]
    feature_sha256: str
    intent_files: tuple[tuple[str, str], ...]
    trade_files: tuple[tuple[str, str], ...]


@_dataclass(frozen=True, slots=True)
class _Observation:
    intent_key: int
    exit_key: int
    direction: int
    net_pnl: float
    exit_reason: int


@_dataclass(frozen=True, slots=True)
class _CorpusProduct:
    source_manifest: dict[str, object]
    corpus_identity_key: str
    sample_ids: _npt.NDArray[_np.uint8]
    feature_origin_ids: _npt.NDArray[_np.uint8]
    decision_keys: _npt.NDArray[_np.int64]
    directions: _npt.NDArray[_np.int8]
    max_exit_keys: _npt.NDArray[_np.int64]
    configuration_ordinals: _npt.NDArray[_np.int16]
    deployment_mask: _npt.NDArray[_np.bool_]
    weights: _npt.NDArray[_np.float64]
    inputs: dict[str, _npt.NDArray[_np.float64]]
    targets: dict[str, _npt.NDArray[_np.float64]]
    counts: dict[str, object]


def _build_uls_entry_bundle(
    *,
    runs_root: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
) -> dict[str, object]:
    """Build twice, qualify, fit and atomically publish the frozen pilot."""
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
    first = _construct_corpus(
        runs_root=runs_root,
        platform=platform,
        dependency_sha256=dependency_sha,
    )
    second = _construct_corpus(
        runs_root=runs_root,
        platform=platform,
        dependency_sha256=dependency_sha,
    )
    _assert_equal_corpus(first, second)
    source_bytes = _json_bytes(first.source_manifest)
    corpus_bytes = _encode_corpus(first)
    source_sha = _sha256_bytes(source_bytes)
    corpus_sha = _sha256_bytes(corpus_bytes)
    bundle_id = f"uls-entry-selector-v1-{first.corpus_identity_key[:20]}"
    destination = runs_root / "training-bundles" / bundle_id
    is_new = not destination.exists()
    if is_new:
        _begin_bundle_lifecycle(bundle_root=destination.parent, bundle_id=bundle_id)
    try:
        return _fit_and_publish(
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


def _construct_corpus(
    *,
    runs_root: _Path,
    platform: _PlatformIdentity,
    dependency_sha256: str,
) -> _CorpusProduct:
    sources = _discover_sources(runs_root)
    carrier = _discover_context_carrier(
        runs_root,
        expected_params=sources[_DEPLOYMENT_ORDINAL].config_params,
        platform=platform,
        dependency_sha256=dependency_sha256,
    )
    context = _read_feature_artifact(
        carrier,
        required_names=_CONTEXT_FEATURE_NAMES,
        expected_provider="uls_market_context_v1",
    )
    context_keys = _typing.cast("_npt.NDArray[_np.int64]", context["keys"])
    context_columns = _typing.cast("dict[str, _npt.NDArray[_np.float64]]", context["columns"])

    predictor_chunks: list[_npt.NDArray[_np.float64]] = []
    key_chunks: list[_npt.NDArray[_np.int64]] = []
    exit_chunks: list[_npt.NDArray[_np.int64]] = []
    direction_chunks: list[_npt.NDArray[_np.int8]] = []
    target_chunks: list[_npt.NDArray[_np.float64]] = []
    ordinal_chunks: list[_npt.NDArray[_np.int16]] = []
    sample_chunks: list[_npt.NDArray[_np.uint8]] = []
    origin_chunks: list[_npt.NDArray[_np.uint8]] = []
    source_entries: list[dict[str, object]] = []
    configuration_entries: list[dict[str, object]] = []
    raw_events = 0
    raw_trades = 0
    excluded_boundary = 0
    exit_reasons = _Counter[int]()

    for ordinal in sorted(sources):
        source = sources[ordinal]
        feature = _read_feature_artifact(
            source,
            required_names=(_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES),
            expected_provider="uls_push_v7",
        )
        keys = _typing.cast("_npt.NDArray[_np.int64]", feature["keys"])
        columns = _typing.cast("dict[str, _npt.NDArray[_np.float64]]", feature["columns"])
        if not _np.array_equal(keys, context_keys):
            raise _ContractViolationError("ULS source and context carrier axes differ")
        raw_events += int(_np.count_nonzero(_np.isin(columns[_ULS_EVENT_FEATURE], (-1.0, 1.0))))
        observations = _read_observations(
            source,
            feature_keys=keys,
            events=columns[_ULS_EVENT_FEATURE],
        )
        raw_trades += len(observations)
        exit_reasons.update(item.exit_reason for item in observations)
        retained = [item for item in observations if item.exit_reason != 4]
        excluded_boundary += len(observations) - len(retained)
        if not retained:
            configuration_entries.append(_configuration_entry(source, feature))
            source_entries.append(_source_entry(source, feature))
            continue
        decision_keys = _np.asarray([item.intent_key for item in retained], dtype=_np.int64)
        positions = _np.searchsorted(keys, decision_keys).astype(_np.int64, copy=False)
        if _np.any(positions >= keys.size) or not _np.array_equal(keys[positions], decision_keys):
            raise _ContractViolationError("ULS trade intent key is absent from feature axis")
        directions = _np.asarray([item.direction for item in retained], dtype=_np.int8)
        combined_columns = {**columns, **context_columns}
        predictors = _predictor_rows_from_arrays(
            columns=combined_columns,
            positions=positions,
            params=_decoded_params(source.config_params),
            expected_directions=directions,
        )
        origin_digest = _typing.cast("bytes", feature["origin_digest"])
        sample_ids = _sample_ids(
            origin_digest=origin_digest,
            decision_keys=decision_keys,
            directions=directions,
        )
        predictor_chunks.append(predictors)
        key_chunks.append(decision_keys)
        exit_chunks.append(_np.asarray([item.exit_key for item in retained], dtype=_np.int64))
        direction_chunks.append(directions)
        target_chunks.append(_np.asarray([item.net_pnl for item in retained], dtype=_np.float64))
        ordinal_chunks.append(_np.full(len(retained), ordinal, dtype=_np.int16))
        sample_chunks.append(sample_ids)
        origin_chunks.append(
            _np.broadcast_to(
                _np.frombuffer(origin_digest, dtype=_np.uint8),
                (len(retained), 32),
            ).copy()
        )
        configuration_entries.append(_configuration_entry(source, feature))
        source_entries.append(_source_entry(source, feature))

    if (
        len(sources) != _EXPECTED_CONFIGURATIONS
        or raw_events != _EXPECTED_RAW_EVENTS
        or raw_trades != _EXPECTED_RAW_TRADES
        or excluded_boundary != _EXPECTED_BOUNDARY_EXCLUDED
    ):
        raise _ContractViolationError("ULS source inventory differs from frozen TRAIN truth")
    predictors = _np.concatenate(predictor_chunks, axis=0)
    decision_keys = _np.concatenate(key_chunks)
    max_exit_keys = _np.concatenate(exit_chunks)
    directions = _np.concatenate(direction_chunks)
    targets = _np.concatenate(target_chunks)
    ordinals = _np.concatenate(ordinal_chunks)
    sample_ids = _np.concatenate(sample_chunks, axis=0)
    origin_ids = _np.concatenate(origin_chunks, axis=0)
    if predictors.shape != (_EXPECTED_CORPUS_ROWS, len(_INPUT_NAMES)):
        raise _ContractViolationError("ULS corpus row count or predictor schema differs")
    if _np.unique(sample_ids, axis=0).shape[0] != sample_ids.shape[0]:
        raise _ContractViolationError("ULS corpus contains an exact duplicate sample")
    frequency = _Counter(zip(decision_keys.tolist(), directions.tolist(), strict=True))
    if len(frequency) != _EXPECTED_UNIQUE_EPISODES:
        raise _ContractViolationError("ULS unique market-episode count differs")
    weights = _np.asarray(
        [
            1.0 / frequency[(int(key), int(direction))]
            for key, direction in zip(decision_keys, directions, strict=True)
        ],
        dtype=_np.float64,
    )
    deployment_mask = ordinals == _DEPLOYMENT_ORDINAL
    inputs = {name: predictors[:, index] for index, name in enumerate(_INPUT_NAMES)}
    target_columns = {_TARGET_NAMES[0]: targets}
    source_manifest: dict[str, object] = {
        "schema": _SOURCE_SCHEMA,
        "dataset": {
            "dataset_id": _TRAIN_DATASET_ID,
            "manifest_sha256": _TRAIN_DATASET_SHA256,
            "role": "train",
        },
        "source_sweep": {
            "sweep_id": _SOURCE_SWEEP_ID,
            "spec_digest": _SOURCE_SWEEP_SHA256,
            "ordinal_start": _SOURCE_ORDINAL_START,
            "ordinal_stop": _SOURCE_ORDINAL_STOP,
        },
        "deployment": {
            "source_ordinal": _DEPLOYMENT_ORDINAL,
            "source_run_id": sources[_DEPLOYMENT_ORDINAL].run_id,
            "feature_params": sources[_DEPLOYMENT_ORDINAL].config_params,
        },
        "context_carrier": _carrier_entry(carrier, context),
        "configurations": configuration_entries,
        "outcome_sources": source_entries,
        "evaluation_execution": sources[_DEPLOYMENT_ORDINAL].execution,
        "selection": {
            "bracket": "tp800_sl800",
            "boundary_forced_close_excluded": True,
            "configuration_count": len(sources),
            "cost_semantics": "unverified",
        },
    }
    source_sha = _sha256_bytes(_json_bytes(source_manifest))
    identity_document: dict[str, object] = {
        "format": "uls-entry-corpus-identity-v1",
        "builder": {"plugin_id": "uls_entry_corpus_builder_v1", "version": 1},
        "source_manifest_sha256": source_sha,
        "sample_count": int(decision_keys.size),
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
        "seed": _MODEL_BASE_PARAMS["random_seed"],
    }
    identity_key = _identity_key_from_canonical(_canonical_identity_json(identity_document))
    counts: dict[str, object] = {
        "boundary_forced_close_excluded": excluded_boundary,
        "configurations": len(sources),
        "deployment_rows": int(_np.count_nonzero(deployment_mask)),
        "exit_reasons_raw": {str(key): value for key, value in sorted(exit_reasons.items())},
        "raw_events": raw_events,
        "raw_trades": raw_trades,
        "retained_rows": int(decision_keys.size),
        "unique_episodes": len(frequency),
    }
    return _CorpusProduct(
        source_manifest,
        identity_key,
        sample_ids,
        origin_ids,
        decision_keys,
        directions,
        max_exit_keys,
        ordinals,
        deployment_mask,
        weights,
        inputs,
        target_columns,
        counts,
    )


def _discover_sources(runs_root: _Path) -> dict[int, _SourceRun]:
    plan = _read_sweep_plan(runs_root, _SOURCE_SWEEP_ID)
    if plan._document.get("spec_digest") != _SOURCE_SWEEP_SHA256:
        raise _ContractViolationError("ULS source sweep identity differs")
    expected = {
        child._experiment_fingerprint: child._child_ordinal
        for child in plan._children
        if _SOURCE_ORDINAL_START <= child._child_ordinal < _SOURCE_ORDINAL_STOP
    }
    if len(expected) != _EXPECTED_CONFIGURATIONS:
        raise _ContractViolationError("ULS source sweep does not contain 200 TP800 children")
    candidates: dict[str, list[_SourceRun]] = _defaultdict(list)
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    if scan._errors:
        raise _ContractViolationError("authoritative ULS source scan reported corruption")
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
        _validate_train_dataset(config)
        features = config.get("features")
        if type(features) is not list or len(features) != 1:
            raise _ContractViolationError("ULS source feature inventory differs")
        feature = _exact_mapping(features[0], label="ULS source feature")
        if feature.get("name") != "uls_push_v7" or feature.get("version") != 1:
            raise _ContractViolationError("ULS source provider differs")
        execution = _exact_mapping(config.get("execution"), label="ULS source execution")
        if not _is_tp800_sl800(execution):
            raise _ContractViolationError("ULS source child is not TP800/SL800")
        files = dict(manifest.artifact_hashes)
        if "features.npz" not in files:
            raise _ContractViolationError("ULS source lacks features.npz")
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
        if len(intent_files) != 5 or len(trade_files) != 5:
            raise _ContractViolationError("ULS source fold artifacts are incomplete")
        candidates[manifest.experiment_fingerprint].append(
            _SourceRun(
                expected[manifest.experiment_fingerprint],
                manifest.run_id,
                manifest.experiment_fingerprint,
                runs_root / "runs" / manifest.run_id,
                dict(_exact_mapping(feature.get("params"), label="ULS source params")),
                dict(execution),
                files["features.npz"],
                intent_files,
                trade_files,
            )
        )
    selected: dict[int, _SourceRun] = {}
    for _fingerprint, attempts in candidates.items():
        signatures = {
            (item.feature_sha256, item.intent_files, item.trade_files) for item in attempts
        }
        if len(signatures) != 1:
            raise _ContractViolationError("canonical ULS source attempts disagree")
        source = min(attempts, key=lambda item: item.run_id)
        if source.ordinal in selected:
            raise _ContractViolationError("multiple ULS fingerprints claim one ordinal")
        selected[source.ordinal] = source
    if set(selected) != set(range(_SOURCE_ORDINAL_START, _SOURCE_ORDINAL_STOP)):
        raise _ContractViolationError("canonical ULS source inventory is incomplete")
    return selected


def _discover_context_carrier(
    runs_root: _Path,
    *,
    expected_params: dict[str, object],
    platform: _PlatformIdentity,
    dependency_sha256: str,
) -> _SourceRun:
    candidates: dict[str, list[_SourceRun]] = _defaultdict(list)
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    if scan._errors:
        raise _ContractViolationError("authoritative context-carrier scan reported corruption")
    for projection in scan._projections:
        manifest = projection._manifest
        config = projection._resolved_config
        record = projection._record
        if (
            manifest is None
            or record._state is not _RunState.COMPLETE
            or record._canonical is not True
            or manifest.platform_identity != platform
            or manifest.dependency_lock_sha256 != dependency_sha256
        ):
            continue
        features = config.get("features")
        if type(features) is not list or len(features) != 2:
            continue
        first = _exact_mapping(features[0], label="carrier ULS feature")
        second = _exact_mapping(features[1], label="carrier context feature")
        if (
            first.get("name") != "uls_push_v7"
            or first.get("version") != 1
            or second != {"name": "uls_market_context_v1", "version": 1, "params": {}}
        ):
            continue
        if _canonical_json(first.get("params")) != _canonical_json(expected_params):
            continue
        _validate_train_dataset(config)
        files = dict(manifest.artifact_hashes)
        if "features.npz" not in files:
            raise _ContractViolationError("context carrier lacks features.npz")
        candidates[manifest.experiment_fingerprint].append(
            _SourceRun(
                _DEPLOYMENT_ORDINAL,
                manifest.run_id,
                manifest.experiment_fingerprint,
                runs_root / "runs" / manifest.run_id,
                dict(expected_params),
                dict(_exact_mapping(config.get("execution"), label="carrier execution")),
                files["features.npz"],
                (),
                (),
            )
        )
    if len(candidates) != 1:
        raise _ContractViolationError("exactly one semantic context carrier is required")
    attempts = next(iter(candidates.values()))
    if len({item.feature_sha256 for item in attempts}) != 1:
        raise _ContractViolationError("canonical context carrier attempts disagree")
    return min(attempts, key=lambda item: item.run_id)


def _validate_train_dataset(config: dict[str, object]) -> None:
    dataset = _exact_mapping(config.get("dataset"), label="ULS source dataset")
    if dataset != {
        "dataset_id": _TRAIN_DATASET_ID,
        "manifest_sha256": _TRAIN_DATASET_SHA256,
        "role": "train",
    }:
        raise _ContractViolationError("ULS source dataset identity differs")


def _is_tp800_sl800(execution: dict[str, object]) -> bool:
    def decoded(value: object) -> float | None:
        if type(value) is dict and set(value) == {"__float_hex__"}:
            encoded = value.get("__float_hex__")
            return float.fromhex(encoded) if type(encoded) is str else None
        return None

    return (
        decoded(execution.get("take_profit_pips")) == 800.0
        and decoded(execution.get("stop_loss_pips")) == 800.0
    )


def _read_feature_artifact(
    source: _SourceRun,
    *,
    required_names: tuple[str, ...],
    expected_provider: str,
) -> dict[str, object]:
    path = source.run_dir / "artifacts" / "features.npz"
    _verify_file_hash(path, source.feature_sha256)
    metadata, archive = _open_npz(path)
    try:
        if (
            metadata.get("format") != "trading-research-run-frame"
            or metadata.get("kind") != "features"
        ):
            raise _ContractViolationError("ULS feature artifact has the wrong format")
        raw_columns = metadata.get("columns")
        if type(raw_columns) is not list:
            raise _ContractViolationError("ULS feature metadata is malformed")
        members: dict[str, str] = {}
        origins: dict[str, object] = {}
        for item in raw_columns:
            row = _exact_mapping(item, label="ULS feature column")
            name = row.get("name")
            member = row.get("member")
            if type(name) is not str or type(member) is not str:
                raise _ContractViolationError("ULS feature column mapping is malformed")
            members[name] = member
            origins[name] = row.get("origin")
        if any(name not in members for name in required_names):
            raise _ContractViolationError("ULS feature artifact lacks required columns")
        first_origin = origins[required_names[0]]
        if any(origins[name] != first_origin for name in required_names[1:]):
            raise _ContractViolationError("ULS feature columns do not share one origin")
        origin = _exact_mapping(first_origin, label="ULS FeatureOrigin")
        if origin.get("provider_id") != expected_provider or origin.get("version") != 1:
            raise _ContractViolationError("ULS feature artifact has the wrong provider")
        keys = _np.array(archive["keys"], dtype=_np.int64, copy=True)
        columns = {
            name: _np.array(archive[members[name]], dtype=_np.float64, copy=True)
            for name in required_names
        }
    finally:
        archive.close()
    if keys.ndim != 1 or any(values.shape != keys.shape for values in columns.values()):
        raise _ContractViolationError("ULS feature artifact arrays are misaligned")
    origin_bytes = _canonical_json(origin).encode("ascii")
    return {
        "artifact_sha256": source.feature_sha256,
        "keys": keys,
        "columns": columns,
        "origin": origin,
        "origin_digest": _hashlib.sha256(origin_bytes).digest(),
    }


def _read_observations(
    source: _SourceRun,
    *,
    feature_keys: _npt.NDArray[_np.int64],
    events: _npt.NDArray[_np.float64],
) -> list[_Observation]:
    observed: list[_Observation] = []
    seen: set[tuple[int, int]] = set()
    for (intent_name, intent_sha), (trade_name, trade_sha) in zip(
        source.intent_files, source.trade_files, strict=True
    ):
        intent_path = source.run_dir / "artifacts" / intent_name
        trade_path = source.run_dir / "artifacts" / trade_name
        _verify_file_hash(intent_path, intent_sha)
        _verify_file_hash(trade_path, trade_sha)
        intent_meta, intent_archive = _open_npz(intent_path)
        try:
            if intent_meta.get("format") != "trading-research-intent-frame":
                raise _ContractViolationError("ULS intent artifact has the wrong format")
            intent_keys = _np.array(intent_archive["keys"], dtype=_np.int64, copy=True)
            exposures = _np.array(intent_archive["target_exposure"], dtype=_np.float64, copy=True)
        finally:
            intent_archive.close()
        trade_meta, trade_archive = _open_npz(trade_path)
        try:
            if trade_meta.get("format") != "trading-research-trade-log":
                raise _ContractViolationError("ULS TradeLog artifact has the wrong format")
            entry_keys = _np.array(trade_archive["entry_key"], dtype=_np.int64, copy=True)
            exit_keys = _np.array(trade_archive["exit_key"], dtype=_np.int64, copy=True)
            directions = _np.array(trade_archive["direction"], dtype=_np.int8, copy=True)
            net_pnl = _np.array(trade_archive["net_pnl"], dtype=_np.float64, copy=True)
            exit_reason = _np.array(trade_archive["exit_reason"], dtype=_np.int8, copy=True)
        finally:
            trade_archive.close()
        rows = entry_keys.size
        if intent_keys.shape != exposures.shape or any(
            values.shape != (rows,) for values in (exit_keys, directions, net_pnl, exit_reason)
        ):
            raise _ContractViolationError("ULS intent or TradeLog arrays are misaligned")
        intent_lookup = {
            int(key): float(value) for key, value in zip(intent_keys, exposures, strict=True)
        }
        entry_positions = _np.searchsorted(feature_keys, entry_keys)
        if (
            _np.any(entry_positions <= 0)
            or _np.any(entry_positions >= feature_keys.size)
            or not _np.array_equal(feature_keys[entry_positions], entry_keys)
        ):
            raise _ContractViolationError("ULS trade entry is absent from feature axis")
        source_positions = entry_positions - 1
        source_keys = feature_keys[source_positions]
        if not _np.array_equal(events[source_positions], directions.astype(_np.float64)):
            raise _ContractViolationError("ULS trade does not follow its recorded event")
        for index in range(rows):
            intent_key = int(source_keys[index])
            direction = int(directions[index])
            if intent_lookup.get(intent_key) != float(direction):
                raise _ContractViolationError("ULS trade does not map to opening intent")
            identity = (intent_key, direction)
            if identity in seen:
                raise _ContractViolationError("one run opened the same ULS sample twice")
            seen.add(identity)
            observed.append(
                _Observation(
                    intent_key,
                    int(exit_keys[index]),
                    direction,
                    float(net_pnl[index]),
                    int(exit_reason[index]),
                )
            )
    return observed


def _sample_ids(
    *,
    origin_digest: bytes,
    decision_keys: _npt.NDArray[_np.int64],
    directions: _npt.NDArray[_np.int8],
) -> _npt.NDArray[_np.uint8]:
    rows = []
    for key, direction in zip(decision_keys, directions, strict=True):
        preimage = (
            origin_digest
            + int(key).to_bytes(8, "big", signed=True)
            + int(direction).to_bytes(1, "big", signed=True)
        )
        rows.append(_np.frombuffer(_hashlib.sha256(preimage).digest(), dtype=_np.uint8))
    return _np.vstack(rows)


def _configuration_entry(source: _SourceRun, feature: dict[str, object]) -> dict[str, object]:
    return {
        "source_ordinal": source.ordinal,
        "feature_origin_digest": _typing.cast("bytes", feature["origin_digest"]).hex(),
        "params": source.config_params,
    }


def _source_entry(source: _SourceRun, feature: dict[str, object]) -> dict[str, object]:
    return {
        "source_ordinal": source.ordinal,
        "run_id": source.run_id,
        "experiment_fingerprint": source.fingerprint,
        "artifacts": {
            "feature": {"name": "features.npz", "sha256": source.feature_sha256},
            "intents": [{"name": name, "sha256": digest} for name, digest in source.intent_files],
            "trade_logs": [{"name": name, "sha256": digest} for name, digest in source.trade_files],
        },
        "feature_origin_digest": _typing.cast("bytes", feature["origin_digest"]).hex(),
    }


def _carrier_entry(source: _SourceRun, feature: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": source.run_id,
        "experiment_fingerprint": source.fingerprint,
        "artifact": {"name": "features.npz", "sha256": source.feature_sha256},
        "context_origin_digest": _typing.cast("bytes", feature["origin_digest"]).hex(),
    }


def _fit_and_publish(
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
    threshold, qualification, first_payload = _qualify(corpus)
    model_params = {**_MODEL_BASE_PARAMS, "selection_threshold": threshold}
    model = _CORPUS_MODELS.create("uls_entry_forest_v1", **model_params)
    plan = _snapshot_corpus_model(model)
    fit_mask = _qualification_masks(corpus)[0]
    fit_inputs = _make_corpus_model_inputs(
        inputs={name: values[fit_mask] for name, values in corpus.inputs.items()},
        targets={name: values[fit_mask] for name, values in corpus.targets.items()},
        weights=corpus.weights[fit_mask],
    )
    payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    _assert_equal_payload(first_payload, payload)
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
        training_spec={**_TRAINING_SPEC, "selection_threshold": threshold},
        platform_identity=platform,
        dependency_lock_sha256=dependency_sha,
        comparison_tolerance=plan._comparison_tolerance,
    )
    state = _bind_fitted_state(state_identity, payload)
    fitted_bytes = _encode_fitted_state(state_identity, state)
    fitted_sha = _sha256_bytes(fitted_bytes)
    report: dict[str, object] = {
        "schema": _REPORT_SCHEMA,
        "corpus_identity_key": corpus.corpus_identity_key,
        "corpus_sha256": corpus_sha,
        "fitted_state_identity_key": state.identity_key(),
        "fitted_state_sha256": fitted_sha,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "counts": corpus.counts,
        "qualification": qualification,
        "selection_threshold": threshold,
        "solver": {
            "algorithm": "weighted_random_forest_regression_v1",
            **_MODEL_BASE_PARAMS,
            "n_jobs": 1,
        },
        "determinism": {
            "corpus_double_build_equal": True,
            "fit_payload_equal_before_and_after_threshold_freeze": True,
        },
        "source_manifest_sha256": source_sha,
        "training_spec": {**_TRAINING_SPEC, "selection_threshold": threshold},
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "labels_are_recorded_executed_trades_only": True,
            "missing_unexecuted_opportunities_are_not_imputed": True,
            "target_change_outcomes_depend_on_source_strategy_path": True,
        },
    }
    artifacts = {
        "corpus.npz": corpus_bytes,
        "fitted-state.npz": fitted_bytes,
        "source-manifest.json": source_bytes,
        "training-report.json": _json_bytes(report),
    }
    artifact_hashes = {
        name: _sha256_bytes(payload_bytes) for name, payload_bytes in sorted(artifacts.items())
    }
    lifecycle = b"".join(
        _json_bytes(
            {
                "schema": "research-training-bundle-lifecycle-event-v1",
                "ordinal": ordinal,
                "state": lifecycle_state,
            }
        )
        for ordinal, lifecycle_state in enumerate(("PLANNED", "RUNNING", "COMPLETE"))
    )
    manifest: dict[str, object] = {
        "schema": _BUNDLE_SCHEMA,
        "bundle_id": destination.name,
        "state": "COMPLETE",
        "files": artifact_hashes,
        "lifecycle_sha256": _sha256_bytes(lifecycle),
        "corpus_identity_key": corpus.corpus_identity_key,
        "fitted_state_identity_key": state.identity_key(),
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    manifest_bytes = _json_bytes(manifest)
    if not is_new:
        existing = _read_bundle_manifest(destination)
        if _json_bytes(existing) != manifest_bytes:
            raise _ContractViolationError("existing immutable ULS entry bundle differs")
        _verify_bundle_files(destination, existing)
        return _bundle_reference_document(existing, manifest_sha256=_sha256_bytes(manifest_bytes))
    _publish_new_bundle(
        destination=destination,
        artifacts=artifacts,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )
    return _bundle_reference_document(manifest, manifest_sha256=_sha256_bytes(manifest_bytes))


def _qualification_masks(
    corpus: _CorpusProduct,
) -> tuple[
    _npt.NDArray[_np.bool_],
    _npt.NDArray[_np.bool_],
    _npt.NDArray[_np.bool_],
    int,
    int,
]:
    unique_keys = _np.unique(corpus.decision_keys)
    first = int(unique_keys[_math.floor(unique_keys.size * 0.70)])
    second = int(unique_keys[_math.floor(unique_keys.size * 0.85)])
    fit = (corpus.decision_keys < first) & (corpus.max_exit_keys < first)
    calibration = (
        (corpus.decision_keys >= first)
        & (corpus.decision_keys < second)
        & (corpus.max_exit_keys < second)
    )
    test = corpus.decision_keys >= second
    if not _np.any(fit) or not _np.any(calibration) or not _np.any(test):
        raise _ContractViolationError("ULS chronological qualification split is empty")
    return fit, calibration, test, first, second


def _qualify(
    corpus: _CorpusProduct,
) -> tuple[float, dict[str, object], _FittedPayload]:
    fit, calibration, test, first, second = _qualification_masks(corpus)
    provisional = _CORPUS_MODELS.create(
        "uls_entry_forest_v1",
        **_MODEL_BASE_PARAMS,
        selection_threshold=0.0,
    )
    plan = _snapshot_corpus_model(provisional)
    fit_inputs = _make_corpus_model_inputs(
        inputs={name: values[fit] for name, values in corpus.inputs.items()},
        targets={name: values[fit] for name, values in corpus.targets.items()},
        weights=corpus.weights[fit],
    )
    payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    calibration_prediction = _forest_predict(
        payload,
        _np.column_stack([corpus.inputs[name][calibration] for name in _INPUT_NAMES]),
    )
    test_prediction = _forest_predict(
        payload,
        _np.column_stack([corpus.inputs[name][test] for name in _INPUT_NAMES]),
    )
    calibration_target = corpus.targets[_TARGET_NAMES[0]][calibration]
    calibration_deployment = corpus.deployment_mask[calibration]
    deployment_prediction = calibration_prediction[calibration_deployment]
    deployment_target = calibration_target[calibration_deployment]
    if deployment_prediction.size < 25:
        raise _ContractViolationError("deployment configuration has too few calibration rows")
    candidates = {
        float(_np.nextafter(deployment_prediction.min(), -_np.inf)),
        0.0,
        *(
            float(value)
            for value in _np.quantile(
                deployment_prediction,
                _np.arange(0.0, 1.0, 0.05, dtype=_np.float64),
            )
        ),
    }
    minimum = max(25, _math.ceil(0.05 * deployment_prediction.size))
    candidate_rows: list[dict[str, object]] = []
    ranking: list[tuple[float, int, float]] = []
    for threshold in sorted(candidates):
        selected = deployment_prediction > threshold
        count = int(_np.count_nonzero(selected))
        if count < minimum:
            continue
        total = float(_np.sum(deployment_target[selected], dtype=_np.float64))
        candidate_rows.append(
            {
                "threshold": threshold,
                "selected_rows": count,
                "selected_fraction": count / deployment_prediction.size,
                "total_recorded_net_pnl": total,
            }
        )
        ranking.append((total, count, -threshold))
    if not ranking:
        raise _ContractViolationError("ULS threshold grid has no eligible candidate")
    winner = max(ranking)
    threshold = -winner[2]
    test_target = corpus.targets[_TARGET_NAMES[0]][test]
    test_selected = test_prediction > threshold
    test_deployment = corpus.deployment_mask[test]
    deployment_selected = test_selected & test_deployment
    report: dict[str, object] = {
        "boundary_decision_keys": [first, second],
        "fit_rows": int(_np.count_nonzero(fit)),
        "calibration_rows": int(_np.count_nonzero(calibration)),
        "test_rows": int(_np.count_nonzero(test)),
        "purged_fit_rows": int(_np.count_nonzero((corpus.decision_keys < first) & ~fit)),
        "purged_calibration_rows": int(
            _np.count_nonzero(
                (corpus.decision_keys >= first) & (corpus.decision_keys < second) & ~calibration
            )
        ),
        "threshold_candidates": candidate_rows,
        "chosen_threshold": threshold,
        "deployment_calibration": {
            "rows": int(deployment_prediction.size),
            "baseline_total_recorded_net_pnl": float(_np.sum(deployment_target, dtype=_np.float64)),
            "selected_total_recorded_net_pnl": winner[0],
            "selected_rows": winner[1],
        },
        "deployment_test": {
            "rows": int(_np.count_nonzero(test_deployment)),
            "baseline_total_recorded_net_pnl": float(
                _np.sum(test_target[test_deployment], dtype=_np.float64)
            ),
            "selected_total_recorded_net_pnl": float(
                _np.sum(test_target[deployment_selected], dtype=_np.float64)
            ),
            "selected_rows": int(_np.count_nonzero(deployment_selected)),
        },
        "all_config_test": {
            "baseline_weighted_total_recorded_net_pnl": float(
                _np.sum(corpus.weights[test] * test_target, dtype=_np.float64)
            ),
            "selected_weighted_total_recorded_net_pnl": float(
                _np.sum(
                    corpus.weights[test][test_selected] * test_target[test_selected],
                    dtype=_np.float64,
                )
            ),
            "selected_rows": int(_np.count_nonzero(test_selected)),
        },
    }
    return threshold, report, payload


def _encode_corpus(corpus: _CorpusProduct) -> bytes:
    metadata = {
        "format": _CORPUS_FORMAT,
        "format_version": 1,
        "corpus_identity_key": corpus.corpus_identity_key,
        "input_names": list(_INPUT_NAMES),
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
        "configuration_ordinals": corpus.configuration_ordinals,
        "deployment_mask": corpus.deployment_mask,
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


def _assert_equal_corpus(left: _CorpusProduct, right: _CorpusProduct) -> None:
    if (
        left.source_manifest != right.source_manifest
        or left.corpus_identity_key != right.corpus_identity_key
        or left.counts != right.counts
        or set(left.inputs) != set(right.inputs)
        or set(left.targets) != set(right.targets)
    ):
        raise _ContractViolationError("ULS corpus double build metadata differs")
    pairs: list[tuple[_npt.NDArray[_typing.Any], _npt.NDArray[_typing.Any]]] = [
        (left.sample_ids, right.sample_ids),
        (left.feature_origin_ids, right.feature_origin_ids),
        (left.decision_keys, right.decision_keys),
        (left.directions, right.directions),
        (left.max_exit_keys, right.max_exit_keys),
        (left.configuration_ordinals, right.configuration_ordinals),
        (left.deployment_mask, right.deployment_mask),
        (left.weights, right.weights),
    ]
    pairs.extend((left.inputs[name], right.inputs[name]) for name in _INPUT_NAMES)
    pairs.extend((left.targets[name], right.targets[name]) for name in _TARGET_NAMES)
    if any(not _np.array_equal(first, second) for first, second in pairs):
        raise _ContractViolationError("ULS corpus double build arrays differ")


def _main(argv: list[str] | None = None) -> int:
    parser = _argparse.ArgumentParser(prog="uls-entry-bundle")
    parser.add_argument("--runs-root", required=True, type=lambda value: _Path(value).resolve())
    parser.add_argument(
        "--repository-root", required=True, type=lambda value: _Path(value).resolve()
    )
    parser.add_argument(
        "--dependency-lock", required=True, type=lambda value: _Path(value).resolve()
    )
    args = parser.parse_args(argv)
    try:
        reference = _build_uls_entry_bundle(
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
