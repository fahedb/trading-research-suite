"""Slice-3H experiment identity and files-first run-record goldens."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import stat
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

import trading_research.experiments.runs as runs_module
from trading_research.artifacts.identity import PlatformIdentity
from trading_research.core import (
    ContractViolationError,
    DatasetIdentity,
    MetricValue,
    ParamValue,
    StageOrigin,
)
from trading_research.experiments.evaluation import (
    _descriptive_inventory,
    _EvaluationInventory,
    _EvaluationInventoryEntry,
    _PolicyExecutionRecord,
    _RunOutcomeClass,
)
from trading_research.experiments.run_identity import (
    _ComponentSource,
    _ExperimentIdentity,
    _run_id_from_parts,
    _validated_run_id,
)
from trading_research.experiments.runs import (
    RunState,
    _finish_run,
    _heartbeat_run,
    _record_determinism_report,
    _record_evaluation_inventory,
    _record_run_access,
    _register_new_run,
    _register_run_id,
    _RunCorruptionError,
    _RunStateError,
    _start_run,
    scan_runs,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import FrameType

BASE_NS = 1_800_000_000_000_000_000
STALE_NS = 60_000_000_000


@contextmanager
def _hard_test_deadline(seconds: float, *, detail: str) -> Iterator[None]:
    def expired(_signum: int, _frame: FrameType | None) -> None:
        raise AssertionError(detail)

    previous_handler = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _component(
    *,
    component_kind: str = "research_model",
    component_id: str = "stateless_rule_v1",
    version: int = 1,
    params: dict[str, ParamValue] | None = None,
    source_sha256: str = "2" * 64,
) -> _ComponentSource:
    return _ComponentSource(
        component_kind=component_kind,
        component_id=component_id,
        version=version,
        params={} if params is None else params,
        source_sha256=source_sha256,
    )


def _identity(
    *,
    config: dict[str, object] | None = None,
    sources: tuple[DatasetIdentity, ...] | None = None,
    components: tuple[_ComponentSource, ...] | None = None,
    commit: str = "a" * 40,
    dirty_diff: str | None = None,
    dependency_lock_sha256: str = "3" * 64,
    seed: int | None = 7,
    machine: str = "machine-a",
    lockfile_in_sync: bool = True,
    full_integrity_verified: bool = True,
    declared_lockbox_evaluation: bool = False,
) -> _ExperimentIdentity:
    diff_sha256 = (
        None
        if dirty_diff is None
        else hashlib.sha256(dirty_diff.encode("utf-8")).hexdigest()
    )
    return _ExperimentIdentity(
        resolved_config=(
            {"hypothesis": "synthetic run", "model": "stateless_rule_v1"}
            if config is None
            else config
        ),
        dataset_identities=(
            (
                DatasetIdentity("TRAIN_A", "1" * 64),
                DatasetIdentity("TRAIN_B", "4" * 64),
            )
            if sources is None
            else sources
        ),
        components=(
            (
                _component(
                    component_kind="feature_provider",
                    component_id="log_return_1",
                    params={"window": 1},
                ),
                _component(),
            )
            if components is None
            else components
        ),
        platform_identity=PlatformIdentity(
            commit,
            dirty_diff is not None,
            diff_sha256,
        ),
        dependency_lock_sha256=dependency_lock_sha256,
        seed=seed,
        machine_identity={"machine": machine, "python": "3.13"},
        git_diff=dirty_diff,
        lockfile_in_sync=lockfile_in_sync,
        full_integrity_verified=full_integrity_verified,
        declared_lockbox_evaluation=declared_lockbox_evaluation,
    )


def _run_id(index: int) -> str:
    return _run_id_from_parts(
        timestamp_ms=index,
        randomness=index.to_bytes(10, "big"),
    )


def _evaluated_inventory(*, value: float = float("nan")) -> _EvaluationInventory:
    outcome = StageOrigin(
        "forward_log_return_v1",
        1,
        {
            "boundary_policy": "nan",
            "decision_delay_rows": 1,
            "horizon_rows": 2,
            "reference_price_column": "close",
        },
    )
    evaluator = StageOrigin(
        "rank_ic_v1",
        1,
        {
            "min_effective_samples": 2,
            "outcome_column": "target.forward_log_return",
            "output_column": "score",
        },
    )
    entry = _EvaluationInventoryEntry(
        _fitted_state_identity_key="5" * 64,
        _model_output_artifact_identity_key="6" * 64,
        _evaluated_output_columns=("score",),
        _outcome_origin=outcome,
        _horizon_rows=2,
        _decision_delay_rows=1,
        _evaluator_origin=evaluator,
        _metric_names=("rank_ic",),
        _comparison_tolerance=None,
        _metrics=(MetricValue("rank_ic", value, 4),),
    )
    policy = _PolicyExecutionRecord(
        _origin=StageOrigin(
            "threshold_v1",
            1,
            {"output_column": "score", "threshold": 0.1},
        ),
        _intent_schema_id="target_exposure_intent_v1",
        _intent_rows=3,
        _first_key=10,
        _last_key=12,
    )
    return _EvaluationInventory(
        _outcome_class=_RunOutcomeClass.EVALUATED,
        _entries=(entry,),
        _policy_executions=(policy,),
        _failure_reason=None,
    )


def _register(
    root: Path,
    *,
    run_index: int,
    identity: _ExperimentIdentity | None = None,
) -> str:
    run_id = _run_id(run_index)
    _register_run_id(
        runs_root=root,
        identity=_identity() if identity is None else identity,
        run_id=run_id,
        now_unix_ns=BASE_NS + run_index,
    )
    return run_id


def _start(root: Path, run_id: str, *, offset: int = 100) -> None:
    _start_run(
        runs_root=root,
        run_id=run_id,
        now_unix_ns=BASE_NS + offset,
    )


def test_experiment_fingerprint_sensitivity_and_insensitivity_matrix() -> None:
    base = _identity()
    same_mapping_other_order = _identity(
        config={"model": "stateless_rule_v1", "hypothesis": "synthetic run"}
    )
    other_machine = _identity(machine="machine-b")
    operationally_noncanonical = _identity(
        lockfile_in_sync=False,
        full_integrity_verified=False,
    )
    assert {
        base._experiment_fingerprint,
        same_mapping_other_order._experiment_fingerprint,
        other_machine._experiment_fingerprint,
        operationally_noncanonical._experiment_fingerprint,
    } == {base._experiment_fingerprint}
    assert base._identity_document_json() != other_machine._identity_document_json()
    assert base._summary._registration_canonical is True
    assert operationally_noncanonical._summary._registration_canonical is False

    variants = (
        _identity(config={"hypothesis": "changed", "model": "stateless_rule_v1"}),
        _identity(
            sources=(
                DatasetIdentity("TRAIN_A", "1" * 64),
                DatasetIdentity("TRAIN_B", "7" * 64),
            )
        ),
        _identity(
            sources=(
                DatasetIdentity("TRAIN_B", "4" * 64),
                DatasetIdentity("TRAIN_A", "1" * 64),
            )
        ),
        _identity(components=(_component(component_id="changed"),)),
        _identity(components=(_component(version=2),)),
        _identity(components=(_component(params={"threshold": 1}),)),
        _identity(components=(_component(source_sha256="8" * 64),)),
        _identity(
            components=(
                _component(),
                _component(
                    component_kind="feature_provider",
                    component_id="log_return_1",
                    params={"window": 1},
                ),
            )
        ),
        _identity(commit="b" * 40),
        _identity(dirty_diff="diff --git a/x b/x\n+changed\n"),
        _identity(dependency_lock_sha256="9" * 64),
        _identity(seed=8),
        _identity(declared_lockbox_evaluation=True),
    )
    assert all(
        variant._experiment_fingerprint != base._experiment_fingerprint
        for variant in variants
    )
    assert len({variant._experiment_fingerprint for variant in variants}) == len(
        variants
    )


def test_dirty_identity_requires_exact_captured_diff_hash() -> None:
    with pytest.raises(ContractViolationError, match="captured git_diff"):
        _ExperimentIdentity(
            resolved_config={"hypothesis": "x"},
            dataset_identities=(DatasetIdentity("TRAIN", "1" * 64),),
            components=(_component(),),
            platform_identity=PlatformIdentity("a" * 40, True, "2" * 64),
            dependency_lock_sha256="3" * 64,
            seed=None,
            machine_identity={"machine": "test"},
            git_diff="different bytes",
            lockfile_in_sync=True,
            full_integrity_verified=True,
            declared_lockbox_evaluation=False,
        )


def test_ulid_encoding_is_canonical_and_time_ordered() -> None:
    assert _run_id_from_parts(timestamp_ms=0, randomness=b"\x00" * 10) == "0" * 26
    first = _run_id_from_parts(timestamp_ms=1, randomness=b"\xff" * 10)
    second = _run_id_from_parts(timestamp_ms=2, randomness=b"\x00" * 10)
    assert first < second
    assert _validated_run_id(first) == first
    for invalid in (first.lower(), "8" + first[1:], "I" + first[1:], first[:-1]):
        with pytest.raises(ContractViolationError, match="ULID"):
            _validated_run_id(invalid)


def test_registration_layout_is_exact_and_attempt_is_query_time_only(
    tmp_path: Path,
) -> None:
    identity = _identity()
    first = _register(tmp_path, run_index=1, identity=identity)
    second = _register(tmp_path, run_index=2, identity=identity)
    expected_files = {
        "config.resolved.json",
        "heartbeat.json",
        "identity.json",
        "state.json",
    }
    expected_directories = {"artifacts", "logs", "metrics"}
    for run_id in (first, second):
        run_dir = tmp_path / "runs" / run_id
        assert {path.name for path in run_dir.iterdir() if path.is_file()} == expected_files
        assert {
            path.name for path in run_dir.iterdir() if path.is_dir()
        } == expected_directories
        all_json = "".join(path.read_text() for path in run_dir.glob("*.json"))
        assert "attempt_number" not in all_json
        state = json.loads((run_dir / "state.json").read_text())
        assert state["state"] == "PENDING"

    records = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 10,
        stale_after_ns=STALE_NS,
    )
    assert [record._run_id for record in records] == [first, second]
    assert [record._attempt_number for record in records] == [1, 2]


def test_run_file_limit_is_shared_by_immutable_writer_and_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs_module, "_RUN_FILE_LIMIT_BYTES", 32)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ContractViolationError, match="32-byte V1 limit"):
            runs_module._publish_immutable_bytes_at(directory_fd, "oversized.bin", b"x" * 33)
        assert not (tmp_path / "oversized.bin").exists()

        payload = b"x" * 32
        runs_module._publish_immutable_bytes_at(directory_fd, "bounded.bin", payload)
        assert runs_module._read_regular_bytes_at(directory_fd, "bounded.bin") == payload
    finally:
        os.close(directory_fd)


def test_every_state_transition_and_terminal_immutability_are_enforced(
    tmp_path: Path,
) -> None:
    run_id = _register(tmp_path, run_index=1)
    with pytest.raises(_RunStateError, match="RUNNING"):
        _finish_run(
            runs_root=tmp_path,
            run_id=run_id,
            terminal_state=RunState.FAILED,
            inventory=None,
            failure_reason="not started",
            now_unix_ns=BASE_NS + 10,
        )
    _start(tmp_path, run_id)
    with pytest.raises(_RunStateError, match="PENDING"):
        _start(tmp_path, run_id, offset=101)
    _record_run_access(
        runs_root=tmp_path,
        run_id=run_id,
        oos_touched=True,
        lockbox_touched=False,
        now_unix_ns=BASE_NS + 102,
    )
    _record_run_access(
        runs_root=tmp_path,
        run_id=run_id,
        oos_touched=False,
        lockbox_touched=False,
        now_unix_ns=BASE_NS + 103,
    )
    manifest = _finish_run(
        runs_root=tmp_path,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_descriptive_inventory(policy_executions=()),
        failure_reason=None,
        now_unix_ns=BASE_NS + 104,
    )
    assert manifest.state is RunState.COMPLETE
    assert manifest.oos_touched is True
    run_dir = tmp_path / "runs" / run_id
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o500
    assert stat.S_IMODE((run_dir / "state.json").stat().st_mode) == 0o400
    with pytest.raises(PermissionError):
        (run_dir / "state.json").write_text("mutated")
    with pytest.raises(_RunStateError):
        _heartbeat_run(
            runs_root=tmp_path,
            run_id=run_id,
            now_unix_ns=BASE_NS + 105,
        )
    with pytest.raises(_RunStateError):
        _record_run_access(
            runs_root=tmp_path,
            run_id=run_id,
            oos_touched=True,
            lockbox_touched=True,
            now_unix_ns=BASE_NS + 105,
        )
    with pytest.raises(_RunStateError):
        _finish_run(
            runs_root=tmp_path,
            run_id=run_id,
            terminal_state=RunState.COMPLETE,
            inventory=_descriptive_inventory(policy_executions=()),
            failure_reason=None,
            now_unix_ns=BASE_NS + 105,
        )


def test_heartbeat_is_an_mtime_touch_with_fixed_content(tmp_path: Path) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    heartbeat = tmp_path / "runs" / run_id / "heartbeat.json"
    before = heartbeat.read_bytes()
    _heartbeat_run(
        runs_root=tmp_path,
        run_id=run_id,
        now_unix_ns=BASE_NS + 200,
    )
    assert heartbeat.read_bytes() == before
    assert heartbeat.stat().st_mtime_ns == BASE_NS + 200
    with pytest.raises(_RunStateError, match="backward"):
        _heartbeat_run(
            runs_root=tmp_path,
            run_id=run_id,
            now_unix_ns=BASE_NS + 199,
        )


def test_inventory_and_policy_execution_are_persisted_exactly(tmp_path: Path) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    inventory = _evaluated_inventory()
    manifest = _finish_run(
        runs_root=tmp_path,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=inventory,
        failure_reason=None,
        now_unix_ns=BASE_NS + 200,
    )
    run_dir = tmp_path / "runs" / run_id
    persisted = json.loads((run_dir / "evaluation_inventory.json").read_text())
    metric = persisted["entries"][0]["metrics"][0]
    assert metric["value_float64_bits"] == struct.pack(">d", float("nan")).hex()
    assert "value" not in metric
    assert len(persisted["policy_executions"]) == 1
    manifest_json = json.loads((run_dir / "manifest.json").read_text())
    assert manifest.evaluation_count == 1
    assert len(manifest_json["evaluation"]["policy_executions"]) == 1
    record = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 300,
        stale_after_ns=STALE_NS,
    )[0]
    assert record._evaluation_count == 1
    assert record._policy_execution_count == 1
    assert record._outcome_class is _RunOutcomeClass.EVALUATED


def test_scan_runs_reads_a_phase3_v1_terminal_directory_golden(tmp_path: Path) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    _finish_run(
        runs_root=tmp_path,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=0.25),
        failure_reason=None,
        now_unix_ns=BASE_NS + 200,
    )
    run_dir = tmp_path / "runs" / run_id
    inventory_path = run_dir / "evaluation_inventory.json"
    manifest_path = run_dir / "manifest.json"
    os.chmod(run_dir, 0o700)
    os.chmod(inventory_path, 0o600)
    os.chmod(manifest_path, 0o600)

    inventory = json.loads(inventory_path.read_text())
    inventory["schema"] = "plane-a-evaluation-inventory-v1"
    for entry in inventory["entries"]:
        assert entry.pop("plane") == "A"
    inventory_bytes = (
        json.dumps(inventory, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        .encode("ascii")
        + b"\n"
    )
    inventory_path.write_bytes(inventory_bytes)

    manifest = json.loads(manifest_path.read_text())
    manifest["schema"] = "research-run-manifest-v1"
    assert manifest.pop("simulation") is None
    assert manifest["access"].pop("audit_refs") == []
    manifest["files"]["root"]["evaluation_inventory.json"] = hashlib.sha256(
        inventory_bytes
    ).hexdigest()
    manifest_path.write_bytes(
        json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        .encode("ascii")
        + b"\n"
    )

    record = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 300,
        stale_after_ns=STALE_NS,
    )[0]
    assert record._run_id == run_id
    assert record._state is RunState.COMPLETE
    assert record._evaluation_count == 1
    assert record._outcome_class is _RunOutcomeClass.EVALUATED


def test_determinism_report_is_immutable_and_manifest_hashed(tmp_path: Path) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    report = {
        "schema": "determinism-report-v1",
        "passed": True,
        "observed_deviation": 0.0,
    }
    _record_determinism_report(
        runs_root=tmp_path,
        run_id=run_id,
        report=report,
    )
    _record_determinism_report(
        runs_root=tmp_path,
        run_id=run_id,
        report=report,
    )
    with pytest.raises(_RunCorruptionError, match="immutable run file"):
        _record_determinism_report(
            runs_root=tmp_path,
            run_id=run_id,
            report={**report, "passed": False},
        )

    manifest = _finish_run(
        runs_root=tmp_path,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_descriptive_inventory(policy_executions=()),
        failure_reason=None,
        now_unix_ns=BASE_NS + 200,
    )
    report_path = tmp_path / "runs" / run_id / "determinism_report.json"
    assert json.loads(report_path.read_text()) == report
    root_hashes = dict(manifest.root_file_hashes)
    assert root_hashes["determinism_report.json"] == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    with pytest.raises(_RunStateError, match="only while RUNNING"):
        _record_determinism_report(
            runs_root=tmp_path,
            run_id=run_id,
            report=report,
        )


def test_evaluated_inventory_survives_a_later_interruption(tmp_path: Path) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    _record_evaluation_inventory(
        runs_root=tmp_path,
        run_id=run_id,
        inventory=_evaluated_inventory(value=-0.5),
    )
    record = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 100 + STALE_NS + 1,
        stale_after_ns=STALE_NS,
    )[0]
    assert record._state is RunState.INTERRUPTED
    assert record._outcome_class is _RunOutcomeClass.EVALUATED
    assert record._evaluation_count == 1
    assert record._policy_execution_count == 1


def test_canonical_git_and_gated_access_flags_are_terminally_visible(
    tmp_path: Path,
) -> None:
    cases = (
        (_identity(dirty_diff="diff --git a/x b/x\n+dirty\n"), False, False),
        (_identity(), True, False),
        (_identity(declared_lockbox_evaluation=True), True, True),
    )
    observed: list[tuple[bool, bool, bool]] = []
    for index, (identity, touch_lockbox, expected_canonical) in enumerate(cases, 1):
        run_id = _register(tmp_path, run_index=index, identity=identity)
        _start(tmp_path, run_id, offset=100 + index)
        _record_run_access(
            runs_root=tmp_path,
            run_id=run_id,
            oos_touched=True,
            lockbox_touched=touch_lockbox,
            now_unix_ns=BASE_NS + 150 + index,
        )
        manifest = _finish_run(
            runs_root=tmp_path,
            run_id=run_id,
            terminal_state=RunState.COMPLETE,
            inventory=_descriptive_inventory(policy_executions=()),
            failure_reason=None,
            now_unix_ns=BASE_NS + 200 + index,
        )
        assert manifest.canonical is expected_canonical
        observed.append(
            (manifest.canonical, manifest.oos_touched, manifest.lockbox_touched)
        )
        identity_json = json.loads(
            (tmp_path / "runs" / run_id / "identity.json").read_text()
        )
        if identity._summary._platform_identity.git_dirty:
            assert identity_json["git_diff"] == "diff --git a/x b/x\n+dirty\n"
            assert identity_json["fingerprint_inputs"]["platform"][
                "diff_sha256"
            ] == hashlib.sha256(identity_json["git_diff"].encode()).hexdigest()
    assert observed == [
        (False, True, False),
        (False, True, True),
        (True, True, True),
    ]


def test_run_record_scan_golden_reconstructs_all_known_states_from_files(
    tmp_path: Path,
) -> None:
    evaluated = _register(tmp_path, run_index=1)
    descriptive = _register(tmp_path, run_index=2)
    failed = _register(tmp_path, run_index=3)
    interrupted = _register(tmp_path, run_index=4)
    for run_id in (evaluated, descriptive, failed, interrupted):
        _start(tmp_path, run_id, offset=100 + int(run_id[-1], 32))
    _finish_run(
        runs_root=tmp_path,
        run_id=evaluated,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=1.0),
        failure_reason=None,
        now_unix_ns=BASE_NS + 300,
    )
    _finish_run(
        runs_root=tmp_path,
        run_id=descriptive,
        terminal_state=RunState.COMPLETE,
        inventory=_descriptive_inventory(policy_executions=()),
        failure_reason=None,
        now_unix_ns=BASE_NS + 301,
    )
    _finish_run(
        runs_root=tmp_path,
        run_id=failed,
        terminal_state=RunState.FAILED,
        inventory=None,
        failure_reason="dependency unavailable",
        now_unix_ns=BASE_NS + 302,
    )
    _finish_run(
        runs_root=tmp_path,
        run_id=interrupted,
        terminal_state=RunState.INTERRUPTED,
        inventory=None,
        failure_reason="received SIGTERM",
        now_unix_ns=BASE_NS + 303,
    )

    records = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 400,
        stale_after_ns=STALE_NS,
    )
    assert [
        (
            record._run_id,
            record._attempt_number,
            record._state.value,
            None if record._outcome_class is None else record._outcome_class.value,
            record._evaluation_count,
            record._policy_execution_count,
            record._failure_reason,
        )
        for record in records
    ] == [
        (evaluated, 1, "COMPLETE", "evaluated", 1, 1, None),
        (descriptive, 2, "COMPLETE", "descriptive", 0, 0, None),
        (
            failed,
            3,
            "FAILED",
            "pre_evaluation_failure",
            0,
            0,
            "dependency unavailable",
        ),
        (
            interrupted,
            4,
            "INTERRUPTED",
            "pre_evaluation_failure",
            0,
            0,
            "received SIGTERM",
        ),
    ]


def test_concurrent_launches_get_unique_query_time_attempt_numbers(
    tmp_path: Path,
) -> None:
    identity = _identity()

    def register_one(_index: int) -> str:
        return _register_new_run(
            runs_root=tmp_path,
            identity=identity,
            now_unix_ns=BASE_NS,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        run_ids = tuple(pool.map(register_one, range(24)))
    assert len(set(run_ids)) == 24
    records = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 1,
        stale_after_ns=STALE_NS,
    )
    assert {record._attempt_number for record in records} == set(range(1, 25))
    assert {record._experiment_fingerprint for record in records} == {
        identity._experiment_fingerprint
    }
    for path in (tmp_path / "runs").rglob("*.json"):
        assert "attempt_number" not in path.read_text()


def test_kill_9_becomes_interrupted_on_next_scan(tmp_path: Path) -> None:
    child = """
