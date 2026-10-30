"""The compact, immutable column carrier for admitted market data.

``MarketFrame`` deliberately offers only key-bounded slicing and column
selection.  Loading, joins, expressions, aggregation and per-row iteration
belong elsewhere.  Value columns and the native-key index are exposed as
read-only NumPy arrays and derived frames share their existing buffers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from trading_research.core.errors import (
    ClockDomainError,
    MarketFrameError,
    TimeUnitMismatchError,
)
from trading_research.core.identity import DatasetRef
from trading_research.core.time import ClockDomain, NativeTimeKey, TimeUnit

if TYPE_CHECKING:
    import numpy.typing as npt

__all__ = ["MarketFrame"]


@dataclass(frozen=True, slots=True)
class _ImmutableArrayStorage:
    """Immutable bytes plus the private metadata for one one-dimensional view.

    Public NumPy arrays are reconstructed from this record on demand.  That
    makes every returned ndarray descriptor disposable while selected and
    sliced frames can continue sharing the same immutable data buffer.
    """

    _buffer: bytes
    _dtype: np.dtype[np.generic]
    _offset: int
    _length: int

    def _descriptor(self) -> npt.NDArray[Any]:
        """Return a fresh read-only ndarray descriptor over the stored bytes."""
        descriptor = np.frombuffer(
            self._buffer,
            dtype=self._dtype,
            count=self._length,
            offset=self._offset,
        )
        descriptor.setflags(write=False)
        return descriptor

    def _slice(self, start: int, stop: int) -> _ImmutableArrayStorage:
        """Return zero-copy private metadata for one contiguous basic slice."""
        return _ImmutableArrayStorage(
            _buffer=self._buffer,
            _dtype=self._dtype,
            _offset=self._offset + start * self._dtype.itemsize,
            _length=stop - start,
        )


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class MarketFrame:
    """Immutable native-key index plus aligned, typed value columns.

    Instances are minted only by the data loader's private integration
    factory.  This prevents callers from presenting arbitrary arrays as
    admitted market data.  V1 protects against accidental mutation and
    bypass, not a hostile Python process with owner privileges.
    """

    _dataset_ref: DatasetRef
    _clock_domain: ClockDomain
    _unit: TimeUnit
    _schema: Mapping[str, str]
    _keys: _ImmutableArrayStorage
    _columns: Mapping[str, _ImmutableArrayStorage]
    _admitted_lo: int
    _admitted_hi: int

    def __init__(self) -> None:
        """Refuse direct construction; frames come only from the data loader."""
        raise TypeError("MarketFrame instances are issued only by the data loader")

    @property
    def dataset_ref(self) -> DatasetRef:
        """Exact hash-pinned dataset reference represented by this frame."""
        return self._dataset_ref

    @property
    def clock_domain(self) -> ClockDomain:
        """Opaque clock domain shared by every native key in the frame."""
        return self._clock_domain

    @property
    def unit(self) -> TimeUnit:
        """Resolution shared by every native key in the frame."""
        return self._unit

    @property
    def schema(self) -> Mapping[str, str]:
        """Read-only value-column names and their canonical NumPy dtypes."""
        return self._schema

    @property
    def num_rows(self) -> int:
        """Number of aligned native keys and value rows in the frame."""
        return self._keys._length

    @property
    def keys(self) -> npt.NDArray[np.int64]:
        """Return strict, read-only native keys with an isolated descriptor."""
        return self._keys._descriptor()

    @property
    def column_names(self) -> tuple[str, ...]:
        """Value-column names in declared schema order."""
        return tuple(self._schema)

    def column(self, name: str) -> npt.NDArray[Any]:
        """Return one named read-only value column without copying it.

        Raises:
            MarketFrameError: If ``name`` is not a declared value column.
        """
        if not isinstance(name, str):
            raise MarketFrameError(
                f"column name must be a string, got {type(name).__name__}"
            )
        try:
            return self._columns[name]._descriptor()
        except KeyError as exc:
            raise MarketFrameError(f"unknown market-frame column {name!r}") from exc

    def select(self, names: Sequence[str]) -> MarketFrame:
        """Return a zero-copy frame containing only requested value columns.

        The native-key index is always retained.  Requested column order is
        preserved, and an empty selection produces a valid keys-only frame.

        Raises:
            MarketFrameError: If ``names`` is not a sequence of unique,
                declared value-column names.
        """
        if isinstance(names, str) or not isinstance(names, Sequence):
            raise MarketFrameError("column selection must be a sequence of names")
        requested = tuple(names)
        if any(not isinstance(name, str) for name in requested):
            raise MarketFrameError("every selected column name must be a string")
        if len(set(requested)) != len(requested):
            raise MarketFrameError("column selection contains duplicate names")

        unknown = tuple(name for name in requested if name not in self._columns)
        if unknown:
            raise MarketFrameError(f"unknown market-frame columns: {unknown!r}")

        return _from_validated_parts(
            dataset_ref=self._dataset_ref,
            clock_domain=self._clock_domain,
            unit=self._unit,
            schema={name: self._schema[name] for name in requested},
            keys=self._keys,
            columns={name: self._columns[name] for name in requested},
            admitted_lo=self._admitted_lo,
            admitted_hi=self._admitted_hi,
        )

    def slice_by_keys(self, lo: NativeTimeKey, hi: NativeTimeKey) -> MarketFrame:
        """Return the zero-copy rows whose native keys lie in ``[lo, hi]``.

        Bounds are inclusive.  They must share the frame's unit and opaque
        clock domain and remain inside its private logical interval.  They
        need not coincide with observed keys, so a valid gap may select no
        rows while still retaining its requested interval.

        Raises:
            MarketFrameError: If either bound is not a native key or the
                requested range is inverted.
            TimeUnitMismatchError: If either bound uses another unit.
            ClockDomainError: If either bound uses another clock domain.
        """
        self._require_bound_on_axis(lo, "lower")
        self._require_bound_on_axis(hi, "upper")
        if lo.value > hi.value:
            raise MarketFrameError(
                f"native-key slice is inverted: lower {lo.value} > upper {hi.value}"
            )
        if lo.value < self._admitted_lo or hi.value > self._admitted_hi:
            raise MarketFrameError(
                f"native-key slice [{lo.value}, {hi.value}] exceeds frame bounds "
                f"[{self._admitted_lo}, {self._admitted_hi}]"
            )

        keys = self._keys._descriptor()
        start = int(np.searchsorted(keys, lo.value, side="left"))
        stop = int(np.searchsorted(keys, hi.value, side="right"))
        return _from_validated_parts(
            dataset_ref=self._dataset_ref,
            clock_domain=self._clock_domain,
            unit=self._unit,
            schema=self._schema,
            keys=self._keys._slice(start, stop),
            columns={
                name: column._slice(start, stop)
                for name, column in self._columns.items()
            },
            admitted_lo=lo.value,
            admitted_hi=hi.value,
        )

    def _require_bound_on_axis(self, bound: NativeTimeKey, label: str) -> None:
        """Require one slice bound to be a tagged key on this frame's axis."""
        if not isinstance(bound, NativeTimeKey):
            raise MarketFrameError(
                f"{label} slice bound must be a NativeTimeKey, got "
                f"{type(bound).__name__}"
            )
        if bound.unit is not self._unit:
            raise TimeUnitMismatchError(
                f"{label} slice bound uses {bound.unit.value}, but frame uses "
                f"{self._unit.value}; no implicit conversion exists"
            )
        if bound.clock_domain != self._clock_domain:
            raise ClockDomainError(
                f"{label} slice bound uses clock domain "
                f"{bound.clock_domain.identifier!r}, but frame uses "
                f"{self._clock_domain.identifier!r}; no resolver exists between domains"
            )


