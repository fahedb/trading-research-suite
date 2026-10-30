"""Sparse IntentFrame validation and the absent/abstain/flat trichotomy."""

from __future__ import annotations

import json

import numpy as np
import pytest

from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    DatasetIdentity,
    DecisionPolicy,
    IntentFrame,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewSpan,
)
from trading_research.core.research_frames import (
    ModelOutputFrame,
    _make_intent_frame,
    _make_model_output_frame,
)
from trading_research.decisions import DECISION_POLICIES
from trading_research.decisions.reference import (
    AbstainPolicy,
    EventExposurePolicy,
    ThresholdPolicy,
)
from trading_research.experiments.decisions import (
    _align_intent_to_dense_axis,
    _decode_intent_frame,
    _encode_intent_frame,
    _fan_out_model_output,
    _infer_once_and_fan_out,
)

DOMAIN = ClockDomain("test.native")


def _view(*, eval_end: int = 13) -> ViewSpan:
    identity = DatasetIdentity("TRAIN_TEST", "1" * 64)

    def key(value: int) -> NativeTimeKey:
        return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)

    return ViewSpan(
        (identity,),
        TimeUnit.SECONDS,
        DOMAIN,
        key(10),
        key(10),
        key(eval_end),
    )


def _model_output(
    *,
    keys: tuple[int, ...] = (10, 12, 13),
    values: tuple[float, ...] = (-0.5, 0.0, 0.5),
    view: ViewSpan | None = None,
) -> ModelOutputFrame:
    actual_view = _view() if view is None else view
    return _make_model_output_frame(
        view=actual_view,
        keys=np.asarray(keys, dtype=np.int64),
        columns={"score": np.asarray(values, dtype=np.float64)},
        origins={"score": StageOrigin("model_v1", 1, {})},
    )


class _ObservedAbstainPolicy(AbstainPolicy):
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, model_output: ModelOutputFrame) -> IntentFrame:
        self.calls += 1
        return super().decide(model_output)


class _MissingColumnPolicy(_ObservedAbstainPolicy):
    @property
    def required_output_columns(self) -> tuple[str, ...]:
        return ("missing",)


def test_absent_abstain_and_flat_are_structurally_distinct() -> None:
    frame = _make_intent_frame(
        view=_view(),
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([10, 12, 13], dtype=np.int64),
        target_exposure=np.array([np.nan, 0.0, 0.5], dtype=np.float64),
    )

    assert 11 not in set(frame.keys.tolist())
    assert np.isnan(frame.target_exposure[0])
    assert frame.target_exposure[1] == 0.0


@pytest.mark.parametrize("value", [-1.01, 1.01, np.inf, -np.inf])
def test_target_exposure_refuses_out_of_contract_values(value: float) -> None:
    with pytest.raises(ContractViolationError):
        _make_intent_frame(
            view=_view(),
            intent_schema_id="target_exposure_intent_v1",
            keys=np.array([10], dtype=np.int64),
            target_exposure=np.array([value], dtype=np.float64),
        )


def test_unknown_intent_schema_is_refused() -> None:
    with pytest.raises(ContractViolationError, match="unknown intent_schema_id"):
        _make_intent_frame(
            view=_view(),
            intent_schema_id="unknown",
            keys=np.array([], dtype=np.int64),
            target_exposure=np.array([], dtype=np.float64),
        )


def test_accessors_return_fresh_read_only_descriptors() -> None:
    frame = _make_intent_frame(
        view=_view(),
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([10], dtype=np.int64),
        target_exposure=np.array([0.0], dtype=np.float64),
    )
    first = frame.target_exposure
    with pytest.raises(ValueError, match="read-only"):
        first[0] = 1.0
    second = frame.target_exposure
    assert first is not second
    assert second.shape == (1,)
    assert second.dtype == np.dtype(np.float64)


def test_registry_contains_exactly_the_five_reference_policies() -> None:
    assert DECISION_POLICIES.names() == {
        "adaptive_bracket_exposure_v1",
        "abstain_v1",
        "bracket_threshold_v1",
        "event_exposure_v1",
        "threshold_v1",
        "uls_joint_bracket_exposure_v1",
        "zone_adaptive_bracket_exposure_v1",
        "zone_fixed_bracket_exposure_v1",
    }
    abstain = DECISION_POLICIES.create("abstain_v1")
    event = DECISION_POLICIES.create("event_exposure_v1")
    threshold = DECISION_POLICIES.create(
        "threshold_v1",
        output_column="score",
        threshold=0.25,
    )
    assert isinstance(abstain, DecisionPolicy)
    assert isinstance(event, DecisionPolicy)
    assert isinstance(threshold, DecisionPolicy)
    assert event.params == {}
    assert threshold.params == {"output_column": "score", "threshold": 0.25}


