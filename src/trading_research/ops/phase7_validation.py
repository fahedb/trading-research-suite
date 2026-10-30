"""Freeze and report the preregistered Phase-7 VALIDATION family."""

from __future__ import annotations

import argparse as _argparse
import json as _json
import math as _math
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
    _ZONE_PARAMETER_NAMES,
    _bundle_reference_document,
    _canonical_document_json,
    _exact_mapping,
    _json_bytes,
    _read_bundle_manifest,
    _read_json,
    _sha256_bytes,
    _sha256_file,
    _verify_bundle_files,
    _write_new,
)
from trading_research.index.trials import _files_trial_ledger

if _typing.TYPE_CHECKING:
    from trading_research.experiments.runs import RunManifest as _RunManifest

__all__: list[str] = []

_FREEZE_SCHEMA = "zone-selector-validation-freeze-v1"
_RESULT_SCHEMA = "zone-selector-validation-result-v1"
_SOURCE_SCHEMA = "zone-selector-source-manifest-v1"
_V2_FREEZE_SCHEMA = "zone-selector-validation-freeze-v2"
_V2_RESULT_SCHEMA = "zone-selector-validation-result-v2"
_V2_SOURCE_SCHEMA = "zone-selector-source-manifest-v2"
_REQUEST_SCHEMA = "research-sweep-request-v1"
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_ID = _re.compile(r"^zone-tp-selector-v[12]-[0-9a-f]{20}$")
_ZONE_PARAM_NAMES = _ZONE_PARAMETER_NAMES
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
_WARMUP_ROWS = 28_714
_SPLIT_POLICY = {
    "adr": "ADR-0012",
    "policy_version": "v1",
    "policy_sha256": "b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb",
}
_FAMILIES = (
    ("dynamic", None),
    ("fixed_200", 2.0),
    ("fixed_400", 4.0),
    ("fixed_800", 8.0),
)
_REQUEST_FILES = {
    family: f"phase7-zone-tp-selector-validation-{family.replace('_', '-')}-v1.json"
    for family, _distance in _FAMILIES
}
_SWEEP_IDS = {
    family: f"phase7-zone-tp-selector-validation-{family.replace('_', '-')}-v1"
    for family, _distance in _FAMILIES
}
_SUCCESS_RULE: dict[str, object] = {
    "primary_requires_both": True,
    "criterion_1": "dynamic_mean_total_gross_r_gt_largest_fixed_mean_total_gross_r",
    "criterion_2": "dynamic_paired_wins_vs_single_best_fixed_gt_50_of_100",
    "fixed_mean_exact_tie_order": ["fixed_200", "fixed_400", "fixed_800"],
    "no_tuning_after_validation": True,
}


def _freeze_validation_specs(*, bundle_dir: _Path, output_dir: _Path) -> dict[str, object]:
    """Write four immutable 100-child zip-sweep requests from one verified bundle."""
    for path, label in ((bundle_dir, "bundle_dir"), (output_dir, "output_dir")):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    if _BUNDLE_ID.fullmatch(bundle_dir.name) is None:
        raise _ContractViolationError("bundle directory name is malformed")
    if output_dir.exists():
        raise _ContractViolationError("validation freeze output already exists")
    manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
    manifest = _read_bundle_manifest(bundle_dir)
    _verify_bundle_files(bundle_dir, manifest)
    manifest_sha = _sha256_bytes(manifest_bytes)
    reference = _bundle_reference_document(manifest, manifest_sha256=manifest_sha)
    source = _read_json(bundle_dir / "source-manifest.json")
    study_version = "v2" if source.get("schema") == _V2_SOURCE_SCHEMA else "v1"
    request_files = {
        family: (
            f"phase7-zone-tp-selector-validation-{family.replace('_', '-')}-{study_version}.json"
        )
        for family, _distance in _FAMILIES
    }
    sweep_ids = {
        family: f"phase7-zone-tp-selector-validation-{family.replace('_', '-')}-{study_version}"
        for family, _distance in _FAMILIES
    }
    configurations, execution = _validation_inputs(source)
    requests = _validation_requests(
        configurations=configurations,
        execution=execution,
        bundle_reference=reference,
        study_version=study_version,
        sweep_ids=sweep_ids,
    )
    request_bytes = {
        request_files[family]: _json_bytes(document)
        for family, document in requests.items()
    }
    configuration_order = [
        _typing.cast("str", item["feature_config_digest"]) for item in configurations
    ]
    freeze: dict[str, object] = {
        "schema": _V2_FREEZE_SCHEMA if study_version == "v2" else _FREEZE_SCHEMA,
        "bundle_reference": reference,
        "bundle_platform": manifest["platform"],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"],
        "validation_dataset": _VALIDATION_DATASET,
        "validation_bounds": _VALIDATION_BOUNDS,
        "warmup": {
            "prior_observed_rows": _WARMUP_ROWS,
            "rule": "same_observed_row_warmup_as_train_v1",
        },
        "configuration_order": configuration_order,
        "families": {
            family: {
                "request_file": request_files[family],
                "request_sha256": _sha256_bytes(request_bytes[request_files[family]]),
                "sweep_id": sweep_ids[family],
                "expected_children": 100,
            }
            for family, _distance in _FAMILIES
        },
        "expected_total_children": 400,
        "execution": execution,
        "success_rule": _SUCCESS_RULE,
    }
    if study_version == "v2":
        freeze["study_version"] = "v2"
    temporary = output_dir.with_name(f".{output_dir.name}.{_os.getpid():x}.tmp")
    if temporary.exists():
        raise _ContractViolationError("validation freeze temporary path exists")
    temporary.mkdir(parents=True, mode=0o755)
    try:
        for name, payload in sorted(request_bytes.items()):
            _write_new(temporary / name, payload)
        _write_new(temporary / "freeze-manifest.json", _json_bytes(freeze))
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        _os.replace(temporary, output_dir)
    except BaseException:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        raise
    _verify_freeze_directory(output_dir)
    return freeze


