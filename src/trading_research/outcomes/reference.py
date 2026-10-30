"""The single V1 OutcomeSpec: an observed-row forward log return."""

from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import BoundaryPolicy as _BoundaryPolicy
from trading_research.core.research import OutcomeRequest as _OutcomeRequest
from trading_research.core.research import ToleranceSpec as _ToleranceSpec

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue

__all__ = ["ForwardLogReturn"]

_TARGET_COLUMN = "target.forward_log_return"


class ForwardLogReturn:
    """Natural-log price return over an explicit complete observed-row horizon."""

    def __init__(
        self,
        *,
        horizon_rows: int,
        decision_delay_rows: int,
        reference_price_column: str,
        boundary_policy: _BoundaryPolicy,
    ) -> None:
        if type(horizon_rows) is not int or horizon_rows <= 0:
            raise _ContractViolationError("horizon_rows must be a positive Python int")
        if type(decision_delay_rows) is not int or decision_delay_rows != 1:
            raise _ContractViolationError("V1 decision_delay_rows must be exactly 1")
        if type(reference_price_column) is not str or not reference_price_column:
            raise _ContractViolationError("reference_price_column must be a non-empty string")
        if reference_price_column == "target" or reference_price_column.startswith("target."):
            raise _ContractViolationError(
                "reference_price_column cannot use the reserved target namespace"
            )
        if (
            type(boundary_policy) is not _BoundaryPolicy
            or boundary_policy is not _BoundaryPolicy.NAN
        ):
            raise _ContractViolationError("V1 boundary_policy must be BoundaryPolicy.NAN")
        self._horizon_rows = horizon_rows
        self._decision_delay_rows = decision_delay_rows
        self._reference_price_column = reference_price_column
        self._boundary_policy = boundary_policy

    @property
    def outcome_id(self) -> str:
        """Return the stable V1 identity."""
        return "forward_log_return_v1"

    @property
    def version(self) -> int:
        """Return the outcome contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return all identity-bearing outcome choices with no hidden default."""
        return {
            "boundary_policy": self._boundary_policy.value,
            "decision_delay_rows": self._decision_delay_rows,
            "horizon_rows": self._horizon_rows,
            "reference_price_column": self._reference_price_column,
        }

    @property
    def horizon_rows(self) -> int:
        """Return the positive observed-row movement horizon."""
        return self._horizon_rows

    @property
    def decision_delay_rows(self) -> int:
        """Return the explicit one-row decision delay."""
        return self._decision_delay_rows

    @property
    def reference_price_column(self) -> str:
        """Return the explicit price input column."""
        return self._reference_price_column

    @property
    def boundary_policy(self) -> _BoundaryPolicy:
        """Return the V1 end-of-data policy."""
        return self._boundary_policy

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the sole market column this spec may read."""
        return (self._reference_price_column,)

    @property
    def target_column_names(self) -> tuple[str, ...]:
        """Return the sole reserved-namespace target column."""
        return (_TARGET_COLUMN,)

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison for the V1 reference outcome."""
        return None

    def compute(
        self,
        request: _OutcomeRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute complete horizons and emit NaN where the terminal row is absent."""
        if type(request) is not _OutcomeRequest:
            raise _ContractViolationError("request must be an exact OutcomeRequest")
        prices = request.column(self._reference_price_column)
        if not _np.issubdtype(prices.dtype, _np.number) or _np.issubdtype(
            prices.dtype, _np.complexfloating
        ):
            raise _ContractViolationError("reference price column must have a real numeric dtype")
        result = _np.full(request.decision_length, _np.nan, dtype=_np.float64)
        resolved_length = request.keys.size
        for local_position in range(request.decision_length):
            decision_position = request.decision_offset + local_position
            origin_position = decision_position + self._decision_delay_rows
            terminal_position = origin_position + self._horizon_rows
            if terminal_position >= resolved_length:
                continue
            origin = float(prices[origin_position])
            terminal = float(prices[terminal_position])
            if not _np.isfinite(origin) or not _np.isfinite(terminal):
                continue
            if origin <= 0.0 or terminal <= 0.0:
                raise _ContractViolationError(
                    "forward log return requires strictly positive endpoint prices"
                )
            result[local_position] = _np.log(terminal / origin)
        return {_TARGET_COLUMN: result}