import signal
import sys
from pathlib import Path
from trading_research.artifacts.identity import PlatformIdentity
from trading_research.core import DatasetIdentity
from trading_research.experiments.run_identity import _ComponentSource, _ExperimentIdentity
from trading_research.experiments.runs import _register_new_run, _start_run

root = Path(sys.argv[1])
now = int(sys.argv[2])
identity = _ExperimentIdentity(
    resolved_config={"hypothesis": "kill-9 synthetic"},
    dataset_identities=(DatasetIdentity("TRAIN", "1" * 64),),
    components=(_ComponentSource(
        component_kind="research_model",
        component_id="stateless_rule_v1",
        version=1,
        params={},
        source_sha256="2" * 64,
    ),),
    platform_identity=PlatformIdentity("a" * 40, False, None),
    dependency_lock_sha256="3" * 64,
    seed=None,
    machine_identity={"machine": "test"},
    git_diff=None,
    lockfile_in_sync=True,
    full_integrity_verified=True,
    declared_lockbox_evaluation=False,
)
run_id = _register_new_run(runs_root=root, identity=identity, now_unix_ns=now)
_start_run(runs_root=root, run_id=run_id, now_unix_ns=now + 1)
print(run_id, flush=True)
signal.pause()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", child, str(tmp_path), str(BASE_NS)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        run_id = process.stdout.readline().strip()
        assert len(run_id) == 26
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    records = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 1 + STALE_NS + 1,
        stale_after_ns=STALE_NS,
    )
    record = next(item for item in records if item._run_id == run_id)
    assert record._state is RunState.INTERRUPTED
    assert record._outcome_class is _RunOutcomeClass.PRE_EVALUATION_FAILURE
    assert record._evaluation_count == 0
    assert record._failure_reason == f"stale heartbeat exceeded {STALE_NS} ns"
    assert (tmp_path / "runs" / run_id / "manifest.json").is_file()


