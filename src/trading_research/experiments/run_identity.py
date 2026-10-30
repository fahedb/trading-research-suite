"""Pure experiment fingerprinting and ULID execution identity."""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import math as _math
import os as _os
import re as _re
import time as _time
import typing as _typing
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field

from trading_research.artifacts.container import _decode_params
from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.artifacts.identity import (
    _canonical_identity_json,
    _identity_key_from_canonical,
)
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.research import StageOrigin as _StageOrigin

__all__: list[str] = []

_FINGERPRINT_ALGORITHM = "experiment-fingerprint-v1"
_IDENTITY_SCHEMA = "research-experiment-identity-v1"
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = _re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_COMPONENT_KINDS = frozenset(
    {
        "decision_policy",
        "corpus_research_model",
        "evaluator",
        "execution_spec",
        "feature_provider",
        "outcome_spec",
        "representation_model",
        "research_model",
    }
)


@_dataclass(frozen=True, slots=True)
class _ComponentSource:
    _component_kind: str
    _origin: _StageOrigin
    _source_sha256: str

    def __init__(
        self,
        *,
        component_kind: str,
        component_id: str,
        version: int,
        params: _Mapping[str, _ParamValue],
        source_sha256: str,
    ) -> None:
        if type(component_kind) is not str or component_kind not in _COMPONENT_KINDS:
            raise _ContractViolationError(
                f"component_kind must be one of {sorted(_COMPONENT_KINDS)!r}"
            )
        origin = _StageOrigin(component_id, version, params)
        digest = _validated_sha256(source_sha256, label="component source")
        object.__setattr__(self, "_component_kind", component_kind)
        object.__setattr__(self, "_origin", origin)
        object.__setattr__(self, "_source_sha256", digest)


@_dataclass(frozen=True, slots=True)
class _IdentitySummary:
    _experiment_fingerprint: str
    _platform_identity: _PlatformIdentity
    _dependency_lock_sha256: str
    _registration_canonical: bool
    _declared_lockbox_evaluation: bool
    _resolved_config_json: str


@_dataclass(frozen=True, slots=True, init=False)
class _ExperimentIdentity:
    _experiment_fingerprint: str
    _identity_json: str = _field(repr=False, compare=False)
    _resolved_config_json: str = _field(repr=False, compare=False)
    _summary: _IdentitySummary

    def __init__(
        self,
        *,
        resolved_config: _Mapping[str, object],
        dataset_identities: _Sequence[_DatasetIdentity],
        components: _Sequence[_ComponentSource],
        platform_identity: _PlatformIdentity,
        dependency_lock_sha256: str,
        seed: int | None,
        machine_identity: _Mapping[str, object],
        git_diff: str | None,
        lockfile_in_sync: bool,
        full_integrity_verified: bool,
        declared_lockbox_evaluation: bool,
    ) -> None:
        config_document = _detached_json_mapping(
            resolved_config,
            label="resolved_config",
            allow_empty=False,
        )
        sources = _validated_sources(dataset_identities)
        component_entries = _validated_components(components)
        if type(platform_identity) is not _PlatformIdentity:
            raise _ContractViolationError("platform_identity must be an exact PlatformIdentity")
        dependency_digest = _validated_sha256(
            dependency_lock_sha256,
            label="dependency lock",
        )
        if seed is not None and type(seed) is not int:
            raise _ContractViolationError("seed must be an exact int or None")
        machine_document = _detached_json_mapping(
            machine_identity,
            label="machine_identity",
            allow_empty=False,
        )
        for value, label in (
            (lockfile_in_sync, "lockfile_in_sync"),
            (full_integrity_verified, "full_integrity_verified"),
            (declared_lockbox_evaluation, "declared_lockbox_evaluation"),
        ):
            if type(value) is not bool:
                raise _ContractViolationError(f"{label} must be an exact bool")
        _validate_git_diff(platform_identity, git_diff)

        fingerprint_inputs = {
            "algorithm": _FINGERPRINT_ALGORITHM,
            "resolved_config": config_document,
            "dataset_identities": [
                {
                    "dataset_id": identity.dataset_id,
                    "manifest_sha256": identity.manifest_sha256,
                }
                for identity in sources
            ],
            "components": [_component_document(component) for component in component_entries],
            "platform": _platform_document(platform_identity),
            "dependency_lock_sha256": dependency_digest,
            "seed": seed,
            "declared_lockbox_evaluation": declared_lockbox_evaluation,
        }
        fingerprint = _identity_key_from_canonical(_canonical_identity_json(fingerprint_inputs))
        registration_canonical = (
            not platform_identity.git_dirty and lockfile_in_sync and full_integrity_verified
        )
        document = {
            "schema": _IDENTITY_SCHEMA,
            "experiment_fingerprint": fingerprint,
            "fingerprint_inputs": fingerprint_inputs,
            "machine_identity": machine_document,
            "git_diff": git_diff,
            "canonical_evidence": {
                "lockfile_in_sync": lockfile_in_sync,
                "full_integrity_verified": full_integrity_verified,
                "registration_canonical": registration_canonical,
            },
        }
        identity_json = _canonical_identity_json(document)
        summary = _IdentitySummary(
            fingerprint,
            platform_identity,
            dependency_digest,
            registration_canonical,
            declared_lockbox_evaluation,
            _canonical_identity_json(config_document),
        )
        object.__setattr__(self, "_experiment_fingerprint", fingerprint)
        object.__setattr__(self, "_identity_json", identity_json)
        object.__setattr__(
            self,
            "_resolved_config_json",
            _canonical_identity_json(config_document),
        )
        object.__setattr__(self, "_summary", summary)

    def _identity_document_json(self) -> str:
        return self._identity_json

    def _resolved_config_document_json(self) -> str:
        return self._resolved_config_json


