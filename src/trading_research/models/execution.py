"""Validated declaration snapshot and execution for research models."""

from __future__ import annotations

import typing as _typing
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.research import CorpusModelInputs as _CorpusModelInputs
from trading_research.core.research import ModelInputs as _ModelInputs
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame

__all__: list[str] = []


@_dataclass(frozen=True, slots=True)
class _ModelPlan:
    _origin: _StageOrigin
    _required_feature_names: tuple[str, ...]
    _required_representation_names: tuple[str, ...]
    _output_column_names: tuple[str, ...]
    _cost_hint: _CostHint
    _comparison_tolerance: _ToleranceSpec | None
    _fit: _Callable[[_ViewSpan, _ModelInputs, _TrainingTargetFrame | None], object]
    _infer: _Callable[[_ViewSpan, _FittedState, _ModelInputs], object]


@_dataclass(frozen=True, slots=True)
class _CorpusModelPlan:
    _origin: _StageOrigin
    _required_feature_names: tuple[str, ...]
    _required_representation_names: tuple[str, ...]
    _output_column_names: tuple[str, ...]
    _cost_hint: _CostHint
    _comparison_tolerance: _ToleranceSpec | None
    _fit_corpus: _Callable[[_CorpusModelInputs], object]
    _infer: _Callable[[_ViewSpan, _FittedState, _ModelInputs], object]


def _snapshot_model(model: object) -> _ModelPlan:
    """Read and validate one complete model declaration exactly once."""
    model_id = _read(model, "model_id", label="model")
    if type(model_id) is not str or not model_id:
        raise _ContractViolationError("model_id must be a non-empty string")
    label = f"model {model_id!r}"
    version = _read(model, "version", label=label)
    params = _read(model, "params", label=label)
    if type(version) is not int or version <= 0:
        raise _ContractViolationError(f"{label} version must be a positive Python int")
    if not isinstance(params, _Mapping):
        raise _ContractViolationError(f"{label} params must be a mapping")
    origin = _StageOrigin(
        model_id,
        version,
        _typing.cast("_Mapping[str, _ParamValue]", params),
    )
    features = _names(
        _read(model, "required_feature_names", label=label),
        field="required_feature_names",
        allow_empty=False,
    )
    representations = _names(
        _read(model, "required_representation_names", label=label),
        field="required_representation_names",
        allow_empty=True,
    )
    outputs = _names(
        _read(model, "output_column_names", label=label),
        field="output_column_names",
        allow_empty=False,
    )
    cost_hint = _read(model, "cost_hint", label=label)
    if type(cost_hint) is not _CostHint:
        raise _ContractViolationError(f"{label} cost_hint must be an exact CostHint")
    tolerance = _read(model, "comparison_tolerance", label=label)
    if tolerance is not None and type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError(
            f"{label} comparison_tolerance must be ToleranceSpec or None"
        )
    fit = _read(model, "fit", label=label)
    infer = _read(model, "infer", label=label)
    if not callable(fit) or not callable(infer):
        raise _ContractViolationError(f"{label} fit and infer must be callable")
    if callable(getattr(model, "fit_corpus", None)):
        raise _ContractViolationError(
            f"{label} must not also implement fit_corpus; fit mode is ambiguous"
        )
    return _ModelPlan(
        origin,
        features,
        representations,
        outputs,
        cost_hint,
        tolerance,
        _typing.cast(
            "_Callable[[_ViewSpan, _ModelInputs, _TrainingTargetFrame | None], object]",
            fit,
        ),
        _typing.cast("_Callable[[_ViewSpan, _FittedState, _ModelInputs], object]", infer),
    )


