"""Small, immutable contracts shared by Phase-3 research stages.

This module contains descriptors and declarations only.  It performs no data
loading, plugin execution, hashing, persistence, or registry lookup.
"""

from __future__ import annotations

import math as _math
import typing as _typing
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.frames import MarketFrame as _MarketFrame
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.time import ClockDomain as _ClockDomain
from trading_research.core.time import NativeTimeKey as _NativeTimeKey
from trading_research.core.time import TimeUnit as _TimeUnit

if _typing.TYPE_CHECKING:
    from collections.abc import Sequence as _Sequence

    import numpy.typing as _npt

    from trading_research.core.research_frames import RepresentationFrame as _RepresentationFrame

__all__ = [
    "BoundaryPolicy",
    "CorpusModelInputs",
    "MetricValue",
    "ModelInputs",
    "OutcomeRequest",
    "StageOrigin",
    "ToleranceSpec",
    "ViewSpan",
]


class BoundaryPolicy(_StrEnum):
    """Behaviour when a complete forward outcome horizon is unavailable."""

    NAN = "nan"


def _validated_params(params: _Mapping[str, _ParamValue]) -> _Mapping[str, _ParamValue]:
    """Validate and detach one canonical parameter mapping."""
    _canonical_params_json(params)
    return _MappingProxyType({name: params[name] for name in sorted(params)})


def _validated_name(value: object, *, field: str) -> str:
    if type(value) is not str or not value:
        raise _ContractViolationError(f"{field} must be a non-empty string")
    return value


def _is_reserved_outcome_name(name: object) -> bool:
    """Return whether ``name`` is in the exact reserved outcome namespace."""
    return type(name) is str and (name == "target" or name.startswith("target."))


@_dataclass(frozen=True, slots=True)
class ViewSpan:
    """Bounded-view descriptor with no row, column, loader, or grant authority."""

    source_identities: tuple[_DatasetIdentity, ...]
    unit: _TimeUnit
    clock_domain: _ClockDomain
    context_start: _NativeTimeKey
    eval_start: _NativeTimeKey
    eval_end: _NativeTimeKey

    def __post_init__(self) -> None:
        """Validate one ordered native-time span and detach its identity tuple."""
        identities = self.source_identities
        if type(identities) is not tuple or not identities:
            raise _ContractViolationError(
                "ViewSpan source_identities must be a non-empty exact tuple"
            )
        if any(type(identity) is not _DatasetIdentity for identity in identities):
            raise _ContractViolationError(
                "ViewSpan source_identities must contain exact DatasetIdentity values"
            )
        if len(set(identities)) != len(identities):
            raise _ContractViolationError("ViewSpan source_identities must be unique")
        if type(self.unit) is not _TimeUnit:
            raise _ContractViolationError("ViewSpan unit must be an exact TimeUnit")
        if type(self.clock_domain) is not _ClockDomain:
            raise _ContractViolationError("ViewSpan clock_domain must be an exact ClockDomain")
        for field in ("context_start", "eval_start", "eval_end"):
            key = getattr(self, field)
            if type(key) is not _NativeTimeKey:
                raise _ContractViolationError(f"ViewSpan {field} must be an exact NativeTimeKey")
            if key.unit is not self.unit or key.clock_domain != self.clock_domain:
                raise _ContractViolationError(
                    f"ViewSpan {field} must share the span unit and clock domain"
                )
        if self.context_start > self.eval_start:
            raise _ContractViolationError("ViewSpan context_start must not follow eval_start")
        if self.eval_start > self.eval_end:
            raise _ContractViolationError("ViewSpan eval_start must not follow eval_end")


@_dataclass(frozen=True, slots=True)
class StageOrigin:
    """Identity-bearing declaration that produced one research-stage column."""

    plugin_id: str
    version: int
    params: _Mapping[str, _ParamValue]

    def __post_init__(self) -> None:
        """Validate identity fields and detach parameters from caller mutation."""
        _validated_name(self.plugin_id, field="plugin_id")
        if type(self.version) is not int or self.version <= 0:
            raise _ContractViolationError("stage version must be a positive Python int")
        object.__setattr__(self, "params", _validated_params(self.params))


@_dataclass(frozen=True, slots=True)
class ToleranceSpec:
    """Declared identity-bearing floating comparison tolerance."""

    atol: float
    rtol: float
    reason: str

    def __post_init__(self) -> None:
        """Require finite non-negative bounds and a visible reason."""
        for field in ("atol", "rtol"):
            value = getattr(self, field)
            if type(value) is not float or not _math.isfinite(value) or value < 0.0:
                raise _ContractViolationError(f"{field} must be a finite non-negative float")
        if type(self.reason) is not str or not self.reason.strip():
            raise _ContractViolationError("tolerance reason must be a non-empty string")


@_dataclass(frozen=True, slots=True)
class MetricValue:
    """One metric value together with the rows that actually informed it."""

    name: str
    value: float
    effective_samples: int

    def __post_init__(self) -> None:
        """Validate the finite-or-NaN scalar and non-negative sample count."""
        _validated_name(self.name, field="metric name")
        if type(self.value) is not float or _math.isinf(self.value):
            raise _ContractViolationError("metric value must be a float and may be finite or NaN")
        if type(self.effective_samples) is not int or self.effective_samples < 0:
            raise _ContractViolationError("effective_samples must be a non-negative Python int")


