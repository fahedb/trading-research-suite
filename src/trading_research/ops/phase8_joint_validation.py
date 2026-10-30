"""Freeze and report the full non-interruptible ULS joint-model comparison."""

from __future__ import annotations

import argparse as _argparse
import os as _os
import re as _re
import struct as _struct
import sys as _sys
import time as _time
import typing as _typing
from pathlib import Path as _Path

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.runs import RunState as _RunState
from trading_research.experiments.runs import _scan_run_projections
from trading_research.experiments.sweeps import _expand_request, _read_sweep_plan
from trading_research.experiments.training_bundles import (
    _bundle_reference_document,
    _canonical_document_json,
    _exact_mapping,
    _json_bytes,
    _read_bundle_manifest,
    _read_json,
    _sha256_bytes,
    _sha256_file,
    _verify_bundle_files,
    _verify_file_hash,
    _write_new,
)
from trading_research.experiments.uls_joint_bundles import _SOURCE_SCHEMA

if _typing.TYPE_CHECKING:
    from trading_research.experiments.runs import _RunProjection

__all__: list[str] = []

_BUNDLE_ID = _re.compile(r"^uls-joint-action-selector-v1-[0-9a-f]{20}$")
_FREEZE_SCHEMA = "uls-joint-action-full-validation-freeze-v1"
_SINGLE_FREEZE_SCHEMA = "uls-joint-action-single-validation-freeze-v1"
_RESULT_SCHEMA = "uls-joint-action-full-validation-result-v1"
_REQUEST_SCHEMA = "research-sweep-request-v1"
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_SWEEP_ID = "uls-joint-action-non-interruptible-full-validation-v1"
_SINGLE_SWEEP_ID = "uls-joint-action-non-interruptible-single-validation-v1"
_STRATEGY_COUNT = 300
_TP_PIPS = (200, 400, 600, 800)
_GROUP_SIZE = 1 + len(_TP_PIPS)
_EXPECTED_CHILDREN = _STRATEGY_COUNT * _GROUP_SIZE
_VALIDATION_DATASET = {
    "dataset_id": "XAUUSD_S_M1_VALIDATION_V1",
    "manifest_sha256": "5d122caa6a38c5bcf94ebc20895199d89c30f0f460fabd244865ad8829ebe6fd",
    "role": "validation",
}
_VALIDATION_BOUNDS = {
    "context_start": 1_704_157_200,
    "eval_start": 1_706_658_840,
    "eval_end": 1_735_689_360,
}
_SPLIT_POLICY = {
    "adr": "ADR-0012",
    "policy_version": "v1",
    "policy_sha256": "b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb",
}
_SUCCESS_RULE = {
    "primary": (
        "mean_dynamic_net_pnl_gt_mean_train_selected_fixed_tp_and_zero_with_"
        "median_dynamic_trades_gte_30"
    ),
    "minimum_median_dynamic_trades": 30,
    "unit": "one_uls_configuration",
    "configuration_count": _STRATEGY_COUNT,
    "validation_status": "reused_development_evidence_not_independent",
    "no_tuning_after_validation": True,
    "cost_semantics": "unverified",
}


