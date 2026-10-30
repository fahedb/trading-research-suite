"""Decision fan-out, ephemeral canonical round-trip, and sparse alignment."""

from __future__ import annotations

import json as _json
import re as _re
import typing as _typing
from collections.abc import Callable as _Callable
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import _make_intent_frame
from trading_research.decisions.execution import (
    _DecisionPlan,
    _execute_policy,
    _snapshot_policy,
    _validate_policy_columns,
    _validate_policy_compatibility,
)

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

__all__: list[str] = []

_FORMAT = "trading-research-intent-frame"
_FORMAT_VERSION = 1
_TOP_LEVEL_FIELDS = frozenset(
    {
        "clock_domain",
        "format",
        "format_version",
        "intent_schema_id",
        "keys_int64",
        "row_count",
        "target_exposure_uint64",
        "time_unit",
    }
)
_BRACKET_FIELDS = frozenset(
    {
        "stop_loss_distance_uint64",
        "take_profit_distance_uint64",
    }
)
_INT64_TEXT = _re.compile(r"^(?:0|-?[1-9][0-9]*)$")
_UINT64_HEX = _re.compile(r"^[0-9a-f]{16}$")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


@_dataclass(frozen=True, slots=True)
class _StageCounters:
    _model_inference_calls: int
    _model_reinference_calls: int
    _decision_policy_calls: int


@_dataclass(frozen=True, slots=True)
class _FanOutResult:
    _model_output: _ModelOutputFrame
    _intents: tuple[_IntentFrame, ...]
    _counters: _StageCounters


@_dataclass(frozen=True, slots=True)
class _AlignedIntent:
    _present: _npt.NDArray[_np.bool_]
    _target_exposure: _npt.NDArray[_np.float64]


def _infer_once_and_fan_out(
    *,
    inference: _Callable[[], _ModelOutputFrame],
    output_column_names: tuple[str, ...],
    policies: _Sequence[object],
) -> _FanOutResult:
    """Invoke inference exactly once, then give only its frame to every policy."""
    if not callable(inference):
        raise _ContractViolationError("inference must be callable")
    policy_objects, plans = _policy_objects_and_plans(policies)
    for plan in plans:
        _validate_policy_columns(plan, output_column_names)
    model_output = inference()
    if type(model_output) is not _ModelOutputFrame:
        raise _ContractViolationError("inference must return an exact ModelOutputFrame")
    if model_output.column_names != output_column_names:
        raise _ContractViolationError(
            "inference columns differ from their pre-execution declaration"
        )
    intents = _execute_policy_plans(
        model_output=model_output,
        policy_objects=policy_objects,
        plans=plans,
    )
    return _FanOutResult(
        _model_output=model_output,
        _intents=intents,
        _counters=_StageCounters(
            _model_inference_calls=1,
            _model_reinference_calls=0,
            _decision_policy_calls=len(intents),
        ),
    )


def _fan_out_model_output(
    *,
    model_output: _ModelOutputFrame,
    policies: _Sequence[object],
) -> tuple[_IntentFrame, ...]:
    """Execute an ordered non-empty policy set over one already-computed frame."""
    if type(model_output) is not _ModelOutputFrame:
        raise _ContractViolationError("fan-out requires an exact ModelOutputFrame")
    policy_objects, plans = _policy_objects_and_plans(policies)
    return _execute_policy_plans(
        model_output=model_output,
        policy_objects=policy_objects,
        plans=plans,
    )


def _execute_policy_plans(
    *,
    model_output: _ModelOutputFrame,
    policy_objects: tuple[object, ...],
    plans: tuple[_DecisionPlan, ...],
) -> tuple[_IntentFrame, ...]:
    for plan in plans:
        _validate_policy_compatibility(plan, model_output)
    return tuple(
        _execute_policy(policy, plan, model_output)
        for policy, plan in zip(policy_objects, plans, strict=True)
    )


def _policy_objects_and_plans(
    policies: _Sequence[object],
) -> tuple[tuple[object, ...], tuple[_DecisionPlan, ...]]:
    if isinstance(policies, str | bytes) or not isinstance(policies, _Sequence):
        raise _ContractViolationError("policies must be an ordered sequence")
    policy_objects = tuple(policies)
    if not policy_objects:
        raise _ContractViolationError("fan-out requires at least one decision policy")
    plans = tuple(_snapshot_policy(policy) for policy in policy_objects)
    return policy_objects, plans


