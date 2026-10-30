"""Build forward-only ULS entry states and the seven-arm TRAIN source sweep."""

from __future__ import annotations

import argparse as _argparse
import io as _io
import json as _json
import math as _math
import os as _os
import sys as _sys
import typing as _typing
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
from trading_research.experiments.training_bundles import (
    _assert_equal_payload,
    _begin_bundle_lifecycle,
    _bundle_reference_document,
    _clean_commit,
    _exact_mapping,
    _fail_bundle_lifecycle,
    _json_bytes,
    _open_npz,
    _publish_new_bundle,
    _read_bundle_manifest,
    _sha256_bytes,
    _sha256_file,
    _verify_bundle_files,
)
from trading_research.models import CORPUS_RESEARCH_MODELS as _CORPUS_MODELS
from trading_research.models.execution import (
    _fit_corpus_model_payload,
    _snapshot_corpus_model,
)
from trading_research.models.uls_entry_selector import _INPUT_NAMES, _TARGET_NAMES, _forest_predict

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue

__all__: list[str] = []

_SWEEP_ID = "uls-entry-tp-oof-train-v1"
_TP_PIPS = (200, 300, 400, 500, 600, 700, 800)
_FOLDS = (
    {"fit_end_fraction": 0.35, "calibration_end_fraction": 0.45, "eval_end_fraction": 0.70},
    {"fit_end_fraction": 0.60, "calibration_end_fraction": 0.70, "eval_end_fraction": 1.00},
)
_MODEL_BASE_PARAMS: _Mapping[str, _ParamValue] = {
    "bootstrap": False,
    "max_depth": 8,
    "max_features": 0.75,
    "min_samples_leaf": 96,
    "n_estimators": 64,
    "random_seed": 20_260_826,
}
_TRAINING_SPEC: _Mapping[str, _ParamValue] = {
    "folds": "expanding_two_fold_35_45_70__60_70_100_v1",
    "grouping": "intent_key_direction_v1",
    "purge": "exit_reaches_next_boundary_v1",
    "threshold_candidates": "deployment_prediction_quantiles_0_to_95_step_5_plus_zero_v1",
    "threshold_minimum_calibration_rows": 25,
    "threshold_objective": "deployment_total_recorded_net_pnl_v1",
}
_TARGET_SPEC_PARAMS: _Mapping[str, _ParamValue] = {
    "boundary_forced_close_excluded": True,
    "cost_semantics": "unverified",
    "source_take_profit_pips": 800.0,
    "source_stop_loss_pips": 800.0,
    "target_column": "net_pnl",
}
_TARGET_SPEC_ID = "recorded_trade_net_pnl_v1"
_BUNDLE_SCHEMA = "research-training-bundle-manifest-v1"
_CORPUS_FORMAT = "uls-entry-oof-corpus-v1"
_REPORT_SCHEMA = "uls-entry-oof-training-report-v1"
_SOURCE_SCHEMA = "uls-entry-oof-source-manifest-v1"
_TRAIN_END = 1_703_894_160


@_dataclass(frozen=True, slots=True)
class _EntryCorpus:
    bundle_reference: dict[str, object]
    source_manifest: dict[str, object]
    corpus_sha256: str
    decision_keys: _npt.NDArray[_np.int64]
    max_exit_keys: _npt.NDArray[_np.int64]
    deployment_mask: _npt.NDArray[_np.bool_]
    weights: _npt.NDArray[_np.float64]
    inputs: dict[str, _npt.NDArray[_np.float64]]
    target: _npt.NDArray[_np.float64]


@_dataclass(frozen=True, slots=True)
class _FoldProduct:
    ordinal: int
    fit_end: int
    calibration_end: int
    eval_start: int
    eval_end: int
    selection_threshold: float
    reference: dict[str, object]
    model_params: dict[str, object]
    qualification: dict[str, object]


