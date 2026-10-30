"""Slice 2F materialization policy and cache-composition qualification."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import OrderedDict
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

import trading_research.artifacts.materialization as materialization_module
from trading_research.artifacts import (
    ArtifactStore,
    MaterializationPolicy,
    PlatformIdentity,
    StageIdentity,
)
from trading_research.artifacts.container import _encode_feature_artifact
from trading_research.core import (
    ClockDomain,
    CostHint,
    DatasetRef,
    FeatureContractError,
    FeatureFrame,
    FeatureFrameError,
    FeatureOrigin,
    FeatureProvider,
    FeatureRequest,
    GapPolicy,
    InsufficientContextError,
    NativeTimeKey,
    ParamValue,
    TimeUnit,
    TradingResearchError,
    ViewRole,
    WindowSpec,
)
from trading_research.core.feature_frames import _make_feature_frame
from trading_research.core.frames import MarketFrame, _make_market_frame
from trading_research.data import SplitPolicyIdentity
from trading_research.data.views import (
    BoundedSegment,
    BoundedView,
    ContextResolution,
    ContextShortfall,
)
from trading_research.experiments.features import materialize_features

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from pathlib import Path
    from typing import Protocol


    class _ComputationFailure(Protocol):
        identity_key: str
        provider_id: str

_DOMAIN = ClockDomain("feature-materialization.wallclock")
_CONTEXT_DIGEST = "a" * 64
_EVALUATION_DIGEST = "b" * 64
_POLICY_DIGEST = "c" * 64
_COMMIT = "d" * 40
_ZERO_WINDOW = WindowSpec(0, GapPolicy.CROSS)


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, _DOMAIN)


def _frame(
    keys: Sequence[int],
    *,
    dataset_id: str,
    digest: str,
    role: ViewRole,
) -> MarketFrame:
    key_array = np.asarray(keys, dtype=np.int64)
    close = np.arange(100.0, 100.0 + key_array.size, dtype=np.float64)
    return _make_market_frame(
        dataset_ref=DatasetRef(dataset_id, digest, role),
        clock_domain=_DOMAIN,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=key_array,
        columns={"close": close},
        admitted_lo=int(key_array[0]),
        admitted_hi=int(key_array[-1]),
    )


def _view(
    *,
    context_digest: str = _CONTEXT_DIGEST,
    evaluation_digest: str = _EVALUATION_DIGEST,
    context_role: ViewRole = ViewRole.TRAIN,
    evaluation_role: ViewRole = ViewRole.VALIDATION,
    requested_context_start: int = 10,
    resolved_context_start: int = 10,
    eval_end: int = 55,
) -> BoundedView:
    context_frame = _frame(
        (10, 20),
        dataset_id="SYNTH_CONTEXT",
        digest=context_digest,
        role=context_role,
    )
    evaluation_frame = _frame(
        (30, 40, 50, 60),
        dataset_id="SYNTH_EVALUATION",
        digest=evaluation_digest,
        role=evaluation_role,
    ).slice_by_keys(_key(30), _key(eval_end))
    frames = (
        context_frame,
        evaluation_frame,
    )
    requested = _key(requested_context_start)
    resolved = _key(resolved_context_start)
    shortfall = (
        None
        if requested == resolved
        else ContextShortfall(
            requested_context_start=requested,
            resolved_context_start=resolved,
            reason_code="synthetic-short-history",
            detail="The synthetic fixture explicitly accepts its context shortfall.",
            accepted=True,
        )
    )
    context = ContextResolution._resolved(
        requested_context_start=requested,
        resolved_context_start=resolved,
        shortfall=shortfall,
    )
    return BoundedView._from_bounded(
        segments=tuple(BoundedSegment._from_bounded(frame) for frame in frames),
        eval_frame=evaluation_frame,
        context=context,
        context_start=requested,
        eval_start=_key(30),
        eval_end=_key(eval_end),
    )


class _Provider:
    def __init__(
        self,
        provider_id: str = "materialized",
        *,
        version: int = 1,
        feature_names: tuple[str, ...] = ("materialized.value",),
        required_columns: tuple[str, ...] = ("close",),
        window: WindowSpec = _ZERO_WINDOW,
        params: Mapping[str, ParamValue] | None = None,
        cost_hint: CostHint = CostHint.CHEAP,
        output: Callable[[FeatureRequest], object] | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.version = version
        self.feature_names = feature_names
        self.required_columns = required_columns
        self.window = window
        self.params = {"scale": 1.0} if params is None else params
        self.cost_hint = cost_hint
        self.output = output
        self.failure = failure
        self.calls = 0
        self.forbid_compute = False

    def compute(self, request: FeatureRequest) -> object:
        if self.forbid_compute:
            raise AssertionError("cache hit recomputed a provider")
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        if self.output is not None:
            return self.output(request)
        bits = np.asarray(
            [0x0000000000000000, 0x8000000000000000, 0x7FF8000000000042],
            dtype=np.uint64,
        )
        values = bits[: request.eval_length].view(np.float64).copy()
        return {
            name: values.copy() if index == 0 else values.copy() + float(index)
            for index, name in enumerate(self.feature_names)
        }


class _MemoryStore:
    def __init__(self) -> None:
        self.entries: dict[str, bytes] = {}
        self.get_calls: list[str] = []
        self.put_calls: list[tuple[str, bytes]] = []

    def get(self, identity_key: str) -> bytes | None:
        self.get_calls.append(identity_key)
        return self.entries.get(identity_key)

    def put(self, identity_key: str, payload: bytes) -> str:
        self.put_calls.append((identity_key, payload))
        prior = self.entries.get(identity_key)
        if prior is not None and prior != payload:
            raise AssertionError("test double observed conflicting immutable content")
        self.entries[identity_key] = payload
        return hashlib.sha256(payload).hexdigest()


class _RaisingStore:
    def get(self, identity_key: str) -> bytes | None:
        raise AssertionError(f"policy unexpectedly read store key {identity_key}")

    def put(self, identity_key: str, payload: bytes) -> str:
        raise AssertionError(
            f"policy unexpectedly wrote store key {identity_key} ({len(payload)} bytes)"
        )


class _GetErrorStore:
    def __init__(self, payload: bytes | None = None) -> None:
        self.payload = payload
        self.put_calls = 0

    def get(self, identity_key: str) -> bytes | None:
        if self.payload is not None:
            return self.payload
        raise TradingResearchError(f"injected corrupt store read for {identity_key}")

    def put(self, identity_key: str, payload: bytes) -> str:
        self.put_calls += 1
        raise AssertionError((identity_key, payload))


class _PutErrorStore(_MemoryStore):
    def put(self, identity_key: str, payload: bytes) -> str:
        self.put_calls.append((identity_key, payload))
        raise TradingResearchError("injected publication failure")


def _policy() -> SplitPolicyIdentity:
    return SplitPolicyIdentity("ADR-0012", "v1", _POLICY_DIGEST)


def _platform(
    *,
    commit: str = _COMMIT,
    dirty: bool = False,
    diff: str | None = None,
) -> PlatformIdentity:
    return PlatformIdentity(commit, dirty, diff)


def _call(
    view: BoundedView,
    providers: Sequence[object],
    *,
    store: object,
    policy: MaterializationPolicy | None = None,
    split_policy_identity: SplitPolicyIdentity | None = None,
    platform_identity: PlatformIdentity | None = None,
) -> FeatureFrame:
    kwargs: dict[str, object] = {
        "split_policy_identity": _policy()
        if split_policy_identity is None
        else split_policy_identity,
        "platform_identity": _platform()
        if platform_identity is None
        else platform_identity,
        "store": cast("ArtifactStore", store),
    }
    if policy is not None:
        kwargs["policy"] = policy
    return materialize_features(
        view,
        cast("Sequence[FeatureProvider]", providers),
        **kwargs,  # type: ignore[arg-type]
    )


def _identity(
    view: BoundedView,
    providers: Sequence[object],
    *,
    split_policy_identity: SplitPolicyIdentity | None = None,
    platform_identity: PlatformIdentity | None = None,
) -> StageIdentity:
    split = _policy() if split_policy_identity is None else split_policy_identity
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
        providers=cast("Sequence[FeatureProvider]", providers),
        platform_identity=_platform()
        if platform_identity is None
        else platform_identity,
    )


def _assert_frame_bits_equal(actual: FeatureFrame, expected: FeatureFrame) -> None:
    assert actual.keys.tobytes() == expected.keys.tobytes()
    assert actual.unit is expected.unit
    assert actual.clock_domain == expected.clock_domain
    assert actual.source_identities == expected.source_identities
    assert actual.column_names == expected.column_names
    assert dict(actual.schema) == dict(expected.schema)
    for name in actual.column_names:
        assert actual.column(name).view(np.uint64).tobytes() == expected.column(name).view(
            np.uint64
        ).tobytes()
        assert actual.origin(name) == expected.origin(name)


def test_materialization_policy_has_the_exact_four_values() -> None:
    assert tuple(MaterializationPolicy) == (
        MaterializationPolicy.EPHEMERAL,
        MaterializationPolicy.CACHE_IF_EXPENSIVE,
        MaterializationPolicy.PERSIST,
        MaterializationPolicy.PERSIST_ON_FAILURE,
    )
    assert tuple(policy.value for policy in MaterializationPolicy) == (
        "ephemeral",
        "cache_if_expensive",
        "persist",
        "persist_on_failure",
    )


def test_real_store_miss_then_hit_is_bitwise_identical_and_does_not_recompute(
    tmp_path: Path,
) -> None:
    view = _view()
    provider = _Provider()
    store = ArtifactStore(tmp_path)

    miss = _call(
        view,
        (provider,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )
    assert provider.calls == 1
    identity = _identity(view, (provider,))
    identity_dir = tmp_path / "cache" / "features" / identity.identity_key()
    finals = tuple(identity_dir.glob("*.npz"))
    assert len(finals) == 1
    content_hash = hashlib.sha256(finals[0].read_bytes()).hexdigest()
    assert finals[0].name == f"{content_hash}.npz"

    provider.forbid_compute = True
    hit = _call(
        view,
        (provider,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )

    assert provider.calls == 1
    _assert_frame_bits_equal(hit, miss)
    assert hit is not miss
    for name in hit.column_names:
        assert not np.shares_memory(hit.column(name), miss.column(name))
        assert hit.column(name).flags.writeable is False
    assert hashlib.sha256(_encode_feature_artifact(identity, hit)).hexdigest() == content_hash


_PROCESS_PROGRAM = r"""
import json
import os
import time
from pathlib import Path
import numpy as np
from trading_research.artifacts import ArtifactStore, MaterializationPolicy, PlatformIdentity
from trading_research.core import (
    ClockDomain, CostHint, DatasetRef, GapPolicy, NativeTimeKey, TimeUnit,
    ViewRole, WindowSpec,
)
from trading_research.core.frames import _make_market_frame
from trading_research.data import SplitPolicyIdentity
from trading_research.data.views import BoundedSegment, BoundedView, ContextResolution
from trading_research.experiments.features import materialize_features

