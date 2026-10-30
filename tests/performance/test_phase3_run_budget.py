"""Explicit published-TRAIN Phase-3 config-to-stored-run qualification."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING

import numpy as np
import pytest

from tests.performance._measure_child import (
    DATASET_ROOT,
    POLICY_PATH,
    REGISTRY_ROOTS,
    TRAIN_EXPECTED_ROWS,
    TRAIN_MANIFEST,
    TRAIN_REF,
    VIEWS_ROOT,
)
from trading_research.experiments.config import load_resolved_config
from trading_research.experiments.orchestration import (
    _runtime_from_paths,
    run_experiment,
)
from trading_research.experiments.runs import RunState, scan_runs

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).parents[2]
_RUNNING_FILE = Path(__file__).resolve()
_RUNS_ROOT = Path("/path/to/research-runs")
_RUNS_TMP_ROOT = _RUNS_ROOT / "tmp"
_AUDIT_PATH = _RUNS_ROOT / "audit.jsonl"
_DEPENDENCY_LOCK = _REPO_ROOT / "uv.lock"
_EXPECTED_AUDIT_RECORDS = 3
_EXPECTED_AUDIT_SHA256 = (
    "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5"
)
_TRAIN_CONTEXT_START = 1_577_926_800
_TRAIN_EVAL_START = 1_577_926_860
_TRAIN_EVAL_END = 1_703_894_160
_EVALUATION_ROWS = TRAIN_EXPECTED_ROWS - 1
_FIT_ROWS = 10_000
_FOLD_EVAL_ROWS = _EVALUATION_ROWS - _FIT_ROWS
_TIMED_REPETITIONS = 5
_BUDGET_SECONDS = 30.0
_EVIDENCE_SCHEMA = "phase3-config-run-performance-v1"


@dataclass(frozen=True, slots=True)
class _TimingSummary:
    samples_seconds: tuple[float, ...]
    median_seconds: float
    minimum_seconds: float


@dataclass(frozen=True, slots=True)
class _RunEvidence:
    elapsed_seconds: float
    experiment_fingerprint: str
    run_id: str
    git_commit: str
    feature_identity_key: str
    fitted_state_identity_key: str
    feature_rows: int
    evaluation_count: int
    policy_execution_count: int
    artifact_hashes: tuple[tuple[str, str], ...]
    metric_hashes: tuple[tuple[str, str], ...]
    artifact_sizes: tuple[tuple[str, int], ...]
    metric_sizes: tuple[tuple[str, int], ...]


class _RejectingAuditWriter:
    """Make any non-NORMAL access attempt fail before it can touch the audit log."""

    def append(self, event: Mapping[str, object]) -> None:
        del event
        raise AssertionError("published TRAIN run unexpectedly attempted an audit write")


@pytest.fixture(scope="module", autouse=True)
def _require_published_train_and_runs_root() -> None:
    assert sys.platform == "linux", "Phase 3 peak-RSS qualification requires Linux"
    assert all(path.is_dir() for path in (DATASET_ROOT, VIEWS_ROOT, _RUNS_ROOT, _RUNS_TMP_ROOT))
    assert all(
        path.is_file()
        for path in (POLICY_PATH, TRAIN_MANIFEST, _AUDIT_PATH, _DEPENDENCY_LOCK)
    )


def _git_output(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _clean_committed_head() -> str:
    head = _git_output("rev-parse", "HEAD").decode("ascii").strip()
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AssertionError("Phase 3 qualification requires a clean repository worktree")
    relative = _RUNNING_FILE.relative_to(_REPO_ROOT).as_posix()
    if _git_output("show", f"{head}:{relative}") != _RUNNING_FILE.read_bytes():
        raise AssertionError("Phase 3 performance harness is not the committed HEAD version")
    return head


def _audit_snapshot() -> bytes:
    if _AUDIT_PATH.is_symlink() or not _AUDIT_PATH.is_file():
        raise AssertionError("audit log is not one existing regular file")
    payload = _AUDIT_PATH.read_bytes()
    if len(payload.splitlines()) != _EXPECTED_AUDIT_RECORDS:
        raise AssertionError("Phase 3 qualification requires the exact three-record audit log")
    if hashlib.sha256(payload).hexdigest() != _EXPECTED_AUDIT_SHA256:
        raise AssertionError("Phase 3 qualification audit bytes differ from the baseline")
    return payload


def _config_document() -> dict[str, object]:
    return {
        "schema": "research-experiment-config-v1",
        "integrity_mode": "full",
        "dataset": {
            "dataset_id": TRAIN_REF.dataset_id,
            "manifest_sha256": TRAIN_REF.manifest_sha256,
            "role": TRAIN_REF.role.value,
        },
        "split_policy": {
            "adr": "ADR-0012",
            "policy_version": "v1",
            "policy_sha256": "b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb",
        },
        "bounds": {
            "context_start": _TRAIN_CONTEXT_START,
            "eval_start": _TRAIN_EVAL_START,
            "eval_end": _TRAIN_EVAL_END,
        },
        "features": [{"name": "log_return_1", "version": 1, "params": {}}],
        "representation": None,
        "model": {"name": "stateless_rule_v1", "version": 1, "params": {}},
        "training_outcome": None,
        "evaluation_outcomes": [
            {
                "name": "forward_log_return_v1",
                "version": 1,
                "params": {
                    "boundary_policy": "nan",
                    "decision_delay_rows": 1,
                    "horizon_rows": 1,
                    "reference_price_column": "close",
                },
            }
        ],
        "policies": [
            {
                "name": "threshold_v1",
                "version": 1,
                "params": {
                    "output_column": "score",
                    "threshold": {"__float_hex__": "0x0.0p+0"},
                },
            }
        ],
        "evaluators": [
            {
                "name": "sign_accuracy_v1",
                "version": 1,
                "params": {
                    "min_effective_samples": 1,
                    "outcome_column": "target.forward_log_return",
                    "output_column": "score",
                },
            }
        ],
        "execution": None,
        "trade_evaluators": [],
        "folds": {
            "fit_rows": _FIT_ROWS,
            "eval_rows": _FOLD_EVAL_ROWS,
            "step_rows": _EVALUATION_ROWS,
            "embargo_rows": 0,
            "purge_override": None,
        },
        "seed": None,
        "access_reason": None,
        "declared_lockbox_evaluation": False,
    }


def _peak_rss_bytes() -> int:
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _file_sizes(root: Path) -> tuple[tuple[str, int], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.stat().st_size)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _single_cache_identity(runs_root: Path, namespace: str) -> str:
    root = runs_root / "cache" / namespace
    identities = tuple(path.name for path in root.iterdir() if path.is_dir())
    if len(identities) != 1:
        raise AssertionError(f"expected one {namespace} cache identity, got {identities!r}")
    return identities[0]


def _fitted_identity(run_root: Path) -> str:
    path = run_root / "artifacts" / "fold-0000-model-state.json"
    document = json.loads(path.read_text(encoding="ascii"))
    identity = document.get("identity_key")
    if type(identity) is not str or len(identity) != 64:
        raise AssertionError("stored fitted-state reference has no exact identity key")
    return identity


def _feature_rows(run_root: Path) -> int:
    with np.load(run_root / "artifacts" / "features.npz", allow_pickle=False) as archive:
        keys = archive["keys"]
        if keys.dtype != np.dtype(np.int64) or keys.ndim != 1:
            raise AssertionError("stored feature axis is not one int64 vector")
        return int(keys.size)


def _run_once(expected_head: str) -> _RunEvidence:
    with TemporaryDirectory(prefix="phase3-run-qualification-", dir=_RUNS_TMP_ROOT) as root_text:
        qualification_root = Path(root_text)
        config_path = qualification_root / "experiment.resolved.json"
        config_path.write_text(
            json.dumps(_config_document(), sort_keys=True, separators=(",", ":")),
            encoding="ascii",
        )
        runs_root = qualification_root / "runtime"
        started = perf_counter()
        config = load_resolved_config(config_path)
        runtime = _runtime_from_paths(
            config=config,
            data_roots=REGISTRY_ROOTS,
            runs_root=runs_root,
            split_policy_path=POLICY_PATH.resolve(),
            audit_path=_AUDIT_PATH,
            repository_root=_REPO_ROOT,
            dependency_lock_path=_DEPENDENCY_LOCK,
        )
        runtime = replace(runtime, _audit_writer=_RejectingAuditWriter())
        manifest = run_experiment(
            config,
            runtime=runtime,
            heartbeat_interval_seconds=1.0,
        )
        records = scan_runs(
            runs_root,
            now_unix_ns=time.time_ns(),
            stale_after_ns=60_000_000_000,
        )
        if len(records) != 1 or records[0]._state is not RunState.COMPLETE:
            raise AssertionError("completed config-only run was not reconstructable")
        if manifest.state is not RunState.COMPLETE or not manifest.canonical:
            raise AssertionError("published TRAIN qualification run was not canonical COMPLETE")
        if manifest.platform_identity.git_commit != expected_head:
            raise AssertionError("run platform identity differs from the qualified clean commit")
        run_root = runs_root / "runs" / manifest.run_id
        evidence = _RunEvidence(
            elapsed_seconds=perf_counter() - started,
            experiment_fingerprint=manifest.experiment_fingerprint,
            run_id=manifest.run_id,
            git_commit=manifest.platform_identity.git_commit,
            feature_identity_key=_single_cache_identity(runs_root, "features"),
            fitted_state_identity_key=_fitted_identity(run_root),
            feature_rows=_feature_rows(run_root),
            evaluation_count=manifest.evaluation_count,
            policy_execution_count=len(manifest.policy_execution_json),
            artifact_hashes=manifest.artifact_hashes,
            metric_hashes=manifest.metric_hashes,
            artifact_sizes=_file_sizes(run_root / "artifacts"),
            metric_sizes=_file_sizes(run_root / "metrics"),
        )
        if evidence.feature_rows != _EVALUATION_ROWS:
            raise AssertionError("stored feature row count differs from resolved TRAIN bounds")
        if evidence.evaluation_count != 1 or evidence.policy_execution_count != 1:
            raise AssertionError("one-fold qualification inventory is not exactly one-by-one")
        return evidence


def _summarize(samples: Sequence[float]) -> _TimingSummary:
    observed = tuple(samples)
    if len(observed) != _TIMED_REPETITIONS or any(
        not math.isfinite(value) or value < 0.0 for value in observed
    ):
        raise AssertionError("qualification requires exactly five finite non-negative samples")
    return _TimingSummary(observed, median(observed), min(observed))


def _deterministic_signature(evidence: _RunEvidence) -> tuple[object, ...]:
    return (
        evidence.experiment_fingerprint,
        evidence.git_commit,
        evidence.feature_identity_key,
        evidence.fitted_state_identity_key,
        evidence.feature_rows,
        evidence.evaluation_count,
        evidence.policy_execution_count,
        evidence.artifact_hashes,
        evidence.metric_hashes,
        evidence.artifact_sizes,
        evidence.metric_sizes,
    )


def _render_evidence(document: Mapping[str, object]) -> None:
    print("PHASE3_RUN_EVIDENCE_BEGIN")
    print(json.dumps(document, sort_keys=True, allow_nan=False, separators=(",", ":")))
    print("PHASE3_RUN_EVIDENCE_END")


def test_published_train_config_to_stored_run_meets_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run one discarded warm-up and five full config-only TRAIN repetitions."""
    audit_before = _audit_snapshot()
    head = _clean_committed_head()
    pre_measure_rss_bytes = _peak_rss_bytes()

    warmup = _run_once(head)
    measured = tuple(_run_once(head) for _ in range(_TIMED_REPETITIONS))
    expected_signature = _deterministic_signature(warmup)
    assert all(_deterministic_signature(item) == expected_signature for item in measured)
    assert len({warmup.run_id, *(item.run_id for item in measured)}) == 6

    timing = _summarize(tuple(item.elapsed_seconds for item in measured))
    audit_after = _audit_snapshot()
    assert audit_after == audit_before
    peak_rss_bytes = _peak_rss_bytes()
    assert peak_rss_bytes >= pre_measure_rss_bytes > 0

    evidence: dict[str, object] = {
        "schema": _EVIDENCE_SCHEMA,
        "runs_root": str(_RUNS_ROOT),
        "target_dataset_id": TRAIN_REF.dataset_id,
        "target_manifest_sha256": TRAIN_REF.manifest_sha256,
        "target_role": TRAIN_REF.role.value,
        "dataset_row_count": TRAIN_EXPECTED_ROWS,
        "feature_row_count": warmup.feature_rows,
        "inventory_row_count": warmup.evaluation_count,
        "policy_execution_count": warmup.policy_execution_count,
        "git_commit": head,
        "experiment_fingerprint": warmup.experiment_fingerprint,
        "feature_identity_key": warmup.feature_identity_key,
        "fitted_state_identity_key": warmup.fitted_state_identity_key,
        "artifact_hashes": dict(warmup.artifact_hashes),
        "metric_hashes": dict(warmup.metric_hashes),
        "artifact_sizes": dict(warmup.artifact_sizes),
        "metric_sizes": dict(warmup.metric_sizes),
        "pre_measure_rss_bytes": pre_measure_rss_bytes,
        "peak_rss_bytes": peak_rss_bytes,
        "audit_record_count_before": len(audit_before.splitlines()),
        "audit_record_count_after": len(audit_after.splitlines()),
        "audit_sha256_before": hashlib.sha256(audit_before).hexdigest(),
        "audit_sha256_after": hashlib.sha256(audit_after).hexdigest(),
        "budget_seconds": _BUDGET_SECONDS,
        "samples_seconds": list(timing.samples_seconds),
        "median_seconds": timing.median_seconds,
        "minimum_seconds": timing.minimum_seconds,
    }
    with capsys.disabled():
        _render_evidence(evidence)

    assert timing.median_seconds < _BUDGET_SECONDS, (
        f"config-only TRAIN run median {timing.median_seconds:.9f}s missed "
        f"{_BUDGET_SECONDS:.1f}s budget; samples={timing.samples_seconds!r}"
    )
