"""Pure, pre-execution identity for one bounded feature stage.

The caller supplies every semantic input explicitly. This module performs no
data access, filesystem inspection, environment lookup, Git invocation, or
artifact I/O.
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import re as _re
import typing as _typing
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field

from trading_research.core.errors import ClockDomainError as _ClockDomainError
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.errors import FeatureContractError as _FeatureContractError
from trading_research.core.errors import TimeUnitMismatchError as _TimeUnitMismatchError
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import WindowSpec as _WindowSpec
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.time import NativeTimeKey as _NativeTimeKey

if _typing.TYPE_CHECKING:
    from trading_research.core.interfaces import FeatureProvider as _FeatureProvider

__all__ = ["PlatformIdentity", "StageIdentity"]

_GIT_COMMIT = _re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SHA256_HEX = _re.compile(r"^[0-9a-fA-F]{64}$")


@_dataclass(frozen=True, slots=True)
class PlatformIdentity:
    """Caller-supplied conservative identity of the complete platform code."""

    git_commit: str
    git_dirty: bool
    diff_sha256: str | None

    def __post_init__(self) -> None:
        """Validate and normalize one complete Git identity declaration."""
        if type(self.git_commit) is not str or _GIT_COMMIT.fullmatch(self.git_commit) is None:
            raise _ContractViolationError(
                "platform git_commit must be exactly 40 or 64 hexadecimal characters"
            )
        if type(self.git_dirty) is not bool:
            raise _ContractViolationError("platform git_dirty must be an exact bool")
        if self.git_dirty:
            if type(self.diff_sha256) is not str or _SHA256_HEX.fullmatch(self.diff_sha256) is None:
                raise _ContractViolationError(
                    "a dirty platform requires diff_sha256 as exactly 64 hexadecimal characters"
                )
            object.__setattr__(self, "diff_sha256", self.diff_sha256.lower())
        elif self.diff_sha256 is not None:
            raise _ContractViolationError("a clean platform requires diff_sha256 to be None")
        object.__setattr__(self, "git_commit", self.git_commit.lower())


@_dataclass(frozen=True, slots=True)
class _ProviderEntry:
    """Detached snapshot of every identity-bearing provider declaration."""

    _origin: _FeatureOrigin
    _feature_names: tuple[str, ...]
    _required_columns: tuple[str, ...]


@_dataclass(frozen=True, slots=True, init=False)
class StageIdentity:
    """Opaque deterministic key for one bounded, ordered feature computation."""

    _canonical_json: str = _field(repr=False, compare=False)
    _identity_key: str

    def __init__(
        self,
        *,
        source_identities: _Sequence[_DatasetIdentity],
        resolved_context_start: _NativeTimeKey,
        eval_start: _NativeTimeKey,
        eval_end: _NativeTimeKey,
        split_policy_adr: str,
        split_policy_version: str,
        split_policy_sha256: str,
        providers: _Sequence[_FeatureProvider],
        platform_identity: PlatformIdentity,
    ) -> None:
        """Snapshot exact semantic inputs and compute their canonical SHA-256 key."""
        sources = _validated_sources(source_identities)
        bounds = _validated_bounds(
            resolved_context_start=resolved_context_start,
            eval_start=eval_start,
            eval_end=eval_end,
        )
        split_policy = _validated_split_policy(
            adr=split_policy_adr,
            policy_version=split_policy_version,
            policy_sha256=split_policy_sha256,
        )
        entries = _validated_providers(providers)
        if type(platform_identity) is not PlatformIdentity:
            raise _ContractViolationError("platform_identity must be an exact PlatformIdentity")

        document = {
            "source_identities": [
                {
                    "dataset_id": identity.dataset_id,
                    "manifest_sha256": identity.manifest_sha256,
                }
                for identity in sources
            ],
            "bounds": bounds,
            "split_policy": split_policy,
            "providers": [_provider_document(entry) for entry in entries],
            "platform": {
                "git_commit": platform_identity.git_commit,
                "git_dirty": platform_identity.git_dirty,
                "diff_sha256": platform_identity.diff_sha256,
            },
        }
        canonical = _json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "_canonical_json", canonical)
        object.__setattr__(
            self,
            "_identity_key",
            _hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def identity_key(self) -> str:
        """Return the deterministic 64-character lower-case cache key."""
        return self._identity_key

    def _canonical_document_json(self) -> str:
        """Return the detached canonical identity document for artifact provenance."""
        return self._canonical_json


def _canonical_identity_json(document: object) -> str:
    """Return the one canonical JSON encoding shared by artifact identities."""
    try:
        return _json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception as exc:
        raise _ContractViolationError("identity document is not canonicalizable") from exc


def _identity_key_from_canonical(canonical: str) -> str:
    """Hash one canonical identity document with the platform SHA-256 rule."""
    if type(canonical) is not str:
        raise _ContractViolationError("canonical identity document must be exact text")
    return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_sources(value: object) -> tuple[_DatasetIdentity, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError("source_identities must be an ordered sequence")
    sources = tuple(value)
    if not sources:
        raise _ContractViolationError("at least one source identity is required")
    for index, identity in enumerate(sources):
        if type(identity) is not _DatasetIdentity:
            raise _ContractViolationError(
                f"source identity {index} must be an exact DatasetIdentity"
            )
    return _typing.cast("tuple[_DatasetIdentity, ...]", sources)


def _validated_bounds(
    *,
    resolved_context_start: object,
    eval_start: object,
    eval_end: object,
) -> dict[str, str | int]:
    values = (
        ("resolved_context_start", resolved_context_start),
        ("eval_start", eval_start),
        ("eval_end", eval_end),
    )
    for label, bound in values:
        if type(bound) is not _NativeTimeKey:
            raise _ContractViolationError(f"{label} must be an exact NativeTimeKey")
    resolved = _typing.cast("_NativeTimeKey", resolved_context_start)
    start = _typing.cast("_NativeTimeKey", eval_start)
    end = _typing.cast("_NativeTimeKey", eval_end)
    for label, bound in (("eval_start", start), ("eval_end", end)):
        if bound.unit is not resolved.unit:
            raise _TimeUnitMismatchError(
                f"{label} uses {bound.unit.value}, expected {resolved.unit.value}"
            )
        if bound.clock_domain != resolved.clock_domain:
            raise _ClockDomainError(
                f"{label} uses clock domain {bound.clock_domain.identifier!r}, expected "
                f"{resolved.clock_domain.identifier!r}"
            )
    if resolved.value > start.value or start.value > end.value:
        raise _ContractViolationError(
            "stage bounds require resolved_context_start <= eval_start <= eval_end"
        )
    return {
        "resolved_context_start": resolved.value,
        "eval_start": start.value,
        "eval_end": end.value,
        "time_unit": resolved.unit.value,
        "clock_domain": resolved.clock_domain.identifier,
    }


def _validated_split_policy(
    *,
    adr: object,
    policy_version: object,
    policy_sha256: object,
) -> dict[str, str]:
    for label, value in (("adr", adr), ("policy_version", policy_version)):
        if type(value) is not str or not value or value != value.strip():
            raise _ContractViolationError(
                f"split policy {label} must be a non-empty trimmed string"
            )
    if type(policy_sha256) is not str or _SHA256_HEX.fullmatch(policy_sha256) is None:
        raise _ContractViolationError(
            "split policy policy_sha256 must be exactly 64 hexadecimal characters"
        )
    return {
        "adr": _typing.cast("str", adr),
        "policy_version": _typing.cast("str", policy_version),
        "policy_sha256": policy_sha256.lower(),
    }


def _validated_names(
    value: object,
    *,
    field: str,
    provider_id: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _FeatureContractError(f"provider {provider_id!r} {field} must be an exact tuple")
    names = _typing.cast("tuple[object, ...]", value)
    if not names and not allow_empty:
        raise _FeatureContractError(
            f"provider {provider_id!r} must declare at least one feature name"
        )
    if any(type(name) is not str or not name for name in names):
        raise _FeatureContractError(
            f"provider {provider_id!r} {field} must contain only non-empty strings"
        )
    if len(set(names)) != len(names):
        raise _FeatureContractError(f"provider {provider_id!r} {field} contains duplicate names")
    return _typing.cast("tuple[str, ...]", names)


def _validated_providers(value: object) -> tuple[_ProviderEntry, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _FeatureContractError("providers must be an ordered sequence")
    providers = tuple(value)
    if not providers:
        raise _FeatureContractError("at least one feature provider is required")
    entries: list[_ProviderEntry] = []
    for index, provider in enumerate(providers):
        provider_id = _read_provider_attribute(provider, "provider_id", index=index)
        if type(provider_id) is not str or not provider_id:
            raise _FeatureContractError(
                f"provider at index {index} provider_id must be a non-empty string"
            )
        version = _read_provider_attribute(provider, "version", index=index)
        if type(version) is not int:
            raise _FeatureContractError(f"provider {provider_id!r} version must be a Python int")
        params = _read_provider_attribute(provider, "params", index=index)
        if not isinstance(params, _Mapping):
            raise _FeatureContractError(f"provider {provider_id!r} params must be a mapping")
        window = _read_provider_attribute(provider, "window", index=index)
        if type(window) is not _WindowSpec:
            raise _FeatureContractError(
                f"provider {provider_id!r} window must be an exact WindowSpec"
            )
        origin = _FeatureOrigin(
            provider_id=provider_id,
            version=version,
            params=_typing.cast("_Mapping[str, _ParamValue]", params),
            window=window,
        )
        feature_names = _validated_names(
            _read_provider_attribute(provider, "feature_names", index=index),
            field="feature_names",
            provider_id=provider_id,
            allow_empty=False,
        )
        required_columns = _validated_names(
            _read_provider_attribute(provider, "required_columns", index=index),
            field="required_columns",
            provider_id=provider_id,
            allow_empty=True,
        )
        entries.append(_ProviderEntry(origin, feature_names, required_columns))
    return tuple(entries)


def _read_provider_attribute(provider: object, name: str, *, index: int) -> object:
    try:
        return getattr(provider, name)
    except Exception as exc:
        raise _FeatureContractError(
            f"provider at index {index} declaration {name!r} could not be read"
        ) from exc


def _provider_document(entry: _ProviderEntry) -> dict[str, object]:
    origin = entry._origin
    return {
        "provider_id": origin.provider_id,
        "version": origin.version,
        "params": _json.loads(_canonical_params_json(origin.params)),
        "feature_names": list(entry._feature_names),
        "required_columns": list(entry._required_columns),
        "window": {
            "max_prior_rows": origin.window.max_prior_rows,
            "gap_policy": origin.window.gap_policy.value,
            "nominal_interval": origin.window.nominal_interval,
        },
    }
