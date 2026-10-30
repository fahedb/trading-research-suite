"""Validation and execution shared by training-target and evaluation outcomes."""

from __future__ import annotations

import typing as _typing
from collections.abc import Mapping as _Mapping

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.interfaces import OutcomeSpec as _OutcomeSpec
from trading_research.core.research import BoundaryPolicy as _BoundaryPolicy
from trading_research.core.research import OutcomeRequest as _OutcomeRequest
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import _is_reserved_outcome_name

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

__all__: list[str] = []


def _validated_spec(spec: _OutcomeSpec) -> _OutcomeSpec:
    if not isinstance(spec, _OutcomeSpec):
        raise _ContractViolationError("outcome component does not satisfy OutcomeSpec")
    if type(spec.outcome_id) is not str or not spec.outcome_id:
        raise _ContractViolationError("outcome_id must be a non-empty string")
    if type(spec.version) is not int or spec.version <= 0:
        raise _ContractViolationError("outcome version must be a positive Python int")
    _canonical_params_json(spec.params)
    if type(spec.horizon_rows) is not int or spec.horizon_rows <= 0:
        raise _ContractViolationError("horizon_rows must be a positive Python int")
    if type(spec.decision_delay_rows) is not int or spec.decision_delay_rows != 1:
        raise _ContractViolationError("V1 decision_delay_rows must be exactly 1")
    if type(spec.reference_price_column) is not str or not spec.reference_price_column:
        raise _ContractViolationError("reference_price_column must be a non-empty string")
    if type(spec.boundary_policy) is not _BoundaryPolicy:
        raise _ContractViolationError("boundary_policy must be an exact BoundaryPolicy")
    required = spec.required_columns
    targets = spec.target_column_names
    for field, values in (("required_columns", required), ("target_column_names", targets)):
        if type(values) is not tuple or not values:
            raise _ContractViolationError(f"{field} must be a non-empty exact tuple")
        if any(type(name) is not str or not name for name in values) or len(set(values)) != len(
            values
        ):
            raise _ContractViolationError(f"{field} must contain unique non-empty strings")
    if any(_is_reserved_outcome_name(name) for name in required):
        raise _ContractViolationError("OutcomeSpec inputs cannot use the reserved target namespace")
    if any(not _is_reserved_outcome_name(name) for name in targets):
        raise _ContractViolationError("OutcomeSpec outputs must use the reserved target namespace")
    tolerance = spec.comparison_tolerance
    if tolerance is not None and type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError("comparison_tolerance must be ToleranceSpec or None")
    return spec


def _execute_outcome(
    spec: _OutcomeSpec,
    request: _OutcomeRequest,
) -> tuple[_Mapping[str, _npt.NDArray[_np.float64]], _Mapping[str, _StageOrigin]]:
    validated = _validated_spec(spec)
    if type(request) is not _OutcomeRequest:
        raise _ContractViolationError("request must be an exact OutcomeRequest")
    emitted = validated.compute(request)
    if not isinstance(emitted, _Mapping) or tuple(emitted) != validated.target_column_names:
        raise _ContractViolationError(
            "OutcomeSpec output names and order must exactly match target_column_names"
        )
    columns: dict[str, _npt.NDArray[_np.float64]] = {}
    origin = _StageOrigin(validated.outcome_id, validated.version, validated.params)
    origins: dict[str, _StageOrigin] = {}
    for name in validated.target_column_names:
        values = emitted[name]
        if type(values) is not _np.ndarray or values.ndim != 1:
            raise _ContractViolationError(f"outcome column {name!r} must be one-dimensional")
        if values.dtype != _np.dtype(_np.float64) or values.dtype.metadata is not None:
            raise _ContractViolationError(f"outcome column {name!r} must have exact dtype float64")
        if values.shape[0] != request.decision_length:
            raise _ContractViolationError(
                f"outcome column {name!r} length must equal decision_length"
            )
        if _np.isinf(values).any():
            raise _ContractViolationError(f"outcome column {name!r} contains infinity")
        columns[name] = values.copy()
        origins[name] = origin
    return columns, origins


def _forward_rows(spec: _OutcomeSpec) -> int:
    """Return the exact declared future reach consumed by purge derivation."""
    validated = _validated_spec(spec)
    return validated.decision_delay_rows + validated.horizon_rows


def _target_end_position(decision_position: int, spec: _OutcomeSpec) -> int:
    """Return the exact resolved-axis terminal position for one decision."""
    if type(decision_position) is not int or decision_position < 0:
        raise _ContractViolationError("decision_position must be a non-negative Python int")
    return decision_position + _forward_rows(spec)
