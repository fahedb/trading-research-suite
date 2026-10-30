"""Deterministic reference policies for the two V1 intent schemas."""

from __future__ import annotations

import math as _math
import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import _make_intent_frame_from_model_output
from trading_research.decisions.registry import DECISION_POLICIES as _DECISION_POLICIES

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue

__all__ = [
    "AbstainPolicy",
    "AdaptiveBracketExposurePolicy",
    "BracketThresholdPolicy",
    "EventExposurePolicy",
    "ThresholdPolicy",
    "UlsJointBracketExposurePolicy",
    "ZoneAdaptiveBracketExposurePolicy",
    "ZoneFixedBracketExposurePolicy",
]

_INTENT_SCHEMA_ID = "target_exposure_intent_v1"
_BRACKETED_INTENT_SCHEMA_ID = "bracketed_exposure_intent_v1"


class ThresholdPolicy:
    """Map one generic scalar output to short, flat, or long exposure."""

    __slots__ = ("_output_column", "_threshold")

    def __init__(self, *, output_column: str, threshold: float) -> None:
        if type(output_column) is not str or not output_column:
            raise _ContractViolationError("threshold output_column must be non-empty")
        if type(threshold) is not float or not _math.isfinite(threshold) or threshold < 0.0:
            raise _ContractViolationError(
                "threshold must be a finite non-negative Python float"
            )
        self._output_column = output_column
        self._threshold = threshold

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "threshold_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the complete identity-bearing threshold parameters."""
        return {
            "output_column": self._output_column,
            "threshold": self._threshold,
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the sole generic model-output input."""
        return (self._output_column,)

    @property
    def intent_schema_id(self) -> str:
        """Return the sole V1 intent schema identifier."""
        return _INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Apply a symmetric threshold while preserving explicit NaN abstention."""
        values = model_output.column(self._output_column)
        finite = _np.isfinite(values)
        exposure = _np.full(values.shape, _np.nan, dtype=_np.float64)
        exposure[finite] = 0.0
        exposure[finite & (values > self._threshold)] = 1.0
        exposure[finite & (values < -self._threshold)] = -1.0
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys,
            target_exposure=exposure,
        )


class AbstainPolicy:
    """Emit an explicit abstention at every model-output row."""

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "abstain_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing parameter mapping."""
        return {}

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Declare that the policy reads no model values."""
        return ()

    @property
    def intent_schema_id(self) -> str:
        """Return the sole V1 intent schema identifier."""
        return _INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Preserve sparse row presence while making every row an abstention."""
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys,
            target_exposure=_np.full(model_output.num_rows, _np.nan, dtype=_np.float64),
        )


class EventExposurePolicy:
    """Emit sparse unit exposure only at finite non-zero score rows."""

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "event_exposure_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing parameter mapping."""
        return {}

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the sole generic model-output input."""
        return ("score",)

    @property
    def intent_schema_id(self) -> str:
        """Return the sole V1 intent schema identifier."""
        return _INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Map event scores to sparse short or long unit exposure."""
        values = model_output.column("score")
        selected = _np.isfinite(values) & (values != 0.0)
        exposure = _np.where(values[selected] > 0.0, 1.0, -1.0).astype(_np.float64)
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys[selected],
            target_exposure=exposure,
        )

def _validated_distance(value: object, *, field: str) -> float | None:
    """Accept a finite positive price-unit distance, or None for no bracket."""
    if value is None:
        return None
    if type(value) is not float or not _math.isfinite(value) or value <= 0.0:
        raise _ContractViolationError(
            f"{field} must be a finite positive Python float, or None for no bracket"
        )
    return value