def _validated_model_params(
    solver: dict[str, object], *, selection_threshold: float
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in ("n_estimators", "max_depth", "min_samples_leaf", "random_seed"):
        value = solver.get(name)
        if type(value) is not int:
            raise _ContractViolationError(f"joint solver {name} is malformed")
        result[name] = value
    bootstrap = solver.get("bootstrap")
    maximum = solver.get("max_features")
    if type(bootstrap) is not bool or type(maximum) is not float:
        raise _ContractViolationError("joint solver forest parameters are malformed")
    result["bootstrap"] = bootstrap
    result["max_features"] = _encoded_float(maximum)
    result["selection_threshold"] = _encoded_float(selection_threshold)
    return result


def _dynamic_policy() -> dict[str, object]:
    return {
        "name": "uls_joint_bracket_exposure_v1",
        "version": 1,
        "params": {
            "stop_loss_distance": _encoded_float(8.0),
            **{
                f"value_{index}_distance": _encoded_float(float(tp) / 100.0)
                for index, tp in enumerate(_TP_PIPS)
            },
        },
    }


def _fixed_policy() -> dict[str, object]:
    return {
        "name": "bracket_threshold_v1",
        "version": 1,
        "params": {
            "output_column": "baseline_score",
            "stop_loss_column": None,
            "take_profit_column": None,
            "threshold": _encoded_float(0.0),
        },
    }


def _extract_metrics(summary: dict[str, object]) -> dict[str, float]:
    entries = summary.get("entries")
    if type(entries) is not list or len(entries) != 2:
        raise _ContractViolationError("validation evaluation inventory differs")
    result: dict[str, float] = {}
    for raw_entry in entries:
        entry = _exact_mapping(raw_entry, label="validation evaluation entry")
        if entry.get("plane") != "B" or entry.get("cost_semantics_unverified") is not True:
            raise _ContractViolationError("validation metric is not unverified Plane B")
        raw_metrics = entry.get("metrics")
        if type(raw_metrics) is not list:
            raise _ContractViolationError("validation metric rows are malformed")
        for raw_metric in raw_metrics:
            metric = _exact_mapping(raw_metric, label="validation metric")
            name = metric.get("name")
            bits = metric.get("value_float64_bits")
            if (
                type(name) is not str
                or type(bits) is not str
                or _re.fullmatch(r"[0-9a-f]{16}", bits) is None
            ):
                raise _ContractViolationError("validation metric encoding is malformed")
            if name in result:
                raise _ContractViolationError("validation metric name is duplicated")
            result[name] = _struct.unpack(">d", bytes.fromhex(bits))[0]
    expected = {
        "trade_count",
        "gross_pnl",
        "total_cost",
        "net_pnl",
        "win_rate",
        "ambiguous_exit_rate",
        "gapped_exit_rate",
        "boundary_forced_close_rate",
        "max_drawdown",
    }
    if set(result) != expected:
        raise _ContractViolationError("validation metric inventory is incomplete")
    return result


def _encoded_float(value: float) -> dict[str, str]:
    if type(value) is not float:
        raise _ContractViolationError("encoded config value must be a Python float")
    return {"__float_hex__": value.hex()}


def _freeze_validation(*, bundle_dir: _Path, output_dir: _Path) -> dict[str, object]:
    for path, label in ((bundle_dir, "bundle_dir"), (output_dir, "output_dir")):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    if _BUNDLE_ID.fullmatch(bundle_dir.name) is None:
        raise _ContractViolationError("ULS joint bundle directory name is malformed")
    if output_dir.exists():
        raise _ContractViolationError("ULS joint full-validation freeze already exists")
    manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
    manifest = _read_bundle_manifest(bundle_dir)
    _verify_bundle_files(bundle_dir, manifest)
    reference = _bundle_reference_document(
        manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )
    source = _read_json(bundle_dir / "source-manifest.json")
    if source.get("schema") != _SOURCE_SCHEMA:
        raise _ContractViolationError("ULS joint source manifest schema differs")
    report = _read_json(bundle_dir / "training-report.json")
    solver = _exact_mapping(report.get("solver"), label="joint solver")
    qualification = _exact_mapping(
        report.get("qualification"), label="joint qualification"
    )
    best_fixed = qualification.get("train_selected_fixed_tp_pips")
    selection_threshold = qualification.get("selection_threshold")
    if (
        type(best_fixed) is not int
        or best_fixed not in _TP_PIPS
        or type(selection_threshold) is not float
        or selection_threshold < 0.0
    ):
        raise _ContractViolationError("ULS joint qualification is malformed")
    request = _validation_request(
        source=source,
        reference=reference,
        solver=solver,
        selection_threshold=selection_threshold,
    )
    expanded = _expand_request(request)
    if len(expanded) != _EXPECTED_CHILDREN:
        raise _ContractViolationError("ULS joint full-validation child count differs")
    _verify_pairing(expanded, source=source)
    request_bytes = _json_bytes(request)
    freeze: dict[str, object] = {
        "schema": _FREEZE_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "request_file": "request.json",
        "request_sha256": _sha256_bytes(request_bytes),
        "expected_children": _EXPECTED_CHILDREN,
        "configuration_count": _STRATEGY_COUNT,
        "children_per_configuration": _GROUP_SIZE,
        "child_order": ["model_joint_dynamic", *(f"fixed_tp_{tp}" for tp in _TP_PIPS)],
        "bundle_reference": reference,
        "bundle_platform": manifest["platform"],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"],
        "validation_dataset": _VALIDATION_DATASET,
        "validation_bounds": _VALIDATION_BOUNDS,
        "selection_threshold": selection_threshold,
        "train_selected_fixed_tp_pips": best_fixed,
        "success_rule": _SUCCESS_RULE,
        "mandatory_notice": (
            "VALIDATION was previously consumed; this is exploratory development evidence"
        ),
    }
    temporary = output_dir.with_name(f".{output_dir.name}.{_os.getpid():x}.tmp")
    if temporary.exists():
        raise _ContractViolationError("full-validation freeze temporary path exists")
    temporary.mkdir(parents=True, mode=0o755)
    try:
        _write_new(temporary / "request.json", request_bytes)
        _write_new(temporary / "freeze-manifest.json", _json_bytes(freeze))
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        _os.replace(temporary, output_dir)
    except BaseException:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        raise
    _verify_freeze(output_dir)
    return freeze


def _validation_request(
    *,
    source: dict[str, object],
    reference: dict[str, object],
    solver: dict[str, object],
    selection_threshold: float,
) -> dict[str, object]:
    configurations = _configurations(source)
    execution = dict(
        _exact_mapping(source.get("evaluation_execution"), label="ULS execution")
    )
    binding = _exact_mapping(execution.get("cost_binding"), label="ULS cost binding")
    if (
        execution.get("execution_id") != "bracket_non_interruptible_v1"
        or binding.get("cost_semantics") != "unverified"
    ):
        raise _ContractViolationError(
            "full validation requires non-interruptible unverified execution"
        )
    model_params = _validated_model_params(solver, selection_threshold=selection_threshold)
    feature_values: list[dict[str, object]] = []
    policy_values: list[dict[str, object]] = []
    take_profit_values: list[dict[str, str]] = []
    for configuration in configurations:
        params = _exact_mapping(configuration.get("params"), label="ULS feature params")
        feature_values.extend(dict(params) for _index in range(_GROUP_SIZE))
        policy_values.extend([_dynamic_policy(), *(_fixed_policy() for _tp in _TP_PIPS)])
        take_profit_values.extend(
            [_encoded_float(800.0), *(_encoded_float(float(tp)) for tp in _TP_PIPS)]
        )
    document: dict[str, object] = {
        "schema": "research-experiment-config-v2",
        "integrity_mode": "full",
        "dataset": _VALIDATION_DATASET,
        "split_policy": _SPLIT_POLICY,
        "bounds": _VALIDATION_BOUNDS,
        "features": [
            {
                "name": "uls_push_v7",
                "version": 1,
                "params": feature_values[0],
            },
            {"name": "uls_market_context_v1", "version": 1, "params": {}},
        ],
        "representation": None,
        "model": {
            "name": "uls_joint_action_forest_v1",
            "version": 1,
            "params": model_params,
        },
        "training_outcome": None,
        "evaluation_outcomes": [],
        "policies": [policy_values[0]],
        "evaluators": [],
        "execution": execution,
        "trade_evaluators": [
            {
                "name": "trade_summary_v1",
                "version": 1,
                "params": {"min_effective_trades": 1},
            },
            {
                "name": "equity_drawdown_v1",
                "version": 1,
                "params": {"min_effective_rows": 1},
            },
        ],
        "fit_mode": {"mode": "frozen_state_v1", **reference},
        "seed": None,
        "access_reason": None,
        "declared_lockbox_evaluation": False,
    }
    base = _config_from_document(document).document()
    return {
        "schema": _REQUEST_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "base_config": base,
        "axes": [
            {"json_pointer": "/features/0/params", "values": feature_values},
            {"json_pointer": "/policies/0", "values": policy_values},
            {
                "json_pointer": "/execution/take_profit_pips",
                "values": take_profit_values,
            },
        ],
        "combination": "zip",
    }


def _single_validation_request(
    *,
    source: dict[str, object],
    reference: dict[str, object],
    solver: dict[str, object],
    selection_threshold: float,
) -> dict[str, object]:
    deployment = _exact_mapping(source.get("deployment"), label="ULS deployment")
    strategy_ordinal = deployment.get("strategy_ordinal")
    if type(strategy_ordinal) is not int or not 0 <= strategy_ordinal < _STRATEGY_COUNT:
        raise _ContractViolationError("ULS deployment strategy ordinal is malformed")
    full = _validation_request(
        source=source,
        reference=reference,
        solver=solver,
        selection_threshold=selection_threshold,
    )
    selected_index = strategy_ordinal * _GROUP_SIZE
    axes = full.get("axes")
    if type(axes) is not list or len(axes) != 3:
        raise _ContractViolationError("ULS full-validation axes are malformed")
    selected_axes: list[dict[str, object]] = []
    for raw_axis in axes:
        axis = _exact_mapping(raw_axis, label="ULS full-validation axis")
        pointer = axis.get("json_pointer")
        values = axis.get("values")
        if (
            type(pointer) is not str
            or type(values) is not list
            or len(values) != _EXPECTED_CHILDREN
        ):
            raise _ContractViolationError("ULS full-validation axis values are malformed")
        selected_axes.append({"json_pointer": pointer, "values": [values[selected_index]]})
    request = {
        **full,
        "sweep_id": _SINGLE_SWEEP_ID,
        "axes": selected_axes,
    }
    expanded = _expand_request(request)
    if len(expanded) != 1:
        raise _ContractViolationError("ULS single validation must expand to one child")
    _verify_single_child(expanded[0][1], source=source, reference=reference)
    return request


def _verify_single_child(
    child: dict[str, object],
    *,
    source: dict[str, object],
    reference: dict[str, object],
) -> None:
    deployment = _exact_mapping(source.get("deployment"), label="ULS deployment")
    expected_params = _exact_mapping(
        deployment.get("feature_params"), label="ULS deployment feature params"
    )
    features = child.get("features")
    policies = child.get("policies")
    if type(features) is not list or len(features) != 2:
        raise _ContractViolationError("ULS single-validation feature inventory differs")
    if type(policies) is not list or len(policies) != 1:
        raise _ContractViolationError("ULS single-validation policy inventory differs")
    feature = _exact_mapping(features[0], label="ULS single-validation feature")
    policy = _exact_mapping(policies[0], label="ULS single-validation policy")
    model = _exact_mapping(child.get("model"), label="ULS single-validation model")
    execution = _exact_mapping(
        child.get("execution"), label="ULS single-validation execution"
    )
    fit_mode = _exact_mapping(child.get("fit_mode"), label="ULS frozen fit mode")
    if (
        _exact_mapping(child.get("dataset"), label="ULS single-validation dataset")
        != _VALIDATION_DATASET
        or feature.get("name") != "uls_push_v7"
        or feature.get("version") != 1
        or _canonical_document_json(feature.get("params"))
        != _canonical_document_json(expected_params)
        or policy.get("name") != "uls_joint_bracket_exposure_v1"
        or model.get("name") != "uls_joint_action_forest_v1"
        or execution.get("execution_id") != "bracket_non_interruptible_v1"
        or fit_mode != {"mode": "frozen_state_v1", **reference}
        or child.get("access_reason") is not None
        or child.get("declared_lockbox_evaluation") is not False
    ):
        raise _ContractViolationError("ULS single-validation child differs from frozen deployment")


def _freeze_single_validation(*, bundle_dir: _Path, output_dir: _Path) -> dict[str, object]:
    for path, label in ((bundle_dir, "bundle_dir"), (output_dir, "output_dir")):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    if _BUNDLE_ID.fullmatch(bundle_dir.name) is None:
        raise _ContractViolationError("ULS joint bundle directory name is malformed")
    if output_dir.exists():
        raise _ContractViolationError("ULS joint single-validation freeze already exists")
    manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
    manifest = _read_bundle_manifest(bundle_dir)
    _verify_bundle_files(bundle_dir, manifest)
    reference = _bundle_reference_document(
        manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )
    source = _read_json(bundle_dir / "source-manifest.json")
    if source.get("schema") != _SOURCE_SCHEMA:
        raise _ContractViolationError("ULS joint source manifest schema differs")
    report = _read_json(bundle_dir / "training-report.json")
    solver = _exact_mapping(report.get("solver"), label="joint solver")
    qualification = _exact_mapping(
        report.get("qualification"), label="joint qualification"
    )
    selection_threshold = qualification.get("selection_threshold")
    if type(selection_threshold) is not float or selection_threshold < 0.0:
        raise _ContractViolationError("ULS joint selection threshold is malformed")
    request = _single_validation_request(
        source=source,
        reference=reference,
        solver=solver,
        selection_threshold=selection_threshold,
    )
    deployment = _exact_mapping(source.get("deployment"), label="ULS deployment")
    request_bytes = _json_bytes(request)
    freeze: dict[str, object] = {
        "schema": _SINGLE_FREEZE_SCHEMA,
        "sweep_id": _SINGLE_SWEEP_ID,
        "request_file": "request.json",
        "request_sha256": _sha256_bytes(request_bytes),
        "expected_children": 1,
        "deployment_strategy_ordinal": deployment["strategy_ordinal"],
        "bundle_reference": reference,
        "bundle_platform": manifest["platform"],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"],
        "validation_dataset": _VALIDATION_DATASET,
        "validation_bounds": _VALIDATION_BOUNDS,
        "selection_threshold": selection_threshold,
        "model_action_space": ["no_entry", *[f"tp_{tp}" for tp in _TP_PIPS]],
        "mandatory_notice": (
            "VALIDATION was previously consumed; this is exploratory development evidence"
        ),
    }
    temporary = output_dir.with_name(f".{output_dir.name}.{_os.getpid():x}.tmp")
    if temporary.exists():
        raise _ContractViolationError("single-validation freeze temporary path exists")
    temporary.mkdir(parents=True, mode=0o755)
    try:
        _write_new(temporary / "request.json", request_bytes)
        _write_new(temporary / "freeze-manifest.json", _json_bytes(freeze))
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        _os.replace(temporary, output_dir)
    except BaseException:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        raise
    _verify_single_freeze(output_dir)
    return freeze


def _verify_single_freeze(directory: _Path) -> dict[str, object]:
    freeze = _read_json(directory / "freeze-manifest.json")
    if (
        freeze.get("schema") != _SINGLE_FREEZE_SCHEMA
        or freeze.get("sweep_id") != _SINGLE_SWEEP_ID
        or freeze.get("expected_children") != 1
    ):
        raise _ContractViolationError("ULS joint single-validation freeze is malformed")
    if {path.name for path in directory.iterdir() if path.is_file()} != {
        "freeze-manifest.json",
        "request.json",
    }:
        raise _ContractViolationError("ULS joint single-validation freeze files differ")
    request_path = directory / "request.json"
    if _sha256_file(request_path) != freeze.get("request_sha256"):
        raise _ContractViolationError("ULS joint single-validation request hash differs")
    request = _read_json(request_path)
    expanded = _expand_request(request)
    reference = _exact_mapping(freeze["bundle_reference"], label="bundle reference")
    if len(expanded) != 1:
        raise _ContractViolationError("ULS joint frozen single child count differs")
    child = expanded[0][1]
    fit_mode = _exact_mapping(child.get("fit_mode"), label="frozen fit mode")
    if fit_mode != {"mode": "frozen_state_v1", **reference}:
        raise _ContractViolationError("ULS joint frozen-state reference differs")
    return freeze


def _configurations(source: dict[str, object]) -> list[dict[str, object]]:
    raw = source.get("configurations")
    if type(raw) is not list or len(raw) != _STRATEGY_COUNT:
        raise _ContractViolationError("ULS source configuration inventory differs")
    configurations = [
        dict(_exact_mapping(item, label="ULS source configuration")) for item in raw
    ]
    if [item.get("strategy_ordinal") for item in configurations] != list(
        range(_STRATEGY_COUNT)
    ):
        raise _ContractViolationError("ULS source configuration order differs")
    return configurations


def _verify_pairing(
    expanded: tuple[tuple[int, dict[str, object]], ...],
    *,
    source: dict[str, object],
) -> None:
    configurations = _configurations(source)
    if len(expanded) != _EXPECTED_CHILDREN:
        raise _ContractViolationError("full-validation expansion length differs")
    for ordinal, child in expanded:
        strategy_ordinal, within = divmod(ordinal, _GROUP_SIZE)
        features = child.get("features")
        policies = child.get("policies")
        execution = _exact_mapping(child.get("execution"), label="validation execution")
        if type(features) is not list or len(features) != 2:
            raise _ContractViolationError("validation feature inventory differs")
        if type(policies) is not list or len(policies) != 1:
            raise _ContractViolationError("validation policy inventory differs")
        feature = _exact_mapping(features[0], label="validation ULS feature")
        policy = _exact_mapping(policies[0], label="validation policy")
        expected_params = _exact_mapping(
            configurations[strategy_ordinal].get("params"), label="source ULS params"
        )
        expected_name = (
            "uls_joint_bracket_exposure_v1" if within == 0 else "bracket_threshold_v1"
        )
        expected_tp = 800 if within == 0 else _TP_PIPS[within - 1]
        raw_tp = execution.get("take_profit_pips")
        decoded_tp = (
            float.fromhex(raw_tp["__float_hex__"])
            if type(raw_tp) is dict and type(raw_tp.get("__float_hex__")) is str
            else None
        )
        if (
            feature.get("name") != "uls_push_v7"
            or feature.get("version") != 1
            or _canonical_document_json(feature.get("params"))
            != _canonical_document_json(expected_params)
            or policy.get("name") != expected_name
            or decoded_tp != float(expected_tp)
            or execution.get("execution_id") != "bracket_non_interruptible_v1"
        ):
            raise _ContractViolationError("ULS full-validation child pairing differs")


def _verify_freeze(directory: _Path) -> dict[str, object]:
    freeze = _read_json(directory / "freeze-manifest.json")
    if (
        freeze.get("schema") != _FREEZE_SCHEMA
        or freeze.get("sweep_id") != _SWEEP_ID
        or freeze.get("expected_children") != _EXPECTED_CHILDREN
    ):
        raise _ContractViolationError("ULS joint full-validation freeze is malformed")
    if {path.name for path in directory.iterdir() if path.is_file()} != {
        "freeze-manifest.json",
        "request.json",
    }:
        raise _ContractViolationError("ULS joint full-validation freeze files differ")
    request_path = directory / "request.json"
    if _sha256_file(request_path) != freeze.get("request_sha256"):
        raise _ContractViolationError("ULS joint full-validation request hash differs")
    request = _read_json(request_path)
    expanded = _expand_request(request)
    reference = _exact_mapping(freeze["bundle_reference"], label="bundle reference")
    if len(expanded) != _EXPECTED_CHILDREN:
        raise _ContractViolationError("ULS joint frozen child count differs")
    for _ordinal, child in expanded:
        fit_mode = _exact_mapping(child.get("fit_mode"), label="frozen fit mode")
        if fit_mode != {"mode": "frozen_state_v1", **reference}:
            raise _ContractViolationError("ULS joint frozen-state reference differs")
    return freeze


def _report_validation(
    *, runs_root: _Path, freeze_dir: _Path, output_path: _Path | None = None
) -> dict[str, object]:
    if not isinstance(runs_root, _Path) or not runs_root.is_absolute():
        raise _ContractViolationError("runs_root must be an absolute Path")
    freeze = _verify_freeze(freeze_dir)
    request = _read_json(freeze_dir / "request.json")
    plan = _read_sweep_plan(runs_root, _SWEEP_ID)
    if len(plan._children) != _EXPECTED_CHILDREN:
        raise _ContractViolationError("planned ULS full-validation child count differs")
    for field in ("base_config", "axes", "combination"):
        if _canonical_document_json(plan._document.get(field)) != _canonical_document_json(
            request.get(field)
        ):
            raise _ContractViolationError("planned full-validation request differs from freeze")
    expected_platform = _exact_mapping(freeze["bundle_platform"], label="bundle platform")
    if _exact_mapping(plan._document.get("platform"), label="plan platform") != expected_platform:
        raise _ContractViolationError("ULS full-validation platform differs from bundle")
    if plan._document.get("dependency_lock_sha256") != freeze.get(
        "dependency_lock_sha256"
    ):
        raise _ContractViolationError("ULS full-validation dependency differs from bundle")
    expected = {child._experiment_fingerprint: child for child in plan._children}
    attempts: dict[str, list[_RunProjection]] = {fingerprint: [] for fingerprint in expected}
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=60_000_000_000,
        reconcile=False,
        tolerate_corruption=False,
    )
    if scan._errors:
        raise _ContractViolationError("authoritative full-validation scan reported corruption")
    for projection in scan._projections:
        fingerprint = projection._record._experiment_fingerprint
        if fingerprint in attempts:
            attempts[fingerprint].append(projection)
    rows: list[dict[str, object]] = []
    for ordinal, child in enumerate(plan._children):
        candidates = [
            projection
            for projection in attempts[child._experiment_fingerprint]
            if projection._record._state is _RunState.COMPLETE
            and projection._record._canonical is True
        ]
        if not candidates:
            raise _ContractViolationError(f"validation child {ordinal} is not COMPLETE")
        projection = min(candidates, key=lambda item: item._record._run_id)
        manifest = projection._manifest
        if manifest is None:
            raise _ContractViolationError("validation COMPLETE child lacks manifest")
        digest = dict(manifest.metric_hashes).get("evaluation-summary.json")
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise _ContractViolationError("validation metric artifact hash is missing")
        metric_path = runs_root / "runs" / manifest.run_id / "metrics/evaluation-summary.json"
        _verify_file_hash(metric_path, digest)
        strategy_ordinal, within = divmod(ordinal, _GROUP_SIZE)
        rows.append(
            {
                "ordinal": ordinal,
                "strategy_ordinal": strategy_ordinal,
                "label": (
                    "model_joint_dynamic" if within == 0 else f"fixed_tp_{_TP_PIPS[within - 1]}"
                ),
                "tp_pips": None if within == 0 else _TP_PIPS[within - 1],
                "run_id": manifest.run_id,
                "experiment_fingerprint": manifest.experiment_fingerprint,
                "metrics": _serializable_metrics(_read_json(metric_path)),
            }
        )
    result = _result_document(rows=rows, freeze=freeze, freeze_dir=freeze_dir)
    if output_path is not None:
        if not output_path.is_absolute():
            raise _ContractViolationError("output_path must be absolute")
        _write_new(output_path, _json_bytes(result))
    return result


