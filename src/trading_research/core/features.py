"""Columnar feature declarations and bounded provider input.

Feature providers receive one immutable, columnar request whose key axis ends
at the evaluation upper bound.  The request contains only columns declared by
the caller and carries the exact ordered identities that contributed rows.
It performs no loading, authorization, persistence, or feature computation.
"""

from __future__ import annotations

import json as _json
import math as _math
import typing as _typing
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.core.errors import FeatureContractError as _FeatureContractError
from trading_research.core.errors import FeatureRequestError as _FeatureRequestError
from trading_research.core.errors import MarketFrameError as _MarketFrameError
from trading_research.core.frames import MarketFrame as _MarketFrame
from trading_research.core.time import ClockDomain as _ClockDomain
from trading_research.core.time import NativeTimeKey as _NativeTimeKey
from trading_research.core.time import TimeUnit as _TimeUnit

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

    from trading_research.core.identity import DatasetIdentity as _DatasetIdentity

__all__ = [
    "CostHint",
    "FeatureOrigin",
    "FeatureRequest",
    "GapPolicy",
    "ParamValue",
    "WindowSpec",
    "canonical_params_json",
]

_INT64_MAX = int(_np.iinfo(_np.int64).max)


type ParamValue = str | int | float | bool | tuple[ParamValue, ...] | None


class GapPolicy(_StrEnum):
    """How an observed arithmetic discontinuity affects a feature window."""

    CROSS = "cross"
    BREAK = "break"


class CostHint(_StrEnum):
    """Declared cost used by materialization policy without timing a provider."""

    CHEAP = "cheap"
    EXPENSIVE = "expensive"


@_dataclass(frozen=True, slots=True)
class WindowSpec:
    """Finite observed-row reach and explicit arithmetic-gap behaviour."""

    max_prior_rows: int
    gap_policy: GapPolicy
    nominal_interval: int | None = None

    def __post_init__(self) -> None:
        """Validate reach and make the gap declaration total."""
        if type(self.max_prior_rows) is not int:
            raise _FeatureContractError("max_prior_rows must be a non-negative Python int")
        if self.max_prior_rows < 0:
            raise _FeatureContractError("max_prior_rows must be non-negative")
        if not isinstance(self.gap_policy, GapPolicy):
            raise _FeatureContractError(
                f"gap_policy must be a GapPolicy, got {type(self.gap_policy).__name__}"
            )

        interval = self.nominal_interval
        if self.gap_policy is GapPolicy.CROSS:
            if interval is not None:
                raise _FeatureContractError(
                    "nominal_interval must be None when gap_policy is CROSS"
                )
            return

        if type(interval) is not int:
            raise _FeatureContractError("BREAK requires nominal_interval as a positive Python int")
        if interval <= 0:
            raise _FeatureContractError("nominal_interval must be positive")
        if interval > _INT64_MAX:
            raise _FeatureContractError("nominal_interval must fit in a positive signed int64")


