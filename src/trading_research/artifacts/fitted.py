"""Pure identity, pickle-free serialization, and materialization for fitted state.

Plugins never import this module. They return :class:`FittedPayload`; the
experiment composition root supplies every identity-bearing fact and calls the
private binding path here.
"""

from __future__ import annotations

import hashlib as _hashlib
import io as _io
import json as _json
import re as _re
import typing as _typing
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field

import numpy as _np

from trading_research.artifacts.container import (
    _ArtifactContainerError,
    _canonical_json,
    _decode_params,
    _load_arrays,
    _reject_duplicate_pairs,
    _validate_zip_envelope,
)
from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.artifacts.identity import (
    _canonical_identity_json,
    _identity_key_from_canonical,
)
from trading_research.artifacts.materialization import MaterializationPolicy as _Policy
from trading_research.artifacts.materialization import _requires_persistence
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame
from trading_research.core.research_frames import _make_fitted_state

if _typing.TYPE_CHECKING:
    from collections.abc import Callable as _Callable

    import numpy.typing as _npt

    from trading_research.artifacts.store import ArtifactStore as _ArtifactStore

__all__: list[str] = []

_FORMAT = "trading-research-fitted-state"
_FORMAT_VERSION = 1
_META_MEMBER = "__meta__"
_SHA256_HEX = _re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "arrays",
        "fitted_identity",
        "format",
        "format_version",
        "identity_key",
        "payload_metadata",
    }
)
_ARRAY_FIELDS = frozenset({"dtype", "member", "name", "shape"})
_IDENTITY_FIELDS = frozenset(
    {
        "dependencies",
        "features",
        "fit_isolation",
        "format",
        "format_version",
        "platform",
        "plugin",
        "representation",
        "split_policy",
        "tolerance",
        "training_outcome",
        "training_view",
    }
)
_CORPUS_IDENTITY_FIELDS = frozenset(
    {
        "corpus",
        "dependencies",
        "format",
        "format_version",
        "inference_feature_names",
        "platform",
        "plugin",
        "tolerance",
        "training_spec",
    }
)


@_dataclass(frozen=True, slots=True, init=False)
class _FittedStateIdentity:
    """Complete pre-fit identity assembled only by the composition root."""

    _canonical_json: str = _field(repr=False, compare=False)
    _identity_key: str

    def __init__(
        self,
        *,
        plugin_kind: str,
        plugin_id: str,
        plugin_version: int,
        plugin_params: _Mapping[str, _ParamValue],
        feature_origins: _Sequence[tuple[str, _FeatureOrigin]],
        representation_origins: _Sequence[tuple[str, _StageOrigin]],
        representation_state_identity_key: str | None,
        training_view: _ViewSpan,
        split_policy_adr: str,
        split_policy_version: str,
        split_policy_sha256: str,
        training_targets: _TrainingTargetFrame | None,
        fit_isolation: _Mapping[str, _ParamValue],
        platform_identity: _PlatformIdentity,
        dependency_lock_sha256: str,
        comparison_tolerance: _ToleranceSpec | None,
    ) -> None:
        plugin = _plugin_document(
            kind=plugin_kind,
            plugin_id=plugin_id,
            version=plugin_version,
            params=plugin_params,
        )
        features = _feature_documents(feature_origins)
        representation = _representation_document(
            origins=representation_origins,
            state_identity_key=representation_state_identity_key,
        )
        view = _view_document(training_view)
        split = _split_document(
            adr=split_policy_adr,
            version=split_policy_version,
            sha256=split_policy_sha256,
        )
        outcome = _outcome_document(training_targets)
        isolation = _params_document(fit_isolation, label="fit_isolation", require_nonempty=True)
        platform = _platform_document(platform_identity)
        dependency = _sha256(dependency_lock_sha256, label="dependency_lock_sha256")
        tolerance = _tolerance_document(comparison_tolerance)
        document = {
            "format": "trading-research-fitted-state-identity",
            "format_version": 1,
            "plugin": plugin,
            "features": features,
            "representation": representation,
            "training_view": view,
            "split_policy": split,
            "training_outcome": outcome,
            "fit_isolation": isolation,
            "platform": platform,
            "dependencies": {"lock_sha256": dependency},
            "tolerance": tolerance,
        }
        canonical = _canonical_identity_json(document)
        object.__setattr__(self, "_canonical_json", canonical)
        object.__setattr__(self, "_identity_key", _identity_key_from_canonical(canonical))

    def identity_key(self) -> str:
        """Return the complete deterministic fitted-state identity key."""
        return self._identity_key

    def _canonical_document_json(self) -> str:
        """Return the exact detached identity preimage for artifact binding."""
        return self._canonical_json


