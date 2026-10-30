"""Deterministic in-memory container for one dense feature artifact.

This module owns serialization validation only.  It performs no filesystem
access and exports no public symbol; the artifact store supplies durable I/O.
"""

from __future__ import annotations

import hashlib as _hashlib
import io as _io
import json as _json
import re as _re
import typing as _typing
import zipfile as _zipfile
from dataclasses import dataclass as _dataclass
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.artifacts.identity import StageIdentity as _StageIdentity
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import GapPolicy as _GapPolicy
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import WindowSpec as _WindowSpec
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.time import ClockDomain as _ClockDomain
from trading_research.core.time import TimeUnit as _TimeUnit

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

__all__: list[str] = []

_FORMAT = "trading-research-feature-frame"
_FORMAT_VERSION = 1
_META_MEMBER = "__meta__"
_KEYS_MEMBER = "__keys__"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SHA256_HEX = _re.compile(r"^[0-9a-f]{64}$")
_FLOAT_HEX = _re.compile(r"^-?(?:0x[0-9a-f]+\.[0-9a-f]+p[+-][0-9]+|inf)$")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_TOP_LEVEL_FIELDS = frozenset(
    {
        "columns",
        "format",
        "format_version",
        "identity_key",
        "row_count",
        "stage_identity",
    }
)
_COLUMN_FIELDS = frozenset({"dtype", "member", "name"})
_STAGE_FIELDS = frozenset({"bounds", "platform", "providers", "source_identities", "split_policy"})
_BOUNDS_FIELDS = frozenset(
    {"clock_domain", "eval_end", "eval_start", "resolved_context_start", "time_unit"}
)
_SOURCE_FIELDS = frozenset({"dataset_id", "manifest_sha256"})
_SPLIT_FIELDS = frozenset({"adr", "policy_sha256", "policy_version"})
_PLATFORM_FIELDS = frozenset({"diff_sha256", "git_commit", "git_dirty"})
_PROVIDER_FIELDS = frozenset(
    {"feature_names", "params", "provider_id", "required_columns", "version", "window"}
)
_WINDOW_FIELDS = frozenset({"gap_policy", "max_prior_rows", "nominal_interval"})


class _ArtifactContainerError(_ContractViolationError):
    """An artifact payload or its binding is malformed or inconsistent."""


@_dataclass(frozen=True, slots=True)
class _ImmutableArray:
    """Detached array bytes with a fresh read-only descriptor on demand."""

    _buffer: bytes
    _dtype: _np.dtype[_np.generic]
    _length: int

    @classmethod
    def _from_array(cls, values: _npt.NDArray[_typing.Any]) -> _ImmutableArray:
        array = _np.ascontiguousarray(values)
        return cls(array.tobytes(order="C"), array.dtype, int(array.size))

    def _descriptor(self) -> _npt.NDArray[_typing.Any]:
        result = _np.frombuffer(self._buffer, dtype=self._dtype, count=self._length)
        result.setflags(write=False)
        return result


@_dataclass(frozen=True, slots=True)
class _DecodedFeatureArtifact:
    """Fully validated, detached feature artifact awaiting Slice-2F restoration."""

    _identity_key: str
    _stage_document: _Mapping[str, object]
    _keys: _ImmutableArray
    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _column_names: tuple[str, ...]
    _columns: _Mapping[str, _ImmutableArray]
    _origins: _Mapping[str, _FeatureOrigin]


def _encode_feature_artifact(identity: _StageIdentity, frame: _FeatureFrame) -> bytes:
    """Encode one exact identity/frame pair as canonical uncompressed NPZ bytes."""
    if type(identity) is not _StageIdentity:
        raise _ArtifactContainerError("identity must be an exact StageIdentity")
    if type(frame) is not _FeatureFrame:
        raise _ArtifactContainerError("frame must be an exact FeatureFrame")

    stage = _parse_stage_document(identity._canonical_document_json())
    identity_key = identity.identity_key()
    if _stage_key(stage) != identity_key:
        raise _ArtifactContainerError("StageIdentity canonical document does not match its key")
    binding = _stage_binding(stage)
    _validate_frame_binding(frame=frame, binding=binding)

    column_documents = [
        {"name": name, "member": _column_member(index), "dtype": "float64"}
        for index, name in enumerate(frame.column_names)
    ]
    metadata = {
        "format": _FORMAT,
        "format_version": _FORMAT_VERSION,
        "identity_key": identity_key,
        "stage_identity": stage,
        "row_count": frame.num_rows,
        "columns": column_documents,
    }
    metadata_bytes = _canonical_json(metadata).encode("utf-8")
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        _META_MEMBER: _np.frombuffer(metadata_bytes, dtype=_np.uint8),
        _KEYS_MEMBER: frame.keys,
    }
    for column in column_documents:
        name = column["name"]
        member = column["member"]
        arrays[member] = frame.column(name)

    output = _io.BytesIO()
    try:
        _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **arrays)
    except Exception as exc:
        raise _ArtifactContainerError("cannot encode deterministic feature artifact") from exc
    payload = output.getvalue()
    _validate_zip_envelope(payload, tuple(arrays))
    return payload


