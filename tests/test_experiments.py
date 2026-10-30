"""Resolved-config and fixed-stage orchestrator integration tests."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from tests.load_fixtures import (
    PartitionSpec,
    RecordingAuditWriter,
    make_load_case,
)
from trading_research.artifacts import (
    ArtifactStore,
    MaterializationPolicy,
    PlatformIdentity,
)
from trading_research.core.errors import AccessDeniedError, ContractViolationError
from trading_research.core.identity import ViewRole
from trading_research.data import MarketFrameLoader
from trading_research.experiments.config import ResolvedExperimentConfig, load_resolved_config
from trading_research.experiments.orchestration import (
    _RuntimeEnvironment,
    _upgrade_ephemeral_for_downstream,
    run_experiment,
    verify_experiment,
)
from trading_research.experiments.runs import RunState, scan_runs
from trading_research.features.reference import LogReturn1
from trading_research.models.reference import StatelessRuleModel

if TYPE_CHECKING:
    from pathlib import Path

    from tests.load_fixtures import LoadCase


def _config_document(case: LoadCase) -> dict[str, object]:
    policy = case.policy.identity
    manifest = case.target.manifest
    return {
        "schema": "research-experiment-config-v1",
        "integrity_mode": "full",
        "dataset": {
            "dataset_id": manifest.dataset_id,
            "manifest_sha256": manifest.manifest_sha256,
            "role": "train",
        },
        "split_policy": {
            "adr": policy.adr,
            "policy_version": policy.policy_version,
            "policy_sha256": policy.policy_sha256,
        },
        "bounds": {"context_start": 100, "eval_start": 101, "eval_end": 140},
        "features": [{"name": "log_return_1", "version": 1, "params": {}}],
        "representation": None,
        "model": {"name": "stateless_rule_v1", "version": 1, "params": {}},
        "training_outcome": None,
        "evaluation_outcomes": [
            {
                "name": "forward_log_return_v1",
                "version": 1,
                "params": {
                    "boundary_policy": "nan",
                    "decision_delay_rows": 1,
                    "horizon_rows": 1,
                    "reference_price_column": "close",
                },
            }
        ],
        "policies": [
            {
                "name": "threshold_v1",
                "version": 1,
                "params": {
                    "output_column": "score",
                    "threshold": {"__float_hex__": "0x0.0p+0"},
                },
            }
        ],
        "evaluators": [
            {
                "name": "sign_accuracy_v1",
                "version": 1,
                "params": {
                    "min_effective_samples": 1,
                    "outcome_column": "target.forward_log_return",
                    "output_column": "score",
                },
            }
        ],
        "execution": None,
        "trade_evaluators": [],
        "folds": {
            "fit_rows": 10,
            "eval_rows": 5,
            "step_rows": 10,
            "embargo_rows": 0,
            "purge_override": None,
        },
        "seed": None,
        "access_reason": None,
        "declared_lockbox_evaluation": False,
    }


def _execution_document() -> dict[str, object]:
    def encoded(value: float) -> dict[str, str]:
        return {"__float_hex__": value.hex()}

    return {
        "execution_id": "bracket_single_position_v1",
        "version": 1,
        "lot_size": encoded(1.0),
        "take_profit_pips": None,
        "stop_loss_pips": None,
        "cost_binding": {
            "binding_id": "synthetic-recorded-path-v1",
            "binding_digest": None,
            "cost_semantics": "unverified",
            "execution_price_model": "recorded_path_unverified",
            "contract_size": encoded(1.0),
            "price_units_per_pip": encoded(1.0),
            "account_currency_per_quote_currency": encoded(1.0),
            "spread_pips": encoded(0.0),
            "commission_per_lot": encoded(0.0),
            "commission_basis": "per_round_turn",
        },
    }


def _resolved(
    tmp_path: Path,
    document: dict[str, object],
) -> ResolvedExperimentConfig:
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_resolved_config(path)


def _case_and_runtime(
    tmp_path: Path,
) -> tuple[
    LoadCase,
    _RuntimeEnvironment,
    ResolvedExperimentConfig,
    RecordingAuditWriter,
]:
    keys = tuple(range(100, 146))
    case = make_load_case(
        tmp_path,
        specs=(PartitionSpec("p0", "bars.parquet", keys, 100, 145, len(keys)),),
    )
    audit = RecordingAuditWriter()
    runtime = _runtime_for_case(case, tmp_path, audit=audit)
    config = _resolved(tmp_path, _config_document(case))
    return case, runtime, config, audit


def _runtime_for_case(
    case: LoadCase,
    tmp_path: Path,
    *,
    audit: RecordingAuditWriter,
    name: str = "runtime",
) -> _RuntimeEnvironment:
    runs_root = (tmp_path / name).resolve()
    runs_root.mkdir()
    return _RuntimeEnvironment(
        _runs_root=runs_root,
        _registry=case.registry,
        _split_policy=case.policy,
        _loader=MarketFrameLoader(
            registry=case.registry,
            split_policy=case.policy,
        ),
        _store=ArtifactStore(runs_root),
        _audit_writer=audit,
        _platform_identity=PlatformIdentity("a" * 40, False, None),
        _dependency_lock_sha256="b" * 64,
        _machine_identity={"machine": "synthetic"},
        _git_diff=None,
        _lockfile_in_sync=True,
        _full_integrity_verified=True,
    )


def _set_sensitive_role(
    document: dict[str, object],
    *,
    role: ViewRole,
    first_key: int,
) -> None:
    dataset = cast("dict[str, object]", document["dataset"])
    dataset["role"] = role.value
    document["bounds"] = {
        "context_start": first_key,
        "eval_start": first_key + 1,
        "eval_end": first_key + 40,
    }
    document["access_reason"] = {
        "code": "synthetic-research",
        "detail": "Approved synthetic sensitive-view integration test",
    }
    document["declared_lockbox_evaluation"] = role is ViewRole.LOCKBOX


def test_resolved_config_refuses_unknown_fields_and_role_ceremony_mismatch(
    tmp_path: Path,
) -> None:
    case = make_load_case(tmp_path)
    document = _config_document(case)
    document["tolerance"] = 1.0
    with pytest.raises(ContractViolationError, match="fields"):
        _resolved(tmp_path, document)

    document = _config_document(case)
    document["access_reason"] = {"code": "unexpected", "detail": "not gated"}
    with pytest.raises(ContractViolationError, match="must not declare"):
        _resolved(tmp_path, document)


def test_config_only_run_persists_complete_evaluated_result(tmp_path: Path) -> None:
    _case, runtime, config, audit = _case_and_runtime(tmp_path)

    manifest = run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)

    assert manifest.state.value == "COMPLETE"
    assert manifest.canonical
    assert manifest.evaluation_count == 3
    assert len(manifest.policy_execution_json) == 3
    assert dict(manifest.artifact_hashes).keys() >= {
        "features.npz",
        "outcome-0000.npz",
        "fold-0000-model-state.json",
        "fold-0000-model-output.npz",
    }
    assert not any("-intent-" in name for name in dict(manifest.artifact_hashes))
    assert audit.events == []


def test_config_run_projects_provider_superset_to_exact_model_inputs(
    tmp_path: Path,
) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    features = cast("list[dict[str, object]]", document["features"])
    features.insert(0, {"name": "bar_range", "version": 1, "params": {}})
    config = _resolved(tmp_path, document)

    manifest = run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)

    assert manifest.state is RunState.COMPLETE
    assert manifest.evaluation_count == 3
    assert audit.events == []


def test_bracketed_policy_run_records_levels_and_persists_its_intent_channels(
    tmp_path: Path,
) -> None:
    """End to end for the bracketed intent schema on the one simulator identity.

    The reference model emits only ``score``, so this configuration names no
    bracket column and every instruction defers to the run's fixed execution
    values. That is the interesting case for compatibility: the run behaves
    exactly like a fixed-bracket run, and the levels it used are now recorded
    on each trade instead of having to be reconstructed by a reader.
    """
    case, runtime, _config, _audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    document["execution"] = _execution_document()
    document["policies"] = [
        {
            "name": "bracket_threshold_v1",
            "version": 1,
            "params": {
                "output_column": "score",
                "stop_loss_column": None,
                "take_profit_column": None,
                "threshold": {"__float_hex__": "0x0.0p+0"},
            },
        }
    ]
    document["trade_evaluators"] = [
        {
            "name": "trade_summary_v1",
            "version": 1,
            "params": {"min_effective_trades": 0},
        }
    ]
    config = _resolved(tmp_path, document)

    manifest = run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)
    assert manifest.state is RunState.COMPLETE

    run_path = runtime._runs_root / "runs" / manifest.run_id
    artifacts = run_path / "artifacts"

    with np.load(artifacts / "fold-0000-intent-0000.npz", allow_pickle=False) as payload:
        intent_meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        assert intent_meta["intent_schema_id"] == "bracketed_exposure_intent_v1"
        assert set(payload.files) == {
            "__meta__",
            "keys",
            "target_exposure",
            "take_profit_distance",
            "stop_loss_distance",
        }

    with np.load(
        artifacts / "fold-0000-policy-0000-trades.npz",
        allow_pickle=False,
    ) as payload:
        trade_meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        assert trade_meta["format_version"] == 2
        assert trade_meta["intent_schema_id"] == "bracketed_exposure_intent_v1"
        entry = payload["entry_price"]
        direction = payload["direction"].astype(np.float64)
        take_profit = payload["take_profit_price"]
        stop_loss = payload["stop_loss_price"]
        assert take_profit.dtype == np.dtype(np.float64)
        assert stop_loss.dtype == np.dtype(np.float64)
        if entry.size:
            recorded = ~np.isnan(take_profit)
            assert np.all(direction[recorded] * (take_profit[recorded] - entry[recorded]) > 0.0)
            recorded = ~np.isnan(stop_loss)
            assert np.all(direction[recorded] * (entry[recorded] - stop_loss[recorded]) > 0.0)


def test_phase4_config_run_persists_plane_b_artifacts_inventory_and_marking(
    tmp_path: Path,
) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    document["execution"] = _execution_document()
    policies = cast("list[dict[str, object]]", document["policies"])
    policies.append({"name": "abstain_v1", "version": 1, "params": {}})
    document["trade_evaluators"] = [
        {
            "name": "trade_summary_v1",
            "version": 1,
            "params": {"min_effective_trades": 0},
        },
        {
            "name": "equity_drawdown_v1",
            "version": 1,
            "params": {"min_effective_rows": 1},
        },
    ]
    config = _resolved(tmp_path, document)

    manifest = run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)

    assert manifest.state is RunState.COMPLETE
    assert manifest.evaluation_count == 15
    assert manifest.cost_semantics_unverified
    assert manifest.binding_id == "synthetic-recorded-path-v1"
    assert manifest.binding_digest is None
    assert manifest.execution_price_model == "recorded_path_unverified"
    artifacts = dict(manifest.artifact_hashes)
    for fold in range(3):
        for policy in range(2):
            assert f"fold-{fold:04d}-intent-{policy:04d}.npz" in artifacts
            assert f"fold-{fold:04d}-policy-{policy:04d}-trades.npz" in artifacts
            assert f"fold-{fold:04d}-policy-{policy:04d}-equity.npz" in artifacts
    assert "simulation-summary.json" in dict(manifest.metric_hashes)

    run_path = runtime._runs_root / "runs" / manifest.run_id
    inventory = json.loads((run_path / "evaluation_inventory.json").read_text())
    assert inventory["schema"] == "research-evaluation-inventory-v2"
    assert [entry["plane"] for entry in inventory["entries"]].count("A") == 3
    assert [entry["plane"] for entry in inventory["entries"]].count("B") == 12
    for entry in inventory["entries"]:
        if entry["plane"] == "B":
            assert entry["cost_semantics_unverified"] is True
            assert entry["binding_id"] == "synthetic-recorded-path-v1"
            assert entry["binding_digest"] is None
            assert entry["execution_price_model"] == "recorded_path_unverified"
            assert "policy" in entry
        else:
            assert "policy" not in entry
    summary = json.loads((run_path / "metrics" / "simulation-summary.json").read_text())
    assert summary["cost_semantics_unverified"] is True
    assert len(summary["results"]) == 6

    intent_path = run_path / "artifacts" / "fold-0000-intent-0000.npz"
    trade_path = run_path / "artifacts" / "fold-0000-policy-0000-trades.npz"
    equity_path = run_path / "artifacts" / "fold-0000-policy-0000-equity.npz"
    with np.load(intent_path, allow_pickle=False) as payload:
        assert set(payload.files) == {"__meta__", "keys", "target_exposure"}
        intent_meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        assert intent_meta["format"] == "trading-research-intent-frame"
        assert payload["keys"].dtype == np.dtype(np.int64)
        assert payload["target_exposure"].dtype == np.dtype(np.float64)
    with np.load(trade_path, allow_pickle=False) as payload:
        assert set(payload.files) == {
            "__meta__",
            "entry_key",
            "exit_key",
            "direction",
            "entry_price",
            "exit_price",
            "exit_reason",
            "ambiguous_exit",
            "gapped_exit",
            "gross_pnl",
            "spread_cost",
            "commission_cost",
            "net_pnl",
            "take_profit_price",
            "stop_loss_price",
        }
        trade_meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        assert trade_meta["format"] == "trading-research-trade-log"
        assert trade_meta["format_version"] == 2
        assert trade_meta["counters"]["intent_rows_absent"] >= 0
    with np.load(equity_path, allow_pickle=False) as payload:
        assert set(payload.files) == {"__meta__", "keys", "equity"}
        equity_meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        assert equity_meta["format"] == "trading-research-equity-curve"
        assert payload["keys"].dtype == np.dtype(np.int64)
        assert payload["equity"].dtype == np.dtype(np.float64)
    assert audit.events == []


def test_phase4_verification_compares_trade_and_equity_artifacts_exactly(
    tmp_path: Path,
) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    document["execution"] = _execution_document()
    document["trade_evaluators"] = [
        {
            "name": "trade_summary_v1",
            "version": 1,
            "params": {"min_effective_trades": 0},
        },
        {
            "name": "equity_drawdown_v1",
            "version": 1,
            "params": {"min_effective_rows": 1},
        },
    ]
    report = verify_experiment(
        _resolved(tmp_path, document),
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )

    assert report["passed"] is True
    artifact_reports = cast("list[dict[str, object]]", report["artifacts"])
    simulation_reports = [
        item
        for item in artifact_reports
        if "-policy-" in cast("str", item["label"])
        or "-intent-" in cast("str", item["label"])
    ]
    assert len(simulation_reports) == 9
    assert all(item["passed"] is True for item in simulation_reports)
    assert all(item["tolerance"] is None for item in simulation_reports)
    assert any(cast("str", item["label"]).endswith("-trades") for item in simulation_reports)
    assert any(cast("str", item["label"]).endswith("-equity") for item in simulation_reports)
    assert audit.events == []


def test_execution_changes_run_fingerprint_but_not_fitted_state_identity(
    tmp_path: Path,
) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    baseline_document = _config_document(case)
    baseline_document["execution"] = _execution_document()
    changed_document = _config_document(case)
    changed_execution = _execution_document()
    changed_execution["take_profit_pips"] = {"__float_hex__": (2.0).hex()}
    changed_document["execution"] = changed_execution

    first = run_experiment(
        _resolved(tmp_path, baseline_document),
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )
    second = run_experiment(
        _resolved(tmp_path, changed_document),
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )

    assert first.experiment_fingerprint != second.experiment_fingerprint
    state_keys = []
    for run_id in (first.run_id, second.run_id):
        document = json.loads(
            (
                runtime._runs_root
                / "runs"
                / run_id
                / "artifacts"
                / "fold-0000-model-state.json"
            ).read_text()
        )
        state_keys.append(document["identity_key"])
    assert state_keys[0] == state_keys[1]
    assert audit.events == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cost_semantics", "verified", "only unverified"),
        ("execution_price_model", "synthetic_declared", "recorded_path_unverified"),
    ],
)
def test_phase4_config_refuses_unavailable_execution_semantics(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    case = make_load_case(tmp_path)
    document = _config_document(case)
    execution = _execution_document()
    binding = cast("dict[str, object]", execution["cost_binding"])
    binding[field] = value
    document["execution"] = execution
    with pytest.raises(ContractViolationError, match=message):
        _resolved(tmp_path, document)


def test_run_verification_uses_two_attempts_and_stores_report(tmp_path: Path) -> None:
    _case, runtime, config, audit = _case_and_runtime(tmp_path)

    report = verify_experiment(
        config,
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )

    assert report["passed"] is True
    assert report["cache_policy"] == "forced_ephemeral"
    run_ids = cast("list[str]", report["run_ids"])
    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]
    assert report["first_content_hash"] == report["second_content_hash"]
    for run_id in run_ids:
        run_path = runtime._runs_root / "runs" / run_id
        assert (run_path / "determinism_report.json").is_file()
        manifest = json.loads((run_path / "manifest.json").read_text())
        assert manifest["state"] == "COMPLETE"
    assert not list((runtime._runs_root / "cache").rglob("*.npz"))
    assert audit.events == []


def test_verification_bypasses_an_existing_poisoned_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _case, runtime, config, _audit = _case_and_runtime(tmp_path)
    run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)
    cache_before = {
        path.relative_to(runtime._runs_root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in (runtime._runs_root / "cache").rglob("*.npz")
    }
    assert cache_before

    def forbidden_get(self: ArtifactStore, identity_key: str) -> bytes | None:
        raise AssertionError(f"verification read poisoned cache key {identity_key}")

    def forbidden_put(self: ArtifactStore, identity_key: str, payload: bytes) -> str:
        raise AssertionError(
            f"verification wrote poisoned cache key {identity_key} ({len(payload)} bytes)"
        )

    monkeypatch.setattr(ArtifactStore, "get", forbidden_get)
    monkeypatch.setattr(ArtifactStore, "put", forbidden_put)

    report = verify_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)

    assert report["passed"] is True
    cache_after = {
        path.relative_to(runtime._runs_root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in (runtime._runs_root / "cache").rglob("*.npz")
    }
    assert cache_after == cache_before


def test_ephemeral_is_upgraded_only_for_a_configured_downstream() -> None:
    assert (
        _upgrade_ephemeral_for_downstream(
            MaterializationPolicy.EPHEMERAL,
            downstream_configured=True,
        )
        is MaterializationPolicy.PERSIST
    )
    assert (
        _upgrade_ephemeral_for_downstream(
            MaterializationPolicy.EPHEMERAL,
            downstream_configured=False,
        )
        is MaterializationPolicy.EPHEMERAL
    )
    assert (
        _upgrade_ephemeral_for_downstream(
            MaterializationPolicy.CACHE_IF_EXPENSIVE,
            downstream_configured=True,
        )
        is MaterializationPolicy.CACHE_IF_EXPENSIVE
    )


def test_dirty_platform_attempt_is_permanently_noncanonical(tmp_path: Path) -> None:
    _case, runtime, config, _audit = _case_and_runtime(tmp_path)
    evidence = "synthetic dirty evidence"
    dirty = replace(
        runtime,
        _platform_identity=PlatformIdentity(
            "a" * 40,
            True,
            sha256(evidence.encode("utf-8")).hexdigest(),
        ),
        _git_diff=evidence,
    )

    manifest = run_experiment(config, runtime=dirty, heartbeat_interval_seconds=0.01)

    assert manifest.state.value == "COMPLETE"
    assert not manifest.canonical


def test_lockbox_requires_unlock_audits_before_loading_and_taints_manifest(
    tmp_path: Path,
) -> None:
    keys = tuple(range(3_100, 3_146))
    case = make_load_case(
        tmp_path,
        role=ViewRole.LOCKBOX,
        specs=(PartitionSpec("p0", "lockbox.parquet", keys, 3_100, 3_145, len(keys)),),
    )
    document = _config_document(case)
    _set_sensitive_role(document, role=ViewRole.LOCKBOX, first_key=3_100)
    config = _resolved(tmp_path, document)
    audit = RecordingAuditWriter()
    runtime = _runtime_for_case(case, tmp_path, audit=audit, name="lockbox-runtime")

    with pytest.raises(AccessDeniedError, match="unlock"):
        run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)
    assert [event["outcome"] for event in audit.events] == ["denied"]

    manifest = run_experiment(
        config,
        runtime=runtime,
        unlock_gated=True,
        heartbeat_interval_seconds=0.01,
    )

    assert [event["outcome"] for event in audit.events] == ["denied", "allowed"]
    assert manifest.oos_touched
    assert manifest.lockbox_touched
    assert manifest.declared_lockbox_evaluation


def test_oos_requires_reason_and_is_permanently_recorded(tmp_path: Path) -> None:
    keys = tuple(range(2_100, 2_146))
    case = make_load_case(
        tmp_path,
        role=ViewRole.OOS,
        specs=(PartitionSpec("p0", "oos.parquet", keys, 2_100, 2_145, len(keys)),),
    )
    document = _config_document(case)
    _set_sensitive_role(document, role=ViewRole.OOS, first_key=2_100)
    config = _resolved(tmp_path, document)
    audit = RecordingAuditWriter()
    runtime = _runtime_for_case(case, tmp_path, audit=audit, name="oos-runtime")

    manifest = run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)

    assert [event["outcome"] for event in audit.events] == ["allowed"]
    assert manifest.oos_touched
    assert not manifest.lockbox_touched


def test_unlock_flag_is_refused_for_train_without_an_audit_event(tmp_path: Path) -> None:
    _case, runtime, config, audit = _case_and_runtime(tmp_path)

    with pytest.raises(ContractViolationError, match="only for a fully gated"):
        run_experiment(
            config,
            runtime=runtime,
            unlock_gated=True,
            heartbeat_interval_seconds=0.01,
        )

    assert audit.events == []


def test_kill_9_interruption_then_rerun_reuses_published_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    document["execution"] = _execution_document()
    document["trade_evaluators"] = [
        {
            "name": "trade_summary_v1",
            "version": 1,
            "params": {"min_effective_trades": 0},
        }
    ]
    config = _resolved(tmp_path, document)
    ready = tmp_path / "third-fold-ready"
    child = """
