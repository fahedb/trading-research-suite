"""Slice 2G cache-bypassing exact feature determinism qualification."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import OrderedDict
from dataclasses import FrozenInstanceError, fields
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

import trading_research.artifacts.determinism as determinism_module
from trading_research.artifacts import (
    ArtifactStore,
    DeterminismReport,
    MaterializationPolicy,
    PlatformIdentity,
    StageIdentity,
    verify_determinism,
)
from trading_research.artifacts.container import _encode_feature_artifact
from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    CostHint,
    DatasetIdentity,
    DatasetRef,
    FeatureFrame,
    FeatureOrigin,
    FeatureProvider,
    FeatureRequest,
    GapPolicy,
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
from trading_research.data.views import BoundedSegment, BoundedView, ContextResolution
from trading_research.experiments.features import materialize_features

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    import numpy.typing as npt

_DOMAIN = ClockDomain("feature-determinism.wallclock")
_SOURCES = (
    DatasetIdentity("DETERMINISM_CONTEXT", "a" * 64),
    DatasetIdentity("DETERMINISM_EVALUATION", "b" * 64),
)
_SPLIT = SplitPolicyIdentity("ADR-0012", "v1", "c" * 64)
_PLATFORM = PlatformIdentity("d" * 40, False, None)
_WINDOW = WindowSpec(0, GapPolicy.CROSS)


def _key(
    value: int,
    *,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
) -> NativeTimeKey:
    return NativeTimeKey(value, unit, domain)


def _market_frame(
    keys: Sequence[int],
    *,
    source: DatasetIdentity,
    role: ViewRole,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
) -> MarketFrame:
    key_array = np.asarray(keys, dtype=np.int64)
    return _make_market_frame(
        dataset_ref=DatasetRef(source.dataset_id, source.manifest_sha256, role),
        clock_domain=domain,
        unit=unit,
        schema={"close": "float64"},
        keys=key_array,
        columns={"close": np.arange(key_array.size, dtype=np.float64)},
        admitted_lo=int(key_array[0]),
        admitted_hi=int(key_array[-1]),
    )


def _view() -> BoundedView:
    context_frame = _market_frame(
        (10, 20),
        source=_SOURCES[0],
        role=ViewRole.TRAIN,
    )
    evaluation_frame = _market_frame(
        (30, 40, 50),
        source=_SOURCES[1],
        role=ViewRole.VALIDATION,
    )
    context = ContextResolution._resolved(
        requested_context_start=_key(10),
        resolved_context_start=_key(10),
        shortfall=None,
    )
    return BoundedView._from_bounded(
        segments=(
            BoundedSegment._from_bounded(context_frame),
            BoundedSegment._from_bounded(evaluation_frame),
        ),
        eval_frame=evaluation_frame,
        context=context,
        context_start=_key(10),
        eval_start=_key(30),
        eval_end=_key(50),
    )


class _Provider:
    provider_id = "deterministic"
    version = 1
    feature_names = ("deterministic.value",)
    required_columns = ("close",)
    window = _WINDOW
    params: Mapping[str, ParamValue] = {"scale": 1.0}
    cost_hint = CostHint.CHEAP

    def __init__(self, outputs: Sequence[npt.NDArray[np.float64]]) -> None:
        self._outputs = tuple(np.asarray(output, dtype=np.float64).copy() for output in outputs)
        if not self._outputs:
            raise AssertionError("provider fixture requires an output")
        self.calls = 0
        self.forbid_compute = False

    def compute(self, request: FeatureRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        if self.forbid_compute:
            raise AssertionError("poisoned cache hit recomputed the provider")
        output = self._outputs[self.calls % len(self._outputs)]
        self.calls += 1
        if output.shape != (request.eval_length,):
            raise AssertionError("provider fixture output has the wrong evaluation length")
        return {self.feature_names[0]: output.copy()}


def _origin(
    *,
    provider_id: str = _Provider.provider_id,
    version: int = _Provider.version,
    params: Mapping[str, ParamValue] | None = None,
    window: WindowSpec = _WINDOW,
) -> FeatureOrigin:
    return FeatureOrigin(
        provider_id,
        version,
        {"scale": 1.0} if params is None else params,
        window,
    )


def _identity(view: BoundedView, provider: _Provider) -> StageIdentity:
    return StageIdentity(
        source_identities=tuple(
            segment.dataset_ref.canonical_identity for segment in view.segments
        ),
        resolved_context_start=view.context.resolved_context_start,
        eval_start=view.eval_start,
        eval_end=view.eval_end,
        split_policy_adr=_SPLIT.adr,
        split_policy_version=_SPLIT.policy_version,
        split_policy_sha256=_SPLIT.policy_sha256,
        providers=cast("Sequence[FeatureProvider]", (provider,)),
        platform_identity=_PLATFORM,
    )


def _materialize(
    view: BoundedView,
    provider: _Provider,
    *,
    store: object,
    policy: MaterializationPolicy,
) -> FeatureFrame:
    return materialize_features(
        view,
        (provider,),
        split_policy_identity=_SPLIT,
        platform_identity=_PLATFORM,
        store=cast("ArtifactStore", store),
        policy=policy,
    )


def _feature_frame(
    columns: Mapping[str, npt.NDArray[np.float64]],
    *,
    eval_keys: Sequence[int] = (30, 40, 50),
    sources: tuple[DatasetIdentity, DatasetIdentity] = _SOURCES,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
    origins: Mapping[str, FeatureOrigin] | None = None,
) -> FeatureFrame:
    frames = (
        _market_frame(
            (10, 20),
            source=sources[0],
            role=ViewRole.TRAIN,
            unit=unit,
            domain=domain,
        ),
        _market_frame(
            eval_keys,
            source=sources[1],
            role=ViewRole.VALIDATION,
            unit=unit,
            domain=domain,
        ),
    )
    request = FeatureRequest.from_segments(
        frames,
        eval_start=_key(int(eval_keys[0]), unit=unit, domain=domain),
        eval_end=_key(int(eval_keys[-1]), unit=unit, domain=domain),
        required_columns=("close",),
    )
    resolved_origins = (
        OrderedDict((name, _origin()) for name in columns)
        if origins is None
        else origins
    )
    return _make_feature_frame(
        request=request,
        columns=columns,
        origins=resolved_origins,
    )


def _bits(values: npt.NDArray[np.float64]) -> tuple[int, ...]:
    return tuple(int(value) for value in values.view(np.uint64))


def _inventory(root: Path) -> tuple[tuple[str, bytes | None], ...]:
    entries: list[tuple[str, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        entries.append(
            (
                path.relative_to(root).as_posix(),
                path.read_bytes() if path.is_file() else None,
            )
        )
    return tuple(entries)


class _RaisingStore:
    def get(self, identity_key: str) -> bytes | None:
        raise AssertionError(f"verification read cache key {identity_key}")

    def put(self, identity_key: str, payload: bytes) -> str:
        raise AssertionError(
            f"verification wrote cache key {identity_key} ({len(payload)} bytes)"
        )


class _ArmableStore:
    def __init__(self, delegate: ArtifactStore) -> None:
        self.delegate = delegate
        self.armed = False
        self.get_calls = 0
        self.put_calls = 0

    def get(self, identity_key: str) -> bytes | None:
        if self.armed:
            raise AssertionError(f"verification read poisoned cache key {identity_key}")
        self.get_calls += 1
        return self.delegate.get(identity_key)

    def put(self, identity_key: str, payload: bytes) -> str:
        if self.armed:
            raise AssertionError(f"verification wrote poisoned cache key {identity_key}")
        self.put_calls += 1
        return self.delegate.put(identity_key, payload)


def test_report_surface_is_exact_frozen_and_internally_consistent() -> None:
    assert tuple(field.name for field in fields(DeterminismReport)) == (
        "identity_key",
        "first_content_hash",
        "second_content_hash",
        "structurally_equal",
        "deterministic",
    )
    report = DeterminismReport("a" * 64, "b" * 64, "b" * 64, True, True)
    with pytest.raises(FrozenInstanceError):
        report.deterministic = False  # type: ignore[misc]
    assert not hasattr(report, "__dict__")

    with pytest.raises(ContractViolationError):
        DeterminismReport("a" * 64, "b" * 64, "c" * 64, True, True)
    with pytest.raises(ContractViolationError):
        DeterminismReport("not-a-key", "b" * 64, "b" * 64, True, True)


def test_stable_recomputation_is_exact_with_nan_payload_and_signed_zero() -> None:
    view = _view()
    raw_bits = np.asarray(
        [0x0000000000000000, 0x8000000000000000, 0x7FF8000000000042],
        dtype=np.uint64,
    )
    values = raw_bits.view(np.float64).copy()
    provider = _Provider((values,))
    identity = _identity(view, provider)
    policies: list[MaterializationPolicy] = []
    frames_seen: list[FeatureFrame] = []

    def recompute(policy: MaterializationPolicy) -> FeatureFrame:
        policies.append(policy)
        frame = _materialize(view, provider, store=_RaisingStore(), policy=policy)
        frames_seen.append(frame)
        return frame

    report = verify_determinism(identity, recompute)
    expected_frame = _feature_frame(
        OrderedDict(((provider.feature_names[0], values),))
    )
    expected_payload = _encode_feature_artifact(identity, expected_frame)
    expected_hash = hashlib.sha256(expected_payload).hexdigest()

    assert policies == [MaterializationPolicy.EPHEMERAL] * 2
    assert provider.calls == 2
    assert len(frames_seen) == 2
    assert frames_seen[0] is not frames_seen[1]
    assert all(
        _bits(frame.column(provider.feature_names[0])) == tuple(raw_bits)
        for frame in frames_seen
    )
    assert report == DeterminismReport(
        identity.identity_key(),
        expected_hash,
        expected_hash,
        True,
        True,
    )


def test_poisoned_normal_hit_is_wrong_but_verification_is_correct_and_cache_free(
    tmp_path: Path,
) -> None:
    view = _view()
    correct = np.asarray([1.0, 2.0, 3.0], dtype=np.float64)
    poisoned = np.asarray([91.0, 92.0, 93.0], dtype=np.float64)
    provider = _Provider((correct,))
    identity = _identity(view, provider)
    real_store = ArtifactStore(tmp_path)
    poison_frame = _feature_frame(
        OrderedDict(((provider.feature_names[0], poisoned),))
    )
    poison_payload = _encode_feature_artifact(identity, poison_frame)
    poison_hash = real_store.put(identity.identity_key(), poison_payload)
    store = _ArmableStore(real_store)

    provider.forbid_compute = True
    normal = _materialize(
        view,
        provider,
        store=store,
        policy=MaterializationPolicy.PERSIST,
    )
    provider.forbid_compute = False
    assert provider.calls == 0
    assert _bits(normal.column(provider.feature_names[0])) == _bits(poisoned)
    assert _bits(normal.column(provider.feature_names[0])) != _bits(correct)

    before = _inventory(tmp_path)
    store.armed = True
    policies: list[MaterializationPolicy] = []
    verified_frames: list[FeatureFrame] = []

    def recompute(policy: MaterializationPolicy) -> FeatureFrame:
        policies.append(policy)
        frame = _materialize(view, provider, store=store, policy=policy)
        verified_frames.append(frame)
        return frame

    report = verify_determinism(identity, recompute)
    after = _inventory(tmp_path)
    expected_frame = _feature_frame(
        OrderedDict(((provider.feature_names[0], correct),))
    )
    expected_payload = _encode_feature_artifact(identity, expected_frame)
    expected_hash = hashlib.sha256(expected_payload).hexdigest()

    assert policies == [MaterializationPolicy.EPHEMERAL] * 2
    assert provider.calls == 2
    assert len(verified_frames) == 2
    assert all(
        _bits(frame.column(provider.feature_names[0])) == _bits(correct)
        for frame in verified_frames
    )
    assert report.identity_key == identity.identity_key()
    assert (report.first_content_hash, report.second_content_hash) == (
        expected_hash,
        expected_hash,
    )
    assert expected_hash != poison_hash
    assert report.structurally_equal is True
    assert report.deterministic is True
    assert store.get_calls == 1
    assert store.put_calls == 0
    assert after == before


def test_raising_store_proves_both_verification_runs_are_ephemeral() -> None:
    view = _view()
    provider = _Provider((np.asarray([4.0, 5.0, 6.0], dtype=np.float64),))
    identity = _identity(view, provider)
    policies: list[MaterializationPolicy] = []

    def recompute(policy: MaterializationPolicy) -> FeatureFrame:
        policies.append(policy)
        return _materialize(view, provider, store=_RaisingStore(), policy=policy)

    report = verify_determinism(identity, recompute)

    assert policies == [MaterializationPolicy.EPHEMERAL] * 2
    assert provider.calls == 2
    assert report.structurally_equal is True
    assert report.deterministic is True


def test_deterministic_alternating_provider_is_detected_without_randomness() -> None:
    view = _view()
    first = np.asarray([1.0, 2.0, 3.0], dtype=np.float64)
    second = np.asarray([1.0, 2.0, 4.0], dtype=np.float64)
    provider = _Provider((first, second))
    identity = _identity(view, provider)

    report = verify_determinism(
        identity,
        lambda policy: _materialize(view, provider, store=_RaisingStore(), policy=policy),
    )
    expected_first = hashlib.sha256(
        _encode_feature_artifact(
            identity,
            _feature_frame(OrderedDict(((provider.feature_names[0], first),))),
        )
    ).hexdigest()
    expected_second = hashlib.sha256(
        _encode_feature_artifact(
            identity,
            _feature_frame(OrderedDict(((provider.feature_names[0], second),))),
        )
    ).hexdigest()

    assert provider.calls == 2
    assert report.first_content_hash == expected_first
    assert report.second_content_hash == expected_second
    assert expected_first != expected_second
    assert report.structurally_equal is True
    assert report.deterministic is False


@pytest.mark.parametrize(
    ("first_bits", "second_bits"),
    [
        (
            (0x0000000000000000, 0x3FF0000000000000, 0x4000000000000000),
            (0x8000000000000000, 0x3FF0000000000000, 0x4000000000000000),
        ),
        (
            (0x7FF8000000000042, 0x3FF0000000000000, 0x4000000000000000),
            (0x7FF8000000000043, 0x3FF0000000000000, 0x4000000000000000),
        ),
    ],
    ids=["signed-zero", "nan-payload"],
)
def test_value_comparison_is_raw_byte_exact_not_numeric(
    first_bits: tuple[int, int, int],
    second_bits: tuple[int, int, int],
) -> None:
    view = _view()
    first = np.asarray(first_bits, dtype=np.uint64).view(np.float64).copy()
    second = np.asarray(second_bits, dtype=np.uint64).view(np.float64).copy()
    provider = _Provider((first, second))

    report = verify_determinism(
        _identity(view, provider),
        lambda policy: _materialize(view, provider, store=_RaisingStore(), policy=policy),
    )

    assert report.structurally_equal is True
    assert report.first_content_hash != report.second_content_hash
    assert report.deterministic is False


def test_nan_position_and_key_axis_are_structural_not_toleranced() -> None:
    view = _view()
    provider = _Provider((np.asarray([1.0, 2.0, 3.0], dtype=np.float64),))
    identity = _identity(view, provider)
    nan_left = _feature_frame(
        OrderedDict(((provider.feature_names[0], np.asarray([np.nan, 2.0, 3.0])),))
    )
    nan_right = _feature_frame(
        OrderedDict(((provider.feature_names[0], np.asarray([1.0, np.nan, 3.0])),))
    )
    frames = iter((nan_left, nan_right))
    nan_report = verify_determinism(identity, lambda _policy: next(frames))
    assert nan_report.structurally_equal is False
    assert nan_report.deterministic is False

    full = _feature_frame(
        OrderedDict(((provider.feature_names[0], np.asarray([1.0, 2.0, 3.0])),))
    )
    sparse = _feature_frame(
        OrderedDict(((provider.feature_names[0], np.asarray([1.0, 3.0])),)),
        eval_keys=(30, 50),
    )
    frames = iter((full, sparse))
    key_report = verify_determinism(identity, lambda _policy: next(frames))
    assert key_report.structurally_equal is False
    assert key_report.deterministic is False


def _structural_variant(label: str) -> FeatureFrame:
    columns = OrderedDict(
        (
            ("deterministic.first", np.asarray([1.0, np.nan, 3.0], dtype=np.float64)),
            ("deterministic.second", np.asarray([4.0, 5.0, 6.0], dtype=np.float64)),
        )
    )
    origins: Mapping[str, FeatureOrigin] | None = None
    if label == "keys":
        return _feature_frame(
            OrderedDict((name, values[[0, 2]]) for name, values in columns.items()),
            eval_keys=(30, 50),
        )
    if label == "unit":
        return _feature_frame(columns, unit=TimeUnit.MILLISECONDS)
    if label == "clock-domain":
        return _feature_frame(columns, domain=ClockDomain("other.wallclock"))
    if label == "sources":
        return _feature_frame(
            columns,
            sources=(DatasetIdentity("OTHER", "e" * 64), _SOURCES[1]),
        )
    if label == "source-order":
        return _feature_frame(columns, sources=(_SOURCES[1], _SOURCES[0]))
    if label == "column-set":
        return _feature_frame(
            OrderedDict((("deterministic.first", columns["deterministic.first"]),))
        )
    if label == "column-order":
        return _feature_frame(OrderedDict(reversed(tuple(columns.items()))))
    if label == "nan-position":
        changed = OrderedDict(columns)
        changed["deterministic.first"] = np.asarray([1.0, 2.0, np.nan])
        return _feature_frame(changed)
    if label == "origin-provider":
        origins = OrderedDict((name, _origin(provider_id="other")) for name in columns)
    elif label == "origin-version":
        origins = OrderedDict((name, _origin(version=2)) for name in columns)
    elif label == "origin-params":
        origins = OrderedDict((name, _origin(params={"scale": -0.0})) for name in columns)
    elif label == "origin-window":
        origins = OrderedDict(
            (name, _origin(window=WindowSpec(1, GapPolicy.CROSS))) for name in columns
        )
    else:
        raise AssertionError(f"unknown structural variant {label}")
    return _feature_frame(columns, origins=origins)


@pytest.mark.parametrize(
    "label",
    [
        "keys",
        "unit",
        "clock-domain",
        "sources",
        "source-order",
        "column-set",
        "column-order",
        "nan-position",
        "origin-provider",
        "origin-version",
        "origin-params",
        "origin-window",
    ],
)
def test_private_structural_comparator_covers_every_live_frame_dimension(label: str) -> None:
    baseline = _feature_frame(
        OrderedDict(
            (
                ("deterministic.first", np.asarray([1.0, np.nan, 3.0])),
                ("deterministic.second", np.asarray([4.0, 5.0, 6.0])),
            )
        ),
    )
    variant = _structural_variant(label)
    assert determinism_module._feature_frames_structurally_equal(baseline, variant) is False


def test_structural_comparator_excludes_finite_bits_and_nan_payload_bits() -> None:
    left_bits = np.asarray(
        [0x0000000000000000, 0x7FF8000000000042, 0x3FF0000000000000],
        dtype=np.uint64,
    )
    right_bits = np.asarray(
        [0x8000000000000000, 0x7FF8000000000043, 0x4000000000000000],
        dtype=np.uint64,
    )
    left = _feature_frame(
        OrderedDict((("deterministic.value", left_bits.view(np.float64).copy()),))
    )
    right = _feature_frame(
        OrderedDict((("deterministic.value", right_bits.view(np.float64).copy()),))
    )

    assert determinism_module._feature_frames_structurally_equal(left, right) is True


@pytest.mark.parametrize(
    ("left_param", "right_param"),
    [(0.0, -0.0), (1, True)],
    ids=["signed-zero-origin", "int-versus-bool-origin"],
)
def test_structural_origin_params_use_canonical_type_and_bit_equality(
    left_param: ParamValue,
    right_param: ParamValue,
) -> None:
    columns = OrderedDict(
        (("deterministic.value", np.asarray([1.0, 2.0, 3.0], dtype=np.float64)),)
    )
    left = _feature_frame(
        columns,
        origins={"deterministic.value": _origin(params={"value": left_param})},
    )
    right = _feature_frame(
        columns,
        origins={"deterministic.value": _origin(params={"value": right_param})},
    )

    assert determinism_module._feature_frames_structurally_equal(left, right) is False


@pytest.mark.parametrize("fail_on", [1, 2])
def test_callback_failure_propagates_the_original_exception(fail_on: int) -> None:
    view = _view()
    provider = _Provider((np.asarray([1.0, 2.0, 3.0], dtype=np.float64),))
    identity = _identity(view, provider)
    frame = _feature_frame(
        OrderedDict(((provider.feature_names[0], np.asarray([1.0, 2.0, 3.0])),))
    )
    failure = RuntimeError(f"failure on recomputation {fail_on}")
    calls = 0

    def recompute(_policy: MaterializationPolicy) -> FeatureFrame:
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise failure
        return frame

    with pytest.raises(RuntimeError) as caught:
        verify_determinism(identity, recompute)

    assert caught.value is failure
    assert calls == fail_on


def test_invalid_identity_callback_result_and_frame_binding_refuse_loudly() -> None:
    view = _view()
    provider = _Provider((np.asarray([1.0, 2.0, 3.0], dtype=np.float64),))
    identity = _identity(view, provider)
    callback_calls = 0

    def valid_callback(_policy: MaterializationPolicy) -> FeatureFrame:
        nonlocal callback_calls
        callback_calls += 1
        return _feature_frame(
            OrderedDict(((provider.feature_names[0], np.asarray([1.0, 2.0, 3.0])),))
        )

    with pytest.raises(ContractViolationError):
        verify_determinism(cast("StageIdentity", object()), valid_callback)
    assert callback_calls == 0
    with pytest.raises(ContractViolationError):
        verify_determinism(identity, cast("Callable[[MaterializationPolicy], FeatureFrame]", 7))
    assert callback_calls == 0

    wrong_calls = 0

    def wrong_result(_policy: MaterializationPolicy) -> FeatureFrame:
        nonlocal wrong_calls
        wrong_calls += 1
        return cast("FeatureFrame", object())

    with pytest.raises(TradingResearchError):
        verify_determinism(identity, wrong_result)
    assert wrong_calls == 1

    mismatch_calls = 0

    def mismatched_frame(_policy: MaterializationPolicy) -> FeatureFrame:
        nonlocal mismatch_calls
        mismatch_calls += 1
        return _feature_frame(
            OrderedDict(((provider.feature_names[0], np.asarray([1.0, 2.0, 3.0])),)),
            origins={provider.feature_names[0]: _origin(version=99)},
        )

    with pytest.raises(TradingResearchError):
        verify_determinism(identity, mismatched_frame)
    assert mismatch_calls == 1


def test_normal_materialization_still_computes_only_once() -> None:
    provider = _Provider((np.asarray([1.0, 2.0, 3.0], dtype=np.float64),))
    result = _materialize(
        _view(),
        provider,
        store=_RaisingStore(),
        policy=MaterializationPolicy.EPHEMERAL,
    )

    assert result.column_names == provider.feature_names
    assert provider.calls == 1


_SUBPROCESS_PROGRAM = r"""
import json
import numpy as np
from trading_research.artifacts import (
    MaterializationPolicy, PlatformIdentity, StageIdentity, verify_determinism,
)
from trading_research.core import (
    ClockDomain, CostHint, DatasetRef, FeatureRequest, GapPolicy,
    NativeTimeKey, TimeUnit, ViewRole, WindowSpec,
)
from trading_research.core.frames import _make_market_frame
from trading_research.data import SplitPolicyIdentity
from trading_research.data.views import BoundedSegment, BoundedView, ContextResolution
from trading_research.experiments.features import materialize_features