def _make_market_frame(
    *,
    dataset_ref: DatasetRef,
    clock_domain: ClockDomain,
    unit: TimeUnit,
    schema: Mapping[str, str],
    keys: npt.NDArray[Any],
    columns: Mapping[str, npt.NDArray[Any]],
    admitted_lo: int,
    admitted_hi: int,
) -> MarketFrame:
    """Validate loader-produced arrays and mint one immutable frame.

    This function is the private integration seam for the data loader.  It
    performs no casts: a dtype mismatch is a hard refusal.  After validation,
    one defensive copy places every array over an immutable Python ``bytes``
    buffer.  Derived frames can then share those buffers without exposing a
    writable NumPy owner through their ordinary ``.base`` chains.

    Raises:
        MarketFrameError: If metadata, keys, schema or aligned columns do not
            satisfy the compact frame contract.
    """
    if not isinstance(dataset_ref, DatasetRef):
        raise MarketFrameError(
            f"dataset_ref must be a DatasetRef, got {type(dataset_ref).__name__}"
        )
    if not isinstance(clock_domain, ClockDomain):
        raise MarketFrameError(
            f"clock_domain must be a ClockDomain, got {type(clock_domain).__name__}"
        )
    if not isinstance(unit, TimeUnit):
        raise MarketFrameError(f"unit must be a TimeUnit, got {type(unit).__name__}")

    validated_lo, validated_hi = _validate_admitted_bounds(admitted_lo, admitted_hi)
    validated_keys = _validate_keys(keys)
    if validated_keys.size and (
        int(validated_keys[0]) < validated_lo or int(validated_keys[-1]) > validated_hi
    ):
        raise MarketFrameError(
            f"keys exceed frame bounds [{validated_lo}, {validated_hi}]: "
            f"observed [{int(validated_keys[0])}, {int(validated_keys[-1])}]"
        )
    validated_schema = _validate_schema(schema)
    validated_columns = _validate_columns(
        schema=validated_schema,
        columns=columns,
        num_rows=int(validated_keys.shape[0]),
    )

    protected_keys = _immutable_array_copy(validated_keys)
    protected_columns = {
        name: _immutable_array_copy(column)
        for name, column in validated_columns.items()
    }

    return _from_validated_parts(
        dataset_ref=dataset_ref,
        clock_domain=clock_domain,
        unit=unit,
        schema=validated_schema,
        keys=protected_keys,
        columns=protected_columns,
        admitted_lo=validated_lo,
        admitted_hi=validated_hi,
    )