def test_published_manifest_recovers_a_crash_before_terminal_state_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    original_replace = runs_module._atomic_replace_bytes_at

    def crash_before_state_replace(directory_fd: int, name: str, payload: bytes) -> None:
        if name == "state.json":
            raise OSError("simulated kill after manifest publication")
        original_replace(directory_fd, name, payload)

    monkeypatch.setattr(
        runs_module,
        "_atomic_replace_bytes_at",
        crash_before_state_replace,
    )
    with pytest.raises(OSError, match="simulated kill"):
        _finish_run(
            runs_root=tmp_path,
            run_id=run_id,
            terminal_state=RunState.COMPLETE,
            inventory=_descriptive_inventory(policy_executions=()),
            failure_reason=None,
            now_unix_ns=BASE_NS + 200,
        )
    assert json.loads(
        (tmp_path / "runs" / run_id / "state.json").read_text()
    )["state"] == "RUNNING"
    assert (tmp_path / "runs" / run_id / "manifest.json").is_file()

    monkeypatch.setattr(
        runs_module,
        "_atomic_replace_bytes_at",
        original_replace,
    )
    record = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + 201,
        stale_after_ns=STALE_NS,
    )[0]
    assert record._state is RunState.COMPLETE
    assert stat.S_IMODE(
        (tmp_path / "runs" / run_id).stat().st_mode
    ) == 0o500