import signal
import sys
from pathlib import Path

from trading_research.artifacts import ArtifactStore, PlatformIdentity
from trading_research.data import (
    DatasetRegistry,
    JsonlAuditWriter,
    MarketFrameLoader,
    read_split_policy,
)
from trading_research.experiments.config import load_resolved_config
from trading_research.experiments.orchestration import _RuntimeEnvironment, run_experiment
from trading_research.models.reference import StatelessRuleModel

config_path, data_root, runs_root, policy_path, audit_path, ready_path = map(Path, sys.argv[1:])
registry = DatasetRegistry.discover((data_root,))
policy = read_split_policy(policy_path)
runtime = _RuntimeEnvironment(
    _runs_root=runs_root,
    _registry=registry,
    _split_policy=policy,
    _loader=MarketFrameLoader(registry=registry, split_policy=policy),
    _store=ArtifactStore(runs_root),
    _audit_writer=JsonlAuditWriter(audit_path),
    _platform_identity=PlatformIdentity("a" * 40, False, None),
    _dependency_lock_sha256="b" * 64,
    _machine_identity={"machine": "synthetic"},
    _git_diff=None,
    _lockfile_in_sync=True,
    _full_integrity_verified=True,
)
original = StatelessRuleModel.infer
calls = 0
def blocking(self, view, fitted_state, inputs):
    global calls
    calls += 1
    if calls == 3:
        ready_path.write_text("ready")
        signal.pause()
    return original(self, view, fitted_state, inputs)