def _canonical_param_value(value: object, *, path: str) -> object:
    """Return one type-preserving JSON value or refuse outside ParamValue."""
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if _math.isnan(value):
            raise _FeatureContractError(
                f"{path} contains NaN, whose distinct bit patterns have no "
                "injective float.hex() encoding"
            )
        return {"__float_hex__": value.hex()}
    if type(value) is tuple:
        return [
            _canonical_param_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise _FeatureContractError(
        f"{path} has unsupported parameter type {type(value).__name__}; "
        "expected str, int, float, bool, None, or nested tuples"
    )


def _validated_params(
    params: _Mapping[str, ParamValue],
) -> _Mapping[str, ParamValue]:
    """Return a deterministic immutable copy of canonical provider parameters."""
    if not isinstance(params, _Mapping):
        raise _FeatureContractError(f"params must be a mapping, got {type(params).__name__}")
    copied: dict[str, ParamValue] = {}
    for name, value in params.items():
        if type(name) is not str or not name:
            raise _FeatureContractError("every parameter name must be a non-empty string")
        _canonical_param_value(value, path=f"params[{name!r}]")
        copied[name] = value
    return _MappingProxyType({name: copied[name] for name in sorted(copied)})


def canonical_params_json(params: _Mapping[str, ParamValue]) -> str:
    """Encode parameters deterministically while preserving every value type."""
    validated = _validated_params(params)
    encoded = {
        name: _canonical_param_value(value, path=f"params[{name!r}]")
        for name, value in validated.items()
    }
    return _json.dumps(
        encoded,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


@_dataclass(frozen=True, slots=True)
class FeatureOrigin:
    """Identity-bearing declaration that produced one feature column."""

    provider_id: str
    version: int
    params: _Mapping[str, ParamValue]
    window: WindowSpec

    def __post_init__(self) -> None:
        """Validate identity fields and detach parameters from caller mutation."""
        if type(self.provider_id) is not str or not self.provider_id:
            raise _FeatureContractError("provider_id must be a non-empty string")
        if type(self.version) is not int:
            raise _FeatureContractError("provider version must be a Python int")
        if type(self.window) is not WindowSpec:
            raise _FeatureContractError(
                f"window must be a WindowSpec, got {type(self.window).__name__}"
            )
        object.__setattr__(self, "params", _validated_params(self.params))


@_dataclass(frozen=True, slots=True)
class _ImmutableArrayStorage:
    """Immutable bytes plus metadata for one one-dimensional array."""

    _buffer: bytes
    _dtype: _np.dtype[_np.generic]
    _length: int

    @classmethod
    def _from_array(cls, array: _npt.NDArray[_typing.Any]) -> _ImmutableArrayStorage:
        contiguous = _np.ascontiguousarray(array)
        return cls(
            _buffer=contiguous.tobytes(order="C"),
            _dtype=contiguous.dtype,
            _length=int(contiguous.size),
        )

    def _descriptor(self) -> _npt.NDArray[_typing.Any]:
        descriptor = _np.frombuffer(
            self._buffer,
            dtype=self._dtype,
            count=self._length,
        )
        descriptor.setflags(write=False)
        return descriptor


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class FeatureRequest:
    """Immutable platform-issued market columns over one bounded context axis."""

    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _keys: _ImmutableArrayStorage
    _columns: _Mapping[str, _ImmutableArrayStorage]
    _eval_offset: int
    _eval_length: int

    def __init__(self) -> None:
        """Refuse direct construction; requests come only from bounded segments."""
        raise TypeError("FeatureRequest instances are issued only by from_segments")

    @classmethod
    def from_segments(
        cls,
        frames: _Sequence[_MarketFrame],
        *,
        eval_start: _NativeTimeKey,
        eval_end: _NativeTimeKey,
        required_columns: _Sequence[str],
    ) -> FeatureRequest:
        """Build one private contiguous buffer from already-issued frames.

        No loading or authorization occurs here. Every input must be an exact
        ``MarketFrame`` already bounded by the data layer, and every retained
        key must lie at or before ``eval_end``.
        """
        ordered_frames = _validated_frames(frames)
        columns = _validated_required_columns(required_columns)
        _validate_evaluation_bounds(
            eval_start=eval_start,
            eval_end=eval_end,
            unit=ordered_frames[0].unit,
            clock_domain=ordered_frames[0].clock_domain,
        )

        key_parts: list[_npt.NDArray[_np.int64]] = []
        column_parts: dict[str, list[_npt.NDArray[_typing.Any]]] = {name: [] for name in columns}
        expected_dtypes: dict[str, _np.dtype[_np.generic]] = {}
        previous_last: int | None = None
        prefix_rows = 0
        final_eval_offset: int | None = None
        final_eval_length: int | None = None

        for index, frame in enumerate(ordered_frames):
            if frame.unit is not ordered_frames[0].unit:
                raise _FeatureRequestError(
                    f"segment {index} uses {frame.unit.value}, expected "
                    f"{ordered_frames[0].unit.value}"
                )
            if frame.clock_domain != ordered_frames[0].clock_domain:
                raise _FeatureRequestError(
                    f"segment {index} uses clock domain "
                    f"{frame.clock_domain.identifier!r}, expected "
                    f"{ordered_frames[0].clock_domain.identifier!r}"
                )

            keys = frame.keys
            if keys.dtype.metadata is not None:
                raise _FeatureRequestError(
                    f"segment {index} keys must use a metadata-free int64 dtype"
                )
            if keys.size and int(keys[-1]) > eval_end.value:
                raise _FeatureRequestError(
                    f"segment {index} exposes key {int(keys[-1])} past eval_end {eval_end.value}"
                )
            if keys.size:
                first = int(keys[0])
                if previous_last is not None:
                    if first == previous_last:
                        raise _FeatureRequestError(
                            f"segments contain duplicate key {first} at segment {index}"
                        )
                    if first < previous_last:
                        raise _FeatureRequestError(
                            f"segments overlap or are unordered at segment {index}: "
                            f"{first} follows {previous_last}"
                        )
                previous_last = int(keys[-1])

                if index < len(ordered_frames) - 1 and previous_last >= eval_start.value:
                    raise _FeatureRequestError(
                        f"context segment {index} reaches evaluation range at key "
                        f"{previous_last}; evaluation rows must belong to the final segment"
                    )

            if index == len(ordered_frames) - 1:
                local_eval_offset = int(_np.searchsorted(keys, eval_start.value, side="left"))
                local_eval_stop = int(_np.searchsorted(keys, eval_end.value, side="right"))
                final_eval_offset = prefix_rows + local_eval_offset
                final_eval_length = local_eval_stop - local_eval_offset
            key_parts.append(keys)

            for name in columns:
                try:
                    values = frame.column(name)
                except _MarketFrameError as exc:
                    raise _FeatureRequestError(
                        f"segment {index} does not provide required column {name!r}"
                    ) from exc
                if values.dtype.metadata is not None:
                    raise _FeatureRequestError(
                        f"required column {name!r} in segment {index} must use a "
                        "metadata-free dtype"
                    )
                dtype = values.dtype
                prior = expected_dtypes.setdefault(name, dtype)
                if dtype != prior:
                    raise _FeatureRequestError(
                        f"required column {name!r} changes dtype across segments: "
                        f"{prior} vs {dtype}"
                    )
                column_parts[name].append(values)
            prefix_rows += int(keys.size)

        keys = _np.concatenate(key_parts)
        if final_eval_offset is None or final_eval_length is None:
            raise AssertionError("validated non-empty frame sequence has no final segment")
        if final_eval_length <= 0:
            raise _FeatureRequestError(
                f"evaluation interval [{eval_start.value}, {eval_end.value}] "
                "contains no observed rows"
            )

        request = object.__new__(cls)
        object.__setattr__(request, "_unit", ordered_frames[0].unit)
        object.__setattr__(request, "_clock_domain", ordered_frames[0].clock_domain)
        object.__setattr__(
            request,
            "_source_identities",
            tuple(frame.dataset_ref.canonical_identity for frame in ordered_frames),
        )
        object.__setattr__(
            request,
            "_keys",
            _ImmutableArrayStorage._from_array(keys),
        )
        object.__setattr__(
            request,
            "_columns",
            _MappingProxyType(
                {
                    name: _ImmutableArrayStorage._from_array(_np.concatenate(column_parts[name]))
                    for name in columns
                }
            ),
        )
        object.__setattr__(request, "_eval_offset", final_eval_offset)
        object.__setattr__(request, "_eval_length", final_eval_length)
        return request

    @property
    def unit(self) -> _TimeUnit:
        """Resolution shared by every native key in the request."""
        return self._unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Opaque clock domain shared by every native key in the request."""
        return self._clock_domain

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Ordered canonical identities of every contributing segment."""
        return self._source_identities

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return the read-only resolved context axis, never past eval_end."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._keys._descriptor())

    def column(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one declared input column, refusing every other name."""
        if type(name) is not str:
            raise _FeatureRequestError(f"column name must be a string, got {type(name).__name__}")
        try:
            return self._columns[name]._descriptor()
        except KeyError as exc:
            raise _FeatureRequestError(
                f"column {name!r} was not declared for this feature request"
            ) from exc

    @property
    def eval_offset(self) -> int:
        """Offset of the first observed evaluation row on the context axis."""
        return self._eval_offset

    @property
    def eval_length(self) -> int:
        """Number of observed evaluation rows."""
        return self._eval_length


def _validated_frames(frames: object) -> tuple[_MarketFrame, ...]:
    if isinstance(frames, str | bytes) or not isinstance(frames, _Sequence):
        raise _FeatureRequestError("frames must be a sequence of issued MarketFrame objects")
    ordered = tuple(frames)
    if not ordered:
        raise _FeatureRequestError("at least one bounded MarketFrame segment is required")
    for index, frame in enumerate(ordered):
        if type(frame) is not _MarketFrame:
            raise _FeatureRequestError(
                f"segment {index} must be an exact MarketFrame, got {type(frame).__name__}"
            )
    return ordered


def _validated_required_columns(columns: object) -> tuple[str, ...]:
    if isinstance(columns, str | bytes) or not isinstance(columns, _Sequence):
        raise _FeatureRequestError("required_columns must be a sequence of names")
    names = tuple(columns)
    if any(type(name) is not str or not name for name in names):
        raise _FeatureRequestError("every required column name must be a non-empty string")
    if len(set(names)) != len(names):
        raise _FeatureRequestError("required_columns contains duplicate names")
    return names


def _validate_evaluation_bounds(
    *,
    eval_start: _NativeTimeKey,
    eval_end: _NativeTimeKey,
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
) -> None:
    for label, bound in (("eval_start", eval_start), ("eval_end", eval_end)):
        if not isinstance(bound, _NativeTimeKey):
            raise _FeatureRequestError(
                f"{label} must be a NativeTimeKey, got {type(bound).__name__}"
            )
        if bound.unit is not unit:
            raise _FeatureRequestError(f"{label} uses {bound.unit.value}, expected {unit.value}")
        if bound.clock_domain != clock_domain:
            raise _FeatureRequestError(
                f"{label} uses clock domain {bound.clock_domain.identifier!r}, "
                f"expected {clock_domain.identifier!r}"
            )
    if eval_start.value > eval_end.value:
        raise _FeatureRequestError(
            f"evaluation interval is inverted: {eval_start.value} > {eval_end.value}"
        )