@_dataclass(frozen=True, slots=True, init=False)
class _CorpusFittedStateIdentity:
    """Complete identity for one state learned from an immutable corpus bundle."""

    _canonical_json: str = _field(repr=False, compare=False)
    _identity_key: str

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_version: int,
        plugin_params: _Mapping[str, _ParamValue],
        inference_feature_names: _Sequence[str],
        corpus_identity_key: str,
        corpus_artifact_sha256: str,
        source_manifest_sha256: str,
        target_spec_id: str,
        target_spec_version: int,
        target_spec_params: _Mapping[str, _ParamValue],
        training_spec: _Mapping[str, _ParamValue],
        platform_identity: _PlatformIdentity,
        dependency_lock_sha256: str,
        comparison_tolerance: _ToleranceSpec | None,
    ) -> None:
        names = _name_sequence(inference_feature_names, label="inference_feature_names")
        if type(target_spec_id) is not str or not target_spec_id:
            raise _ContractViolationError("target_spec_id must be a non-empty string")
        if type(target_spec_version) is not int or target_spec_version <= 0:
            raise _ContractViolationError("target_spec_version must be a positive int")
        document = {
            "format": "trading-research-corpus-fitted-state-identity",
            "format_version": 1,
            "plugin": _plugin_document(
                kind="model",
                plugin_id=plugin_id,
                version=plugin_version,
                params=plugin_params,
            ),
            "inference_feature_names": list(names),
            "corpus": {
                "identity_key": _sha256(corpus_identity_key, label="corpus_identity_key"),
                "artifact_sha256": _sha256(
                    corpus_artifact_sha256,
                    label="corpus_artifact_sha256",
                ),
                "source_manifest_sha256": _sha256(
                    source_manifest_sha256,
                    label="source_manifest_sha256",
                ),
                "target_spec": {
                    "plugin_id": target_spec_id,
                    "version": target_spec_version,
                    "params": _params_document(
                        target_spec_params,
                        label="target_spec_params",
                        require_nonempty=True,
                    ),
                },
            },
            "training_spec": _params_document(
                training_spec,
                label="training_spec",
                require_nonempty=True,
            ),
            "platform": _platform_document(platform_identity),
            "dependencies": {
                "lock_sha256": _sha256(
                    dependency_lock_sha256,
                    label="dependency_lock_sha256",
                )
            },
            "tolerance": _tolerance_document(comparison_tolerance),
        }
        canonical = _canonical_identity_json(document)
        object.__setattr__(self, "_canonical_json", canonical)
        object.__setattr__(self, "_identity_key", _identity_key_from_canonical(canonical))

    def identity_key(self) -> str:
        """Return the complete deterministic corpus-state identity key."""
        return self._identity_key

    def _canonical_document_json(self) -> str:
        """Return the exact detached identity preimage for artifact binding."""
        return self._canonical_json