def _serializable_metrics(summary: dict[str, object]) -> dict[str, float | None]:
    """Represent evaluator-undefined non-finite rates as JSON null."""
    raw = _extract_metrics(summary)
    if raw["trade_count"] == 0.0:
        for name in ("gross_pnl", "total_cost", "net_pnl", "max_drawdown"):
            if not _np.isfinite(raw[name]):
                raw[name] = 0.0
    return {
        name: value if _np.isfinite(value) else None
        for name, value in raw.items()
    }


def _result_document(
    *, rows: list[dict[str, object]], freeze: dict[str, object], freeze_dir: _Path
) -> dict[str, object]:
    matrix = [rows[index : index + _GROUP_SIZE] for index in range(0, len(rows), _GROUP_SIZE)]
    metric_names = ("trade_count", "gross_pnl", "total_cost", "net_pnl", "max_drawdown")
    summaries: list[dict[str, object]] = []
    for within, label in enumerate(("model_joint_dynamic", *(f"fixed_tp_{tp}" for tp in _TP_PIPS))):
        metrics = [
            _typing.cast("dict[str, float | None]", group[within]["metrics"])
            for group in matrix
        ]
        summaries.append(
            {
                "label": label,
                "mean": {
                    name: _aggregate_metric(metrics, name=name, median=False)
                    for name in metric_names
                },
                "median": {
                    name: _aggregate_metric(metrics, name=name, median=True)
                    for name in metric_names
                },
                "undefined_configurations": {
                    name: sum(item[name] is None for item in metrics) for name in metric_names
                },
                "net_positive_configurations": sum(
                    _required_metric(item, name="net_pnl") > 0.0 for item in metrics
                ),
            }
        )
    dynamic_mean = _typing.cast("dict[str, float | None]", summaries[0]["mean"])
    dynamic_median = _typing.cast("dict[str, float | None]", summaries[0]["median"])
    selected_tp = freeze.get("train_selected_fixed_tp_pips")
    if type(selected_tp) is not int or selected_tp not in _TP_PIPS:
        raise _ContractViolationError("TRAIN-selected fixed TP is malformed")
    baseline_index = _TP_PIPS.index(selected_tp) + 1
    baseline_mean = _typing.cast(
        "dict[str, float | None]", summaries[baseline_index]["mean"]
    )
    paired: dict[str, object] = {}
    for arm_index, tp in enumerate(_TP_PIPS, start=1):
        differences = []
        for group in matrix:
            dynamic_metrics = _typing.cast(
                "dict[str, float | None]", group[0]["metrics"]
            )
            fixed_metrics = _typing.cast(
                "dict[str, float | None]", group[arm_index]["metrics"]
            )
            differences.append(
                _required_metric(dynamic_metrics, name="net_pnl")
                - _required_metric(fixed_metrics, name="net_pnl")
            )
        paired[str(tp)] = {
            "wins": sum(value > 0.0 for value in differences),
            "losses": sum(value < 0.0 for value in differences),
            "ties": sum(value == 0.0 for value in differences),
            "mean_net_pnl_difference": float(_np.mean(differences)),
            "median_net_pnl_difference": float(_np.median(differences)),
        }
    return {
        "schema": _RESULT_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "freeze_manifest_sha256": _sha256_file(freeze_dir / "freeze-manifest.json"),
        "bundle_reference": freeze["bundle_reference"],
        "validation_dataset": freeze["validation_dataset"],
        "children": rows,
        "aggregate": {
            "configuration_count": _STRATEGY_COUNT,
            "summaries": summaries,
            "paired_dynamic_minus_fixed": paired,
            "train_selected_fixed_tp_pips": selected_tp,
            "primary_success": _required_metric(dynamic_mean, name="net_pnl")
            > _required_metric(baseline_mean, name="net_pnl")
            and _required_metric(dynamic_mean, name="net_pnl") > 0.0
            and _required_metric(dynamic_median, name="trade_count") >= 30.0,
            "success_rule": freeze["success_rule"],
        },
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "not_a_profitability_claim": True,
            "validation_is_reused_development_evidence": True,
            "not_independent_validation": True,
            "configurations_are_related_hypotheses": True,
        },
    }


