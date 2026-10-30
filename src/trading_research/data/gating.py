"""Metadata-only split-policy classification and access authorization.

This module is the boundary between learning what a manifest says and being
allowed to open any partition it names.  It deliberately has no row decoder,
no partition-opening helper, and no market-time conversion.  Authorization is
computed from immutable manifest metadata and an exact, versioned split-policy
record; a future loader receives an :class:`AccessGrant`, never a bare
``RegisteredManifest``.

For access that requires an audit event, grant construction happens only after
the audit writer returns successfully.  That ordering is the central contract
of Slice 1C.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, Self
from uuid import UUID, uuid4

from trading_research.core.errors import (
    AccessDeniedError,
    AccessGrantMismatchError,
    AuditError,
    ContractViolationError,
    InvalidAccessReasonError,
    MalformedSplitPolicyError,
    SplitPolicyMismatchError,
    SplitPolicyNotFoundError,
    UnsupportedSplitPolicyError,
)
from trading_research.core.identity import DatasetIdentity, DatasetRef, ViewRole
from trading_research.core.time import CalendarTimePolicy, ClockDomain, TimeUnit
from trading_research.data.audit import operational_event_time_utc
from trading_research.data.manifests import (
    ManifestFormat,
    NativeKeyRange,
    NormalizedManifest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from trading_research.data.registry import DatasetRegistry, RegisteredManifest

__all__ = [
    "AccessClassification",
    "AccessContext",
    "AccessGrant",
    "AccessReason",
    "AccessRequest",
    "GatedUnlock",
    "SplitPolicy",
    "SplitPolicyIdentity",
    "SplitPolicyProvenance",
    "authorize_access",
    "classify_access",
    "read_split_policy",
]

_POLICY_SCHEMA_VERSION = "1"
_AUDIT_SCHEMA_VERSION = "1"
_AUDIT_EVENT_TYPE = "dataset_access_decision"
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_REASON_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_GIT_COMMIT = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_MAX_REASON_CODE_LENGTH = 64
_MAX_REASON_DETAIL_BYTES = 2_048
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "adr",
        "policy_version",
        "clock_domain",
        "time_unit",
        "calendar_time_policy",
        "ranges",
        "provenance",
    }
)
_RANGE_ROLES = (ViewRole.TRAIN, ViewRole.VALIDATION, ViewRole.OOS, ViewRole.LOCKBOX)
_RANGE_FIELDS = frozenset({"start_inclusive_key", "end_inclusive_key"})
_PROVENANCE_FIELDS = frozenset({"normative_source", "ratified_on"})


class _AuditAppender(Protocol):
    """The one operation authorization needs from the concrete audit writer."""

    def append(self, event: Mapping[str, object]) -> None:
        """Durably append ``event`` or raise an :class:`AuditError`."""


class _DuplicatePolicyKeyError(ValueError):
    """Internal signal from the strict JSON object-pairs hook."""


class _NonFinitePolicyNumberError(ValueError):
    """Internal signal for JSON constants outside the JSON number grammar."""


class AccessClassification(IntEnum):
    """Ordered friction required before a dataset may be read.

    Ordering is intentional: a role may raise the range-derived floor but can
    never lower it.
    """

    NORMAL = 0
    OOS_EXPLICIT = 1
    FULLY_GATED = 2

    @property
    def audit_value(self) -> str:
        """Stable lower-case representation written to audit events."""
        return self.name.lower()


@dataclass(frozen=True, slots=True)
class SplitPolicyIdentity:
    """Logical split-policy identity plus its exact-byte digest."""

    adr: str
    policy_version: str
    policy_sha256: str

    def __post_init__(self) -> None:
        """Validate logical labels and normalize the digest."""
        _require_nonempty_text(self.adr, "split policy ADR")
        _require_nonempty_text(self.policy_version, "split policy version")
        if (
            not isinstance(self.policy_sha256, str)
            or _SHA256_HEX.fullmatch(self.policy_sha256) is None
        ):
            raise MalformedSplitPolicyError(
                "split policy SHA-256 must be exactly 64 hexadecimal characters"
            )
        object.__setattr__(self, "policy_sha256", self.policy_sha256.lower())


@dataclass(frozen=True, slots=True)
class SplitPolicyProvenance:
    """Human-auditable source of a machine-readable split policy."""

    normative_source: str
    ratified_on: str

    def __post_init__(self) -> None:
        """Require both provenance labels to be present."""
        _require_nonempty_text(self.normative_source, "policy normative_source")
        _require_nonempty_text(self.ratified_on, "policy ratified_on")


@dataclass(frozen=True, slots=True, init=False)
class SplitPolicy:
    """The exact machine-readable policy used to classify manifest ranges.

    Ordinary construction is disabled: authorization accepts only an object
    minted by :func:`read_split_policy` from exact, validated bytes.
    """

    identity: SplitPolicyIdentity
    clock_domain: ClockDomain
    time_unit: TimeUnit
    calendar_time_policy: CalendarTimePolicy
    train_range: NativeKeyRange
    validation_range: NativeKeyRange
    oos_range: NativeKeyRange
    lockbox_range: NativeKeyRange
    provenance: SplitPolicyProvenance

    def __init__(self) -> None:
        """Refuse direct construction; policies come only from resolved bytes."""
        raise TypeError("SplitPolicy instances are issued only by read_split_policy")

    @classmethod
    def _from_resolved(
        cls,
        *,
        identity: SplitPolicyIdentity,
        clock_domain: ClockDomain,
        time_unit: TimeUnit,
        calendar_time_policy: CalendarTimePolicy,
        train_range: NativeKeyRange,
        validation_range: NativeKeyRange,
        oos_range: NativeKeyRange,
        lockbox_range: NativeKeyRange,
        provenance: SplitPolicyProvenance,
    ) -> Self:
        """Construct after strict parsing, then validate cross-field invariants."""
        policy = object.__new__(cls)
        object.__setattr__(policy, "identity", identity)
        object.__setattr__(policy, "clock_domain", clock_domain)
        object.__setattr__(policy, "time_unit", time_unit)
        object.__setattr__(policy, "calendar_time_policy", calendar_time_policy)
        object.__setattr__(policy, "train_range", train_range)
        object.__setattr__(policy, "validation_range", validation_range)
        object.__setattr__(policy, "oos_range", oos_range)
        object.__setattr__(policy, "lockbox_range", lockbox_range)
        object.__setattr__(policy, "provenance", provenance)
        policy._validate()
        return policy

    def _validate(self) -> None:
        """Require a valid axis and strictly ordered, non-overlapping ranges."""
        if not isinstance(self.identity, SplitPolicyIdentity):
            raise MalformedSplitPolicyError("policy identity has the wrong type")
        if not isinstance(self.clock_domain, ClockDomain):
            raise MalformedSplitPolicyError("policy clock_domain has the wrong type")
        if not isinstance(self.time_unit, TimeUnit):
            raise MalformedSplitPolicyError("policy time_unit has the wrong type")
        if not isinstance(self.calendar_time_policy, CalendarTimePolicy):
            raise MalformedSplitPolicyError("policy calendar_time_policy has the wrong type")
        if not isinstance(self.provenance, SplitPolicyProvenance):
            raise MalformedSplitPolicyError("policy provenance has the wrong type")

        ordered = tuple(self.range_for(role) for role in _RANGE_ROLES)
        for role, span in zip(_RANGE_ROLES, ordered, strict=True):
            _validate_policy_range(span, role)
        for left_role, left, right_role, right in zip(
            _RANGE_ROLES, ordered, _RANGE_ROLES[1:], ordered[1:], strict=False
        ):
            if left.end >= right.start:
                raise MalformedSplitPolicyError(
                    f"split policy ranges overlap or are out of order: "
                    f"{left_role.value} ends at {left.end}, "
                    f"{right_role.value} starts at {right.start}"
                )

    def range_for(self, role: ViewRole) -> NativeKeyRange:
        """Return the policy range for a Tier-1 role."""
        ranges = {
            ViewRole.TRAIN: self.train_range,
            ViewRole.VALIDATION: self.validation_range,
            ViewRole.OOS: self.oos_range,
            ViewRole.LOCKBOX: self.lockbox_range,
        }
        try:
            return ranges[role]
        except KeyError as exc:
            raise SplitPolicyMismatchError(
                f"role {role.value!r} has no Tier-1 range in the split policy"
            ) from exc


@dataclass(frozen=True, slots=True)
class AccessReason:
    """Structured, bounded reason supplied by resolved configuration."""

    code: str
    detail: str

    def __post_init__(self) -> None:
        """Require a lower-case slug and a trimmed, bounded explanation."""
        if (
            not isinstance(self.code, str)
            or not self.code
            or len(self.code) > _MAX_REASON_CODE_LENGTH
            or _REASON_CODE.fullmatch(self.code) is None
        ):
            raise InvalidAccessReasonError(
                "access reason code must be a lower-case hyphenated slug of at most "
                f"{_MAX_REASON_CODE_LENGTH} characters",
                denial_code="invalid_reason",
            )
        if not isinstance(self.detail, str):
            raise InvalidAccessReasonError(
                "access reason detail must be a string",
                denial_code="invalid_reason",
            )
        trimmed = self.detail.strip()
        if not trimmed:
            raise InvalidAccessReasonError(
                "access reason detail must not be empty",
                denial_code="invalid_reason",
            )
        if len(trimmed.encode("utf-8")) > _MAX_REASON_DETAIL_BYTES:
            raise InvalidAccessReasonError(
                f"access reason detail exceeds {_MAX_REASON_DETAIL_BYTES} UTF-8 bytes",
                denial_code="invalid_reason",
            )
        object.__setattr__(self, "detail", trimmed)


@dataclass(frozen=True, slots=True)
class GatedUnlock:
    """Explicit CLI authorization bound to one exact canonical identity."""

    target: DatasetIdentity

    def __post_init__(self) -> None:
        """Require an exact canonical target, never a role or path."""
        if not isinstance(self.target, DatasetIdentity):
            raise AccessDeniedError(
                "gated unlock target must be a DatasetIdentity",
                denial_code="invalid_unlock_target",
            )


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """One exact metadata identity plus the authorization presented for it."""

    dataset_ref: DatasetRef
    reason: AccessReason | None = None
    gated_unlock: GatedUnlock | None = None

    def __post_init__(self) -> None:
        """Reject malformed request members before registry resolution."""
        if not isinstance(self.dataset_ref, DatasetRef):
            raise AccessDeniedError(
                "access request must carry an exact DatasetRef",
                denial_code="invalid_dataset_ref",
            )
        if self.reason is not None and not isinstance(self.reason, AccessReason):
            raise InvalidAccessReasonError(
                "access request reason must be an AccessReason",
                denial_code="invalid_reason",
            )
        if self.gated_unlock is not None and not isinstance(self.gated_unlock, GatedUnlock):
            raise AccessDeniedError(
                "access request gated_unlock must be a GatedUnlock",
                denial_code="invalid_unlock",
            )


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Minimal caller and platform identity captured once per invocation."""

    session_id: UUID
    os_user: str
    hostname: str
    pid: int
    platform_git_commit: str
    platform_git_dirty: bool

    def __post_init__(self) -> None:
        """Validate the caller facts without inventing a run object."""
        if not isinstance(self.session_id, UUID) or self.session_id.version != 4:
            raise AccessDeniedError(
                "access context session_id must be a UUID4",
                denial_code="invalid_access_context",
            )
        _require_context_text(self.os_user, "os_user")
        _require_context_text(self.hostname, "hostname")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise AccessDeniedError(
                "access context pid must be a positive integer",
                denial_code="invalid_access_context",
            )
        if (
            not isinstance(self.platform_git_commit, str)
            or _GIT_COMMIT.fullmatch(self.platform_git_commit) is None
        ):
            raise AccessDeniedError(
                "access context platform_git_commit must be a 40- or 64-character hex digest",
                denial_code="invalid_access_context",
            )
        object.__setattr__(self, "platform_git_commit", self.platform_git_commit.lower())
        if not isinstance(self.platform_git_dirty, bool):
            raise AccessDeniedError(
                "access context platform_git_dirty must be a bool",
                denial_code="invalid_access_context",
            )


