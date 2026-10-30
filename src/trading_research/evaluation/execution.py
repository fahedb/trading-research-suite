"""Validated declaration snapshot and artifact-only Plane-A evaluation."""

from __future__ import annotations

import typing as _typing
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.pipeline import _require_columns
from trading_research.core.research import MetricValue as _MetricValue
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import _is_reserved_outcome_name
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import OutcomeFrame as _OutcomeFrame
from trading_research.core.simulation import EquityCurve as _EquityCurve
from trading_research.core.simulation import TradeLog as _TradeLog

__all__: list[str] = []


@_dataclass(frozen=True, slots=True)
class _EvaluatorPlan:
    _origin: _StageOrigin
    _required_output_columns: tuple[str, ...]
    _required_outcome_columns: tuple[str, ...]
    _metric_names: tuple[str, ...]
    _comparison_tolerance: _ToleranceSpec | None
    _evaluate: _Callable[[_ModelOutputFrame, _OutcomeFrame], object]


@_dataclass(frozen=True, slots=True)
class _TradeEvaluatorPlan:
    _origin: _StageOrigin
    _metric_names: tuple[str, ...]
    _comparison_tolerance: _ToleranceSpec | None
    _evaluate: _Callable[[_TradeLog, _EquityCurve], object]


def _snapshot_evaluator(evaluator: object) -> _EvaluatorPlan:
    """Read and validate one complete evaluator declaration exactly once."""
    evaluator_id = _read(evaluator, "evaluator_id", label="evaluator")
    if type(evaluator_id) is not str or not evaluator_id:
        raise _ContractViolationError("evaluator_id must be a non-empty string")
    label = f"evaluator {evaluator_id!r}"
    version = _read(evaluator, "version", label=label)
    params = _read(evaluator, "params", label=label)
    if type(version) is not int or version <= 0:
        raise _ContractViolationError(f"{label} version must be a positive Python int")
    if not isinstance(params, _Mapping):
        raise _ContractViolationError(f"{label} params must be a mapping")
    origin = _StageOrigin(
        evaluator_id,
        version,
        _typing.cast("_Mapping[str, _ParamValue]", params),
    )
    outputs = _names(
        _read(evaluator, "required_output_columns", label=label),
        field="required_output_columns",
    )
    outcomes = _names(
        _read(evaluator, "required_outcome_columns", label=label),
        field="required_outcome_columns",
    )
    metrics = _names(
        _read(evaluator, "metric_names", label=label),
        field="metric_names",
    )
    if any(_is_reserved_outcome_name(name) for name in outputs):
        raise _ContractViolationError(
            "evaluator model-output inputs cannot use the target namespace"
        )
    if any(not _is_reserved_outcome_name(name) for name in outcomes):
        raise _ContractViolationError("evaluator outcome inputs must use the target namespace")
    tolerance = _read(evaluator, "comparison_tolerance", label=label)
    if tolerance is not None and type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError(f"{label} comparison_tolerance must be ToleranceSpec or None")
    evaluate = _read(evaluator, "evaluate", label=label)
    if not callable(evaluate):
        raise _ContractViolationError(f"{label} evaluate must be callable")
    return _EvaluatorPlan(
        origin,
        outputs,
        outcomes,
        metrics,
        tolerance,
        _typing.cast("_Callable[[_ModelOutputFrame, _OutcomeFrame], object]", evaluate),
    )


