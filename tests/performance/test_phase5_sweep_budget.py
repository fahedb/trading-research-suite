"""Clean-commit Phase-5 sweep, recovery, index, and ledger qualification."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tests.performance._measure_child import (
    DATASET_ROOT,
    POLICY_PATH,
    REGISTRY_ROOTS,
    TRAIN_MANIFEST,
    TRAIN_REF,
    VIEWS_ROOT,
)
from tests.performance.test_phase3_run_budget import _config_document
from tests.test_runs import BASE_NS, _evaluated_inventory, _identity, _register, _start
from trading_research.core.errors import ContractViolationError
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import _runtime_from_paths
from trading_research.experiments.pool import _run_sweep
from trading_research.experiments.resources import (
    _THREAD_CLAMPS,
    _disk_usage,
    _enforce_disk_preflight,
)
from trading_research.experiments.runs import (
    RunState,
    _finish_run,
    _scan_run_projections,
)
from trading_research.experiments.sweeps import (
    _plan_sweep,
    _SweepRuntimePaths,
)
from trading_research.index.build import (
    _canonical_table_dump,
    _rebuild_index,
)
from trading_research.index.query import _compare_metric, _query_runs
from trading_research.index.trials import _files_trial_ledger, _index_trial_ledger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from trading_research.experiments.orchestration import _RuntimeEnvironment
    from trading_research.experiments.runs import _RunProjection
    from trading_research.experiments.sweeps import _SweepPlan

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).parents[2]
_RUNNING_FILE = Path(__file__).resolve()
_RUNS_ROOT = Path("/path/to/research-runs")
_RUNS_TMP_ROOT = _RUNS_ROOT / "tmp"
_AUDIT_PATH = _RUNS_ROOT / "audit.jsonl"
_DEPENDENCY_LOCK = _REPO_ROOT / "uv.lock"
_VIEWS = {
    "lockbox": VIEWS_ROOT / "XAUUSD_S_M1_LOCKBOX_V1" / "manifest.json",
    "oos": VIEWS_ROOT / "XAUUSD_S_M1_OOS_V1" / "manifest.json",
    "snapshot": VIEWS_ROOT / "XAUUSD_S_M1_SNAPSHOT_V1" / "manifest.json",
    "train": TRAIN_MANIFEST,
    "validation": VIEWS_ROOT / "XAUUSD_S_M1_VALIDATION_V1" / "manifest.json",
}
_EXPECTED_VIEW_DIGESTS = {
    "lockbox": "c9080133719b79016beace397ab569aad07a2b15008bab6c5d36730b14b64dc8",
    "oos": "d94a54331a1ef30b0242e685ab8d2f3f0d8d910a64b9c52412682d991bcab3af",
    "snapshot": "d5b1724cb204699d4e8c3a45bebffa39d080a5260d285610f50981f6ae7eb600",
    "train": "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
    "validation": "5d122caa6a38c5bcf94ebc20895199d89c30f0f460fabd244865ad8829ebe6fd",
}
_EXPECTED_AUDIT_SHA256 = (
    "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5"
)
_EXPECTED_DATASET_FINGERPRINT = (
    "5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180"
)
_CHILD_COUNT = 100
_INDEX_RUN_COUNT = 1_000
_INDEX_EVALUATED_COUNT = 100
_REQUESTED_WORKERS = 10
_MEMORY_BUDGET_BYTES = 16 * 1024**3
_ASSUMED_RSS_BYTES = 1024**3
_HEARTBEAT_INTERVAL_NS = 50_000_000
_STALE_AFTER_NS = 600_000_000
_POLL_SECONDS = 0.02
_EVIDENCE_SCHEMA = "phase5-experiment-operations-performance-v1"


@dataclass(frozen=True, slots=True)
class _ProcessEvidence:
    elapsed_seconds: float
    worker_pids: int
    clamp_verified_pids: int
    maximum_concurrency: int
    at_capacity_fraction: float
    maximum_observed_concurrent_rss_bytes: int
    maximum_observed_worker_rss_bytes: int
    median_observed_worker_peak_rss_bytes: float


@pytest.fixture(scope="module", autouse=True)
def _require_published_fixture() -> None:
    assert sys.platform == "linux", "Phase 5 qualification reads Linux /proc telemetry"
    assert _RUNS_ROOT.is_dir()
    assert _RUNS_TMP_ROOT.is_dir()
    assert DATASET_ROOT.is_dir()
    assert VIEWS_ROOT.is_dir()
    assert POLICY_PATH.is_file()
    assert _AUDIT_PATH.is_file()
    assert _DEPENDENCY_LOCK.is_file()
    assert all(path.is_file() for path in _VIEWS.values())


def _git_output(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _clean_committed_head() -> str:
    head = _git_output("rev-parse", "HEAD").decode("ascii").strip()
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AssertionError("Phase 5 qualification requires a clean repository worktree")
    relative = _RUNNING_FILE.relative_to(_REPO_ROOT).as_posix()
    if _git_output("show", f"{head}:{relative}") != _RUNNING_FILE.read_bytes():
        raise AssertionError("Phase 5 performance harness is not the committed HEAD version")
    return head


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _external_snapshot() -> dict[str, object]:
    audit = _AUDIT_PATH.read_bytes()
    view_digests = {name: _sha256_file(path) for name, path in _VIEWS.items()}
    fingerprint, entry_count = _dataset_fingerprint()
    return {
        "audit_records": len(audit.splitlines()),
        "audit_sha256": hashlib.sha256(audit).hexdigest(),
        "view_manifest_sha256": view_digests,
        "dataset_metadata_fingerprint": fingerprint,
        "dataset_metadata_entries": entry_count,
    }


def _dataset_fingerprint() -> tuple[str, int]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    found = subprocess.run(
        ["find", ".", "-mindepth", "1", "-printf", "%y %m %s %p\n"],
        cwd=DATASET_ROOT,
        check=True,
        capture_output=True,
        env=environment,
    ).stdout
    ordered = subprocess.run(
        ["sort"],
        check=True,
        input=found,
        capture_output=True,
        env=environment,
    ).stdout
    return hashlib.sha256(ordered).hexdigest(), found.count(b"\n")


def _assert_expected_external_snapshot(snapshot: Mapping[str, object]) -> None:
    assert snapshot["audit_records"] == 3
    assert snapshot["audit_sha256"] == _EXPECTED_AUDIT_SHA256
    assert snapshot["view_manifest_sha256"] == _EXPECTED_VIEW_DIGESTS
    assert snapshot["dataset_metadata_fingerprint"] == _EXPECTED_DATASET_FINGERPRINT


def _runtime(runs_root: Path) -> tuple[_RuntimeEnvironment, _SweepRuntimePaths]:
    config = _config_from_document(_config_document())
    runtime = _runtime_from_paths(
        config=config,
        data_roots=REGISTRY_ROOTS,
        runs_root=runs_root,
        split_policy_path=POLICY_PATH.resolve(),
        audit_path=_AUDIT_PATH,
        repository_root=_REPO_ROOT,
        dependency_lock_path=_DEPENDENCY_LOCK,
    )
    paths = _SweepRuntimePaths(
        tuple(path.resolve() for path in REGISTRY_ROOTS),
        runs_root,
        POLICY_PATH.resolve(),
        _AUDIT_PATH,
        _REPO_ROOT,
        _DEPENDENCY_LOCK,
        _HEARTBEAT_INTERVAL_NS,
    )
    return runtime, paths


def _plan(
    runtime: _RuntimeEnvironment,
    paths: _SweepRuntimePaths,
    *,
    sweep_id: str,
    seeds: Sequence[int],
) -> _SweepPlan:
    request: dict[str, object] = {
        "schema": "research-sweep-request-v1",
        "sweep_id": sweep_id,
        "base_config": _config_document(),
        "axes": [{"json_pointer": "/seed", "values": list(seeds)}],
        "combination": "cartesian",
    }
    plan = _plan_sweep(
        request,
        runtime=runtime,
        runtime_paths=paths,
        spend_oos=False,
        max_protected_children=None,
        protected_reason=None,
    )
    assert len(plan._children) == _CHILD_COUNT
    return plan


def _dispatcher_command(runs_root: Path, sweep_id: str) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "trading_research.experiments.pool",
        "--runs-root",
        str(runs_root),
        "--sweep",
        sweep_id,
        "--max-workers",
        str(_REQUESTED_WORKERS),
        "--memory-budget-bytes",
        str(_MEMORY_BUDGET_BYTES),
        "--assumed-peak-rss-bytes",
        str(_ASSUMED_RSS_BYTES),
        "--stale-after-ns",
        str(_STALE_AFTER_NS),
        "--poll-interval-seconds",
        str(_POLL_SECONDS),
        "--launch-mode",
        "phase5-qualification-foreground",
    )


def _direct_children(parent_pid: int) -> tuple[int, ...]:
    try:
        content = Path(f"/proc/{parent_pid}/task/{parent_pid}/children").read_text(
            encoding="ascii"
        )
    except OSError:
        return ()
    return tuple(int(value) for value in content.split())


def _process_status(pid: int) -> tuple[str, int] | None:
    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    state = next((line.split()[1] for line in lines if line.startswith("State:")), "?")
    rss_kib = next(
        (int(line.split()[1]) for line in lines if line.startswith("VmRSS:")),
        0,
    )
    return state, rss_kib * 1024


def _process_environment(pid: int) -> dict[str, str]:
    try:
        payload = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for entry in payload.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        result[key.decode("utf-8")] = value.decode("utf-8")
    return result


def _worker_run_id(pid: int) -> str | None:
    try:
        arguments = tuple(
            value.decode("utf-8")
            for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if value
        )
    except (OSError, UnicodeError):
        return None
    try:
        return arguments[arguments.index("--run-id") + 1]
    except (ValueError, IndexError):
        return None


def _sample_workers(
    dispatcher_pid: int,
    *,
    seen: set[int],
    clamp_verified: set[int],
    peaks: dict[int, int],
) -> tuple[int, ...]:
    active: list[int] = []
    for pid in _direct_children(dispatcher_pid):
        if _worker_run_id(pid) is None:
            continue
        status = _process_status(pid)
        if status is None or status[0] == "Z":
            continue
        seen.add(pid)
        environment = _process_environment(pid)
        if all(environment.get(name) == value for name, value in _THREAD_CLAMPS.items()):
            clamp_verified.add(pid)
        peaks[pid] = max(peaks.get(pid, 0), status[1])
        active.append(pid)
    return tuple(active)


def _run_monitored_dispatcher(runs_root: Path, sweep_id: str) -> _ProcessEvidence:
    process = subprocess.Popen(
        _dispatcher_command(runs_root, sweep_id),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    seen: set[int] = set()
    verified: set[int] = set()
    peaks: dict[int, int] = {}
    concurrency: list[int] = []
    concurrent_rss: list[int] = []
    started = perf_counter()
    while process.poll() is None:
        active = _sample_workers(
            process.pid,
            seen=seen,
            clamp_verified=verified,
            peaks=peaks,
        )
        concurrency.append(len(active))
        concurrent_rss.append(sum(peaks.get(pid, 0) for pid in active))
        time.sleep(_POLL_SECONDS)
    return_code = process.wait(timeout=10)
    elapsed = perf_counter() - started
    assert return_code == 0
    nonzero_samples = tuple(value for value in concurrency if value > 0)
    assert nonzero_samples
    worker_peaks = tuple(peaks.values())
    assert len(seen) == _CHILD_COUNT
    assert verified == seen
    at_capacity = sum(value >= _REQUESTED_WORKERS for value in nonzero_samples)
    return _ProcessEvidence(
        elapsed,
        len(seen),
        len(verified),
        max(nonzero_samples),
        at_capacity / len(nonzero_samples),
        max(concurrent_rss),
        max(worker_peaks),
        median(worker_peaks),
    )


def _manifest_count(runs_root: Path) -> int:
    collection = runs_root / "runs"
    if not collection.exists():
        return 0
    return sum((child / "manifest.json").is_file() for child in collection.iterdir())


def _run_state(runs_root: Path, run_id: str) -> str | None:
    try:
        document = json.loads(
            (runs_root / "runs" / run_id / "state.json").read_text(encoding="ascii")
        )
    except (OSError, json.JSONDecodeError):
        return None
    value = document.get("state")
    return value if type(value) is str else None


def _kill_dispatcher_and_two_running_children(
    runs_root: Path,
    sweep_id: str,
    *,
    terminal_baseline: int,
) -> tuple[tuple[tuple[str, str], ...], int, int]:
    process = subprocess.Popen(
        _dispatcher_command(runs_root, sweep_id),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    seen: set[int] = set()
    verified: set[int] = set()
    peaks: dict[int, int] = {}
    deadline = time.monotonic() + 300.0
    completed_at_kill = 0
    killed: tuple[tuple[str, str], ...] = ()
    while time.monotonic() < deadline:
        active = _sample_workers(
            process.pid,
            seen=seen,
            clamp_verified=verified,
            peaks=peaks,
        )
        completed = _manifest_count(runs_root) - terminal_baseline
        if completed >= 45 and len(active) >= 2:
            os.kill(process.pid, signal.SIGSTOP)
            candidates = tuple(
                (pid, run_id)
                for pid in _direct_children(process.pid)
                if (run_id := _worker_run_id(pid)) is not None
                and _run_state(runs_root, run_id) == "RUNNING"
            )
            stopped: list[tuple[int, str]] = []
            for pid, run_id in candidates:
                if len(stopped) == 2:
                    break
                try:
                    os.kill(pid, signal.SIGSTOP)
                except ProcessLookupError:
                    continue
                if _run_state(runs_root, run_id) == "RUNNING":
                    stopped.append((pid, run_id))
                else:
                    os.kill(pid, signal.SIGCONT)
            if len(stopped) == 2:
                completed_at_kill = _manifest_count(runs_root) - terminal_baseline
                killed = tuple((str(pid), run_id) for pid, run_id in stopped)
                for pid, _run_id in stopped:
                    os.kill(pid, signal.SIGKILL)
                os.kill(process.pid, signal.SIGKILL)
                break
            for pid, _run_id in stopped:
                os.kill(pid, signal.SIGCONT)
            os.kill(process.pid, signal.SIGCONT)
        if process.poll() is not None:
            raise AssertionError("forced-kill dispatcher exited before the kill point")
        time.sleep(_POLL_SECONDS)
    else:
        process.kill()
        raise AssertionError("forced-kill sweep did not reach its midpoint")
    assert process.wait(timeout=10) == -signal.SIGKILL
    assert len(killed) == 2
    assert verified == seen
    survivor_pids = {pid for pid in seen if str(pid) not in {item[0] for item in killed}}
    _wait_for_process_exit(survivor_pids, timeout_seconds=60.0)
    time.sleep(_STALE_AFTER_NS / 1_000_000_000 + 0.2)
    return killed, completed_at_kill, len(seen)


def _wait_for_process_exit(pids: set[int], *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        remaining = {
            pid
            for pid in remaining
            if (status := _process_status(pid)) is not None and status[0] != "Z"
        }
        if remaining:
            time.sleep(0.05)
    assert not remaining, f"orphan workers did not exit: {sorted(remaining)!r}"


def _projections(runs_root: Path) -> tuple[_RunProjection, ...]:
    return _scan_run_projections(
        runs_root,
        now_unix_ns=time.time_ns(),
        stale_after_ns=_STALE_AFTER_NS,
        reconcile=False,
        tolerate_corruption=False,
    )._projections


def _complete_fingerprints(runs_root: Path, plan: _SweepPlan) -> set[str]:
    planned = {child._experiment_fingerprint for child in plan._children}
    return {
        projection._record._experiment_fingerprint
        for projection in _projections(runs_root)
        if projection._record._experiment_fingerprint in planned
        and projection._record._state is RunState.COMPLETE
    }


def _cache_snapshot(runs_root: Path) -> dict[str, tuple[int, int, str]]:
    cache = runs_root / "cache"
    if not cache.exists():
        return {}
    return {
        path.relative_to(cache).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            _sha256_file(path),
        )
        for path in sorted(cache.rglob("*"))
        if path.is_file()
    }


def _resume_forced_sweep(
    runs_root: Path,
    plan: _SweepPlan,
    *,
    complete_before: set[str],
) -> tuple[dict[str, object], float]:
    planned = {child._experiment_fingerprint for child in plan._children}
    incomplete = planned - complete_before
    launched: list[str] = []
    environment_checks = 0

    def launcher(
        command: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> subprocess.Popen[bytes]:
        nonlocal environment_checks
        assert all(environment.get(name) == value for name, value in _THREAD_CLAMPS.items())
        environment_checks += 1
        run_id = command[command.index("--run-id") + 1]
        identity = json.loads(
            (runs_root / "runs" / run_id / "identity.json").read_text(encoding="ascii")
        )
        fingerprint = identity.get("experiment_fingerprint")
        assert type(fingerprint) is str
        launched.append(fingerprint)
        return subprocess.Popen(command, env=dict(environment))

    started = perf_counter()
    summary = _run_sweep(
        runs_root,
        str(plan._document["sweep_id"]),
        requested_workers=_REQUESTED_WORKERS,
        memory_budget_bytes=_MEMORY_BUDGET_BYTES,
        assumed_peak_rss_bytes=_ASSUMED_RSS_BYTES,
        retry_failed=False,
        allow_noncanonical=False,
        acknowledge_oos_access=False,
        allow_critical_disk_usage=False,
        stale_after_ns=_STALE_AFTER_NS,
        poll_interval_seconds=_POLL_SECONDS,
        launch_mode="phase5-qualification-resume",
        _launcher=launcher,
    )
    elapsed = perf_counter() - started
    after = _projections(runs_root)
    noncomplete = tuple(
        {
            "run_id": projection._record._run_id,
            "fingerprint": projection._record._experiment_fingerprint,
            "state": projection._record._state.value,
            "failure_reason": projection._record._failure_reason,
        }
        for projection in after
        if projection._record._experiment_fingerprint in planned
        and projection._record._state is not RunState.COMPLETE
    )
    assert summary._successful, {
        "summary": repr(summary),
        "noncomplete_attempts": noncomplete,
    }
    assert summary._dispatched == len(incomplete)
    assert environment_checks == len(incomplete)
    assert set(launched) == incomplete
    for fingerprint in complete_before:
        assert sum(
            projection._record._experiment_fingerprint == fingerprint
            for projection in after
        ) == 1
    assert _complete_fingerprints(runs_root, plan) == planned
    assert all(
        projection._record._state not in {RunState.PENDING, RunState.RUNNING}
        for projection in after
    )
    return {
        "complete_before_resume": len(complete_before),
        "incomplete_before_resume": len(incomplete),
        "dispatched_on_resume": summary._dispatched,
        "interrupted_after_resume": summary._interrupted,
        "complete_after_resume": summary._complete,
        "environment_checks": environment_checks,
    }, elapsed


def _populate_synthetic_index_root(root: Path) -> tuple[str, ...]:
    root.mkdir(mode=0o700)
    fingerprints: list[str] = []
    for index in range(1, _INDEX_RUN_COUNT + 1):
        identity = _identity(
            config={
                "dataset": {
                    "dataset_id": "SYNTHETIC_TRAIN",
                    "manifest_sha256": "1" * 64,
                    "role": "train",
                },
                "model": {"parameter": index},
            },
            seed=index,
        )
        run_id = _register(root, run_index=index, identity=identity)
        if index <= _INDEX_EVALUATED_COUNT:
            _start(root, run_id, offset=10_000 + index)
            _finish_run(
                runs_root=root,
                run_id=run_id,
                terminal_state=RunState.COMPLETE,
                inventory=_evaluated_inventory(value=index / _INDEX_EVALUATED_COUNT),
                failure_reason=None,
                now_unix_ns=BASE_NS + 20_000 + index,
            )
            fingerprints.append(identity._experiment_fingerprint)
    return tuple(fingerprints)


def _timed_samples(operation: Callable[[], object]) -> tuple[tuple[float, ...], float]:
    operation()
    samples: list[float] = []
    for _index in range(5):
        started = perf_counter()
        operation()
        samples.append(perf_counter() - started)
    return tuple(samples), median(samples)


def _qualify_index(root: Path, head: str) -> dict[str, object]:
    fingerprints = _populate_synthetic_index_root(root)
    fixed_now = BASE_NS + 1_000_000
    started = perf_counter()
    first = _rebuild_index(
        root,
        builder_git_commit=head,
        incremental=False,
        now_unix_ns=fixed_now,
        stale_after_ns=_STALE_AFTER_NS,
    )
    full_seconds = perf_counter() - started
    assert first._run_count == _INDEX_RUN_COUNT
    first_dump = _canonical_table_dump(first._path)
    database_bytes = first._path.stat().st_size
    first._path.unlink()
    started = perf_counter()
    rebuilt = _rebuild_index(
        root,
        builder_git_commit=head,
        incremental=False,
        now_unix_ns=fixed_now,
        stale_after_ns=_STALE_AFTER_NS,
    )
    replacement_seconds = perf_counter() - started
    assert _canonical_table_dump(rebuilt._path) == first_dump
    started = perf_counter()
    incremental = _rebuild_index(
        root,
        builder_git_commit=head,
        incremental=True,
        now_unix_ns=fixed_now + 1,
        stale_after_ns=_STALE_AFTER_NS,
    )
    incremental_seconds = perf_counter() - started
    assert incremental._generation == 2

    query_samples, query_median = _timed_samples(
        lambda: _query_runs(
            root,
            states=("COMPLETE",),
            metric_name="rank_ic",
            plane="A",
            order_by="metric",
            limit=_INDEX_EVALUATED_COUNT,
        )
    )
    compare_samples, compare_median = _timed_samples(
        lambda: _compare_metric(
            root,
            metric_name="rank_ic",
            fingerprints=fingerprints,
            plane="A",
        )
    )
    ledger_samples, ledger_median = _timed_samples(lambda: _index_trial_ledger(root))
    files_ledger = _files_trial_ledger(root, now_unix_ns=fixed_now + 2)
    index_ledger = _index_trial_ledger(root)
    assert files_ledger._document == index_ledger._document
    assert index_ledger._document["plane_a_trials"] == _INDEX_EVALUATED_COUNT
    assert compare_median < 1.0
    serialized_ledger = json.dumps(index_ledger._document, sort_keys=True).lower()
    assert "p_value" not in serialized_ledger
    assert "adjusted_metric" not in serialized_ledger
    return {
        "run_count": first._run_count,
        "evaluated_run_count": _INDEX_EVALUATED_COUNT,
        "full_rebuild_seconds": full_seconds,
        "delete_and_rebuild_seconds": replacement_seconds,
        "incremental_rebuild_seconds": incremental_seconds,
        "database_bytes": database_bytes,
        "query_samples_seconds": query_samples,
        "query_median_seconds": query_median,
        "comparison_samples_seconds": compare_samples,
        "comparison_median_seconds": compare_median,
        "trial_ledger_samples_seconds": ledger_samples,
        "trial_ledger_median_seconds": ledger_median,
        "files_index_ledger_equal": True,
        "delete_rebuild_content_equal": True,
    }


def _threshold_evidence(root: Path) -> dict[str, object]:
    seventy = SimpleNamespace(f_blocks=1_000, f_frsize=1, f_bavail=300)
    ninety = SimpleNamespace(f_blocks=1_000, f_frsize=1, f_bavail=100)
    with patch(
        "trading_research.experiments.resources._os.statvfs",
        return_value=seventy,
    ):
        warning = _disk_usage(root)
    with patch(
        "trading_research.experiments.resources._os.statvfs",
        return_value=ninety,
    ):
        refusal = _disk_usage(root)
        with pytest.raises(ContractViolationError, match="90 percent"):
            _enforce_disk_preflight(root, allow_critical_usage=False)
        overridden = _enforce_disk_preflight(root, allow_critical_usage=True)
    assert warning._warning
    assert not warning._refusal
    assert refusal._warning
    assert refusal._refusal
    assert overridden == refusal
    return {
        "warning_basis_points": warning._used_basis_points,
        "refusal_basis_points": refusal._used_basis_points,
        "critical_override_exercised": True,
    }


def _render_evidence(document: Mapping[str, object]) -> None:
    print("PHASE5_EXPERIMENT_OPS_EVIDENCE_BEGIN")
    print(json.dumps(document, sort_keys=True, allow_nan=False, separators=(",", ":")))
    print("PHASE5_EXPERIMENT_OPS_EVIDENCE_END")


def test_phase5_sweep_recovery_index_and_ledger_qualification(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise the complete Phase-5 scale and crash-recovery contract once."""
    head = _clean_committed_head()
    external_before = _external_snapshot()
    _assert_expected_external_snapshot(external_before)
    disk_before = _disk_usage(_RUNS_TMP_ROOT)

    with TemporaryDirectory(
        prefix=f"phase5-qualification-{head[:12]}-",
        dir=_RUNS_TMP_ROOT,
    ) as root_text:
        qualification_root = Path(root_text).resolve()
        runs_root = qualification_root / "runtime"
        runtime, paths = _runtime(runs_root)
        cold_plan = _plan(
            runtime,
            paths,
            sweep_id="phase5-cold-100",
            seeds=range(0, _CHILD_COUNT),
        )
        cold = _run_monitored_dispatcher(runs_root, "phase5-cold-100")
        assert cold.maximum_concurrency >= _REQUESTED_WORKERS
        assert cold.at_capacity_fraction >= 0.5
        assert cold.maximum_observed_concurrent_rss_bytes <= _MEMORY_BUDGET_BYTES
        assert len(_complete_fingerprints(runs_root, cold_plan)) == _CHILD_COUNT

        forced_plan = _plan(
            runtime,
            paths,
            sweep_id="phase5-forced-kill-100",
            seeds=range(_CHILD_COUNT, _CHILD_COUNT * 2),
        )
        terminal_baseline = _manifest_count(runs_root)
        killed, completed_at_kill, workers_seen_before_kill = (
            _kill_dispatcher_and_two_running_children(
                runs_root,
                "phase5-forced-kill-100",
                terminal_baseline=terminal_baseline,
            )
        )
        complete_before = _complete_fingerprints(runs_root, forced_plan)
        cache_before = _cache_snapshot(runs_root)
        assert cache_before
        resume, resume_seconds = _resume_forced_sweep(
            runs_root,
            forced_plan,
            complete_before=complete_before,
        )
        cache_after = _cache_snapshot(runs_root)
        assert all(cache_after.get(name) == value for name, value in cache_before.items())

        actual_index = _rebuild_index(
            runs_root,
            builder_git_commit=head,
            incremental=False,
            stale_after_ns=_STALE_AFTER_NS,
        )
        actual_ledger = _index_trial_ledger(runs_root, audit_path=_AUDIT_PATH)
        assert actual_index._run_count >= _CHILD_COUNT * 2
        assert actual_ledger._document["plane_a_trials"] == _CHILD_COUNT * 2
        assert actual_ledger._document["audit_discrepancies"] == []
        assert actual_ledger._document["oos_access_events"] == 0
        assert actual_ledger._document["lockbox_access_events"] == 0

        synthetic_index = _qualify_index(qualification_root / "synthetic-index", head)
        threshold = _threshold_evidence(runs_root)
        disk_during = _disk_usage(_RUNS_TMP_ROOT)
        qualification_path = str(qualification_root)

    assert not Path(qualification_path).exists()
    disk_after = _disk_usage(_RUNS_TMP_ROOT)
    external_after = _external_snapshot()
    assert external_after == external_before

    evidence: dict[str, object] = {
        "schema": _EVIDENCE_SCHEMA,
        "git_commit": head,
        "target_dataset_id": TRAIN_REF.dataset_id,
        "target_manifest_sha256": TRAIN_REF.manifest_sha256,
        "target_role": TRAIN_REF.role.value,
        "qualification_root": qualification_path,
        "qualification_root_removed_after_measurement": True,
        "cold_sweep": {
            "children": _CHILD_COUNT,
            "elapsed_seconds": cold.elapsed_seconds,
            "worker_pids": cold.worker_pids,
            "clamp_verified_pids": cold.clamp_verified_pids,
            "maximum_concurrency": cold.maximum_concurrency,
            "at_capacity_fraction": cold.at_capacity_fraction,
            "maximum_observed_concurrent_rss_bytes": (
                cold.maximum_observed_concurrent_rss_bytes
            ),
            "maximum_observed_worker_rss_bytes": cold.maximum_observed_worker_rss_bytes,
            "median_observed_worker_peak_rss_bytes": (
                cold.median_observed_worker_peak_rss_bytes
            ),
        },
        "forced_kill_sweep": {
            "children": _CHILD_COUNT,
            "completed_at_kill": completed_at_kill,
            "workers_seen_before_kill": workers_seen_before_kill,
            "killed_workers": [
                {"pid": int(pid), "run_id": run_id} for pid, run_id in killed
            ],
            "resume_seconds": resume_seconds,
            **resume,
            "preexisting_cache_files_unchanged": len(cache_before),
        },
        "index": synthetic_index,
        "trial_ledger": {
            "plane_a_trials": actual_ledger._document["plane_a_trials"],
            "plane_b_trials": actual_ledger._document["plane_b_trials"],
            "distinct_hypotheses": actual_ledger._document["distinct_hypotheses"],
            "audit_discrepancies": actual_ledger._document["audit_discrepancies"],
            "caveat": actual_ledger._document["caveat"],
        },
        "disk": {
            "before_basis_points": disk_before._used_basis_points,
            "during_basis_points": disk_during._used_basis_points,
            "after_cleanup_basis_points": disk_after._used_basis_points,
            **threshold,
        },
        "external_before": external_before,
        "external_after": external_after,
        "memory_max": (
            "not measured: foreground dispatcher required for per-worker /proc telemetry"
        ),
    }
    with capsys.disabled():
        _render_evidence(evidence)