def _aggregate_metric(
    metrics: list[dict[str, float | None]], *, name: str, median: bool
) -> float | None:
    values: list[float] = []
    for item in metrics:
        value = item[name]
        if value is not None:
            values.append(value)
    if not values:
        return None
    return float(_np.median(values) if median else _np.mean(values))


def _required_metric(metrics: dict[str, float | None], *, name: str) -> float:
    value = metrics.get(name)
    if type(value) is not float or not _np.isfinite(value):
        raise _ContractViolationError(f"required validation metric {name} is undefined")
    return value


def _absolute_path(value: str) -> _Path:
    if type(value) is not str or not value:
        raise _argparse.ArgumentTypeError("path must be non-empty")
    return _Path(value).expanduser().resolve()


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="phase8-joint-full-validation")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--bundle-dir", required=True, type=_absolute_path)
    freeze.add_argument("--output-dir", required=True, type=_absolute_path)
    single = commands.add_parser("freeze-single")
    single.add_argument("--bundle-dir", required=True, type=_absolute_path)
    single.add_argument("--output-dir", required=True, type=_absolute_path)
    report = commands.add_parser("report")
    report.add_argument("--runs-root", required=True, type=_absolute_path)
    report.add_argument("--freeze-dir", required=True, type=_absolute_path)
    report.add_argument("--output", type=_absolute_path)
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            result = _freeze_validation(bundle_dir=args.bundle_dir, output_dir=args.output_dir)
        elif args.command == "freeze-single":
            result = _freeze_single_validation(
                bundle_dir=args.bundle_dir,
                output_dir=args.output_dir,
            )
        else:
            result = _report_validation(
                runs_root=args.runs_root,
                freeze_dir=args.freeze_dir,
                output_path=args.output,
            )
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2
    _sys.stdout.write(_canonical_document_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
