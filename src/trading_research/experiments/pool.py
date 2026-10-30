"""Bounded whole-run process pool with files-first sweep resume."""

from __future__ import annotations

import argparse as _argparse
import errno as _errno
import fcntl as _fcntl
import json as _json
import os as _os
import stat as _stat
import subprocess as _subprocess
import sys as _sys
import time as _time
import typing as _typing
import uuid as _uuid
from contextlib import contextmanager as _contextmanager
from contextlib import suppress as _suppress
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import (
    _fail_pending_run,
    _register_experiment,
    _runtime_from_paths,
    _RuntimeEnvironment,
)
from trading_research.experiments.resources import (
    _DEFAULT_ASSUMED_PEAK_RSS_BYTES,
    _DEFAULT_MAX_WORKERS,
    _DEFAULT_MEMORY_BUDGET_BYTES,
    _effective_worker_count,
    _enforce_disk_preflight,
    _worker_environment,
)
from trading_research.experiments.runs import (
    RunState as _RunState,
)
from trading_research.experiments.runs import (
    _observe_run_lifecycle,
    _RunProjection,
    _scan_run_projections,
)
from trading_research.experiments.sweeps import (
    _count_oos_access_events,
    _PlannedChild,
    _read_sweep_plan,
    _runtime_paths_from_manifest,
    _SweepPlan,
    _verify_sweep_fingerprints,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Callable as _Callable
    from collections.abc import Iterator as _Iterator
    from collections.abc import Mapping as _Mapping
    from collections.abc import Sequence as _Sequence
    from typing import Protocol as _Protocol

    from trading_research.experiments.resources import _DiskUsage
    from trading_research.experiments.sweeps import _SweepRuntimePaths

    class _ChildProcess(_Protocol):
        @property
        def pid(self) -> int: ...

        def poll(self) -> int | None: ...


__all__: list[str] = []

_INVOCATION_SCHEMA = "research-sweep-invocation-v1"
_COMPLETION_SCHEMA = "research-sweep-invocation-completion-v1"
_DETACHED_LAUNCH_SCHEMA = "research-sweep-detached-launch-v1"
_DEFAULT_STALE_AFTER_NS = 60_000_000_000
_DEFAULT_POLL_INTERVAL_SECONDS = 0.2


class _SweepPoolError(_TradingResearchError):
    """A sweep cannot be dispatched without violating its persisted plan."""


@_dataclass(frozen=True, slots=True)
class _QueuedChild:
    _child: _PlannedChild
    _existing_run_id: str | None


@_dataclass(frozen=True, slots=True)
class _ResumePlan:
    _queue: tuple[_QueuedChild, ...]
    _adopted_run_ids: tuple[str, ...]
    _skipped_complete: int
    _skipped_failed: int


@_dataclass(frozen=True, slots=True)
class _SweepRunSummary:
    _sweep_id: str
    _effective_workers: int
    _children: int
    _complete: int
    _failed: int
    _interrupted: int
    _pending: int
    _running: int
    _dispatched: int
    _adopted: int
    _skipped_complete: int
    _skipped_failed: int
    _requires_resume: bool
    _disk_used_basis_points: int
    _invocation_id: str

    @property
    def _successful(self) -> bool:
        return (
            self._complete == self._children
            and self._failed == 0
            and self._interrupted == 0
            and self._pending == 0
            and self._running == 0
            and not self._requires_resume
        )


def _run_sweep(
    runs_root: _Path,
    sweep_id: str,
    *,
    requested_workers: int = _DEFAULT_MAX_WORKERS,
    memory_budget_bytes: int = _DEFAULT_MEMORY_BUDGET_BYTES,
    assumed_peak_rss_bytes: int = _DEFAULT_ASSUMED_PEAK_RSS_BYTES,
    retry_failed: bool,
    allow_noncanonical: bool,
    acknowledge_oos_access: bool,
    allow_critical_disk_usage: bool,
    stale_after_ns: int = _DEFAULT_STALE_AFTER_NS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    launch_mode: str = "foreground-process-pool",
    _launcher: _Callable[[tuple[str, ...], _Mapping[str, str]], _ChildProcess]
    | None = None,
    _sleep: _Callable[[float], None] = _time.sleep,
) -> _SweepRunSummary:
    """Run or resume one immutable sweep using only plan and run files."""
    _validate_run_options(
        retry_failed=retry_failed,
        allow_noncanonical=allow_noncanonical,
        acknowledge_oos_access=acknowledge_oos_access,
        allow_critical_disk_usage=allow_critical_disk_usage,
        stale_after_ns=stale_after_ns,
        poll_interval_seconds=poll_interval_seconds,
        launch_mode=launch_mode,
    )
    root = _validated_runs_root(runs_root)
    plan = _read_sweep_plan(root, sweep_id)
    runtime = _runtime_for_plan(plan)
    if runtime._runs_root.resolve() != root:
        raise _ContractViolationError("sweep runtime and invocation runs roots differ")
    _verify_execution_identity(
        plan,
        runtime,
        allow_noncanonical=allow_noncanonical,
    )
    current_oos_count = _verify_oos_resume(
        plan,
        acknowledge_oos_access=acknowledge_oos_access,
    )
    disk = _enforce_disk_preflight(
        root,
        allow_critical_usage=allow_critical_disk_usage,
    )
    effective_workers = _effective_worker_count(
        requested_workers=requested_workers,
        memory_budget_bytes=memory_budget_bytes,
        assumed_peak_rss_bytes=assumed_peak_rss_bytes,
    )
    invocation_id = str(_uuid.uuid4())
    launcher = _default_launcher if _launcher is None else _launcher

    with _dispatcher_lock(root, sweep_id):
        _append_invocation_event(
            root,
            sweep_id,
            _invocation_document(
                invocation_id=invocation_id,
                plan=plan,
                requested_workers=requested_workers,
                effective_workers=effective_workers,
                memory_budget_bytes=memory_budget_bytes,
                assumed_peak_rss_bytes=assumed_peak_rss_bytes,
                retry_failed=retry_failed,
                allow_noncanonical=allow_noncanonical,
                acknowledge_oos_access=acknowledge_oos_access,
                allow_critical_disk_usage=allow_critical_disk_usage,
                current_oos_count=current_oos_count,
                disk=disk,
                launch_mode=launch_mode,
            ),
        )
        try:
            summary = _dispatch_plan(
                plan,
                runtime=runtime,
                effective_workers=effective_workers,
                retry_failed=retry_failed,
                stale_after_ns=stale_after_ns,
                poll_interval_seconds=poll_interval_seconds,
                invocation_id=invocation_id,
                disk=disk,
                launcher=launcher,
                sleep=_sleep,
            )
        except BaseException as exc:
            _append_invocation_event(
                root,
                sweep_id,
                {
                    "schema": _COMPLETION_SCHEMA,
                    "invocation_id": invocation_id,
                    "finished_unix_ns": _time.time_ns(),
                    "status": "dispatcher_error",
                    "error_kind": type(exc).__name__,
                    "error_detail": (" ".join(str(exc).split()) or "no detail")[:2_000],
                },
            )
            raise
        _append_invocation_event(
            root,
            sweep_id,
            {
                "schema": _COMPLETION_SCHEMA,
                "invocation_id": invocation_id,
                "finished_unix_ns": _time.time_ns(),
                "status": "complete" if summary._successful else "incomplete",
                "summary": _summary_document(summary),
            },
        )
        return summary


def _dispatch_plan(
    plan: _SweepPlan,
    *,
    runtime: _RuntimeEnvironment,
    effective_workers: int,
    retry_failed: bool,
    stale_after_ns: int,
    poll_interval_seconds: float,
    invocation_id: str,
    disk: _DiskUsage,
    launcher: _Callable[[tuple[str, ...], _Mapping[str, str]], _ChildProcess],
    sleep: _Callable[[float], None],
) -> _SweepRunSummary:
    initial = _scan_run_projections(
        runtime._runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=stale_after_ns,
        reconcile=True,
        tolerate_corruption=False,
    )
    resume = _resume_plan(
        plan,
        initial._projections,
        retry_failed=retry_failed,
    )
    queue = list(resume._queue)
    adopted = set(resume._adopted_run_ids)
    active: dict[str, _ChildProcess] = {}
    dispatched = 0
    requires_resume = False
    # OOS uses its structured reason but never a fully-gated unlock token.
    # LOCKBOX sweeps are rejected by the planner, so no sweep worker may carry
    # --unlock-gated.
    unlock_gated = False
    paths = _runtime_paths_from_manifest(plan._document)
    heartbeat_seconds = paths._heartbeat_interval_ns / 1_000_000_000
    environment = _worker_environment()
    states = {
        projection._record._run_id: projection._record._state
        for projection in initial._projections
    }
    heartbeat_stale = {
        projection._record._run_id: projection._record._heartbeat_stale
        for projection in initial._projections
    }

    while queue or adopted or active:
        return_codes = {
            run_id: process.poll() for run_id, process in active.items()
        }
        # The initial/final rich scans validate every terminal payload hash.
        # Scheduling only needs the locked lifecycle files for an exited child
        # or adopted orphan; repeatedly rehashing all prior results would make
        # refill cost grow quadratically with sweep size.
        refresh = set(adopted)
        refresh.update(
            run_id for run_id, return_code in return_codes.items() if return_code is not None
        )
        for run_id in sorted(refresh):
            try:
                observation = _observe_run_lifecycle(
                    runtime._runs_root,
                    run_id=run_id,
                    now_unix_ns=_time.time_ns(),
                    stale_after_ns=stale_after_ns,
                )
            except FileNotFoundError as exc:
                raise _SweepPoolError(f"observed run {run_id!r} disappeared") from exc
            states[run_id] = observation._state
            heartbeat_stale[run_id] = observation._heartbeat_stale

        for run_id in tuple(active):
            state = states.get(run_id)
            return_code = return_codes[run_id]
            if state in _terminal_states():
                active.pop(run_id)
                continue
            if return_code is not None:
                if state is None:
                    raise _SweepPoolError(f"dispatched run {run_id!r} disappeared")
                if state is _RunState.PENDING:
                    _fail_pending_run(
                        runtime=runtime,
                        run_id=run_id,
                        reason=f"worker process exited {return_code} before starting",
                    )
                    active.pop(run_id)
                elif heartbeat_stale[run_id]:
                    requires_resume = True
                    active.pop(run_id)

        for run_id in tuple(adopted):
            state = states.get(run_id)
            if state is None:
                raise _SweepPoolError(f"adopted run {run_id!r} disappeared")
            if state in _terminal_states():
                adopted.remove(run_id)
            elif heartbeat_stale[run_id]:
                adopted.remove(run_id)
                requires_resume = True

        occupied = len(active) + len(adopted)
        while queue and occupied < effective_workers:
            work = queue.pop(0)
            candidate_run_id = work._existing_run_id
            if candidate_run_id is None:
                config = _config_from_document(work._child._config_document)
                candidate_run_id = _register_experiment(config, runtime=runtime)
            command = _worker_command(
                paths=paths,
                run_id=candidate_run_id,
                unlock_gated=unlock_gated,
                heartbeat_interval_seconds=heartbeat_seconds,
            )
            try:
                process = launcher(command, environment)
            except OSError as exc:
                _fail_pending_run(
                    runtime=runtime,
                    run_id=candidate_run_id,
                    reason=f"worker launch failed: {type(exc).__name__}: {exc}",
                )
                raise _SweepPoolError(f"cannot launch sweep child {candidate_run_id}") from exc
            active[candidate_run_id] = process
            states[candidate_run_id] = _RunState.PENDING
            heartbeat_stale[candidate_run_id] = False
            dispatched += 1
            occupied += 1

        if queue or adopted or active:
            sleep(poll_interval_seconds)

    final = _scan_run_projections(
        runtime._runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=stale_after_ns,
        reconcile=False,
        tolerate_corruption=False,
    )._projections
    counts = _child_state_counts(plan, final)
    return _SweepRunSummary(
        _sweep_id=_sweep_id(plan),
        _effective_workers=effective_workers,
        _children=len(plan._children),
        _complete=counts[_RunState.COMPLETE],
        _failed=counts[_RunState.FAILED],
        _interrupted=counts[_RunState.INTERRUPTED],
        _pending=counts[_RunState.PENDING],
        _running=counts[_RunState.RUNNING],
        _dispatched=dispatched,
        _adopted=len(resume._adopted_run_ids),
        _skipped_complete=resume._skipped_complete,
        _skipped_failed=resume._skipped_failed,
        _requires_resume=requires_resume,
        _disk_used_basis_points=disk._used_basis_points,
        _invocation_id=invocation_id,
    )


def _resume_plan(
    plan: _SweepPlan,
    projections: tuple[_RunProjection, ...],
    *,
    retry_failed: bool,
) -> _ResumePlan:
    if type(retry_failed) is not bool:
        raise _ContractViolationError("retry_failed must be an exact bool")
    by_fingerprint: dict[str, list[_RunProjection]] = {}
    for projection in projections:
        by_fingerprint.setdefault(
            projection._record._experiment_fingerprint,
            [],
        ).append(projection)
    queue: list[_QueuedChild] = []
    adopted: list[str] = []
    skipped_complete = 0
    skipped_failed = 0
    for child in plan._children:
        attempts = by_fingerprint.get(child._experiment_fingerprint, [])
        if any(item._record._state is _RunState.COMPLETE for item in attempts):
            skipped_complete += 1
            continue
        running = tuple(
            item
            for item in attempts
            if item._record._state is _RunState.RUNNING
            and not item._record._heartbeat_stale
        )
        if running:
            adopted.extend(item._record._run_id for item in running)
            continue
        pending = tuple(item for item in attempts if item._record._state is _RunState.PENDING)
        if len(pending) > 1:
            raise _SweepPoolError(
                "multiple PENDING attempts share one planned fingerprint; refusing an "
                "ambiguous dispatch"
            )
        if pending:
            queue.append(_QueuedChild(child, pending[0]._record._run_id))
            continue
        failed = any(item._record._state is _RunState.FAILED for item in attempts)
        if failed and not retry_failed:
            skipped_failed += 1
            continue
        queue.append(_QueuedChild(child, None))
    return _ResumePlan(tuple(queue), tuple(adopted), skipped_complete, skipped_failed)


def _runtime_for_plan(plan: _SweepPlan) -> _RuntimeEnvironment:
    if not plan._children:
        raise _SweepPoolError("sweep has no children")
    paths = _runtime_paths_from_manifest(plan._document)
    config = _config_from_document(plan._children[0]._config_document)
    return _runtime_from_paths(
        config=config,
        data_roots=paths._data_roots,
        runs_root=paths._runs_root,
        split_policy_path=paths._split_policy_path,
        audit_path=paths._audit_path,
        repository_root=paths._repository_root,
        dependency_lock_path=paths._dependency_lock_path,
    )


def _verify_execution_identity(
    plan: _SweepPlan,
    runtime: _RuntimeEnvironment,
    *,
    allow_noncanonical: bool,
) -> None:
    platform = plan._document.get("platform")
    if type(platform) is not dict:
        raise _SweepPoolError("sweep platform is malformed")
    dirty = platform.get("git_dirty")
    if dirty is True and not allow_noncanonical:
        raise _SweepPoolError(
            "sweep was planned from a dirty tree; --allow-noncanonical is required"
        )
    _verify_sweep_fingerprints(plan, runtime)


def _verify_oos_resume(
    plan: _SweepPlan,
    *,
    acknowledge_oos_access: bool,
) -> int:
    protection = plan._document.get("protection")
    if type(protection) is not dict:
        raise _SweepPoolError("sweep protection is malformed")
    planned = protection.get("oos_access_events_at_plan")
    if type(planned) is not int or planned < 0:
        raise _SweepPoolError("sweep plan-time OOS access count is malformed")
    audit_path = _runtime_paths_from_manifest(plan._document)._audit_path
    current = _count_oos_access_events(audit_path)
    if current < planned:
        raise _SweepPoolError("append-only OOS audit count is lower than at sweep planning")
    if current > planned and not acknowledge_oos_access:
        raise _SweepPoolError(
            "OOS access count increased since planning; --acknowledge-oos-access is required"
        )
    return current


def _child_state_counts(
    plan: _SweepPlan,
    projections: tuple[_RunProjection, ...],
) -> dict[_RunState, int]:
    by_fingerprint: dict[str, list[_RunState]] = {}
    for projection in projections:
        by_fingerprint.setdefault(
            projection._record._experiment_fingerprint,
            [],
        ).append(projection._record._state)
    counts = dict.fromkeys(_RunState, 0)
    priority = (
        _RunState.COMPLETE,
        _RunState.RUNNING,
        _RunState.PENDING,
        _RunState.FAILED,
        _RunState.INTERRUPTED,
    )
    for child in plan._children:
        states = by_fingerprint.get(child._experiment_fingerprint, [])
        chosen = next((state for state in priority if state in states), _RunState.INTERRUPTED)
        counts[chosen] += 1
    return counts


def _worker_command(
    *,
    paths: _SweepRuntimePaths,
    run_id: str,
    unlock_gated: bool,
    heartbeat_interval_seconds: float,
) -> tuple[str, ...]:
    command = [
        _sys.executable,
        "-m",
        "trading_research.experiments.worker",
        "--run-id",
        run_id,
    ]
    for root in paths._data_roots:
        command.extend(("--data-root", str(root)))
    command.extend(
        (
            "--runs-root",
            str(paths._runs_root),
            "--split-policy",
            str(paths._split_policy_path),
            "--audit",
            str(paths._audit_path),
            "--repository-root",
            str(paths._repository_root),
            "--dependency-lock",
            str(paths._dependency_lock_path),
            "--heartbeat-interval-seconds",
            str(heartbeat_interval_seconds),
        )
    )
    if unlock_gated:
        command.append("--unlock-gated")
    return tuple(command)


def _default_launcher(
    command: tuple[str, ...],
    environment: _Mapping[str, str],
) -> _ChildProcess:
    return _subprocess.Popen(command, env=dict(environment))


def _terminal_states() -> frozenset[_RunState]:
    return frozenset({_RunState.COMPLETE, _RunState.FAILED, _RunState.INTERRUPTED})


@_contextmanager
def _dispatcher_lock(runs_root: _Path, sweep_id: str) -> _Iterator[None]:
    directory = runs_root / "sweeps" / sweep_id
    if not directory.is_dir() or directory.is_symlink():
        raise _SweepPoolError("sweep directory must be concrete")
    flags = _os.O_RDWR | _os.O_CREAT | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    try:
        descriptor = _os.open(directory / "dispatcher.lock", flags, 0o600)
    except OSError as exc:
        raise _SweepPoolError("cannot open sweep dispatcher lock") from exc
    try:
        metadata = _os.fstat(descriptor)
    except OSError as exc:
        _os.close(descriptor)
        raise _SweepPoolError("cannot open sweep dispatcher lock") from exc
    if not _stat.S_ISREG(metadata.st_mode):
        _os.close(descriptor)
        raise _SweepPoolError("sweep dispatcher lock must be a regular file")
    try:
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {_errno.EACCES, _errno.EAGAIN}:
                raise _SweepPoolError("another dispatcher already owns this sweep") from exc
            raise
        yield
    finally:
        with _suppress(OSError):
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        _os.close(descriptor)


def _append_invocation_event(
    runs_root: _Path,
    sweep_id: str,
    document: dict[str, object],
) -> None:
    payload = (_canonical_json(document) + "\n").encode("ascii")
    directory = runs_root / "sweeps" / sweep_id
    flags = _os.O_WRONLY | _os.O_CREAT | _os.O_APPEND | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    try:
        descriptor = _os.open(directory / "invocations.jsonl", flags, 0o600)
        try:
            metadata = _os.fstat(descriptor)
            if not _stat.S_ISREG(metadata.st_mode):
                raise _SweepPoolError("sweep invocation receipt must be a regular file")
            _fcntl.flock(descriptor, _fcntl.LOCK_EX)
            try:
                _write_all(descriptor, payload)
                _os.fsync(descriptor)
            finally:
                _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        finally:
            _os.close(descriptor)
    except OSError as exc:
        raise _SweepPoolError("cannot append sweep invocation receipt") from exc


def _record_detached_launch(
    runs_root: _Path,
    sweep_id: str,
    *,
    launch_request_id: str,
    launcher: str,
    memory_budget_bytes: int,
) -> None:
    """Record launcher enforcement honestly; never consult this for resume."""
    try:
        parsed_request_id = _uuid.UUID(launch_request_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _ContractViolationError("launch_request_id must be UUID4 text") from exc
    if parsed_request_id.version != 4 or str(parsed_request_id) != launch_request_id:
        raise _ContractViolationError("launch_request_id must be canonical UUID4 text")
    if type(launcher) is not str or not launcher:
        raise _ContractViolationError("detached launcher must be non-empty text")
    if type(memory_budget_bytes) is not int or memory_budget_bytes <= 0:
        raise _ContractViolationError("memory budget must be an exact positive int")
    _append_invocation_event(
        _validated_runs_root(runs_root),
        sweep_id,
        {
            "schema": _DETACHED_LAUNCH_SCHEMA,
            "launch_request_id": launch_request_id,
            "recorded_unix_ns": _time.time_ns(),
            "launcher": launcher,
            "memory_budget_bytes": memory_budget_bytes,
            "memory_max_enforced": launcher == "systemd-run-memorymax",
        },
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = _os.write(descriptor, view)
        if written <= 0:
            raise OSError("short invocation receipt write")
        view = view[written:]


def _invocation_document(
    *,
    invocation_id: str,
    plan: _SweepPlan,
    requested_workers: int,
    effective_workers: int,
    memory_budget_bytes: int,
    assumed_peak_rss_bytes: int,
    retry_failed: bool,
    allow_noncanonical: bool,
    acknowledge_oos_access: bool,
    allow_critical_disk_usage: bool,
    current_oos_count: int,
    disk: _DiskUsage,
    launch_mode: str,
) -> dict[str, object]:
    return {
        "schema": _INVOCATION_SCHEMA,
        "invocation_id": invocation_id,
        "started_unix_ns": _time.time_ns(),
        "sweep_id": _sweep_id(plan),
        "spec_digest": plan._document["spec_digest"],
        "requested_workers": requested_workers,
        "effective_workers": effective_workers,
        "memory_budget_bytes": memory_budget_bytes,
        "assumed_peak_rss_bytes": assumed_peak_rss_bytes,
        "retry_failed": retry_failed,
        "allow_noncanonical": allow_noncanonical,
        "acknowledge_oos_access": acknowledge_oos_access,
        "allow_critical_disk_usage": allow_critical_disk_usage,
        "oos_access_events_observed": current_oos_count,
        "disk": {
            "total_bytes": disk._total_bytes,
            "used_bytes": disk._used_bytes,
            "available_bytes": disk._available_bytes,
            "used_basis_points": disk._used_basis_points,
            "warning": disk._warning,
            "refusal": disk._refusal,
        },
        "launch_mode": launch_mode,
    }


def _summary_document(summary: _SweepRunSummary) -> dict[str, object]:
    return {
        "sweep_id": summary._sweep_id,
        "effective_workers": summary._effective_workers,
        "children": summary._children,
        "complete": summary._complete,
        "failed": summary._failed,
        "interrupted": summary._interrupted,
        "pending": summary._pending,
        "running": summary._running,
        "dispatched": summary._dispatched,
        "adopted": summary._adopted,
        "skipped_complete": summary._skipped_complete,
        "skipped_failed": summary._skipped_failed,
        "requires_resume": summary._requires_resume,
        "disk_used_basis_points": summary._disk_used_basis_points,
        "invocation_id": summary._invocation_id,
        "successful": summary._successful,
    }


def _sweep_id(plan: _SweepPlan) -> str:
    value = plan._document.get("sweep_id")
    if type(value) is not str:
        raise _SweepPoolError("sweep ID is malformed")
    return value


def _validated_runs_root(value: _Path) -> _Path:
    if not isinstance(value, _Path) or not value.is_absolute():
        raise _ContractViolationError("runs_root must be an absolute Path")
    if not value.is_dir() or value.is_symlink():
        raise _ContractViolationError("runs_root must be a concrete existing directory")
    return value.resolve()


def _validate_run_options(
    *,
    retry_failed: bool,
    allow_noncanonical: bool,
    acknowledge_oos_access: bool,
    allow_critical_disk_usage: bool,
    stale_after_ns: int,
    poll_interval_seconds: float,
    launch_mode: str,
) -> None:
    for value, label in (
        (retry_failed, "retry_failed"),
        (allow_noncanonical, "allow_noncanonical"),
        (acknowledge_oos_access, "acknowledge_oos_access"),
        (allow_critical_disk_usage, "allow_critical_disk_usage"),
    ):
        if type(value) is not bool:
            raise _ContractViolationError(f"{label} must be an exact bool")
    if type(stale_after_ns) is not int or stale_after_ns <= 0:
        raise _ContractViolationError("stale_after_ns must be an exact positive int")
    if type(poll_interval_seconds) not in {int, float} or not (
        0.0 < float(poll_interval_seconds) <= 10.0
    ):
        raise _ContractViolationError("poll interval must be in (0, 10] seconds")
    if type(launch_mode) is not str or not launch_mode.strip():
        raise _ContractViolationError("launch_mode must be non-empty exact text")


def _canonical_json(value: object) -> str:
    return _json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _absolute_path(value: str) -> _Path:
    return _Path(value).expanduser().resolve()


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="trading-research-sweep-worker")
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--sweep", required=True)
    parser.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS)
    parser.add_argument("--memory-budget-bytes", type=int, default=_DEFAULT_MEMORY_BUDGET_BYTES)
    parser.add_argument(
        "--assumed-peak-rss-bytes",
        type=int,
        default=_DEFAULT_ASSUMED_PEAK_RSS_BYTES,
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--allow-noncanonical", action="store_true")
    parser.add_argument("--acknowledge-oos-access", action="store_true")
    parser.add_argument("--allow-critical-disk-usage", action="store_true")
    parser.add_argument("--stale-after-ns", type=int, default=_DEFAULT_STALE_AFTER_NS)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.2)
    parser.add_argument("--launch-mode", default="detached-process-pool")
    return parser


def _main(argv: _Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = _run_sweep(
        args.runs_root,
        args.sweep,
        requested_workers=args.max_workers,
        memory_budget_bytes=args.memory_budget_bytes,
        assumed_peak_rss_bytes=args.assumed_peak_rss_bytes,
        retry_failed=args.retry_failed,
        allow_noncanonical=args.allow_noncanonical,
        acknowledge_oos_access=args.acknowledge_oos_access,
        allow_critical_disk_usage=args.allow_critical_disk_usage,
        stale_after_ns=args.stale_after_ns,
        poll_interval_seconds=args.poll_interval_seconds,
        launch_mode=args.launch_mode,
    )
    _sys.stdout.write(_canonical_json(_summary_document(summary)) + "\n")
    return 0 if summary._successful else 1


if __name__ == "__main__":  # pragma: no cover - private detached entry point
    try:
        _exit_code = _main()
    except BaseException as exc:
        _sys.stderr.write(f"sweep dispatcher failed: {type(exc).__name__}: {exc}\n")
        raise
    raise SystemExit(_exit_code)