domain = ClockDomain("feature-determinism.wallclock")
def key(value):
    return NativeTimeKey(value, TimeUnit.SECONDS, domain)
def frame(keys, dataset_id, digest, role):
    axis = np.asarray(keys, dtype=np.int64)
    return _make_market_frame(
        dataset_ref=DatasetRef(dataset_id, digest, role), clock_domain=domain,
        unit=TimeUnit.SECONDS, schema={"close": "float64"}, keys=axis,
        columns={"close": np.arange(axis.size, dtype=np.float64)},
        admitted_lo=int(axis[0]), admitted_hi=int(axis[-1]),
    )
context_frame = frame((10, 20), "DETERMINISM_CONTEXT", "a" * 64, ViewRole.TRAIN)
evaluation_frame = frame(
    (30, 40, 50), "DETERMINISM_EVALUATION", "b" * 64, ViewRole.VALIDATION,
)
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
    provider_id = "deterministic"
    version = 1
    feature_names = ("deterministic.value",)
    required_columns = ("close",)
    window = WindowSpec(0, GapPolicy.CROSS)
    params = {"scale": 1.0}
    cost_hint = CostHint.CHEAP
    def __init__(self):
        self.calls = 0
    def compute(self, request):
        self.calls += 1
        return {self.feature_names[0]: np.asarray([1.0, 2.0, 3.0], dtype=np.float64)}