domain = ClockDomain("feature-materialization.wallclock")
def key(value):
    return NativeTimeKey(value, TimeUnit.SECONDS, domain)
def frame(keys, dataset_id, digest, role):
    keys = np.asarray(keys, dtype=np.int64)
    close = np.arange(100.0, 100.0 + keys.size, dtype=np.float64)
    return _make_market_frame(
        dataset_ref=DatasetRef(dataset_id, digest, role), clock_domain=domain,
        unit=TimeUnit.SECONDS, schema={"close": "float64"}, keys=keys,
        columns={"close": close}, admitted_lo=int(keys[0]), admitted_hi=int(keys[-1]),
    )
context_frame = frame((10, 20), "SYNTH_CONTEXT", "a" * 64, ViewRole.TRAIN)
evaluation_frame = frame((30, 40, 50), "SYNTH_EVALUATION", "b" * 64, ViewRole.VALIDATION)
context = ContextResolution._resolved(
    requested_context_start=key(10), resolved_context_start=key(10), shortfall=None,
)
view = BoundedView._from_bounded(
    segments=(BoundedSegment._from_bounded(context_frame),
              BoundedSegment._from_bounded(evaluation_frame)),
    eval_frame=evaluation_frame, context=context, context_start=key(10),
    eval_start=key(30), eval_end=key(50),
)
class Provider:
    provider_id = "process-provider"
    version = 1
    feature_names = ("process.value",)
    required_columns = ("close",)
    window = WindowSpec(0, GapPolicy.CROSS)
    params = {"scale": 1.0}
    cost_hint = CostHint.CHEAP
    def __init__(self):
        self.calls = 0
    def compute(self, request):
        self.calls += 1
        return {"process.value": np.asarray([0.0, -0.0, np.nan], dtype=np.float64)}