def test_run_state_writes_cannot_escape_through_symlinks(tmp_path: Path) -> None:
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(actual_root, target_is_directory=True)
    with pytest.raises(runs_module._RunStoreError, match="safely open"):
        _register_new_run(
            runs_root=linked_root,
            identity=_identity(),
            now_unix_ns=BASE_NS,
        )

    run_id = _register(actual_root, run_index=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "state.json"
    sentinel.write_text("outside remains exact")
    original = actual_root / "runs" / run_id
    saved = tmp_path / "saved-run"
    original.rename(saved)
    original.symlink_to(outside, target_is_directory=True)
    with pytest.raises(runs_module._RunStoreError, match="safely open"):
        _start(actual_root, run_id)
    assert sentinel.read_text() == "outside remains exact"


def test_scan_refuses_config_or_terminal_payload_tampering(tmp_path: Path) -> None:
    pending = _register(tmp_path, run_index=1)
    config = tmp_path / "runs" / pending / "config.resolved.json"
    config.write_text('{"hypothesis":"tampered"}\n')
    with pytest.raises(_RunCorruptionError, match="resolved config"):
        scan_runs(
            tmp_path,
            now_unix_ns=BASE_NS + 10,
            stale_after_ns=STALE_NS,
        )

    separate = tmp_path / "separate"
    separate.mkdir()
    complete = _register(separate, run_index=2)
    _start(separate, complete)
    _finish_run(
        runs_root=separate,
        run_id=complete,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=0.5),
        failure_reason=None,
        now_unix_ns=BASE_NS + 200,
    )
    inventory_path = separate / "runs" / complete / "evaluation_inventory.json"
    inventory_path.chmod(0o600)
    inventory_path.write_text('{"tampered":true}\n')
    with pytest.raises(_RunCorruptionError):
        scan_runs(
            separate,
            now_unix_ns=BASE_NS + 300,
            stale_after_ns=STALE_NS,
        )