@pytest.mark.parametrize("threshold", [-0.1, np.nan, np.inf, 0, True])
def test_threshold_configuration_is_explicit_and_finite(threshold: object) -> None:
    with pytest.raises(ContractViolationError, match="finite non-negative"):
        ThresholdPolicy(
            output_column="score",
            threshold=threshold,  # type: ignore[arg-type]
        )


def test_threshold_policy_maps_generic_values_and_preserves_nan() -> None:
    view = _view(eval_end=15)
    output = _model_output(
        view=view,
        keys=(10, 11, 12, 13, 14, 15),
        values=(-0.5, -0.25, 0.0, 0.25, 0.5, np.nan),
    )
    intent = _fan_out_model_output(
        model_output=output,
        policies=(ThresholdPolicy(output_column="score", threshold=0.25),),
    )[0]

    np.testing.assert_array_equal(intent.keys, output.keys)
    np.testing.assert_array_equal(
        intent.target_exposure,
        np.array([-1.0, 0.0, 0.0, 0.0, 1.0, np.nan]),
    )


def test_abstain_policy_preserves_sparse_presence() -> None:
    output = _model_output()
    intent = _fan_out_model_output(
        model_output=output,
        policies=(AbstainPolicy(),),
    )[0]

    np.testing.assert_array_equal(intent.keys, np.array([10, 12, 13]))
    assert np.isnan(intent.target_exposure).all()
    assert 11 not in set(intent.keys.tolist())


def test_event_exposure_is_sparse_and_preserves_the_input_trichotomy() -> None:
    view = _view(eval_end=16)
    output = _model_output(
        view=view,
        keys=(10, 12, 13, 14, 15, 16),
        values=(np.nan, 0.0, -1.0, np.nan, 1.0, 0.0),
    )
    policy = EventExposurePolicy()
    assert (
        policy.policy_id,
        policy.version,
        policy.params,
        policy.required_output_columns,
        policy.intent_schema_id,
    ) == (
        "event_exposure_v1",
        1,
        {},
        ("score",),
        "target_exposure_intent_v1",
    )

    intent = _fan_out_model_output(model_output=output, policies=(policy,))[0]

    np.testing.assert_array_equal(intent.keys, np.array([13, 15], dtype=np.int64))
    np.testing.assert_array_equal(
        intent.target_exposure,
        np.array([-1.0, 1.0], dtype=np.float64),
    )
    assert np.all(intent.keys[1:] > intent.keys[:-1])
    assert set(intent.keys.tolist()) < set(output.keys.tolist())
    assert 11 not in set(output.keys.tolist())
    assert np.isnan(output.column("score")[0])
    assert output.column("score")[1] == 0.0
    assert 10 not in set(intent.keys.tolist())
    assert 12 not in set(intent.keys.tolist())


def test_event_exposure_emits_one_intent_per_sparse_m5_event_not_per_m1_row() -> None:
    view = _view(eval_end=19)
    values = np.full(10, np.nan, dtype=np.float64)
    values[0] = 1.0
    values[5] = -1.0
    output = _model_output(
        view=view,
        keys=tuple(range(10, 20)),
        values=tuple(values.tolist()),
    )

    intent = EventExposurePolicy().decide(output)

    np.testing.assert_array_equal(intent.keys, np.array([10, 15], dtype=np.int64))
    np.testing.assert_array_equal(intent.target_exposure, np.array([1.0, -1.0]))


def test_all_policy_compatibility_is_checked_before_any_policy_executes() -> None:
    first = _ObservedAbstainPolicy()
    incompatible = _MissingColumnPolicy()

    with pytest.raises(ContractViolationError, match="missing model output"):
        _fan_out_model_output(
            model_output=_model_output(),
            policies=(first, incompatible),
        )

    assert first.calls == 0
    assert incompatible.calls == 0