provider = Provider()
result = materialize_features(
    view, (provider,),
    split_policy_identity=SplitPolicyIdentity("ADR-0012", "v1", "c" * 64),
    platform_identity=PlatformIdentity("d" * 40, False, None),
    store=ArtifactStore(Path(os.environ["TEST_RUNS_ROOT"])),
    policy=MaterializationPolicy.PERSIST,
)
print(json.dumps({
    "calls": provider.calls, "pid": os.getpid(), "cwd": os.getcwd(),
    "hostname": os.environ["HOSTNAME"],
    "wall_time_ns": time.time_ns(),
    "bits": result.column("process.value").view(np.uint64).tolist(),
}))
"""


def _run_process(*, root: Path, cwd: Path, hostname: str, hash_seed: str) -> dict[str, object]:
    environment = os.environ.copy()
    environment["HOSTNAME"] = hostname
    environment["PYTHONHASHSEED"] = hash_seed
    environment["TEST_RUNS_ROOT"] = str(root)
    completed = subprocess.run(
        [sys.executable, "-c", _PROCESS_PROGRAM],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return cast("dict[str, object]", json.loads(completed.stdout))


def test_pid_hostname_cwd_wall_time_and_hash_seed_do_not_invalidate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    left = tmp_path / "left"
    right = tmp_path / "right"
    root.mkdir()
    left.mkdir()
    right.mkdir()

    first = _run_process(root=root, cwd=left, hostname="first-host", hash_seed="1")
    second = _run_process(root=root, cwd=right, hostname="second-host", hash_seed="987654")

    assert (first["calls"], second["calls"]) == (1, 0)
    assert first["bits"] == second["bits"]
    assert first["pid"] != second["pid"]
    assert first["cwd"] != second["cwd"]
    assert first["hostname"] != second["hostname"]
    assert first["wall_time_ns"] != second["wall_time_ns"]
    identity_directories = tuple((root / "cache" / "features").iterdir())
    assert len(identity_directories) == 1
    assert len(tuple(identity_directories[0].glob("*.npz"))) == 1


def test_persist_hit_never_builds_a_request_or_reads_market_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view()
    provider = _Provider()
    store = _MemoryStore()
    expected = _call(
        view,
        (provider,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )

    def forbidden_request(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cache hit constructed a FeatureRequest")

    def forbidden_column(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cache hit read a market column")

    monkeypatch.setattr(FeatureRequest, "from_segments", forbidden_request)
    monkeypatch.setattr(MarketFrame, "column", forbidden_column)
    provider.forbid_compute = True

    hit = _call(
        view,
        (provider,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )

    _assert_frame_bits_equal(hit, expected)
    assert provider.calls == 1
    assert len(store.put_calls) == 1


@pytest.mark.parametrize(
    "policy",
    [
        MaterializationPolicy.EPHEMERAL,
        MaterializationPolicy.PERSIST_ON_FAILURE,
    ],
)
def test_store_free_policies_compute_without_touching_the_store(
    policy: MaterializationPolicy,
) -> None:
    provider = _Provider()
    result = _call(_view(), (provider,), store=_RaisingStore(), policy=policy)

    assert result.column_names == provider.feature_names
    assert provider.calls == 1


def test_default_and_all_cheap_cache_if_expensive_are_store_free() -> None:
    first = _Provider("first", feature_names=("first",))
    second = _Provider("second", feature_names=("second",))

    default_result = _call(_view(), (first, second), store=_RaisingStore())
    explicit_result = _call(
        _view(),
        (_Provider(),),
        store=_RaisingStore(),
        policy=MaterializationPolicy.CACHE_IF_EXPENSIVE,
    )

    assert default_result.column_names == ("first", "second")
    assert explicit_result.column_names == ("materialized.value",)
    assert (first.calls, second.calls) == (1, 1)


@pytest.mark.parametrize("expensive_index", [0, 1])
def test_any_expensive_provider_makes_cache_if_expensive_persistent(
    expensive_index: int,
) -> None:
    providers = [
        _Provider("first", feature_names=("first",)),
        _Provider("second", feature_names=("second",)),
    ]
    providers[expensive_index].cost_hint = CostHint.EXPENSIVE
    store = _MemoryStore()

    first = _call(
        _view(),
        providers,
        store=store,
        policy=MaterializationPolicy.CACHE_IF_EXPENSIVE,
    )
    for provider in providers:
        provider.forbid_compute = True
    second = _call(
        _view(),
        providers,
        store=store,
        policy=MaterializationPolicy.CACHE_IF_EXPENSIVE,
    )

    _assert_frame_bits_equal(second, first)
    assert len(store.get_calls) == 2
    assert len(store.put_calls) == 1


def test_persist_on_failure_wraps_provider_failure_with_identity_and_provider() -> None:
    failure = RuntimeError("synthetic provider failure")
    provider = _Provider("failing", feature_names=("failing",), failure=failure)
    view = _view()

    with pytest.raises(TradingResearchError) as caught:
        _call(
            view,
            (provider,),
            store=_RaisingStore(),
            policy=MaterializationPolicy.PERSIST_ON_FAILURE,
        )

    expected_key = _identity(view, (provider,)).identity_key()
    failure_error = cast("_ComputationFailure", caught.value)
    assert failure_error.identity_key == expected_key
    assert failure_error.provider_id == "failing"
    assert caught.value.__cause__ is failure
    assert provider.calls == 1


def test_persist_on_failure_names_the_later_provider_and_persists_nothing() -> None:
    first = _Provider("first", feature_names=("first",))
    failure = RuntimeError("later provider failed")
    second = _Provider(
        "second",
        feature_names=("second",),
        failure=failure,
    )

    with pytest.raises(TradingResearchError) as caught:
        _call(
            _view(),
            (first, second),
            store=_RaisingStore(),
            policy=MaterializationPolicy.PERSIST_ON_FAILURE,
        )

    wrapped = cast("_ComputationFailure", caught.value)
    assert wrapped.provider_id == "second"
    assert caught.value.__cause__ is failure
    assert (first.calls, second.calls) == (1, 1)


def test_persist_on_failure_wraps_insufficient_context_without_store_access() -> None:
    provider = _Provider(
        "needs-context",
        feature_names=("needs.context",),
        window=WindowSpec(3, GapPolicy.CROSS),
    )
    view = _view()

    with pytest.raises(TradingResearchError) as caught:
        _call(
            view,
            (provider,),
            store=_RaisingStore(),
            policy=MaterializationPolicy.PERSIST_ON_FAILURE,
        )

    wrapped = cast("_ComputationFailure", caught.value)
    expected_key = _identity(view, (provider,)).identity_key()
    assert wrapped.identity_key == expected_key
    assert wrapped.provider_id == "needs-context"
    assert caught.value.__cause__ is not None
    assert provider.calls == 0


@pytest.mark.parametrize(
    "policy",
    [
        MaterializationPolicy.EPHEMERAL,
        MaterializationPolicy.PERSIST,
        MaterializationPolicy.CACHE_IF_EXPENSIVE,
    ],
)
def test_non_failure_policies_propagate_provider_error_without_wrapper_or_cycle(
    policy: MaterializationPolicy,
) -> None:
    failure = RuntimeError("direct provider failure")
    provider = _Provider(failure=failure)
    store = _MemoryStore()

    with pytest.raises(RuntimeError) as caught:
        _call(_view(), (provider,), store=store, policy=policy)

    assert caught.value is failure
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert type(caught.value).__name__ != "_ProviderExecutionError"
    assert provider.calls == 1
    assert store.put_calls == []


@pytest.mark.parametrize(
    "policy",
    [
        MaterializationPolicy.EPHEMERAL,
        MaterializationPolicy.PERSIST,
        MaterializationPolicy.CACHE_IF_EXPENSIVE,
    ],
)
def test_non_failure_policies_propagate_context_error_without_wrapper_or_cycle(
    policy: MaterializationPolicy,
) -> None:
    provider = _Provider(window=WindowSpec(3, GapPolicy.CROSS))

    with pytest.raises(InsufficientContextError) as caught:
        _call(_view(), (provider,), store=_RaisingStore(), policy=policy)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert type(caught.value).__name__ != "_ProviderExecutionError"
    assert provider.calls == 0


@pytest.mark.parametrize(
    "output",
    [
        lambda request: {"materialized.value": np.ones(request.eval_length, dtype=np.int64)},
        lambda request: {
            "materialized.value": np.full(request.eval_length, np.inf, dtype=np.float64)
        },
        lambda request: {
            "wrong.name": np.ones(request.eval_length, dtype=np.float64)
        },
    ],
    ids=["wrong-dtype", "infinity", "wrong-name"],
)
def test_invalid_provider_output_never_reaches_store_put(
    output: Callable[[FeatureRequest], object],
) -> None:
    provider = _Provider(output=output)
    store = _MemoryStore()

    with pytest.raises(FeatureFrameError):
        _call(
            _view(),
            (provider,),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert len(store.get_calls) == 1
    assert store.put_calls == []


def test_provider_failure_after_an_earlier_success_never_publishes() -> None:
    first = _Provider("first", feature_names=("first",))
    second = _Provider(
        "second",
        feature_names=("second",),
        failure=RuntimeError("second failed"),
    )
    store = _MemoryStore()

    with pytest.raises(RuntimeError, match="second failed"):
        _call(
            _view(),
            (first, second),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert (first.calls, second.calls) == (1, 1)
    assert len(store.get_calls) == 1
    assert store.put_calls == []


def test_invalid_plan_and_wrong_policy_refuse_before_store_or_compute() -> None:
    duplicate = _Provider(feature_names=("duplicate", "duplicate"))
    with pytest.raises(FeatureContractError):
        _call(
            _view(),
            (duplicate,),
            store=_RaisingStore(),
            policy=MaterializationPolicy.PERSIST,
        )
    assert duplicate.calls == 0

    valid = _Provider()
    with pytest.raises(FeatureContractError):
        _call(
            _view(),
            (valid,),
            store=_RaisingStore(),
            policy=cast("MaterializationPolicy", "persist"),
        )
    assert valid.calls == 0


@pytest.mark.parametrize("payload", [None, b""])
def test_store_error_or_malformed_hit_is_never_downgraded_to_a_miss(
    payload: bytes | None,
) -> None:
    provider = _Provider()
    store = _GetErrorStore(payload)

    with pytest.raises(TradingResearchError):
        _call(
            _view(),
            (provider,),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert provider.calls == 0
    assert store.put_calls == 0


def test_publication_failure_is_loud_and_is_not_retried() -> None:
    provider = _Provider()
    store = _PutErrorStore()

    with pytest.raises(TradingResearchError, match="publication failure"):
        _call(
            _view(),
            (provider,),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert provider.calls == 1
    assert len(store.get_calls) == 1
    assert len(store.put_calls) == 1


def test_encoding_failure_never_reaches_store_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider()
    store = _MemoryStore()

    def failed_encode(*_args: object, **_kwargs: object) -> bytes:
        raise TradingResearchError("injected encoding failure")

    monkeypatch.setattr(
        materialization_module,
        "_encode_feature_artifact",
        failed_encode,
    )

    with pytest.raises(TradingResearchError, match="encoding failure"):
        _call(
            _view(),
            (provider,),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert provider.calls == 1
    assert len(store.get_calls) == 1
    assert store.put_calls == []


class _ReadOnceProvider:
    def __init__(self, *, maximum_reads: int = 1) -> None:
        self.maximum_reads = maximum_reads
        self.reads: dict[str, int] = {}
        self.calls = 0

    def _read[T](self, name: str, value: T) -> T:
        self.reads[name] = self.reads.get(name, 0) + 1
        if self.reads[name] > self.maximum_reads:
            raise AssertionError(f"live provider declaration {name!r} was read too often")
        return value

    @property
    def provider_id(self) -> str:
        return self._read("provider_id", "read-once")

    @property
    def version(self) -> int:
        return self._read("version", 7)

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._read("feature_names", ("read.once",))

    @property
    def required_columns(self) -> tuple[str, ...]:
        return self._read("required_columns", ("close",))

    @property
    def window(self) -> WindowSpec:
        return self._read("window", WindowSpec(0, GapPolicy.CROSS))

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return self._read("params", {"seed": 3})

    @property
    def cost_hint(self) -> CostHint:
        return self._read("cost_hint", CostHint.EXPENSIVE)

    @property
    def compute(self) -> Callable[[FeatureRequest], object]:
        return self._read("compute", self._compute)

    def _compute(self, request: FeatureRequest) -> object:
        self.calls += 1
        return {"read.once": np.arange(request.eval_length, dtype=np.float64)}


class _OneShotProviders(list[object]):
    def __init__(self, values: tuple[object, ...]) -> None:
        super().__init__(values)
        self.iterations = 0

    def __iter__(self) -> Iterator[object]:
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("provider sequence was iterated more than once")
        return super().__iter__()


def test_nonpersistent_execution_reads_provider_declarations_exactly_once() -> None:
    provider = _ReadOnceProvider()

    result = _call(
        _view(),
        (provider,),
        store=_RaisingStore(),
        policy=MaterializationPolicy.EPHEMERAL,
    )

    assert result.column_names == ("read.once",)
    assert provider.calls == 1
    assert provider.reads == {
        "provider_id": 1,
        "version": 1,
        "feature_names": 1,
        "required_columns": 1,
        "window": 1,
        "params": 1,
        "cost_hint": 1,
        "compute": 1,
    }


def test_persistent_miss_revalidates_declarations_and_consumes_sequence_once() -> None:
    provider = _ReadOnceProvider(maximum_reads=2)
    providers = _OneShotProviders((provider,))
    store = _MemoryStore()

    result = _call(
        _view(),
        providers,
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )

    assert result.column_names == ("read.once",)
    assert provider.calls == 1
    assert providers.iterations == 1
    assert provider.reads == {
        "provider_id": 2,
        "version": 2,
        "feature_names": 2,
        "required_columns": 2,
        "window": 2,
        "params": 2,
        "cost_hint": 2,
        "compute": 2,
    }
    assert len(store.get_calls) == 1
    assert len(store.put_calls) == 1


class _ScaleProvider(_Provider):
    def compute(self, request: FeatureRequest) -> object:
        if self.forbid_compute:
            raise AssertionError("cache hit recomputed a provider")
        self.calls += 1
        scale = self.params["scale"]
        if type(scale) is not float:
            raise AssertionError("scale fixture must remain a float")
        return {
            name: np.full(request.eval_length, scale + index, dtype=np.float64)
            for index, name in enumerate(self.feature_names)
        }


class _MutatingMissStore(_MemoryStore):
    def __init__(self, provider: _ScaleProvider) -> None:
        super().__init__()
        self.provider = provider

    def get(self, identity_key: str) -> bytes | None:
        self.get_calls.append(identity_key)
        self.provider.params = {"scale": 99.0}
        return None


def test_store_lookup_mutation_cannot_split_identity_from_execution_snapshot() -> None:
    provider = _ScaleProvider(cost_hint=CostHint.EXPENSIVE)
    store = _MutatingMissStore(provider)

    with pytest.raises(FeatureContractError, match="declaration changed"):
        _call(
            _view(),
            (provider,),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert provider.calls == 0
    assert len(store.get_calls) == 1
    assert store.put_calls == []


def _subset_payload(
    view: BoundedView,
    provider: _Provider,
    identity: StageIdentity,
) -> bytes:
    frames = tuple(segment.frame for segment in view.segments)
    request = FeatureRequest.from_segments(
        frames,
        eval_start=_key(40),
        eval_end=_key(50),
        required_columns=provider.required_columns,
    )
    origin = FeatureOrigin(
        provider.provider_id,
        provider.version,
        provider.params,
        provider.window,
    )
    frame = _make_feature_frame(
        request=request,
        columns=OrderedDict(
            ((provider.feature_names[0], np.asarray([1.0, 2.0], dtype=np.float64)),)
        ),
        origins={provider.feature_names[0]: origin},
    )
    return _encode_feature_artifact(identity, frame)


def test_valid_same_identity_artifact_with_wrong_evaluation_axis_is_refused() -> None:
    view = _view()
    provider = _Provider()
    identity = _identity(view, (provider,))
    store = _MemoryStore()
    store.entries[identity.identity_key()] = _subset_payload(view, provider, identity)

    with pytest.raises(TradingResearchError):
        _call(
            view,
            (provider,),
            store=store,
            policy=MaterializationPolicy.PERSIST,
        )

    assert provider.calls == 0
    assert store.put_calls == []


def _invalidation_cases() -> tuple[
    tuple[
        str,
        BoundedView,
        _Provider,
        SplitPolicyIdentity,
        PlatformIdentity,
        BoundedView,
        _Provider,
        SplitPolicyIdentity,
        PlatformIdentity,
    ],
    ...,
]:
    baseline_view = _view()
    baseline_provider = _Provider()
    baseline_split = _policy()
    baseline_platform = _platform()
    return (
        (
            "git-commit",
            baseline_view,
            baseline_provider,
            baseline_split,
            baseline_platform,
            _view(),
            _Provider(),
            _policy(),
            _platform(commit="e" * 40),
        ),
        (
            "dirty-flag",
            _view(),
            _Provider(),
            _policy(),
            _platform(),
            _view(),
            _Provider(),
            _policy(),
            _platform(dirty=True, diff="e" * 64),
        ),
        (
            "diff-digest",
            _view(),
            _Provider(),
            _policy(),
            _platform(dirty=True, diff="e" * 64),
            _view(),
            _Provider(),
            _policy(),
            _platform(dirty=True, diff="f" * 64),
        ),
        (
            "provider-version",
            _view(),
            _Provider(version=1),
            _policy(),
            _platform(),
            _view(),
            _Provider(version=2),
            _policy(),
            _platform(),
        ),
        (
            "provider-param",
            _view(),
            _Provider(params={"scale": 1.0}),
            _policy(),
            _platform(),
            _view(),
            _Provider(params={"scale": -0.0}),
            _policy(),
            _platform(),
        ),
        (
            "window",
            _view(),
            _Provider(window=WindowSpec(0, GapPolicy.CROSS)),
            _policy(),
            _platform(),
            _view(),
            _Provider(window=WindowSpec(1, GapPolicy.CROSS)),
            _policy(),
            _platform(),
        ),
        (
            "eval-end-inside-gap",
            _view(eval_end=55),
            _Provider(),
            _policy(),
            _platform(),
            _view(eval_end=59),
            _Provider(),
            _policy(),
            _platform(),
        ),
        (
            "source-manifest",
            _view(),
            _Provider(),
            _policy(),
            _platform(),
            _view(evaluation_digest="9" * 64),
            _Provider(),
            _policy(),
            _platform(),
        ),
        (
            "split-policy",
            _view(),
            _Provider(),
            _policy(),
            _platform(),
            _view(),
            _Provider(),
            SplitPolicyIdentity("ADR-0012", "v1", "9" * 64),
            _platform(),
        ),
    )


@pytest.mark.parametrize(
    (
        "_label",
        "baseline_view",
        "baseline_provider",
        "baseline_split",
        "baseline_platform",
        "changed_view",
        "changed_provider",
        "changed_split",
        "changed_platform",
    ),
    _invalidation_cases(),
    ids=[case[0] for case in _invalidation_cases()],
)
def test_every_identity_input_turns_a_populated_hit_into_a_miss(
    _label: str,
    baseline_view: BoundedView,
    baseline_provider: _Provider,
    baseline_split: SplitPolicyIdentity,
    baseline_platform: PlatformIdentity,
    changed_view: BoundedView,
    changed_provider: _Provider,
    changed_split: SplitPolicyIdentity,
    changed_platform: PlatformIdentity,
) -> None:
    store = _MemoryStore()
    _call(
        baseline_view,
        (baseline_provider,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
        split_policy_identity=baseline_split,
        platform_identity=baseline_platform,
    )
    _call(
        changed_view,
        (changed_provider,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
        split_policy_identity=changed_split,
        platform_identity=changed_platform,
    )

    assert len(store.get_calls) == 2
    assert store.get_calls[0] != store.get_calls[1]
    assert len(store.put_calls) == 2
    assert baseline_provider.calls == 1
    assert changed_provider.calls == 1


def test_requested_context_role_param_order_and_cost_hint_do_not_invalidate() -> None:
    store = _MemoryStore()
    baseline = _Provider(params=OrderedDict((("z", 1), ("a", 2.0))))
    first = _call(
        _view(requested_context_start=10, resolved_context_start=10),
        (baseline,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )
    variant = _Provider(
        params=OrderedDict((("a", 2.0), ("z", 1))),
        cost_hint=CostHint.EXPENSIVE,
    )
    variant.forbid_compute = True
    second = _call(
        _view(
            context_role=ViewRole.VALIDATION,
            evaluation_role=ViewRole.TRAIN,
            requested_context_start=9,
            resolved_context_start=10,
        ),
        (variant,),
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )

    assert store.get_calls[0] == store.get_calls[1]
    assert len(store.put_calls) == 1
    _assert_frame_bits_equal(second, first)