def _build(
    *,
    runs_root: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
    entry_bundle_dir: _Path,
    carrier_config_path: _Path,
    output_dir: _Path,
) -> dict[str, object]:
    _assert_worker_environment()
    commit = _clean_commit(repository_root)
    dependency_sha = _sha256_file(dependency_lock_path)
    platform = _PlatformIdentity(commit, False, None)
    corpus = _load_entry_corpus(entry_bundle_dir)
    boundaries = _fold_boundaries(corpus.decision_keys)
    products = tuple(
        _fit_fold(
            corpus=corpus,
            ordinal=ordinal,
            boundaries=fold_boundaries,
            runs_root=runs_root,
            platform=platform,
            dependency_sha=dependency_sha,
            commit=commit,
        )
        for ordinal, fold_boundaries in enumerate(boundaries)
    )
    request = _request(products, carrier_config_path=carrier_config_path)
    request_bytes = _json_bytes(request)
    freeze = {
        "schema": "uls-entry-tp-oof-freeze-v1",
        "sweep_id": _SWEEP_ID,
        "entry_source_bundle": corpus.bundle_reference,
        "folds": [
            {
                "ordinal": product.ordinal,
                "fit_end": product.fit_end,
                "calibration_end": product.calibration_end,
                "eval_start": product.eval_start,
                "eval_end": product.eval_end,
                "selection_threshold": product.selection_threshold,
                "bundle_reference": product.reference,
            }
            for product in products
        ],
        "tp_pips": list(_TP_PIPS),
        "stop_loss_pips": 800,
        "expected_children": len(products) * len(_TP_PIPS),
        "request_file": "request.json",
        "request_sha256": _sha256_bytes(request_bytes),
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    freeze_bytes = _json_bytes(freeze)
    if output_dir.exists():
        existing_request = (output_dir / "request.json").read_bytes()
        existing_freeze = (output_dir / "manifest.json").read_bytes()
        if existing_request != request_bytes or existing_freeze != freeze_bytes:
            raise _ContractViolationError("existing OOF freeze differs")
    else:
        output_dir.mkdir(parents=True)
        _write_new(output_dir / "request.json", request_bytes)
        _write_new(output_dir / "manifest.json", freeze_bytes)
    return freeze


def _load_entry_corpus(bundle: _Path) -> _EntryCorpus:
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    manifest = _read_bundle_manifest(bundle)
    _verify_bundle_files(bundle, manifest)
    files = _exact_mapping(manifest.get("files"), label="entry bundle files")
    corpus_sha = files.get("corpus.npz")
    fitted_sha = files.get("fitted-state.npz")
    if type(corpus_sha) is not str or type(fitted_sha) is not str:
        raise _ContractViolationError("entry bundle artifact hashes are malformed")
    source_manifest = _json.loads((bundle / "source-manifest.json").read_text(encoding="ascii"))
    if type(source_manifest) is not dict:
        raise _ContractViolationError("entry source manifest is malformed")
    metadata, archive = _open_npz(bundle / "corpus.npz")
    try:
        input_names = metadata.get("input_names")
        target_names = metadata.get("target_names")
        if (
            metadata.get("format") != "uls-entry-corpus-v1"
            or type(input_names) is not list
            or tuple(input_names) != _INPUT_NAMES
            or type(target_names) is not list
            or tuple(target_names) != _TARGET_NAMES
        ):
            raise _ContractViolationError("entry corpus schema differs")
        inputs = {
            name: _np.array(archive[f"input_{index:04d}"], dtype=_np.float64, copy=True)
            for index, name in enumerate(_INPUT_NAMES)
        }
        target = _np.array(archive["target_0000"], dtype=_np.float64, copy=True)
        decision_keys = _np.array(archive["decision_keys"], dtype=_np.int64, copy=True)
        max_exit_keys = _np.array(archive["max_exit_keys"], dtype=_np.int64, copy=True)
        deployment = _np.array(archive["deployment_mask"], dtype=_np.bool_, copy=True)
        weights = _np.array(archive["weights"], dtype=_np.float64, copy=True)
    finally:
        archive.close()
    rows = decision_keys.size
    if any(
        values.shape != (rows,)
        for values in (*inputs.values(), target, max_exit_keys, deployment, weights)
    ):
        raise _ContractViolationError("entry corpus arrays are misaligned")
    reference = {
        "bundle_id": bundle.name,
        "bundle_manifest_sha256": _sha256_bytes(manifest_bytes),
        "fitted_state_identity_key": manifest["fitted_state_identity_key"],
        "fitted_state_artifact_sha256": fitted_sha,
    }
    return _EntryCorpus(
        reference,
        source_manifest,
        corpus_sha,
        decision_keys,
        max_exit_keys,
        deployment,
        weights,
        inputs,
        target,
    )


def _fold_boundaries(keys: _npt.NDArray[_np.int64]) -> tuple[tuple[int, int, int, int], ...]:
    unique = _np.unique(keys)
    products: list[tuple[int, int, int, int]] = []
    for raw in _FOLDS:
        fit_end = int(unique[_math.floor(unique.size * raw["fit_end_fraction"])])
        calibration_end = int(
            unique[_math.floor(unique.size * raw["calibration_end_fraction"])]
        )
        eval_end = (
            _TRAIN_END
            if raw["eval_end_fraction"] == 1.0
            else int(unique[_math.floor(unique.size * raw["eval_end_fraction"])]) - 1
        )
        products.append((fit_end, calibration_end, calibration_end, eval_end))
    if products[0][3] >= products[1][2] or products[0][2] >= products[0][3]:
        raise _ContractViolationError("OOF fold bounds overlap or are empty")
    return tuple(products)


def _fit_fold(
    *,
    corpus: _EntryCorpus,
    ordinal: int,
    boundaries: tuple[int, int, int, int],
    runs_root: _Path,
    platform: _PlatformIdentity,
    dependency_sha: str,
    commit: str,
) -> _FoldProduct:
    fit_end, calibration_end, eval_start, eval_end = boundaries
    keys = corpus.decision_keys
    fit = (keys < fit_end) & (corpus.max_exit_keys < fit_end)
    calibration = (
        (keys >= fit_end)
        & (keys < calibration_end)
        & (corpus.max_exit_keys < calibration_end)
    )
    deployment_calibration = calibration & corpus.deployment_mask
    if _np.count_nonzero(fit) < 1_000 or _np.count_nonzero(deployment_calibration) < 25:
        raise _ContractViolationError("OOF fold has too few fit or calibration rows")
    provisional = _CORPUS_MODELS.create(
        "uls_entry_forest_v1",
        **_MODEL_BASE_PARAMS,
        selection_threshold=0.0,
    )
    plan = _snapshot_corpus_model(provisional)
    fit_inputs = _make_corpus_model_inputs(
        inputs={name: values[fit] for name, values in corpus.inputs.items()},
        targets={_TARGET_NAMES[0]: corpus.target[fit]},
        weights=corpus.weights[fit],
    )
    first_payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    second_payload = _fit_corpus_model_payload(plan, inputs=fit_inputs)
    _assert_equal_payload(first_payload, second_payload)
    matrix = _np.column_stack(
        [corpus.inputs[name][deployment_calibration] for name in _INPUT_NAMES]
    )
    prediction = _forest_predict(first_payload, matrix)
    target = corpus.target[deployment_calibration]
    threshold, threshold_rows = _choose_threshold(prediction, target)
    model_params = {**_MODEL_BASE_PARAMS, "selection_threshold": threshold}
    selected_source = keys < calibration_end
    corpus_bytes = _encode_fold_corpus(
        corpus,
        selected_source,
        ordinal=ordinal,
        boundaries=boundaries,
    )
    corpus_sha = _sha256_bytes(corpus_bytes)
    source_manifest = {
        "schema": _SOURCE_SCHEMA,
        "upstream_entry_bundle": corpus.bundle_reference,
        "upstream_entry_corpus_sha256": corpus.corpus_sha256,
        "fold_ordinal": ordinal,
        "fit_end": fit_end,
        "calibration_end": calibration_end,
        "eval_start": eval_start,
        "eval_end": eval_end,
        "cost_semantics": "unverified",
    }
    source_bytes = _json_bytes(source_manifest)
    source_sha = _sha256_bytes(source_bytes)
    identity_document = {
        "format": "uls-entry-oof-corpus-identity-v1",
        "builder": {"plugin_id": "uls_entry_oof_builder_v1", "version": 1},
        "source_manifest_sha256": source_sha,
        "corpus_sha256": corpus_sha,
        "fold_ordinal": ordinal,
        "model_params": model_params,
        "training_spec": _TRAINING_SPEC,
        "platform": {"git_commit": commit, "git_dirty": False, "diff_sha256": None},
        "dependency_lock_sha256": dependency_sha,
    }
    corpus_identity = _identity_key_from_canonical(
        _canonical_identity_json(identity_document)
    )
    model = _CORPUS_MODELS.create("uls_entry_forest_v1", **model_params)
    frozen_plan = _snapshot_corpus_model(model)
    state_identity = _CorpusFittedStateIdentity(
        plugin_id=frozen_plan._origin.plugin_id,
        plugin_version=frozen_plan._origin.version,
        plugin_params=frozen_plan._origin.params,
        inference_feature_names=frozen_plan._required_feature_names,
        corpus_identity_key=corpus_identity,
        corpus_artifact_sha256=corpus_sha,
        source_manifest_sha256=source_sha,
        target_spec_id=_TARGET_SPEC_ID,
        target_spec_version=1,
        target_spec_params=_TARGET_SPEC_PARAMS,
        training_spec={**_TRAINING_SPEC, "fold_ordinal": ordinal, "selection_threshold": threshold},
        platform_identity=platform,
        dependency_lock_sha256=dependency_sha,
        comparison_tolerance=frozen_plan._comparison_tolerance,
    )
    state = _bind_fitted_state(state_identity, first_payload)
    fitted_bytes = _encode_fitted_state(state_identity, state)
    qualification = {
        "fit_rows": int(_np.count_nonzero(fit)),
        "calibration_rows": int(_np.count_nonzero(calibration)),
        "deployment_calibration_rows": int(target.size),
        "threshold_candidates": threshold_rows,
        "chosen_threshold": threshold,
        "eval_start": eval_start,
        "eval_end": eval_end,
    }
    report = {
        "schema": _REPORT_SCHEMA,
        "fold_ordinal": ordinal,
        "corpus_identity_key": corpus_identity,
        "corpus_sha256": corpus_sha,
        "fitted_state_identity_key": state.identity_key(),
        "fitted_state_sha256": _sha256_bytes(fitted_bytes),
        "selection_threshold": threshold,
        "qualification": qualification,
        "solver": {**_MODEL_BASE_PARAMS, "n_jobs": 1},
        "determinism": {"fit_payload_equal": True},
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "forward_only_for_declared_eval_span": True,
        },
    }
    artifacts = {
        "corpus.npz": corpus_bytes,
        "fitted-state.npz": fitted_bytes,
        "source-manifest.json": source_bytes,
        "training-report.json": _json_bytes(report),
    }
    hashes = {name: _sha256_bytes(value) for name, value in sorted(artifacts.items())}
    bundle_id = f"uls-entry-oof-v1-{corpus_identity[:20]}"
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
            raise _ContractViolationError("existing OOF bundle differs")
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
    reference = _bundle_reference_document(
        manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )
    return _FoldProduct(
        ordinal,
        fit_end,
        calibration_end,
        eval_start,
        eval_end,
        threshold,
        reference,
        dict(model_params),
        qualification,
    )


