"""Explicit published-TRAIN feature-performance qualification."""

from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

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
from trading_research.artifacts import (
    ArtifactStore,
    MaterializationPolicy,
    PlatformIdentity,
    StageIdentity,
)
from trading_research.artifacts.container import _encode_feature_artifact
from trading_research.core import GapPolicy, NativeTimeKey, ViewRole
from trading_research.data import (
    AccessClassification,
    AccessContext,
    AccessRequest,
    ContextShortfall,
    DatasetRegistry,
    GapIndex,
    MarketFrameLoader,
    authorize_access,
    open_bounded_view,
    read_split_policy,
)
from trading_research.experiments.features import compute_features, materialize_features
from trading_research.features.reference import BarRange, LogReturn1, RollingMeanClose

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import numpy.typing as npt

    from trading_research.core import (
        CostHint,
        FeatureFrame,
        FeatureProvider,
        FeatureRequest,
        ParamValue,
        WindowSpec,
    )
    from trading_research.data import AccessGrant, BoundedView, SplitPolicy

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).parents[2]
_RUNNING_FILE = Path(__file__).resolve()
_RUNS_ROOT = Path("/path/to/research-runs")
_RUNS_TMP_ROOT = _RUNS_ROOT / "tmp"
_AUDIT_PATH = _RUNS_ROOT / "audit.jsonl"
_EXPECTED_AUDIT_RECORDS = 3
_EXPECTED_AUDIT_SHA256 = (
    "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5"
)

_REQUIRED_MARKET_COLUMNS = ("high", "low", "close")
_REFERENCE_COLUMN_NAMES = (
    "log_return_1",
    "rolling_mean_close",
    "bar_range",
)
_CROSS_NAN_COUNTS = {
    "log_return_1": 1,
    "rolling_mean_close": 19,
    "bar_range": 0,
}
_NOMINAL_INTERVAL = 60
_ROLLING_REACH = 19
_TIMED_REPETITIONS = 5
_CACHE_HIT_BUDGET_SECONDS = 0.150
_FULL_PASS_BUDGET_SECONDS = 5.0
_EVIDENCE_SCHEMA = "phase2-feature-performance-v1"


@dataclass(frozen=True, slots=True)
class _TimingSummary:
    samples_seconds: tuple[float, ...]
    median_seconds: float
    minimum_seconds: float


@dataclass(frozen=True, slots=True)
class _CrossEvidence:
    timing: _TimingSummary
    payload: bytes
    content_hash: str
    nan_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _PreparedTrain:
    policy: SplitPolicy
    grant: AccessGrant
    loader: MarketFrameLoader
    view: BoundedView
    platform_identity: PlatformIdentity


class _RejectingAuditWriter:
    """Prove that the fixed TRAIN request cannot append an audit event."""

    def append(self, event: Mapping[str, object]) -> None:
        del event
        raise AssertionError("published TRAIN access unexpectedly attempted an audit write")


class _ObservedProvider:
    """Preserve one reference declaration while observing forbidden computation."""

    __slots__ = (
        "_compute_calls",
        "_cost_hint",
        "_delegate",
        "_feature_names",
        "_forbid_compute",
        "_params",
        "_provider_id",
        "_required_columns",
        "_version",
        "_window",
    )

    def __init__(self, delegate: FeatureProvider, *, forbid_compute: bool) -> None:
        self._delegate = delegate
        self._provider_id = delegate.provider_id
        self._version = delegate.version
        self._feature_names = tuple(delegate.feature_names)
        self._required_columns = tuple(delegate.required_columns)
        self._window = delegate.window
        self._params = dict(delegate.params)
        self._cost_hint = delegate.cost_hint
        self._forbid_compute = forbid_compute
        self._compute_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def version(self) -> int:
        return self._version

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._feature_names

    @property
    def required_columns(self) -> tuple[str, ...]:
        return self._required_columns

    @property
    def window(self) -> WindowSpec:
        return self._window

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return self._params

    @property
    def cost_hint(self) -> CostHint:
        return self._cost_hint

    @property
    def compute_calls(self) -> int:
        return self._compute_calls

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        self._compute_calls += 1
        if self._forbid_compute:
            raise AssertionError(
                f"verified cache hit unexpectedly computed provider {self.provider_id!r}"
            )
        return self._delegate.compute(request)