def _snapshot_corpus_model(model: object) -> _CorpusModelPlan:
    """Read and validate one complete corpus-model declaration exactly once."""
    model_id = _read(model, "model_id", label="corpus model")
    if type(model_id) is not str or not model_id:
        raise _ContractViolationError("model_id must be a non-empty string")
    label = f"corpus model {model_id!r}"
    version = _read(model, "version", label=label)
    params = _read(model, "params", label=label)
    if type(version) is not int or version <= 0:
        raise _ContractViolationError(f"{label} version must be a positive Python int")
    if not isinstance(params, _Mapping):
        raise _ContractViolationError(f"{label} params must be a mapping")
    origin = _StageOrigin(
        model_id,
        version,
        _typing.cast("_Mapping[str, _ParamValue]", params),
    )
    features = _names(
        _read(model, "required_feature_names", label=label),
        field="required_feature_names",
        allow_empty=False,
    )
    representations = _names(
        _read(model, "required_representation_names", label=label),
        field="required_representation_names",
        allow_empty=True,
    )
    outputs = _names(
        _read(model, "output_column_names", label=label),
        field="output_column_names",
        allow_empty=False,
    )
    cost_hint = _read(model, "cost_hint", label=label)
    if type(cost_hint) is not _CostHint:
        raise _ContractViolationError(f"{label} cost_hint must be an exact CostHint")
    tolerance = _read(model, "comparison_tolerance", label=label)
    if tolerance is not None and type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError(
            f"{label} comparison_tolerance must be ToleranceSpec or None"
        )
    fit_corpus = _read(model, "fit_corpus", label=label)
    infer = _read(model, "infer", label=label)
    if not callable(fit_corpus) or not callable(infer):
        raise _ContractViolationError(f"{label} fit_corpus and infer must be callable")
    if callable(getattr(model, "fit", None)):
        raise _ContractViolationError(
            f"{label} must not also implement ordinary fit; fit mode is ambiguous"
        )
    return _CorpusModelPlan(
        origin,
        features,
        representations,
        outputs,
        cost_hint,
        tolerance,
        _typing.cast("_Callable[[_CorpusModelInputs], object]", fit_corpus),
        _typing.cast("_Callable[[_ViewSpan, _FittedState, _ModelInputs], object]", infer),
    )


def _same_model_plan(left: _ModelPlan, right: _ModelPlan) -> bool:
    return (
        left._origin.plugin_id == right._origin.plugin_id
        and left._origin.version == right._origin.version
        and _canonical_params_json(left._origin.params)
        == _canonical_params_json(right._origin.params)
        and left._required_feature_names == right._required_feature_names
        and left._required_representation_names == right._required_representation_names
        and left._output_column_names == right._output_column_names
        and left._cost_hint is right._cost_hint
        and left._comparison_tolerance == right._comparison_tolerance
    )


def _same_corpus_model_plan(left: _CorpusModelPlan, right: _CorpusModelPlan) -> bool:
    return (
        left._origin.plugin_id == right._origin.plugin_id
        and left._origin.version == right._origin.version
        and _canonical_params_json(left._origin.params)
        == _canonical_params_json(right._origin.params)
        and left._required_feature_names == right._required_feature_names
        and left._required_representation_names == right._required_representation_names
        and left._output_column_names == right._output_column_names
        and left._cost_hint is right._cost_hint
        and left._comparison_tolerance == right._comparison_tolerance
    )


def _revalidate_model(model: object, expected: _ModelPlan) -> _ModelPlan:
    observed = _snapshot_model(model)
    if not _same_model_plan(expected, observed):
        raise _ContractViolationError(
            f"model {expected._origin.plugin_id!r} declaration changed after identity construction"
        )
    return observed


def _revalidate_corpus_model(
    model: object, expected: _CorpusModelPlan
) -> _CorpusModelPlan:
    observed = _snapshot_corpus_model(model)
    if not _same_corpus_model_plan(expected, observed):
        raise _ContractViolationError(
            f"corpus model {expected._origin.plugin_id!r} declaration changed after identity "
            "construction"
        )
    return observed


def _validate_model_inputs(
    plan: _ModelPlan | _CorpusModelPlan,
    *,
    view: _ViewSpan,
    inputs: _ModelInputs,
    training_targets: _TrainingTargetFrame | None = None,
) -> None:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("model view must be an exact ViewSpan")
    if type(inputs) is not _ModelInputs:
        raise _ContractViolationError("model inputs must be an exact ModelInputs")
    features = inputs.features
    if features.column_names != plan._required_feature_names:
        raise _ContractViolationError(
            "model FeatureFrame columns must exactly equal required_feature_names"
        )
    _validate_frame_axis(view=view, frame=features, label="model FeatureFrame")
    representation = inputs.representation
    if plan._required_representation_names:
        if representation is None:
            raise _ContractViolationError("model requires a RepresentationFrame")
        if representation.column_names != plan._required_representation_names:
            raise _ContractViolationError(
                "model RepresentationFrame columns must exactly equal "
                "required_representation_names"
            )
    elif representation is not None:
        raise _ContractViolationError(
            "model declaring no representation names must not receive a RepresentationFrame"
        )
    if representation is not None:
        _validate_frame_axis(view=view, frame=representation, label="model RepresentationFrame")
        if not _np.array_equal(representation.keys, features.keys):
            raise _ContractViolationError("model feature and representation keys must align")
    if training_targets is not None:
        if type(training_targets) is not _TrainingTargetFrame:
            raise _ContractViolationError(
                "training_targets must be an exact TrainingTargetFrame or None"
            )
        _validate_frame_axis(view=view, frame=training_targets, label="training targets")
        if not _np.array_equal(training_targets.keys, features.keys):
            raise _ContractViolationError("training target keys must exactly match fit inputs")


