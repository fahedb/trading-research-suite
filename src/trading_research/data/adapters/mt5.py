"""Private decoder for MT5 exporter-v1 Parquet partitions.

The physical field names and Arrow representations belong here, never in
``core``.  Native keys remain opaque signed integers tagged once at the frame
level; this module performs no calendar or civil-time conversion.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

import numpy as _np
import numpy.typing as _npt
import pyarrow as _pa  # type: ignore[import-untyped]
import pyarrow.compute as _pc  # type: ignore[import-untyped]
import pyarrow.parquet as _pq  # type: ignore[import-untyped]

from trading_research.core.errors import PartitionReadError as _PartitionReadError
from trading_research.data.manifests import ColumnPurpose as _ColumnPurpose

if _TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import BinaryIO

    from trading_research.data.manifests import NormalizedManifest

__all__: list[str] = []

type _Array = _npt.NDArray[_np.generic]

_PHYSICAL_KEY_COLUMN = "mt5_time_raw"
_DISPLAY_ONLY_COLUMN = "timestamp_mt5_encoded"


@_dataclass(frozen=True, slots=True)
class _PhysicalColumn:
    """One pinned exporter-v1 physical representation."""

    name: str
    arrow_type: _pa.DataType
    numpy_dtype: _np.dtype[_np.generic] | None


# Established against the authorized real 2020--2023 TRAIN smoke.  The
# timestamp duplicate is checked as physical evidence but is never projected
# or exposed as a computational column.
_EXPORTER_V1_SCHEMA = (
    _PhysicalColumn(_PHYSICAL_KEY_COLUMN, _pa.int64(), _np.dtype("int64")),
    _PhysicalColumn(_DISPLAY_ONLY_COLUMN, _pa.timestamp("ns"), None),
    _PhysicalColumn("open", _pa.float64(), _np.dtype("float64")),
    _PhysicalColumn("high", _pa.float64(), _np.dtype("float64")),
    _PhysicalColumn("low", _pa.float64(), _np.dtype("float64")),
    _PhysicalColumn("close", _pa.float64(), _np.dtype("float64")),
    _PhysicalColumn("tick_volume", _pa.int64(), _np.dtype("int64")),
    _PhysicalColumn("spread", _pa.int32(), _np.dtype("int32")),
    _PhysicalColumn("real_volume", _pa.int64(), _np.dtype("int64")),
)
_PHYSICAL_BY_NAME = _MappingProxyType(
    {column.name: column for column in _EXPORTER_V1_SCHEMA}
)


@_dataclass(frozen=True, slots=True)
class _DecodedPartition:
    """Private NumPy carrier for one already-restricted admitted slice."""

    keys: _Array
    columns: Mapping[str, _Array]


def _value_dtype_table() -> Mapping[str, _np.dtype[_np.generic]]:
    """Return immutable vendor-neutral dtypes for public value columns."""
    return _MappingProxyType(
        {
            column.name: column.numpy_dtype
            for column in _EXPORTER_V1_SCHEMA
            if column.numpy_dtype is not None and column.name != _PHYSICAL_KEY_COLUMN
        }
    )


def _validate_target_schema(
    manifest: NormalizedManifest,
    value_columns: Sequence[str],
) -> Mapping[str, _np.dtype[_np.generic]]:
    """Require target declarations to agree exactly with exporter-v1 facts.

    Legacy manifests honestly carry no dtype strings, so their physical types
    are governed solely by the pinned exporter table.  Format-1.1 targets do
    carry types and must name the corresponding NumPy dtype exactly.
    """
    if manifest.adapter_id != "mt5":
        raise _PartitionReadError(
            f"unsupported market-data adapter {manifest.adapter_id!r}; Slice 1D supports 'mt5'"
        )

    supported = _value_dtype_table()
    by_name = {column.name: column for column in manifest.columns}
    for column in manifest.columns:
        if column.purpose is _ColumnPurpose.TIME_KEY:
            if column.name not in {"native_key", _PHYSICAL_KEY_COLUMN}:
                raise _PartitionReadError(
                    f"target time-key column {column.name!r} is not an MT5 native-key field"
                )
            if column.dtype is not None and column.dtype != "int64":
                raise _PartitionReadError(
                    "target time-key dtype must be exactly 'int64', "
                    f"got {column.dtype!r}"
                )
            continue

        if column.purpose is _ColumnPurpose.DISPLAY_ONLY:
            if column.name != _DISPLAY_ONLY_COLUMN:
                raise _PartitionReadError(
                    f"unsupported MT5 display-only target column {column.name!r}"
                )
            if column.dtype is not None and column.dtype != "datetime64[ns]":
                raise _PartitionReadError(
                    "target display-only timestamp dtype must be exactly "
                    f"'datetime64[ns]', got {column.dtype!r}"
                )
            continue

        physical = supported.get(column.name)
        if physical is None:
            raise _PartitionReadError(
                f"MT5 exporter-v1 has no loadable value column {column.name!r}"
            )
        if column.dtype is not None and column.dtype != physical.name:
            raise _PartitionReadError(
                f"target column {column.name!r} declares dtype {column.dtype!r}; "
                f"MT5 exporter-v1 requires {physical.name!r}"
            )

    resolved: dict[str, _np.dtype[_np.generic]] = {}
    for name in value_columns:
        physical = supported[name]
        if by_name[name].purpose is not _ColumnPurpose.VALUE:  # pragma: no cover - defensive
            raise _PartitionReadError(f"target column {name!r} is not a value column")
        resolved[name] = physical
    return _MappingProxyType(resolved)


def _schema_signature(schema: _pa.Schema) -> tuple[tuple[str, str, bool], ...]:
    """Return stable physical field evidence for errors and documentation."""
    return tuple((field.name, str(field.type), field.nullable) for field in schema)


def _expected_schema_signature() -> tuple[tuple[str, str], ...]:
    """Return the pinned name/type table, excluding incidental nullability."""
    return tuple((column.name, str(column.arrow_type)) for column in _EXPORTER_V1_SCHEMA)


def _validate_physical_schema(schema: _pa.Schema) -> None:
    """Refuse any field-name, order, or Arrow-type drift."""
    observed = tuple((field.name, str(field.type)) for field in schema)
    expected = _expected_schema_signature()
    if observed != expected:
        raise _PartitionReadError(
            "MT5 exporter-v1 physical schema mismatch; "
            f"expected {expected!r}, observed {_schema_signature(schema)!r}"
        )


def _array_to_numpy(
    array: _pa.ChunkedArray,
    expected: _np.dtype[_np.generic],
    *,
    column_name: str,
    path_label: str,
) -> _Array:
    """Materialize one admitted Arrow column without casts or object fallback."""
    try:
        combined = array.combine_chunks()
        values = _np.asarray(combined.to_numpy(zero_copy_only=False))
    except (_pa.ArrowException, ValueError, TypeError) as exc:
        raise _PartitionReadError(
            f"cannot convert admitted Arrow column {column_name!r} from "
            f"{path_label} to NumPy: {exc}"
        ) from exc
    if values.ndim != 1:
        raise _PartitionReadError(
            f"decoded column {column_name!r} must be one-dimensional, got {values.shape}"
        )
    if values.dtype != expected:
        raise _PartitionReadError(
            f"decoded column {column_name!r} dtype {values.dtype} differs from "
            f"required {expected}; "
            "silent casts are forbidden"
        )
    return values


def _restrict_to_admission(
    table: _pa.Table,
    *,
    admitted_first_key: int,
    admitted_last_key: int,
    path_label: str,
) -> _pa.Table:
    """Filter physical Arrow rows to the target-authoritative admitted span."""
    try:
        keys = table.column(_PHYSICAL_KEY_COLUMN)
        lower = _pc.greater_equal(keys, _pa.scalar(admitted_first_key, type=_pa.int64()))
        upper = _pc.less_equal(keys, _pa.scalar(admitted_last_key, type=_pa.int64()))
        return table.filter(_pc.and_kleene(lower, upper))
    except (_pa.ArrowException, ValueError, TypeError) as exc:
        raise _PartitionReadError(
            f"cannot restrict Parquet partition {path_label} to target admission: {exc}"
        ) from exc


def _validate_admitted_arrow_columns(
    table: _pa.Table,
    *,
    projected: tuple[str, ...],
    path_label: str,
) -> None:
    """Reject admitted nulls and Arrow dtype drift before NumPy conversion."""
    for name in projected:
        column = table.column(name)
        if column.null_count:
            if name == _PHYSICAL_KEY_COLUMN:
                detail = "native keys contain nulls"
            else:
                detail = f"column {name!r} contains nulls"
            raise _PartitionReadError(f"partition {path_label} admitted {detail}")
    for name in projected:
        observed = table.schema.field(name).type
        expected = _PHYSICAL_BY_NAME[name].arrow_type
        if observed != expected:
            raise _PartitionReadError(
                f"partition {path_label} admitted Arrow column {name!r} has type "
                f"{observed}; expected exact {expected}"
            )


def _decode_partition(
    source: BinaryIO,
    *,
    path_label: str,
    value_columns: tuple[str, ...],
    admitted_first_key: int,
    admitted_last_key: int,
) -> _DecodedPartition:
    """Decode, Arrow-restrict, validate, then materialize one admitted slice."""
    projected = (_PHYSICAL_KEY_COLUMN, *value_columns)
    if _DISPLAY_ONLY_COLUMN in projected:
        raise _PartitionReadError("display-only timestamp must never be projected")
    try:
        source.seek(0)
        parquet = _pq.ParquetFile(source)
        _validate_physical_schema(parquet.schema_arrow)
        table = parquet.read(columns=list(projected), use_threads=False)
    except _PartitionReadError:
        raise
    except (_pa.ArrowException, OSError, ValueError) as exc:
        raise _PartitionReadError(
            f"cannot decode Parquet partition {path_label}: {exc}"
        ) from exc

    admitted = _restrict_to_admission(
        table,
        admitted_first_key=admitted_first_key,
        admitted_last_key=admitted_last_key,
        path_label=path_label,
    )
    _validate_admitted_arrow_columns(
        admitted,
        projected=projected,
        path_label=path_label,
    )

    expected = _PHYSICAL_BY_NAME
    keys = _array_to_numpy(
        admitted.column(_PHYSICAL_KEY_COLUMN),
        _np.dtype("int64"),
        column_name=_PHYSICAL_KEY_COLUMN,
        path_label=path_label,
    )
    columns: dict[str, _Array] = {}
    for name in value_columns:
        dtype = expected[name].numpy_dtype
        if dtype is None:  # defensive: display-only and key never reach this branch
            raise _PartitionReadError(f"column {name!r} has no public NumPy dtype")
        columns[name] = _array_to_numpy(
            admitted.column(name),
            dtype,
            column_name=name,
            path_label=path_label,
        )
    return _DecodedPartition(
        keys=keys,
        columns=_MappingProxyType(columns),
    )
