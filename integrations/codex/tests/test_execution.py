"""General input checks, immutable preparation and launch exclusion."""

import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trading_research_mcp.demo import create_demo
from trading_research_mcp.execution import LIMITS, ExperimentService
from trading_research_mcp.service import Refused, Settings, atomic_json, digest, encoded
from trading_research_mcp.specification import (
    Approval,
    ExecutionLimits,
    ExperimentSpec,
    review_spec,
)

SUITE = Path(__file__).resolve().parents[3]
RUN = "01M678S7BV30M2V4QFTSFMDYF7"


@pytest.fixture
def example(tmp_path):
    root = tmp_path / "demo"
    create_demo(root, SUITE)
    service = ExperimentService(Settings.load(root / "codex.json"))
    spec = ExperimentSpec.model_validate_json((root / "specification.json").read_text())
    service.specs.freeze(
        spec,
        Approval(
            spec_sha256=review_spec(spec)["spec_sha256"],
            user_message="Synthetic test authorization",
            scope="integrate_and_run",
            limits=ExecutionLimits(**LIMITS),
        ),
    )
    return service, json.loads((root / "request.json").read_text()), root


def test_actual_check_calculates_features_but_requires_checkout_verification(example):
    service, request, _root = example
    result = service.check(**request)
    assert not result["ready_to_run"]
    assert result["engine"]["ready"]
    assert len(result["engine"]["folds"]) == 13
    assert result["engine"]["feature_columns"] == ["rolling_close_position"]
    assert any("experiment_verify" in message for message in result["blockers"])
    assert not (service.settings.output_root / "runs").exists()
    assert "plan_id" not in service.prepare(**request)


@pytest.mark.parametrize(
    "problem",
    [
        "missing_feature",
        "missing_history",
        "missing_outcome_history",
        "mapping",
        "protected",
        "kind",
        "policy_path",
    ],
)
def test_invalid_inputs_never_produce_a_prepared_plan(example, problem):
    service, request, _root = example
    if problem == "missing_feature":
        request["config"]["features"][0]["name"] = "unimplemented_feature"
    elif problem == "missing_history":
        request["config"]["features"][0]["params"]["window"] = 9999
    elif problem == "missing_outcome_history":
        request["config"]["evaluation_outcomes"][0]["params"]["horizon_rows"] = 9999
    elif problem == "mapping":
        request["bindings"]["close-position"] = ["/model"]
    elif problem == "protected":
        request["config"]["dataset"]["role"] = "lockbox"
    elif problem == "kind":
        request["config"]["execution"] = {"unexpected": "simulation"}
    else:
        request["policy_name"] = "../../escape"
    result = service.prepare(**request)
    assert not result["ready_to_run"]
    assert result["blockers"]
    assert "plan_id" not in result
    assert not (service.settings.output_root / "runs").exists()


def test_corrupt_data_is_refused_on_a_fresh_check(example):
    service, request, root = example
    assert service.check(**request)["engine"]["ready"]
    with (root / "data/source/synthetic-bars.parquet").open("ab") as stream:
        stream.write(b"corrupted")
    result = service.check(**request)
    assert not result["ready_to_run"]
    assert "engine" not in result
    assert not (service.settings.output_root / "runs").exists()


def test_missing_source_column_and_integration_only_scope_are_blockers(example):
    service, request, _root = example
    frozen = service.specs.get(request["experiment_id"])
    document = frozen["specification"]
    document["experiment_id"] = "missing-source"
    document["data"]["required_columns"].append("bid")
    spec = ExperimentSpec.model_validate(document)
    service.specs.freeze(
        spec,
        Approval(
            spec_sha256=review_spec(spec)["spec_sha256"],
            user_message="Integrate only test",
            scope="integrate",
            limits=ExecutionLimits(**LIMITS),
        ),
    )
    request["experiment_id"] = spec.experiment_id
    assert "source columns" in str(service.check(**request)["blockers"])
    document["experiment_id"] = "integrate-only"
    document["data"]["required_columns"].remove("bid")
    spec = ExperimentSpec.model_validate(document)
    service.specs.freeze(
        spec,
        Approval(
            spec_sha256=review_spec(spec)["spec_sha256"],
            user_message="Integrate only test",
            scope="integrate",
            limits=ExecutionLimits(**LIMITS),
        ),
    )
    request["experiment_id"] = spec.experiment_id
    assert "integration only" in str(service.check(**request)["blockers"])