@dataclass(frozen=True, slots=True, init=False)
class AccessGrant:
    """In-process proof that one exact registered manifest passed the gate.

    Construction is intentionally private-by-convention and disabled through
    the ordinary dataclass initializer.  V1 protects against accidental and
    ambient access, not a malicious Python caller with owner privileges.
    """

    registered_manifest: RegisteredManifest
    classification: AccessClassification
    split_policy_identity: SplitPolicyIdentity
    session_id: UUID
    audit_event_id: str | None

    def __init__(self) -> None:
        """Refuse direct construction; grants are issued only by the gate."""
        raise TypeError("AccessGrant instances are issued only by authorize_access")

    @classmethod
    def _issue(
        cls,
        *,
        registered_manifest: RegisteredManifest,
        classification: AccessClassification,
        split_policy_identity: SplitPolicyIdentity,
        session_id: UUID,
        audit_event_id: str | None,
    ) -> Self:
        """Issue a grant after classification and any required audit write."""
        grant = object.__new__(cls)
        object.__setattr__(grant, "registered_manifest", registered_manifest)
        object.__setattr__(grant, "classification", classification)
        object.__setattr__(grant, "split_policy_identity", split_policy_identity)
        object.__setattr__(grant, "session_id", session_id)
        object.__setattr__(grant, "audit_event_id", audit_event_id)
        return grant

    @property
    def dataset_ref(self) -> DatasetRef:
        """Exact role-validated reference bound into this grant."""
        return _dataset_ref_for(self.registered_manifest.manifest)

    def require_matches(
        self,
        *,
        registered_manifest: RegisteredManifest,
        split_policy_identity: SplitPolicyIdentity,
    ) -> None:
        """Refuse reuse for different data or different policy bytes."""
        candidate_ref = _dataset_ref_for(registered_manifest.manifest)
        if candidate_ref != self.dataset_ref:
            raise AccessGrantMismatchError(
                f"access grant is for {self.dataset_ref.canonical_identity!r}, "
                f"not {candidate_ref.canonical_identity!r}"
            )
        if split_policy_identity != self.split_policy_identity:
            raise AccessGrantMismatchError(
                "access grant was issued under different split-policy bytes"
            )


