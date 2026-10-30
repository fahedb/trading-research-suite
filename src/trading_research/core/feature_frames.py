"""Dense immutable carrier for computed feature columns."""

from __future__ import annotations

import typing as _typing
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.core.errors import FeatureFrameError as _FeatureFrameError
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import FeatureRequest as _FeatureRequest
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.time import ClockDomain as _ClockDomain
from trading_research.core.time import TimeUnit as _TimeUnit

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

__all__ = ["FeatureFrame"]


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
class FeatureFrame:
    """Dense float64 columns aligned exactly to observed evaluation keys."""

    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _keys: _ImmutableArrayStorage
    _schema: _Mapping[str, str]
    _columns: _Mapping[str, _ImmutableArrayStorage]
    _origins: _Mapping[str, _FeatureOrigin]

    def __init__(self) -> None:
        """Refuse direct construction; validated platform code mints frames."""
        raise TypeError("FeatureFrame instances are issued only by the feature platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return dense, read-only observed evaluation keys."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Resolution shared by every native key in the frame."""
        return self._unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Opaque clock domain shared by every native key in the frame."""
        return self._clock_domain

    @property
    def num_rows(self) -> int:
        """Number of dense evaluation rows."""
        return self._keys._length

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Ordered canonical identities that contributed to computation."""
        return self._source_identities

    @property
    def column_names(self) -> tuple[str, ...]:
        """Feature names in deterministic declaration order."""
        return tuple(self._columns)

    @property
    def schema(self) -> _Mapping[str, str]:
        """Read-only feature schema; every V1 value is ``float64``."""
        return self._schema

    def column(self, name: str) -> _npt.NDArray[_np.float64]:
        """Return one read-only float64 feature column."""
        if type(name) is not str:
            raise _FeatureFrameError(f"column name must be a string, got {type(name).__name__}")
        try:
            return _typing.cast(
                "_npt.NDArray[_np.float64]",
                self._columns[name]._descriptor(),
            )
        except KeyError as exc:
            raise _FeatureFrameError(f"unknown feature column {name!r}") from exc

    def origin(self, name: str) -> _FeatureOrigin:
        """Return the immutable provider/window provenance for one column."""
        if type(name) is not str:
            raise _FeatureFrameError(f"origin name must be a string, got {type(name).__name__}")
        try:
            return self._origins[name]
        except KeyError as exc:
            raise _FeatureFrameError(f"unknown feature column {name!r}") from exc


def _make_feature_frame(
    *,
    request: _FeatureRequest,
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _FeatureOrigin],
) -> FeatureFrame:
    """Validate platform output and mint one immutable feature frame."""
    if type(request) is not _FeatureRequest:
        raise _FeatureFrameError(
            f"request must be an exact FeatureRequest, got {type(request).__name__}"
        )
    request_keys = request.keys
    return _restore_feature_frame(
        keys=request_keys[request.eval_offset : request.eval_offset + request.eval_length],
        unit=request.unit,
        clock_domain=request.clock_domain,
        source_identities=request.source_identities,
        columns=columns,
        origins=origins,
    )


def _restore_feature_frame(
    *,
    keys: _npt.NDArray[_typing.Any],
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    source_identities: tuple[_DatasetIdentity, ...],
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _FeatureOrigin],
) -> FeatureFrame:
    """Validate primitive cached data and mint one detached feature frame."""
    validated_keys = _validated_keys(keys)
    if type(unit) is not _TimeUnit:
        raise _FeatureFrameError(
            f"FeatureFrame unit must be an exact TimeUnit, got {type(unit).__name__}"
        )
    if type(clock_domain) is not _ClockDomain:
        raise _FeatureFrameError(
            "FeatureFrame clock_domain must be an exact ClockDomain, "
            f"got {type(clock_domain).__name__}"
        )
    if type(source_identities) is not tuple or not source_identities:
        raise _FeatureFrameError("FeatureFrame source_identities must be a non-empty exact tuple")
    if any(type(identity) is not _DatasetIdentity for identity in source_identities):
        raise _FeatureFrameError(
            "FeatureFrame source_identities must contain exact DatasetIdentity values"
        )
    if not isinstance(columns, _Mapping) or not columns:
        raise _FeatureFrameError("FeatureFrame requires at least one feature column")
    names = tuple(columns)
    if any(type(name) is not str or not name for name in names):
        raise _FeatureFrameError("every feature name must be a non-empty string")

    if not isinstance(origins, _Mapping):
        raise _FeatureFrameError("origins must be a mapping")
    if set(origins) != set(names):
        missing = sorted(set(names) - set(origins))
        extra = sorted(set(origins) - set(names))
        raise _FeatureFrameError(
            f"origin names must exactly match feature columns; missing={missing}, extra={extra}"
        )

    validated_columns: dict[str, _npt.NDArray[_np.float64]] = {}
    validated_origins: dict[str, _FeatureOrigin] = {}
    for name in names:
        values = columns[name]
        if type(values) is not _np.ndarray:
            raise _FeatureFrameError(f"feature column {name!r} must be a NumPy array")
        if values.ndim != 1:
            raise _FeatureFrameError(f"feature column {name!r} must be one-dimensional")
        if values.dtype != _np.dtype(_np.float64) or values.dtype.metadata is not None:
            raise _FeatureFrameError(
                f"feature column {name!r} must have exact metadata-free dtype float64, "
                f"got {values.dtype}"
            )
        if values.shape[0] != validated_keys.shape[0]:
            raise _FeatureFrameError(
                f"feature column {name!r} length {values.shape[0]} does not match "
                f"key length {validated_keys.shape[0]}"
            )
        if _np.isinf(values).any():
            raise _FeatureFrameError(
                f"feature column {name!r} contains positive or negative infinity"
            )
        origin = origins[name]
        if type(origin) is not _FeatureOrigin:
            raise _FeatureFrameError(f"origin for feature {name!r} must be a FeatureOrigin")
        validated_columns[name] = values
        validated_origins[name] = origin

    frame = object.__new__(FeatureFrame)
    object.__setattr__(frame, "_unit", unit)
    object.__setattr__(frame, "_clock_domain", clock_domain)
    object.__setattr__(frame, "_source_identities", source_identities)
    object.__setattr__(frame, "_keys", _ImmutableArrayStorage._from_array(validated_keys))
    object.__setattr__(
        frame,
        "_schema",
        _MappingProxyType(dict.fromkeys(names, "float64")),
    )
    object.__setattr__(
        frame,
        "_columns",
        _MappingProxyType(
            {name: _ImmutableArrayStorage._from_array(validated_columns[name]) for name in names}
        ),
    )
    object.__setattr__(
        frame,
        "_origins",
        _MappingProxyType(validated_origins),
    )
    return frame


def _validated_keys(keys: _npt.NDArray[_typing.Any]) -> _npt.NDArray[_np.int64]:
    if type(keys) is not _np.ndarray:
        raise _FeatureFrameError("FeatureFrame keys must be a NumPy array")
    if keys.ndim != 1:
        raise _FeatureFrameError("FeatureFrame keys must be one-dimensional")
    if keys.dtype != _np.dtype(_np.int64) or keys.dtype.metadata is not None:
        raise _FeatureFrameError(
            f"FeatureFrame keys must have exact metadata-free dtype int64, got {keys.dtype}"
        )
    if keys.size == 0:
        raise _FeatureFrameError("FeatureFrame requires at least one evaluation key")
    if keys.size > 1 and _np.any(keys[1:] <= keys[:-1]):
        raise _FeatureFrameError("FeatureFrame keys must be strictly increasing and unique")
    return keys
