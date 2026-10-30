"""Fresh-process operational measurements for the published TRAIN view."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from trading_research.core import DatasetRef, ViewRole
from trading_research.data import DatasetRegistry, MarketFrameLoader
from trading_research.data.gating import (
    AccessClassification,
    AccessContext,
    AccessRequest,
    authorize_access,
    read_split_policy,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import TextIO

    from trading_research.core import MarketFrame
    from trading_research.data.gating import AccessGrant

DATASET_ROOT = Path("/path/to/research-data")
VIEWS_ROOT = Path("/path/to/research-views")
POLICY_PATH = Path(__file__).parents[2] / "config" / "split-policies" / "adr-0012-v1.json"
RAW_MANIFEST = DATASET_ROOT / "XAUUSD_S_M1_MT5_V1" / "manifest.json"
TRAIN_MANIFEST = VIEWS_ROOT / "XAUUSD_S_M1_TRAIN_V1" / "manifest.json"
REGISTRY_ROOTS = (DATASET_ROOT, VIEWS_ROOT)

TRAIN_REF = DatasetRef(
    dataset_id="XAUUSD_S_M1_TRAIN_V1",
    manifest_sha256="30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
    role=ViewRole.TRAIN,
)
TRAIN_FULL_CONFIGURATION = "train-full"
TRAIN_TWO_COLUMN_CONFIGURATION = "train-two-column"
TRAIN_COLD_CONFIGURATION = "train-cold"
TRAIN_TWO_COLUMNS = ("close", "spread")
TRAIN_EXPECTED_ROWS = 1_414_538
TRAIN_FULL_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)

WARM_REPETITIONS = 5
COLD_REPETITIONS = 5
WARM_BUDGET_SECONDS = 2.0
COLD_BUDGET_SECONDS = 5.0
RSS_BUDGET_BYTES = 1024**3
SUDO_UNAVAILABLE_TOKEN = "sudo unavailable"
COLD_UNAVAILABLE_RESULT = "not measured on this machine — sudo unavailable"

_CONFIGURATION_COLUMNS = {
    TRAIN_FULL_CONFIGURATION: None,
    TRAIN_TWO_COLUMN_CONFIGURATION: TRAIN_TWO_COLUMNS,
}


@dataclass(frozen=True, slots=True)
class _SampleSummary:
    samples_seconds: tuple[float, ...]
    median_seconds: float
    minimum_seconds: float


class _RejectingAuditWriter:
    """Prove the published TRAIN request remains unaudited NORMAL access."""

    def append(self, event: Mapping[str, object]) -> None:
        del event
        raise AssertionError("published TRAIN access unexpectedly attempted an audit write")


def _prepare_train_access() -> AccessGrant:
    """Issue the exact published TRAIN grant once, before any measured load."""
    policy = read_split_policy(POLICY_PATH)
    registry = DatasetRegistry.discover(REGISTRY_ROOTS)
    grant = authorize_access(
        registry=registry,
        request=AccessRequest(dataset_ref=TRAIN_REF),
        policy=policy,
        context=AccessContext(
            session_id=uuid4(),
            os_user="performance-harness",
            hostname="research-vm",
            pid=os.getpid(),
            platform_git_commit="0" * 40,
            platform_git_dirty=True,
        ),
        audit_writer=_RejectingAuditWriter(),
    )
    if (
        grant.classification is not AccessClassification.NORMAL
        or grant.audit_event_id is not None
    ):
        raise AssertionError("published TRAIN did not produce an unaudited NORMAL grant")
    return grant


def _timed_load(
    grant: AccessGrant,
    *,
    columns: tuple[str, ...] | None,
    policy_path: Path = POLICY_PATH,
    registry_roots: Sequence[Path] = REGISTRY_ROOTS,
) -> tuple[MarketFrame, float]:
    """Execute the final Section 8 load region with authorization already complete."""
    started = perf_counter()
    policy = read_split_policy(policy_path)
    registry = DatasetRegistry.discover(registry_roots)
    loader = MarketFrameLoader(registry=registry, split_policy=policy)
    frame = loader.load(grant, columns=columns)
    return frame, perf_counter() - started


def _peak_rss_bytes() -> int:
    """Return this Linux process's high-water RSS in bytes."""
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _retained_payload_bytes(frame: MarketFrame) -> int:
    """Return exact immutable key/value buffer bytes retained by one frame."""
    return int(
        frame.keys.nbytes
        + sum(frame.column(name).nbytes for name in frame.column_names)
    )


def _summarize_samples(samples: Sequence[float]) -> _SampleSummary:
    """Summarize exactly five accepted timing samples."""
    observed = tuple(samples)
    if len(observed) != WARM_REPETITIONS or any(value < 0.0 for value in observed):
        raise AssertionError("qualification summary requires exactly five non-negative samples")
    return _SampleSummary(
        samples_seconds=observed,
        median_seconds=median(observed),
        minimum_seconds=min(observed),
    )


