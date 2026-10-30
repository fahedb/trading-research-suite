"""Top-level command composition without reversing experiment/index dependencies."""

from __future__ import annotations

import argparse as _argparse
import json as _json
import subprocess as _subprocess
import sys as _sys
import typing as _typing
import uuid as _uuid
from pathlib import Path as _Path

from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.experiments.cli import _launch_detached
from trading_research.experiments.cli import main as _experiments_main
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import (
    _runtime_from_paths,
    _validated_heartbeat_interval,
)
from trading_research.experiments.pool import (
    _record_detached_launch,
    _run_sweep,
    _summary_document,
)
from trading_research.experiments.resources import (
    _DEFAULT_ASSUMED_PEAK_RSS_BYTES,
    _DEFAULT_MAX_WORKERS,
    _DEFAULT_MEMORY_BUDGET_BYTES,
    _worker_environment,
)
from trading_research.experiments.sweeps import (
    _load_sweep_request,
    _plan_sweep,
    _read_sweep_plan,
    _SweepRuntimePaths,
)
from trading_research.index.build import _index_status, _rebuild_index
from trading_research.index.query import _compare_metric, _query_runs
from trading_research.index.trials import _index_trial_ledger

if _typing.TYPE_CHECKING:
    from collections.abc import Sequence as _Sequence

__all__ = ["main"]

_EXPERIMENT_COMMANDS = frozenset({"run", "status", "list", "verify", "preflight"})


def main(argv: _Sequence[str] | None = None) -> int:
    """Dispatch unchanged experiment verbs or the Phase-5 operations surface."""
    arguments = tuple(_sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in _EXPERIMENT_COMMANDS:
        return _experiments_main(arguments)
    parser = _parser()
    args = parser.parse_args(arguments)
    try:
        return _dispatch(args)
    except (_TradingResearchError, OSError, ValueError, _json.JSONDecodeError) as exc:
        _sys.stderr.write(f"error: {exc}\n")
        return 2


def _parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(
        prog="trading-research",
        description="Experiment commands: run, status, list, verify, preflight; operations follow.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="build or inspect the disposable run index")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    rebuild = index_commands.add_parser("rebuild", help="atomically rebuild the run index")
    rebuild.add_argument("--runs-root", required=True, type=_absolute_path)
    rebuild.add_argument("--repository-root", required=True, type=_absolute_path)
    rebuild.add_argument("--incremental", action="store_true")
    rebuild.add_argument("--stale-after-ns", type=int, default=60_000_000_000)
    rebuild.add_argument("--json", action="store_true", dest="json_output")
    status = index_commands.add_parser("status", help="show index freshness and errors")
    status.add_argument("--runs-root", required=True, type=_absolute_path)
    status.add_argument("--json", action="store_true", dest="json_output")

    runs = commands.add_parser("runs", help="query or compare indexed runs")
    runs_commands = runs.add_subparsers(dest="runs_command", required=True)
    query = runs_commands.add_parser("query", help="query indexed run and metric rows")
    _add_query_arguments(query)
    compare = runs_commands.add_parser("compare", help="compare one recorded metric")
    _add_compare_arguments(compare)

    sweep = commands.add_parser("sweep", help="plan or execute a deterministic sweep")
    sweep_commands = sweep.add_subparsers(dest="sweep_command", required=True)
    plan = sweep_commands.add_parser("plan", help="expand and persist an immutable sweep")
    plan.add_argument("--spec", required=True, type=_absolute_path)
    _add_sweep_runtime_arguments(plan)
    plan.add_argument("--spend-oos", action="store_true")
    plan.add_argument("--max-protected-children", type=int)
    plan.add_argument("--protected-reason")
    plan.add_argument("--json", action="store_true", dest="json_output")
    run = sweep_commands.add_parser("run", help="execute or resume a planned sweep")
    run.add_argument("--runs-root", required=True, type=_absolute_path)
    run.add_argument("--sweep", required=True)
    run.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS)
    run.add_argument("--memory-budget-bytes", type=int, default=_DEFAULT_MEMORY_BUDGET_BYTES)
    run.add_argument(
        "--assumed-peak-rss-bytes",
        type=int,
        default=_DEFAULT_ASSUMED_PEAK_RSS_BYTES,
    )
    run.add_argument("--retry-failed", action="store_true")
    run.add_argument("--allow-noncanonical", action="store_true")
    run.add_argument("--acknowledge-oos-access", action="store_true")
    run.add_argument("--allow-critical-disk-usage", action="store_true")
    run.add_argument("--stale-after-ns", type=int, default=60_000_000_000)
    run.add_argument("--poll-interval-seconds", type=float, default=0.2)
    run.add_argument("--detach", action="store_true")
    run.add_argument("--json", action="store_true", dest="json_output")

    trials = commands.add_parser("trials", help="report the mechanical Trial Ledger")
    trial_commands = trials.add_subparsers(dest="trials_command", required=True)
    report = trial_commands.add_parser("report", help="report evaluated trial counts")
    report.add_argument("--runs-root", required=True, type=_absolute_path)
    report.add_argument("--audit", type=_absolute_path)
    trial_scope = report.add_mutually_exclusive_group()
    trial_scope.add_argument("--sweep")
    trial_scope.add_argument("--since", type=int, dest="since_unix_ns")
    report.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _add_sweep_runtime_arguments(parser: _argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", action="append", required=True, type=_absolute_path)
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--split-policy", required=True, type=_absolute_path)
    parser.add_argument("--audit", required=True, type=_absolute_path)
    parser.add_argument("--repository-root", required=True, type=_absolute_path)
    parser.add_argument("--dependency-lock", required=True, type=_absolute_path)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=5.0)


def _add_query_arguments(parser: _argparse.ArgumentParser) -> None:
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--state", action="append", choices=sorted(_states()))
    parser.add_argument(
        "--outcome-class",
        action="append",
        choices=("evaluated", "descriptive", "pre_evaluation_failure"),
    )
    canonical = parser.add_mutually_exclusive_group()
    canonical.add_argument("--canonical", action="store_true", dest="canonical")
    canonical.add_argument("--noncanonical", action="store_false", dest="canonical")
    parser.set_defaults(canonical=None)
    parser.add_argument("--fingerprint", action="append")
    parser.add_argument("--sweep")
    parser.add_argument(
        "--dataset-role",
        action="append",
        choices=("TRAIN", "VALIDATION", "OOS", "LOCKBOX"),
    )
    oos = parser.add_mutually_exclusive_group()
    oos.add_argument("--oos-touched", action="store_true", dest="oos_touched")
    oos.add_argument("--no-oos-touched", action="store_false", dest="oos_touched")
    parser.set_defaults(oos_touched=None)
    parser.add_argument("--lockbox-touched", action="store_true", default=None)
    parser.add_argument("--config-path", action="append")
    parser.add_argument("--metric")
    parser.add_argument("--plane", choices=("A", "B"))
    parser.add_argument("--order-by", choices=("finished", "metric"), default="finished")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--include-protected", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")


