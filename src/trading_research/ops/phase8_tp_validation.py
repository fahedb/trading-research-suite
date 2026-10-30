"""Freeze and report the reused-VALIDATION ULS TP-selector comparison."""

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
from trading_research.experiments.uls_tp_bundles import (
    _SELECTOR_PARAMS,
    _SOURCE_SCHEMA,
)

if _typing.TYPE_CHECKING:
    from trading_research.experiments.runs import _RunProjection

__all__: list[str] = []

_BUNDLE_ID = _re.compile(r"^uls-entry-tp-selector-v1-[0-9a-f]{20}$")
_FREEZE_SCHEMA = "uls-entry-tp-validation-freeze-v1"
_REQUEST_SCHEMA = "research-sweep-request-v1"
_RESULT_SCHEMA = "uls-entry-tp-validation-result-v1"
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_SWEEP_ID = "uls-entry-tp-validation-v1"
_TP_PIPS = (200, 300, 400, 500, 600, 700, 800)
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
_LABELS = ("model_dynamic_tp", *(f"fixed_tp_{value}" for value in _TP_PIPS))
_SUCCESS_RULE = {
    "primary": "dynamic_net_pnl_gt_train_selected_fixed_tp_net_pnl",
    "minimum_dynamic_trades": 30,
    "secondary": [
        "gross_pnl",
        "total_cost",
        "trade_count",
        "win_rate",
        "max_drawdown",
    ],
    "validation_status": "reused_development_evidence_not_independent",
    "no_tuning_after_validation": True,
    "cost_semantics": "unverified",
}


def _freeze_validation(*, bundle_dir: _Path, output_dir: _Path) -> dict[str, object]:
    for path, label in ((bundle_dir, "bundle_dir"), (output_dir, "output_dir")):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    if _BUNDLE_ID.fullmatch(bundle_dir.name) is None:
        raise _ContractViolationError("ULS TP bundle directory name is malformed")
    if output_dir.exists():
        raise _ContractViolationError("ULS TP validation freeze already exists")
    manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
    manifest = _read_bundle_manifest(bundle_dir)
    _verify_bundle_files(bundle_dir, manifest)
    reference = _bundle_reference_document(
        manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )
    source = _read_json(bundle_dir / "source-manifest.json")
    if source.get("schema") != _SOURCE_SCHEMA:
        raise _ContractViolationError("ULS TP source manifest schema differs")
    entry_source = _entry_source(bundle_dir=bundle_dir, source=source)
    report = _read_json(bundle_dir / "training-report.json")
    threshold = report.get("solver")
    qualification = _exact_mapping(report.get("qualification"), label="TP qualification")
    best_fixed = qualification.get("best_fixed_tp_pips")
    if type(threshold) is not dict or type(best_fixed) is not int or best_fixed not in _TP_PIPS:
        raise _ContractViolationError("ULS TP training report is malformed")
    selection_threshold = threshold.get("selection_threshold")
    if type(selection_threshold) is not float:
        raise _ContractViolationError("ULS TP selection threshold is malformed")
    request = _validation_request(
        entry_source=entry_source,
        reference=reference,
        selection_threshold=selection_threshold,
    )
    expanded = _expand_request(request)
    if len(expanded) != len(_LABELS):
        raise _ContractViolationError("ULS TP validation request child count differs")
    _verify_pairing(expanded)
    request_bytes = _json_bytes(request)
    freeze: dict[str, object] = {
        "schema": _FREEZE_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "request_file": "request.json",
        "request_sha256": _sha256_bytes(request_bytes),
        "expected_children": len(_LABELS),
        "child_labels": [
            {"ordinal": ordinal, "label": label}
            for ordinal, label in enumerate(_LABELS)
        ],
        "bundle_reference": reference,
        "bundle_platform": manifest["platform"],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"],
        "validation_dataset": _VALIDATION_DATASET,
        "validation_bounds": _VALIDATION_BOUNDS,
        "selection_threshold": selection_threshold,
        "train_selected_fixed_tp_pips": best_fixed,
        "success_rule": _SUCCESS_RULE,
        "mandatory_notice": "VALIDATION was previously consumed; this is development evidence",
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


def _entry_source(*, bundle_dir: _Path, source: dict[str, object]) -> dict[str, object]:
    oof = _exact_mapping(source.get("oof_freeze"), label="OOF freeze")
    reference = _exact_mapping(oof.get("entry_source_bundle"), label="entry source reference")
    bundle_id = reference.get("bundle_id")
    if type(bundle_id) is not str:
        raise _ContractViolationError("entry source bundle id is malformed")
    upstream = bundle_dir.parent / bundle_id
    upstream_manifest = _read_bundle_manifest(upstream)
    _verify_bundle_files(upstream, upstream_manifest)
    if _bundle_reference_document(
        upstream_manifest,
        manifest_sha256=_sha256_file(upstream / "manifest.json"),
    ) != reference:
        raise _ContractViolationError("entry source bundle reference differs")
    return _read_json(upstream / "source-manifest.json")


def _validation_request(
    *,
    entry_source: dict[str, object],
    reference: dict[str, object],
    selection_threshold: float,
) -> dict[str, object]:
    deployment = _exact_mapping(entry_source.get("deployment"), label="ULS deployment")
    feature_params = _exact_mapping(
        deployment.get("feature_params"), label="ULS deployment params"
    )
    execution = dict(
        _exact_mapping(entry_source.get("evaluation_execution"), label="ULS execution")
    )
    binding = _exact_mapping(execution.get("cost_binding"), label="ULS cost binding")
    if binding.get("cost_semantics") != "unverified":
        raise _ContractViolationError("ULS TP validation costs must remain unverified")
    model_params = {
        **_SELECTOR_PARAMS,
        "entry_max_features": _encoded_float(
            _typing.cast("float", _SELECTOR_PARAMS["entry_max_features"])
        ),
        "selector_max_features": _encoded_float(
            _typing.cast("float", _SELECTOR_PARAMS["selector_max_features"])
        ),
        "selection_threshold": _encoded_float(selection_threshold),
    }
    policies = [_dynamic_policy(), *(_fixed_policy() for _value in _TP_PIPS)]
    take_profits = [_encoded_float(800.0), *(_encoded_float(float(value)) for value in _TP_PIPS)]
    document: dict[str, object] = {
        "schema": "research-experiment-config-v2",
        "integrity_mode": "full",
        "dataset": _VALIDATION_DATASET,
        "split_policy": _SPLIT_POLICY,
        "bounds": _VALIDATION_BOUNDS,
        "features": [
            {"name": "uls_push_v7", "version": 1, "params": feature_params},
            {"name": "uls_market_context_v1", "version": 1, "params": {}},
        ],
        "representation": None,
        "model": {"name": "uls_entry_tp_forest_v1", "version": 1, "params": model_params},
        "training_outcome": None,
        "evaluation_outcomes": [],
        "policies": [policies[0]],
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
            {"json_pointer": "/policies/0", "values": policies},
            {"json_pointer": "/execution/take_profit_pips", "values": take_profits},
        ],
        "combination": "zip",
    }


