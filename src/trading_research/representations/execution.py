"""Validated declaration snapshot and execution for one representation stage."""

from __future__ import annotations

import typing as _typing
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import RepresentationFrame as _RepresentationFrame

__all__: list[str] = []


@_dataclass(frozen=True, slots=True)
class _RepresentationPlan:
    _origin: _StageOrigin
    _required_feature_names: tuple[str, ...]
    _output_column_names: tuple[str, ...]
    _cost_hint: _CostHint
    _comparison_tolerance: _ToleranceSpec | None
    _fit: _Callable[[_ViewSpan, _FeatureFrame], object]
    _encode: _Callable[[_ViewSpan, _FittedState, _FeatureFrame], object]


def _snapshot_representation(representation: object) -> _RepresentationPlan:
    """Read and validate one complete representation declaration once."""
    representation_id = _read(representation, "representation_id", label="representation")
    if type(representation_id) is not str or not representation_id:
        raise _ContractViolationError("representation_id must be a non-empty string")
    label = f"representation {representation_id!r}"
    version = _read(representation, "version", label=label)
    params = _read(representation, "params", label=label)
    if type(version) is not int or version <= 0:
        raise _ContractViolationError(f"{label} version must be a positive Python int")
    if not isinstance(params, _Mapping):
        raise _ContractViolationError(f"{label} params must be a mapping")
    origin = _StageOrigin(
        representation_id,
        version,
        _typing.cast("_Mapping[str, _ParamValue]", params),
    )
    features = _names(
        _read(representation, "required_feature_names", label=label),
        field="required_feature_names",
    )
    outputs = _names(
        _read(representation, "output_column_names", label=label),
        field="output_column_names",
    )
    cost_hint = _read(representation, "cost_hint", label=label)
    if type(cost_hint) is not _CostHint:
        raise _ContractViolationError(f"{label} cost_hint must be an exact CostHint")
    tolerance = _read(representation, "comparison_tolerance", label=label)
    if tolerance is not None and type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError(
            f"{label} comparison_tolerance must be ToleranceSpec or None"
        )
    fit = _read(representation, "fit", label=label)
    encode = _read(representation, "encode", label=label)
    if not callable(fit) or not callable(encode):
        raise _ContractViolationError(f"{label} fit and encode must be callable")
    return _RepresentationPlan(
        origin,
        features,
        outputs,
        cost_hint,
        tolerance,
        _typing.cast("_Callable[[_ViewSpan, _FeatureFrame], object]", fit),
        _typing.cast(
            "_Callable[[_ViewSpan, _FittedState, _FeatureFrame], object]",
            encode,
        ),
    )


def _same_representation_plan(
    left: _RepresentationPlan, right: _RepresentationPlan
) -> bool:
    return (
        left._origin.plugin_id == right._origin.plugin_id
        and left._origin.version == right._origin.version
        and _canonical_params_json(left._origin.params)
        == _canonical_params_json(right._origin.params)
        and left._required_feature_names == right._required_feature_names
        and left._output_column_names == right._output_column_names
        and left._cost_hint is right._cost_hint
        and left._comparison_tolerance == right._comparison_tolerance
    )


def _revalidate_representation(
    representation: object, expected: _RepresentationPlan
) -> _RepresentationPlan:
    observed = _snapshot_representation(representation)
    if not _same_representation_plan(expected, observed):
        raise _ContractViolationError(
            f"representation {expected._origin.plugin_id!r} declaration changed after "
            "identity construction"
        )
    return observed


def _validate_representation_features(
    plan: _RepresentationPlan,
    *,
    view: _ViewSpan,
    features: _FeatureFrame,
) -> None:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("representation view must be an exact ViewSpan")
    if type(features) is not _FeatureFrame:
        raise _ContractViolationError("representation features must be an exact FeatureFrame")
    if features.column_names != plan._required_feature_names:
        raise _ContractViolationError(
            "representation FeatureFrame columns must exactly equal required_feature_names"
        )
    if (
        features.unit is not view.unit
        or features.clock_domain != view.clock_domain
        or features.source_identities != view.source_identities
    ):
        raise _ContractViolationError("representation FeatureFrame axis must match ViewSpan")
    keys = features.keys
    if int(keys[0]) < view.eval_start.value or int(keys[-1]) > view.eval_end.value:
        raise _ContractViolationError("representation FeatureFrame keys must lie inside ViewSpan")


def _fit_representation_payload(
    plan: _RepresentationPlan,
    *,
    view: _ViewSpan,
    features: _FeatureFrame,
) -> _FittedPayload:
    _validate_representation_features(plan, view=view, features=features)
    result = plan._fit(view, features)
    if type(result) is not _FittedPayload:
        raise _ContractViolationError("representation fit must return an exact FittedPayload")
    return result


def _encode_representation_frame(
    plan: _RepresentationPlan,
    *,
    view: _ViewSpan,
    fitted_state: _FittedState,
    features: _FeatureFrame,
) -> _RepresentationFrame:
    _validate_representation_features(plan, view=view, features=features)
    if type(fitted_state) is not _FittedState:
        raise _ContractViolationError("representation encode requires an exact FittedState")
    result = plan._encode(view, fitted_state, features)
    if type(result) is not _RepresentationFrame:
        raise _ContractViolationError(
            "representation encode must return an exact RepresentationFrame"
        )
    if result.column_names != plan._output_column_names:
        raise _ContractViolationError(
            "representation columns must exactly equal output_column_names"
        )
    if (
        result.unit is not view.unit
        or result.clock_domain != view.clock_domain
        or result.source_identities != view.source_identities
        or not _np.array_equal(result.keys, features.keys)
    ):
        raise _ContractViolationError(
            "RepresentationFrame must align exactly with its FeatureFrame and ViewSpan"
        )
    for name in result.column_names:
        if not _same_origin(result.origin(name), plan._origin):
            raise _ContractViolationError(
                f"representation origin for {name!r} differs from its declaration"
            )
    return result


def _same_origin(left: _StageOrigin, right: _StageOrigin) -> bool:
    return (
        type(left) is _StageOrigin
        and left.plugin_id == right.plugin_id
        and left.version == right.version
        and _canonical_params_json(left.params) == _canonical_params_json(right.params)
    )


def _names(value: object, *, field: str) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        raise _ContractViolationError(f"{field} must be a non-empty exact tuple")
    names = _typing.cast("tuple[object, ...]", value)
    if any(type(name) is not str or not name for name in names) or len(set(names)) != len(names):
        raise _ContractViolationError(f"{field} must contain unique non-empty strings")
    return _typing.cast("tuple[str, ...]", names)


def _read(instance: object, name: str, *, label: str) -> object:
    try:
        return getattr(instance, name)
    except Exception as exc:
        raise _ContractViolationError(f"cannot read {label} {name}") from exc