def _snapshot_trade_evaluator(evaluator: object) -> _TradeEvaluatorPlan:
    """Read and validate one complete Plane-B declaration exactly once."""
    evaluator_id = _read(evaluator, "evaluator_id", label="trade evaluator")
    if type(evaluator_id) is not str or not evaluator_id:
        raise _ContractViolationError("trade evaluator_id must be a non-empty string")
    label = f"trade evaluator {evaluator_id!r}"
    version = _read(evaluator, "version", label=label)
    params = _read(evaluator, "params", label=label)
    if type(version) is not int or version <= 0:
        raise _ContractViolationError(f"{label} version must be a positive Python int")
    if not isinstance(params, _Mapping):
        raise _ContractViolationError(f"{label} params must be a mapping")
    origin = _StageOrigin(
        evaluator_id,
        version,
        _typing.cast("_Mapping[str, _ParamValue]", params),
    )
    metrics = _names(
        _read(evaluator, "metric_names", label=label),
        field="metric_names",
    )
    tolerance = _read(evaluator, "comparison_tolerance", label=label)
    if tolerance is not None and type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError(f"{label} comparison_tolerance must be ToleranceSpec or None")
    evaluate = _read(evaluator, "evaluate", label=label)
    if not callable(evaluate):
        raise _ContractViolationError(f"{label} evaluate must be callable")
    return _TradeEvaluatorPlan(
        origin,
        metrics,
        tolerance,
        _typing.cast("_Callable[[_TradeLog, _EquityCurve], object]", evaluate),
    )


def _validate_evaluator_columns(
    plan: _EvaluatorPlan,
    *,
    output_columns: tuple[str, ...],
    outcome_columns: tuple[str, ...],
) -> None:
    """Check both declared input surfaces without requiring computed rows."""
    label = f"evaluator {plan._origin.plugin_id!r}"
    _require_columns(
        required=plan._required_output_columns,
        available=output_columns,
        component_label=label,
        artifact_label="model output",
    )
    _require_columns(
        required=plan._required_outcome_columns,
        available=outcome_columns,
        component_label=label,
        artifact_label="outcome",
    )


def _execute_evaluator(
    evaluator: object,
    plan: _EvaluatorPlan,
    *,
    model_output: _ModelOutputFrame,
    outcome_targets: _OutcomeFrame,
) -> tuple[_MetricValue, ...]:
    """Evaluate two supplied artifacts without upstream execution authority."""
    _validate_artifacts(
        plan,
        model_output=model_output,
        outcome_targets=outcome_targets,
    )
    emitted = plan._evaluate(model_output, outcome_targets)
    if not isinstance(emitted, _Mapping) or tuple(emitted) != plan._metric_names:
        raise _ContractViolationError(
            "evaluator metric names and order must exactly match metric_names"
        )
    overlap = int(
        _np.intersect1d(
            model_output.keys,
            outcome_targets.keys,
            assume_unique=True,
        ).size
    )
    metrics: list[_MetricValue] = []
    for name in plan._metric_names:
        metric = emitted[name]
        if type(metric) is not _MetricValue or metric.name != name:
            raise _ContractViolationError(
                f"evaluator metric {name!r} must be an exact matching MetricValue"
            )
        if metric.effective_samples > overlap:
            raise _ContractViolationError(
                f"evaluator metric {name!r} effective sample count exceeds aligned rows"
            )
        metrics.append(metric)
    if not _same_evaluator_plan(plan, _snapshot_evaluator(evaluator)):
        raise _ContractViolationError(
            f"evaluator {plan._origin.plugin_id!r} declaration changed during execution"
        )
    return tuple(metrics)


