"""Structural contracts implemented by feature and Phase-3 research plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Protocol as _Protocol
from typing import runtime_checkable as _runtime_checkable

if _TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy as np
    import numpy.typing as npt

    from trading_research.core.feature_frames import FeatureFrame
    from trading_research.core.features import (
        CostHint,
        FeatureRequest,
        ParamValue,
        WindowSpec,
    )
    from trading_research.core.research import (
        BoundaryPolicy,
        CorpusModelInputs,
        MetricValue,
        ModelInputs,
        OutcomeRequest,
        ToleranceSpec,
        ViewSpan,
    )
    from trading_research.core.research_frames import (
        FittedPayload,
        FittedState,
        IntentFrame,
        ModelOutputFrame,
        OutcomeFrame,
        RepresentationFrame,
        TrainingTargetFrame,
    )
    from trading_research.core.simulation import EquityCurve, TradeLog

__all__ = [
    "CorpusResearchModel",
    "DecisionPolicy",
    "Evaluator",
    "FeatureProvider",
    "OutcomeSpec",
    "RepresentationModel",
    "ResearchModel",
    "TradeEvaluator",
]


@_runtime_checkable
class FeatureProvider(_Protocol):
    """Compute declared dense feature columns from bounded market columns."""

    @property
    def provider_id(self) -> str:
        """Return the stable provider identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return ordered feature columns emitted by the provider."""
        ...

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return ordered market columns the provider may read."""
        ...

    @property
    def window(self) -> WindowSpec:
        """Return the finite declared backward reach."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def cost_hint(self) -> CostHint:
        """Return the explicit materialization cost hint."""
        ...

    def compute(self, request: FeatureRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        """Compute exactly the declared feature columns."""
        ...


@_runtime_checkable
class OutcomeSpec(_Protocol):
    """The only plugin permitted to read forward of a decision row."""

    @property
    def outcome_id(self) -> str:
        """Return the stable outcome identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def horizon_rows(self) -> int:
        """Return the positive observed-row horizon."""
        ...

    @property
    def decision_delay_rows(self) -> int:
        """Return the explicit decision-to-origin delay."""
        ...

    @property
    def reference_price_column(self) -> str:
        """Return the market price column used by the outcome."""
        ...

    @property
    def boundary_policy(self) -> BoundaryPolicy:
        """Return the explicit end-of-data boundary policy."""
        ...

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the only market columns the outcome may read."""
        ...

    @property
    def target_column_names(self) -> tuple[str, ...]:
        """Return ordered reserved-namespace target columns."""
        ...

    @property
    def comparison_tolerance(self) -> ToleranceSpec | None:
        """Return the declared tolerance, or None for exact comparison."""
        ...

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        """Compute exactly the declared target columns."""
        ...


@_runtime_checkable
class ResearchModel(_Protocol):
    """Fit on permitted inputs and infer row-locally without a target channel."""

    @property
    def model_id(self) -> str:
        """Return the stable model identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return ordered feature columns consumed by the model."""
        ...

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Return ordered representation columns consumed by the model."""
        ...

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return ordered generic output columns."""
        ...

    @property
    def cost_hint(self) -> CostHint:
        """Return the explicit materialization cost hint."""
        ...

    @property
    def comparison_tolerance(self) -> ToleranceSpec | None:
        """Return the declared tolerance, or None for exact comparison."""
        ...

    def fit(
        self,
        view: ViewSpan,
        inputs: ModelInputs,
        training_targets: TrainingTargetFrame | None = None,
    ) -> FittedPayload:
        """Return learned content only; the platform binds its identity."""
        ...

    def infer(
        self,
        view: ViewSpan,
        fitted_state: FittedState,
        inputs: ModelInputs,
    ) -> ModelOutputFrame:
        """Infer each row from only that row's inputs plus fixed fitted state."""
        ...


