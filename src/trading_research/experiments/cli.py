"""Four-command Phase-3 command line for runs and verification."""

from __future__ import annotations

import argparse as _argparse
import json as _json
import subprocess as _subprocess
import sys as _sys
import time as _time
import typing as _typing
from pathlib import Path as _Path

from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.experiments.config import (
    ResolvedExperimentConfig as _ResolvedExperimentConfig,
)
from trading_research.experiments.config import (
    load_resolved_config as _load_resolved_config,
)
from trading_research.experiments.orchestration import (
    _fail_pending_run,
    _register_experiment,
    _runtime_from_paths,
    _RuntimeEnvironment,
)
from trading_research.experiments.orchestration import (
    run_experiment as _run_experiment,
)
from trading_research.experiments.orchestration import (
    verify_experiment as _verify_experiment,
)
from trading_research.experiments.resources import (
    _THREAD_CLAMPS,
    _worker_environment,
)
from trading_research.experiments.runs import (
    RunManifest as _RunManifest,
)
from trading_research.experiments.runs import (
    RunState as _RunState,
)
from trading_research.experiments.runs import (
    _RunRecord,
)
from trading_research.experiments.runs import (
    scan_runs as _scan_runs,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Sequence as _Sequence

__all__ = ["main"]


class _DetachedLaunchError(_TradingResearchError):
    """Neither approved detached launcher accepted the worker."""


def main(argv: _Sequence[str] | None = None) -> int:
    """Run exactly one of ``run``, ``status``, ``list``, or ``verify``."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (_TradingResearchError, OSError, ValueError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="trading-research")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="register and launch one resolved config")
    _add_execution_arguments(run)
    run.add_argument("--foreground", action="store_true")
    run.add_argument("--json", action="store_true", dest="json_output")

    status = commands.add_parser("status", help="show one run")
    status.add_argument("run_id")
    _add_scan_arguments(status)
    status.add_argument("--json", action="store_true", dest="json_output")

    listing = commands.add_parser("list", help="list all runs")
    _add_scan_arguments(listing)
    listing.add_argument("--json", action="store_true", dest="json_output")

    verify = commands.add_parser("verify", help="cache-bypassing double execution")
    _add_execution_arguments(verify)
    verify.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _add_execution_arguments(parser: _argparse.ArgumentParser) -> None:
    parser.add_argument("config", type=_absolute_path)
    parser.add_argument("--data-root", action="append", required=True, type=_absolute_path)
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--split-policy", required=True, type=_absolute_path)
    parser.add_argument("--audit", required=True, type=_absolute_path)
    parser.add_argument("--repository-root", required=True, type=_absolute_path)
    parser.add_argument("--dependency-lock", required=True, type=_absolute_path)
    parser.add_argument("--unlock-gated", action="store_true")
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=5.0)


def _add_scan_arguments(parser: _argparse.ArgumentParser) -> None:
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--stale-after-ns", required=True, type=int)


def _absolute_path(value: str) -> _Path:
    if type(value) is not str or not value:
        raise _argparse.ArgumentTypeError("path must be non-empty")
    return _Path(value).expanduser().resolve()


def _dispatch(args: _argparse.Namespace) -> int:
    if args.command == "run":
        return _run_command(args)
    if args.command == "status":
        return _status_command(args)
    if args.command == "list":
        return _list_command(args)
    if args.command == "verify":
        return _verify_command(args)
    raise AssertionError(f"unsupported command {args.command!r}")


def _run_command(args: _argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    runtime = _runtime(args, config)
    if args.foreground:
        manifest = _run_experiment(
            config,
            runtime=runtime,
            unlock_gated=args.unlock_gated,
            heartbeat_interval_seconds=args.heartbeat_interval_seconds,
        )
        _emit(_manifest_document(manifest), json_output=args.json_output)
        return 0 if manifest.state is _RunState.COMPLETE else 1

    run_id = _register_experiment(config, runtime=runtime)
    command = _worker_command(args, run_id=run_id)
    try:
        launcher = _launch_detached(
            command,
            run_id=run_id,
            environment=_worker_environment(),
        )
    except _DetachedLaunchError as exc:
        _fail_pending_run(
            runtime=runtime,
            run_id=run_id,
            reason=f"detached launch failed: {exc}",
        )
        raise
    _emit(
        {"run_id": run_id, "state": _RunState.PENDING.value, "launcher": launcher},
        json_output=args.json_output,
    )
    return 0


def _status_command(args: _argparse.Namespace) -> int:
    records = _scan_runs(
        args.runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=args.stale_after_ns,
    )
    matches = tuple(record for record in records if record._run_id == args.run_id)
    if not matches:
        raise _TradingResearchError(f"unknown run_id {args.run_id!r}")
    document = _record_document(matches[0])
    _emit(document, json_output=args.json_output)
    return 0


def _list_command(args: _argparse.Namespace) -> int:
    records = _scan_runs(
        args.runs_root,
        now_unix_ns=_time.time_ns(),
        stale_after_ns=args.stale_after_ns,
    )
    documents = [_record_document(record) for record in records]
    if args.json_output:
        _sys.stdout.write(_json.dumps(documents, sort_keys=True, separators=(",", ":")) + "\n")
    elif not documents:
        _sys.stdout.write("no runs\n")
    else:
        for document in documents:
            _sys.stdout.write(_plain_record(document) + "\n")
    return 0


def _verify_command(args: _argparse.Namespace) -> int:
    config = _load_resolved_config(args.config)
    report = _verify_experiment(
        config,
        runtime=_runtime(args, config),
        unlock_gated=args.unlock_gated,
        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
    )
    _emit(report, json_output=args.json_output)
    return 0 if report["passed"] is True else 1


def _runtime(
    args: _argparse.Namespace,
    config: _ResolvedExperimentConfig,
) -> _RuntimeEnvironment:
    return _runtime_from_paths(
        config=config,
        data_roots=tuple(args.data_root),
        runs_root=args.runs_root,
        split_policy_path=args.split_policy,
        audit_path=args.audit,
        repository_root=args.repository_root,
        dependency_lock_path=args.dependency_lock,
    )


def _worker_command(args: _argparse.Namespace, *, run_id: str) -> list[str]:
    command = [
        _sys.executable,
        "-m",
        "trading_research.experiments.worker",
        "--run-id",
        run_id,
    ]
    for root in args.data_root:
        command.extend(("--data-root", str(root)))
    command.extend(
        (
            "--runs-root",
            str(args.runs_root),
            "--split-policy",
            str(args.split_policy),
            "--audit",
            str(args.audit),
            "--repository-root",
            str(args.repository_root),
            "--dependency-lock",
            str(args.dependency_lock),
            "--heartbeat-interval-seconds",
            str(args.heartbeat_interval_seconds),
        )
    )
    if args.unlock_gated:
        command.append("--unlock-gated")
    return command


def _launch_detached(
    command: _Sequence[str],
    *,
    run_id: str,
    environment: dict[str, str] | None = None,
    memory_max_bytes: int | None = None,
) -> str:
    worker = tuple(command)
    if not worker or any(type(part) is not str or not part for part in worker):
        raise _DetachedLaunchError("worker command is empty or malformed")
    if memory_max_bytes is not None and (
        type(memory_max_bytes) is not int or memory_max_bytes <= 0
    ):
        raise _DetachedLaunchError("MemoryMax must be an exact positive byte count")
    child_environment = _worker_environment(environment)
    unit = f"trading-research-{run_id.lower()}"
    memory_property = (
        ()
        if memory_max_bytes is None
        else (f"--property=MemoryMax={memory_max_bytes}",)
    )
    systemd_command = (
        "systemd-run",
        "--user",
        "--collect",
        "--quiet",
        "--unit",
        unit,
        *memory_property,
        *(f"--setenv={name}={value}" for name, value in sorted(_THREAD_CLAMPS.items())),
        "--",
        *worker,
    )
    systemd_error = _launch_result(systemd_command, environment=child_environment)
    if systemd_error is None:
        return "systemd-run" if memory_max_bytes is None else "systemd-run-memorymax"
    tmux_command = (
        "tmux",
        "new-session",
        "-d",
        "-s",
        unit,
        "--",
        "env",
        *(f"{name}={value}" for name, value in sorted(_THREAD_CLAMPS.items())),
        *worker,
    )
    tmux_error = _launch_result(tmux_command, environment=child_environment)
    if tmux_error is None:
        return "tmux" if memory_max_bytes is None else "tmux-no-memorymax"
    raise _DetachedLaunchError(
        f"systemd-run refused ({systemd_error}); tmux refused ({tmux_error})"
    )


def _launch_result(
    command: tuple[str, ...],
    *,
    environment: dict[str, str],
) -> str | None:
    try:
        result = _subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        return type(exc).__name__
    if result.returncode == 0:
        return None
    detail = " ".join((result.stderr or result.stdout).split())
    return f"exit {result.returncode}" if not detail else f"exit {result.returncode}: {detail}"


def _manifest_document(manifest: _RunManifest) -> dict[str, object]:
    return {
        "run_id": manifest.run_id,
        "experiment_fingerprint": manifest.experiment_fingerprint,
        "state": manifest.state.value,
        "canonical": manifest.canonical,
        "evaluation_count": manifest.evaluation_count,
        "policy_execution_count": len(manifest.policy_execution_json),
        "oos_touched": manifest.oos_touched,
        "lockbox_touched": manifest.lockbox_touched,
        "access_audit_refs": [
            {
                "session_id": ref._session_id,
                "audit_event_id": ref._audit_event_id,
            }
            for ref in manifest.access_audit_refs
        ],
        "failure_reason": manifest.failure_reason,
    }


def _record_document(record: _RunRecord) -> dict[str, object]:
    return {
        "run_id": record._run_id,
        "experiment_fingerprint": record._experiment_fingerprint,
        "attempt_number": record._attempt_number,
        "state": record._state.value,
        "canonical": record._canonical,
        "outcome_class": (None if record._outcome_class is None else record._outcome_class.value),
        "evaluation_count": record._evaluation_count,
        "policy_execution_count": record._policy_execution_count,
        "oos_touched": record._oos_touched,
        "lockbox_touched": record._lockbox_touched,
        "failure_reason": record._failure_reason,
        "heartbeat_age_ns": record._heartbeat_age_ns,
        "heartbeat_stale": record._heartbeat_stale,
    }


def _emit(document: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        _sys.stdout.write(_json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
        return
    _sys.stdout.write(_plain_record(document) + "\n")


def _plain_record(document: dict[str, object]) -> str:
    return " ".join(f"{name}={value}" for name, value in document.items())


if __name__ == "__main__":  # pragma: no cover - exercised through module launch
    raise SystemExit(main())