def test_empty_files_first_registry_scans_to_empty(tmp_path: Path) -> None:
    assert scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS,
        stale_after_ns=STALE_NS,
    ) == ()


def test_read_only_scan_reports_staleness_without_mutating_any_run_or_audit_byte(
    tmp_path: Path,
) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"fixed":true}\n', encoding="ascii")

    def snapshot() -> str:
        digest = hashlib.sha256()
        for path in sorted(tmp_path.rglob("*")):
            relative = path.relative_to(tmp_path).as_posix()
            metadata = path.lstat()
            digest.update(relative.encode("utf-8"))
            digest.update(str(metadata.st_mode).encode("ascii"))
            digest.update(str(metadata.st_mtime_ns).encode("ascii"))
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    before = snapshot()
    records = scan_runs(
        tmp_path,
        now_unix_ns=BASE_NS + STALE_NS + 1_000,
        stale_after_ns=STALE_NS,
        reconcile=False,
    )
    after = snapshot()
    assert before == after
    assert records[0]._state is RunState.RUNNING
    assert records[0]._heartbeat_stale is True
    state = json.loads((tmp_path / "runs" / run_id / "state.json").read_text())
    assert state["state"] == "RUNNING"


def test_exclusive_run_lock_times_out_with_the_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = _register(tmp_path, run_index=41)
    run_directory = tmp_path / "runs" / run_id
    holder_fd = os.open(run_directory, os.O_RDONLY | os.O_DIRECTORY)
    contender_fd = os.open(run_directory, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(runs_module, "_RUN_LOCK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(runs_module, "_RUN_LOCK_RETRY_SECONDS", 0.005)
    started = time.monotonic()
    try:
        with _hard_test_deadline(
            1.0,
            detail="exclusive run-lock regression blocked past the test deadline",
        ), pytest.raises(runs_module._RunLockTimeoutError) as raised:
            runs_module._lock_run(
                contender_fd,
                run_directory=run_directory,
            )
        elapsed = time.monotonic() - started
        assert 0.02 <= elapsed < 1.0
        assert "exclusive" in str(raised.value)
        assert str(run_directory) in str(raised.value)
    finally:
        os.close(contender_fd)
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_shared_run_lock_times_out_behind_an_exclusive_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = _register(tmp_path, run_index=42)
    run_directory = tmp_path / "runs" / run_id
    holder_fd = os.open(run_directory, os.O_RDONLY | os.O_DIRECTORY)
    contender_fd = os.open(run_directory, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(runs_module, "_RUN_LOCK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(runs_module, "_RUN_LOCK_RETRY_SECONDS", 0.005)
    started = time.monotonic()
    try:
        with _hard_test_deadline(
            1.0,
            detail="shared run-lock regression blocked past the test deadline",
        ), pytest.raises(runs_module._RunLockTimeoutError) as raised:
            runs_module._lock_run_shared(
                contender_fd,
                run_directory=run_directory,
            )
        elapsed = time.monotonic() - started
        assert 0.02 <= elapsed < 1.0
        assert "shared" in str(raised.value)
        assert str(run_directory) in str(raised.value)
    finally:
        os.close(contender_fd)
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


@pytest.mark.parametrize("shared", [False, True], ids=("exclusive", "shared"))
def test_uncontended_run_lock_preserves_run_truth(
    tmp_path: Path,
    *,
    shared: bool,
) -> None:
    run_id = _register(tmp_path, run_index=43)
    run_directory = tmp_path / "runs" / run_id
    state_path = run_directory / "state.json"
    before = state_path.read_bytes()
    run_fd = os.open(run_directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if shared:
            runs_module._lock_run_shared(run_fd, run_directory=run_directory)
        else:
            runs_module._lock_run(run_fd, run_directory=run_directory)
    finally:
        runs_module._unlock_and_close(run_fd)
    assert state_path.read_bytes() == before