@_dataclass(frozen=True, slots=True)
class ModelInputs:
    """The complete frame-only input surface of a ResearchModel."""

    features: _FeatureFrame
    representation: _RepresentationFrame | None = None

    def __post_init__(self) -> None:
        """Require exact carriers and aligned optional representation rows."""
        if type(self.features) is not _FeatureFrame:
            raise _ContractViolationError("ModelInputs features must be an exact FeatureFrame")
        representation = self.representation
        if representation is None:
            return
        from trading_research.core.research_frames import RepresentationFrame

        if type(representation) is not RepresentationFrame:
            raise _ContractViolationError(
                "ModelInputs representation must be an exact RepresentationFrame or None"
            )
        if (
            representation.unit is not self.features.unit
            or representation.clock_domain != self.features.clock_domain
            or representation.source_identities != self.features.source_identities
            or not _np.array_equal(representation.keys, self.features.keys)
        ):
            raise _ContractViolationError(
                "ModelInputs representation must align exactly with the FeatureFrame"
            )


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class CorpusModelInputs:
    """Platform-issued immutable numeric inputs for cross-run model fitting.

    The carrier deliberately has no native-time frame semantics and exposes no
    source paths, run artifacts, sample identifiers, or decision keys.  Those
    provenance and grouping facts remain in the training-bundle composition
    root; a corpus model receives only its declared predictors, targets, and
    deterministic sample weights.
    """

    _inputs: _Mapping[str, _ImmutableArrayStorage]
    _targets: _Mapping[str, _ImmutableArrayStorage]
    _weights: _ImmutableArrayStorage
    _num_rows: int

    def __init__(self) -> None:
        """Refuse direct construction; only the corpus platform mints inputs."""
        raise TypeError("CorpusModelInputs instances are issued only by the corpus platform")

    @property
    def num_rows(self) -> int:
        """Return the exact number of corpus samples."""
        return self._num_rows

    @property
    def input_names(self) -> tuple[str, ...]:
        """Return the ordered predictor schema."""
        return tuple(self._inputs)

    @property
    def target_names(self) -> tuple[str, ...]:
        """Return the ordered target schema."""
        return tuple(self._targets)

    def input(self, name: str) -> _npt.NDArray[_np.float64]:
        """Return one predictor as a fresh read-only NumPy descriptor."""
        return _typing.cast("_npt.NDArray[_np.float64]", _corpus_column(self._inputs, name))

    def target(self, name: str) -> _npt.NDArray[_np.float64]:
        """Return one target as a fresh read-only NumPy descriptor."""
        return _typing.cast("_npt.NDArray[_np.float64]", _corpus_column(self._targets, name))

    @property
    def weights(self) -> _npt.NDArray[_np.float64]:
        """Return positive deterministic sample weights as a read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._weights._descriptor())


@_dataclass(frozen=True, slots=True)
class _ImmutableArrayStorage:
    _buffer: bytes
    _dtype: _np.dtype[_np.generic]
    _length: int

    @classmethod
    def _from_array(cls, array: _npt.NDArray[_typing.Any]) -> _ImmutableArrayStorage:
        contiguous = _np.ascontiguousarray(array)
        return cls(contiguous.tobytes(order="C"), contiguous.dtype, int(contiguous.size))

    def _descriptor(self) -> _npt.NDArray[_typing.Any]:
        descriptor = _np.frombuffer(self._buffer, dtype=self._dtype, count=self._length)
        descriptor.setflags(write=False)
        return descriptor


def _corpus_column(
    columns: _Mapping[str, _ImmutableArrayStorage], name: object
) -> _npt.NDArray[_typing.Any]:
    if type(name) is not str:
        raise _ContractViolationError("corpus column name must be a string")
    try:
        return columns[name]._descriptor()
    except KeyError as exc:
        raise _ContractViolationError(f"unknown corpus column {name!r}") from exc


def _make_corpus_model_inputs(
    *,
    inputs: _Mapping[str, _npt.NDArray[_typing.Any]],
    targets: _Mapping[str, _npt.NDArray[_typing.Any]],
    weights: _npt.NDArray[_typing.Any],
) -> CorpusModelInputs:
    """Mint one detached, finite float64 corpus carrier."""
    input_storages, rows = _validated_corpus_columns(inputs, label="input", expected_rows=None)
    target_storages, target_rows = _validated_corpus_columns(
        targets,
        label="target",
        expected_rows=rows,
    )
    if target_rows != rows:  # pragma: no cover - helper validates this directly
        raise AssertionError("corpus target rows differ from inputs")
    if type(weights) is not _np.ndarray or weights.ndim != 1:
        raise _ContractViolationError("corpus weights must be a one-dimensional NumPy array")
    if weights.dtype != _np.dtype(_np.float64) or weights.dtype.metadata is not None:
        raise _ContractViolationError("corpus weights must have exact metadata-free dtype float64")
    if weights.shape[0] != rows:
        raise _ContractViolationError("corpus weights must align with every sample")
    if not _np.isfinite(weights).all() or _np.any(weights <= 0.0):
        raise _ContractViolationError("corpus weights must be finite and strictly positive")
    result = object.__new__(CorpusModelInputs)
    object.__setattr__(result, "_inputs", _MappingProxyType(input_storages))
    object.__setattr__(result, "_targets", _MappingProxyType(target_storages))
    object.__setattr__(result, "_weights", _ImmutableArrayStorage._from_array(weights))
    object.__setattr__(result, "_num_rows", rows)
    return result


def _validated_corpus_columns(
    value: object,
    *,
    label: str,
    expected_rows: int | None,
) -> tuple[dict[str, _ImmutableArrayStorage], int]:
    if not isinstance(value, _Mapping) or not value:
        raise _ContractViolationError(f"corpus {label} columns must be a non-empty mapping")
    columns = _typing.cast("_Mapping[object, object]", value)
    names = tuple(columns)
    if any(type(name) is not str or not name for name in names) or len(set(names)) != len(names):
        raise _ContractViolationError(
            f"corpus {label} column names must be unique non-empty strings"
        )
    rows = expected_rows
    storages: dict[str, _ImmutableArrayStorage] = {}
    for raw_name in names:
        name = _typing.cast("str", raw_name)
        array = columns[raw_name]
        if type(array) is not _np.ndarray or array.ndim != 1:
            raise _ContractViolationError(
                f"corpus {label} column {name!r} must be a one-dimensional NumPy array"
            )
        if array.dtype != _np.dtype(_np.float64) or array.dtype.metadata is not None:
            raise _ContractViolationError(
                f"corpus {label} column {name!r} must have exact metadata-free dtype float64"
            )
        if rows is None:
            rows = int(array.shape[0])
            if rows <= 0:
                raise _ContractViolationError("corpus must contain at least one sample")
        if array.shape[0] != rows:
            raise _ContractViolationError(f"corpus {label} columns must have equal lengths")
        if not _np.isfinite(array).all():
            raise _ContractViolationError(
                f"corpus {label} column {name!r} must contain only finite values"
            )
        storages[name] = _ImmutableArrayStorage._from_array(array)
    if rows is None:  # pragma: no cover - non-empty columns set it
        raise AssertionError("corpus rows were not established")
    return storages, rows


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class OutcomeRequest:
    """Platform-minted forward axis exposing only one spec's declared columns."""

    _keys: _ImmutableArrayStorage
    _columns: _Mapping[str, _ImmutableArrayStorage]
    _decision_offset: int
    _decision_length: int

    def __init__(self) -> None:
        """Refuse direct construction; only the outcome platform mints requests."""
        raise TypeError("OutcomeRequest instances are issued only by the outcome platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return the complete resolved forward axis as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._keys._descriptor())

    def column(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one declared market column as a fresh read-only descriptor."""
        if type(name) is not str:
            raise _ContractViolationError("outcome request column name must be a string")
        try:
            return self._columns[name]._descriptor()
        except KeyError as exc:
            raise _ContractViolationError(f"unknown outcome request column {name!r}") from exc

    @property
    def decision_offset(self) -> int:
        """First resolved-axis position for which targets are requested."""
        return self._decision_offset

    @property
    def decision_length(self) -> int:
        """Number of consecutive decision rows for which targets are requested."""
        return self._decision_length


def _make_outcome_request(
    frame: _MarketFrame,
    *,
    required_columns: _Sequence[str],
    decision_offset: int,
    decision_length: int,
) -> OutcomeRequest:
    """Mint one bounded forward request from an already-authorized exact frame."""
    if type(frame) is not _MarketFrame:
        raise _ContractViolationError("outcome source must be an exact MarketFrame")
    if type(required_columns) not in {tuple, list} or not required_columns:
        raise _ContractViolationError("required_columns must be a non-empty tuple or list")
    names = tuple(required_columns)
    if any(type(name) is not str or not name for name in names) or len(set(names)) != len(names):
        raise _ContractViolationError("required_columns must contain unique non-empty strings")
    if any(_is_reserved_outcome_name(name) for name in names):
        raise _ContractViolationError("an OutcomeSpec cannot read the reserved outcome namespace")
    if type(decision_offset) is not int or decision_offset < 0:
        raise _ContractViolationError("decision_offset must be a non-negative Python int")
    if type(decision_length) is not int or decision_length <= 0:
        raise _ContractViolationError("decision_length must be a positive Python int")
    if decision_offset + decision_length > frame.num_rows:
        raise _ContractViolationError("decision range lies outside the resolved outcome axis")

    request = object.__new__(OutcomeRequest)
    object.__setattr__(request, "_keys", _ImmutableArrayStorage._from_array(frame.keys))
    object.__setattr__(
        request,
        "_columns",
        _MappingProxyType(
            {name: _ImmutableArrayStorage._from_array(frame.column(name)) for name in names}
        ),
    )
    object.__setattr__(request, "_decision_offset", decision_offset)
    object.__setattr__(request, "_decision_length", decision_length)
    return request
