"""Clock-free materialization policy and verified feature-cache composition."""

from __future__ import annotations

import typing as _typing
from enum import StrEnum as _StrEnum

import numpy as _np

from trading_research.artifacts.container import (
    _decode_feature_artifact,
    _encode_feature_artifact,
)
from trading_research.artifacts.identity import StageIdentity as _StageIdentity
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.feature_frames import _restore_feature_frame
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import canonical_params_json as _canonical_params_json

if _typing.TYPE_CHECKING:
    from collections.abc import Callable as _Callable
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.artifacts.store import ArtifactStore as _ArtifactStore
    from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
    from trading_research.core.time import ClockDomain as _ClockDomain
    from trading_research.core.time import TimeUnit as _TimeUnit

__all__ = ["MaterializationPolicy"]


class MaterializationPolicy(_StrEnum):
    """Deterministic persistence choice for one feature-stage output."""

    EPHEMERAL = "ephemeral"
    CACHE_IF_EXPENSIVE = "cache_if_expensive"
    PERSIST = "persist"
    PERSIST_ON_FAILURE = "persist_on_failure"


class _FeatureMaterializationError(_TradingResearchError):
    """A verified artifact cannot represent the requested live feature stage."""


class _FeatureComputationError(_TradingResearchError):
    """Scalar-only failure provenance for V1 ``persist_on_failure``."""

    __slots__ = ("identity_key", "provider_id")

    def __init__(self, *, identity_key: str, provider_id: str) -> None:
        self.identity_key = identity_key
        self.provider_id = provider_id
        super().__init__(f"feature stage {identity_key} failed in provider {provider_id!r}")


def _raise_feature_computation_failure(
    *,
    identity_key: str,
    provider_id: str,
    cause: Exception,
) -> _typing.Never:
    """Raise the private scalar-only failure record while preserving its cause."""
    raise _FeatureComputationError(
        identity_key=identity_key,
        provider_id=provider_id,
    ) from cause


def _materialize_feature_frame(
    *,
    identity: _StageIdentity,
    policy: MaterializationPolicy,
    cost_hints: tuple[_CostHint, ...],
    store: _ArtifactStore,
    expected_eval_keys: _npt.NDArray[_np.int64],
    expected_unit: _TimeUnit,
    expected_clock_domain: _ClockDomain,
    expected_source_identities: tuple[_DatasetIdentity, ...],
    expected_column_names: tuple[str, ...],
    expected_origins: _Mapping[str, _FeatureOrigin],
    validate_after_lookup: _Callable[[], None],
    compute: _Callable[[], _FeatureFrame],
) -> _FeatureFrame:
    """Return a verified hit or compute and optionally publish one complete frame."""
    if type(identity) is not _StageIdentity:
        raise _ContractViolationError("identity must be an exact StageIdentity")
    if type(policy) is not MaterializationPolicy:
        raise _ContractViolationError(
            f"policy must be an exact MaterializationPolicy, got {type(policy).__name__}"
        )
    persist = _requires_persistence(policy=policy, cost_hints=cost_hints)
    if not persist:
        return compute()

    identity_key = identity.identity_key()
    payload = store.get(identity_key)
    if payload is not None:
        return _restore_cached_feature_frame(
            payload=payload,
            identity_key=identity_key,
            expected_eval_keys=expected_eval_keys,
            expected_unit=expected_unit,
            expected_clock_domain=expected_clock_domain,
            expected_source_identities=expected_source_identities,
            expected_column_names=expected_column_names,
            expected_origins=expected_origins,
        )

    validate_after_lookup()
    frame = compute()
    encoded = _encode_feature_artifact(identity, frame)
    store.put(identity_key, encoded)
    return frame


def _requires_persistence(
    *,
    policy: MaterializationPolicy,
    cost_hints: tuple[_CostHint, ...],
) -> bool:
    if type(cost_hints) is not tuple or not cost_hints:
        raise _ContractViolationError("cost_hints must be a non-empty exact tuple")
    if any(type(hint) is not _CostHint for hint in cost_hints):
        raise _ContractViolationError("cost_hints must contain exact CostHint values")
    if policy is MaterializationPolicy.PERSIST:
        return True
    if policy is MaterializationPolicy.CACHE_IF_EXPENSIVE:
        return any(hint is _CostHint.EXPENSIVE for hint in cost_hints)
    return False


def _restore_cached_feature_frame(
    *,
    payload: bytes,
    identity_key: str,
    expected_eval_keys: _npt.NDArray[_np.int64],
    expected_unit: _TimeUnit,
    expected_clock_domain: _ClockDomain,
    expected_source_identities: tuple[_DatasetIdentity, ...],
    expected_column_names: tuple[str, ...],
    expected_origins: _Mapping[str, _FeatureOrigin],
) -> _FeatureFrame:
    decoded = _decode_feature_artifact(payload, expected_identity_key=identity_key)
    decoded_keys = _typing.cast(
        "_npt.NDArray[_np.int64]",
        decoded._keys._descriptor(),
    )
    if not _np.array_equal(decoded_keys, expected_eval_keys):
        raise _FeatureMaterializationError(
            "cached feature keys do not exactly match the bounded evaluation frame"
        )
    if decoded._unit is not expected_unit:
        raise _FeatureMaterializationError("cached feature time unit does not match the view")
    if decoded._clock_domain != expected_clock_domain:
        raise _FeatureMaterializationError("cached feature clock domain does not match the view")
    if decoded._source_identities != expected_source_identities:
        raise _FeatureMaterializationError(
            "cached feature source identities do not match the bounded segments"
        )
    if decoded._column_names != expected_column_names:
        raise _FeatureMaterializationError(
            "cached feature column order does not match the prepared provider plan"
        )
    if tuple(expected_origins) != expected_column_names:
        raise _ContractViolationError(
            "expected origins must follow the exact prepared feature-name order"
        )
    for name in expected_column_names:
        if not _origins_match(decoded._origins[name], expected_origins[name]):
            raise _FeatureMaterializationError(
                f"cached feature origin for {name!r} does not match the prepared plan"
            )

    columns = {
        name: _typing.cast(
            "_npt.NDArray[_np.float64]",
            decoded._columns[name]._descriptor(),
        )
        for name in expected_column_names
    }
    return _restore_feature_frame(
        keys=decoded_keys,
        unit=decoded._unit,
        clock_domain=decoded._clock_domain,
        source_identities=decoded._source_identities,
        columns=columns,
        origins=decoded._origins,
    )


def _origins_match(left: _FeatureOrigin, right: _FeatureOrigin) -> bool:
    return (
        type(left) is _FeatureOrigin
        and type(right) is _FeatureOrigin
        and left.provider_id == right.provider_id
        and left.version == right.version
        and left.window == right.window
        and _canonical_params_json(left.params) == _canonical_params_json(right.params)
    )