def _decode_feature_artifact(
    payload: bytes,
    *,
    expected_identity_key: str,
) -> _DecodedFeatureArtifact:
    """Eagerly validate and detach one complete feature-artifact payload."""
    if type(payload) is not bytes:
        raise _ArtifactContainerError("artifact payload must be exact bytes")
    identity_key = _validated_identity_key(expected_identity_key)
    metadata, expected_members = _read_canonical_metadata(payload)
    _validate_zip_envelope(payload, expected_members)

    embedded_key = metadata["identity_key"]
    if type(embedded_key) is not str or embedded_key != identity_key:
        raise _ArtifactContainerError("artifact embedded identity key does not match request")
    stage = _parse_stage_value(metadata["stage_identity"])
    if _stage_key(stage) != identity_key:
        raise _ArtifactContainerError("artifact stage document does not hash to its identity key")
    binding = _stage_binding(stage)

    row_count = metadata["row_count"]
    if type(row_count) is not int or row_count <= 0:
        raise _ArtifactContainerError("artifact row_count must be a positive Python int")
    columns_meta = _validated_column_documents(metadata["columns"])
    expected_names = tuple(binding._origins)
    if tuple(column["name"] for column in columns_meta) != expected_names:
        raise _ArtifactContainerError(
            "artifact column order does not match the embedded provider plan"
        )

    loaded = _load_arrays(payload, expected_members)
    keys = _validated_keys(loaded[_KEYS_MEMBER], row_count=row_count)
    if int(keys[0]) < binding._eval_start or int(keys[-1]) > binding._eval_end:
        raise _ArtifactContainerError("artifact keys lie outside stage evaluation bounds")
    columns: dict[str, _ImmutableArray] = {}
    for column in columns_meta:
        name = _typing.cast("str", column["name"])
        member = _typing.cast("str", column["member"])
        values = _validated_column(loaded[member], name=name, row_count=row_count)
        columns[name] = _ImmutableArray._from_array(values)

    return _DecodedFeatureArtifact(
        _identity_key=identity_key,
        _stage_document=_typing.cast("_Mapping[str, object]", _freeze_json(stage)),
        _keys=_ImmutableArray._from_array(keys),
        _unit=binding._unit,
        _clock_domain=binding._clock_domain,
        _source_identities=binding._source_identities,
        _column_names=expected_names,
        _columns=_MappingProxyType(columns),
        _origins=binding._origins,
    )


@_dataclass(frozen=True, slots=True)
class _StageBinding:
    """Typed semantic facts reconstructed from one canonical stage document."""

    _resolved_context_start: int
    _eval_start: int
    _eval_end: int
    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _origins: _Mapping[str, _FeatureOrigin]


def _parse_stage_document(canonical: object) -> dict[str, object]:
    if type(canonical) is not str:
        raise _ArtifactContainerError("stage identity document must be exact text")
    try:
        parsed = _json.loads(canonical, object_pairs_hook=_reject_duplicate_pairs)
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("stage identity document is not strict JSON") from exc
    if _canonical_json(parsed) != canonical:
        raise _ArtifactContainerError("stage identity document is not canonically encoded")
    return _parse_stage_value(parsed)


def _parse_stage_value(value: object) -> dict[str, object]:
    stage = _exact_object(value, fields=_STAGE_FIELDS, label="stage identity")
    # Full typed reconstruction is the validation; returning this detached JSON
    # object preserves the exact preimage used for the identity hash.
    _stage_binding(stage)
    return stage