def _dynamic_policy() -> dict[str, object]:
    return {
        "name": "adaptive_bracket_exposure_v1",
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
            "output_column": "score",
            "stop_loss_column": None,
            "take_profit_column": None,
            "threshold": _encoded_float(0.0),
        },
    }


def _verify_pairing(expanded: tuple[tuple[int, dict[str, object]], ...]) -> None:
    for ordinal, child in expanded:
        policy = _first_policy(child)
        execution = _exact_mapping(child.get("execution"), label="validation execution")
        expected_name = "adaptive_bracket_exposure_v1" if ordinal == 0 else "bracket_threshold_v1"
        expected_tp = 800 if ordinal == 0 else _TP_PIPS[ordinal - 1]
        if policy.get("name") != expected_name or _decoded_float(
            execution.get("take_profit_pips")
        ) != float(expected_tp):
            raise _ContractViolationError("ULS TP validation policy/TP pairing differs")


def _verify_freeze(directory: _Path) -> dict[str, object]:
    freeze = _read_json(directory / "freeze-manifest.json")
    if (
        freeze.get("schema") != _FREEZE_SCHEMA
        or freeze.get("sweep_id") != _SWEEP_ID
        or freeze.get("expected_children") != len(_LABELS)
    ):
        raise _ContractViolationError("ULS TP validation freeze is malformed")
    if {path.name for path in directory.iterdir() if path.is_file()} != {
        "freeze-manifest.json",
        "request.json",
    }:
        raise _ContractViolationError("ULS TP validation freeze files differ")
    request_path = directory / "request.json"
    if _sha256_file(request_path) != freeze.get("request_sha256"):
        raise _ContractViolationError("ULS TP validation request hash differs")
    request = _read_json(request_path)
    expanded = _expand_request(request)
    if len(expanded) != len(_LABELS):
        raise _ContractViolationError("ULS TP frozen validation child count differs")
    _verify_pairing(expanded)
    reference = _exact_mapping(freeze["bundle_reference"], label="bundle reference")
    for _ordinal, child in expanded:
        fit_mode = _exact_mapping(child.get("fit_mode"), label="frozen fit mode")
        if fit_mode != {"mode": "frozen_state_v1", **reference}:
            raise _ContractViolationError("ULS TP frozen-state reference differs")
    return freeze


