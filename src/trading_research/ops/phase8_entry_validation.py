"""Freeze and report the two-child ULS entry-gate VALIDATION comparison."""

from __future__ import annotations

import argparse as _argparse
import os as _os
import re as _re
import struct as _struct
import sys as _sys
import time as _time
import typing as _typing
from pathlib import Path as _Path

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
from trading_research.experiments.uls_entry_bundles import (
    _MODEL_BASE_PARAMS,
    _SOURCE_SCHEMA,
)

if _typing.TYPE_CHECKING:
    from trading_research.experiments.runs import _RunProjection

__all__: list[str] = []

_FREEZE_SCHEMA = "uls-entry-validation-freeze-v1"
_RESULT_SCHEMA = "uls-entry-validation-result-v1"
_REQUEST_SCHEMA = "research-sweep-request-v1"
_BUNDLE_ID = _re.compile(r"^uls-entry-selector-v1-[0-9a-f]{20}$")
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_SWEEP_ID = "uls-entry-gate-validation-v1"
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
_OUTPUT_COLUMNS = ("score", "value_0")
_LABELS = ("model_gate", "fixed_best_baseline")
_SUCCESS_RULE = {
    "primary": "model_gate_total_net_pnl_gt_fixed_best_baseline_total_net_pnl",
    "secondary": [
        "gross_pnl",
        "total_cost",
        "trade_count",
        "win_rate",
        "max_drawdown",
    ],
    "minimum_model_trades": 30,
    "no_tuning_after_validation": True,
    "cost_semantics": "unverified",
}


def _freeze_validation(*, bundle_dir: _Path, output_dir: _Path) -> dict[str, object]:
    for path, label in ((bundle_dir, "bundle_dir"), (output_dir, "output_dir")):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    if _BUNDLE_ID.fullmatch(bundle_dir.name) is None:
        raise _ContractViolationError("ULS entry bundle directory name is malformed")
    if output_dir.exists():
        raise _ContractViolationError("ULS entry validation freeze already exists")
    manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
    manifest = _read_bundle_manifest(bundle_dir)
    _verify_bundle_files(bundle_dir, manifest)
    reference = _bundle_reference_document(
        manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )
    source = _read_json(bundle_dir / "source-manifest.json")
    if source.get("schema") != _SOURCE_SCHEMA:
        raise _ContractViolationError("ULS entry source manifest schema differs")
    report = _read_json(bundle_dir / "training-report.json")
    threshold = report.get("selection_threshold")
    if type(threshold) is not float:
        raise _ContractViolationError("ULS entry selection threshold is malformed")
    request = _validation_request(
        source=source,
        reference=reference,
        selection_threshold=threshold,
    )
    expanded = _expand_request(request)
    if len(expanded) != 2:
        raise _ContractViolationError("ULS entry validation request is not two children")
    observed = [_first_policy(child)["params"] for _ordinal, child in expanded]
    if [
        _exact_mapping(item, label="validation policy params").get("output_column")
        for item in observed
    ] != list(_OUTPUT_COLUMNS):
        raise _ContractViolationError("ULS entry validation pairing differs")
    request_bytes = _json_bytes(request)
    freeze: dict[str, object] = {
        "schema": _FREEZE_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "request_file": "request.json",
        "request_sha256": _sha256_bytes(request_bytes),
        "expected_children": 2,
        "child_labels": [
            {"ordinal": ordinal, "label": label, "output_column": output}
            for ordinal, (label, output) in enumerate(zip(_LABELS, _OUTPUT_COLUMNS, strict=True))
        ],
        "bundle_reference": reference,
        "bundle_platform": manifest["platform"],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"],
        "validation_dataset": _VALIDATION_DATASET,
        "validation_bounds": _VALIDATION_BOUNDS,
        "selection_threshold": threshold,
        "success_rule": _SUCCESS_RULE,
    }
    temporary = output_dir.with_name(f".{output_dir.name}.{_os.getpid():x}.tmp")
    if temporary.exists():
        raise _ContractViolationError("validation freeze temporary path exists")
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
    selection_threshold: float,
) -> dict[str, object]:
    deployment = _exact_mapping(source.get("deployment"), label="ULS deployment")
    params = _exact_mapping(deployment.get("feature_params"), label="ULS deployment params")
    execution = _exact_mapping(source.get("evaluation_execution"), label="ULS execution")
    binding = _exact_mapping(execution.get("cost_binding"), label="ULS cost binding")
    if binding.get("cost_semantics") != "unverified":
        raise _ContractViolationError("ULS validation costs must remain unverified")
    model_params = {
        **_MODEL_BASE_PARAMS,
        "max_features": _encoded_float(_typing.cast("float", _MODEL_BASE_PARAMS["max_features"])),
        "selection_threshold": _encoded_float(selection_threshold),
    }
    document: dict[str, object] = {
        "schema": "research-experiment-config-v2",
        "integrity_mode": "full",
        "dataset": _VALIDATION_DATASET,
        "split_policy": _SPLIT_POLICY,
        "bounds": _VALIDATION_BOUNDS,
        "features": [
            {"name": "uls_push_v7", "version": 1, "params": params},
            {"name": "uls_market_context_v1", "version": 1, "params": {}},
        ],
        "representation": None,
        "model": {
            "name": "uls_entry_forest_v1",
            "version": 1,
            "params": model_params,
        },
        "training_outcome": None,
        "evaluation_outcomes": [],
        "policies": [
            {
                "name": "threshold_v1",
                "version": 1,
                "params": {
                    "output_column": _OUTPUT_COLUMNS[0],
                    "threshold": _encoded_float(0.0),
                },
            }
        ],
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
            {
                "json_pointer": "/policies/0/params/output_column",
                "values": list(_OUTPUT_COLUMNS),
            }
        ],
        "combination": "zip",
    }