def _fit_model_payload(
    plan: _ModelPlan,
    *,
    view: _ViewSpan,
    inputs: _ModelInputs,
    training_targets: _TrainingTargetFrame | None,
) -> _FittedPayload:
    _validate_model_inputs(
        plan,
        view=view,
        inputs=inputs,
        training_targets=training_targets,
    )
    result = plan._fit(view, inputs, training_targets)
    if type(result) is not _FittedPayload:
        raise _ContractViolationError("model fit must return an exact FittedPayload")
    return result


def _fit_corpus_model_payload(
    plan: _CorpusModelPlan,
    *,
    inputs: _CorpusModelInputs,
) -> _FittedPayload:
    if type(plan) is not _CorpusModelPlan:
        raise _ContractViolationError("corpus fit requires an exact corpus-model plan")
    if type(inputs) is not _CorpusModelInputs:
        raise _ContractViolationError("corpus fit requires exact CorpusModelInputs")
    result = plan._fit_corpus(inputs)
    if type(result) is not _FittedPayload:
        raise _ContractViolationError("corpus model fit must return an exact FittedPayload")
    return result


def _infer_model_output(
    plan: _ModelPlan | _CorpusModelPlan,
    *,
    view: _ViewSpan,
    fitted_state: _FittedState,
    inputs: _ModelInputs,
) -> _ModelOutputFrame:
    _validate_model_inputs(plan, view=view, inputs=inputs)
    if type(fitted_state) is not _FittedState:
        raise _ContractViolationError("model inference requires an exact FittedState")
    result = plan._infer(view, fitted_state, inputs)
    if type(result) is not _ModelOutputFrame:
        raise _ContractViolationError("model infer must return an exact ModelOutputFrame")
    if result.column_names != plan._output_column_names:
        raise _ContractViolationError(
            "model output columns must exactly equal output_column_names"
        )
    _validate_frame_axis(view=view, frame=result, label="model output")
    feature_keys = inputs.features.keys
    if result.num_rows and not _np.isin(result.keys, feature_keys, assume_unique=True).all():
        raise _ContractViolationError("model output keys must be a subset of input keys")
    for name in result.column_names:
        if not _same_origin(result.origin(name), plan._origin):
            raise _ContractViolationError(
                f"model output origin for {name!r} differs from its declaration"
            )
    return result


def _validate_frame_axis(*, view: _ViewSpan, frame: object, label: str) -> None:
    unit = getattr(frame, "unit", None)
    domain = getattr(frame, "clock_domain", None)
    sources = getattr(frame, "source_identities", None)
    keys = getattr(frame, "keys", None)
    if unit is not view.unit or domain != view.clock_domain or sources != view.source_identities:
        raise _ContractViolationError(f"{label} axis tags must exactly match ViewSpan")
    if type(keys) is not _np.ndarray:
        raise _ContractViolationError(f"{label} keys must be a NumPy array")
    if keys.size and (int(keys[0]) < view.eval_start.value or int(keys[-1]) > view.eval_end.value):
        raise _ContractViolationError(f"{label} keys must lie inside ViewSpan")


def _same_origin(left: _StageOrigin, right: _StageOrigin) -> bool:
    return (
        type(left) is _StageOrigin
        and left.plugin_id == right.plugin_id
        and left.version == right.version
        and _canonical_params_json(left.params) == _canonical_params_json(right.params)
    )


def _names(value: object, *, field: str, allow_empty: bool) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _ContractViolationError(f"{field} must be an exact tuple")
    names = _typing.cast("tuple[object, ...]", value)
    if not names and not allow_empty:
        raise _ContractViolationError(f"{field} must not be empty")
    if any(type(name) is not str or not name for name in names) or len(set(names)) != len(names):
        raise _ContractViolationError(f"{field} must contain unique non-empty strings")
    return _typing.cast("tuple[str, ...]", names)


def _read(instance: object, name: str, *, label: str) -> object:
    try:
        return getattr(instance, name)
    except Exception as exc:
        raise _ContractViolationError(f"cannot read {label} {name}") from exc