class ZoneFixedBracketExposurePolicy:
    """Zone Trader exposure carrying one fixed, configured bracket per trade.

    The exposure decision is deliberately identical to ``event_exposure_v1``:
    the same sparse selection, the same signs, the same keys. The only thing
    added is that each opening instruction also names a take-profit and a
    stop-loss distance, so the bracketed intent path can be exercised end to end
    without changing a single signal.

    The distances are **fixed identity-bearing parameters in price units**, not a
    stop model. They are what makes this a plumbing qualification rather than a
    strategy: the real Zone Trader stop and target are per-event quantities the
    provider already computes, and reaching them is separate future work. Price
    units are used because converting pips would require ``price_units_per_pip``,
    which belongs to the execution cost binding and has no business in a
    decision policy.
    """

    __slots__ = ("_stop_loss_distance", "_take_profit_distance")

    def __init__(
        self,
        *,
        take_profit_distance: float | None,
        stop_loss_distance: float | None,
    ) -> None:
        self._take_profit_distance = _validated_distance(
            take_profit_distance,
            field="take_profit_distance",
        )
        self._stop_loss_distance = _validated_distance(
            stop_loss_distance,
            field="stop_loss_distance",
        )

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "zone_fixed_bracket_exposure_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the complete identity-bearing bracket parameters."""
        return {
            "stop_loss_distance": self._stop_loss_distance,
            "take_profit_distance": self._take_profit_distance,
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the sole generic model-output input, as event_exposure_v1 does."""
        return ("score",)

    @property
    def intent_schema_id(self) -> str:
        """Return the bracketed intent schema identifier."""
        return _BRACKETED_INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Map event scores to sparse unit exposure, each with its fixed bracket.

        Every emitted row is an opening instruction — the selection admits only
        finite non-zero scores, which map to exactly +1 or -1 — so every row
        carries the configured distances and none is left to hold state.
        """
        values = model_output.column("score")
        selected = _np.isfinite(values) & (values != 0.0)
        exposure = _np.where(values[selected] > 0.0, 1.0, -1.0).astype(_np.float64)
        rows = int(exposure.shape[0])
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys[selected],
            target_exposure=exposure,
            take_profit_distance=_np.full(
                rows,
                _np.nan if self._take_profit_distance is None else self._take_profit_distance,
                dtype=_np.float64,
            ),
            stop_loss_distance=_np.full(
                rows,
                _np.nan if self._stop_loss_distance is None else self._stop_loss_distance,
                dtype=_np.float64,
            ),
        )


class ZoneAdaptiveBracketExposurePolicy:
    """Choose one configured bracket distance from three generic model values."""

    __slots__ = ("_distances", "_stop_loss_distance")

    def __init__(
        self,
        *,
        stop_loss_distance: float,
        value_0_distance: float,
        value_1_distance: float,
        value_2_distance: float,
    ) -> None:
        stop = _validated_distance(stop_loss_distance, field="stop_loss_distance")
        distances = tuple(
            _validated_distance(value, field=f"value_{index}_distance")
            for index, value in enumerate(
                (value_0_distance, value_1_distance, value_2_distance)
            )
        )
        if stop is None or any(value is None for value in distances):
            raise _ContractViolationError("adaptive Zone brackets require four distances")
        self._stop_loss_distance = stop
        self._distances = _typing.cast("tuple[float, float, float]", distances)

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "zone_adaptive_bracket_exposure_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the fixed alternative-to-distance mapping and stop."""
        return {
            "stop_loss_distance": self._stop_loss_distance,
            "value_0_distance": self._distances[0],
            "value_1_distance": self._distances[1],
            "value_2_distance": self._distances[2],
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the event score and three generic choice values."""
        return ("score", "value_0", "value_1", "value_2")

    @property
    def intent_schema_id(self) -> str:
        """Return the bracketed intent schema identifier."""
        return _BRACKETED_INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Emit one opening instruction per row with a stable first-arm tie-break."""
        score = model_output.column("score")
        alternatives = _np.column_stack(
            tuple(model_output.column(f"value_{index}") for index in range(3))
        )
        if (
            not _np.isfinite(score).all()
            or not _np.isin(score, (-1.0, 1.0)).all()
            or not _np.isfinite(alternatives).all()
        ):
            raise _ContractViolationError("adaptive Zone model output contains a malformed event")
        selected = _np.argmax(alternatives, axis=1)
        distances = _np.asarray(self._distances, dtype=_np.float64)[selected]
        exposure = _np.where(score > 0.0, 1.0, -1.0).astype(_np.float64)
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys,
            target_exposure=exposure,
            take_profit_distance=distances,
            stop_loss_distance=_np.full(
                score.shape,
                self._stop_loss_distance,
                dtype=_np.float64,
            ),
        )