StatelessRuleModel.infer = blocking
run_experiment(
    load_resolved_config(config_path),
    runtime=runtime,
    heartbeat_interval_seconds=0.01,
)
"""
    process = subprocess.Popen(
        (
            sys.executable,
            "-c",
            child,
            str(tmp_path / "experiment.json"),
            str(case.source_directory.parent),
            str(runtime._runs_root),
            str(case.source_directory.parent / "policy.json"),
            str(tmp_path / "child-audit.jsonl"),
            str(ready),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10.0
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file()
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    cache_files = tuple((runtime._runs_root / "cache").rglob("*.npz"))
    assert len(cache_files) == 4
    interrupted_records = scan_runs(
        runtime._runs_root,
        now_unix_ns=time.time_ns() + 1_000_000_000,
        stale_after_ns=100_000_000,
    )
    assert len(interrupted_records) == 1
    assert interrupted_records[0]._state is RunState.INTERRUPTED
    interrupted_run = runtime._runs_root / "runs" / interrupted_records[0]._run_id
    plane_b_artifacts = tuple((interrupted_run / "artifacts").glob("*policy*.npz"))
    assert len(plane_b_artifacts) == 4
    assert not tuple((interrupted_run / "artifacts").glob("*.tmp"))
    for path in plane_b_artifacts:
        with np.load(path, allow_pickle=False) as payload:
            assert "__meta__" in payload.files

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("rerun recomputed a cached feature or fitted state")

    monkeypatch.setattr(LogReturn1, "compute", forbidden)
    monkeypatch.setattr(StatelessRuleModel, "fit", forbidden)

    manifest = run_experiment(config, runtime=runtime, heartbeat_interval_seconds=0.01)

    assert manifest.state is RunState.COMPLETE
    assert manifest.experiment_fingerprint == interrupted_records[0]._experiment_fingerprint
    assert tuple((runtime._runs_root / "cache").rglob("*.npz")) == cache_files
    assert audit.events == []


@pytest.mark.parametrize(
    ("model_name", "with_representation"),
    [
        ("supervised_mean_v1", False),
        ("reference_library_v1", True),
    ],
)
def test_supervised_and_representation_lifecycles_run_end_to_end(
    tmp_path: Path,
    model_name: str,
    with_representation: bool,
) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    outcomes = cast("list[dict[str, object]]", document["evaluation_outcomes"])
    document["training_outcome"] = dict(outcomes[0])
    document["model"] = {"name": model_name, "version": 1, "params": {}}
    if with_representation:
        document["representation"] = {
            "name": "trailing_standardizer_v1",
            "version": 1,
            "params": {},
        }
    policies = cast("list[dict[str, object]]", document["policies"])
    policies.append({"name": "abstain_v1", "version": 1, "params": {}})
    resolved = _resolved(tmp_path, document)

    manifest = run_experiment(
        resolved,
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )

    assert manifest.state is RunState.COMPLETE
    assert manifest.evaluation_count == 3
    assert len(manifest.policy_execution_json) == 6
    artifact_names = set(dict(manifest.artifact_hashes))
    assert (
        bool({name for name in artifact_names if "representation-frame" in name})
        is with_representation
    )
    assert audit.events == []


def test_descriptive_run_records_policies_but_no_evaluations(tmp_path: Path) -> None:
    case, runtime, _config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    document["evaluation_outcomes"] = []
    document["evaluators"] = []
    resolved = _resolved(tmp_path, document)

    manifest = run_experiment(
        resolved,
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )

    assert manifest.state is RunState.COMPLETE
    assert manifest.outcome_class.value == "descriptive"
    assert manifest.evaluation_count == 0
    assert len(manifest.policy_execution_json) == 3
    assert audit.events == []