def _choose_threshold(
    prediction: _npt.NDArray[_np.float64],
    target: _npt.NDArray[_np.float64],
) -> tuple[float, list[dict[str, object]]]:
    candidates = {
        float(_np.nextafter(prediction.min(), -_np.inf)),
        0.0,
        *(float(value) for value in _np.quantile(prediction, _np.arange(0.0, 1.0, 0.05))),
    }
    minimum = max(25, _math.ceil(0.05 * prediction.size))
    rows: list[dict[str, object]] = []
    ranking: list[tuple[float, int, float]] = []
    for threshold in sorted(candidates):
        selected = prediction > threshold
        count = int(_np.count_nonzero(selected))
        if count < minimum:
            continue
        total = float(_np.sum(target[selected], dtype=_np.float64))
        rows.append({"threshold": threshold, "selected_rows": count, "total_net_pnl": total})
        ranking.append((total, count, -threshold))
    if not ranking:
        raise _ContractViolationError("OOF threshold grid has no eligible candidate")
    winner = max(ranking)
    return -winner[2], rows


def _encode_fold_corpus(
    corpus: _EntryCorpus,
    selected: _npt.NDArray[_np.bool_],
    *,
    ordinal: int,
    boundaries: tuple[int, int, int, int],
) -> bytes:
    metadata = {
        "format": _CORPUS_FORMAT,
        "format_version": 1,
        "input_names": list(_INPUT_NAMES),
        "target_names": list(_TARGET_NAMES),
        "fold_ordinal": ordinal,
        "boundaries": list(boundaries),
        "source_entry_bundle": corpus.bundle_reference,
    }
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        "__meta__": _np.frombuffer(_canonical_json(metadata).encode("ascii"), dtype=_np.uint8),
        "decision_keys": corpus.decision_keys[selected],
        "max_exit_keys": corpus.max_exit_keys[selected],
        "deployment_mask": corpus.deployment_mask[selected],
        "weights": corpus.weights[selected],
        "target_0000": corpus.target[selected],
    }
    arrays.update(
        {
            f"input_{index:04d}": corpus.inputs[name][selected]
            for index, name in enumerate(_INPUT_NAMES)
        }
    )
    output = _io.BytesIO()
    _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **arrays)
    payload = output.getvalue()
    _validate_zip_envelope(payload, tuple(arrays))
    return payload