def _execute_trade_evaluator(
    evaluator: object,
    plan: _TradeEvaluatorPlan,
    *,
    trade_log: _TradeLog,
    equity_curve: _EquityCurve,
) -> tuple[_MetricValue, ...]:
    """Evaluate supplied Plane-B artifacts without upstream execution authority."""
    if type(trade_log) is not _TradeLog:
        raise _ContractViolationError("trade evaluator requires an exact TradeLog")
    if type(equity_curve) is not _EquityCurve:
        raise _ContractViolationError("trade evaluator requires an exact EquityCurve")
    if (
        trade_log.unit is not equity_curve.unit
        or trade_log.clock_domain != equity_curve.clock_domain
        or trade_log.source_identities != equity_curve.source_identities
        or trade_log.fold_index != equity_curve.fold_index
        or trade_log.policy_origin != equity_curve.policy_origin
        or trade_log.execution_rows != equity_curve.num_rows
    ):
        raise _ContractViolationError(
            "TradeLog and EquityCurve identity and axis metadata must exactly match"
        )
    emitted = plan._evaluate(trade_log, equity_curve)
    if not isinstance(emitted, _Mapping) or tuple(emitted) != plan._metric_names:
        raise _ContractViolationError(
            "trade evaluator metric names and order must exactly match metric_names"
        )
    metrics: list[_MetricValue] = []
    for name in plan._metric_names:
        metric = emitted[name]
        if type(metric) is not _MetricValue or metric.name != name:
            raise _ContractViolationError(
                f"trade evaluator metric {name!r} must be an exact matching MetricValue"
            )
        if metric.effective_samples > equity_curve.num_rows:
            raise _ContractViolationError(
                f"trade evaluator metric {name!r} effective sample count exceeds rows"
            )
        metrics.append(metric)
    if not _same_trade_evaluator_plan(plan, _snapshot_trade_evaluator(evaluator)):
        raise _ContractViolationError(
            f"trade evaluator {plan._origin.plugin_id!r} declaration changed during execution"
        )
    return tuple(metrics)


def _validate_artifacts(
    plan: _EvaluatorPlan,
    *,
    model_output: object,
    outcome_targets: object,
) -> None:
    if type(model_output) is not _ModelOutputFrame:
        raise _ContractViolationError("evaluator requires an exact ModelOutputFrame")
    if type(outcome_targets) is not _OutcomeFrame:
        raise _ContractViolationError("evaluator requires an exact OutcomeFrame")
    _validate_evaluator_columns(
        plan,
        output_columns=model_output.column_names,
        outcome_columns=outcome_targets.column_names,
    )
    if (
        model_output.unit is not outcome_targets.unit
        or model_output.clock_domain != outcome_targets.clock_domain
        or model_output.source_identities != outcome_targets.source_identities
    ):
        raise _ContractViolationError("evaluation artifact axis tags must exactly match")
    if (
        model_output.num_rows
        and not _np.isin(
            model_output.keys,
            outcome_targets.keys,
            assume_unique=True,
        ).all()
    ):
        raise _ContractViolationError("ModelOutputFrame keys must be a subset of OutcomeFrame keys")


def _same_evaluator_plan(left: _EvaluatorPlan, right: _EvaluatorPlan) -> bool:
    return (
        left._origin.plugin_id == right._origin.plugin_id
        and left._origin.version == right._origin.version
        and _canonical_params_json(left._origin.params)
        == _canonical_params_json(right._origin.params)
        and left._required_output_columns == right._required_output_columns
        and left._required_outcome_columns == right._required_outcome_columns
        and left._metric_names == right._metric_names
        and left._comparison_tolerance == right._comparison_tolerance
    )


def _same_trade_evaluator_plan(
    left: _TradeEvaluatorPlan,
    right: _TradeEvaluatorPlan,
) -> bool:
    return (
        left._origin.plugin_id == right._origin.plugin_id
        and left._origin.version == right._origin.version
        and _canonical_params_json(left._origin.params)
        == _canonical_params_json(right._origin.params)
        and left._metric_names == right._metric_names
        and left._comparison_tolerance == right._comparison_tolerance
    )


def _names(value: object, *, field: str) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        raise _ContractViolationError(f"{field} must be a non-empty exact tuple")
    names = _typing.cast("tuple[object, ...]", value)
    if any(type(name) is not str or not name for name in names):
        raise _ContractViolationError(f"{field} must contain only non-empty strings")
    if len(set(names)) != len(names):
        raise _ContractViolationError(f"{field} must contain unique names")
    return _typing.cast("tuple[str, ...]", names)


def _read(instance: object, name: str, *, label: str) -> object:
    try:
        return getattr(instance, name)
    except Exception as exc:
        raise _ContractViolationError(f"cannot read {label} {name}") from exc