class RaisingStore:
    def get(self, identity_key):
        raise AssertionError("verification read " + identity_key)
    def put(self, identity_key, payload):
        raise AssertionError("verification wrote " + identity_key)
provider = Provider()
split = SplitPolicyIdentity("ADR-0012", "v1", "c" * 64)
platform = PlatformIdentity("d" * 40, False, None)
identity = StageIdentity(
    source_identities=tuple(segment.dataset_ref.canonical_identity for segment in view.segments),
    resolved_context_start=view.context.resolved_context_start,
    eval_start=view.eval_start, eval_end=view.eval_end,
    split_policy_adr=split.adr, split_policy_version=split.policy_version,
    split_policy_sha256=split.policy_sha256, providers=(provider,),
    platform_identity=platform,
)
policies = []
def recompute(policy):
    policies.append(policy.value)
    return materialize_features(
        view, (provider,), split_policy_identity=split, platform_identity=platform,
        store=RaisingStore(), policy=policy,
    )
report = verify_determinism(identity, recompute)
print(json.dumps({
    "identity_key": report.identity_key,
    "first_content_hash": report.first_content_hash,
    "second_content_hash": report.second_content_hash,
    "structurally_equal": report.structurally_equal,
    "deterministic": report.deterministic,
    "policies": policies,
    "calls": provider.calls,
}, sort_keys=True))
"""


def _run_subprocess(*, cwd: Path, hash_seed: str) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = hash_seed
    completed = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_PROGRAM],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return cast("dict[str, object]", json.loads(completed.stdout))


def test_fresh_process_recomputation_is_stable_across_hash_seeds_and_cwd(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    first = _run_subprocess(cwd=left, hash_seed="1")
    second = _run_subprocess(cwd=right, hash_seed="987654")

    assert first == second
    assert first["policies"] == ["ephemeral", "ephemeral"]
    assert first["calls"] == 2
    assert first["structurally_equal"] is True
    assert first["deterministic"] is True
    assert first["first_content_hash"] == first["second_content_hash"]
    assert first["identity_key"] == _identity(
        _view(),
        _Provider((np.asarray([1.0, 2.0, 3.0], dtype=np.float64),)),
    ).identity_key()