def _verify_freeze(directory: _Path) -> dict[str, object]:
    freeze = _read_json(directory / "freeze-manifest.json")
    if (
        freeze.get("schema") != _FREEZE_SCHEMA
        or freeze.get("sweep_id") != _SWEEP_ID
        or freeze.get("expected_children") != 2
    ):
        raise _ContractViolationError("ULS entry validation freeze is malformed")
    if {path.name for path in directory.iterdir() if path.is_file()} != {
        "freeze-manifest.json",
        "request.json",
    }:
        raise _ContractViolationError("ULS entry validation freeze files differ")
    request_path = directory / "request.json"
    if _sha256_file(request_path) != freeze.get("request_sha256"):
        raise _ContractViolationError("ULS entry validation request hash differs")
    request = _read_json(request_path)
    expanded = _expand_request(request)
    if len(expanded) != 2:
        raise _ContractViolationError("ULS entry frozen validation child count differs")
    output_columns = [
        _exact_mapping(_first_policy(child).get("params"), label="frozen policy params").get(
            "output_column"
        )
        for _ordinal, child in expanded
    ]
    if output_columns != list(_OUTPUT_COLUMNS):
        raise _ContractViolationError("ULS entry frozen validation pairing differs")
    frozen_reference = _exact_mapping(freeze["bundle_reference"], label="bundle reference")
    for _ordinal, child in expanded:
        fit_mode = _exact_mapping(child.get("fit_mode"), label="frozen fit mode")
        if fit_mode != {"mode": "frozen_state_v1", **frozen_reference}:
            raise _ContractViolationError("ULS entry frozen-state reference differs")
    return freeze


def _encoded_float(value: float) -> dict[str, str]:
    if type(value) is not float:
        raise _ContractViolationError("encoded config value must be a Python float")
    return {"__float_hex__": value.hex()}