def _encode_intent_frame(frame: _IntentFrame) -> bytes:
    """Encode one ephemeral intent as canonical JSON without writing it."""
    if type(frame) is not _IntentFrame:
        raise _ContractViolationError("intent encoding requires an exact IntentFrame")
    bits = frame.target_exposure.view(_np.uint64)
    document: dict[str, object] = {
        "format": _FORMAT,
        "format_version": _FORMAT_VERSION,
        "intent_schema_id": frame.intent_schema_id,
        "time_unit": frame.unit.value,
        "clock_domain": frame.clock_domain.identifier,
        "row_count": frame.num_rows,
        "keys_int64": [str(int(value)) for value in frame.keys],
        "target_exposure_uint64": [f"{int(value):016x}" for value in bits],
    }
    take_profit = frame.take_profit_distance
    stop_loss = frame.stop_loss_distance
    if take_profit is not None and stop_loss is not None:
        # Bracket distances round-trip as raw bit patterns for the same reason
        # exposure does: NaN is load-bearing here and decimal text would not
        # preserve it exactly.
        document["take_profit_distance_uint64"] = [
            f"{int(value):016x}" for value in take_profit.view(_np.uint64)
        ]
        document["stop_loss_distance_uint64"] = [
            f"{int(value):016x}" for value in stop_loss.view(_np.uint64)
        ]
    return _canonical_json(document).encode("ascii")


def _decode_intent_frame(payload: bytes, *, view: _ViewSpan) -> _IntentFrame:
    """Decode one canonical in-memory intent payload against its evaluation span."""
    if type(payload) is not bytes:
        raise _ContractViolationError("intent payload must be exact bytes")
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("intent decoding requires an exact ViewSpan")
    try:
        text = payload.decode("ascii")
        value = _json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except _ContractViolationError:
        raise
    except Exception as exc:
        raise _ContractViolationError("intent payload is not strict ASCII JSON") from exc
    if _canonical_json(value) != text:
        raise _ContractViolationError("intent payload is not canonically encoded")
    if type(value) is not dict:
        raise _ContractViolationError("intent payload fields differ from the V1 schema")
    present = frozenset(value)
    bracket_present = present & _BRACKET_FIELDS
    if present - _BRACKET_FIELDS != _TOP_LEVEL_FIELDS or len(bracket_present) not in (0, 2):
        raise _ContractViolationError("intent payload fields differ from the V1 schema")
    if (
        type(value["format"]) is not str
        or value["format"] != _FORMAT
        or type(value["format_version"]) is not int
        or value["format_version"] != _FORMAT_VERSION
    ):
        raise _ContractViolationError("intent payload format or version is unsupported")
    if value["time_unit"] != view.unit.value:
        raise _ContractViolationError("intent payload time unit differs from ViewSpan")
    if value["clock_domain"] != view.clock_domain.identifier:
        raise _ContractViolationError("intent payload clock domain differs from ViewSpan")

    row_count = value["row_count"]
    key_values = value["keys_int64"]
    bit_values = value["target_exposure_uint64"]
    if type(row_count) is not int or row_count < 0:
        raise _ContractViolationError("intent row_count must be a non-negative Python int")
    if type(key_values) is not list or type(bit_values) is not list:
        raise _ContractViolationError("intent keys and values must be arrays")
    if len(key_values) != row_count or len(bit_values) != row_count:
        raise _ContractViolationError("intent row_count differs from its arrays")
    keys = _decode_keys(key_values)
    exposure = _decode_exposure_bits(bit_values)
    intent_schema_id = value["intent_schema_id"]
    if type(intent_schema_id) is not str:
        raise _ContractViolationError("intent_schema_id must be a string")
    take_profit: _npt.NDArray[_np.float64] | None = None
    stop_loss: _npt.NDArray[_np.float64] | None = None
    if bracket_present:
        take_profit = _decode_bracket_bits(
            value["take_profit_distance_uint64"],
            row_count=row_count,
            field="take_profit_distance",
        )
        stop_loss = _decode_bracket_bits(
            value["stop_loss_distance_uint64"],
            row_count=row_count,
            field="stop_loss_distance",
        )
    return _make_intent_frame(
        view=view,
        intent_schema_id=intent_schema_id,
        keys=keys,
        target_exposure=exposure,
        take_profit_distance=take_profit,
        stop_loss_distance=stop_loss,
    )