@pytest.fixture(scope="module", autouse=True)
def _require_published_train_and_runs_root() -> None:
    """Refuse explicit qualification when fixed research-VM resources are absent."""
    assert sys.platform == "linux", "Phase 2 peak-RSS qualification requires Linux"
    directories = (DATASET_ROOT, VIEWS_ROOT, _RUNS_ROOT, _RUNS_TMP_ROOT)
    files = (POLICY_PATH, TRAIN_MANIFEST, _AUDIT_PATH)
    assert all(path.is_dir() for path in directories), (
        "published TRAIN data, views, and runs roots must exist"
    )
    assert all(path.is_file() for path in files), (
        "published TRAIN manifest, split policy, and audit evidence must exist"
    )


def _git_output(*arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssertionError(f"Git inspection failed for {arguments!r}: {exc}") from exc
    return completed.stdout


def _clean_committed_platform_identity() -> PlatformIdentity:
    """Bind evidence to the exact committed harness and a clean Git tree."""
    head = _git_output("rev-parse", "HEAD").decode().strip()
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AssertionError("feature qualification requires a clean repository worktree")
    try:
        relative = _RUNNING_FILE.relative_to(_REPO_ROOT).as_posix()
        committed = _git_output("show", f"{head}:{relative}")
        current = _RUNNING_FILE.read_bytes()
    except (OSError, ValueError) as exc:
        raise AssertionError("cannot verify the committed feature-performance harness") from exc
    if committed != current:
        raise AssertionError("feature-performance harness is not the committed HEAD version")
    return PlatformIdentity(git_commit=head, git_dirty=False, diff_sha256=None)


def _audit_snapshot() -> bytes:
    if _AUDIT_PATH.is_symlink() or not _AUDIT_PATH.is_file():
        raise AssertionError(f"audit log is not an existing regular file: {_AUDIT_PATH}")
    payload = _AUDIT_PATH.read_bytes()
    if len(payload.splitlines()) != _EXPECTED_AUDIT_RECORDS:
        raise AssertionError("Phase 2 qualification requires the exact three-record audit log")
    if hashlib.sha256(payload).hexdigest() != _EXPECTED_AUDIT_SHA256:
        raise AssertionError("Phase 2 qualification audit bytes differ from CP-7 evidence")
    return payload


def _native_key(value: int, policy: SplitPolicy) -> NativeTimeKey:
    return NativeTimeKey(value, policy.time_unit, policy.clock_domain)


def _open_full_train_view(
    *,
    loader: MarketFrameLoader,
    grant: AccessGrant,
    policy: SplitPolicy,
) -> BoundedView:
    admitted = grant.registered_manifest.manifest.admitted_native_range
    eval_start = _native_key(admitted.start, policy)
    eval_end = _native_key(admitted.end, policy)
    requested_context_start = _native_key(admitted.start - 1, policy)
    shortfall = ContextShortfall(
        requested_context_start=requested_context_start,
        resolved_context_start=eval_start,
        reason_code="published-train-initial-boundary",
        detail=(
            "The full published TRAIN view begins at its earliest observed row; "
            "accept the initial provider-declared warm-up only for Phase 2 "
            "infrastructure qualification."
        ),
        accepted=True,
    )
    return open_bounded_view(
        loader=loader,
        grants=(grant,),
        context_start=requested_context_start,
        eval_start=eval_start,
        eval_end=eval_end,
        columns=_REQUIRED_MARKET_COLUMNS,
        context_shortfall=shortfall,
    )


def _validate_train_view(view: BoundedView) -> None:
    """Validate the fixed full-TRAIN view outside every reported timer."""
    shortfall = view.context.shortfall
    assert shortfall is not None
    assert shortfall.reason_code == "published-train-initial-boundary"
    assert shortfall.accepted is True
    assert view.context.short_context_accepted is True
    assert len(view.segments) == 1
    assert view.segments[0].dataset_ref == TRAIN_REF
    assert view.eval_frame.dataset_ref == TRAIN_REF
    assert view.eval_frame.num_rows == TRAIN_EXPECTED_ROWS
    assert view.eval_frame.column_names == _REQUIRED_MARKET_COLUMNS


def _prepare_train(platform_identity: PlatformIdentity) -> _PreparedTrain:
    policy = read_split_policy(POLICY_PATH)
    registry = DatasetRegistry.discover(REGISTRY_ROOTS)
    registered = registry.resolve(TRAIN_REF)
    assert registered.manifest.role is ViewRole.TRAIN
    assert registered.manifest.expected_row_count == TRAIN_EXPECTED_ROWS
    grant = authorize_access(
        registry=registry,
        request=AccessRequest(dataset_ref=TRAIN_REF),
        policy=policy,
        context=AccessContext(
            session_id=uuid4(),
            os_user=getpass.getuser(),
            hostname=socket.gethostname(),
            pid=os.getpid(),
            platform_git_commit=platform_identity.git_commit,
            platform_git_dirty=platform_identity.git_dirty,
        ),
        audit_writer=_RejectingAuditWriter(),
    )
    assert grant.classification is AccessClassification.NORMAL
    assert grant.audit_event_id is None
    loader = MarketFrameLoader(registry=registry, split_policy=policy)
    view = _open_full_train_view(loader=loader, grant=grant, policy=policy)
    _validate_train_view(view)
    return _PreparedTrain(
        policy=policy,
        grant=grant,
        loader=loader,
        view=view,
        platform_identity=platform_identity,
    )


def _reference_providers() -> tuple[FeatureProvider, ...]:
    return (LogReturn1(), RollingMeanClose(), BarRange())


def _break_reference_providers() -> tuple[FeatureProvider, ...]:
    return (
        LogReturn1(),
        RollingMeanClose(
            gap_policy=GapPolicy.BREAK,
            nominal_interval=_NOMINAL_INTERVAL,
        ),
        BarRange(),
    )


def _stage_identity(
    prepared: _PreparedTrain,
    providers: Sequence[FeatureProvider],
) -> StageIdentity:
    view = prepared.view
    split = prepared.policy.identity
    return StageIdentity(
        source_identities=tuple(
            segment.dataset_ref.canonical_identity for segment in view.segments
        ),
        resolved_context_start=view.context.resolved_context_start,
        eval_start=view.eval_start,
        eval_end=view.eval_end,
        split_policy_adr=split.adr,
        split_policy_version=split.policy_version,
        split_policy_sha256=split.policy_sha256,
        providers=providers,
        platform_identity=prepared.platform_identity,
    )


def _summarize(samples: Sequence[float]) -> _TimingSummary:
    observed = tuple(samples)
    if len(observed) != _TIMED_REPETITIONS or any(
        not math.isfinite(value) or value < 0.0 for value in observed
    ):
        raise AssertionError("qualification requires exactly five finite non-negative samples")
    return _TimingSummary(
        samples_seconds=observed,
        median_seconds=median(observed),
        minimum_seconds=min(observed),
    )


def _nan_counts(frame: FeatureFrame) -> dict[str, int]:
    return {
        name: int(np.count_nonzero(np.isnan(frame.column(name))))
        for name in frame.column_names
    }


def _validate_feature_frame(
    frame: FeatureFrame,
    *,
    view: BoundedView,
    expected_nan_counts: Mapping[str, int],
) -> dict[str, int]:
    assert frame.num_rows == TRAIN_EXPECTED_ROWS
    assert frame.keys.tobytes(order="C") == view.eval_frame.keys.tobytes(order="C")
    assert frame.unit is view.eval_frame.unit
    assert frame.clock_domain == view.eval_frame.clock_domain
    assert frame.source_identities == (TRAIN_REF.canonical_identity,)
    assert frame.column_names == _REFERENCE_COLUMN_NAMES
    assert tuple(frame.schema.items()) == tuple(
        (name, "float64") for name in _REFERENCE_COLUMN_NAMES
    )
    counts = _nan_counts(frame)
    assert counts == dict(expected_nan_counts)
    return counts


def _measure_cross_passes(
    prepared: _PreparedTrain,
    providers: Sequence[FeatureProvider],
    identity: StageIdentity,
) -> _CrossEvidence:
    def compute_once() -> FeatureFrame:
        return compute_features(prepared.view, providers)

    discarded = compute_once()
    nan_counts = _validate_feature_frame(
        discarded,
        view=prepared.view,
        expected_nan_counts=_CROSS_NAN_COUNTS,
    )
    canonical_payload = _encode_feature_artifact(identity, discarded)
    del discarded

    samples: list[float] = []
    for _ in range(_TIMED_REPETITIONS):
        started = perf_counter()
        frame = compute_once()
        samples.append(perf_counter() - started)
        assert (
            _validate_feature_frame(
                frame,
                view=prepared.view,
                expected_nan_counts=_CROSS_NAN_COUNTS,
            )
            == nan_counts
        )
        assert _encode_feature_artifact(identity, frame) == canonical_payload
        del frame

    return _CrossEvidence(
        timing=_summarize(samples),
        payload=canonical_payload,
        content_hash=hashlib.sha256(canonical_payload).hexdigest(),
        nan_counts=nan_counts,
    )


def _measure_feature_passes(
    operation: Callable[[], FeatureFrame],
    *,
    view: BoundedView,
    expected_nan_counts: Mapping[str, int],
) -> tuple[_TimingSummary, dict[str, int]]:
    discarded = operation()
    observed_counts = _validate_feature_frame(
        discarded,
        view=view,
        expected_nan_counts=expected_nan_counts,
    )
    del discarded
    samples: list[float] = []
    for _ in range(_TIMED_REPETITIONS):
        started = perf_counter()
        frame = operation()
        samples.append(perf_counter() - started)
        assert (
            _validate_feature_frame(
                frame,
                view=view,
                expected_nan_counts=expected_nan_counts,
            )
            == observed_counts
        )
        del frame
    return _summarize(samples), observed_counts


def _expected_break_nan_counts(keys: npt.NDArray[np.int64]) -> tuple[dict[str, int], int]:
    unsigned = keys.view(np.uint64)
    gap_positions = np.flatnonzero(
        unsigned[1:] - unsigned[:-1] != np.uint64(_NOMINAL_INTERVAL)
    ) + 1
    unavailable = np.arange(keys.size, dtype=np.int64) < _ROLLING_REACH
    for offset in range(_ROLLING_REACH):
        positions = gap_positions + offset
        unavailable[positions[positions < keys.size]] = True
    rolling_nan_count = int(np.count_nonzero(unavailable))
    assert gap_positions.size > 0
    assert rolling_nan_count > _CROSS_NAN_COUNTS["rolling_mean_close"]
    return (
        {
            "log_return_1": 1,
            "rolling_mean_close": rolling_nan_count,
            "bar_range": 0,
        },
        int(gap_positions.size),
    )


def _measure_gap_index(view: BoundedView, *, expected_num_gaps: int) -> _TimingSummary:
    def compute_once() -> GapIndex:
        return GapIndex.from_frame(
            view.eval_frame,
            nominal_interval=_NOMINAL_INTERVAL,
        )

    discarded = compute_once()
    assert discarded.num_gaps == expected_num_gaps
    del discarded
    samples: list[float] = []
    for _ in range(_TIMED_REPETITIONS):
        started = perf_counter()
        gap_index = compute_once()
        samples.append(perf_counter() - started)
        assert gap_index.num_gaps == expected_num_gaps
        del gap_index
    return _summarize(samples)


def _measure_load_plus_full(
    prepared: _PreparedTrain,
    providers: Sequence[FeatureProvider],
    *,
    identity: StageIdentity,
    expected_payload: bytes,
) -> _TimingSummary:
    """Measure production view opening plus the complete reference pass."""

    def operation() -> tuple[BoundedView, FeatureFrame]:
        view = _open_full_train_view(
            loader=prepared.loader,
            grant=prepared.grant,
            policy=prepared.policy,
        )
        return view, compute_features(view, providers)

    discarded_view, discarded_frame = operation()
    _validate_train_view(discarded_view)
    _validate_feature_frame(
        discarded_frame,
        view=discarded_view,
        expected_nan_counts=_CROSS_NAN_COUNTS,
    )
    assert _encode_feature_artifact(identity, discarded_frame) == expected_payload
    del discarded_frame, discarded_view

    samples: list[float] = []
    for _ in range(_TIMED_REPETITIONS):
        started = perf_counter()
        view, frame = operation()
        samples.append(perf_counter() - started)
        _validate_train_view(view)
        _validate_feature_frame(
            frame,
            view=view,
            expected_nan_counts=_CROSS_NAN_COUNTS,
        )
        assert _encode_feature_artifact(identity, frame) == expected_payload
        del frame, view
    return _summarize(samples)


def _measure_full_plus_persist(
    prepared: _PreparedTrain,
    providers: Sequence[FeatureProvider],
    *,
    identity: StageIdentity,
    expected_payload: bytes,
) -> _TimingSummary:
    """Measure complete misses in test-owned roots on the real runs filesystem."""

    def operation() -> float:
        with TemporaryDirectory(
            prefix="phase2-feature-persist-",
            dir=_RUNS_TMP_ROOT,
        ) as temporary_root:
            store = ArtifactStore(Path(temporary_root))
            observed = tuple(
                _ObservedProvider(provider, forbid_compute=False)
                for provider in providers
            )
            assert _stage_identity(prepared, observed).identity_key() == identity.identity_key()
            started = perf_counter()
            frame = _materialize(prepared, observed, store=store)
            elapsed_seconds = perf_counter() - started
            assert tuple(provider.compute_calls for provider in observed) == (1, 1, 1)
            _validate_feature_frame(
                frame,
                view=prepared.view,
                expected_nan_counts=_CROSS_NAN_COUNTS,
            )
            assert _encode_feature_artifact(identity, frame) == expected_payload
            assert store.get(identity.identity_key()) == expected_payload
            del frame
            return elapsed_seconds

    operation()
    return _summarize([operation() for _ in range(_TIMED_REPETITIONS)])


def _materialize(
    prepared: _PreparedTrain,
    providers: Sequence[FeatureProvider],
    *,
    store: ArtifactStore,
) -> FeatureFrame:
    return materialize_features(
        prepared.view,
        providers,
        split_policy_identity=prepared.policy.identity,
        platform_identity=prepared.platform_identity,
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )


def _ensure_canonical_cache_entry(
    prepared: _PreparedTrain,
    providers: Sequence[FeatureProvider],
    *,
    store: ArtifactStore,
    identity: StageIdentity,
    expected_payload: bytes,
) -> str:
    identity_key = identity.identity_key()
    existing = store.get(identity_key)
    if existing is not None:
        assert existing == expected_payload
        return "existing_verified"

    observed = tuple(_ObservedProvider(provider, forbid_compute=False) for provider in providers)
    assert _stage_identity(prepared, observed).identity_key() == identity_key
    frame = _materialize(prepared, observed, store=store)
    calls = tuple(provider.compute_calls for provider in observed)
    if calls == (0, 0, 0):
        status = "concurrent_hit_verified"
    else:
        assert calls == (1, 1, 1)
        status = "published_after_confirmed_miss"
    _validate_feature_frame(
        frame,
        view=prepared.view,
        expected_nan_counts=_CROSS_NAN_COUNTS,
    )
    assert _encode_feature_artifact(identity, frame) == expected_payload
    del frame
    assert store.get(identity_key) == expected_payload
    return status


def _peak_rss_bytes() -> int:
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _timing_fields(prefix: str, summary: _TimingSummary) -> dict[str, object]:
    return {
        f"{prefix}_samples_seconds": list(summary.samples_seconds),
        f"{prefix}_median_seconds": summary.median_seconds,
        f"{prefix}_minimum_seconds": summary.minimum_seconds,
    }


def _render_evidence(evidence: Mapping[str, object]) -> None:
    encoded = json.dumps(
        evidence,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    print("PHASE2_FEATURE_EVIDENCE_BEGIN")
    print(encoded)
    print("PHASE2_FEATURE_EVIDENCE_END")


def test_published_train_reference_features_and_verified_cache_hit_meet_budgets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Measure only the fixed full-TRAIN feature and verified-hit regions."""
    audit_before = _audit_snapshot()
    platform_identity = _clean_committed_platform_identity()
    pre_measure_rss_bytes = _peak_rss_bytes()
    prepared = _prepare_train(platform_identity)
    providers = _reference_providers()
    assert tuple(provider.provider_id for provider in providers) == _REFERENCE_COLUMN_NAMES
    identity = _stage_identity(prepared, providers)

    cross = _measure_cross_passes(prepared, providers, identity)

    load_plus_full_timing = _measure_load_plus_full(
        prepared,
        providers,
        identity=identity,
        expected_payload=cross.payload,
    )
    full_plus_persist_timing = _measure_full_plus_persist(
        prepared,
        providers,
        identity=identity,
        expected_payload=cross.payload,
    )

    store = ArtifactStore(_RUNS_ROOT)
    persist_status = _ensure_canonical_cache_entry(
        prepared,
        providers,
        store=store,
        identity=identity,
        expected_payload=cross.payload,
    )

    hit_providers = tuple(
        _ObservedProvider(provider, forbid_compute=True) for provider in providers
    )
    assert _stage_identity(prepared, hit_providers).identity_key() == identity.identity_key()
    hit_timing, hit_nan_counts = _measure_feature_passes(
        lambda: _materialize(prepared, hit_providers, store=store),
        view=prepared.view,
        expected_nan_counts=_CROSS_NAN_COUNTS,
    )
    assert tuple(provider.compute_calls for provider in hit_providers) == (0, 0, 0)
    assert hit_nan_counts == dict(cross.nan_counts)

    expected_break_nans, expected_num_gaps = _expected_break_nan_counts(
        prepared.view.eval_frame.keys
    )
    gap_timing = _measure_gap_index(
        prepared.view,
        expected_num_gaps=expected_num_gaps,
    )
    break_providers = _break_reference_providers()
    break_timing, break_nan_counts = _measure_feature_passes(
        lambda: compute_features(prepared.view, break_providers),
        view=prepared.view,
        expected_nan_counts=expected_break_nans,
    )

    settled_payload = store.get(identity.identity_key())
    assert settled_payload == cross.payload
    artifact_path = (
        _RUNS_ROOT
        / "cache"
        / "features"
        / identity.identity_key()
        / f"{cross.content_hash}.npz"
    )
    assert artifact_path.is_file()
    assert artifact_path.stat().st_size == len(cross.payload)

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
        "row_count": TRAIN_EXPECTED_ROWS,
        "provider_order": [provider.provider_id for provider in providers],
        "git_commit": platform_identity.git_commit,
        "git_dirty": platform_identity.git_dirty,
        "git_diff_sha256": platform_identity.diff_sha256,
        "identity_key": identity.identity_key(),
        "content_hash": cross.content_hash,
        "artifact_bytes": len(cross.payload),
        "artifact_relative_path": artifact_path.relative_to(_RUNS_ROOT).as_posix(),
        "cross_nan_counts": dict(cross.nan_counts),
        "break_nan_counts": break_nan_counts,
        "gap_index_num_gaps": expected_num_gaps,
        "pre_measure_rss_bytes": pre_measure_rss_bytes,
        "peak_rss_bytes": peak_rss_bytes,
        "canonical_persist_status": persist_status,
        "audit_record_count_before": len(audit_before.splitlines()),
        "audit_record_count_after": len(audit_after.splitlines()),
        "audit_sha256_before": hashlib.sha256(audit_before).hexdigest(),
        "audit_sha256_after": hashlib.sha256(audit_after).hexdigest(),
        "cache_hit_budget_seconds": _CACHE_HIT_BUDGET_SECONDS,
        "full_pass_budget_seconds": _FULL_PASS_BUDGET_SECONDS,
        **_timing_fields("full_pass", cross.timing),
        **_timing_fields("cache_hit", hit_timing),
        **_timing_fields("gap_index", gap_timing),
        **_timing_fields("break_pass", break_timing),
        **_timing_fields("load_plus_full", load_plus_full_timing),
        **_timing_fields("full_plus_persist", full_plus_persist_timing),
    }
    with capsys.disabled():
        _render_evidence(evidence)

    assert cross.timing.median_seconds < _FULL_PASS_BUDGET_SECONDS, (
        f"full TRAIN feature pass median {cross.timing.median_seconds:.9f}s "
        f"missed {_FULL_PASS_BUDGET_SECONDS:.3f}s budget; "
        f"samples={cross.timing.samples_seconds!r}"
    )
    assert hit_timing.median_seconds < _CACHE_HIT_BUDGET_SECONDS, (
        f"verified TRAIN cache-hit median {hit_timing.median_seconds:.9f}s "
        f"missed {_CACHE_HIT_BUDGET_SECONDS:.3f}s budget; "
        f"samples={hit_timing.samples_seconds!r}"
    )
