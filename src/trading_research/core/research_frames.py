"""Immutable columnar carriers for Phase-3 research stages."""

from __future__ import annotations

import re as _re
import typing as _typing
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research import _is_reserved_outcome_name
from trading_research.core.time import ClockDomain as _ClockDomain
from trading_research.core.time import TimeUnit as _TimeUnit

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue

__all__ = [
    "FittedPayload",
    "FittedState",
    "IntentFrame",
    "ModelOutputFrame",
    "OutcomeFrame",
    "RepresentationFrame",
    "TrainingTargetFrame",
]

_IDENTITY_KEY = _re.compile(r"^[0-9a-f]{64}$")
_BANNED_MODEL_OUTPUT_NAMES = frozenset(
    {"action", "buy", "direction", "position", "sell", "side", "signal"}
)
_INTENT_SCHEMA_IDS = frozenset(
    {"target_exposure_intent_v1", "bracketed_exposure_intent_v1"}
)
# The subset of the intent family that additionally carries one immutable
# per-row bracket instruction. Membership is what decides whether the two
# bracket channels are required or forbidden on a minted frame.
_BRACKETED_INTENT_SCHEMA_IDS = frozenset({"bracketed_exposure_intent_v1"})


@_dataclass(frozen=True, slots=True)
class _ImmutableArrayStorage:
    _buffer: bytes
    _dtype: _np.dtype[_np.generic]
    _length: int
    _shape: tuple[int, ...]

    @classmethod
    def _from_array(cls, array: _npt.NDArray[_typing.Any]) -> _ImmutableArrayStorage:
        contiguous = _np.ascontiguousarray(array)
        return cls(
            contiguous.tobytes(order="C"),
            contiguous.dtype,
            int(contiguous.size),
            tuple(int(dimension) for dimension in contiguous.shape),
        )

    def _descriptor(self) -> _npt.NDArray[_typing.Any]:
        descriptor = _np.frombuffer(self._buffer, dtype=self._dtype, count=self._length)
        descriptor = descriptor.reshape(self._shape)
        descriptor.setflags(write=False)
        return descriptor


def _validated_keys(
    keys: _npt.NDArray[_typing.Any], *, allow_empty: bool
) -> _npt.NDArray[_np.int64]:
    if type(keys) is not _np.ndarray or keys.ndim != 1:
        raise _ContractViolationError("frame keys must be a one-dimensional NumPy array")
    if keys.dtype != _np.dtype(_np.int64) or keys.dtype.metadata is not None:
        raise _ContractViolationError("frame keys must have exact metadata-free dtype int64")
    if not allow_empty and keys.size == 0:
        raise _ContractViolationError("dense frame keys must not be empty")
    if keys.size > 1 and _np.any(keys[1:] <= keys[:-1]):
        raise _ContractViolationError("frame keys must be strictly increasing and unique")
    return keys


def _validated_sources(
    source_identities: tuple[_DatasetIdentity, ...],
) -> tuple[_DatasetIdentity, ...]:
    if type(source_identities) is not tuple or not source_identities:
        raise _ContractViolationError("source_identities must be a non-empty exact tuple")
    if any(type(identity) is not _DatasetIdentity for identity in source_identities):
        raise _ContractViolationError("source_identities must contain exact DatasetIdentity values")
    return source_identities