def _align_intent_to_dense_axis(
    *,
    view: _ViewSpan,
    intent: _IntentFrame,
    dense_keys: _npt.NDArray[_np.int64],
) -> _AlignedIntent:
    """Align sparse intent while retaining a separate row-presence mask."""
    if type(view) is not _ViewSpan or type(intent) is not _IntentFrame:
        raise _ContractViolationError("intent alignment requires exact frame and ViewSpan")
    if (
        type(dense_keys) is not _np.ndarray
        or dense_keys.ndim != 1
        or dense_keys.dtype != _np.dtype(_np.int64)
        or dense_keys.dtype.metadata is not None
        or not dense_keys.size
        or (dense_keys.size > 1 and _np.any(dense_keys[1:] <= dense_keys[:-1]))
    ):
        raise _ContractViolationError(
            "dense evaluation keys must be a non-empty ordered metadata-free int64 array"
        )
    if int(dense_keys[0]) < view.eval_start.value or int(dense_keys[-1]) > view.eval_end.value:
        raise _ContractViolationError("dense evaluation keys must lie inside ViewSpan")
    if intent.unit is not view.unit or intent.clock_domain != view.clock_domain:
        raise _ContractViolationError("IntentFrame axis tags must match ViewSpan")

    intent_keys = intent.keys
    positions = _np.searchsorted(dense_keys, intent_keys)
    if intent_keys.size and (
        _np.any(positions >= dense_keys.size)
        or not _np.array_equal(dense_keys[positions], intent_keys)
    ):
        raise _ContractViolationError("IntentFrame keys must be a subset of the dense axis")
    present = _np.zeros(dense_keys.size, dtype=_np.bool_)
    exposure = _np.full(dense_keys.size, _np.nan, dtype=_np.float64)
    present[positions] = True
    exposure[positions] = intent.target_exposure
    present.setflags(write=False)
    exposure.setflags(write=False)
    return _AlignedIntent(present, exposure)


def _decode_keys(values: list[object]) -> _npt.NDArray[_np.int64]:
    decoded: list[int] = []
    for value in values:
        if type(value) is not str or _INT64_TEXT.fullmatch(value) is None:
            raise _ContractViolationError("intent key is not canonical int64 text")
        integer = int(value)
        if integer < _INT64_MIN or integer > _INT64_MAX:
            raise _ContractViolationError("intent key lies outside int64")
        decoded.append(integer)
    return _np.asarray(decoded, dtype=_np.int64)


def _decode_exposure_bits(values: list[object]) -> _npt.NDArray[_np.float64]:
    decoded: list[int] = []
    for value in values:
        if type(value) is not str or _UINT64_HEX.fullmatch(value) is None:
            raise _ContractViolationError(
                "target_exposure value is not canonical uint64 hexadecimal"
            )
        decoded.append(int(value, 16))
    return _np.asarray(decoded, dtype=_np.uint64).view(_np.float64).copy()


def _decode_bracket_bits(
    values: object,
    *,
    row_count: int,
    field: str,
) -> _npt.NDArray[_np.float64]:
    if type(values) is not list or len(values) != row_count:
        raise _ContractViolationError(f"intent {field} length differs from row_count")
    decoded: list[int] = []
    for value in values:
        if type(value) is not str or _UINT64_HEX.fullmatch(value) is None:
            raise _ContractViolationError(f"{field} value is not canonical uint64 hexadecimal")
        decoded.append(int(value, 16))
    return _np.asarray(decoded, dtype=_np.uint64).view(_np.float64).copy()


def _canonical_json(value: object) -> str:
    try:
        return _json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise _ContractViolationError("intent document is not canonical JSON") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise _ContractViolationError(f"intent payload repeats field {name!r}")
        result[name] = value
    return result
