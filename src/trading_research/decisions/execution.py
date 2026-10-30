"""Validated declaration snapshot and execution for decision policies."""

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
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import _is_reserved_outcome_name
from trading_research.core.research_frames import _INTENT_SCHEMA_IDS
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame

__all__: list[str] = []


@_dataclass(frozen=True, slots=True)
class _DecisionPlan:
    _origin: _StageOrigin
    _required_output_columns: tuple[str, ...]
    _intent_schema_id: str
    _decide: _Callable[[_ModelOutputFrame], object]


def _snapshot_policy(policy: object) -> _DecisionPlan:
    """Read and validate one complete policy declaration exactly once."""
    policy_id = _read(policy, "policy_id", label="decision policy")
    if type(policy_id) is not str or not policy_id:
        raise _ContractViolationError("policy_id must be a non-empty string")
    label = f"decision policy {policy_id!r}"
    version = _read(policy, "version", label=label)
    params = _read(policy, "params", label=label)
    if type(version) is not int or version <= 0:
        raise _ContractViolationError(f"{label} version must be a positive Python int")
    if not isinstance(params, _Mapping):
        raise _ContractViolationError(f"{label} params must be a mapping")
    origin = _StageOrigin(
        policy_id,
        version,
        _typing.cast("_Mapping[str, _ParamValue]", params),
    )
    required = _names(
        _read(policy, "required_output_columns", label=label),
        field="required_output_columns",
    )
    if any(_is_reserved_outcome_name(name) for name in required):
        raise _ContractViolationError(
            "decision policy inputs cannot use the reserved target namespace"
        )
    intent_schema_id = _read(policy, "intent_schema_id", label=label)
    if type(intent_schema_id) is not str or intent_schema_id not in _INTENT_SCHEMA_IDS:
        raise _ContractViolationError(
            f"{label} intent_schema_id must be one of {sorted(_INTENT_SCHEMA_IDS)!r}"
        )
    decide = _read(policy, "decide", label=label)
    if not callable(decide):
        raise _ContractViolationError(f"{label} decide must be callable")
    return _DecisionPlan(
        origin,
        required,
        intent_schema_id,
        _typing.cast("_Callable[[_ModelOutputFrame], object]", decide),
    )


def _validate_policy_compatibility(
    plan: _DecisionPlan,
    model_output: _ModelOutputFrame,
) -> None:
    """Refuse a missing declared input before invoking any policy."""
    if type(model_output) is not _ModelOutputFrame:
        raise _ContractViolationError("policy input must be an exact ModelOutputFrame")
    _validate_policy_columns(plan, model_output.column_names)


def _validate_policy_columns(
    plan: _DecisionPlan,
    available_columns: tuple[str, ...],
) -> None:
    """Check declared compatibility without requiring computed output rows."""
    _require_columns(
        required=plan._required_output_columns,
        available=available_columns,
        component_label=f"decision policy {plan._origin.plugin_id!r}",
        artifact_label="model output",
    )


def _execute_policy(
    policy: object,
    plan: _DecisionPlan,
    model_output: _ModelOutputFrame,
) -> _IntentFrame:
    """Execute one prevalidated policy and verify its complete result."""
    result = plan._decide(model_output)
    if type(result) is not _IntentFrame:
        raise _ContractViolationError("decision policy must return an exact IntentFrame")
    if result.intent_schema_id != plan._intent_schema_id:
        raise _ContractViolationError(
            "decision policy result schema differs from its declaration"
        )
    if (
        result.unit is not model_output.unit
        or result.clock_domain != model_output.clock_domain
        or (
            result.num_rows
            and not _np.isin(
                result.keys,
                model_output.keys,
                assume_unique=True,
            ).all()
        )
    ):
        raise _ContractViolationError(
            "IntentFrame axis must be a subset of its ModelOutputFrame axis"
        )
    if not _same_policy_plan(plan, _snapshot_policy(policy)):
        raise _ContractViolationError(
            f"decision policy {plan._origin.plugin_id!r} declaration changed during execution"
        )
    return result


def _same_policy_plan(left: _DecisionPlan, right: _DecisionPlan) -> bool:
    return (
        left._origin.plugin_id == right._origin.plugin_id
        and left._origin.version == right._origin.version
        and _canonical_params_json(left._origin.params)
        == _canonical_params_json(right._origin.params)
        and left._required_output_columns == right._required_output_columns
        and left._intent_schema_id == right._intent_schema_id
    )


def _names(value: object, *, field: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _ContractViolationError(f"{field} must be an exact tuple")
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