def _manual_cold_samples(
    load_once: Callable[[], float],
    *,
    stdin: TextIO,
    stderr: TextIO,
) -> tuple[float, ...] | None:
    """Run one discarded and five measured loads after manual operator releases."""
    measured: list[float] = []
    for index in range(COLD_REPETITIONS + 1):
        label = "discarded cold trial" if index == 0 else f"measured cold #{index}"
        print(
            f"Cold checkpoint: {label}. Perform one manual cache drop, then press Enter.",
            file=stderr,
            flush=True,
        )
        if index == 0:
            print(
                f"If sudo is unavailable, enter exactly: {SUDO_UNAVAILABLE_TOKEN}",
                file=stderr,
                flush=True,
            )
        release = stdin.readline()
        if release == "":
            raise RuntimeError(f"stdin closed before {label} was released")
        token = release.strip()
        if index == 0 and token == SUDO_UNAVAILABLE_TOKEN:
            return None
        if token:
            raise RuntimeError(
                f"unexpected cold-checkpoint token {token!r}; press Enter to release"
            )
        elapsed_seconds = load_once()
        if index > 0:
            measured.append(elapsed_seconds)
    if len(measured) != COLD_REPETITIONS:
        raise AssertionError("cold qualification did not retain exactly five samples")
    return tuple(measured)


def _validate_train_frame(frame: MarketFrame) -> None:
    """Check only the published TRAIN identity, row count, and schema."""
    if frame.dataset_ref != TRAIN_REF:
        raise AssertionError("TRAIN load returned a different dataset identity")
    if frame.num_rows != TRAIN_EXPECTED_ROWS:
        raise AssertionError("TRAIN load returned an unexpected row count")
    if frame.column_names != TRAIN_FULL_COLUMNS:
        raise AssertionError("TRAIN full load returned an unexpected column schema")


def _run_train_cold(*, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """Run the fixed operator-driven TRAIN cold sequence in one NORMAL process."""
    grant = _prepare_train_access()

    def load_once() -> float:
        frame, elapsed_seconds = _timed_load(grant, columns=None)
        _validate_train_frame(frame)
        del frame
        return elapsed_seconds

    samples = _manual_cold_samples(load_once, stdin=stdin, stderr=stderr)
    print(f"target_identity={TRAIN_REF.dataset_id}", file=stdout)
    print(f"manifest_sha256={TRAIN_REF.manifest_sha256}", file=stdout)
    print(f"row_count={TRAIN_EXPECTED_ROWS}", file=stdout)
    print(f"column_names={','.join(TRAIN_FULL_COLUMNS)}", file=stdout)
    if samples is None:
        print(f"cold_status={COLD_UNAVAILABLE_RESULT}", file=stdout)
        return 0
    summary = _summarize_samples(samples)
    print(
        "cold_samples_seconds="
        + ",".join(f"{sample:.9f}" for sample in summary.samples_seconds),
        file=stdout,
    )
    print(f"cold_median_seconds={summary.median_seconds:.9f}", file=stdout)
    print(f"cold_minimum_seconds={summary.minimum_seconds:.9f}", file=stdout)
    passed = summary.median_seconds < COLD_BUDGET_SECONDS
    print(f"cold_status={'passed' if passed else 'budget miss'}", file=stdout)
    print("integrity_status=verified", file=stdout)
    return 0 if passed else 1


def _main(
    argv: Sequence[str],
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one fixed TRAIN measurement and emit operational facts only."""
    selected_stdin = sys.stdin if stdin is None else stdin
    selected_stdout = sys.stdout if stdout is None else stdout
    selected_stderr = sys.stderr if stderr is None else stderr
    allowed = {*_CONFIGURATION_COLUMNS, TRAIN_COLD_CONFIGURATION}
    if len(argv) != 1 or argv[0] not in allowed:
        choices = " | ".join(sorted(allowed))
        raise SystemExit(f"usage: python -m tests.performance._measure_child [{choices}]")
    if argv[0] == TRAIN_COLD_CONFIGURATION:
        return _run_train_cold(
            stdin=selected_stdin,
            stdout=selected_stdout,
            stderr=selected_stderr,
        )

    grant = _prepare_train_access()
    pre_load_rss_bytes = _peak_rss_bytes()
    frame, elapsed_seconds = _timed_load(
        grant,
        columns=_CONFIGURATION_COLUMNS[argv[0]],
    )
    peak_rss_bytes = _peak_rss_bytes()
    retained_payload_bytes = _retained_payload_bytes(frame)

    print(f"elapsed_seconds={elapsed_seconds:.9f}", file=selected_stdout)
    print(f"pre_load_rss_bytes={pre_load_rss_bytes}", file=selected_stdout)
    print(f"peak_rss_bytes={peak_rss_bytes}", file=selected_stdout)
    print(f"retained_payload_bytes={retained_payload_bytes}", file=selected_stdout)
    print(f"row_count={frame.num_rows}", file=selected_stdout)
    print(f"column_names={','.join(frame.column_names)}", file=selected_stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(tuple(sys.argv[1:])))