def _validation_inputs(
    source: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    source_schema = source.get("schema")
    if source_schema not in {_SOURCE_SCHEMA, _V2_SOURCE_SCHEMA}:
        raise _ContractViolationError("bundle source manifest schema is unsupported")
    raw = source.get("configurations")
    if type(raw) is not list or len(raw) != 100:
        raise _ContractViolationError("bundle must freeze exactly 100 configurations")
    configurations: list[dict[str, object]] = []
    config_digests: set[str] = set()
    origin_digests: set[str] = set()
    context_origin_digests: set[str] = set()
    for item in raw:
        row = _exact_mapping(item, label="source configuration")
        expected_fields = {"feature_config_digest", "feature_origin_digest", "params"}
        if source_schema == _V2_SOURCE_SCHEMA:
            expected_fields.add("context_origin_digest")
        if set(row) != expected_fields:
            raise _ContractViolationError("source configuration fields differ")
        config_digest = row["feature_config_digest"]
        origin_digest = row["feature_origin_digest"]
        context_origin_digest = row.get("context_origin_digest")
        params = _exact_mapping(row["params"], label="source Zone params")
        if (
            type(config_digest) is not str
            or _SHA256.fullmatch(config_digest) is None
            or type(origin_digest) is not str
            or _SHA256.fullmatch(origin_digest) is None
            or set(params) != set(_ZONE_PARAM_NAMES)
            or _sha256_bytes(_canonical_document_json(params).encode("ascii")) != config_digest
            or (
                source_schema == _V2_SOURCE_SCHEMA
                and (
                    type(context_origin_digest) is not str
                    or _SHA256.fullmatch(context_origin_digest) is None
                )
            )
        ):
            raise _ContractViolationError("source configuration identity is malformed")
        config_digests.add(config_digest)
        origin_digests.add(origin_digest)
        if type(context_origin_digest) is str:
            context_origin_digests.add(context_origin_digest)
        configurations.append(row)
    if len(config_digests) != 100 or len(origin_digests) != 100:
        raise _ContractViolationError("configuration or FeatureOrigin identities are duplicated")
    if source_schema == _V2_SOURCE_SCHEMA and len(context_origin_digests) != 100:
        raise _ContractViolationError("V2 context FeatureOrigin identities are duplicated")
    if [item["feature_config_digest"] for item in configurations] != sorted(config_digests):
        raise _ContractViolationError("source configurations are not in frozen digest order")
    execution = _exact_mapping(source.get("evaluation_execution"), label="execution")
    if execution.get("take_profit_pips") is not None or execution.get("stop_loss_pips") is not None:
        raise _ContractViolationError("evaluation execution must delegate both brackets to policy")
    binding = _exact_mapping(execution.get("cost_binding"), label="cost binding")
    if binding.get("cost_semantics") != "unverified":
        raise _ContractViolationError("Phase-7 execution must remain permanently unverified")
    return configurations, execution


def _validation_requests(
    *,
    configurations: list[dict[str, object]],
    execution: dict[str, object],
    bundle_reference: dict[str, object],
    study_version: str = "v1",
    sweep_ids: dict[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    if study_version not in {"v1", "v2"}:
        raise _ContractViolationError("validation study version is unsupported")
    resolved_sweep_ids = _SWEEP_IDS if sweep_ids is None else sweep_ids
    first_params = _exact_mapping(configurations[0]["params"], label="first Zone params")
    axes = [
        {
            "json_pointer": f"/features/0/params/{name}",
            "values": [
                _exact_mapping(item["params"], label="Zone params")[name]
                for item in configurations
            ],
        }
        for name in _ZONE_PARAM_NAMES
    ]
    if study_version == "v2":
        axes.extend(
            {
                "json_pointer": f"/features/1/params/{name}",
                "values": [
                    _exact_mapping(item["params"], label="Zone params")[name]
                    for item in configurations
                ],
            }
            for name in _ZONE_PARAM_NAMES
        )
    requests: dict[str, dict[str, object]] = {}
    for family, distance in _FAMILIES:
        request: dict[str, object] = {
            "schema": _REQUEST_SCHEMA,
            "sweep_id": resolved_sweep_ids[family],
            "base_config": _base_validation_config(
                feature_params=first_params,
                execution=execution,
                bundle_reference=bundle_reference,
                distance=distance,
                study_version=study_version,
            ),
            "axes": axes,
            "combination": "zip",
        }
        expanded = _expand_request(request)
        if len(expanded) != 100:
            raise _ContractViolationError("validation request did not expand to 100 children")
        observed = [
            _sha256_bytes(
                _canonical_document_json(_feature_params(child)).encode("ascii")
            )
            for _ordinal, child in expanded
        ]
        expected = [
            _typing.cast("str", item["feature_config_digest"])
            for item in configurations
        ]
        if observed != expected:
            raise _ContractViolationError("validation request changed configuration pairing")
        requests[family] = request
    return requests


def _base_validation_config(
    *,
    feature_params: dict[str, object],
    execution: dict[str, object],
    bundle_reference: dict[str, object],
    distance: float | None,
    study_version: str = "v1",
) -> dict[str, object]:
    policy = (
        {
            "name": "zone_adaptive_bracket_exposure_v1",
            "version": 1,
            "params": {
                "stop_loss_distance": _encoded_float(4.0),
                "value_0_distance": _encoded_float(2.0),
                "value_1_distance": _encoded_float(4.0),
                "value_2_distance": _encoded_float(8.0),
            },
        }
        if distance is None
        else {
            "name": "zone_fixed_bracket_exposure_v1",
            "version": 1,
            "params": {
                "stop_loss_distance": _encoded_float(4.0),
                "take_profit_distance": _encoded_float(distance),
            },
        }
    )
    features: list[dict[str, object]] = [
        {"name": "zone_trader_v13", "version": 1, "params": feature_params}
    ]
    model: dict[str, object] = {
        "name": "zone_ridge_selector_v1",
        "version": 1,
        "params": {"ridge_lambda": _encoded_float(1.0)},
    }
    if study_version == "v2":
        features.append(
            {"name": "zone_context_v1", "version": 1, "params": feature_params}
        )
        model = {
            "name": "zone_forest_selector_v2",
            "version": 1,
            "params": {
                "bootstrap": False,
                "max_depth": 8,
                "max_features": _encoded_float(0.75),
                "min_samples_leaf": 64,
                "n_estimators": 96,
                "random_seed": 20_260_823,
            },
        }
    document: dict[str, object] = {
        "schema": "research-experiment-config-v2",
        "integrity_mode": "full",
        "dataset": _VALIDATION_DATASET,
        "split_policy": _SPLIT_POLICY,
        "bounds": _VALIDATION_BOUNDS,
        "features": features,
        "representation": None,
        "model": model,
        "training_outcome": None,
        "evaluation_outcomes": [],
        "policies": [policy],
        "evaluators": [],
        "execution": execution,
        "trade_evaluators": [
            {
                "name": "trade_summary_v1",
                "version": 1,
                "params": {"min_effective_trades": 1},
            },
            {
                "name": "gross_r_summary_v1",
                "version": 1,
                "params": {
                    "min_effective_trades": 1,
                    "risk_distance": _encoded_float(4.0),
                },
            },
        ],
        "fit_mode": {
            "mode": "frozen_state_v1",
            **bundle_reference,
        },
        "seed": None,
        "access_reason": None,
        "declared_lockbox_evaluation": False,
    }
    return _config_from_document(document).document()


def _verify_freeze_directory(directory: _Path) -> dict[str, object]:
    freeze = _read_json(directory / "freeze-manifest.json")
    if (
        freeze.get("schema") not in {_FREEZE_SCHEMA, _V2_FREEZE_SCHEMA}
        or freeze.get("expected_total_children") != 400
    ):
        raise _ContractViolationError("validation freeze manifest is malformed")
    families = _exact_mapping(freeze.get("families"), label="freeze families")
    if set(families) != {family for family, _distance in _FAMILIES}:
        raise _ContractViolationError("validation freeze family inventory differs")
    request_names = {
        _exact_mapping(families[family], label="freeze family").get("request_file")
        for family, _distance in _FAMILIES
    }
    if any(type(name) is not str for name in request_names):
        raise _ContractViolationError("validation freeze request filenames are malformed")
    expected_files = {"freeze-manifest.json", *_typing.cast("set[str]", request_names)}
    if {path.name for path in directory.iterdir() if path.is_file()} != expected_files:
        raise _ContractViolationError("validation freeze files are missing or extra")
    for family, _distance in _FAMILIES:
        row = _exact_mapping(families[family], label="freeze family")
        name = row.get("request_file")
        digest = row.get("request_sha256")
        sweep_id = row.get("sweep_id")
        if (
            type(name) is not str
            or type(sweep_id) is not str
            or type(digest) is not str
            or _SHA256.fullmatch(digest) is None
            or _sha256_file(directory / name) != digest
        ):
            raise _ContractViolationError("frozen validation request hash differs")
        request = _read_json(directory / name)
        if request.get("sweep_id") != sweep_id or len(_expand_request(request)) != 100:
            raise _ContractViolationError("frozen validation request content differs")
    return freeze


def _report_validation_results(
    *,
    runs_root: _Path,
    freeze_dir: _Path,
    audit_path: _Path,
) -> dict[str, object]:
    """Read exactly the four frozen sweeps and evaluate the preregistered rule."""
    for path, label in (
        (runs_root, "runs_root"),
        (freeze_dir, "freeze_dir"),
        (audit_path, "audit_path"),
    ):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    freeze = _verify_freeze_directory(freeze_dir)
    attempts = _run_attempts_by_fingerprint(runs_root)
    configuration_order = freeze.get("configuration_order")
    if type(configuration_order) is not list or len(configuration_order) != 100:
        raise _ContractViolationError("freeze configuration order is malformed")
    family_rows: dict[str, list[dict[str, object]]] = {}
    trial_ledgers: dict[str, object] = {}
    frozen_families = _exact_mapping(freeze.get("families"), label="freeze families")
    for family, _distance in _FAMILIES:
        family_spec = _exact_mapping(
            frozen_families[family],
            label="freeze family",
        )
        sweep_id = family_spec.get("sweep_id")
        request_file = family_spec.get("request_file")
        if type(sweep_id) is not str or type(request_file) is not str:
            raise _ContractViolationError("freeze family identity is malformed")
        plan = _read_sweep_plan(runs_root, sweep_id)
        frozen_request = _read_json(freeze_dir / request_file)
        _assert_frozen_execution_identity(
            platform=plan._document.get("platform"),
            dependency_lock_sha256=plan._document.get("dependency_lock_sha256"),
            freeze=freeze,
        )
        if len(plan._children) != 100 or plan._document["duplicates_collapsed"] != []:
            raise _ContractViolationError("planned validation sweep is not exactly 100 unique")
        _assert_full_child_pairing(
            tuple(child._config_document for child in plan._children),
            frozen_request=frozen_request,
        )
        rows: list[dict[str, object]] = []
        for ordinal, child in enumerate(plan._children):
            if child._child_ordinal != ordinal:
                raise _ContractViolationError("validation child ordinal order differs")
            params = _feature_params(child._config_document)
            config_digest = _sha256_bytes(_canonical_document_json(params).encode("ascii"))
            if config_digest != configuration_order[ordinal]:
                raise _ContractViolationError("planned child configuration pairing differs")
            candidate_attempts = attempts.get(child._experiment_fingerprint, [])
            complete = [
                item
                for item in candidate_attempts
                if item[1].state is _RunState.COMPLETE and item[1].canonical is True
            ]
            if not complete:
                raise _ContractViolationError(
                    f"validation child {family}/{ordinal} has no canonical COMPLETE attempt"
                )
            run_dir, manifest = min(complete, key=lambda item: item[0].name)
            _assert_frozen_execution_identity(
                platform={
                    "git_commit": manifest.platform_identity.git_commit,
                    "git_dirty": manifest.platform_identity.git_dirty,
                    "diff_sha256": manifest.platform_identity.diff_sha256,
                },
                dependency_lock_sha256=manifest.dependency_lock_sha256,
                freeze=freeze,
            )
            rows.append(
                {
                    "ordinal": ordinal,
                    "feature_config_digest": config_digest,
                    "experiment_fingerprint": child._experiment_fingerprint,
                    "run_id": run_dir.name,
                    "attempt_run_ids": [item[0].name for item in candidate_attempts],
                    "metrics": _read_run_metrics(run_dir, manifest),
                }
            )
        family_rows[family] = rows
        ledger = _files_trial_ledger(
            runs_root,
            audit_path=audit_path,
            sweep_id=sweep_id,
        )
        if ledger._has_errors:
            raise _ContractViolationError(f"Trial Ledger has errors for {family}")
        trial_ledgers[family] = ledger._document
    summaries = {family: _family_summary(rows) for family, rows in family_rows.items()}
    fixed_order = ["fixed_200", "fixed_400", "fixed_800"]
    best_fixed = max(
        fixed_order,
        key=lambda name: summaries[name]["mean_total_gross_r"],
    )
    dynamic_totals = _metric_column(family_rows["dynamic"], "gross_r_total")
    fixed_totals = _metric_column(family_rows[best_fixed], "gross_r_total")
    differences = dynamic_totals - fixed_totals
    criterion_1 = summaries["dynamic"]["mean_total_gross_r"] > summaries[best_fixed][
        "mean_total_gross_r"
    ]
    paired_wins = int(_np.count_nonzero(differences > 0.0))
    criterion_2 = paired_wins > 50
    return {
        "schema": (
            _V2_RESULT_SCHEMA
            if freeze.get("schema") == _V2_FREEZE_SCHEMA
            else _RESULT_SCHEMA
        ),
        "freeze_manifest_sha256": _sha256_file(freeze_dir / "freeze-manifest.json"),
        "bundle_reference": freeze["bundle_reference"],
        "validation_dataset": freeze["validation_dataset"],
        "run_counts": {
            "dynamic": len(family_rows["dynamic"]),
            "fixed_200": len(family_rows["fixed_200"]),
            "fixed_400": len(family_rows["fixed_400"]),
            "fixed_800": len(family_rows["fixed_800"]),
            "total": sum(len(rows) for rows in family_rows.values()),
        },
        "family_summaries": summaries,
        "primary": {
            "best_fixed_family": best_fixed,
            "criterion_1_dynamic_mean_exceeds_best_fixed": bool(criterion_1),
            "criterion_2_paired_wins_gt_50": bool(criterion_2),
            "paired_wins": paired_wins,
            "paired_losses": int(_np.count_nonzero(differences < 0.0)),
            "paired_ties": int(_np.count_nonzero(differences == 0.0)),
            "median_paired_difference": float(_np.median(differences)),
            "success": bool(criterion_1 and criterion_2),
        },
        "success_rule": freeze["success_rule"],
        "families": family_rows,
        "trial_ledgers": trial_ledgers,
        "claims": {
            "cost_semantics": "unverified",
            "independence_claim": False,
            "multiple_testing_adjustment": None,
            "p_value": None,
        },
    }


def _assert_frozen_execution_identity(
    *,
    platform: object,
    dependency_lock_sha256: object,
    freeze: dict[str, object],
) -> None:
    expected_platform = _exact_mapping(
        freeze.get("bundle_platform"),
        label="frozen execution platform",
    )
    if set(expected_platform) != {"diff_sha256", "git_commit", "git_dirty"}:
        raise _ContractViolationError("frozen execution platform is malformed")
    expected_dependency = freeze.get("dependency_lock_sha256")
    if type(expected_dependency) is not str or _SHA256.fullmatch(expected_dependency) is None:
        raise _ContractViolationError("frozen dependency identity is malformed")
    if (
        _canonical_document_json(platform) != _canonical_document_json(expected_platform)
        or dependency_lock_sha256 != expected_dependency
    ):
        raise _ContractViolationError(
            "validation execution platform or dependency differs from the freeze"
        )


def _assert_full_child_pairing(
    planned_children: tuple[dict[str, object], ...],
    *,
    frozen_request: dict[str, object],
) -> None:
    frozen_children = tuple(child for _ordinal, child in _expand_request(frozen_request))
    if len(planned_children) != len(frozen_children):
        raise _ContractViolationError("planned and frozen validation child counts differ")
    if any(
        _canonical_document_json(planned) != _canonical_document_json(frozen)
        for planned, frozen in zip(planned_children, frozen_children, strict=True)
    ):
        raise _ContractViolationError(
            "planned validation child differs from its full frozen request config"
        )


def _run_attempts_by_fingerprint(
    runs_root: _Path,
) -> dict[str, list[tuple[_Path, _RunManifest]]]:
    result: dict[str, list[tuple[_Path, _RunManifest]]] = {}
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
        manifest = projection._manifest
        if manifest is not None:
            directory = runs_root / "runs" / manifest.run_id
            result.setdefault(manifest.experiment_fingerprint, []).append(
                (directory, manifest)
            )
    return result


def _read_run_metrics(
    run_dir: _Path,
    manifest: _RunManifest,
) -> dict[str, float | None]:
    metrics_files = dict(manifest.metric_hashes)
    expected_sha = metrics_files.get("evaluation-summary.json")
    path = run_dir / "metrics" / "evaluation-summary.json"
    if type(expected_sha) is not str or _sha256_file(path) != expected_sha:
        raise _ContractViolationError("validation metric artifact hash differs")
    document = _read_json(path)
    entries = document.get("entries")
    if type(entries) is not list:
        raise _ContractViolationError("validation evaluation inventory is malformed")
    result: dict[str, float] = {}
    expected_evaluators = {"trade_summary_v1", "gross_r_summary_v1"}
    observed_evaluators: set[str] = set()
    for raw_entry in entries:
        entry = _exact_mapping(raw_entry, label="evaluation entry")
        if entry.get("plane") != "B":
            raise _ContractViolationError("Phase-7 validation emitted a non-Plane-B entry")
        evaluator = _exact_mapping(entry.get("evaluator"), label="trade evaluator")
        evaluator_id = evaluator.get("plugin_id")
        if type(evaluator_id) is not str or evaluator_id not in expected_evaluators:
            raise _ContractViolationError("Phase-7 validation evaluator differs")
        observed_evaluators.add(evaluator_id)
        metrics = entry.get("metrics")
        if type(metrics) is not list:
            raise _ContractViolationError("validation metrics are malformed")
        for raw_metric in metrics:
            metric = _exact_mapping(raw_metric, label="metric")
            name = metric.get("name")
            bits = metric.get("value_float64_bits")
            if type(name) is not str or type(bits) is not str or _re.fullmatch(
                r"[0-9a-f]{16}", bits
            ) is None:
                raise _ContractViolationError("validation metric encoding is malformed")
            if name in result:
                raise _ContractViolationError("validation metric name is duplicated")
            result[name] = _struct.unpack(">d", bytes.fromhex(bits))[0]
    if observed_evaluators != expected_evaluators or set(result) != {
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
    }:
        raise _ContractViolationError("validation metric inventory differs from the freeze")
    return _normalize_validation_metrics(result)


def _normalize_validation_metrics(
    metrics: dict[str, float],
) -> dict[str, float | None]:
    trade_count = metrics["trade_count"]
    if not _math.isfinite(trade_count) or trade_count < 0.0:
        raise _ContractViolationError("validation trade count is invalid")
    if trade_count != 0.0:
        if any(not _math.isfinite(value) for value in metrics.values()):
            raise _ContractViolationError("non-empty validation run has a non-finite metric")
        return dict(metrics)
    undefined = set(metrics) - {"trade_count"}
    if any(not _math.isnan(metrics[name]) for name in undefined):
        raise _ContractViolationError(
            "zero-trade validation run has an unexpected defined metric"
        )
    normalized: dict[str, float | None] = dict.fromkeys(undefined)
    normalized["trade_count"] = 0.0
    for name in {
        "gross_pnl",
        "total_cost",
        "net_pnl",
        "gross_r_total",
        "gross_r_max_drawdown",
    }:
        normalized[name] = 0.0
    return normalized


def _family_summary(rows: list[dict[str, object]]) -> dict[str, float | int]:
    names = {
        "mean_total_gross_r": "gross_r_total",
        "mean_gross_r_per_trade": "gross_r_per_trade",
        "mean_trade_count": "trade_count",
        "mean_choice_0_rate": "choice_0_rate",
        "mean_choice_1_rate": "choice_1_rate",
        "mean_choice_2_rate": "choice_2_rate",
        "mean_gross_r_max_drawdown": "gross_r_max_drawdown",
        "mean_ambiguous_exit_rate": "ambiguous_exit_rate",
        "mean_gapped_exit_rate": "gapped_exit_rate",
        "mean_boundary_forced_close_rate": "boundary_forced_close_rate",
    }
    summary: dict[str, float | int] = {
        output: _mean_defined_metric(rows, metric)
        for output, metric in names.items()
    }
    zero_trade = sum(
        _exact_mapping(row["metrics"], label="row metrics")["trade_count"] == 0.0
        for row in rows
    )
    summary["zero_trade_configurations"] = zero_trade
    summary["defined_rate_configurations"] = len(rows) - zero_trade
    return summary


def _feature_params(document: dict[str, object]) -> dict[str, object]:
    features = document.get("features")
    if type(features) is not list or len(features) not in {1, 2}:
        raise _ContractViolationError("validation child feature inventory differs")
    feature = _exact_mapping(features[0], label="validation feature")
    params = _exact_mapping(feature.get("params"), label="validation Zone params")
    if len(features) == 2:
        context = _exact_mapping(features[1], label="validation context feature")
        context_params = _exact_mapping(
            context.get("params"),
            label="validation context params",
        )
        if _canonical_document_json(params) != _canonical_document_json(context_params):
            raise _ContractViolationError("V2 validation feature parameters are not paired")
    return params


def _metric_column(rows: list[dict[str, object]], name: str) -> _np.ndarray:
    values = [_exact_mapping(row["metrics"], label="row metrics")[name] for row in rows]
    if any(type(value) is not float or not _math.isfinite(value) for value in values):
        raise _ContractViolationError(f"validation metric {name!r} is not fully defined")
    return _np.asarray(values, dtype=_np.float64)


def _mean_defined_metric(rows: list[dict[str, object]], name: str) -> float:
    values = [
        _exact_mapping(row["metrics"], label="row metrics")[name]
        for row in rows
    ]
    defined = [
        value
        for value in values
        if type(value) is float and _math.isfinite(value)
    ]
    if not defined:
        raise _ContractViolationError(f"validation metric {name!r} has no defined values")
    if any(
        value is not None
        and (type(value) is not float or not _math.isfinite(value))
        for value in values
    ):
        raise _ContractViolationError(f"validation metric {name!r} is malformed")
    return float(_np.mean(_np.asarray(defined, dtype=_np.float64)))


def _encoded_float(value: float) -> dict[str, str]:
    return {"__float_hex__": value.hex()}


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="phase7-validation")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--bundle-dir", required=True, type=_absolute_path)
    freeze.add_argument("--output-dir", required=True, type=_absolute_path)
    report = commands.add_parser("report")
    report.add_argument("--runs-root", required=True, type=_absolute_path)
    report.add_argument("--freeze-dir", required=True, type=_absolute_path)
    report.add_argument("--audit", required=True, type=_absolute_path)
    report.add_argument("--output", required=True, type=_absolute_path)
    return parser


def _absolute_path(value: str) -> _Path:
    return _Path(value).expanduser().resolve()


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            result = _freeze_validation_specs(
                bundle_dir=args.bundle_dir,
                output_dir=args.output_dir,
            )
        else:
            result = _report_validation_results(
                runs_root=args.runs_root,
                freeze_dir=args.freeze_dir,
                audit_path=args.audit,
            )
            _write_new(args.output, _json_bytes(result))
        _sys.stdout.write(_json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except (_ContractViolationError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
