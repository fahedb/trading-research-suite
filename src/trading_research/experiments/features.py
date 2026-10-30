"""Bounded-view adaptation for deterministic feature computation."""

from __future__ import annotations

import typing as _typing

from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.artifacts.identity import StageIdentity as _StageIdentity
from trading_research.artifacts.materialization import (
    MaterializationPolicy as _MaterializationPolicy,
)
from trading_research.artifacts.materialization import (
    _materialize_feature_frame,
    _raise_feature_computation_failure,
)
from trading_research.core.errors import FeatureContractError as _FeatureContractError
from trading_research.data.gating import SplitPolicyIdentity as _SplitPolicyIdentity
from trading_research.data.views import BoundedView as _BoundedView
from trading_research.features.execution import (
    _execute_feature_plans,
    _execute_features,
    _ProviderExecutionError,
    _revalidated_provider_plan,
    _validate_prepared_context,
    _validated_plan,
    _validated_provider_objects,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Sequence as _Sequence

    from trading_research.artifacts.store import ArtifactStore as _ArtifactStore
    from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
    from trading_research.core.interfaces import FeatureProvider as _FeatureProvider
    from trading_research.features.execution import _ProviderPlan

__all__ = ["compute_features", "materialize_features"]


def compute_features(
    view: _BoundedView,
    providers: _Sequence[_FeatureProvider],
) -> _FeatureFrame:
    """Compute a complete provider plan from one already-authorized bounded view."""
    if type(view) is not _BoundedView:
        raise _FeatureContractError(f"view must be an exact BoundedView, got {type(view).__name__}")
    return _execute_features(
        frames=tuple(segment.frame for segment in view.segments),
        eval_start=view.eval_start,
        eval_end=view.eval_end,
        expected_eval_keys=view.eval_frame.keys,
        providers=providers,
        short_context_accepted=view.context.short_context_accepted,
    )


def materialize_features(
    view: _BoundedView,
    providers: _Sequence[_FeatureProvider],
    *,
    split_policy_identity: _SplitPolicyIdentity,
    platform_identity: _PlatformIdentity,
    store: _ArtifactStore,
    policy: _MaterializationPolicy = _MaterializationPolicy.CACHE_IF_EXPENSIVE,
) -> _FeatureFrame:
    """Compute or restore one identity-bound feature frame under an explicit policy."""
    if type(view) is not _BoundedView:
        raise _FeatureContractError(f"view must be an exact BoundedView, got {type(view).__name__}")
    if type(split_policy_identity) is not _SplitPolicyIdentity:
        raise _FeatureContractError("split_policy_identity must be an exact SplitPolicyIdentity")
    if type(platform_identity) is not _PlatformIdentity:
        raise _FeatureContractError("platform_identity must be an exact PlatformIdentity")
    if type(policy) is not _MaterializationPolicy:
        raise _FeatureContractError(
            f"policy must be an exact MaterializationPolicy, got {type(policy).__name__}"
        )

    provider_objects = _validated_provider_objects(providers)
    plans = _validated_plan(provider_objects)
    frames = tuple(segment.frame for segment in view.segments)
    expected_eval_keys = view.eval_frame.keys
    source_identities = tuple(frame.dataset_ref.canonical_identity for frame in frames)
    identity = _identity_from_plans(
        view=view,
        plans=plans,
        split_policy_identity=split_policy_identity,
        platform_identity=platform_identity,
    )
    capture_provider_failure = policy is _MaterializationPolicy.PERSIST_ON_FAILURE
    try:
        _validate_prepared_context(
            frames=frames,
            eval_start=view.eval_start,
            expected_eval_keys=expected_eval_keys,
            plans=plans,
            short_context_accepted=view.context.short_context_accepted,
            capture_provider_failure=capture_provider_failure,
        )
    except _ProviderExecutionError as exc:
        _raise_feature_computation_failure(
            identity_key=identity.identity_key(),
            provider_id=exc.provider_id,
            cause=exc._error,
        )
    expected_column_names = tuple(name for plan in plans for name in plan._feature_names)
    expected_origins = {name: plan._origin for plan in plans for name in plan._feature_names}
    execution_plans = plans

    def _validate_after_lookup() -> None:
        nonlocal execution_plans
        execution_plans = _revalidated_provider_plan(
            provider_objects=provider_objects,
            expected=plans,
        )

    def _compute_prepared() -> _FeatureFrame:
        try:
            return _execute_feature_plans(
                frames=frames,
                eval_start=view.eval_start,
                eval_end=view.eval_end,
                expected_eval_keys=expected_eval_keys,
                plans=execution_plans,
                short_context_accepted=view.context.short_context_accepted,
                capture_provider_failure=capture_provider_failure,
            )
        except _ProviderExecutionError as exc:
            _raise_feature_computation_failure(
                identity_key=identity.identity_key(),
                provider_id=exc.provider_id,
                cause=exc._error,
            )

    return _materialize_feature_frame(
        identity=identity,
        policy=policy,
        cost_hints=tuple(plan._cost_hint for plan in plans),
        store=store,
        expected_eval_keys=expected_eval_keys,
        expected_unit=view.eval_frame.unit,
        expected_clock_domain=view.eval_frame.clock_domain,
        expected_source_identities=source_identities,
        expected_column_names=expected_column_names,
        expected_origins=expected_origins,
        validate_after_lookup=_validate_after_lookup,
        compute=_compute_prepared,
    )


def _feature_stage_identity(
    view: _BoundedView,
    providers: _Sequence[_FeatureProvider],
    *,
    split_policy_identity: _SplitPolicyIdentity,
    platform_identity: _PlatformIdentity,
) -> _StageIdentity:
    """Build the exact pre-execution identity used by feature materialization."""
    if type(view) is not _BoundedView:
        raise _FeatureContractError("view must be an exact BoundedView")
    if type(split_policy_identity) is not _SplitPolicyIdentity:
        raise _FeatureContractError("split_policy_identity must be an exact SplitPolicyIdentity")
    if type(platform_identity) is not _PlatformIdentity:
        raise _FeatureContractError("platform_identity must be an exact PlatformIdentity")
    plans = _validated_plan(_validated_provider_objects(providers))
    return _identity_from_plans(
        view=view,
        plans=plans,
        split_policy_identity=split_policy_identity,
        platform_identity=platform_identity,
    )


def _identity_from_plans(
    *,
    view: _BoundedView,
    plans: _Sequence[_ProviderPlan],
    split_policy_identity: _SplitPolicyIdentity,
    platform_identity: _PlatformIdentity,
) -> _StageIdentity:
    return _StageIdentity(
        source_identities=tuple(
            segment.dataset_ref.canonical_identity for segment in view.segments
        ),
        resolved_context_start=view.context.resolved_context_start,
        eval_start=view.eval_start,
        eval_end=view.eval_end,
        split_policy_adr=split_policy_identity.adr,
        split_policy_version=split_policy_identity.policy_version,
        split_policy_sha256=split_policy_identity.policy_sha256,
        providers=plans,
        platform_identity=platform_identity,
    )
