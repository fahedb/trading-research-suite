"""Construction, sparsity, namespace, and immutability of research frames."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    DatasetIdentity,
    FeatureFrame,
    FeatureOrigin,
    GapPolicy,
    ModelInputs,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewSpan,
    WindowSpec,
)
from trading_research.core.feature_frames import _restore_feature_frame
from trading_research.core.research_frames import (
    FittedPayload,
    FittedState,
    IntentFrame,
    ModelOutputFrame,
    OutcomeFrame,
    RepresentationFrame,
    TrainingTargetFrame,
    _make_fitted_state,
    _make_model_output_frame,
    _make_outcome_frame,
    _make_representation_frame,
    _make_training_target_frame,
)

if TYPE_CHECKING:
    import numpy.typing as npt

DOMAIN = ClockDomain("test.native")
IDENTITY = DatasetIdentity("TRAIN_TEST", "1" * 64)
FEATURE_ORIGIN = FeatureOrigin(
    "feature",
    1,
    {},
    WindowSpec(max_prior_rows=0, gap_policy=GapPolicy.CROSS),
)
STAGE_ORIGIN = StageOrigin("stage", 1, {})


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _view() -> ViewSpan:
    return ViewSpan((IDENTITY,), TimeUnit.SECONDS, DOMAIN, _key(10), _key(10), _key(13))


def _features(keys: npt.NDArray[np.int64] | None = None) -> FeatureFrame:
    actual = np.array([10, 11, 12, 13], dtype=np.int64) if keys is None else keys
    return _restore_feature_frame(
        keys=actual,
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(IDENTITY,),
        columns={"x": np.arange(actual.size, dtype=np.float64)},
        origins={"x": FEATURE_ORIGIN},
    )


@pytest.mark.parametrize(
    "frame_type", [RepresentationFrame, ModelOutputFrame, TrainingTargetFrame, OutcomeFrame]
)
def test_research_frames_refuse_direct_construction(frame_type: type[object]) -> None:
    with pytest.raises(TypeError, match="issued only"):
        frame_type()


@pytest.mark.parametrize("frame_type", [IntentFrame, FittedState])
def test_other_platform_carriers_refuse_direct_construction(frame_type: type[object]) -> None:
    with pytest.raises(TypeError, match="issued only"):
        frame_type()


def test_representation_is_dense_and_model_output_may_be_sparse() -> None:
    representation = _make_representation_frame(
        view=_view(),
        features=_features(),
        columns={"scaled": np.array([0.0, 0.1, 0.2, np.nan], dtype=np.float64)},
        origins={"scaled": STAGE_ORIGIN},
    )
    output = _make_model_output_frame(
        view=_view(),
        keys=np.array([10, 13], dtype=np.int64),
        columns={"score": np.array([1.0, -1.0], dtype=np.float64)},
        origins={"score": STAGE_ORIGIN},
    )

    assert np.array_equal(representation.keys, _features().keys)
    assert output.keys.tolist() == [10, 13]
    assert output.num_rows == 2


def test_sparse_output_keys_must_be_ordered_and_in_bounds() -> None:
    with pytest.raises(ContractViolationError, match="strictly increasing"):
        _make_model_output_frame(
            view=_view(),
            keys=np.array([11, 10], dtype=np.int64),
            columns={"score": np.array([1.0, 2.0], dtype=np.float64)},
            origins={"score": STAGE_ORIGIN},
        )
    with pytest.raises(ContractViolationError, match="inside"):
        _make_model_output_frame(
            view=_view(),
            keys=np.array([14], dtype=np.int64),
            columns={"score": np.array([1.0], dtype=np.float64)},
            origins={"score": STAGE_ORIGIN},
        )


@pytest.mark.parametrize("name", ["direction", "BUY", "target", "target.future"])
def test_model_output_refuses_trading_and_outcome_vocabulary(name: str) -> None:
    with pytest.raises(ContractViolationError, match="reserved"):
        _make_model_output_frame(
            view=_view(),
            keys=np.array([10], dtype=np.int64),
            columns={name: np.array([1.0], dtype=np.float64)},
            origins={name: STAGE_ORIGIN},
        )


def test_target_carriers_are_distinct_and_require_reserved_names() -> None:
    training = _make_training_target_frame(
        view=_view(),
        keys=np.array([10, 11], dtype=np.int64),
        columns={"target.return": np.array([0.1, np.nan], dtype=np.float64)},
        origins={"target.return": STAGE_ORIGIN},
    )
    outcome = _make_outcome_frame(
        view=_view(),
        keys=np.array([10, 11], dtype=np.int64),
        columns={"target.return": np.array([0.1, np.nan], dtype=np.float64)},
        origins={"target.return": STAGE_ORIGIN},
    )

    assert type(training) is TrainingTargetFrame
    assert type(outcome) is OutcomeFrame
    assert id(TrainingTargetFrame) != id(OutcomeFrame)
    with pytest.raises(ContractViolationError, match="reserved target"):
        _make_outcome_frame(
            view=_view(),
            keys=np.array([10], dtype=np.int64),
            columns={"return": np.array([0.1], dtype=np.float64)},
            origins={"return": STAGE_ORIGIN},
        )


def test_model_inputs_contains_only_aligned_feature_and_representation_frames() -> None:
    features = _features()
    representation = _make_representation_frame(
        view=_view(),
        features=features,
        columns={"scaled": np.arange(4, dtype=np.float64)},
        origins={"scaled": STAGE_ORIGIN},
    )
    inputs = ModelInputs(features, representation)

    assert inputs.features is features
    assert inputs.representation is representation
    with pytest.raises(ContractViolationError, match="align exactly"):
        ModelInputs(
            features,
            _make_representation_frame(
                view=_view(),
                features=_features(np.array([10, 11, 12], dtype=np.int64)),
                columns={"scaled": np.arange(3, dtype=np.float64)},
                origins={"scaled": STAGE_ORIGIN},
            ),
        )


def test_frame_accessors_are_isolated_from_descriptor_mutation() -> None:
    output = _make_model_output_frame(
        view=_view(),
        keys=np.array([10, 13], dtype=np.int64),
        columns={"score": np.array([1.0, -1.0], dtype=np.float64)},
        origins={"score": STAGE_ORIGIN},
    )
    first = output.column("score")
    with pytest.raises(ValueError, match="read-only"):
        first[0] = 9.0

    second = output.column("score")
    assert first is not second
    assert second.shape == (2,)
    assert second.dtype == np.dtype(np.float64)
    assert second.tolist() == [1.0, -1.0]


def test_fitted_state_payload_is_non_pickle_and_descriptor_isolated() -> None:
    payload = FittedPayload(
        arrays={"weights": np.array([[1.0, 2.0]], dtype=np.float64)},
        metadata={"format_version": 1},
    )
    state = _make_fitted_state(
        identity_key="a" * 64,
        payload=payload,
    )
    first = state.payload.array("weights")
    with pytest.raises(ValueError, match="read-only"):
        first[0, 0] = 7.0
    second = state.payload.array("weights")

    assert state.identity_key() == "a" * 64
    assert first is not second
    assert second.shape == (1, 2)
    assert state.payload is payload
    assert state.payload.array_names == ("weights",)
    assert state.payload.metadata == {"format_version": 1}
    assert "pickle" not in type(state).__module__.lower()


def test_fitted_payload_is_publicly_constructible_but_identity_free() -> None:
    original = np.array([1, 2], dtype=np.int64)
    payload = FittedPayload(
        arrays={"z": original, "a": np.array([], dtype=np.float64)}, metadata={}
    )
    original[0] = 99

    assert payload.array_names == ("a", "z")
    assert payload.array("z").tolist() == [1, 2]
    assert payload.metadata == {}
    assert not hasattr(payload, "identity_key")
    with pytest.raises(ContractViolationError, match="non-object"):
        FittedPayload(arrays={"bad": np.array([object()], dtype=object)}, metadata={})
    with pytest.raises(ContractViolationError, match="exact FittedPayload"):
        _make_fitted_state(identity_key="a" * 64, payload=object())  # type: ignore[arg-type]