class AdaptiveBracketExposurePolicy:
    """Choose one of seven configured bracket distances for each gated event."""

    __slots__ = ("_distances", "_stop_loss_distance")

    def __init__(
        self,
        *,
        stop_loss_distance: float,
        value_0_distance: float,
        value_1_distance: float,
        value_2_distance: float,
        value_3_distance: float,
        value_4_distance: float,
        value_5_distance: float,
        value_6_distance: float,
    ) -> None:
        stop = _validated_distance(stop_loss_distance, field="stop_loss_distance")
        raw_distances = (
            value_0_distance,
            value_1_distance,
            value_2_distance,
            value_3_distance,
            value_4_distance,
            value_5_distance,
            value_6_distance,
        )
        distances = tuple(
            _validated_distance(value, field=f"value_{index}_distance")
            for index, value in enumerate(raw_distances)
        )
        if stop is None or any(value is None for value in distances):
            raise _ContractViolationError("adaptive brackets require seven distances and one stop")
        self._stop_loss_distance = stop
        self._distances = _typing.cast("tuple[float, ...]", distances)

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "adaptive_bracket_exposure_v1"

    @property
    def version(self) -> int:
        """Return the stable contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the fixed alternative mapping and stop distance."""
        return {
            "stop_loss_distance": self._stop_loss_distance,
            **{
                f"value_{index}_distance": distance
                for index, distance in enumerate(self._distances)
            },
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Consume one gated event and seven generic alternative values."""
        return ("score", *(f"value_{index}" for index in range(7)))

    @property
    def intent_schema_id(self) -> str:
        """Return the bracketed intent schema identifier."""
        return _BRACKETED_INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Emit only accepted events with a deterministic first-arm tie-break."""
        score = model_output.column("score")
        selected = _np.flatnonzero(_np.isfinite(score) & (score != 0.0))
        accepted = score[selected]
        alternatives = _np.column_stack(
            tuple(model_output.column(f"value_{index}")[selected] for index in range(7))
        )
        if not _np.isin(accepted, (-1.0, 1.0)).all() or not _np.isfinite(
            alternatives
        ).all():
            raise _ContractViolationError("adaptive model output contains a malformed event")
        chosen = _np.argmax(alternatives, axis=1)
        distances = _np.asarray(self._distances, dtype=_np.float64)[chosen]
        exposure = accepted.astype(_np.float64, copy=True)
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys[selected],
            target_exposure=exposure,
            take_profit_distance=distances,
            stop_loss_distance=_np.full(
                exposure.shape,
                self._stop_loss_distance,
                dtype=_np.float64,
            ),
        )


class UlsJointBracketExposurePolicy:
    """Choose one of four TP distances for each joint-model accepted event."""

    __slots__ = ("_distances", "_stop_loss_distance")

    def __init__(
        self,
        *,
        stop_loss_distance: float,
        value_0_distance: float,
        value_1_distance: float,
        value_2_distance: float,
        value_3_distance: float,
    ) -> None:
        stop = _validated_distance(stop_loss_distance, field="stop_loss_distance")
        distances = tuple(
            _validated_distance(value, field=f"value_{index}_distance")
            for index, value in enumerate(
                (value_0_distance, value_1_distance, value_2_distance, value_3_distance)
            )
        )
        if stop is None or any(value is None for value in distances):
            raise _ContractViolationError("joint ULS brackets require four TP distances and stop")
        self._stop_loss_distance = stop
        self._distances = _typing.cast("tuple[float, float, float, float]", distances)

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "uls_joint_bracket_exposure_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the fixed TP mapping and stop distance."""
        return {
            "stop_loss_distance": self._stop_loss_distance,
            **{
                f"value_{index}_distance": distance
                for index, distance in enumerate(self._distances)
            },
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Consume one gated event and four predicted action values."""
        return ("score", "value_0", "value_1", "value_2", "value_3")

    @property
    def intent_schema_id(self) -> str:
        """Return the bracketed intent schema identifier."""
        return _BRACKETED_INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Emit accepted events with a deterministic lowest-TP tie break."""
        score = model_output.column("score")
        selected = _np.flatnonzero(_np.isfinite(score) & (score != 0.0))
        accepted = score[selected]
        alternatives = _np.column_stack(
            tuple(model_output.column(f"value_{index}")[selected] for index in range(4))
        )
        if not _np.isin(accepted, (-1.0, 1.0)).all() or not _np.isfinite(
            alternatives
        ).all():
            raise _ContractViolationError("joint ULS model output contains a malformed event")
        chosen = _np.argmax(alternatives, axis=1)
        distances = _np.asarray(self._distances, dtype=_np.float64)[chosen]
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys[selected],
            target_exposure=accepted.astype(_np.float64, copy=True),
            take_profit_distance=distances,
            stop_loss_distance=_np.full(
                accepted.shape,
                self._stop_loss_distance,
                dtype=_np.float64,
            ),
        )