def test_n_policy_fan_out_performs_one_inference_and_zero_reinference() -> None:
    inference_calls = 0
    observed = _ObservedAbstainPolicy()

    def infer() -> ModelOutputFrame:
        nonlocal inference_calls
        inference_calls += 1
        return _model_output()

    result = _infer_once_and_fan_out(
        inference=infer,
        output_column_names=("score",),
        policies=(
            ThresholdPolicy(output_column="score", threshold=0.25),
            observed,
            AbstainPolicy(),
        ),
    )

    assert inference_calls == 1
    assert observed.calls == 1
    assert len(result._intents) == 3
    assert result._counters._model_inference_calls == 1
    assert result._counters._model_reinference_calls == 0
    assert result._counters._decision_policy_calls == 3


def test_incompatible_policy_refuses_before_inference() -> None:
    inference_calls = 0

    def infer() -> ModelOutputFrame:
        nonlocal inference_calls
        inference_calls += 1
        return _model_output()

    with pytest.raises(ContractViolationError, match="missing model output"):
        _infer_once_and_fan_out(
            inference=infer,
            output_column_names=("score",),
            policies=(_MissingColumnPolicy(),),
        )
    assert inference_calls == 0


def test_canonical_round_trip_preserves_absent_abstain_and_flat_bits() -> None:
    view = _view()
    frame = _make_intent_frame(
        view=view,
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([10, 12, 13], dtype=np.int64),
        target_exposure=np.array([np.nan, 0.0, 0.5], dtype=np.float64),
    )

    payload = _encode_intent_frame(frame)
    restored = _decode_intent_frame(payload, view=view)

    assert payload == _encode_intent_frame(restored)
    np.testing.assert_array_equal(restored.keys, frame.keys)
    np.testing.assert_array_equal(
        restored.target_exposure.view(np.uint64),
        frame.target_exposure.view(np.uint64),
    )
    assert 11 not in set(restored.keys.tolist())
    assert np.isnan(restored.target_exposure[0])
    assert restored.target_exposure[1] == 0.0


def test_intent_decoder_refuses_noncanonical_and_duplicate_json() -> None:
    frame = _make_intent_frame(
        view=_view(),
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([10], dtype=np.int64),
        target_exposure=np.array([0.0], dtype=np.float64),
    )
    canonical = _encode_intent_frame(frame)
    document = json.loads(canonical)
    noncanonical = json.dumps(document, sort_keys=True).encode("ascii")
    with pytest.raises(ContractViolationError, match="canonically encoded"):
        _decode_intent_frame(noncanonical, view=_view())

    duplicate = canonical.replace(
        b'{"clock_domain":',
        b'{"clock_domain":"duplicate","clock_domain":',
        1,
    )
    with pytest.raises(ContractViolationError, match="repeats field"):
        _decode_intent_frame(duplicate, view=_view())


def test_sparse_consumer_alignment_keeps_presence_separate_from_nan() -> None:
    view = _view()
    dense_keys = np.array([10, 11, 12, 13], dtype=np.int64)
    sparse = _make_intent_frame(
        view=view,
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([10, 12, 13], dtype=np.int64),
        target_exposure=np.array([np.nan, 0.0, 0.5], dtype=np.float64),
    )

    aligned = _align_intent_to_dense_axis(
        view=view,
        intent=sparse,
        dense_keys=dense_keys,
    )

    np.testing.assert_array_equal(aligned._present, [True, False, True, True])
    assert np.isnan(aligned._target_exposure[0])
    assert np.isnan(aligned._target_exposure[1])
    assert aligned._target_exposure[2] == 0.0
    assert aligned._target_exposure[3] == 0.5
    assert not aligned._present.flags.writeable
    assert not aligned._target_exposure.flags.writeable


def test_sparse_consumer_refuses_an_intent_key_outside_its_dense_axis() -> None:
    sparse = _make_intent_frame(
        view=_view(),
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([11], dtype=np.int64),
        target_exposure=np.array([0.0], dtype=np.float64),
    )
    with pytest.raises(ContractViolationError, match="subset of the dense axis"):
        _align_intent_to_dense_axis(
            view=_view(),
            intent=sparse,
            dense_keys=np.array([10, 12, 13], dtype=np.int64),
        )


def test_empty_sparse_model_output_can_fan_out_without_fabricated_bounds() -> None:
    output = _model_output(keys=(), values=())
    intents = _fan_out_model_output(
        model_output=output,
        policies=(
            ThresholdPolicy(output_column="score", threshold=0.0),
            AbstainPolicy(),
        ),
    )
    assert all(intent.num_rows == 0 for intent in intents)
