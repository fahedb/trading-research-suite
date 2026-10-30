"""Deterministic sweep planning and protected-role safeguards."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from typing import TYPE_CHECKING, cast

import pytest

from tests.load_fixtures import PartitionSpec, RecordingAuditWriter, make_load_case
from tests.test_experiments import _config_document, _runtime_for_case, _set_sensitive_role
from trading_research.core.errors import ContractViolationError
from trading_research.core.identity import ViewRole
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import _prepare_experiment
from trading_research.experiments.sweeps import (
    _count_oos_access_events,
    _expand_request,
    _plan_sweep,
    _read_sweep_plan,
    _SweepRuntimePaths,
)

if TYPE_CHECKING:
    from pathlib import Path

    from trading_research.experiments.orchestration import _RuntimeEnvironment
    from trading_research.experiments.sweeps import _SweepPlan


def _request(
    base: dict[str, object],
    *,
    sweep_id: str = "sweep-a",
    axes: list[dict[str, object]] | None = None,
    combination: str = "cartesian",
) -> dict[str, object]:
    return {
        "schema": "research-sweep-request-v1",
        "sweep_id": sweep_id,
        "base_config": deepcopy(base),
        "axes": deepcopy(
            axes
            if axes is not None
            else [{"json_pointer": "/seed", "values": [1, 2]}]
        ),
        "combination": combination,
    }


def _runtime_paths(
    tmp_path: Path,
    *,
    source_root: Path,
    runs_root: Path,
) -> _SweepRuntimePaths:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir(exist_ok=True)
    dependency_lock = (tmp_path / "dependency.lock").resolve()
    dependency_lock.write_text("synthetic lock\n", encoding="ascii")
    return _SweepRuntimePaths(
        (source_root.resolve(),),
        runs_root.resolve(),
        (source_root / "policy.json").resolve(),
        (tmp_path / "audit.jsonl").resolve(),
        repository,
        dependency_lock,
        5_000_000_000,
    )


def _train_context(
    tmp_path: Path,
) -> tuple[dict[str, object], _RuntimeEnvironment, _SweepRuntimePaths]:
    keys = tuple(range(100, 146))
    case = make_load_case(
        tmp_path,
        specs=(PartitionSpec("p0", "bars.parquet", keys, 100, 145, len(keys)),),
    )
    audit = RecordingAuditWriter()
    runtime = _runtime_for_case(case, tmp_path, audit=audit)
    paths = _runtime_paths(
        tmp_path,
        source_root=case.source_directory.parent,
        runs_root=runtime._runs_root,
    )
    return _config_document(case), runtime, paths


def _sensitive_context(
    tmp_path: Path,
    *,
    role: ViewRole,
    base_key: int,
) -> tuple[dict[str, object], _RuntimeEnvironment, _SweepRuntimePaths]:
    keys = tuple(range(base_key, base_key + 46))
    case = make_load_case(
        tmp_path,
        role=role,
        specs=(
            PartitionSpec(
                "p0",
                "bars.parquet",
                keys,
                base_key,
                base_key + 45,
                len(keys),
            ),
        ),
    )
    audit = RecordingAuditWriter()
    runtime = _runtime_for_case(case, tmp_path, audit=audit)
    document = _config_document(case)
    _set_sensitive_role(document, role=role, first_key=base_key)
    paths = _runtime_paths(
        tmp_path,
        source_root=case.source_directory.parent,
        runs_root=runtime._runs_root,
    )
    return document, runtime, paths


def _plan(
    request: dict[str, object],
    runtime: _RuntimeEnvironment,
    paths: _SweepRuntimePaths,
    *,
    spend_oos: bool = False,
    max_protected_children: int | None = None,
    protected_reason: str | None = None,
) -> _SweepPlan:
    return _plan_sweep(
        request,
        runtime=runtime,
        runtime_paths=paths,
        spend_oos=spend_oos,
        max_protected_children=max_protected_children,
        protected_reason=protected_reason,
        now_unix_ns=1_900_000_000_000_000_000,
    )


def test_expansion_is_deterministic_across_processes(tmp_path: Path) -> None:
    base, _runtime, _paths = _train_context(tmp_path)
    request = _request(
        base,
        axes=[
            {"json_pointer": "/seed", "values": [7, 8]},
            {"json_pointer": "/folds/eval_rows", "values": [4, 5]},
        ],
    )
    expected = _expand_request(request)
    request_path = (tmp_path / "request.json").resolve()
    request_path.write_text(json.dumps(request), encoding="ascii")
    code = (
        "import json,sys;"
        "from pathlib import Path;"
        "from trading_research.experiments.sweeps import _expand_request;"
        "d=json.loads(Path(sys.argv[1]).read_text());"
        "print(json.dumps(_expand_request(d),sort_keys=True,separators=(',',':')))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(request_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(completed.stdout)
    assert observed == json.loads(json.dumps(expected))


def test_duplicate_fingerprints_are_collapsed_and_recorded(tmp_path: Path) -> None:
    base, runtime, paths = _train_context(tmp_path)
    plan = _plan(
        _request(base, axes=[{"json_pointer": "/seed", "values": [1, 1, 2]}]),
        runtime,
        paths,
    )
    assert [child._child_ordinal for child in plan._children] == [0, 2]
    assert plan._document["duplicates_collapsed"] == [
        {"ordinal": 1, "duplicate_of_ordinal": 0}
    ]


def test_cartesian_expansion_uses_declared_last_axis_fastest(tmp_path: Path) -> None:
    base, _runtime, _paths = _train_context(tmp_path)
    expanded = _expand_request(
        _request(
            base,
            axes=[
                {"json_pointer": "/seed", "values": [1, 2]},
                {"json_pointer": "/folds/eval_rows", "values": [4, 5]},
            ],
        )
    )
    observed = []
    for _ordinal, document in expanded:
        folds = cast("dict[str, object]", document["folds"])
        observed.append((document["seed"], folds["eval_rows"]))
    assert observed == [(1, 4), (1, 5), (2, 4), (2, 5)]


def test_zip_expansion_pairs_declared_values_in_deterministic_order(
    tmp_path: Path,
) -> None:
    base, _runtime, _paths = _train_context(tmp_path)
    request = _request(
        base,
        axes=[
            {"json_pointer": "/seed", "values": [7, 8, 9]},
            {"json_pointer": "/folds/eval_rows", "values": [4, 5, 6]},
        ],
        combination="zip",
    )
    first = _expand_request(request)
    second = _expand_request(request)
    assert first == second
    observed = []
    for ordinal, document in first:
        folds = cast("dict[str, object]", document["folds"])
        observed.append((ordinal, document["seed"], folds["eval_rows"]))
    assert observed == [(0, 7, 4), (1, 8, 5), (2, 9, 6)]


def test_bracket_policy_parameters_sweep_through_the_existing_pointer_machinery(
    tmp_path: Path,
) -> None:
    """A per-trade bracket sweep needs no new sweep concept at all.

    Bracket shaping now lives in DecisionPolicy parameters, and a policy
    parameter is reached by exactly the same JSON pointer as any other config
    value. This pins that: no bracket-specific axis kind, no new expansion rule,
    and children that differ only in a bracket parameter really are distinct
    documents rather than collapsing into one.
    """
    base, _runtime, _paths = _train_context(tmp_path)
    base["policies"] = [
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
    expanded = _expand_request(
        _request(
            base,
            axes=[
                {
                    "json_pointer": "/policies/0/params/stop_loss_column",
                    "values": [None, "stop_distance"],
                },
                {
                    "json_pointer": "/policies/0/params/take_profit_column",
                    "values": [None, "target_distance"],
                },
            ],
        )
    )
    observed = []
    for _ordinal, document in expanded:
        policies = cast("list[dict[str, object]]", document["policies"])
        params = cast("dict[str, object]", policies[0]["params"])
        observed.append((params["stop_loss_column"], params["take_profit_column"]))
    assert observed == [
        (None, None),
        (None, "target_distance"),
        ("stop_distance", None),
        ("stop_distance", "target_distance"),
    ]
    # Four distinct documents, so four distinct child identities downstream.
    assert len({json.dumps(document, sort_keys=True) for _, document in expanded}) == 4


def test_zip_expansion_refuses_unequal_axis_lengths(tmp_path: Path) -> None:
    base, _runtime, _paths = _train_context(tmp_path)
    request = _request(
        base,
        axes=[
            {"json_pointer": "/seed", "values": [7, 8]},
            {"json_pointer": "/folds/eval_rows", "values": [4]},
        ],
        combination="zip",
    )
    with pytest.raises(ContractViolationError, match="zip sweep axes must have equal lengths"):
        _expand_request(request)


def test_planned_child_identity_is_the_ordinary_run_identity(tmp_path: Path) -> None:
    base, runtime, paths = _train_context(tmp_path)
    plan = _plan(_request(base), runtime, paths)
    for child in plan._children:
        config = _config_from_document(child._config_document)
        ordinary = _prepare_experiment(config, runtime)
        assert ordinary._identity._experiment_fingerprint == child._experiment_fingerprint


def test_sweep_identifier_never_enters_child_fingerprint(tmp_path: Path) -> None:
    base, runtime, paths = _train_context(tmp_path)
    first = _plan(_request(base, sweep_id="first"), runtime, paths)
    second = _plan(_request(base, sweep_id="second"), runtime, paths)
    assert [child._experiment_fingerprint for child in first._children] == [
        child._experiment_fingerprint for child in second._children
    ]


def test_planning_creates_no_run_and_reads_no_dataset_rows(tmp_path: Path) -> None:
    base, runtime, paths = _train_context(tmp_path)
    audit = cast("RecordingAuditWriter", runtime._audit_writer)
    before_audit = tuple(audit.events)
    _plan(_request(base), runtime, paths)
    assert not (paths._runs_root / "runs").exists()
    assert tuple(audit.events) == before_audit


def test_manifest_reader_refuses_tampering(tmp_path: Path) -> None:
    base, runtime, paths = _train_context(tmp_path)
    _plan(_request(base), runtime, paths)
    manifest = paths._runs_root / "sweeps" / "sweep-a" / "sweep.json"
    document = json.loads(manifest.read_text(encoding="ascii"))
    children = cast("list[dict[str, object]]", document["children"])
    children[0]["config_digest"] = "0" * 64
    manifest.chmod(0o600)
    manifest.write_text(json.dumps(document), encoding="ascii")
    with pytest.raises(ContractViolationError, match="digest differs"):
        _read_sweep_plan(paths._runs_root, "sweep-a")


def test_lockbox_sweep_is_refused(tmp_path: Path) -> None:
    base, runtime, paths = _sensitive_context(
        tmp_path,
        role=ViewRole.LOCKBOX,
        base_key=3_100,
    )
    with pytest.raises(ContractViolationError, match="LOCKBOX sweeps are refused"):
        _plan(_request(base), runtime, paths)


def test_oos_sweep_requires_explicit_bounded_spend(tmp_path: Path) -> None:
    base, runtime, paths = _sensitive_context(
        tmp_path,
        role=ViewRole.OOS,
        base_key=2_100,
    )
    with pytest.raises(ContractViolationError, match="requires explicit spend_oos"):
        _plan(_request(base, sweep_id="no-spend"), runtime, paths)
    with pytest.raises(ContractViolationError, match="exceeds max_protected_children"):
        _plan(
            _request(base, sweep_id="too-many"),
            runtime,
            paths,
            spend_oos=True,
            max_protected_children=1,
            protected_reason="Approved bounded OOS sweep",
        )
    plan = _plan(
        _request(base, sweep_id="bounded"),
        runtime,
        paths,
        spend_oos=True,
        max_protected_children=2,
        protected_reason="Approved bounded OOS sweep",
    )
    assert plan._document["protection"] == {
        "spend_oos": True,
        "max_protected_children": 2,
        "reason": "Approved bounded OOS sweep",
        "oos_child_count": 2,
        "oos_access_events_at_plan": 0,
    }


def test_oos_audit_counter_reads_the_audit_writers_utf8_contract(tmp_path: Path) -> None:
    audit_path = (tmp_path / "audit.jsonl").resolve()
    event = {
        "schema_version": "1",
        "event_type": "dataset_access_decision",
        "event_id": "00000000-0000-4000-8000-000000000001",
        "outcome": "allowed",
        "classification": "oos_explicit",
        "reason": "בדיקת OOS מאושרת",
    }
    audit_path.write_text(
        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert _count_oos_access_events(audit_path) == 1