def _validated_columns(
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    *,
    length: int,
    float64_only: bool,
) -> tuple[tuple[str, ...], dict[str, _ImmutableArrayStorage], dict[str, str]]:
    if not isinstance(columns, _Mapping) or not columns:
        raise _ContractViolationError("a research frame requires at least one column")
    names = tuple(columns)
    if any(type(name) is not str or not name for name in names) or len(set(names)) != len(names):
        raise _ContractViolationError("frame column names must be unique non-empty strings")
    storages: dict[str, _ImmutableArrayStorage] = {}
    schema: dict[str, str] = {}
    for name in names:
        values = columns[name]
        if type(values) is not _np.ndarray or values.ndim != 1:
            raise _ContractViolationError(f"column {name!r} must be a one-dimensional NumPy array")
        if values.dtype.metadata is not None:
            raise _ContractViolationError(f"column {name!r} dtype metadata is not supported")
        if float64_only:
            if values.dtype != _np.dtype(_np.float64):
                raise _ContractViolationError(f"column {name!r} must have exact dtype float64")
        elif not _np.issubdtype(values.dtype, _np.number) or _np.issubdtype(
            values.dtype, _np.complexfloating
        ):
            raise _ContractViolationError(f"column {name!r} must have a real numeric dtype")
        if values.shape[0] != length:
            raise _ContractViolationError(
                f"column {name!r} length {values.shape[0]} does not match key length {length}"
            )
        if _np.issubdtype(values.dtype, _np.floating) and _np.isinf(values).any():
            raise _ContractViolationError(f"column {name!r} contains positive or negative infinity")
        storages[name] = _ImmutableArrayStorage._from_array(values)
        schema[name] = values.dtype.name
    return names, storages, schema


def _validated_origins(
    names: tuple[str, ...], origins: _Mapping[str, _StageOrigin]
) -> _Mapping[str, _StageOrigin]:
    if not isinstance(origins, _Mapping) or tuple(origins) != names:
        raise _ContractViolationError("origin names and order must exactly match frame columns")
    if any(type(origins[name]) is not _StageOrigin for name in names):
        raise _ContractViolationError("every research-frame origin must be an exact StageOrigin")
    return _MappingProxyType({name: origins[name] for name in names})


def _validate_axis_tags(
    *, unit: _TimeUnit, clock_domain: _ClockDomain, source_identities: tuple[_DatasetIdentity, ...]
) -> None:
    if type(unit) is not _TimeUnit:
        raise _ContractViolationError("frame unit must be an exact TimeUnit")
    if type(clock_domain) is not _ClockDomain:
        raise _ContractViolationError("frame clock_domain must be an exact ClockDomain")
    _validated_sources(source_identities)


@_dataclass(frozen=True, slots=True)
class _ColumnStorage:
    keys: _ImmutableArrayStorage
    unit: _TimeUnit
    clock_domain: _ClockDomain
    source_identities: tuple[_DatasetIdentity, ...]
    schema: _Mapping[str, str]
    columns: _Mapping[str, _ImmutableArrayStorage]
    origins: _Mapping[str, _StageOrigin]


def _read_column(storage: _ColumnStorage, name: str) -> _npt.NDArray[_typing.Any]:
    if type(name) is not str:
        raise _ContractViolationError("column name must be a string")
    try:
        return storage.columns[name]._descriptor()
    except KeyError as exc:
        raise _ContractViolationError(f"unknown research-frame column {name!r}") from exc


