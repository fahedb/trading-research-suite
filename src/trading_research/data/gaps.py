"""Arithmetic discontinuities observed on one native-key axis.

``GapIndex`` deliberately records only integer differences between adjacent
observations.  It does not infer why a discontinuity exists or attach market,
calendar, or provenance meaning to it.
"""

from __future__ import annotations

import typing as _typing
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.frames import MarketFrame as _MarketFrame

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

    from trading_research.core.identity import DatasetRef as _DatasetRef
    from trading_research.core.time import ClockDomain as _ClockDomain
    from trading_research.core.time import TimeUnit as _TimeUnit

__all__ = ["GapIndex"]


@_dataclass(frozen=True, slots=True)
class _Int64Storage:
    """Immutable bytes and length for one exact one-dimensional int64 column."""

    _buffer: bytes
    _length: int

    @classmethod
    def _from_array(
        cls, array: _npt.NDArray[_np.int64]
    ) -> _Int64Storage:
        """Copy one computed column into compact immutable storage."""
        return cls(_buffer=array.tobytes(order="C"), _length=int(array.size))

    def _descriptor(self) -> _npt.NDArray[_np.int64]:
        """Return a fresh read-only descriptor over the immutable bytes."""
        descriptor = _np.frombuffer(
            self._buffer,
            dtype=_np.int64,
            count=self._length,
        )
        descriptor.setflags(write=False)
        return descriptor


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class GapIndex:
    """Compact arithmetic facts for discontinuous adjacent native keys.

    Instances retain only transitions whose observed delta differs from the
    caller-declared nominal interval.  They make no claim about the cause or
    significance of any retained transition.
    """

    _dataset_ref: _DatasetRef
    _clock_domain: _ClockDomain
    _unit: _TimeUnit
    _nominal_interval: int
    _num_gaps: int
    _prev_key: _Int64Storage
    _next_key: _Int64Storage
    _delta: _Int64Storage
    _deviation: _Int64Storage

    def __init__(self) -> None:
        """Refuse direct construction; facts come only from ``from_frame``."""
        raise TypeError("GapIndex instances are issued only by GapIndex.from_frame")

    @classmethod
    def from_frame(
        cls,
        frame: _MarketFrame,
        *,
        nominal_interval: int,
    ) -> GapIndex:
        """Compute discontinuous adjacent transitions from one issued frame.

        ``nominal_interval`` is expressed in ``frame.unit``.  It is explicit:
        no interval is parsed, inferred, converted, or supplied by default.

        Raises:
            ContractViolationError: If the producer inputs are invalid or the
                native-key differences violate strict increase after int64
                arithmetic.
        """
        if type(frame) is not _MarketFrame:
            raise _ContractViolationError(
                f"frame must be a MarketFrame, got {type(frame).__name__}"
            )
        if isinstance(nominal_interval, bool) or not isinstance(
            nominal_interval, int
        ):
            raise _ContractViolationError(
                "nominal_interval must be a positive Python int"
            )
        if nominal_interval <= 0:
            raise _ContractViolationError("nominal_interval must be positive")
        if nominal_interval > int(_np.iinfo(_np.int64).max):
            raise _ContractViolationError(
                "nominal_interval must fit in a positive signed int64"
            )

        keys = frame.keys
        full_delta = _np.diff(keys)
        if full_delta.size and int(full_delta.min()) <= 0:
            raise _ContractViolationError(
                "frame key differences must remain positive under int64 arithmetic"
            )

        retained = _np.flatnonzero(full_delta != nominal_interval)
        prev_key = keys[:-1][retained]
        next_key = keys[1:][retained]
        delta = full_delta[retained]
        deviation = delta - nominal_interval
        gap_index = object.__new__(cls)
        object.__setattr__(gap_index, "_dataset_ref", frame.dataset_ref)
        object.__setattr__(gap_index, "_clock_domain", frame.clock_domain)
        object.__setattr__(gap_index, "_unit", frame.unit)
        object.__setattr__(gap_index, "_nominal_interval", nominal_interval)
        object.__setattr__(gap_index, "_num_gaps", int(delta.size))
        object.__setattr__(
            gap_index,
            "_prev_key",
            _Int64Storage._from_array(prev_key),
        )
        object.__setattr__(
            gap_index,
            "_next_key",
            _Int64Storage._from_array(next_key),
        )
        object.__setattr__(
            gap_index,
            "_delta",
            _Int64Storage._from_array(delta),
        )
        object.__setattr__(
            gap_index,
            "_deviation",
            _Int64Storage._from_array(deviation),
        )
        return gap_index

    @property
    def dataset_ref(self) -> _DatasetRef:
        """Exact source identity inherited from the frame."""
        return self._dataset_ref

    @property
    def clock_domain(self) -> _ClockDomain:
        """Opaque clock domain shared by the retained native keys."""
        return self._clock_domain

    @property
    def unit(self) -> _TimeUnit:
        """Resolution of every integer in this index."""
        return self._unit

    @property
    def nominal_interval(self) -> int:
        """Caller-declared adjacent-key interval in this index's unit."""
        return self._nominal_interval

    @property
    def num_gaps(self) -> int:
        """Number of retained discontinuous transitions."""
        return self._num_gaps

    @property
    def prev_key(self) -> _npt.NDArray[_np.int64]:
        """Earlier observed key for each retained transition."""
        return self._prev_key._descriptor()

    @property
    def next_key(self) -> _npt.NDArray[_np.int64]:
        """Later observed key for each retained transition."""
        return self._next_key._descriptor()

    @property
    def delta(self) -> _npt.NDArray[_np.int64]:
        """Exact adjacent-key difference for each retained transition."""
        return self._delta._descriptor()

    @property
    def deviation(self) -> _npt.NDArray[_np.int64]:
        """Retained delta minus the caller-declared nominal interval."""
        return self._deviation._descriptor()
