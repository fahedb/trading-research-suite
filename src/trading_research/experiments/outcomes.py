"""Composition-root adaptation for platform-controlled outcome construction."""

from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.frames import MarketFrame as _MarketFrame
from trading_research.core.research import OutcomeRequest as _OutcomeRequest
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research import _make_outcome_request
from trading_research.core.research_frames import OutcomeFrame as _OutcomeFrame
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame
from trading_research.core.research_frames import _make_outcome_frame, _make_training_target_frame
from trading_research.outcomes.execution import _execute_outcome, _validated_spec

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.interfaces import OutcomeSpec as _OutcomeSpec

__all__ = ["compute_outcomes", "compute_training_targets"]


def _request_for_view(
    source_frame: _MarketFrame,
    view: _ViewSpan,
    spec: _OutcomeSpec,
) -> _OutcomeRequest:
    validated_spec = _validated_spec(spec)
    if type(source_frame) is not _MarketFrame:
        raise _ContractViolationError("source_frame must be an exact MarketFrame")
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("view must be an exact ViewSpan")
    if source_frame.unit is not view.unit or source_frame.clock_domain != view.clock_domain:
        raise _ContractViolationError("source_frame and ViewSpan must share one native axis")
    source_identity = source_frame.dataset_ref.canonical_identity
    if source_identity not in view.source_identities:
        raise _ContractViolationError("source_frame identity is absent from the ViewSpan")
    keys = source_frame.keys
    start = int(_np.searchsorted(keys, view.eval_start.value, side="left"))
    stop = int(_np.searchsorted(keys, view.eval_end.value, side="right"))
    if (
        start >= keys.size
        or stop <= start
        or int(keys[start]) != view.eval_start.value
        or int(keys[stop - 1]) != view.eval_end.value
    ):
        raise _ContractViolationError("ViewSpan evaluation endpoints must be observed source keys")
    return _make_outcome_request(
        source_frame,
        required_columns=validated_spec.required_columns,
        decision_offset=start,
        decision_length=stop - start,
    )


def _computed(
    source_frame: _MarketFrame,
    view: _ViewSpan,
    spec: _OutcomeSpec,
) -> tuple[
    _npt.NDArray[_np.int64],
    _Mapping[str, _npt.NDArray[_np.float64]],
    _Mapping[str, _typing.Any],
]:
    request = _request_for_view(source_frame, view, spec)
    columns, origins = _execute_outcome(spec, request)
    start = request.decision_offset
    stop = start + request.decision_length
    return request.keys[start:stop], columns, origins


def compute_training_targets(
    source_frame: _MarketFrame,
    view: _ViewSpan,
    spec: _OutcomeSpec,
) -> _TrainingTargetFrame:
    """Compute one platform-minted fit-time target carrier."""
    keys, columns, origins = _computed(source_frame, view, spec)
    return _make_training_target_frame(
        view=view,
        keys=keys,
        columns=columns,
        origins=origins,
    )


def compute_outcomes(
    source_frame: _MarketFrame,
    view: _ViewSpan,
    spec: _OutcomeSpec,
) -> _OutcomeFrame:
    """Compute one platform-minted Plane-A evaluation outcome carrier."""
    keys, columns, origins = _computed(source_frame, view, spec)
    return _make_outcome_frame(
        view=view,
        keys=keys,
        columns=columns,
        origins=origins,
    )
