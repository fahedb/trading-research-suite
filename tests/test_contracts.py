"""Phase-3 protocol signatures and frame-only stage boundaries."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from tests.doubles import ColumnarFeatureProvider
from trading_research.core import (
    BoundaryPolicy,
    CostHint,
    DecisionPolicy,
    Evaluator,
    FeatureProvider,
    FittedPayload,
    FittedState,
    IntentFrame,
    MetricValue,
    ModelInputs,
    ModelOutputFrame,
    OutcomeFrame,
    OutcomeRequest,
    OutcomeSpec,
    RepresentationFrame,
    RepresentationModel,
    ResearchModel,
    ToleranceSpec,
    TrainingTargetFrame,
    ViewSpan,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy as np
    import numpy.typing as npt

    from trading_research.core import FeatureFrame, ParamValue


class _Outcome:
    outcome_id = "test_outcome"
    version = 1
    params: Mapping[str, ParamValue] = {}
    horizon_rows = 1
    decision_delay_rows = 1
    reference_price_column = "close"
    boundary_policy = BoundaryPolicy.NAN
    required_columns = ("close",)
    target_column_names = ("target.test",)
    comparison_tolerance: ToleranceSpec | None = None

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        raise NotImplementedError


class _Model:
    model_id = "test_model"
    version = 1
    params: Mapping[str, ParamValue] = {}
    required_feature_names = ("x",)
    required_representation_names: tuple[str, ...] = ()
    output_column_names = ("score",)
    cost_hint = CostHint.CHEAP
    comparison_tolerance: ToleranceSpec | None = None

    def fit(
        self,
        view: ViewSpan,
        inputs: ModelInputs,
        training_targets: TrainingTargetFrame | None = None,
    ) -> FittedPayload:
        raise NotImplementedError

    def infer(
        self, view: ViewSpan, fitted_state: FittedState, inputs: ModelInputs
    ) -> ModelOutputFrame:
        raise NotImplementedError


class _Representation:
    representation_id = "test_representation"
    version = 1
    params: Mapping[str, ParamValue] = {}
    required_feature_names = ("x",)
    output_column_names = ("scaled_x",)
    cost_hint = CostHint.CHEAP
    comparison_tolerance: ToleranceSpec | None = None

    def fit(self, view: ViewSpan, features: FeatureFrame) -> FittedPayload:
        raise NotImplementedError

    def encode(
        self, view: ViewSpan, fitted_state: FittedState, features: FeatureFrame
    ) -> RepresentationFrame:
        raise NotImplementedError


class _Policy:
    policy_id = "test_policy"
    version = 1
    params: Mapping[str, ParamValue] = {}
    required_output_columns = ("score",)
    intent_schema_id = "target_exposure_intent_v1"

    def decide(self, model_output: ModelOutputFrame) -> IntentFrame:
        raise NotImplementedError


class _Evaluator:
    evaluator_id = "test_evaluator"
    version = 1
    params: Mapping[str, ParamValue] = {}
    required_output_columns = ("score",)
    required_outcome_columns = ("target.test",)
    metric_names = ("metric",)
    comparison_tolerance: ToleranceSpec | None = None

    def evaluate(
        self, model_output: ModelOutputFrame, outcome_targets: OutcomeFrame
    ) -> Mapping[str, MetricValue]:
        raise NotImplementedError


def test_test_doubles_satisfy_all_protocols() -> None:
    assert isinstance(ColumnarFeatureProvider(), FeatureProvider)
    assert isinstance(_Outcome(), OutcomeSpec)
    assert isinstance(_Model(), ResearchModel)
    assert isinstance(_Representation(), RepresentationModel)
    assert isinstance(_Policy(), DecisionPolicy)
    assert isinstance(_Evaluator(), Evaluator)


def test_infer_has_no_target_argument() -> None:
    assert tuple(inspect.signature(ResearchModel.infer).parameters) == (
        "self",
        "view",
        "fitted_state",
        "inputs",
    )


def test_plugin_fit_returns_identity_free_payload() -> None:
    assert "FittedPayload" in str(inspect.signature(ResearchModel.fit).return_annotation)
    assert "FittedPayload" in str(inspect.signature(RepresentationModel.fit).return_annotation)
    assert "FittedState" not in str(inspect.signature(ResearchModel.fit).return_annotation)
    assert "FittedState" not in str(inspect.signature(RepresentationModel.fit).return_annotation)


def test_representation_accepts_features_directly_and_cannot_chain() -> None:
    fit = inspect.signature(RepresentationModel.fit)
    encode = inspect.signature(RepresentationModel.encode)

    assert tuple(fit.parameters) == ("self", "view", "features")
    assert tuple(encode.parameters) == ("self", "view", "fitted_state", "features")
    assert "ModelInputs" not in str(fit)
    assert "RepresentationFrame" not in str(fit)
    assert "ModelInputs" not in str(encode)


def test_only_research_model_accepts_model_inputs() -> None:
    assert "ModelInputs" in str(inspect.signature(ResearchModel.fit))
    assert "ModelInputs" in str(inspect.signature(ResearchModel.infer))
    assert "ModelInputs" not in str(inspect.signature(OutcomeSpec.compute))
    assert "ModelInputs" not in str(inspect.signature(DecisionPolicy.decide))
    assert "ModelInputs" not in str(inspect.signature(Evaluator.evaluate))