def _stage_key(stage: _Mapping[str, object]) -> str:
    digest = _hashlib.sha256(_canonical_json(stage).encode("utf-8"))
    return digest.hexdigest()


def _stage_binding(stage: _Mapping[str, object]) -> _StageBinding:
    sources_value = stage["source_identities"]
    if type(sources_value) is not list or not sources_value:
        raise _ArtifactContainerError("stage source_identities must be a non-empty array")
    sources: list[_DatasetIdentity] = []
    for index, value in enumerate(sources_value):
        source = _exact_object(value, fields=_SOURCE_FIELDS, label=f"stage source {index}")
        manifest_sha256 = _exact_string(source["manifest_sha256"], label="manifest_sha256")
        if _SHA256_HEX.fullmatch(manifest_sha256) is None:
            raise _ArtifactContainerError(
                f"stage source {index} manifest_sha256 is not lowercase SHA-256"
            )
        try:
            sources.append(
                _DatasetIdentity(
                    _exact_string(source["dataset_id"], label="dataset_id"),
                    manifest_sha256,
                )
            )
        except _ContractViolationError as exc:
            raise _ArtifactContainerError(f"invalid stage source {index}") from exc

    bounds = _exact_object(stage["bounds"], fields=_BOUNDS_FIELDS, label="stage bounds")
    resolved = _exact_int64(bounds["resolved_context_start"], label="resolved_context_start")
    eval_start = _exact_int64(bounds["eval_start"], label="eval_start")
    eval_end = _exact_int64(bounds["eval_end"], label="eval_end")
    if resolved > eval_start or eval_start > eval_end:
        raise _ArtifactContainerError("stage bounds are not ordered")
    try:
        unit = _TimeUnit(_exact_string(bounds["time_unit"], label="time_unit"))
        domain = _ClockDomain(_exact_string(bounds["clock_domain"], label="clock_domain"))
    except (ValueError, _ContractViolationError) as exc:
        raise _ArtifactContainerError("stage native axis is invalid") from exc

    split = _exact_object(stage["split_policy"], fields=_SPLIT_FIELDS, label="stage split policy")
    for field in ("adr", "policy_version"):
        text = _exact_string(split[field], label=f"split {field}")
        if not text or text != text.strip():
            raise _ArtifactContainerError(f"split {field} is not canonical text")
    digest = _exact_string(split["policy_sha256"], label="split policy_sha256")
    if _SHA256_HEX.fullmatch(digest) is None:
        raise _ArtifactContainerError("split policy_sha256 is not lowercase SHA-256")

    platform = _exact_object(stage["platform"], fields=_PLATFORM_FIELDS, label="stage platform")
    commit = _exact_string(platform["git_commit"], label="platform git_commit")
    if _re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit) is None:
        raise _ArtifactContainerError("platform git_commit is not canonical hexadecimal")
    dirty = platform["git_dirty"]
    if type(dirty) is not bool:
        raise _ArtifactContainerError("platform git_dirty must be an exact bool")
    diff = platform["diff_sha256"]
    if dirty:
        if type(diff) is not str or _SHA256_HEX.fullmatch(diff) is None:
            raise _ArtifactContainerError("dirty platform has no canonical diff SHA-256")
    elif diff is not None:
        raise _ArtifactContainerError("clean platform must not carry a diff SHA-256")

    providers_value = stage["providers"]
    if type(providers_value) is not list or not providers_value:
        raise _ArtifactContainerError("stage providers must be a non-empty array")
    origins: dict[str, _FeatureOrigin] = {}
    for index, value in enumerate(providers_value):
        provider = _exact_object(value, fields=_PROVIDER_FIELDS, label=f"stage provider {index}")
        provider_id = _exact_string(provider["provider_id"], label="provider_id")
        if not provider_id:
            raise _ArtifactContainerError("provider_id must not be empty")
        version = _exact_int(provider["version"], label="provider version")
        params = _decode_params(provider["params"])
        window_value = _exact_object(
            provider["window"], fields=_WINDOW_FIELDS, label="provider window"
        )
        try:
            window = _WindowSpec(
                max_prior_rows=_exact_int(
                    window_value["max_prior_rows"], label="window max_prior_rows"
                ),
                gap_policy=_GapPolicy(
                    _exact_string(window_value["gap_policy"], label="window gap_policy")
                ),
                nominal_interval=_optional_exact_int(
                    window_value["nominal_interval"], label="window nominal_interval"
                ),
            )
            origin = _FeatureOrigin(provider_id, version, params, window)
        except (ValueError, _ContractViolationError) as exc:
            raise _ArtifactContainerError(f"invalid stage provider {index}") from exc
        names = _exact_name_list(
            provider["feature_names"], label="provider feature_names", allow_empty=False
        )
        required_columns = _exact_name_list(
            provider["required_columns"], label="provider required_columns", allow_empty=True
        )
        for name in (*names, *required_columns):
            if name == "target" or name.startswith("target."):
                raise _ArtifactContainerError(
                    f"stage provider declaration contains reserved name {name!r}"
                )
        for name in names:
            if name in origins:
                raise _ArtifactContainerError(f"duplicate feature name {name!r} in stage plan")
            origins[name] = origin

    return _StageBinding(
        resolved,
        eval_start,
        eval_end,
        unit,
        domain,
        tuple(sources),
        _MappingProxyType(origins),
    )


