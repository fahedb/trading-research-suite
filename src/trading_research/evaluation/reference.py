"""Two closed-semantics reference evaluators for Plane-A forecasts."""

from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import MetricValue as _MetricValue
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.evaluation.registry import EVALUATORS as _EVALUATORS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
    from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
    from trading_research.core.research_frames import OutcomeFrame as _OutcomeFrame

__all__ = ["RankIC", "SignAccuracy"]


class RankIC:
    """Compute exact finite-pair Spearman correlation with average tie ranks."""

    __slots__ = ("_min_effective_samples", "_outcome_column", "_output_column")

    def __init__(
        self,
        *,
        output_column: str,
        outcome_column: str,
        min_effective_samples: int,
    ) -> None:
        _validate_parameters(
            output_column=output_column,
            outcome_column=outcome_column,
            min_effective_samples=min_effective_samples,
            minimum=2,
        )
        self._output_column = output_column
        self._outcome_column = outcome_column
        self._min_effective_samples = min_effective_samples

    @property
    def evaluator_id(self) -> str:
        """Return the stable evaluator identity."""
        return "rank_ic_v1"

    @property
    def version(self) -> int:
        """Return the evaluator contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return all explicit identity-bearing parameters."""
        return {
            "min_effective_samples": self._min_effective_samples,
            "outcome_column": self._outcome_column,
            "output_column": self._output_column,
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the sole forecast input column."""
        return (self._output_column,)

    @property
    def required_outcome_columns(self) -> tuple[str, ...]:
        """Return the sole outcome input column."""
        return (self._outcome_column,)

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return the sole emitted metric name."""
        return ("rank_ic",)

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def evaluate(
        self,
        model_output: _ModelOutputFrame,
        outcome_targets: _OutcomeFrame,
    ) -> _Mapping[str, _MetricValue]:
        """Compute finite-pair Spearman rank correlation."""
        output, outcome = _aligned_columns(
            model_output,
            outcome_targets,
            output_column=self._output_column,
            outcome_column=self._outcome_column,
        )
        included = _np.isfinite(output) & _np.isfinite(outcome)
        left = output[included]
        right = outcome[included]
        effective = int(left.size)
        value = _np.nan
        if effective >= self._min_effective_samples:
            value = _rank_correlation(left, right)
        return {"rank_ic": _MetricValue("rank_ic", float(value), effective)}


class SignAccuracy:
    """Compute exact finite non-zero sign agreement."""

    __slots__ = ("_min_effective_samples", "_outcome_column", "_output_column")

    def __init__(
        self,
        *,
        output_column: str,
        outcome_column: str,
        min_effective_samples: int,
    ) -> None:
        _validate_parameters(
            output_column=output_column,
            outcome_column=outcome_column,
            min_effective_samples=min_effective_samples,
            minimum=1,
        )
        self._output_column = output_column
        self._outcome_column = outcome_column
        self._min_effective_samples = min_effective_samples

    @property
    def evaluator_id(self) -> str:
        """Return the stable evaluator identity."""
        return "sign_accuracy_v1"

    @property
    def version(self) -> int:
        """Return the evaluator contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return all explicit identity-bearing parameters."""
        return {
            "min_effective_samples": self._min_effective_samples,
            "outcome_column": self._outcome_column,
            "output_column": self._output_column,
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the sole forecast input column."""
        return (self._output_column,)

    @property
    def required_outcome_columns(self) -> tuple[str, ...]:
        """Return the sole outcome input column."""
        return (self._outcome_column,)

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return the sole emitted metric name."""
        return ("sign_accuracy",)

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def evaluate(
        self,
        model_output: _ModelOutputFrame,
        outcome_targets: _OutcomeFrame,
    ) -> _Mapping[str, _MetricValue]:
        """Compute finite non-zero sign agreement."""
        output, outcome = _aligned_columns(
            model_output,
            outcome_targets,
            output_column=self._output_column,
            outcome_column=self._outcome_column,
        )
        included = (
            _np.isfinite(output)
            & _np.isfinite(outcome)
            & (output != 0.0)
            & (outcome != 0.0)
        )
        left = output[included]
        right = outcome[included]
        effective = int(left.size)
        value = _np.nan
        if effective >= self._min_effective_samples:
            matches = _np.count_nonzero((left > 0.0) == (right > 0.0))
            value = float(matches / effective)
        return {
            "sign_accuracy": _MetricValue(
                "sign_accuracy",
                float(value),
                effective,
            )
        }


def _aligned_columns(
    model_output: _ModelOutputFrame,
    outcome_targets: _OutcomeFrame,
    *,
    output_column: str,
    outcome_column: str,
) -> tuple[_npt.NDArray[_np.generic], _npt.NDArray[_np.generic]]:
    _keys, output_positions, outcome_positions = _np.intersect1d(
        model_output.keys,
        outcome_targets.keys,
        assume_unique=True,
        return_indices=True,
    )
    return (
        model_output.column(output_column)[output_positions],
        outcome_targets.column(outcome_column)[outcome_positions],
    )


def _average_ranks(values: _npt.NDArray[_np.generic]) -> _npt.NDArray[_np.float64]:
    order = _np.argsort(values, kind="mergesort")
    ordered = values[order]
    ranks = _np.empty(values.size, dtype=_np.float64)
    start = 0
    while start < values.size:
        stop = start + 1
        while stop < values.size and ordered[stop] == ordered[start]:
            stop += 1
        ranks[order[start:stop]] = (start + 1 + stop) / 2.0
        start = stop
    return ranks


def _rank_correlation(
    left: _npt.NDArray[_np.generic],
    right: _npt.NDArray[_np.generic],
) -> float:
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_centered = left_ranks - left_ranks.mean(dtype=_np.float64)
    right_centered = right_ranks - right_ranks.mean(dtype=_np.float64)
    left_ss = float(_np.dot(left_centered, left_centered))
    right_ss = float(_np.dot(right_centered, right_centered))
    denominator = float(_np.sqrt(left_ss * right_ss))
    if denominator == 0.0:
        return float("nan")
    value = float(_np.dot(left_centered, right_centered) / denominator)
    return min(1.0, max(-1.0, value))


def _validate_parameters(
    *,
    output_column: object,
    outcome_column: object,
    min_effective_samples: object,
    minimum: int,
) -> None:
    for value, label in (
        (output_column, "output_column"),
        (outcome_column, "outcome_column"),
    ):
        if type(value) is not str or not value:
            raise _ContractViolationError(f"{label} must be a non-empty string")
    if type(min_effective_samples) is not int or min_effective_samples < minimum:
        raise _ContractViolationError(
            f"min_effective_samples must be an exact int >= {minimum}"
        )


_EVALUATORS.add("rank_ic_v1", RankIC)
_EVALUATORS.add("sign_accuracy_v1", SignAccuracy)