def _report_validation(
    *,
    runs_root: _Path,
    freeze_dir: _Path,
    output_path: _Path | None = None,
) -> dict[str, object]:
    if not isinstance(runs_root, _Path) or not runs_root.is_absolute():
        raise _ContractViolationError("runs_root must be an absolute Path")
    freeze = _verify_freeze(freeze_dir)
    request = _read_json(freeze_dir / "request.json")
    plan = _read_sweep_plan(runs_root, _SWEEP_ID)
    if len(plan._children) != 2:
        raise _ContractViolationError("planned ULS validation sweep is not two children")
    for field in ("base_config", "axes", "combination"):
        if _canonical_document_json(plan._document.get(field)) != _canonical_document_json(
            request.get(field)
        ):
            raise _ContractViolationError("planned ULS validation request differs from freeze")
    expected_platform = _exact_mapping(freeze["bundle_platform"], label="bundle platform")
    if _exact_mapping(plan._document.get("platform"), label="plan platform") != expected_platform:
        raise _ContractViolationError("validation execution platform differs from bundle freeze")
    if plan._document.get("dependency_lock_sha256") != freeze.get("dependency_lock_sha256"):
        raise _ContractViolationError("validation dependency differs from bundle freeze")

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
        raise _ContractViolationError("authoritative validation-run scan reported corruption")
    for projection in scan._projections:
        fingerprint = projection._record._experiment_fingerprint
        if fingerprint in attempts:
            attempts[fingerprint].append(projection)
    rows: list[dict[str, object]] = []
    for ordinal, (label, output_column) in enumerate(zip(_LABELS, _OUTPUT_COLUMNS, strict=True)):
        child = plan._children[ordinal]
        candidates = [
            projection
            for projection in attempts[child._experiment_fingerprint]
            if projection._record._state is _RunState.COMPLETE
            and projection._record._canonical is True
        ]
        if not candidates:
            raise _ContractViolationError(f"validation child {label} is not canonical COMPLETE")
        projection = min(candidates, key=lambda item: item._record._run_id)
        manifest = projection._manifest
        config = projection._resolved_config
        if manifest is None:
            raise _ContractViolationError("validation COMPLETE child lacks manifest")
        policy = _first_policy(config)
        params = _exact_mapping(policy.get("params"), label="validation result policy params")
        if params.get("output_column") != output_column:
            raise _ContractViolationError("validation result policy pairing differs")
        metric_hashes = dict(manifest.metric_hashes)
        digest = metric_hashes.get("evaluation-summary.json")
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise _ContractViolationError("validation metric artifact hash is missing")
        metric_path = runs_root / "runs" / manifest.run_id / "metrics" / "evaluation-summary.json"
        _verify_file_hash(metric_path, digest)
        metrics = _extract_metrics(_read_json(metric_path))
        rows.append(
            {
                "ordinal": ordinal,
                "label": label,
                "output_column": output_column,
                "run_id": manifest.run_id,
                "experiment_fingerprint": manifest.experiment_fingerprint,
                "metrics": metrics,
            }
        )
    model_metrics = _typing.cast("dict[str, float]", rows[0]["metrics"])
    baseline_metrics = _typing.cast("dict[str, float]", rows[1]["metrics"])
    primary = (
        model_metrics["net_pnl"] > baseline_metrics["net_pnl"]
        and model_metrics["trade_count"] >= 30.0
    )
    result: dict[str, object] = {
        "schema": _RESULT_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "freeze_manifest_sha256": _sha256_file(freeze_dir / "freeze-manifest.json"),
        "bundle_reference": freeze["bundle_reference"],
        "validation_dataset": freeze["validation_dataset"],
        "selection_threshold": freeze["selection_threshold"],
        "children": rows,
        "comparison": {
            "model_minus_baseline": {
                name: model_metrics[name] - baseline_metrics[name]
                for name in (
                    "trade_count",
                    "gross_pnl",
                    "total_cost",
                    "net_pnl",
                    "win_rate",
                    "max_drawdown",
                )
            },
            "primary_success": primary,
            "success_rule": freeze["success_rule"],
        },
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "not_a_profitability_claim": True,
            "validation_used_once_after_freeze": True,
        },
    }
    if output_path is not None:
        if not output_path.is_absolute():
            raise _ContractViolationError("output_path must be absolute")
        _write_new(output_path, _json_bytes(result))
    return result


def _first_policy(config: dict[str, object]) -> dict[str, object]:
    policies = config.get("policies")
    if type(policies) is not list or len(policies) != 1:
        raise _ContractViolationError("validation config must contain one policy")
    return _exact_mapping(policies[0], label="validation policy")


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


def _absolute_path(value: str) -> _Path:
    if type(value) is not str or not value:
        raise _argparse.ArgumentTypeError("path must be non-empty")
    return _Path(value).expanduser().resolve()


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="phase8-entry-validation")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--bundle-dir", required=True, type=_absolute_path)
    freeze.add_argument("--output-dir", required=True, type=_absolute_path)
    report = commands.add_parser("report")
    report.add_argument("--runs-root", required=True, type=_absolute_path)
    report.add_argument("--freeze-dir", required=True, type=_absolute_path)
    report.add_argument("--output", type=_absolute_path)
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            _freeze_validation(bundle_dir=args.bundle_dir, output_dir=args.output_dir)
            if args.command == "freeze"
            else _report_validation(
                runs_root=args.runs_root,
                freeze_dir=args.freeze_dir,
                output_path=args.output,
            )
        )
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2
    _sys.stdout.write(_canonical_document_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