def _validate_frame_binding(*, frame: _FeatureFrame, binding: _StageBinding) -> None:
    if frame.unit is not binding._unit or frame.clock_domain != binding._clock_domain:
        raise _ArtifactContainerError("feature frame native axis differs from stage identity")
    if frame.source_identities != binding._source_identities:
        raise _ArtifactContainerError("feature frame sources differ from stage identity")
    if frame.column_names != tuple(binding._origins):
        raise _ArtifactContainerError("feature frame columns differ from stage provider plan")
    keys = frame.keys
    _validated_keys(keys, row_count=frame.num_rows)
    if int(keys[0]) < binding._eval_start or int(keys[-1]) > binding._eval_end:
        raise _ArtifactContainerError("feature frame keys lie outside stage evaluation bounds")
    if tuple(frame.schema) != frame.column_names or any(
        dtype != "float64" for dtype in frame.schema.values()
    ):
        raise _ArtifactContainerError("feature frame schema is not ordered exact float64")
    for name, expected in binding._origins.items():
        if not _origins_match(frame.origin(name), expected):
            raise _ArtifactContainerError(
                f"feature frame origin for {name!r} differs from stage identity"
            )
        _validated_column(frame.column(name), name=name, row_count=frame.num_rows)


def _origins_match(actual: _FeatureOrigin, expected: _FeatureOrigin) -> bool:
    """Compare identity fields without Python's bool/int or signed-zero equality."""
    return (
        type(actual) is _FeatureOrigin
        and actual.provider_id == expected.provider_id
        and actual.version == expected.version
        and actual.window == expected.window
        and _canonical_params_json(actual.params) == _canonical_params_json(expected.params)
    )


def _read_canonical_metadata(payload: bytes) -> tuple[dict[str, object], tuple[str, ...]]:
    try:
        with _np.load(_io.BytesIO(payload), allow_pickle=False) as archive:
            files = tuple(archive.files)
            if not files or files[0] != _META_MEMBER:
                raise _ArtifactContainerError("artifact metadata must be the first NPZ member")
            raw_meta = archive[_META_MEMBER]
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("artifact is not a readable pickle-free NPZ") from exc
    if (
        type(raw_meta) is not _np.ndarray
        or raw_meta.ndim != 1
        or raw_meta.dtype != _np.dtype(_np.uint8)
        or raw_meta.dtype.metadata is not None
    ):
        raise _ArtifactContainerError(
            "artifact metadata member must be exact one-dimensional uint8"
        )
    try:
        encoded = raw_meta.tobytes(order="C").decode("utf-8")
        parsed = _json.loads(encoded, object_pairs_hook=_reject_duplicate_pairs)
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("artifact metadata is not strict UTF-8 JSON") from exc
    if _canonical_json(parsed) != encoded:
        raise _ArtifactContainerError("artifact metadata is not canonically encoded")
    metadata = _exact_object(parsed, fields=_TOP_LEVEL_FIELDS, label="artifact metadata")
    if metadata["format"] != _FORMAT or type(metadata["format"]) is not str:
        raise _ArtifactContainerError("unsupported artifact format")
    if type(metadata["format_version"]) is not int or metadata["format_version"] != 1:
        raise _ArtifactContainerError("unsupported artifact format version")
    columns = _validated_column_documents(metadata["columns"])
    expected = (_META_MEMBER, _KEYS_MEMBER, *(column["member"] for column in columns))
    return metadata, _typing.cast("tuple[str, ...]", expected)