def _read_origin(storage: _ColumnStorage, name: str) -> _StageOrigin:
    if type(name) is not str:
        raise _ContractViolationError("origin name must be a string")
    try:
        return storage.origins[name]
    except KeyError as exc:
        raise _ContractViolationError(f"unknown research-frame column {name!r}") from exc


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class RepresentationFrame:
    """Dense float64 representation columns on the complete evaluation axis."""

    _storage: _ColumnStorage

    def __init__(self) -> None:
        raise TypeError("RepresentationFrame instances are issued only by the research platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return dense evaluation keys as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._storage.keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._storage.unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._storage.clock_domain

    @property
    def num_rows(self) -> int:
        """Return the dense evaluation-row count."""
        return self._storage.keys._length

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._storage.source_identities

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return ordered representation columns."""
        return tuple(self._storage.columns)

    @property
    def schema(self) -> _Mapping[str, str]:
        """Return the immutable all-float64 schema."""
        return self._storage.schema

    def column(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one column as a fresh read-only descriptor."""
        return _read_column(self._storage, name)

    def origin(self, name: str) -> _StageOrigin:
        """Return the producing stage declaration for one column."""
        return _read_origin(self._storage, name)


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class ModelOutputFrame:
    """Generic numeric model columns on a sparse or dense evaluation axis."""

    _storage: _ColumnStorage

    def __init__(self) -> None:
        raise TypeError("ModelOutputFrame instances are issued only by the research platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return sparse or dense keys as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._storage.keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._storage.unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._storage.clock_domain

    @property
    def num_rows(self) -> int:
        """Return the emitted-row count."""
        return self._storage.keys._length

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._storage.source_identities

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return ordered generic output columns."""
        return tuple(self._storage.columns)

    @property
    def schema(self) -> _Mapping[str, str]:
        """Return the immutable numeric schema."""
        return self._storage.schema

    def column(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one column as a fresh read-only descriptor."""
        return _read_column(self._storage, name)

    def origin(self, name: str) -> _StageOrigin:
        """Return the producing stage declaration for one column."""
        return _read_origin(self._storage, name)


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class TrainingTargetFrame:
    """The sole platform-controlled target carrier accepted by model fitting."""

    _storage: _ColumnStorage

    def __init__(self) -> None:
        raise TypeError("TrainingTargetFrame instances are issued only by the outcome platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return target decision keys as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._storage.keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._storage.unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._storage.clock_domain

    @property
    def num_rows(self) -> int:
        """Return the target-row count."""
        return self._storage.keys._length

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._storage.source_identities

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return ordered reserved-namespace target columns."""
        return tuple(self._storage.columns)

    @property
    def schema(self) -> _Mapping[str, str]:
        """Return the immutable all-float64 schema."""
        return self._storage.schema

    def column(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one target column as a fresh read-only descriptor."""
        return _read_column(self._storage, name)

    def origin(self, name: str) -> _StageOrigin:
        """Return the producing OutcomeSpec declaration for one column."""
        return _read_origin(self._storage, name)


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class OutcomeFrame:
    """The sole platform-controlled outcome carrier accepted by evaluators."""

    _storage: _ColumnStorage

    def __init__(self) -> None:
        raise TypeError("OutcomeFrame instances are issued only by the outcome platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return outcome decision keys as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._storage.keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._storage.unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._storage.clock_domain

    @property
    def num_rows(self) -> int:
        """Return the outcome-row count."""
        return self._storage.keys._length

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._storage.source_identities

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return ordered reserved-namespace outcome columns."""
        return tuple(self._storage.columns)

    @property
    def schema(self) -> _Mapping[str, str]:
        """Return the immutable all-float64 schema."""
        return self._storage.schema

    def column(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one outcome column as a fresh read-only descriptor."""
        return _read_column(self._storage, name)

    def origin(self, name: str) -> _StageOrigin:
        """Return the producing OutcomeSpec declaration for one column."""
        return _read_origin(self._storage, name)


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class IntentFrame:
    """Sparse target-exposure intent preserving absent, abstain, and flat."""

    _keys: _ImmutableArrayStorage
    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _target_exposure: _ImmutableArrayStorage
    _intent_schema_id: str
    _take_profit_distance: _ImmutableArrayStorage | None
    _stop_loss_distance: _ImmutableArrayStorage | None

    def __init__(self) -> None:
        raise TypeError("IntentFrame instances are issued only by the decision platform")

    @property
    def intent_schema_id(self) -> str:
        """Return the validated target-exposure schema identifier."""
        return self._intent_schema_id

    @property
    def carries_brackets(self) -> bool:
        """Return whether this schema carries per-row bracket instructions."""
        return self._intent_schema_id in _BRACKETED_INTENT_SCHEMA_IDS

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return sparse or dense intent keys as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._clock_domain

    @property
    def num_rows(self) -> int:
        """Return the emitted intent-row count."""
        return self._keys._length

    @property
    def target_exposure(self) -> _npt.NDArray[_np.float64]:
        """Return exposure values as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._target_exposure._descriptor())

    @property
    def take_profit_distance(self) -> _npt.NDArray[_np.float64] | None:
        """Return per-row take-profit distances, or None when unsupported.

        A finite value is a positive distance in price units from the entry fill;
        NaN states that this instruction supplies no take profit of its own. The
        whole channel is None for a schema that carries no bracket at all, which
        is a different fact from a channel of NaN.
        """
        storage = self._take_profit_distance
        if storage is None:
            return None
        return _typing.cast("_npt.NDArray[_np.float64]", storage._descriptor())

    @property
    def stop_loss_distance(self) -> _npt.NDArray[_np.float64] | None:
        """Return per-row stop-loss distances, or None when unsupported."""
        storage = self._stop_loss_distance
        if storage is None:
            return None
        return _typing.cast("_npt.NDArray[_np.float64]", storage._descriptor())


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class FittedPayload:
    """Detached learned arrays and metadata with no artifact identity authority."""

    _arrays: _Mapping[str, _ImmutableArrayStorage]
    _metadata: _Mapping[str, _ParamValue]

    def __init__(
        self,
        *,
        arrays: _Mapping[str, _npt.NDArray[_typing.Any]],
        metadata: _Mapping[str, _ParamValue],
    ) -> None:
        """Detach and validate one identity-free plugin fit result."""
        if not isinstance(arrays, _Mapping):
            raise _ContractViolationError("FittedPayload arrays must be a mapping")
        names = tuple(arrays)
        if any(type(name) is not str or not name for name in names):
            raise _ContractViolationError(
                "FittedPayload array names must be non-empty strings"
            )
        array_storages: dict[str, _ImmutableArrayStorage] = {}
        for name in sorted(names):
            values = arrays[name]
            if type(values) is not _np.ndarray or values.dtype.hasobject:
                raise _ContractViolationError(
                    "FittedPayload arrays must be non-object NumPy arrays"
                )
            if values.dtype.metadata is not None or values.dtype.kind not in "biuf":
                raise _ContractViolationError(
                    "FittedPayload arrays must have metadata-free real numeric dtypes"
                )
            array_storages[name] = _ImmutableArrayStorage._from_array(values)
        if not isinstance(metadata, _Mapping):
            raise _ContractViolationError("FittedPayload metadata must be a mapping")
        from trading_research.core.features import canonical_params_json

        canonical_params_json(metadata)
        object.__setattr__(self, "_arrays", _MappingProxyType(array_storages))
        object.__setattr__(
            self,
            "_metadata",
            _MappingProxyType({name: metadata[name] for name in sorted(metadata)}),
        )

    @property
    def array_names(self) -> tuple[str, ...]:
        """Return canonical ordered learned-array names."""
        return tuple(self._arrays)

    def array(self, name: str) -> _npt.NDArray[_typing.Any]:
        """Return one learned array as a fresh read-only descriptor."""
        if type(name) is not str:
            raise _ContractViolationError("FittedPayload array name must be a string")
        try:
            return self._arrays[name]._descriptor()
        except KeyError as exc:
            raise _ContractViolationError(f"unknown FittedPayload array {name!r}") from exc

    @property
    def metadata(self) -> _Mapping[str, _ParamValue]:
        """Return immutable canonical metadata."""
        return self._metadata


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class FittedState:
    """Opaque, versionable arrays and JSON-safe metadata under a pinned identity."""

    _identity_key: str
    _payload: FittedPayload

    def __init__(self) -> None:
        raise TypeError("FittedState instances are issued only by the fitting platform")

    def identity_key(self) -> str:
        """Return the precomputed complete fitted-state identity."""
        return self._identity_key

    @property
    def payload(self) -> FittedPayload:
        """Return the validated identity-free learned content."""
        return self._payload


def _mint_column_frame(
    frame_type: type[RepresentationFrame]
    | type[ModelOutputFrame]
    | type[TrainingTargetFrame]
    | type[OutcomeFrame],
    *,
    keys: _npt.NDArray[_typing.Any],
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    source_identities: tuple[_DatasetIdentity, ...],
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _StageOrigin],
    allow_empty: bool,
    float64_only: bool,
) -> RepresentationFrame | ModelOutputFrame | TrainingTargetFrame | OutcomeFrame:
    validated_keys = _validated_keys(keys, allow_empty=allow_empty)
    _validate_axis_tags(unit=unit, clock_domain=clock_domain, source_identities=source_identities)
    names, storages, schema = _validated_columns(
        columns, length=int(validated_keys.size), float64_only=float64_only
    )
    validated_origins = _validated_origins(names, origins)
    frame = object.__new__(frame_type)
    storage = _ColumnStorage(
        keys=_ImmutableArrayStorage._from_array(validated_keys),
        unit=unit,
        clock_domain=clock_domain,
        source_identities=source_identities,
        schema=_MappingProxyType(schema),
        columns=_MappingProxyType(storages),
        origins=validated_origins,
    )
    object.__setattr__(frame, "_storage", storage)
    return frame


def _validate_sparse_bounds(keys: _npt.NDArray[_np.int64], view: _ViewSpan) -> None:
    if keys.size and (int(keys[0]) < view.eval_start.value or int(keys[-1]) > view.eval_end.value):
        raise _ContractViolationError("sparse frame keys must lie inside the evaluation span")


def _make_representation_frame(
    *,
    view: _ViewSpan,
    features: _FeatureFrame,
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _StageOrigin],
) -> RepresentationFrame:
    if type(view) is not _ViewSpan or type(features) is not _FeatureFrame:
        raise _ContractViolationError(
            "representation minting requires exact ViewSpan and FeatureFrame"
        )
    if (
        features.unit is not view.unit
        or features.clock_domain != view.clock_domain
        or features.source_identities != view.source_identities
    ):
        raise _ContractViolationError("FeatureFrame must share the representation ViewSpan axis")
    _validate_sparse_bounds(features.keys, view)
    if any(_is_reserved_outcome_name(name) for name in columns):
        raise _ContractViolationError(
            "representation output cannot use the reserved outcome namespace"
        )
    return _typing.cast(
        "RepresentationFrame",
        _mint_column_frame(
            RepresentationFrame,
            keys=features.keys,
            unit=features.unit,
            clock_domain=features.clock_domain,
            source_identities=features.source_identities,
            columns=columns,
            origins=origins,
            allow_empty=False,
            float64_only=True,
        ),
    )


def _make_model_output_frame(
    *,
    view: _ViewSpan,
    keys: _npt.NDArray[_typing.Any],
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _StageOrigin],
) -> ModelOutputFrame:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("model output minting requires an exact ViewSpan")
    validated_keys = _validated_keys(keys, allow_empty=True)
    _validate_sparse_bounds(validated_keys, view)
    for name in columns:
        if name.lower() in _BANNED_MODEL_OUTPUT_NAMES or _is_reserved_outcome_name(name):
            raise _ContractViolationError(f"model output column {name!r} uses reserved vocabulary")
    return _typing.cast(
        "ModelOutputFrame",
        _mint_column_frame(
            ModelOutputFrame,
            keys=validated_keys,
            unit=view.unit,
            clock_domain=view.clock_domain,
            source_identities=view.source_identities,
            columns=columns,
            origins=origins,
            allow_empty=True,
            float64_only=False,
        ),
    )


def _make_target_frame(
    frame_type: type[TrainingTargetFrame] | type[OutcomeFrame],
    *,
    view: _ViewSpan,
    keys: _npt.NDArray[_typing.Any],
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _StageOrigin],
) -> TrainingTargetFrame | OutcomeFrame:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("target minting requires an exact ViewSpan")
    validated_keys = _validated_keys(keys, allow_empty=False)
    _validate_sparse_bounds(validated_keys, view)
    if any(not _is_reserved_outcome_name(name) for name in columns):
        raise _ContractViolationError("outcome columns must use the reserved target namespace")
    return _typing.cast(
        "TrainingTargetFrame | OutcomeFrame",
        _mint_column_frame(
            frame_type,
            keys=validated_keys,
            unit=view.unit,
            clock_domain=view.clock_domain,
            source_identities=view.source_identities,
            columns=columns,
            origins=origins,
            allow_empty=False,
            float64_only=True,
        ),
    )


def _make_training_target_frame(
    *,
    view: _ViewSpan,
    keys: _npt.NDArray[_typing.Any],
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _StageOrigin],
) -> TrainingTargetFrame:
    return _typing.cast(
        "TrainingTargetFrame",
        _make_target_frame(
            TrainingTargetFrame, view=view, keys=keys, columns=columns, origins=origins
        ),
    )


def _make_outcome_frame(
    *,
    view: _ViewSpan,
    keys: _npt.NDArray[_typing.Any],
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    origins: _Mapping[str, _StageOrigin],
) -> OutcomeFrame:
    return _typing.cast(
        "OutcomeFrame",
        _make_target_frame(OutcomeFrame, view=view, keys=keys, columns=columns, origins=origins),
    )


def _make_intent_frame(
    *,
    view: _ViewSpan,
    intent_schema_id: str,
    keys: _npt.NDArray[_typing.Any],
    target_exposure: _npt.NDArray[_typing.Any],
    take_profit_distance: _npt.NDArray[_typing.Any] | None = None,
    stop_loss_distance: _npt.NDArray[_typing.Any] | None = None,
) -> IntentFrame:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("intent minting requires an exact ViewSpan")
    validated_keys = _validated_keys(keys, allow_empty=True)
    _validate_sparse_bounds(validated_keys, view)
    return _mint_intent_frame(
        unit=view.unit,
        clock_domain=view.clock_domain,
        intent_schema_id=intent_schema_id,
        keys=validated_keys,
        target_exposure=target_exposure,
        take_profit_distance=take_profit_distance,
        stop_loss_distance=stop_loss_distance,
    )


def _make_intent_frame_from_model_output(
    *,
    model_output: ModelOutputFrame,
    intent_schema_id: str,
    keys: _npt.NDArray[_typing.Any],
    target_exposure: _npt.NDArray[_typing.Any],
    take_profit_distance: _npt.NDArray[_typing.Any] | None = None,
    stop_loss_distance: _npt.NDArray[_typing.Any] | None = None,
) -> IntentFrame:
    if type(model_output) is not ModelOutputFrame:
        raise _ContractViolationError(
            "decision intent minting requires an exact ModelOutputFrame"
        )
    validated_keys = _validated_keys(keys, allow_empty=True)
    if validated_keys.size and not _np.isin(
        validated_keys,
        model_output.keys,
        assume_unique=True,
    ).all():
        raise _ContractViolationError("intent keys must be a subset of model output keys")
    return _mint_intent_frame(
        unit=model_output.unit,
        clock_domain=model_output.clock_domain,
        intent_schema_id=intent_schema_id,
        keys=validated_keys,
        target_exposure=target_exposure,
        take_profit_distance=take_profit_distance,
        stop_loss_distance=stop_loss_distance,
    )


def _validated_bracket_channel(
    values: _npt.NDArray[_typing.Any],
    *,
    field: str,
    length: int,
) -> _npt.NDArray[_np.float64]:
    """Validate one per-row bracket channel, refusing rather than reinterpreting.

    NaN is the only legal absence marker. Zero, negative and infinite distances
    are refused outright: each names a different, unverified execution semantic
    (an instant exit at the entry price, a bracket on the wrong side of the fill,
    and an unbounded bracket), and silently reinterpreting any of them would make
    an unaudited assumption look like a platform rule.
    """
    if type(values) is not _np.ndarray or values.ndim != 1:
        raise _ContractViolationError(f"{field} must be a one-dimensional NumPy array")
    if values.dtype != _np.dtype(_np.float64) or values.dtype.metadata is not None:
        raise _ContractViolationError(f"{field} must have exact metadata-free dtype float64")
    if values.shape[0] != length:
        raise _ContractViolationError(f"{field} length must match intent key length")
    if _np.isinf(values).any():
        raise _ContractViolationError(f"{field} contains positive or negative infinity")
    finite = values[_np.isfinite(values)]
    if finite.size and _np.any(finite <= 0.0):
        raise _ContractViolationError(
            f"finite {field} values must be strictly positive; NaN is the only absence marker"
        )
    return _typing.cast("_npt.NDArray[_np.float64]", values)


def _mint_intent_frame(
    *,
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    intent_schema_id: str,
    keys: _npt.NDArray[_np.int64],
    target_exposure: _npt.NDArray[_typing.Any],
    take_profit_distance: _npt.NDArray[_typing.Any] | None = None,
    stop_loss_distance: _npt.NDArray[_typing.Any] | None = None,
) -> IntentFrame:
    if intent_schema_id not in _INTENT_SCHEMA_IDS:
        raise _ContractViolationError(
            f"unknown intent_schema_id {intent_schema_id!r}; "
            f"V1 supports {sorted(_INTENT_SCHEMA_IDS)!r}"
        )
    if type(target_exposure) is not _np.ndarray or target_exposure.ndim != 1:
        raise _ContractViolationError("target_exposure must be a one-dimensional NumPy array")
    if (
        target_exposure.dtype != _np.dtype(_np.float64)
        or target_exposure.dtype.metadata is not None
    ):
        raise _ContractViolationError("target_exposure must have exact metadata-free dtype float64")
    if target_exposure.shape[0] != keys.shape[0]:
        raise _ContractViolationError("target_exposure length must match intent key length")
    if _np.isinf(target_exposure).any():
        raise _ContractViolationError("target_exposure contains positive or negative infinity")
    finite = target_exposure[_np.isfinite(target_exposure)]
    if finite.size and (_np.any(finite < -1.0) or _np.any(finite > 1.0)):
        raise _ContractViolationError("finite target_exposure values must lie in [-1, +1]")

    carries_brackets = intent_schema_id in _BRACKETED_INTENT_SCHEMA_IDS
    supplied = (take_profit_distance is not None, stop_loss_distance is not None)
    if carries_brackets and not all(supplied):
        raise _ContractViolationError(
            f"intent schema {intent_schema_id!r} requires both bracket channels"
        )
    if not carries_brackets and any(supplied):
        raise _ContractViolationError(
            f"intent schema {intent_schema_id!r} does not carry bracket channels"
        )

    take_profit_storage: _ImmutableArrayStorage | None = None
    stop_loss_storage: _ImmutableArrayStorage | None = None
    if take_profit_distance is not None and stop_loss_distance is not None:
        length = int(keys.shape[0])
        take_profit_storage = _ImmutableArrayStorage._from_array(
            _validated_bracket_channel(
                take_profit_distance,
                field="take_profit_distance",
                length=length,
            )
        )
        stop_loss_storage = _ImmutableArrayStorage._from_array(
            _validated_bracket_channel(
                stop_loss_distance,
                field="stop_loss_distance",
                length=length,
            )
        )

    frame = object.__new__(IntentFrame)
    object.__setattr__(frame, "_keys", _ImmutableArrayStorage._from_array(keys))
    object.__setattr__(frame, "_unit", unit)
    object.__setattr__(frame, "_clock_domain", clock_domain)
    object.__setattr__(
        frame,
        "_target_exposure",
        _ImmutableArrayStorage._from_array(target_exposure),
    )
    object.__setattr__(frame, "_intent_schema_id", intent_schema_id)
    object.__setattr__(frame, "_take_profit_distance", take_profit_storage)
    object.__setattr__(frame, "_stop_loss_distance", stop_loss_storage)
    return frame


def _make_fitted_state(
    *,
    identity_key: str,
    payload: FittedPayload,
) -> FittedState:
    if type(identity_key) is not str or _IDENTITY_KEY.fullmatch(identity_key) is None:
        raise _ContractViolationError(
            "FittedState identity_key must be 64 lowercase hex characters"
        )
    if type(payload) is not FittedPayload:
        raise _ContractViolationError("FittedState payload must be an exact FittedPayload")
    state = object.__new__(FittedState)
    object.__setattr__(state, "_identity_key", identity_key)
    object.__setattr__(state, "_payload", payload)
    return state