def _name_sequence(value: object, *, label: str) -> tuple[str, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError(f"{label} must be an ordered sequence")
    names = tuple(value)
    if (
        not names
        or any(type(name) is not str or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise _ContractViolationError(f"{label} must contain unique non-empty strings")
    return _typing.cast("tuple[str, ...]", names)


def _plugin_document(
    *, kind: object, plugin_id: object, version: object, params: object
) -> dict[str, object]:
    if kind not in {"model", "representation"} or type(kind) is not str:
        raise _ContractViolationError("fitted-state plugin_kind must be model or representation")
    if type(plugin_id) is not str or not plugin_id:
        raise _ContractViolationError("fitted-state plugin_id must be a non-empty string")
    if type(version) is not int or version <= 0:
        raise _ContractViolationError("fitted-state plugin_version must be a positive int")
    return {
        "kind": kind,
        "plugin_id": plugin_id,
        "version": version,
        "params": _params_document(params, label="plugin_params", require_nonempty=False),
    }


def _feature_documents(value: object) -> list[dict[str, object]]:
    entries = _ordered_pairs(value, value_type=_FeatureOrigin, label="feature_origins")
    if not entries:
        raise _ContractViolationError("fitted state must consume at least one feature")
    return [
        {
            "name": name,
            "origin": {
                "provider_id": origin.provider_id,
                "version": origin.version,
                "params": _params_document(
                    origin.params, label="feature origin params", require_nonempty=False
                ),
                "window": {
                    "max_prior_rows": origin.window.max_prior_rows,
                    "gap_policy": origin.window.gap_policy.value,
                    "nominal_interval": origin.window.nominal_interval,
                },
            },
        }
        for name, origin in _typing.cast("tuple[tuple[str, _FeatureOrigin], ...]", entries)
    ]


def _representation_document(
    *, origins: object, state_identity_key: object
) -> dict[str, object] | None:
    entries = _ordered_pairs(origins, value_type=_StageOrigin, label="representation_origins")
    if state_identity_key is None:
        if entries:
            raise _ContractViolationError(
                "representation origins require a fitted representation-state identity"
            )
        return None
    key = _sha256(state_identity_key, label="representation_state_identity_key")
    if not entries:
        raise _ContractViolationError(
            "representation-state identity requires consumed representation origins"
        )
    return {
        "fitted_state_identity_key": key,
        "columns": [
            {"name": name, "origin": _stage_origin_document(origin)}
            for name, origin in _typing.cast(
                "tuple[tuple[str, _StageOrigin], ...]", entries
            )
        ],
    }


def _ordered_pairs(
    value: object, *, value_type: type[object], label: str
) -> tuple[tuple[str, object], ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError(f"{label} must be an ordered sequence")
    entries = tuple(value)
    names: set[str] = set()
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise _ContractViolationError(f"{label} entries must be exact name/value tuples")
        name, origin = entry
        if type(name) is not str or not name or name in names:
            raise _ContractViolationError(f"{label} names must be unique non-empty strings")
        if type(origin) is not value_type:
            raise _ContractViolationError(f"{label} contains an invalid origin")
        names.add(name)
    return _typing.cast("tuple[tuple[str, object], ...]", entries)


def _stage_origin_document(origin: _StageOrigin) -> dict[str, object]:
    return {
        "plugin_id": origin.plugin_id,
        "version": origin.version,
        "params": _params_document(
            origin.params, label="stage origin params", require_nonempty=False
        ),
    }


def _view_document(view: object) -> dict[str, object]:
    if type(view) is not _ViewSpan:
        raise _ContractViolationError("training_view must be an exact ViewSpan")
    actual = view
    return {
        "source_identities": [
            {
                "dataset_id": identity.dataset_id,
                "manifest_sha256": identity.manifest_sha256,
            }
            for identity in actual.source_identities
        ],
        "context_start": actual.context_start.value,
        "eval_start": actual.eval_start.value,
        "eval_end": actual.eval_end.value,
        "time_unit": actual.unit.value,
        "clock_domain": actual.clock_domain.identifier,
    }


def _split_document(*, adr: object, version: object, sha256: object) -> dict[str, object]:
    for label, value in (("adr", adr), ("policy_version", version)):
        if type(value) is not str or not value or value != value.strip():
            raise _ContractViolationError(
                f"split policy {label} must be a non-empty trimmed string"
            )
    return {
        "adr": adr,
        "policy_version": version,
        "policy_sha256": _sha256(sha256, label="split_policy_sha256"),
    }


def _outcome_document(targets: object) -> dict[str, object] | None:
    if targets is None:
        return None
    if type(targets) is not _TrainingTargetFrame:
        raise _ContractViolationError(
            "training_targets must be an exact TrainingTargetFrame or None"
        )
    actual = targets
    names = actual.column_names
    first_origin = actual.origin(names[0])
    first_document = _stage_origin_document(first_origin)
    columns: list[dict[str, object]] = []
    for name in names:
        origin = actual.origin(name)
        if _stage_origin_document(origin) != first_document:
            raise _ContractViolationError(
                "one TrainingTargetFrame must come from one OutcomeSpec declaration"
            )
        columns.append({"name": name, "origin": _stage_origin_document(origin)})
    keys = actual.keys
    return {
        "columns": columns,
        "result_range": {
            "first_key": int(keys[0]),
            "last_key": int(keys[-1]),
            "row_count": actual.num_rows,
            "keys_sha256": _hashlib.sha256(keys.tobytes(order="C")).hexdigest(),
        },
        "alignment": _params_document(
            first_origin.params, label="outcome alignment", require_nonempty=False
        ),
    }


def _platform_document(value: object) -> dict[str, object]:
    if type(value) is not _PlatformIdentity:
        raise _ContractViolationError("platform_identity must be an exact PlatformIdentity")
    platform = value
    return {
        "git_commit": platform.git_commit,
        "git_dirty": platform.git_dirty,
        "diff_sha256": platform.diff_sha256,
    }


def _tolerance_document(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not _ToleranceSpec:
        raise _ContractViolationError("comparison_tolerance must be ToleranceSpec or None")
    tolerance = value
    return {
        "atol_hex": tolerance.atol.hex(),
        "rtol_hex": tolerance.rtol.hex(),
        "reason": tolerance.reason,
    }


def _params_document(value: object, *, label: str, require_nonempty: bool) -> object:
    if not isinstance(value, _Mapping):
        raise _ContractViolationError(f"{label} must be a mapping")
    params = _typing.cast("_Mapping[str, _ParamValue]", value)
    if require_nonempty and not params:
        raise _ContractViolationError(f"{label} must not be empty")
    try:
        return _json.loads(_canonical_params_json(params))
    except _ContractViolationError:
        raise
    except Exception as exc:  # pragma: no cover - canonical encoder always emits JSON
        raise _ContractViolationError(f"{label} is not canonical") from exc


def _sha256(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _ContractViolationError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _bind_fitted_state(
    identity: _FittedStateIdentity | _CorpusFittedStateIdentity,
    payload: _FittedPayload,
) -> _FittedState:
    if type(identity) not in {_FittedStateIdentity, _CorpusFittedStateIdentity}:
        raise _ContractViolationError("identity must be an exact fitted-state identity")
    if type(payload) is not _FittedPayload:
        raise _ContractViolationError("fit must return an exact FittedPayload")
    return _make_fitted_state(identity_key=identity.identity_key(), payload=payload)


def _encode_fitted_state(
    identity: _FittedStateIdentity | _CorpusFittedStateIdentity,
    state: _FittedState,
) -> bytes:
    """Encode one exact identity/state pair as canonical pickle-free NPZ bytes."""
    if type(identity) not in {_FittedStateIdentity, _CorpusFittedStateIdentity}:
        raise _ArtifactContainerError("identity must be an exact fitted-state identity")
    if type(state) is not _FittedState or state.identity_key() != identity.identity_key():
        raise _ArtifactContainerError("FittedState does not match the supplied identity")
    identity_document = _json.loads(identity._canonical_document_json())
    arrays_document: list[dict[str, object]] = []
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {}
    for index, name in enumerate(state.payload.array_names):
        member = _array_member(index)
        values = state.payload.array(name)
        arrays_document.append(
            {
                "name": name,
                "member": member,
                "dtype": values.dtype.str,
                "shape": list(values.shape),
            }
        )
        arrays[member] = values
    metadata = {
        "format": _FORMAT,
        "format_version": _FORMAT_VERSION,
        "identity_key": identity.identity_key(),
        "fitted_identity": identity_document,
        "payload_metadata": _json.loads(_canonical_params_json(state.payload.metadata)),
        "arrays": arrays_document,
    }
    metadata_bytes = _canonical_json(metadata).encode("utf-8")
    members: dict[str, _npt.NDArray[_typing.Any]] = {
        _META_MEMBER: _np.frombuffer(metadata_bytes, dtype=_np.uint8),
        **arrays,
    }
    output = _io.BytesIO()
    try:
        _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **members)
    except Exception as exc:
        raise _ArtifactContainerError("cannot encode deterministic fitted state") from exc
    result = output.getvalue()
    _validate_zip_envelope(result, tuple(members))
    return result


def _decode_fitted_state(payload: bytes, *, expected_identity_key: str) -> _FittedState:
    """Eagerly validate, detach, and bind one fitted-state artifact."""
    key = _sha256(expected_identity_key, label="expected_identity_key")
    metadata, array_documents, members = _read_metadata(payload)
    _validate_zip_envelope(payload, members)
    if metadata["identity_key"] != key or type(metadata["identity_key"]) is not str:
        raise _ArtifactContainerError("fitted-state embedded identity key does not match request")
    raw_identity = metadata["fitted_identity"]
    if type(raw_identity) is not dict:
        raise _ArtifactContainerError("fitted identity must be an object")
    identity_format = raw_identity.get("format")
    fields = (
        _IDENTITY_FIELDS
        if identity_format == "trading-research-fitted-state-identity"
        else _CORPUS_IDENTITY_FIELDS
        if identity_format == "trading-research-corpus-fitted-state-identity"
        else frozenset()
    )
    if not fields:
        raise _ArtifactContainerError("unsupported fitted identity format")
    identity_document = _exact_object(
        raw_identity,
        fields=fields,
        label="fitted identity",
    )
    identity_canonical = _canonical_identity_json(identity_document)
    if _identity_key_from_canonical(identity_canonical) != key:
        raise _ArtifactContainerError("fitted identity document does not hash to its key")
    payload_metadata = _decode_params(metadata["payload_metadata"])
    loaded = _load_arrays(payload, members)
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {}
    for document in array_documents:
        name = _typing.cast("str", document["name"])
        member = _typing.cast("str", document["member"])
        values = loaded[member]
        if values.dtype.str != document["dtype"]:
            raise _ArtifactContainerError(f"fitted array {name!r} dtype differs from metadata")
        shape = tuple(_typing.cast("list[int]", document["shape"]))
        if values.shape != shape:
            raise _ArtifactContainerError(f"fitted array {name!r} shape differs from metadata")
        if (
            values.dtype.hasobject
            or values.dtype.metadata is not None
            or values.dtype.kind not in "biuf"
        ):
            raise _ArtifactContainerError(
                f"fitted array {name!r} must have a metadata-free real numeric dtype"
            )
        arrays[name] = values
    fitted_payload = _FittedPayload(arrays=arrays, metadata=payload_metadata)
    return _make_fitted_state(identity_key=key, payload=fitted_payload)


def _read_metadata(
    payload: object,
) -> tuple[dict[str, object], tuple[dict[str, object], ...], tuple[str, ...]]:
    if type(payload) is not bytes:
        raise _ArtifactContainerError("fitted-state payload must be exact bytes")
    try:
        with _np.load(_io.BytesIO(payload), allow_pickle=False) as archive:
            if not archive.files or archive.files[0] != _META_MEMBER:
                raise _ArtifactContainerError(
                    "fitted-state metadata must be the first NPZ member"
                )
            raw = archive[_META_MEMBER]
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("fitted state is not a readable pickle-free NPZ") from exc
    if (
        type(raw) is not _np.ndarray
        or raw.ndim != 1
        or raw.dtype != _np.dtype(_np.uint8)
        or raw.dtype.metadata is not None
    ):
        raise _ArtifactContainerError("fitted-state metadata must be one-dimensional uint8")
    try:
        encoded = raw.tobytes(order="C").decode("utf-8")
        parsed = _json.loads(encoded, object_pairs_hook=_reject_duplicate_pairs)
    except _ArtifactContainerError:
        raise
    except Exception as exc:
        raise _ArtifactContainerError("fitted-state metadata is not strict UTF-8 JSON") from exc
    if _canonical_json(parsed) != encoded:
        raise _ArtifactContainerError("fitted-state metadata is not canonically encoded")
    metadata = _exact_object(parsed, fields=_TOP_LEVEL_FIELDS, label="fitted-state metadata")
    if metadata["format"] != _FORMAT or type(metadata["format"]) is not str:
        raise _ArtifactContainerError("unsupported fitted-state format")
    if type(metadata["format_version"]) is not int or metadata["format_version"] != 1:
        raise _ArtifactContainerError("unsupported fitted-state format version")
    documents = _array_documents(metadata["arrays"])
    members = (_META_MEMBER, *(document["member"] for document in documents))
    return metadata, documents, _typing.cast("tuple[str, ...]", members)


def _array_documents(value: object) -> tuple[dict[str, object], ...]:
    if type(value) is not list:
        raise _ArtifactContainerError("fitted-state arrays metadata must be an array")
    result: list[dict[str, object]] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        document = _exact_object(item, fields=_ARRAY_FIELDS, label=f"fitted array {index}")
        name = document["name"]
        if type(name) is not str or not name or name in names:
            raise _ArtifactContainerError("fitted-state array names must be unique strings")
        names.add(name)
        if document["member"] != _array_member(index) or type(document["member"]) is not str:
            raise _ArtifactContainerError("fitted-state member names are not canonical ordinals")
        dtype = document["dtype"]
        if type(dtype) is not str:
            raise _ArtifactContainerError("fitted-state array dtype must be text")
        try:
            parsed_dtype = _np.dtype(dtype)
        except TypeError as exc:
            raise _ArtifactContainerError("fitted-state array dtype is invalid") from exc
        if parsed_dtype.str != dtype or parsed_dtype.hasobject or parsed_dtype.kind not in "biuf":
            raise _ArtifactContainerError("fitted-state array dtype is not canonical numeric")
        shape = document["shape"]
        if type(shape) is not list or any(type(size) is not int or size < 0 for size in shape):
            raise _ArtifactContainerError("fitted-state array shape is invalid")
        result.append(document)
    return tuple(result)


def _exact_object(value: object, *, fields: frozenset[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _ArtifactContainerError(f"{label} fields are missing, extra, or malformed")
    return _typing.cast("dict[str, object]", value)


def _array_member(index: int) -> str:
    return f"__array_{index}"


def _materialize_fitted_state(
    *,
    identity: _FittedStateIdentity | _CorpusFittedStateIdentity,
    cost_hint: _CostHint,
    store: _ArtifactStore,
    policy: _Policy,
    validate_after_lookup: _Callable[[], None],
    fit: _Callable[[], _FittedPayload],
) -> _FittedState:
    """Return a verified fitted-state hit or fit and optionally publish once."""
    if type(identity) not in {_FittedStateIdentity, _CorpusFittedStateIdentity}:
        raise _ContractViolationError("identity must be an exact fitted-state identity")
    if type(cost_hint) is not _CostHint:
        raise _ContractViolationError("cost_hint must be an exact CostHint")
    if type(policy) is not _Policy:
        raise _ContractViolationError("policy must be an exact MaterializationPolicy")
    persist = _requires_persistence(policy=policy, cost_hints=(cost_hint,))
    if not persist:
        return _bind_fitted_state(identity, fit())

    identity_key = identity.identity_key()
    cached = store._get_namespaced(
        identity_key,
        namespace="fitted-states",
        validate=_decode_fitted_state,
    )
    if cached is not None:
        return _decode_fitted_state(cached, expected_identity_key=identity_key)
    validate_after_lookup()
    state = _bind_fitted_state(identity, fit())
    encoded = _encode_fitted_state(identity, state)
    store._put_namespaced(
        identity_key,
        encoded,
        namespace="fitted-states",
        validate=_decode_fitted_state,
    )
    return state