@_runtime_checkable
class CorpusResearchModel(_Protocol):
    """Fit from an immutable cross-run corpus and infer row-locally."""

    @property
    def model_id(self) -> str:
        """Return the stable model identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return ordered inference feature columns consumed by the model."""
        ...

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Return ordered representation columns consumed at inference."""
        ...

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return ordered generic output columns."""
        ...

    @property
    def cost_hint(self) -> CostHint:
        """Return the explicit materialization cost hint."""
        ...

    @property
    def comparison_tolerance(self) -> ToleranceSpec | None:
        """Return the declared tolerance, or None for exact comparison."""
        ...

    def fit_corpus(self, inputs: CorpusModelInputs) -> FittedPayload:
        """Return learned content only; the bundle platform binds its identity."""
        ...

    def infer(
        self,
        view: ViewSpan,
        fitted_state: FittedState,
        inputs: ModelInputs,
    ) -> ModelOutputFrame:
        """Infer each row from only that row's inputs plus fixed fitted state."""
        ...


@_runtime_checkable
class RepresentationModel(_Protocol):
    """Fit and encode one FeatureFrame directly; representation chaining is absent."""

    @property
    def representation_id(self) -> str:
        """Return the stable representation identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return ordered feature columns consumed by the representation."""
        ...

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return ordered representation columns emitted."""
        ...

    @property
    def cost_hint(self) -> CostHint:
        """Return the explicit materialization cost hint."""
        ...

    @property
    def comparison_tolerance(self) -> ToleranceSpec | None:
        """Return the declared tolerance, or None for exact comparison."""
        ...

    def fit(self, view: ViewSpan, features: FeatureFrame) -> FittedPayload:
        """Return learned content only; the platform binds its identity."""
        ...

    def encode(
        self,
        view: ViewSpan,
        fitted_state: FittedState,
        features: FeatureFrame,
    ) -> RepresentationFrame:
        """Encode rows independently from a FeatureFrame and fixed state."""
        ...


@_runtime_checkable
class DecisionPolicy(_Protocol):
    """Convert generic model output into one declared intent schema."""

    @property
    def policy_id(self) -> str:
        """Return the stable policy identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return ordered model-output columns consumed."""
        ...

    @property
    def intent_schema_id(self) -> str:
        """Return the one intent schema emitted by this policy."""
        ...

    def decide(self, model_output: ModelOutputFrame) -> IntentFrame:
        """Convert one already-computed model output into intent."""
        ...


@_runtime_checkable
class Evaluator(_Protocol):
    """Evaluate already-computed forecast and outcome artifacts only."""

    @property
    def evaluator_id(self) -> str:
        """Return the stable evaluator identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return ordered forecast columns consumed."""
        ...

    @property
    def required_outcome_columns(self) -> tuple[str, ...]:
        """Return ordered outcome columns consumed."""
        ...

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return ordered metric names emitted."""
        ...

    @property
    def comparison_tolerance(self) -> ToleranceSpec | None:
        """Return the declared tolerance, or None for exact comparison."""
        ...

    def evaluate(
        self,
        model_output: ModelOutputFrame,
        outcome_targets: OutcomeFrame,
    ) -> Mapping[str, MetricValue]:
        """Evaluate supplied artifacts without recomputing upstream stages."""
        ...


@_runtime_checkable
class TradeEvaluator(_Protocol):
    """Evaluate closed Plane-B trade and equity artifacts only."""

    @property
    def evaluator_id(self) -> str:
        """Return the stable evaluator identifier."""
        ...

    @property
    def version(self) -> int:
        """Return the identity-bearing contract version."""
        ...

    @property
    def params(self) -> Mapping[str, ParamValue]:
        """Return canonical identity-bearing parameters."""
        ...

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return ordered metric names emitted."""
        ...

    @property
    def comparison_tolerance(self) -> ToleranceSpec | None:
        """Return the declared tolerance, or None for exact comparison."""
        ...

    def evaluate(
        self,
        trade_log: TradeLog,
        equity_curve: EquityCurve,
    ) -> Mapping[str, MetricValue]:
        """Evaluate supplied Plane-B artifacts without recomputing upstream work."""
        ...
