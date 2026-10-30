"""Whole-run pool and files-first sweep resume integration tests."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import trading_research.experiments.runs as runs_module
from tests.load_fixtures import PartitionSpec, make_load_case
from tests.test_experiments import _config_document, _set_sensitive_role
from trading_research.core.errors import TradingResearchError
from trading_research.core.identity import ViewRole
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import (
    _fail_pending_run,
    _register_experiment,
    _runtime_from_paths,
)
from trading_research.experiments.pool import _default_launcher, _resume_plan, _run_sweep
from trading_research.experiments.runs import (
    RunState,
    _scan_run_projections,
    _start_run,
)
from trading_research.experiments.sweeps import (
    _plan_sweep,
    _SweepRuntimePaths,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from types import FrameType

    from trading_research.experiments.orchestration import _RuntimeEnvironment
    from trading_research.experiments.pool import _ChildProcess, _SweepRunSummary
    from trading_research.experiments.runs import _RunProjection
    from trading_research.experiments.sweeps import _SweepPlan


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEPENDENCY_LOCK = _REPOSITORY_ROOT / "uv.lock"
_HEARTBEAT_NS = 50_000_000
_STALE_AFTER_NS = 1_000_000_000


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


@pytest.fixture
def resume_deadlock_deadline() -> Iterator[None]:
    with _hard_test_deadline(
        30.0,
        detail="SIGKILL resume regression exceeded its hard 30-second deadline",
    ):
        yield


def _planned_sweep(
    tmp_path: Path,
    *,
    sweep_id: str = "resume-sweep",
    seeds: tuple[int, ...] = (1, 2),
) -> tuple[_SweepPlan, _RuntimeEnvironment]:
    keys = tuple(range(100, 146))
    case = make_load_case(
        tmp_path,
        specs=(PartitionSpec("p0", "bars.parquet", keys, 100, 145, len(keys)),),
    )
    base = _config_document(case)
    config = _config_from_document(base)
    runs_root = (tmp_path / "runtime").resolve()
    audit_path = (tmp_path / "audit.jsonl").resolve()
    data_root = case.source_directory.parent.resolve()
    policy_path = (data_root / "policy.json").resolve()
    runtime = _runtime_from_paths(
        config=config,
        data_roots=(data_root,),
        runs_root=runs_root,
        split_policy_path=policy_path,
        audit_path=audit_path,
        repository_root=_REPOSITORY_ROOT,
        dependency_lock_path=_DEPENDENCY_LOCK,
    )
    request: dict[str, object] = {
        "schema": "research-sweep-request-v1",
        "sweep_id": sweep_id,
        "base_config": base,
        "axes": [{"json_pointer": "/seed", "values": list(seeds)}],
        "combination": "cartesian",
    }
    plan = _plan_sweep(
        request,
        runtime=runtime,
        runtime_paths=_SweepRuntimePaths(
            (data_root,),
            runs_root,
            policy_path,
            audit_path,
            _REPOSITORY_ROOT,
            _DEPENDENCY_LOCK,
            _HEARTBEAT_NS,
        ),
        spend_oos=False,
        max_protected_children=None,
        protected_reason=None,
    )
    return plan, runtime


def _planned_oos_sweep(
    tmp_path: Path,
    *,
    child_count: int,
) -> tuple[_SweepPlan, _RuntimeEnvironment, Path]:
    keys = tuple(range(2_100, 2_146))
    case = make_load_case(
        tmp_path,
        role=ViewRole.OOS,
        specs=(
            PartitionSpec(
                "p0",
                "bars.parquet",
                keys,
                2_100,
                2_145,
                len(keys),
            ),
        ),
    )
    base = _config_document(case)
    _set_sensitive_role(base, role=ViewRole.OOS, first_key=2_100)
    config = _config_from_document(base)
    runs_root = (tmp_path / "runtime").resolve()
    audit_path = (tmp_path / "audit.jsonl").resolve()
    data_root = case.source_directory.parent.resolve()
    policy_path = (data_root / "policy.json").resolve()
    runtime = _runtime_from_paths(
        config=config,
        data_roots=(data_root,),
        runs_root=runs_root,
        split_policy_path=policy_path,
        audit_path=audit_path,
        repository_root=_REPOSITORY_ROOT,
        dependency_lock_path=_DEPENDENCY_LOCK,
    )
    request: dict[str, object] = {
        "schema": "research-sweep-request-v1",
        "sweep_id": "parallel-oos",
        "base_config": base,
        "axes": [{"json_pointer": "/seed", "values": list(range(child_count))}],
        "combination": "cartesian",
    }
    plan = _plan_sweep(
        request,
        runtime=runtime,
        runtime_paths=_SweepRuntimePaths(
            (data_root,),
            runs_root,
            policy_path,
            audit_path,
            _REPOSITORY_ROOT,
            _DEPENDENCY_LOCK,
            _HEARTBEAT_NS,
        ),
        spend_oos=True,
        max_protected_children=child_count,
        protected_reason="Approved bounded parallel OOS integration test",
    )
    return plan, runtime, audit_path


def _execute(
    plan: _SweepPlan,
    runtime: _RuntimeEnvironment,
    *,
    retry_failed: bool = False,
    workers: int = 2,
    stale_after_ns: int = _STALE_AFTER_NS,
) -> _SweepRunSummary:
    return _run_sweep(
        runtime._runs_root,
        str(plan._document["sweep_id"]),
        requested_workers=workers,
        memory_budget_bytes=536_870_912,
        assumed_peak_rss_bytes=134_217_728,
        retry_failed=retry_failed,
        allow_noncanonical=True,
        acknowledge_oos_access=False,
        allow_critical_disk_usage=False,
        stale_after_ns=stale_after_ns,
        poll_interval_seconds=0.02,
    )


def _projections(runtime: _RuntimeEnvironment) -> tuple[_RunProjection, ...]:
    return _scan_run_projections(
        runtime._runs_root,
        now_unix_ns=time.time_ns(),
        stale_after_ns=_STALE_AFTER_NS,
        reconcile=False,
        tolerate_corruption=False,
    )._projections


def test_pool_runs_whole_children_and_resume_skips_every_complete(
    tmp_path: Path,
) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(1, 2, 3))
    first = _execute(plan, runtime)
    assert first._successful
    assert first._complete == 3
    assert first._dispatched == 3
    assert first._effective_workers == 2
    assert all(item._record._state is RunState.COMPLETE for item in _projections(runtime))

    second = _execute(plan, runtime)
    assert second._successful
    assert second._complete == 3
    assert second._dispatched == 0
    assert second._skipped_complete == 3
    assert len(_projections(runtime)) == 3


def test_resume_launches_the_existing_single_pending_attempt(tmp_path: Path) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(11,))
    child = plan._children[0]
    run_id = _register_experiment(
        _config_from_document(child._config_document),
        runtime=runtime,
    )
    summary = _execute(plan, runtime)
    assert summary._successful
    assert summary._dispatched == 1
    observed = _projections(runtime)
    assert [item._record._run_id for item in observed] == [run_id]
    assert observed[0]._record._state is RunState.COMPLETE


def test_failed_child_is_skipped_until_retry_is_explicit(tmp_path: Path) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(21,))
    child = plan._children[0]
    run_id = _register_experiment(
        _config_from_document(child._config_document),
        runtime=runtime,
    )
    _fail_pending_run(runtime=runtime, run_id=run_id, reason="synthetic launcher failure")

    skipped = _execute(plan, runtime)
    assert not skipped._successful
    assert skipped._failed == 1
    assert skipped._skipped_failed == 1
    assert skipped._dispatched == 0

    retried = _execute(plan, runtime, retry_failed=True)
    assert retried._successful
    assert retried._complete == 1
    assert retried._dispatched == 1
    assert len(_projections(runtime)) == 2


def test_stale_running_attempt_is_reconciled_once_then_retried(tmp_path: Path) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(31,))
    child = plan._children[0]
    stale_run_id = _register_experiment(
        _config_from_document(child._config_document),
        runtime=runtime,
    )
    _start_run(
        runs_root=runtime._runs_root,
        run_id=stale_run_id,
        now_unix_ns=time.time_ns(),
        heartbeat_interval_ns=_HEARTBEAT_NS,
    )
    stale_time = time.time_ns() - 2_000_000_000
    os.utime(
        runtime._runs_root / "runs" / stale_run_id / "heartbeat.json",
        ns=(stale_time, stale_time),
    )
    summary = _execute(plan, runtime)
    assert summary._successful
    assert summary._dispatched == 1
    states = [item._record._state for item in _projections(runtime)]
    assert sorted(state.value for state in states) == ["COMPLETE", "INTERRUPTED"]


def test_outputs_are_identical_at_one_and_four_workers(tmp_path: Path) -> None:
    first_plan, first_runtime = _planned_sweep(
        tmp_path / "serial",
        sweep_id="determinism",
        seeds=(1, 2, 3, 4),
    )
    second_plan, second_runtime = _planned_sweep(
        tmp_path / "parallel",
        sweep_id="determinism",
        seeds=(1, 2, 3, 4),
    )
    assert [child._experiment_fingerprint for child in first_plan._children] == [
        child._experiment_fingerprint for child in second_plan._children
    ]
    assert _execute(first_plan, first_runtime, workers=1)._successful
    assert _execute(second_plan, second_runtime, workers=4)._successful

    def terminal_evidence(runtime: _RuntimeEnvironment) -> dict[str, object]:
        evidence: dict[str, object] = {}
        for projection in _projections(runtime):
            manifest = projection._manifest
            assert manifest is not None
            evidence[projection._record._experiment_fingerprint] = {
                "artifact_hashes": manifest.artifact_hashes,
                "metric_hashes": manifest.metric_hashes,
                "evaluation_count": manifest.evaluation_count,
                "outcome_class": manifest.outcome_class.value,
            }
        return evidence

    assert terminal_evidence(first_runtime) == terminal_evidence(second_runtime)


def test_killed_worker_recomputes_only_its_missing_child(tmp_path: Path) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(51, 52))
    crashed_run_ids: list[str] = []

    def launcher(
        command: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> _ChildProcess:
        if not crashed_run_ids:
            run_id = command[command.index("--run-id") + 1]
            crashed_run_ids.append(run_id)
            code = (
                "import os,signal,sys,time;"
                "from pathlib import Path;"
                "from trading_research.experiments.runs import _start_run;"
                "_start_run(runs_root=Path(sys.argv[1]),run_id=sys.argv[2],"
                "now_unix_ns=time.time_ns(),heartbeat_interval_ns=int(sys.argv[3]));"
                "os.kill(os.getpid(),signal.SIGKILL)"
            )
            return subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    code,
                    str(runtime._runs_root),
                    run_id,
                    str(_HEARTBEAT_NS),
                ],
                env=dict(environment),
            )
        return _default_launcher(command, environment)

    first = _run_sweep(
        runtime._runs_root,
        str(plan._document["sweep_id"]),
        requested_workers=2,
        memory_budget_bytes=536_870_912,
        assumed_peak_rss_bytes=134_217_728,
        retry_failed=False,
        allow_noncanonical=True,
        acknowledge_oos_access=False,
        allow_critical_disk_usage=False,
        stale_after_ns=600_000_000,
        poll_interval_seconds=0.01,
        _launcher=launcher,
    )
    assert not first._successful
    assert first._requires_resume
    before_resume = _projections(runtime)
    complete_before = {
        item._record._experiment_fingerprint
        for item in before_resume
        if item._record._state is RunState.COMPLETE
    }
    assert len(complete_before) == 1

    resumed = _execute(plan, runtime, stale_after_ns=600_000_000)
    assert resumed._successful
    after_resume = _projections(runtime)
    assert len(after_resume) == 3
    assert sum(item._record._state is RunState.COMPLETE for item in after_resume) == 2
    assert sum(item._record._state is RunState.INTERRUPTED for item in after_resume) == 1
    for fingerprint in complete_before:
        assert sum(
            item._record._experiment_fingerprint == fingerprint for item in after_resume
        ) == 1


def test_dispatcher_sigkill_resume_adopts_live_child_and_runs_exact_complement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume_deadlock_deadline: None,
) -> None:
    del resume_deadlock_deadline
    plan, runtime = _planned_sweep(tmp_path, seeds=(61, 62, 63))
    records_path = (tmp_path / "launched-workers.txt").resolve()
    code = "\n".join(
        (
            "import os,subprocess,sys",
            "from pathlib import Path",
            "from trading_research.experiments.pool import _run_sweep",
            "records=Path(sys.argv[3])",
            "def launch(command, environment):",
            "    process=subprocess.Popen(command,env=dict(environment))",
            "    run_id=command[command.index('--run-id')+1]",
            "    fd=os.open(records,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)",
            "    os.write(fd,f'{run_id} {process.pid}\\n'.encode('ascii'))",
            "    os.fsync(fd); os.close(fd)",
            "    return process",
            "result=_run_sweep(Path(sys.argv[1]),sys.argv[2],requested_workers=2,",
            "    memory_budget_bytes=536870912,assumed_peak_rss_bytes=134217728,",
            "    retry_failed=False,allow_noncanonical=True,acknowledge_oos_access=False,",
            "    allow_critical_disk_usage=False,stale_after_ns=10000000000,",
            "    poll_interval_seconds=0.01,_launcher=launch)",
            "raise SystemExit(0 if result._successful else 1)",
        )
    )
    dispatcher = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(runtime._runs_root),
            str(plan._document["sweep_id"]),
            str(records_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stopped_pid: int | None = None
    stopped_run_id: str | None = None
    continue_trigger = threading.Event()
    watchdog: threading.Thread | None = None
    watchdog_errors: list[BaseException] = []
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and stopped_pid is None:
            if records_path.exists():
                for line in records_path.read_text(encoding="ascii").splitlines():
                    run_id, pid_text = line.split()
                    state_path = runtime._runs_root / "runs" / run_id / "state.json"
                    if not state_path.exists():
                        continue
                    state = json.loads(state_path.read_text())["state"]
                    if state == "RUNNING":
                        stopped_pid = int(pid_text)
                        stopped_run_id = run_id
                        os.kill(stopped_pid, signal.SIGSTOP)
                        break
            time.sleep(0.002)
        assert stopped_pid is not None, "no worker remained RUNNING long enough to suspend"

        while time.monotonic() < deadline:
            complete = 0
            for state_path in (runtime._runs_root / "runs").glob("*/state.json"):
                try:
                    state = json.loads(state_path.read_text())["state"]
                except (FileNotFoundError, json.JSONDecodeError, KeyError):
                    continue
                complete += state == "COMPLETE"
            if complete == 2:
                break
            time.sleep(0.01)
        else:
            pytest.fail("dispatcher did not complete the two unstopped children")

        os.kill(dispatcher.pid, signal.SIGKILL)
        dispatcher.wait(timeout=5)
        assert stopped_run_id is not None
        original_shared_lock = runs_module._lock_run_shared

        def observed_shared_lock(
            run_fd: int,
            *,
            run_directory: Path,
        ) -> None:
            if run_directory.name == stopped_run_id:
                continue_trigger.set()
            original_shared_lock(run_fd, run_directory=run_directory)

        monkeypatch.setattr(runs_module, "_lock_run_shared", observed_shared_lock)

        def continue_suspended_worker() -> None:
            try:
                if not continue_trigger.wait(timeout=5.0):
                    raise AssertionError(
                        "resume preflight never attempted the suspended run lock"
                    )
                os.kill(stopped_pid, signal.SIGCONT)
            except BaseException as exc:  # surfaced deterministically in the main test
                watchdog_errors.append(exc)

        watchdog = threading.Thread(
            target=continue_suspended_worker,
            name="resume-suspended-worker-watchdog",
            daemon=True,
        )
        watchdog.start()

        summary = _run_sweep(
            runtime._runs_root,
            str(plan._document["sweep_id"]),
            requested_workers=2,
            memory_budget_bytes=536_870_912,
            assumed_peak_rss_bytes=134_217_728,
            retry_failed=False,
            allow_noncanonical=True,
            acknowledge_oos_access=False,
            allow_critical_disk_usage=False,
            stale_after_ns=10_000_000_000,
            poll_interval_seconds=0.01,
        )
        watchdog.join(timeout=5)
        assert not watchdog.is_alive()
        assert not watchdog_errors
        assert summary._successful
        assert summary._adopted == 1
        assert summary._skipped_complete == 2
        assert summary._dispatched == 0
        assert len(_projections(runtime)) == 3
    finally:
        continue_trigger.set()
        if stopped_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(stopped_pid, signal.SIGCONT)
        if watchdog is not None:
            watchdog.join(timeout=5)
        if dispatcher.poll() is None:
            dispatcher.kill()
            dispatcher.wait(timeout=5)


def test_parallel_gated_children_produce_intact_audit_lines_and_plural_links(
    tmp_path: Path,
) -> None:
    plan, runtime, audit_path = _planned_oos_sweep(tmp_path, child_count=8)
    summary = _run_sweep(
        runtime._runs_root,
        str(plan._document["sweep_id"]),
        requested_workers=8,
        memory_budget_bytes=1_073_741_824,
        assumed_peak_rss_bytes=67_108_864,
        retry_failed=False,
        allow_noncanonical=True,
        acknowledge_oos_access=False,
        allow_critical_disk_usage=False,
        stale_after_ns=_STALE_AFTER_NS,
        poll_interval_seconds=0.01,
    )
    assert summary._successful
    assert summary._complete == 8
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(events) == 8
    event_ids = {event["event_id"] for event in events}
    session_ids = {event["caller"]["session_id"] for event in events}
    assert len(event_ids) == len(session_ids) == 8
    linked_events: set[str] = set()
    linked_sessions: set[str] = set()
    for projection in _projections(runtime):
        assert projection._manifest is not None
        refs = projection._manifest.access_audit_refs
        assert len(refs) == 1
        assert refs[0]._audit_event_id is not None
        linked_events.add(refs[0]._audit_event_id)
        linked_sessions.add(refs[0]._session_id)
    assert linked_events == event_ids
    assert linked_sessions == session_ids

    with pytest.raises(TradingResearchError, match="acknowledge-oos-access"):
        _execute(plan, runtime)
    acknowledged = _run_sweep(
        runtime._runs_root,
        str(plan._document["sweep_id"]),
        requested_workers=8,
        memory_budget_bytes=1_073_741_824,
        assumed_peak_rss_bytes=67_108_864,
        retry_failed=False,
        allow_noncanonical=True,
        acknowledge_oos_access=True,
        allow_critical_disk_usage=False,
        stale_after_ns=_STALE_AFTER_NS,
    )
    assert acknowledged._successful
    assert acknowledged._skipped_complete == 8


def test_resume_refuses_multiple_ambiguous_pending_attempts(tmp_path: Path) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(41,))
    child = plan._children[0]
    config = _config_from_document(child._config_document)
    _register_experiment(config, runtime=runtime)
    _register_experiment(config, runtime=runtime)
    with pytest.raises(TradingResearchError, match="multiple PENDING"):
        _resume_plan(plan, _projections(runtime), retry_failed=False)