@pytest.fixture
def prepared(example, monkeypatch):
    service, request, root = example
    frozen = service.specs.get(request["experiment_id"])
    evidence = {
        "ready_to_run": True,
        "blockers": [],
        "spec_sha256": frozen["spec_sha256"],
        "config_sha256": digest(encoded(request["config"])),
        "engine": {"experiment_fingerprint": "a" * 64},
        "policy_relative": "config/split-policies/synthetic-authoring-v1.json",
        "limits": LIMITS,
    }
    monkeypatch.setattr(service, "check", lambda *_args, **_kwargs: copy.deepcopy(evidence))
    monkeypatch.setattr("trading_research_mcp.execution.shutil.which", lambda _name: "/bin/tmux")
    plan = service.prepare(**request)
    return service, request, root, plan, evidence


def test_simultaneous_retries_share_one_durable_launch(prepared, monkeypatch):
    service, _request, _root, plan, _evidence = prepared
    calls = []

    def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return {"run_id": RUN, "state": "PENDING", "launcher": "test"}

    monkeypatch.setattr(service, "_engine", launch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: service.start(plan["plan_id"]), range(4)))
    assert len(calls) == 1
    assert receipts == [receipts[0]] * 4
    assert calls[0][1]["expected_fingerprint"] == "a" * 64


def test_stale_preparation_and_changed_config_are_refused(prepared):
    service, _request, _root, plan, evidence = prepared
    evidence["engine"]["experiment_fingerprint"] = "b" * 64
    with pytest.raises(Refused, match="Prepared inputs changed"):
        service.start(plan["plan_id"])
    path = service.settings.workspace / "experiment-plans" / plan["plan_id"] / "config.json"
    config = json.loads(path.read_text())
    config["seed"] = 42
    atomic_json(path, config)
    with pytest.raises(Refused, match="configuration changed"):
        service.start(plan["plan_id"])
    assert not list((service.settings.workspace / "launches").glob("*.json"))


def test_uncertain_launch_is_durable_and_blocks_further_plans(prepared, monkeypatch):
    service, request, _root, plan, _evidence = prepared

    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("engine", 60)

    monkeypatch.setattr(service, "_engine", fail)
    first = service.start(plan["plan_id"])
    assert first["launch_state"] == "uncertain"
    assert service.start(plan["plan_id"]) == first
    other = service.prepare(**request)
    with pytest.raises(Refused, match="uncertain"):
        service.start(other["plan_id"])


def test_attempt_budget_cannot_be_reset_by_preparing_again(prepared, monkeypatch):
    service, request, _root, plan, _evidence = prepared
    monkeypatch.setattr(service, "_engine", lambda *_a, **_kw: {"run_id": RUN, "state": "PENDING"})
    service.start(plan["plan_id"])
    monkeypatch.setattr(
        "trading_research_mcp.execution.recorded_state", lambda *_a: {"state": "COMPLETE"}
    )
    other = service.prepare(**request)
    with pytest.raises(Refused, match="budget is exhausted"):
        service.start(other["plan_id"])