def _validated_column_documents(value: object) -> tuple[dict[str, object], ...]:
    if type(value) is not list or not value:
        raise _ArtifactContainerError("artifact columns must be a non-empty array")
    result: list[dict[str, object]] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        column = _exact_object(item, fields=_COLUMN_FIELDS, label=f"artifact column {index}")
        name = _exact_string(column["name"], label="artifact column name")
        if not name or name in names:
            raise _ArtifactContainerError("artifact column names must be non-empty and unique")
        names.add(name)
        if column["member"] != _column_member(index) or type(column["member"]) is not str:
            raise _ArtifactContainerError("artifact column member names are not canonical ordinals")
        if column["dtype"] != "float64" or type(column["dtype"]) is not str:
            raise _ArtifactContainerError("artifact column dtype must be exactly float64")
        result.append(column)
    return tuple(result)


def _load_arrays(
    payload: bytes,
    members: tuple[str, ...],
) -> dict[str, _npt.NDArray[_typing.Any]]:
    loaded: dict[str, _npt.NDArray[_typing.Any]] = {}
    try:
        with _np.load(_io.BytesIO(payload), allow_pickle=False) as archive:
            if tuple(archive.files) != members:
                raise _ArtifactContainerError("artifact NPZ member order differs from metadata")
            for member in members[1:]:
                array = archive[member]
                if type(array) is not _np.ndarray:
                    raise _ArtifactContainerError(f"artifact member {member!r} is not an array")
                loaded[member] = array
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("artifact arrays cannot be decoded without pickle") from exc
    return loaded


def _validate_zip_envelope(
    payload: bytes,
    members: tuple[str, ...],
    *,
    compressed: bool = False,
    max_uncompressed_bytes: int | None = None,
) -> None:
    expected_names = tuple(f"{member}.npy" for member in members)
    expected_compression = _zipfile.ZIP_DEFLATED if compressed else _zipfile.ZIP_STORED
    if max_uncompressed_bytes is not None and (
        type(max_uncompressed_bytes) is not int or max_uncompressed_bytes <= 0
    ):
        raise _ArtifactContainerError("artifact expanded-size limit must be positive")
    try:
        with _zipfile.ZipFile(_io.BytesIO(payload), mode="r") as archive:
            infos = tuple(archive.infolist())
            if archive.comment:
                raise _ArtifactContainerError("artifact ZIP comment must be empty")
            if tuple(info.filename for info in infos) != expected_names:
                raise _ArtifactContainerError(
                    "artifact ZIP members are missing, extra, or reordered"
                )
            if len({info.filename for info in infos}) != len(infos):
                raise _ArtifactContainerError("artifact ZIP contains duplicate members")
            for info in infos:
                if info.is_dir() or info.compress_type != expected_compression:
                    raise _ArtifactContainerError(
                        "artifact ZIP member compression differs from its contract"
                    )
                if info.date_time != _ZIP_TIMESTAMP:
                    raise _ArtifactContainerError("artifact ZIP member timestamp is not canonical")
                if info.extra or info.comment:
                    raise _ArtifactContainerError(
                        "artifact ZIP members must not carry extra metadata"
                    )
            if max_uncompressed_bytes is not None and sum(
                info.file_size for info in infos
            ) > max_uncompressed_bytes:
                raise _ArtifactContainerError("artifact ZIP expanded size exceeds its limit")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise _ArtifactContainerError(
                    f"artifact ZIP member {bad_member!r} fails its CRC"
                )
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("artifact is not a valid deterministic ZIP") from exc


def _validated_keys(
    value: _npt.NDArray[_typing.Any],
    *,
    row_count: int,
) -> _npt.NDArray[_np.int64]:
    if (
        value.ndim != 1
        or value.dtype != _np.dtype(_np.int64)
        or value.dtype.metadata is not None
        or value.shape[0] != row_count
        or row_count <= 0
    ):
        raise _ArtifactContainerError("artifact keys must be exact one-dimensional int64")
    if value.size > 1 and _np.any(value[1:] <= value[:-1]):
        raise _ArtifactContainerError("artifact keys must be strictly increasing and unique")
    return _typing.cast("_npt.NDArray[_np.int64]", value)