def _component_document(component: _ComponentSource) -> dict[str, object]:
    return {
        "component_kind": component._component_kind,
        "component_id": component._origin.plugin_id,
        "version": component._origin.version,
        "params": _json.loads(_canonical_params_json(component._origin.params)),
        "source_sha256": component._source_sha256,
    }


def _platform_document(platform: _PlatformIdentity) -> dict[str, object]:
    return {
        "git_commit": platform.git_commit,
        "git_dirty": platform.git_dirty,
        "diff_sha256": platform.diff_sha256,
    }


def _validated_sources(value: object) -> tuple[_DatasetIdentity, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError("dataset_identities must be an ordered sequence")
    sources = tuple(value)
    if not sources or any(type(item) is not _DatasetIdentity for item in sources):
        raise _ContractViolationError(
            "dataset_identities must contain exact DatasetIdentity values"
        )
    if len(set(sources)) != len(sources):
        raise _ContractViolationError("dataset_identities must not contain duplicates")
    return _typing.cast("tuple[_DatasetIdentity, ...]", sources)


def _validated_components(value: object) -> tuple[_ComponentSource, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError("components must be an ordered sequence")
    components = tuple(value)
    if not components or any(type(item) is not _ComponentSource for item in components):
        raise _ContractViolationError("components must contain exact ComponentSource values")
    documents = tuple(
        _canonical_identity_json(_component_document(component))
        for component in _typing.cast("tuple[_ComponentSource, ...]", components)
    )
    if len(set(documents)) != len(documents):
        raise _ContractViolationError("components must not contain exact duplicates")
    return _typing.cast("tuple[_ComponentSource, ...]", components)


def _detached_json_mapping(
    value: object,
    *,
    label: str,
    allow_empty: bool,
) -> dict[str, object]:
    if not isinstance(value, _Mapping):
        raise _ContractViolationError(f"{label} must be a mapping")
    if not value and not allow_empty:
        raise _ContractViolationError(f"{label} must not be empty")
    detached = _detached_json_value(value, label=label)
    if type(detached) is not dict:
        raise AssertionError("mapping detachment returned a non-mapping")
    return _typing.cast("dict[str, object]", detached)


def _detached_json_value(value: object, *, label: str) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not _math.isfinite(value):
            raise _ContractViolationError(f"{label} contains a non-finite float")
        return value
    if isinstance(value, _Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise _ContractViolationError(f"{label} mapping keys must be non-empty strings")
            result[key] = _detached_json_value(item, label=f"{label}.{key}")
        return result
    if isinstance(value, _Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _detached_json_value(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    raise _ContractViolationError(f"{label} contains non-JSON value {type(value).__name__}")


def _validate_git_diff(
    platform_identity: _PlatformIdentity,
    git_diff: object,
) -> None:
    if platform_identity.git_dirty:
        if type(git_diff) is not str or not git_diff:
            raise _ContractViolationError("a dirty platform requires captured git_diff text")
        observed = _hashlib.sha256(git_diff.encode("utf-8")).hexdigest()
        if observed != platform_identity.diff_sha256:
            raise _ContractViolationError(
                "captured git_diff does not match PlatformIdentity.diff_sha256"
            )
    elif git_diff is not None:
        raise _ContractViolationError("a clean platform requires git_diff to be None")


def _validated_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _ContractViolationError(f"{label} SHA-256 must be 64 lowercase hex characters")
    return value


def _identity_summary_from_document(value: object) -> _IdentitySummary:
    if type(value) is not dict:
        raise _ContractViolationError("identity document must be an exact JSON object")
    document = _typing.cast("dict[str, object]", value)
    if set(document) != {
        "canonical_evidence",
        "experiment_fingerprint",
        "fingerprint_inputs",
        "git_diff",
        "machine_identity",
        "schema",
    }:
        raise _ContractViolationError("identity document fields differ from V1 schema")
    if document["schema"] != _IDENTITY_SCHEMA:
        raise _ContractViolationError("identity document schema is unsupported")
    inputs = document["fingerprint_inputs"]
    if type(inputs) is not dict or set(inputs) != {
        "algorithm",
        "components",
        "dataset_identities",
        "declared_lockbox_evaluation",
        "dependency_lock_sha256",
        "platform",
        "resolved_config",
        "seed",
    }:
        raise _ContractViolationError("fingerprint input fields differ from V1 schema")
    fingerprint_inputs = _typing.cast("dict[str, object]", inputs)
    if fingerprint_inputs["algorithm"] != _FINGERPRINT_ALGORITHM:
        raise _ContractViolationError("fingerprint algorithm is unsupported")
    fingerprint = _validated_sha256(
        document["experiment_fingerprint"],
        label="experiment fingerprint",
    )
    expected = _identity_key_from_canonical(_canonical_identity_json(inputs))
    if fingerprint != expected:
        raise _ContractViolationError("experiment fingerprint does not match its inputs")
    dependency_digest = _validated_sha256(
        fingerprint_inputs["dependency_lock_sha256"],
        label="dependency lock",
    )
    resolved_config = _detached_json_mapping(
        fingerprint_inputs["resolved_config"],
        label="resolved_config",
        allow_empty=False,
    )
    _validate_identity_sources(fingerprint_inputs["dataset_identities"])
    _validate_identity_components(fingerprint_inputs["components"])
    seed = fingerprint_inputs["seed"]
    if seed is not None and type(seed) is not int:
        raise _ContractViolationError("fingerprint seed must be an exact int or None")
    platform = _platform_from_document(fingerprint_inputs["platform"])
    declared_lockbox = fingerprint_inputs["declared_lockbox_evaluation"]
    if type(declared_lockbox) is not bool:
        raise _ContractViolationError("declared_lockbox_evaluation must be an exact bool")
    evidence_value = document["canonical_evidence"]
    if type(evidence_value) is not dict or set(evidence_value) != {
        "full_integrity_verified",
        "lockfile_in_sync",
        "registration_canonical",
    }:
        raise _ContractViolationError("canonical evidence fields differ from V1 schema")
    evidence = _typing.cast("dict[str, object]", evidence_value)
    if any(type(evidence[name]) is not bool for name in evidence):
        raise _ContractViolationError("canonical evidence values must be exact bools")
    expected_canonical = (
        not platform.git_dirty
        and evidence["lockfile_in_sync"] is True
        and evidence["full_integrity_verified"] is True
    )
    if evidence["registration_canonical"] is not expected_canonical:
        raise _ContractViolationError("registration canonical flag is inconsistent")
    _detached_json_mapping(
        document["machine_identity"],
        label="machine_identity",
        allow_empty=False,
    )
    _validate_git_diff(platform, document["git_diff"])
    return _IdentitySummary(
        fingerprint,
        platform,
        dependency_digest,
        expected_canonical,
        declared_lockbox,
        _canonical_identity_json(resolved_config),
    )


def _validate_identity_sources(value: object) -> None:
    if type(value) is not list or not value:
        raise _ContractViolationError("fingerprint dataset identities must be a non-empty list")
    identities: list[_DatasetIdentity] = []
    for item in value:
        if type(item) is not dict or set(item) != {"dataset_id", "manifest_sha256"}:
            raise _ContractViolationError("fingerprint dataset identity is malformed")
        document = _typing.cast("dict[str, object]", item)
        identities.append(
            _DatasetIdentity(
                _typing.cast("str", document["dataset_id"]),
                _typing.cast("str", document["manifest_sha256"]),
            )
        )
    if len(set(identities)) != len(identities):
        raise _ContractViolationError("fingerprint dataset identities contain duplicates")


def _validate_identity_components(value: object) -> None:
    if type(value) is not list or not value:
        raise _ContractViolationError("fingerprint components must be a non-empty list")
    canonical: list[str] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "component_id",
            "component_kind",
            "params",
            "source_sha256",
            "version",
        }:
            raise _ContractViolationError("fingerprint component is malformed")
        document = _typing.cast("dict[str, object]", item)
        kind = document["component_kind"]
        if type(kind) is not str or kind not in _COMPONENT_KINDS:
            raise _ContractViolationError("fingerprint component kind is unsupported")
        try:
            params = _decode_params(document["params"])
            _StageOrigin(
                _typing.cast("str", document["component_id"]),
                _typing.cast("int", document["version"]),
                params,
            )
        except Exception as exc:
            raise _ContractViolationError("fingerprint component origin is malformed") from exc
        _validated_sha256(document["source_sha256"], label="component source")
        canonical.append(_canonical_identity_json(item))
    if len(set(canonical)) != len(canonical):
        raise _ContractViolationError("fingerprint components contain duplicates")


def _platform_from_document(value: object) -> _PlatformIdentity:
    if type(value) is not dict or set(value) != {
        "diff_sha256",
        "git_commit",
        "git_dirty",
    }:
        raise _ContractViolationError("platform identity fields differ from V1 schema")
    platform = _typing.cast("dict[str, object]", value)
    return _PlatformIdentity(
        git_commit=_typing.cast("str", platform["git_commit"]),
        git_dirty=_typing.cast("bool", platform["git_dirty"]),
        diff_sha256=_typing.cast("str | None", platform["diff_sha256"]),
    )


def _run_id_from_parts(*, timestamp_ms: int, randomness: bytes) -> str:
    if type(timestamp_ms) is not int or not 0 <= timestamp_ms < 2**48:
        raise _ContractViolationError("ULID timestamp must be an exact 48-bit integer")
    if type(randomness) is not bytes or len(randomness) != 10:
        raise _ContractViolationError("ULID randomness must be exactly 10 bytes")
    value = (timestamp_ms << 80) | int.from_bytes(randomness, "big")
    encoded = ["0"] * 26
    for index in range(25, -1, -1):
        encoded[index] = _CROCKFORD32[value & 31]
        value >>= 5
    run_id = "".join(encoded)
    return _validated_run_id(run_id)


def _mint_run_id() -> str:
    timestamp_ms = _time.time_ns() // 1_000_000
    return _run_id_from_parts(timestamp_ms=timestamp_ms, randomness=_os.urandom(10))


def _validated_run_id(value: object) -> str:
    if type(value) is not str or _RUN_ID.fullmatch(value) is None:
        raise _ContractViolationError("run_id must be a canonical 26-character uppercase ULID")
    return value