def _add_compare_arguments(parser: _argparse.ArgumentParser) -> None:
    parser.add_argument("--runs-root", required=True, type=_absolute_path)
    parser.add_argument("--audit", type=_absolute_path)
    parser.add_argument("--metric", required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--sweep")
    scope.add_argument("--fingerprint", action="append")
    scope.add_argument("--query-from-file", type=_absolute_path)
    parser.add_argument("--plane", choices=("A", "B"))
    parser.add_argument("--group-by", choices=("fold", "question"), default="question")
    parser.add_argument("--include-protected", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")


def _dispatch(args: _argparse.Namespace) -> int:
    if args.command == "index" and args.index_command == "rebuild":
        return _index_rebuild_command(args)
    if args.command == "index" and args.index_command == "status":
        return _index_status_command(args)
    if args.command == "runs" and args.runs_command == "query":
        return _runs_query_command(args)
    if args.command == "runs" and args.runs_command == "compare":
        return _runs_compare_command(args)
    if args.command == "sweep" and args.sweep_command == "plan":
        return _sweep_plan_command(args)
    if args.command == "sweep" and args.sweep_command == "run":
        return _sweep_run_command(args)
    if args.command == "trials" and args.trials_command == "report":
        return _trials_report_command(args)
    raise AssertionError("unsupported operations command")


def _index_rebuild_command(args: _argparse.Namespace) -> int:
    result = _rebuild_index(
        args.runs_root,
        builder_git_commit=_git_commit(args.repository_root),
        incremental=args.incremental,
        stale_after_ns=args.stale_after_ns,
    )
    document: dict[str, object] = {
        "path": str(result._path),
        "mode": result._mode,
        "generation": result._generation,
        "run_count": result._run_count,
        "scan_error_count": result._scan_error_count,
        "sweep_count": result._sweep_count,
        "source_token": result._source_token,
        "built_unix_ns": result._built_unix_ns,
    }
    _emit_document(document, json_output=args.json_output)
    return 1 if result._scan_error_count else 0


def _index_status_command(args: _argparse.Namespace) -> int:
    document = _index_status(args.runs_root)
    _emit_document(document, json_output=args.json_output)
    return 0 if document.get("available") is True and document.get("fresh") is True else 1


def _runs_query_command(args: _argparse.Namespace) -> int:
    result = _query_runs(
        args.runs_root,
        states=tuple(args.state or ()),
        outcome_classes=tuple(args.outcome_class or ()),
        canonical=args.canonical,
        fingerprints=tuple(args.fingerprint or ()),
        sweep_id=args.sweep,
        dataset_roles=tuple(args.dataset_role or ()),
        oos_touched=args.oos_touched,
        lockbox_touched=args.lockbox_touched,
        config_predicates=tuple(args.config_path or ()),
        metric_name=args.metric,
        plane=args.plane,
        order_by=args.order_by,
        limit=args.limit,
        include_protected=args.include_protected,
    )
    document: dict[str, object] = {
        "rows": list(result._rows),
        "scan_errors": list(result._scan_errors),
    }
    _emit_collection(document, json_output=args.json_output)
    return 1 if result._scan_errors else 0


def _runs_compare_command(args: _argparse.Namespace) -> int:
    fingerprints = tuple(args.fingerprint or ())
    if args.query_from_file is not None:
        fingerprints = _fingerprints_from_file(args.query_from_file)
    result = _compare_metric(
        args.runs_root,
        metric_name=args.metric,
        sweep_id=args.sweep,
        fingerprints=fingerprints,
        plane=args.plane,
        include_protected=args.include_protected,
    )
    trial_context = _index_trial_ledger(
        args.runs_root,
        audit_path=args.audit,
        sweep_id=args.sweep,
    )
    groups = (
        _flatten_folds(result._groups)
        if args.group_by == "fold"
        else list(result._groups)
    )
    document = {
        "metric_name": result._metric_name,
        "plane": result._plane,
        "group_by": args.group_by,
        "groups": groups,
        "scan_errors": list(result._scan_errors),
        "trial_context": trial_context._document,
    }
    _emit_collection(document, json_output=args.json_output)
    return 1 if result._scan_errors or trial_context._has_errors else 0


def _sweep_plan_command(args: _argparse.Namespace) -> int:
    request = _load_sweep_request(args.spec)
    config = _config_from_document(request["base_config"])
    runtime = _runtime_from_paths(
        config=config,
        data_roots=tuple(args.data_root),
        runs_root=args.runs_root,
        split_policy_path=args.split_policy,
        audit_path=args.audit,
        repository_root=args.repository_root,
        dependency_lock_path=args.dependency_lock,
    )
    heartbeat_seconds = _validated_heartbeat_interval(args.heartbeat_interval_seconds)
    runtime_paths = _SweepRuntimePaths(
        tuple(args.data_root),
        args.runs_root,
        args.split_policy,
        args.audit,
        args.repository_root,
        args.dependency_lock,
        max(1, int(heartbeat_seconds * 1_000_000_000)),
    )
    plan = _plan_sweep(
        request,
        runtime=runtime,
        runtime_paths=runtime_paths,
        spend_oos=args.spend_oos,
        max_protected_children=args.max_protected_children,
        protected_reason=args.protected_reason,
    )
    if args.json_output:
        _sys.stdout.write(_canonical_json(plan._document) + "\n")
    else:
        duplicates = plan._document["duplicates_collapsed"]
        if type(duplicates) is not list:  # pragma: no cover - planner contract
            raise AssertionError("planned sweep duplicates are malformed")
        _emit_document(
            {
                "sweep_id": plan._document["sweep_id"],
                "spec_digest": plan._document["spec_digest"],
                "children": len(plan._children),
                "duplicates_collapsed": len(duplicates),
                "path": str(args.runs_root / "sweeps" / request["sweep_id"] / "sweep.json"),
            },
            json_output=False,
        )
    return 0


def _sweep_run_command(args: _argparse.Namespace) -> int:
    _read_sweep_plan(args.runs_root, args.sweep)
    if args.detach:
        launch_request_id = str(_uuid.uuid4())
        launch_mode = f"detached-request:{launch_request_id}"
        command = _sweep_dispatcher_command(args, launch_mode=launch_mode)
        launcher = _launch_detached(
            command,
            run_id=f"sweep-{args.sweep}",
            environment=_worker_environment(),
            memory_max_bytes=args.memory_budget_bytes,
        )
        _record_detached_launch(
            args.runs_root,
            args.sweep,
            launch_request_id=launch_request_id,
            launcher=launcher,
            memory_budget_bytes=args.memory_budget_bytes,
        )
        document: dict[str, object] = {
            "sweep_id": args.sweep,
            "state": "DISPATCHED",
            "launcher": launcher,
            "launch_request_id": launch_request_id,
            "memory_max_enforced": launcher == "systemd-run-memorymax",
        }
        _emit_document(document, json_output=args.json_output)
        return 0

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
    )
    _emit_document(_summary_document(summary), json_output=args.json_output)
    return 0 if summary._successful else 1


def _sweep_dispatcher_command(
    args: _argparse.Namespace,
    *,
    launch_mode: str,
) -> tuple[str, ...]:
    command = [
        _sys.executable,
        "-m",
        "trading_research.experiments.pool",
        "--runs-root",
        str(args.runs_root),
        "--sweep",
        args.sweep,
        "--max-workers",
        str(args.max_workers),
        "--memory-budget-bytes",
        str(args.memory_budget_bytes),
        "--assumed-peak-rss-bytes",
        str(args.assumed_peak_rss_bytes),
        "--stale-after-ns",
        str(args.stale_after_ns),
        "--poll-interval-seconds",
        str(args.poll_interval_seconds),
        "--launch-mode",
        launch_mode,
    ]
    for enabled, flag in (
        (args.retry_failed, "--retry-failed"),
        (args.allow_noncanonical, "--allow-noncanonical"),
        (args.acknowledge_oos_access, "--acknowledge-oos-access"),
        (args.allow_critical_disk_usage, "--allow-critical-disk-usage"),
    ):
        if enabled:
            command.append(flag)
    return tuple(command)


def _trials_report_command(args: _argparse.Namespace) -> int:
    ledger = _index_trial_ledger(
        args.runs_root,
        audit_path=args.audit,
        sweep_id=args.sweep,
        since_unix_ns=args.since_unix_ns,
    )
    _emit_collection(ledger._document, json_output=args.json_output)
    return 1 if ledger._has_errors else 0


def _fingerprints_from_file(path: _Path) -> tuple[str, ...]:
    value = _json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or set(value) != {"fingerprints"}:
        raise ValueError("query-from-file must contain exactly a fingerprints array")
    fingerprints = value["fingerprints"]
    if type(fingerprints) is not list or any(type(item) is not str for item in fingerprints):
        raise ValueError("query-from-file fingerprints must be an array of strings")
    return tuple(fingerprints)


def _flatten_folds(groups: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for group in groups:
        folds = group["fold_measurements"]
        if type(folds) is not list:
            raise ValueError("comparison fold measurements are malformed")
        for fold in folds:
            if type(fold) is not dict:
                raise ValueError("comparison fold row is malformed")
            rows.append(
                {
                    "experiment_fingerprint": group["experiment_fingerprint"],
                    "plane": group["plane"],
                    "question": group["question"],
                    "protected": group["protected"],
                    "cost_semantics_unverified": group["cost_semantics_unverified"],
                    **fold,
                }
            )
    return rows


def _git_commit(repository_root: _Path) -> str:
    result = _subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _absolute_path(value: str) -> _Path:
    if type(value) is not str or not value:
        raise _argparse.ArgumentTypeError("path must be non-empty")
    return _Path(value).expanduser().resolve()


def _states() -> frozenset[str]:
    return frozenset({"PENDING", "RUNNING", "COMPLETE", "FAILED", "INTERRUPTED"})


def _emit_document(document: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        _sys.stdout.write(_canonical_json(document) + "\n")
        return
    _sys.stdout.write(" ".join(f"{key}={value}" for key, value in document.items()) + "\n")


def _emit_collection(document: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        _sys.stdout.write(_canonical_json(document) + "\n")
        return
    for key, value in document.items():
        if type(value) is list:
            _sys.stdout.write(f"{key}:\n")
            for row in value:
                _sys.stdout.write(f"  {_canonical_json(row)}\n")
        else:
            _sys.stdout.write(f"{key}={value}\n")


def _canonical_json(value: object) -> str:
    return _json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