def _validated_column(
    value: _npt.NDArray[_typing.Any],
    *,
    name: str,
    row_count: int,
) -> _npt.NDArray[_np.float64]:
    if (
        value.ndim != 1
        or value.dtype != _np.dtype(_np.float64)
        or value.dtype.metadata is not None
        or value.shape[0] != row_count
    ):
        raise _ArtifactContainerError(
            f"artifact feature {name!r} must be exact one-dimensional float64"
        )
    if _np.isinf(value).any():
        raise _ArtifactContainerError(f"artifact feature {name!r} contains infinity")
    return _typing.cast("_npt.NDArray[_np.float64]", value)


def _decode_params(value: object) -> _Mapping[str, _ParamValue]:
    if type(value) is not dict:
        raise _ArtifactContainerError("provider params must be an object")
    params: dict[str, _ParamValue] = {}
    for name, item in value.items():
        if type(name) is not str or not name:
            raise _ArtifactContainerError("provider parameter names must be non-empty strings")
        params[name] = _decode_param(item, path=f"params[{name!r}]")
    try:
        encoded = _canonical_params_json(params)
    except _ContractViolationError as exc:
        raise _ArtifactContainerError("provider params are not canonical") from exc
    if _json.loads(encoded) != value:
        raise _ArtifactContainerError("provider params do not use canonical type encoding")
    return _MappingProxyType({name: params[name] for name in sorted(params)})


def _decode_param(value: object, *, path: str) -> _ParamValue:
    if value is None or type(value) in {str, bool, int}:
        return _typing.cast("_ParamValue", value)
    if type(value) is list:
        return tuple(
            _decode_param(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    if type(value) is dict and set(value) == {"__float_hex__"}:
        encoded = value["__float_hex__"]
        if type(encoded) is not str or _FLOAT_HEX.fullmatch(encoded) is None:
            raise _ArtifactContainerError(f"{path} has a malformed canonical float")
        try:
            result = float.fromhex(encoded)
        except (OverflowError, ValueError) as exc:
            raise _ArtifactContainerError(f"{path} has a malformed canonical float") from exc
        if result.hex() != encoded or _np.isnan(result):
            raise _ArtifactContainerError(f"{path} has a noncanonical float spelling")
        return result
    raise _ArtifactContainerError(f"{path} has an unsupported canonical parameter value")


def _exact_object(value: object, *, fields: frozenset[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _ArtifactContainerError(f"{label} fields are missing, extra, or malformed")
    return _typing.cast("dict[str, object]", value)


def _exact_name_list(value: object, *, label: str, allow_empty: bool) -> tuple[str, ...]:
    if type(value) is not list:
        raise _ArtifactContainerError(f"{label} must be an array")
    names = tuple(value)
    if not names and not allow_empty:
        raise _ArtifactContainerError(f"{label} must not be empty")
    if any(type(name) is not str or not name for name in names):
        raise _ArtifactContainerError(f"{label} must contain non-empty strings")
    if len(set(names)) != len(names):
        raise _ArtifactContainerError(f"{label} contains duplicates")
    return _typing.cast("tuple[str, ...]", names)


def _exact_string(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise _ArtifactContainerError(f"{label} must be an exact string")
    return value


def _exact_int(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise _ArtifactContainerError(f"{label} must be an exact Python int")
    return value


def _exact_int64(value: object, *, label: str) -> int:
    result = _exact_int(value, label=label)
    if not _INT64_MIN <= result <= _INT64_MAX:
        raise _ArtifactContainerError(f"{label} must fit in a signed int64")
    return result


def _optional_exact_int(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    return _exact_int(value, label=label)


def _validated_identity_key(value: object) -> str:
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _ArtifactContainerError("expected identity key must be 64 lowercase hex characters")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _ArtifactContainerError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _canonical_json(value: object) -> str:
    try:
        return _json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception as exc:
        raise _ArtifactContainerError("artifact JSON value is not canonicalizable") from exc


def _freeze_json(value: object) -> object:
    if type(value) is dict:
        mapping = _typing.cast("dict[str, object]", value)
        return _MappingProxyType({key: _freeze_json(item) for key, item in mapping.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in _typing.cast("list[object]", value))
    return value


def _column_member(index: int) -> str:
    return f"__column_{index}"