def _report_validation(
    *, runs_root: _Path, freeze_dir: _Path, output_path: _Path | None = None
) -> dict[str, object]:
    if not isinstance(runs_root, _Path) or not runs_root.is_absolute():
        raise _ContractViolationError("runs_root must be an absolute Path")
    freeze = _verify_freeze(freeze_dir)
    request = _read_json(freeze_dir / "request.json")
    plan = _read_sweep_plan(runs_root, _SWEEP_ID)
    if len(plan._children) != len(_LABELS):
        raise _ContractViolationError("planned ULS TP validation child count differs")
    for field in ("base_config", "axes", "combination"):
        if _canonical_document_json(plan._document.get(field)) != _canonical_document_json(
            request.get(field)
        ):
            raise _ContractViolationError("planned ULS TP validation request differs from freeze")
    expected_platform = _exact_mapping(freeze["bundle_platform"], label="bundle platform")
    if _exact_mapping(plan._document.get("platform"), label="plan platform") != expected_platform:
        raise _ContractViolationError("ULS TP validation platform differs from bundle freeze")
    if plan._document.get("dependency_lock_sha256") != freeze.get("dependency_lock_sha256"):
        raise _ContractViolationError("ULS TP validation dependency differs from bundle freeze")
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
    for ordinal, label in enumerate(_LABELS):
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
        if manifest is None:
            raise _ContractViolationError("validation COMPLETE child lacks manifest")
        digest = dict(manifest.metric_hashes).get("evaluation-summary.json")
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise _ContractViolationError("validation metric artifact hash is missing")
        metric_path = runs_root / "runs" / manifest.run_id / "metrics/evaluation-summary.json"
        _verify_file_hash(metric_path, digest)
        rows.append(
            {
                "ordinal": ordinal,
                "label": label,
                "tp_pips": None if ordinal == 0 else _TP_PIPS[ordinal - 1],
                "run_id": manifest.run_id,
                "experiment_fingerprint": manifest.experiment_fingerprint,
                "metrics": _extract_metrics(_read_json(metric_path)),
            }
        )
    dynamic = _typing.cast("dict[str, float]", rows[0]["metrics"])
    best_tp = freeze.get("train_selected_fixed_tp_pips")
    if type(best_tp) is not int:
        raise _ContractViolationError("frozen TRAIN-selected TP is malformed")
    baseline_row = rows[_TP_PIPS.index(best_tp) + 1]
    baseline = _typing.cast("dict[str, float]", baseline_row["metrics"])
    result: dict[str, object] = {
        "schema": _RESULT_SCHEMA,
        "sweep_id": _SWEEP_ID,
        "freeze_manifest_sha256": _sha256_file(freeze_dir / "freeze-manifest.json"),
        "bundle_reference": freeze["bundle_reference"],
        "validation_dataset": freeze["validation_dataset"],
        "children": rows,
        "comparison": {
            "train_selected_fixed_tp_pips": best_tp,
            "dynamic_minus_train_selected_fixed": {
                name: dynamic[name] - baseline[name]
                for name in (
                    "trade_count",
                    "gross_pnl",
                    "total_cost",
                    "net_pnl",
                    "win_rate",
                    "max_drawdown",
                )
            },
            "primary_success": dynamic["net_pnl"] > baseline["net_pnl"]
            and dynamic["trade_count"] >= 30.0,
            "success_rule": freeze["success_rule"],
        },
        "mandatory_limitations": {
            "cost_semantics": "unverified",
            "not_a_profitability_claim": True,
            "validation_is_reused_development_evidence": True,
            "not_independent_validation": True,
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


def _encoded_float(value: float) -> dict[str, str]:
    if type(value) is not float:
        raise _ContractViolationError("encoded config value must be a Python float")
    return {"__float_hex__": value.hex()}


def _decoded_float(value: object) -> float | None:
    if type(value) is not dict or set(value) != {"__float_hex__"}:
        return None
    encoded = value.get("__float_hex__")
    return float.fromhex(encoded) if type(encoded) is str else None


def _absolute_path(value: str) -> _Path:
    if type(value) is not str or not value:
        raise _argparse.ArgumentTypeError("path must be non-empty")
    return _Path(value).expanduser().resolve()


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="phase8-tp-validation")
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
