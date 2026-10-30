"""Private operator support for the reviewed one-process SNAPSHOT qualification."""

from __future__ import annotations

import getpass
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from tests.performance._measure_child import (
    COLD_BUDGET_SECONDS,
    COLD_UNAVAILABLE_RESULT,
    DATASET_ROOT,
    POLICY_PATH,
    REGISTRY_ROOTS,
    RSS_BUDGET_BYTES,
    TRAIN_FULL_COLUMNS,
    VIEWS_ROOT,
    WARM_BUDGET_SECONDS,
    WARM_REPETITIONS,
    _manual_cold_samples,
    _peak_rss_bytes,
    _summarize_samples,
    _timed_load,
)
from trading_research.core import DatasetRef, ViewRole
from trading_research.core.errors import TradingResearchError
from trading_research.data import DatasetRegistry, JsonlAuditWriter
from trading_research.data.gating import (
    AccessClassification,
    AccessContext,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    authorize_access,
    read_split_policy,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import TextIO

    from tests.performance._measure_child import _SampleSummary
    from trading_research.core import MarketFrame
    from trading_research.data.gating import AccessGrant

_REPO_ROOT = Path(__file__).parents[2]
_RUNNING_MODULE = Path(__file__).resolve()
_AUDIT_PATH = Path("/path/to/research-runs/audit.jsonl")

_SNAPSHOT_REF = DatasetRef(
    dataset_id="XAUUSD_S_M1_SNAPSHOT_V1",
    manifest_sha256="d5b1724cb204699d4e8c3a45bebffa39d080a5260d285610f50981f6ae7eb600",
    role=ViewRole.SNAPSHOT,
)
_SNAPSHOT_EXPECTED_ROWS = 2_328_717
_ACCESS_REASON_CODE = "phase1h-performance-qualification"
_ACCESS_REASON_DETAIL = (
    "Phase 1 infrastructure qualification of the published SNAPSHOT: integrity "
    "verification, load timing and peak RSS only. Not research or evaluation use. "
    "Load, measure, discard — no value-column content is inspected, derived or retained."
)
_EXPECTED_AUDIT_BEFORE = 2
_EXPECTED_AUDIT_AFTER = 3


class _OperatorError(RuntimeError):
    """The fixed Stage 1H-B operator sequence must refuse."""


@dataclass(frozen=True, slots=True)
class _SnapshotResult:
    pre_load_rss_bytes: int
    peak_rss_bytes: int
    warm: _SampleSummary | None
    cold: _SampleSummary | None
    cold_unavailable: bool
    stop_reason: str | None


def _snapshot_access_request() -> AccessRequest:
    """Build the one owner-approved request for the exact published SNAPSHOT."""
    return AccessRequest(
        dataset_ref=_SNAPSHOT_REF,
        reason=AccessReason(code=_ACCESS_REASON_CODE, detail=_ACCESS_REASON_DETAIL),
        gated_unlock=GatedUnlock(target=_SNAPSHOT_REF.canonical_identity),
    )


def _git_output(*arguments: str) -> bytes:
    """Return exact Git output without changing repository state."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _OperatorError(f"Git inspection failed for {arguments!r}: {exc}") from exc
    return completed.stdout


def _approved_git_head() -> str:
    """Require clean, committed, synchronized main provenance before authorization."""
    head = _git_output("rev-parse", "HEAD").decode().strip()
    local_main = _git_output("rev-parse", "refs/heads/main").decode().strip()
    origin_main = _git_output(
        "rev-parse", "refs/remotes/origin/main"
    ).decode().strip()
    if head != local_main or local_main != origin_main:
        raise _OperatorError("qualification requires HEAD == main == origin/main")
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise _OperatorError("qualification requires a clean repository worktree")
    try:
        relative = _RUNNING_MODULE.relative_to(_REPO_ROOT).as_posix()
        committed = _git_output("show", f"{head}:{relative}")
        current = _RUNNING_MODULE.read_bytes()
    except (OSError, ValueError) as exc:
        raise _OperatorError("cannot verify the committed SNAPSHOT operator module") from exc
    if committed != current:
        raise _OperatorError("SNAPSHOT operator module is not the committed version at HEAD")
    return head


def _production_context() -> AccessContext:
    """Capture the real operator and reviewed Git identity immediately before access."""
    return AccessContext(
        session_id=uuid4(),
        os_user=getpass.getuser(),
        hostname=socket.gethostname(),
        pid=os.getpid(),
        platform_git_commit=_approved_git_head(),
        platform_git_dirty=False,
    )


def _read_audit_records(path: Path) -> tuple[dict[str, object], ...]:
    """Read the fixed audit log narrowly for the 2-to-3 cardinality guard."""
    if path.is_symlink() or not path.is_file():
        raise _OperatorError(f"audit log is not an existing regular file: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise _OperatorError(f"cannot read audit cardinality from {path}: {exc}") from exc
    records: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        try:
            decoded: object = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _OperatorError(f"audit record {index} is not valid JSON") from exc
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) for key in decoded
        ):
            raise _OperatorError(f"audit record {index} is not a string-keyed object")
        records.append(cast("dict[str, object]", decoded))
    return tuple(records)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    """Require one string-keyed audit subobject."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _OperatorError(f"new audit record has invalid {label}")
    return cast("Mapping[str, object]", value)


def _validate_audit_delta(
    before: Sequence[dict[str, object]],
    after: Sequence[dict[str, object]],
    grant: AccessGrant,
) -> None:
    """Require the exact one-event Stage 1H-B production audit effect."""
    if len(before) != _EXPECTED_AUDIT_BEFORE:
        raise _OperatorError(
            f"expected {_EXPECTED_AUDIT_BEFORE} audit records before access, found {len(before)}"
        )
    if len(after) != _EXPECTED_AUDIT_AFTER or tuple(after[:-1]) != tuple(before):
        raise _OperatorError("SNAPSHOT qualification audit cardinality was not exactly 2 -> 3")

    event = after[-1]
    dataset_ref = _mapping(event.get("dataset_ref"), "dataset_ref")
    authorization = _mapping(event.get("authorization"), "authorization")
    reason = _mapping(authorization.get("reason"), "authorization.reason")
    expected = {
        "event_type": "dataset_access_decision",
        "event_id": grant.audit_event_id,
        "outcome": "allowed",
        "classification": "fully_gated",
        "classification_basis": "range_exceeds_oos_boundary",
    }
    if any(event.get(key) != value for key, value in expected.items()):
        raise _OperatorError("new audit record does not match the approved access decision")
    if (
        dataset_ref.get("dataset_id") != _SNAPSHOT_REF.dataset_id
        or dataset_ref.get("manifest_sha256") != _SNAPSHOT_REF.manifest_sha256
        or dataset_ref.get("role") != _SNAPSHOT_REF.role.value
        or reason.get("code") != _ACCESS_REASON_CODE
        or reason.get("detail") != _ACCESS_REASON_DETAIL
    ):
        raise _OperatorError("new audit record does not bind the exact SNAPSHOT request")


def _authorize_snapshot(context: AccessContext) -> AccessGrant:
    """Authorize exactly once outside every timed load using the real writer."""
    policy = read_split_policy(POLICY_PATH)
    registry = DatasetRegistry.discover(REGISTRY_ROOTS)
    registered = registry.resolve(_SNAPSHOT_REF)
    if (
        registered.manifest.role is not ViewRole.SNAPSHOT
        or registered.manifest.expected_row_count != _SNAPSHOT_EXPECTED_ROWS
    ):
        raise _OperatorError("published SNAPSHOT metadata differs from the approved target")
    grant = authorize_access(
        registry=registry,
        request=_snapshot_access_request(),
        policy=policy,
        context=context,
        audit_writer=JsonlAuditWriter(_AUDIT_PATH),
    )
    if (
        grant.classification is not AccessClassification.FULLY_GATED
        or grant.audit_event_id is None
    ):
        raise _OperatorError("SNAPSHOT authorization did not issue one audited gated grant")
    return grant


def _validate_snapshot_frame(frame: MarketFrame) -> None:
    """Check only identity, row count, and selected schema; inspect no values."""
    if frame.dataset_ref != _SNAPSHOT_REF:
        raise _OperatorError("loaded frame identity differs from the approved SNAPSHOT")
    if frame.num_rows != _SNAPSHOT_EXPECTED_ROWS:
        raise _OperatorError("loaded SNAPSHOT row count differs from the approved target")
    if frame.column_names != TRAIN_FULL_COLUMNS:
        raise _OperatorError("loaded SNAPSHOT column names differ from the published schema")


def _render_result(
    result: _SnapshotResult,
    *,
    stdout: TextIO,
) -> None:
    """Emit only the allowlisted operational evidence after audit validation."""
    print(f"target_identity={_SNAPSHOT_REF.dataset_id}", file=stdout)
    print(f"manifest_sha256={_SNAPSHOT_REF.manifest_sha256}", file=stdout)
    print(f"row_count={_SNAPSHOT_EXPECTED_ROWS}", file=stdout)
    print(f"column_names={','.join(TRAIN_FULL_COLUMNS)}", file=stdout)
    print(f"pre_load_rss_bytes={result.pre_load_rss_bytes}", file=stdout)
    print(f"peak_rss_bytes={result.peak_rss_bytes}", file=stdout)
    print(
        f"rss_status={'passed' if result.peak_rss_bytes < RSS_BUDGET_BYTES else 'budget miss'}",
        file=stdout,
    )
    if result.warm is not None:
        print(
            "warm_samples_seconds="
            + ",".join(f"{sample:.9f}" for sample in result.warm.samples_seconds),
            file=stdout,
        )
        print(f"warm_median_seconds={result.warm.median_seconds:.9f}", file=stdout)
        print(f"warm_minimum_seconds={result.warm.minimum_seconds:.9f}", file=stdout)
        print(
            "warm_status="
            + ("passed" if result.warm.median_seconds < WARM_BUDGET_SECONDS else "budget miss"),
            file=stdout,
        )
    elif result.stop_reason is not None:
        print(f"warm_status=not measured — {result.stop_reason}", file=stdout)

    if result.cold_unavailable:
        print(f"cold_status={COLD_UNAVAILABLE_RESULT}", file=stdout)
    elif result.cold is not None:
        print(
            "cold_samples_seconds="
            + ",".join(f"{sample:.9f}" for sample in result.cold.samples_seconds),
            file=stdout,
        )
        print(f"cold_median_seconds={result.cold.median_seconds:.9f}", file=stdout)
        print(f"cold_minimum_seconds={result.cold.minimum_seconds:.9f}", file=stdout)
        print(
            "cold_status="
            + ("passed" if result.cold.median_seconds < COLD_BUDGET_SECONDS else "budget miss"),
            file=stdout,
        )
    elif result.stop_reason is not None:
        print(f"cold_status=not measured — {result.stop_reason}", file=stdout)
    print("integrity_status=verified", file=stdout)
    print("audit_count_status=2->3 verified", file=stdout)


def _run_snapshot(*, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """Run the fixed one-process, one-grant qualification topology."""
    if sys.platform != "linux":
        raise _OperatorError("SNAPSHOT ru_maxrss qualification requires Linux")
    if not DATASET_ROOT.is_dir() or not VIEWS_ROOT.is_dir() or not POLICY_PATH.is_file():
        raise _OperatorError("published research dataset, views, or split policy is unavailable")

    before = _read_audit_records(_AUDIT_PATH)
    if len(before) != _EXPECTED_AUDIT_BEFORE:
        raise _OperatorError(
            f"expected {_EXPECTED_AUDIT_BEFORE} audit records before access, found {len(before)}"
        )
    grant = _authorize_snapshot(_production_context())

    pre_load_rss_bytes = _peak_rss_bytes()
    warmup, _ = _timed_load(grant, columns=None)
    _validate_snapshot_frame(warmup)
    peak_rss_bytes = _peak_rss_bytes()
    del warmup

    warm_summary: _SampleSummary | None = None
    cold_summary: _SampleSummary | None = None
    cold_unavailable = False
    stop_reason: str | None = None
    if peak_rss_bytes >= RSS_BUDGET_BYTES:
        stop_reason = "RSS budget miss"
    else:
        warm_samples: list[float] = []
        for _ in range(WARM_REPETITIONS):
            frame, elapsed_seconds = _timed_load(grant, columns=None)
            _validate_snapshot_frame(frame)
            warm_samples.append(elapsed_seconds)
            del frame
        warm_summary = _summarize_samples(warm_samples)
        if warm_summary.median_seconds >= WARM_BUDGET_SECONDS:
            stop_reason = "warm budget miss"
        else:

            def load_cold_once() -> float:
                frame, elapsed_seconds = _timed_load(grant, columns=None)
                _validate_snapshot_frame(frame)
                del frame
                return elapsed_seconds

            cold_samples = _manual_cold_samples(
                load_cold_once,
                stdin=stdin,
                stderr=stderr,
            )
            if cold_samples is None:
                cold_unavailable = True
            else:
                cold_summary = _summarize_samples(cold_samples)
                if cold_summary.median_seconds >= COLD_BUDGET_SECONDS:
                    stop_reason = "cold budget miss"

    result = _SnapshotResult(
        pre_load_rss_bytes=pre_load_rss_bytes,
        peak_rss_bytes=peak_rss_bytes,
        warm=warm_summary,
        cold=cold_summary,
        cold_unavailable=cold_unavailable,
        stop_reason=stop_reason,
    )
    after = _read_audit_records(_AUDIT_PATH)
    _validate_audit_delta(before, after, grant)
    _render_result(result, stdout=stdout)
    return 0 if stop_reason is None else 1


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run only by an explicit reviewed operator invocation with no arguments."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    selected_stdin = sys.stdin if stdin is None else stdin
    selected_stdout = sys.stdout if stdout is None else stdout
    selected_stderr = sys.stderr if stderr is None else stderr
    if arguments:
        print("REFUSED: SNAPSHOT qualification accepts no arguments", file=selected_stderr)
        return 2
    try:
        return _run_snapshot(
            stdin=selected_stdin,
            stdout=selected_stdout,
            stderr=selected_stderr,
        )
    except (_OperatorError, TradingResearchError, OSError, RuntimeError) as exc:
        print(f"REFUSED: {exc}", file=selected_stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