def _request(
    products: tuple[_FoldProduct, ...],
    *,
    carrier_config_path: _Path,
) -> dict[str, object]:
    base = _json.loads(carrier_config_path.read_text(encoding="utf-8"))
    if type(base) is not dict or base.get("schema") != "research-experiment-config-v1":
        raise _ContractViolationError("OOF carrier config is malformed")
    base = dict(base)
    base["schema"] = "research-experiment-config-v2"
    base.pop("folds", None)
    base["fit_mode"] = {"mode": "frozen_state_v1", **products[0].reference}
    base["model"] = {
        "name": "uls_entry_forest_v1",
        "version": 1,
        "params": _encoded_params(products[0].model_params),
    }
    base["policies"] = [
        {
            "name": "threshold_v1",
            "version": 1,
            "params": {"output_column": "score", "threshold": {"__float_hex__": "0x0.0p+0"}},
        }
    ]
    base["bounds"] = {
        "context_start": base["bounds"]["context_start"],
        "eval_start": products[0].eval_start,
        "eval_end": products[0].eval_end,
    }
    base["trade_evaluators"] = [
        {"name": "trade_summary_v1", "version": 1, "params": {"min_effective_trades": 1}},
        {"name": "equity_drawdown_v1", "version": 1, "params": {"min_effective_rows": 1}},
    ]
    values = [(product, tp) for product in products for tp in _TP_PIPS]
    return {
        "schema": "research-sweep-request-v1",
        "sweep_id": _SWEEP_ID,
        "base_config": base,
        "axes": [
            {"json_pointer": "/bounds", "values": [
                {
                    "context_start": base["bounds"]["context_start"],
                    "eval_start": product.eval_start,
                    "eval_end": product.eval_end,
                }
                for product, _tp in values
            ]},
            {"json_pointer": "/fit_mode", "values": [
                {"mode": "frozen_state_v1", **product.reference}
                for product, _tp in values
            ]},
            {"json_pointer": "/model/params", "values": [
                _encoded_params(product.model_params) for product, _tp in values
            ]},
            {"json_pointer": "/execution/take_profit_pips", "values": [
                {"__float_hex__": float(tp).hex()} for _product, tp in values
            ]},
        ],
        "combination": "zip",
    }


def _encoded_params(params: _Mapping[str, object]) -> dict[str, object]:
    return {
        name: {"__float_hex__": value.hex()} if type(value) is float else value
        for name, value in params.items()
    }


def _write_new(path: _Path, payload: bytes) -> None:
    descriptor = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = _os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("OOF freeze write made no progress")
            remaining = remaining[written:]
        _os.fsync(descriptor)
    finally:
        _os.close(descriptor)


def _main(argv: list[str] | None = None) -> int:
    parser = _argparse.ArgumentParser(prog="uls-tp-oof")
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
        "--carrier-config", required=True, type=lambda value: _Path(value).resolve()
    )
    parser.add_argument("--output-dir", required=True, type=lambda value: _Path(value).resolve())
    args = parser.parse_args(argv)
    try:
        result = _build(
            runs_root=args.runs_root,
            repository_root=args.repository_root,
            dependency_lock_path=args.dependency_lock,
            entry_bundle_dir=args.entry_bundle_dir,
            carrier_config_path=args.carrier_config,
            output_dir=args.output_dir,
        )
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2
    _sys.stdout.write(_canonical_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
