"""Private detached worker for one already-registered run."""

from __future__ import annotations

import argparse as _argparse
import sys as _sys
import time as _time
import typing as _typing
from pathlib import Path as _Path

from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import (
    _run_registered_from_persisted_config,
    _runtime_from_paths,
)
from trading_research.experiments.resources import _apply_worker_limits, _assert_worker_environment
from trading_research.experiments.runs import (
    RunState as _RunState,
)
from trading_research.experiments.runs import (
    _finish_run,
    _read_registered_config,
    _start_run,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Sequence as _Sequence

__all__: list[str] = []


def _main(argv: _Sequence[str] | None = None) -> int:
    """Reconstruct runtime paths and execute one fingerprint-bound attempt."""
    args = _parser().parse_args(argv)
    try:
        _assert_worker_environment()
        _apply_worker_limits(args.max_runtime_seconds, args.max_memory_bytes)
        config = _config_from_document(
            _read_registered_config(runs_root=args.runs_root, run_id=args.run_id)
        )
        runtime = _runtime_from_paths(
            config=config,
            data_roots=tuple(args.data_root),
            runs_root=args.runs_root,
            split_policy_path=args.split_policy,
            audit_path=args.audit,
            repository_root=args.repository_root,
            dependency_lock_path=args.dependency_lock,
        )
    except BaseException as exc:
        _record_startup_failure(
            runs_root=args.runs_root,
            run_id=args.run_id,
            reason=f"worker startup failed: {type(exc).__name__}: {exc}",
        )
        raise
    manifest = _run_registered_from_persisted_config(
        runtime=runtime,
        run_id=args.run_id,
        unlock_gated=args.unlock_gated,
        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
    )
    return 0 if manifest.state is _RunState.COMPLETE else 1


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(prog="trading-research-worker")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--data-root", action="append", required=True, type=_absolute_path)
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--split-policy", required=True, type=_absolute_path)
    parser.add_argument("--audit", required=True, type=_absolute_path)
    parser.add_argument("--repository-root", required=True, type=_absolute_path)
    parser.add_argument("--dependency-lock", required=True, type=_absolute_path)
    parser.add_argument("--unlock-gated", action="store_true")
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-runtime-seconds", type=int)
    parser.add_argument("--max-memory-bytes", type=int)
    return parser


def _absolute_path(value: str) -> _Path:
    return _Path(value).expanduser().resolve()


def _record_startup_failure(*, runs_root: _Path, run_id: str, reason: str) -> None:
    started = _time.time_ns()
    _start_run(runs_root=runs_root, run_id=run_id, now_unix_ns=started)
    _finish_run(
        runs_root=runs_root,
        run_id=run_id,
        terminal_state=_RunState.FAILED,
        inventory=None,
        failure_reason=" ".join(reason.split())[:2_000],
        now_unix_ns=max(started, _time.time_ns()),
    )


if __name__ == "__main__":  # pragma: no cover - launched by the CLI
    try:
        _result = _main()
    except BaseException as exc:
        _sys.stderr.write(f"worker failed: {type(exc).__name__}: {exc}\n")
        raise
    raise SystemExit(_result)