def _validate_admitted_bounds(admitted_lo: int, admitted_hi: int) -> tuple[int, int]:
    """Validate the private logical interval retained across empty slices."""
    int64_min = -(2**63)
    int64_max = 2**63 - 1
    for label, value in (("lower", admitted_lo), ("upper", admitted_hi)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise MarketFrameError(
                f"{label} frame bound must be an integer, got {type(value).__name__}"
            )
        if not int64_min <= value <= int64_max:
            raise MarketFrameError(
                f"{label} frame bound {value} is outside the signed 64-bit range"
            )
    if admitted_lo > admitted_hi:
        raise MarketFrameError(
            f"frame bounds are inverted: lower {admitted_lo} > upper {admitted_hi}"
        )
    return admitted_lo, admitted_hi


def _immutable_array_copy(array: npt.NDArray[Any]) -> _ImmutableArrayStorage:
    """Copy one validated 1-D array into private immutable storage metadata.

    A merely read-only owning ``ndarray`` can make itself writable again.
    ``bytes`` cannot. Public access reconstructs a fresh descriptor with
    ``frombuffer`` so descriptor mutation remains local to the returned
    object. Basic slices retain zero-copy metadata over the same bytes.
    """
    return _ImmutableArrayStorage(
        _buffer=array.tobytes(order="C"),
        _dtype=array.dtype,
        _offset=0,
        _length=int(array.size),
    )


def _validate_keys(keys: npt.NDArray[Any]) -> npt.NDArray[np.int64]:
    """Validate and return the exact int64 native-key array without copying."""
    if type(keys) is not np.ndarray:
        raise MarketFrameError(
            f"keys must be a NumPy ndarray, got {type(keys).__name__}"
        )
    if keys.ndim != 1:
        raise MarketFrameError(f"keys must be one-dimensional, got {keys.ndim} dimensions")
    if keys.dtype != np.dtype(np.int64):
        raise MarketFrameError(f"keys must have exact dtype int64, got {keys.dtype}")
    if keys.size > 1 and bool(np.any(keys[1:] <= keys[:-1])):
        raise MarketFrameError("keys must be strictly increasing with no duplicates")
    return cast("npt.NDArray[np.int64]", keys)


def _validate_schema(schema: Mapping[str, str]) -> dict[str, str]:
    """Copy and validate canonical value-column dtype declarations."""
    if not isinstance(schema, Mapping):
        raise MarketFrameError(f"schema must be a mapping, got {type(schema).__name__}")

    validated: dict[str, str] = {}
    for name, declared_dtype in schema.items():
        if not isinstance(name, str) or not name:
            raise MarketFrameError("every schema column name must be a non-empty string")
        if not isinstance(declared_dtype, str):
            raise MarketFrameError(
                f"schema dtype for {name!r} must be a string, got "
                f"{type(declared_dtype).__name__}"
            )
        try:
            dtype = np.dtype(declared_dtype)
        except (TypeError, ValueError) as exc:
            raise MarketFrameError(
                f"schema dtype for {name!r} is not understood: {declared_dtype!r}"
            ) from exc
        if dtype.name != declared_dtype:
            raise MarketFrameError(
                f"schema dtype for {name!r} must use its canonical name "
                f"{dtype.name!r}, got {declared_dtype!r}"
            )
        if dtype.hasobject:
            raise MarketFrameError(
                f"schema dtype for {name!r} must not contain Python objects"
            )
        validated[name] = declared_dtype
    return validated


def _validate_columns(
    *,
    schema: Mapping[str, str],
    columns: Mapping[str, npt.NDArray[Any]],
    num_rows: int,
) -> dict[str, npt.NDArray[Any]]:
    """Validate aligned value arrays in declared schema order without copying."""
    if not isinstance(columns, Mapping):
        raise MarketFrameError(f"columns must be a mapping, got {type(columns).__name__}")
    if any(not isinstance(name, str) for name in columns):
        raise MarketFrameError("every column mapping key must be a string")

    declared_names = set(schema)
    supplied_names = set(columns)
    if supplied_names != declared_names:
        missing = tuple(name for name in schema if name not in supplied_names)
        extra = tuple(name for name in columns if name not in declared_names)
        raise MarketFrameError(
            f"columns do not match schema; missing={missing!r}, extra={extra!r}"
        )

    validated: dict[str, npt.NDArray[Any]] = {}
    for name, declared_dtype in schema.items():
        column = columns[name]
        if type(column) is not np.ndarray:
            raise MarketFrameError(
                f"column {name!r} must be a NumPy ndarray, got {type(column).__name__}"
            )
        if column.ndim != 1:
            raise MarketFrameError(
                f"column {name!r} must be one-dimensional, got {column.ndim} dimensions"
            )
        if column.shape[0] != num_rows:
            raise MarketFrameError(
                f"column {name!r} has {column.shape[0]} rows, expected {num_rows}"
            )
        expected_dtype = np.dtype(declared_dtype)
        if column.dtype != expected_dtype:
            raise MarketFrameError(
                f"column {name!r} has dtype {column.dtype}, expected exact dtype "
                f"{declared_dtype}"
            )
        validated[name] = column
    return validated


def _from_validated_parts(
    *,
    dataset_ref: DatasetRef,
    clock_domain: ClockDomain,
    unit: TimeUnit,
    schema: Mapping[str, str],
    keys: _ImmutableArrayStorage,
    columns: Mapping[str, _ImmutableArrayStorage],
    admitted_lo: int,
    admitted_hi: int,
) -> MarketFrame:
    """Mint from already-validated metadata and arrays, preserving buffers."""
    frame = object.__new__(MarketFrame)
    object.__setattr__(frame, "_dataset_ref", dataset_ref)
    object.__setattr__(frame, "_clock_domain", clock_domain)
    object.__setattr__(frame, "_unit", unit)
    object.__setattr__(frame, "_schema", MappingProxyType(dict(schema)))
    object.__setattr__(frame, "_keys", keys)
    object.__setattr__(frame, "_columns", MappingProxyType(dict(columns)))
    object.__setattr__(frame, "_admitted_lo", admitted_lo)
    object.__setattr__(frame, "_admitted_hi", admitted_hi)
    return frame
