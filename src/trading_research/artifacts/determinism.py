"""Cache-bypassing exact determinism verification for feature artifacts."""

from __future__ import annotations

import hashlib as _hashlib
import re as _re
import typing as _typing
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.artifacts.container import _encode_feature_artifact
from trading_research.artifacts.identity import StageIdentity as _StageIdentity
from trading_research.artifacts.materialization import (
    MaterializationPolicy as _MaterializationPolicy,
)
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import canonical_params_json as _canonical_params_json

if _typing.TYPE_CHECKING:
    from collections.abc import Callable as _Callable

__all__ = ["DeterminismReport", "verify_determinism"]

_SHA256_HEX = _re.compile(r"^[0-9a-f]{64}$")


@_dataclass(frozen=True, slots=True)
class DeterminismReport:
    """Scalar result of two independent, cache-bypassing recomputations."""

    identity_key: str
    first_content_hash: str
    second_content_hash: str
    structurally_equal: bool
    deterministic: bool

    def __post_init__(self) -> None:
        """Refuse malformed or internally contradictory report values."""
        for label, value in (
            ("identity_key", self.identity_key),
            ("first_content_hash", self.first_content_hash),
            ("second_content_hash", self.second_content_hash),
        ):
            if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
                raise _ContractViolationError(
                    f"{label} must be exactly 64 lowercase hexadecimal characters"
                )
        if type(self.structurally_equal) is not bool:
            raise _ContractViolationError("structurally_equal must be an exact bool")
        if type(self.deterministic) is not bool:
            raise _ContractViolationError("deterministic must be an exact bool")
        if self.deterministic and (
            not self.structurally_equal
            or self.first_content_hash != self.second_content_hash
        ):
            raise _ContractViolationError(
                "a deterministic report requires equal structure and content hashes"
            )


def verify_determinism(
    identity: _StageIdentity,
    recompute: _Callable[[_MaterializationPolicy], _FeatureFrame],
) -> DeterminismReport:
    """Invoke a trusted stage recomputation twice with forced ephemeral policy.

    ``recompute`` is the composition capability for the supplied identity. It
    must honor the passed policy and recompute those exact semantic inputs; as
    with a provider's ``compute`` method, arbitrary hidden I/O or undeclared
    mutable semantics are outside the callable contract.
    """
    if type(identity) is not _StageIdentity:
        raise _ContractViolationError("identity must be an exact StageIdentity")
    if not callable(recompute):
        raise _ContractViolationError("recompute must be callable")

    first = recompute(_MaterializationPolicy.EPHEMERAL)
    first_payload = _encode_feature_artifact(identity, first)
    first_content_hash = _hashlib.sha256(first_payload).hexdigest()

    second = recompute(_MaterializationPolicy.EPHEMERAL)
    second_payload = _encode_feature_artifact(identity, second)
    second_content_hash = _hashlib.sha256(second_payload).hexdigest()

    structurally_equal = _feature_frames_structurally_equal(first, second)
    deterministic = structurally_equal and first_payload == second_payload
    return DeterminismReport(
        identity_key=identity.identity_key(),
        first_content_hash=first_content_hash,
        second_content_hash=second_content_hash,
        structurally_equal=structurally_equal,
        deterministic=deterministic,
    )


def _feature_frames_structurally_equal(
    left: _FeatureFrame,
    right: _FeatureFrame,
) -> bool:
    """Compare exact structure while leaving value bits to artifact bytes."""
    if type(left) is not _FeatureFrame or type(right) is not _FeatureFrame:
        return False

    left_keys = left.keys
    right_keys = right.keys
    if (
        left_keys.dtype != right_keys.dtype
        or left_keys.shape != right_keys.shape
        or left_keys.tobytes(order="C") != right_keys.tobytes(order="C")
        or left.unit is not right.unit
        or left.clock_domain != right.clock_domain
        or left.source_identities != right.source_identities
        or left.column_names != right.column_names
        or tuple(left.schema.items()) != tuple(right.schema.items())
    ):
        return False

    for name in left.column_names:
        left_values = left.column(name)
        right_values = right.column(name)
        if (
            left_values.dtype != right_values.dtype
            or left_values.shape != right_values.shape
            or _np.isnan(left_values).tobytes(order="C")
            != _np.isnan(right_values).tobytes(order="C")
            or not _origins_equal(left.origin(name), right.origin(name))
        ):
            return False
    return True


def _origins_equal(left: _FeatureOrigin, right: _FeatureOrigin) -> bool:
    return (
        type(left) is _FeatureOrigin
        and type(right) is _FeatureOrigin
        and left.provider_id == right.provider_id
        and left.version == right.version
        and left.window == right.window
        and _canonical_params_json(left.params) == _canonical_params_json(right.params)
    )
