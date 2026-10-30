"""Composition-root adaptation for fitted representations and research models."""

from __future__ import annotations

import typing as _typing

from trading_research.artifacts.fitted import _FittedStateIdentity, _materialize_fitted_state
from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.artifacts.materialization import (
    MaterializationPolicy as _MaterializationPolicy,
)
from trading_research.artifacts.store import ArtifactStore as _ArtifactStore
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import ModelInputs as _ModelInputs
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import RepresentationFrame as _RepresentationFrame
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame
from trading_research.data.gating import SplitPolicyIdentity as _SplitPolicyIdentity
from trading_research.data.views import BoundedView as _BoundedView
from trading_research.data.views import ViewUnion as _ViewUnion
from trading_research.experiments.folds import _FitIsolation
from trading_research.models.execution import (
    _fit_model_payload,
    _infer_model_output,
    _revalidate_model,
    _snapshot_corpus_model,
    _snapshot_model,
    _validate_model_inputs,
)
from trading_research.representations.execution import (
    _encode_representation_frame,
    _fit_representation_payload,
    _revalidate_representation,
    _snapshot_representation,
    _validate_representation_features,
)

if _typing.TYPE_CHECKING:
    from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
    from trading_research.core.research import StageOrigin as _StageOrigin

__all__ = [
    "encode_representation",
    "fit_model",
    "fit_representation",
    "infer_model",
    "view_span_from_bounded_view",
]


def view_span_from_bounded_view(view: _BoundedView) -> _ViewSpan:
    """Adapt an already-authorized bounded data view into a rowless core descriptor."""
    if type(view) is not _BoundedView:
        raise _ContractViolationError("view must be an exact BoundedView")
    return _ViewSpan(
        tuple(segment.dataset_ref.canonical_identity for segment in view.segments),
        view.eval_frame.unit,
        view.eval_frame.clock_domain,
        view.context.resolved_context_start,
        view.eval_start,
        view.eval_end,
    )


def fit_model(
    model: object,
    *,
    view: _ViewSpan,
    inputs: _ModelInputs,
    training_targets: _TrainingTargetFrame | None,
    representation_state: _FittedState | None,
    split_policy_identity: _SplitPolicyIdentity,
    fit_isolation: _FitIsolation,
    platform_identity: _PlatformIdentity,
    dependency_lock_sha256: str,
    store: _ArtifactStore,
    view_union: _ViewUnion | None = None,
    policy: _MaterializationPolicy = _MaterializationPolicy.CACHE_IF_EXPENSIVE,
) -> _FittedState:
    """Fit or restore one model state under complete caller-supplied identity."""
    _validate_identity_context(
        view=view,
        split_policy_identity=split_policy_identity,
        fit_isolation=fit_isolation,
        platform_identity=platform_identity,
        store=store,
        policy=policy,
        view_union=view_union,
    )
    plan = _snapshot_model(model)
    _validate_model_inputs(
        plan,
        view=view,
        inputs=inputs,
        training_targets=training_targets,
    )
    fit_isolation._validate_fit(view, inputs.features)
    representation = inputs.representation
    if representation is None:
        if representation_state is not None:
            raise _ContractViolationError(
                "representation_state must be None when ModelInputs has no representation"
            )
        representation_origins: tuple[tuple[str, _StageOrigin], ...] = ()
        representation_key = None
    else:
        if type(representation_state) is not _FittedState:
            raise _ContractViolationError(
                "a model representation requires its exact fitted representation state"
            )
        representation_origins = tuple(
            (name, representation.origin(name)) for name in plan._required_representation_names
        )
        representation_key = representation_state.identity_key()
    identity = _FittedStateIdentity(
        plugin_kind="model",
        plugin_id=plan._origin.plugin_id,
        plugin_version=plan._origin.version,
        plugin_params=plan._origin.params,
        feature_origins=tuple(
            (name, inputs.features.origin(name)) for name in plan._required_feature_names
        ),
        representation_origins=representation_origins,
        representation_state_identity_key=representation_key,
        training_view=view,
        split_policy_adr=split_policy_identity.adr,
        split_policy_version=split_policy_identity.policy_version,
        split_policy_sha256=split_policy_identity.policy_sha256,
        training_targets=training_targets,
        fit_isolation=fit_isolation._identity_params(),
        platform_identity=platform_identity,
        dependency_lock_sha256=dependency_lock_sha256,
        comparison_tolerance=plan._comparison_tolerance,
    )
    execution_plan = plan

    def _validate_after_lookup() -> None:
        nonlocal execution_plan
        execution_plan = _revalidate_model(model, plan)

    return _materialize_fitted_state(
        identity=identity,
        cost_hint=plan._cost_hint,
        store=store,
        policy=policy,
        validate_after_lookup=_validate_after_lookup,
        fit=lambda: _fit_model_payload(
            execution_plan,
            view=view,
            inputs=inputs,
            training_targets=training_targets,
        ),
    )