class BracketThresholdPolicy:
    """Threshold exposure carrying one immutable per-trade bracket instruction.

    The bracket distances are read from generic model-output columns the caller
    names. Nothing here interprets a broker unit: the columns are distances in
    price units, which is what keeps cost-binding semantics out of the decision
    layer entirely. Naming no column for a side leaves that side to the run's
    fixed execution configuration.
    """

    __slots__ = ("_output_column", "_stop_loss_column", "_take_profit_column", "_threshold")

    def __init__(
        self,
        *,
        output_column: str,
        threshold: float,
        take_profit_column: str | None = None,
        stop_loss_column: str | None = None,
    ) -> None:
        if type(output_column) is not str or not output_column:
            raise _ContractViolationError("threshold output_column must be non-empty")
        if type(threshold) is not float or not _math.isfinite(threshold) or threshold < 0.0:
            raise _ContractViolationError("threshold must be a finite non-negative Python float")
        for name, label in (
            (take_profit_column, "take_profit_column"),
            (stop_loss_column, "stop_loss_column"),
        ):
            if name is not None and (type(name) is not str or not name):
                raise _ContractViolationError(f"{label} must be a non-empty string or None")
        self._output_column = output_column
        self._threshold = threshold
        self._take_profit_column = take_profit_column
        self._stop_loss_column = stop_loss_column

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "bracket_threshold_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the complete identity-bearing parameters."""
        return {
            "output_column": self._output_column,
            "stop_loss_column": self._stop_loss_column,
            "take_profit_column": self._take_profit_column,
            "threshold": self._threshold,
        }

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the score column plus whichever bracket columns are named."""
        names = [self._output_column]
        for name in (self._take_profit_column, self._stop_loss_column):
            if name is not None and name not in names:
                names.append(name)
        return tuple(names)

    @property
    def intent_schema_id(self) -> str:
        """Return the bracketed intent schema identifier."""
        return _BRACKETED_INTENT_SCHEMA_ID

    def _channel(
        self,
        model_output: _ModelOutputFrame,
        column: str | None,
        rows: int,
    ) -> tuple[_npt.NDArray[_np.float64], _npt.NDArray[_np.bool_]]:
        """Read one bracket channel and report which rows carry an unusable value."""
        if column is None:
            return _np.full(rows, _np.nan, dtype=_np.float64), _np.zeros(rows, dtype=_np.bool_)
        values = model_output.column(column).astype(_np.float64, copy=True)
        finite = _np.isfinite(values)
        unusable = finite & (values <= 0.0)
        values[~finite | unusable] = _np.nan
        return values, unusable

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Apply the threshold, then attach one bracket per instruction row.

        A row whose bracket column carries a present-but-unusable distance is
        turned into an explicit abstention rather than being quietly traded
        without the bracket its model asked for. Absent (NaN) is different: it
        states that this instruction supplies no distance of its own.
        """
        values = model_output.column(self._output_column)
        rows = int(values.shape[0])
        finite = _np.isfinite(values)
        exposure = _np.full(rows, _np.nan, dtype=_np.float64)
        exposure[finite] = 0.0
        exposure[finite & (values > self._threshold)] = 1.0
        exposure[finite & (values < -self._threshold)] = -1.0

        take_profit, take_profit_unusable = self._channel(
            model_output, self._take_profit_column, rows
        )
        stop_loss, stop_loss_unusable = self._channel(model_output, self._stop_loss_column, rows)

        abstain = take_profit_unusable | stop_loss_unusable
        exposure[abstain] = _np.nan
        # A bracket is only ever an attribute of an opening instruction, so it is
        # cleared everywhere the frame does not name a direction.
        held = ~_np.isin(exposure, (1.0, -1.0))
        take_profit[held] = _np.nan
        stop_loss[held] = _np.nan

        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys,
            target_exposure=exposure,
            take_profit_distance=take_profit,
            stop_loss_distance=stop_loss,
        )


_DECISION_POLICIES.add("threshold_v1", ThresholdPolicy)
_DECISION_POLICIES.add("abstain_v1", AbstainPolicy)
_DECISION_POLICIES.add("event_exposure_v1", EventExposurePolicy)
_DECISION_POLICIES.add("adaptive_bracket_exposure_v1", AdaptiveBracketExposurePolicy)
_DECISION_POLICIES.add("bracket_threshold_v1", BracketThresholdPolicy)
_DECISION_POLICIES.add("uls_joint_bracket_exposure_v1", UlsJointBracketExposurePolicy)
_DECISION_POLICIES.add("zone_fixed_bracket_exposure_v1", ZoneFixedBracketExposurePolicy)
_DECISION_POLICIES.add(
    "zone_adaptive_bracket_exposure_v1",
    ZoneAdaptiveBracketExposurePolicy,
)