def read_split_policy(
    path: Path,
    *,
    expected_identity: SplitPolicyIdentity | None = None,
) -> SplitPolicy:
    """Read and strictly validate one exact-byte JSON split-policy record.

    Unknown fields, missing fields, duplicate object keys, non-finite numbers,
    unsupported schema versions and malformed ranges are all refusals.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SplitPolicyNotFoundError(f"cannot read split policy {path}: {exc}") from exc

    digest = hashlib.sha256(raw_bytes).hexdigest()
    try:
        document: object = json.loads(
            raw_bytes,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite_number,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, _DuplicatePolicyKeyError) as exc:
        raise MalformedSplitPolicyError(
            f"split policy {path} is not strict JSON: {exc}"
        ) from exc
    except _NonFinitePolicyNumberError as exc:
        raise MalformedSplitPolicyError(f"split policy {path} contains {exc}") from exc

    root = _require_object(document, "split policy")
    _require_exact_fields(root, _TOP_LEVEL_FIELDS, "split policy")

    schema_version = _require_string(root, "schema_version", "split policy")
    if schema_version != _POLICY_SCHEMA_VERSION:
        raise UnsupportedSplitPolicyError(
            f"split policy {path} declares schema_version {schema_version!r}; "
            f"supported: {_POLICY_SCHEMA_VERSION!r}"
        )

    identity = SplitPolicyIdentity(
        adr=_require_string(root, "adr", "split policy"),
        policy_version=_require_string(root, "policy_version", "split policy"),
        policy_sha256=digest,
    )
    if expected_identity is not None and identity != expected_identity:
        raise SplitPolicyMismatchError(
            f"requested split policy {expected_identity!r}, but {path} resolves to {identity!r}"
        )

    clock_domain_text = _require_string(root, "clock_domain", "split policy")
    time_unit_text = _require_string(root, "time_unit", "split policy")
    calendar_policy_text = _require_string(root, "calendar_time_policy", "split policy")
    try:
        clock_domain = ClockDomain(clock_domain_text)
        time_unit = TimeUnit(time_unit_text)
        calendar_policy = CalendarTimePolicy(calendar_policy_text)
    except (ContractViolationError, TypeError, ValueError) as exc:
        raise MalformedSplitPolicyError(f"split policy {path} has an invalid axis: {exc}") from exc

    ranges_document = _require_object(root["ranges"], "split policy ranges")
    expected_range_names = frozenset(role.value for role in _RANGE_ROLES)
    _require_exact_fields(ranges_document, expected_range_names, "split policy ranges")
    ranges = {
        role: _read_range(ranges_document[role.value], role)
        for role in _RANGE_ROLES
    }

    provenance_document = _require_object(root["provenance"], "split policy provenance")
    _require_exact_fields(
        provenance_document, _PROVENANCE_FIELDS, "split policy provenance"
    )
    provenance = SplitPolicyProvenance(
        normative_source=_require_string(
            provenance_document, "normative_source", "split policy provenance"
        ),
        ratified_on=_require_string(
            provenance_document, "ratified_on", "split policy provenance"
        ),
    )

    return SplitPolicy._from_resolved(
        identity=identity,
        clock_domain=clock_domain,
        time_unit=time_unit,
        calendar_time_policy=calendar_policy,
        train_range=ranges[ViewRole.TRAIN],
        validation_range=ranges[ViewRole.VALIDATION],
        oos_range=ranges[ViewRole.OOS],
        lockbox_range=ranges[ViewRole.LOCKBOX],
        provenance=provenance,
    )


def classify_access(
    manifest: NormalizedManifest,
    policy: SplitPolicy,
) -> AccessClassification:
    """Return the required access level after exact policy validation."""
    classification, _ = _classify_with_basis(manifest, policy)
    return classification


def authorize_access(
    *,
    registry: DatasetRegistry,
    request: AccessRequest,
    policy: SplitPolicy,
    context: AccessContext,
    audit_writer: _AuditAppender,
) -> AccessGrant:
    """Resolve, classify, audit where required, then issue an access grant.

    Exact registry resolution occurs first, but its result remains metadata.
    No grant for OOS or fully gated data is constructed until the corresponding
    allowed audit event has been durably appended.
    """
    registered = registry.resolve(request.dataset_ref)
    classification, basis = _classify_with_basis(registered.manifest, policy)
    denial = _ceremony_denial(classification, request)

    if classification is AccessClassification.NORMAL:
        if denial is not None:
            raise denial
        return AccessGrant._issue(
            registered_manifest=registered,
            classification=classification,
            split_policy_identity=policy.identity,
            session_id=context.session_id,
            audit_event_id=None,
        )

    outcome = "denied" if denial is not None else "allowed"
    event_id = str(uuid4())
    event = _audit_event(
        event_id=event_id,
        outcome=outcome,
        denial_code=denial.denial_code if denial is not None else None,
        registered=registered,
        policy=policy,
        classification=classification,
        basis=basis,
        request=request,
        context=context,
    )
    try:
        audit_writer.append(event)
    except AuditError as exc:
        if denial is not None:
            raise exc from denial
        raise

    if denial is not None:
        raise denial

    return AccessGrant._issue(
        registered_manifest=registered,
        classification=classification,
        split_policy_identity=policy.identity,
        session_id=context.session_id,
        audit_event_id=event_id,
    )


def _classify_with_basis(
    manifest: NormalizedManifest,
    policy: SplitPolicy,
) -> tuple[AccessClassification, str]:
    """Validate compatibility, then apply range classification and role floor."""
    _validate_manifest_policy_compatibility(manifest, policy)

    admitted = manifest.admitted_native_range
    if admitted.end > policy.oos_range.end:
        classification = AccessClassification.FULLY_GATED
        basis = "range_exceeds_oos_boundary"
    elif admitted.start <= policy.oos_range.end and admitted.end >= policy.oos_range.start:
        classification = AccessClassification.OOS_EXPLICIT
        basis = "range_overlaps_oos"
    else:
        classification = AccessClassification.NORMAL
        basis = "range_precedes_oos"

    role_floor = _role_floor(manifest.role)
    if role_floor > classification:
        classification = role_floor
        basis = f"role_floor_{manifest.role.value}"
    return classification, basis


def _validate_manifest_policy_compatibility(
    manifest: NormalizedManifest,
    policy: SplitPolicy,
) -> None:
    """Require one axis and, for generated views, one exact policy artifact."""
    if manifest.clock_domain != policy.clock_domain:
        raise SplitPolicyMismatchError(
            f"{manifest.dataset_id}: manifest clock domain "
            f"{manifest.clock_domain.identifier!r} differs from split policy "
            f"{policy.clock_domain.identifier!r}"
        )
    if manifest.time_unit is not policy.time_unit:
        raise SplitPolicyMismatchError(
            f"{manifest.dataset_id}: manifest time unit {manifest.time_unit.value!r} differs "
            f"from split policy {policy.time_unit.value!r}"
        )

    reference = manifest.split_policy_ref
    is_generated = manifest.source_format is ManifestFormat.VIEW_MANIFEST_V1_1
    if reference is None:
        if is_generated:
            raise SplitPolicyMismatchError(
                f"{manifest.dataset_id}: generated view has no exact split-policy reference"
            )
    elif (
        reference.adr != policy.identity.adr
        or reference.policy_version != policy.identity.policy_version
        or reference.policy_sha256 != policy.identity.policy_sha256
    ):
        raise SplitPolicyMismatchError(
            f"{manifest.dataset_id}: manifest pins split policy "
            f"({reference.adr!r}, {reference.policy_version!r}, "
            f"{reference.policy_sha256}), but the resolved policy is "
            f"({policy.identity.adr!r}, {policy.identity.policy_version!r}, "
            f"{policy.identity.policy_sha256})"
        )

    if manifest.calendar_time_policy is None:
        if is_generated:
            raise SplitPolicyMismatchError(
                f"{manifest.dataset_id}: generated view has no calendar-time policy"
            )
    elif manifest.calendar_time_policy is not policy.calendar_time_policy:
        raise SplitPolicyMismatchError(
            f"{manifest.dataset_id}: manifest calendar policy "
            f"{manifest.calendar_time_policy.value!r} differs from split policy "
            f"{policy.calendar_time_policy.value!r}"
        )

    admitted = manifest.admitted_native_range
    if is_generated and manifest.role in _RANGE_ROLES:
        allowed = policy.range_for(manifest.role)
        if admitted.start < allowed.start or admitted.end > allowed.end:
            raise SplitPolicyMismatchError(
                f"{manifest.dataset_id}: observed {manifest.role.value} range "
                f"[{admitted.start}, {admitted.end}] is outside policy range "
                f"[{allowed.start}, {allowed.end}]"
            )


def _role_floor(role: ViewRole) -> AccessClassification:
    """Return the minimum friction implied by a validated manifest role."""
    if role is ViewRole.OOS:
        return AccessClassification.OOS_EXPLICIT
    if role in {ViewRole.RAW, ViewRole.CURATED, ViewRole.SNAPSHOT, ViewRole.LOCKBOX}:
        return AccessClassification.FULLY_GATED
    return AccessClassification.NORMAL


def _ceremony_denial(
    classification: AccessClassification,
    request: AccessRequest,
) -> AccessDeniedError | None:
    """Return the precise ceremony failure, or ``None`` when complete."""
    reason_present = request.reason is not None
    unlock = request.gated_unlock

    if classification is AccessClassification.NORMAL:
        if reason_present or unlock is not None:
            return AccessDeniedError(
                "normal access must not carry an OOS reason or gated unlock",
                denial_code="unexpected_authorization",
            )
        return None

    if classification is AccessClassification.OOS_EXPLICIT:
        if unlock is not None:
            return AccessDeniedError(
                "OOS access must be explicit and reasoned, but does not accept a gated unlock",
                denial_code="unexpected_gated_unlock",
            )
        if not reason_present:
            return AccessDeniedError(
                "OOS access requires a structured reason",
                denial_code="reason_required",
            )
        return None

    if unlock is None and not reason_present:
        return AccessDeniedError(
            "fully gated access requires both an exact unlock and a structured reason",
            denial_code="unlock_and_reason_required",
        )
    if unlock is None:
        return AccessDeniedError(
            "fully gated access requires an exact gated unlock",
            denial_code="unlock_required",
        )
    if unlock.target != request.dataset_ref.canonical_identity:
        return AccessDeniedError(
            "gated unlock targets a different dataset identity",
            denial_code="unlock_identity_mismatch",
        )
    if not reason_present:
        return AccessDeniedError(
            "fully gated access requires a structured reason",
            denial_code="reason_required",
        )
    return None


def _audit_event(
    *,
    event_id: str,
    outcome: str,
    denial_code: str | None,
    registered: RegisteredManifest,
    policy: SplitPolicy,
    classification: AccessClassification,
    basis: str,
    request: AccessRequest,
    context: AccessContext,
) -> dict[str, object]:
    """Build the exact JSON-native Slice 1C audit schema."""
    manifest = registered.manifest
    admitted = manifest.admitted_native_range
    unlock = request.gated_unlock
    reason = request.reason
    return {
        "schema_version": _AUDIT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": _AUDIT_EVENT_TYPE,
        "event_time_utc": operational_event_time_utc(),
        "outcome": outcome,
        "denial_code": denial_code,
        "caller": {
            "session_id": str(context.session_id),
            "os_user": context.os_user,
            "hostname": context.hostname,
            "pid": context.pid,
        },
        "platform": {
            "git_commit": context.platform_git_commit,
            "git_dirty": context.platform_git_dirty,
        },
        "dataset_ref": {
            "dataset_id": request.dataset_ref.dataset_id,
            "manifest_sha256": request.dataset_ref.manifest_sha256,
            "role": request.dataset_ref.role.value,
        },
        "manifest_axis": {
            "clock_domain": manifest.clock_domain.identifier,
            "time_unit": manifest.time_unit.value,
        },
        "admitted_native_range": {"start": admitted.start, "end": admitted.end},
        "split_policy": {
            "adr": policy.identity.adr,
            "policy_version": policy.identity.policy_version,
            "policy_sha256": policy.identity.policy_sha256,
        },
        "classification": classification.audit_value,
        "classification_basis": basis,
        "authorization": {
            "unlock_present": unlock is not None,
            "unlock_target": (
                None
                if unlock is None
                else {
                    "dataset_id": unlock.target.dataset_id,
                    "manifest_sha256": unlock.target.manifest_sha256,
                }
            ),
            "reason": (
                None if reason is None else {"code": reason.code, "detail": reason.detail}
            ),
        },
    }


def _dataset_ref_for(manifest: NormalizedManifest) -> DatasetRef:
    """Construct the exact role-bearing reference declared by ``manifest``."""
    return DatasetRef(
        dataset_id=manifest.dataset_id,
        manifest_sha256=manifest.manifest_sha256,
        role=manifest.role,
    )


def _strict_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate keys at every depth."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicatePolicyKeyError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> object:
    """Reject the non-standard NaN/Infinity constants accepted by ``json``."""
    raise _NonFinitePolicyNumberError(f"non-finite number {value!r}")


def _require_object(value: object, context: str) -> dict[str, object]:
    """Return a JSON object or raise a policy-format error."""
    if not isinstance(value, dict):
        raise MalformedSplitPolicyError(
            f"{context} must be a JSON object, got {type(value).__name__}"
        )
    if not all(isinstance(key, str) for key in value):
        raise MalformedSplitPolicyError(f"{context} contains a non-string key")
    return value


def _require_exact_fields(
    value: Mapping[str, object],
    expected: frozenset[str],
    context: str,
) -> None:
    """Require exactly ``expected`` keys, with neither defaults nor extensions."""
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise MalformedSplitPolicyError(f"{context} fields are invalid: {', '.join(details)}")


def _require_string(value: Mapping[str, object], key: str, context: str) -> str:
    """Return one required non-empty string from a validated object."""
    candidate = value[key]
    if not isinstance(candidate, str) or not candidate or candidate != candidate.strip():
        raise MalformedSplitPolicyError(
            f"{context} field {key!r} must be a non-empty trimmed string"
        )
    return candidate


def _read_range(value: object, role: ViewRole) -> NativeKeyRange:
    """Parse one strict, inclusive int64 policy range."""
    context = f"split policy {role.value} range"
    document = _require_object(value, context)
    _require_exact_fields(document, _RANGE_FIELDS, context)
    start = _require_int64(document, "start_inclusive_key", context)
    end = _require_int64(document, "end_inclusive_key", context)
    if end < start:
        raise MalformedSplitPolicyError(
            f"{context} is inverted: start {start} > end {end}"
        )
    return NativeKeyRange(start=start, end=end)


def _require_int64(value: Mapping[str, object], key: str, context: str) -> int:
    """Return a required signed int64, rejecting ``bool`` explicitly."""
    candidate = value[key]
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise MalformedSplitPolicyError(
            f"{context} field {key!r} must be an integer, got {type(candidate).__name__}"
        )
    if not _INT64_MIN <= candidate <= _INT64_MAX:
        raise MalformedSplitPolicyError(
            f"{context} field {key!r} is outside the signed 64-bit range"
        )
    return candidate


def _validate_policy_range(span: NativeKeyRange, role: ViewRole) -> None:
    """Validate a directly constructed policy range, not only parsed ranges."""
    if not isinstance(span, NativeKeyRange):
        raise MalformedSplitPolicyError(
            f"split policy {role.value} range has the wrong type"
        )
    for label, value in (("start", span.start), ("end", span.end)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise MalformedSplitPolicyError(
                f"split policy {role.value} range {label} must be an integer"
            )
        if not _INT64_MIN <= value <= _INT64_MAX:
            raise MalformedSplitPolicyError(
                f"split policy {role.value} range {label} is outside signed int64"
            )
    if span.end < span.start:
        raise MalformedSplitPolicyError(
            f"split policy {role.value} range is inverted"
        )


def _require_nonempty_text(value: object, label: str) -> str:
    """Validate a trimmed non-empty policy label."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise MalformedSplitPolicyError(f"{label} must be a non-empty trimmed string")
    return value


def _require_context_text(value: object, label: str) -> str:
    """Validate a trimmed non-empty access-context label."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise AccessDeniedError(
            f"access context {label} must be a non-empty trimmed string",
            denial_code="invalid_access_context",
        )
    return value