def test_active_gaps_launch_also_blocks_general_launch(prepared):
    service, _request, _root, plan, _evidence = prepared
    launches = service._mkdir("launches")
    atomic_json(launches / ("f" * 32 + ".json"), {"launch_state": "launched", "run_id": RUN})
    run_dir = service._mkdir("engine/runs/" + RUN)
    atomic_json(
        run_dir / "state.json",
        {"schema": "research-run-state-v2", "run_id": RUN, "state": "RUNNING"},
    )
    with pytest.raises(Refused, match="still active"):
        service.start(plan["plan_id"])


def test_checkout_verification_records_failures_and_protects_its_log(example, monkeypatch):
    service, _request, _root = example
    identity = {"git_commit": "a" * 40, "dirty": False}
    monkeypatch.setattr(service, "_checkout", lambda **_kw: dict(identity))
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert service.verify_checkout()["passed"] is False
    assert len(calls) == 1
    assert not list((service.settings.workspace / "verification").glob("*.json"))
    monkeypatch.setattr(subprocess, "run", lambda argv, **_kw: subprocess.CompletedProcess(argv, 0))
    receipt = service.verify_checkout()
    assert receipt["passed"]
    assert len(receipt["checks"]) == 3
    assert service.verify_checkout() == receipt
    log = service.settings.workspace / "verification" / receipt["log_name"]
    log.write_text("altered")
    with pytest.raises(Refused, match="log changed"):
        service.verify_checkout()


def test_demo_refuses_to_overwrite_existing_data(example):
    _service, _request, root = example
    before = (root / "data/source/synthetic-bars.parquet").read_bytes()
    with pytest.raises(Refused, match="already exists"):
        create_demo(root, SUITE)
    assert (root / "data/source/synthetic-bars.parquet").read_bytes() == before


def test_legacy_authoring_record_needs_numerical_limits_before_loading(example):
    service, request, _root = example
    document = service.specs.get(request["experiment_id"])["specification"]
    document["experiment_id"] = "legacy-design"
    spec = ExperimentSpec.model_validate(document)
    service.specs.freeze(
        spec,
        Approval(
            spec_sha256=review_spec(spec)["spec_sha256"],
            user_message="Legacy integration instruction",
            scope="integrate_and_run",
        ),
    )
    assert service.specs.get(spec.experiment_id)["spec_sha256"] == review_spec(spec)["spec_sha256"]
    request["experiment_id"] = spec.experiment_id
    assert "structured execution limits" in str(service.check(**request)["blockers"])
    assert not service.settings.output_root.exists()


def test_frozen_smaller_row_budget_is_enforced(example):
    service, request, _root = example
    document = service.specs.get(request["experiment_id"])["specification"]
    document["experiment_id"] = "small-budget"
    spec = ExperimentSpec.model_validate(document)
    service.specs.freeze(
        spec,
        Approval(
            spec_sha256=review_spec(spec)["spec_sha256"],
            user_message="Use a small test budget",
            scope="integrate_and_run",
            limits=ExecutionLimits(**{**LIMITS, "max_rows": 100}),
        ),
    )
    request["experiment_id"] = spec.experiment_id
    assert "row budget" in str(service.check(**request)["blockers"])
    assert not (service.settings.output_root / "runs").exists()


def test_data_dependency_cannot_be_silently_skipped(example):
    service, request, _root = example
    document = service.specs.get(request["experiment_id"])["specification"]
    document["experiment_id"] = "missing-data-dependency"
    document["components"].append(
        {
            "name": "bid-source",
            "kind": "data",
            "definition": "Observed bid quotes",
            "inputs": ["bid"],
            "availability": "obtain",
        }
    )
    spec = ExperimentSpec.model_validate(document)
    service.specs.freeze(
        spec,
        Approval(
            spec_sha256=review_spec(spec)["spec_sha256"],
            user_message="Test the missing source dependency",
            scope="integrate_and_run",
            limits=ExecutionLimits(**LIMITS),
        ),
    )
    request["experiment_id"] = spec.experiment_id
    request["bindings"]["bid-source"] = ["/dataset"]
    assert "Data dependency" in str(service.check(**request)["blockers"])