def infer_model(
    model: object,
    *,
    view: _ViewSpan,
    fitted_state: _FittedState,
    inputs: _ModelInputs,
) -> _ModelOutputFrame:
    """Validate declarations and execute row-local model inference."""
    plan = _snapshot_model(model)
    return _infer_model_output(
        plan,
        view=view,
        fitted_state=fitted_state,
        inputs=inputs,
    )


def _infer_corpus_model(
    model: object,
    *,
    view: _ViewSpan,
    fitted_state: _FittedState,
    inputs: _ModelInputs,
) -> _ModelOutputFrame:
    """Validate a corpus-model declaration and run its shared inference boundary."""
    plan = _snapshot_corpus_model(model)
    return _infer_model_output(
        plan,
        view=view,
        fitted_state=fitted_state,
        inputs=inputs,
    )


def fit_representation(
    representation: object,
    *,
    view: _ViewSpan,
    features: _FeatureFrame,
    split_policy_identity: _SplitPolicyIdentity,
    fit_isolation: _FitIsolation,
    platform_identity: _PlatformIdentity,
    dependency_lock_sha256: str,
    store: _ArtifactStore,
    view_union: _ViewUnion | None = None,
    policy: _MaterializationPolicy = _MaterializationPolicy.CACHE_IF_EXPENSIVE,
) -> _FittedState:
    """Fit or restore one representation state without a target channel."""
    _validate_identity_context(
        view=view,
        split_policy_identity=split_policy_identity,
        fit_isolation=fit_isolation,
        platform_identity=platform_identity,
        store=store,
        policy=policy,
        view_union=view_union,
    )
    plan = _snapshot_representation(representation)
    _validate_representation_features(plan, view=view, features=features)
    fit_isolation._validate_fit(view, features)
    identity = _FittedStateIdentity(
        plugin_kind="representation",
        plugin_id=plan._origin.plugin_id,
        plugin_version=plan._origin.version,
        plugin_params=plan._origin.params,
        feature_origins=tuple(
            (name, features.origin(name)) for name in plan._required_feature_names
        ),
        representation_origins=(),
        representation_state_identity_key=None,
        training_view=view,
        split_policy_adr=split_policy_identity.adr,
        split_policy_version=split_policy_identity.policy_version,
        split_policy_sha256=split_policy_identity.policy_sha256,
        training_targets=None,
        fit_isolation=fit_isolation._identity_params(),
        platform_identity=platform_identity,
        dependency_lock_sha256=dependency_lock_sha256,
        comparison_tolerance=plan._comparison_tolerance,
    )
    execution_plan = plan

    def _validate_after_lookup() -> None:
        nonlocal execution_plan
        execution_plan = _revalidate_representation(representation, plan)

    return _materialize_fitted_state(
        identity=identity,
        cost_hint=plan._cost_hint,
        store=store,
        policy=policy,
        validate_after_lookup=_validate_after_lookup,
        fit=lambda: _fit_representation_payload(
            execution_plan,
            view=view,
            features=features,
        ),
    )


def encode_representation(
    representation: object,
    *,
    view: _ViewSpan,
    fitted_state: _FittedState,
    features: _FeatureFrame,
) -> _RepresentationFrame:
    """Validate declarations and encode one dense row-local representation."""
    plan = _snapshot_representation(representation)
    return _encode_representation_frame(
        plan,
        view=view,
        fitted_state=fitted_state,
        features=features,
    )


def _validate_identity_context(
    *,
    view: _ViewSpan,
    split_policy_identity: _SplitPolicyIdentity,
    fit_isolation: _FitIsolation,
    platform_identity: _PlatformIdentity,
    store: _ArtifactStore,
    policy: _MaterializationPolicy,
    view_union: _ViewUnion | None,
) -> None:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("fit view must be an exact ViewSpan")
    if type(split_policy_identity) is not _SplitPolicyIdentity:
        raise _ContractViolationError("split_policy_identity must be an exact SplitPolicyIdentity")
    if type(platform_identity) is not _PlatformIdentity:
        raise _ContractViolationError("platform_identity must be an exact PlatformIdentity")
    if type(store) is not _ArtifactStore:
        raise _ContractViolationError("store must be an exact ArtifactStore")
    if type(fit_isolation) is not _FitIsolation:
        raise _ContractViolationError("fit_isolation must be an exact generated FitIsolation")
    if type(policy) is not _MaterializationPolicy:
        raise _ContractViolationError("policy must be an exact MaterializationPolicy")
    if view_union is None:
        return
    if type(view_union) is not _ViewUnion:
        raise _ContractViolationError("view_union must be an exact ViewUnion or None")
    union_sources = tuple(member.dataset_ref.canonical_identity for member in view_union.members)
    if union_sources != view.source_identities:
        raise _ContractViolationError(
            "ViewUnion member identities must exactly match the promotion fit ViewSpan"
        )
    if any(member._split_policy_identity != split_policy_identity for member in view_union.members):
        raise _ContractViolationError(
            "ViewUnion split-policy identity must exactly match the fit identity"
        )
