"""Authorization-aware bounded composition over verified market frames.

This module performs no row decoding and no filesystem access.  Its only data
source is :meth:`MarketFrameLoader.load`, called with the exact grant for each
member whose admitted rows intersect the requested bounded interval.  The
loader and grants are consumed during construction and never retained by the
returned semantic objects.
"""

from __future__ import annotations

import re as _re
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from itertools import pairwise as _pairwise
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Self as _Self

from trading_research.core.errors import ClockDomainError as _ClockDomainError
from trading_research.core.errors import InsufficientContextError as _InsufficientContextError
from trading_research.core.errors import TimeUnitMismatchError as _TimeUnitMismatchError
from trading_research.core.errors import ViewBoundsError as _ViewBoundsError
from trading_research.core.errors import ViewCompositionError as _ViewCompositionError
from trading_research.core.errors import ViewMemberError as _ViewMemberError
from trading_research.core.frames import MarketFrame as _MarketFrame
from trading_research.core.identity import DatasetRef as _DatasetRef
from trading_research.core.identity import ViewRole as _ViewRole
from trading_research.core.time import NativeTimeKey as _NativeTimeKey
from trading_research.data.gating import AccessClassification as _AccessClassification
from trading_research.data.gating import AccessGrant as _AccessGrant
from trading_research.data.gating import SplitPolicy as _SplitPolicy
from trading_research.data.gating import SplitPolicyIdentity as _SplitPolicyIdentity
from trading_research.data.integrity import MarketFrameLoader as _MarketFrameLoader

if _TYPE_CHECKING:
    from trading_research.data.manifests import NormalizedManifest as _NormalizedManifest

__all__ = [
    "BoundedSegment",
    "BoundedView",
    "ContextResolution",
    "ContextShortfall",
    "ViewUnion",
    "ViewUnionMember",
    "open_bounded_view",
]

_SHORTFALL_REASON_CODE = _re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SHORTFALL_REASON_CODE_LENGTH = 64
_MAX_SHORTFALL_DETAIL_BYTES = 2_048


@_dataclass(frozen=True, slots=True, init=False)
class BoundedSegment:
    """One exact member's already-bounded, immutable market frame."""

    _dataset_ref: _DatasetRef
    _frame: _MarketFrame

    def __init__(self) -> None:
        """Refuse construction outside the bounded composition route."""
        raise TypeError("BoundedSegment instances are issued by open_bounded_view")

    @classmethod
    def _from_bounded(cls, frame: _MarketFrame) -> _Self:
        if type(frame) is not _MarketFrame:
            raise _ViewMemberError("bounded segment requires an exact MarketFrame")
        segment = object.__new__(cls)
        object.__setattr__(segment, "_dataset_ref", frame.dataset_ref)
        object.__setattr__(segment, "_frame", frame)
        return segment

    @property
    def dataset_ref(self) -> _DatasetRef:
        """Exact hash-pinned identity of this bounded segment."""
        return self._dataset_ref

    @property
    def role(self) -> _ViewRole:
        """Manifest-validated Tier-1 role of this segment."""
        return self._dataset_ref.role

    @property
    def frame(self) -> _MarketFrame:
        """The member's already-bounded frame; never an underlying full frame."""
        return self._frame


@_dataclass(frozen=True, slots=True)
class ContextShortfall:
    """Explicit evidence accepting one precisely measured context shortfall."""

    requested_context_start: _NativeTimeKey
    resolved_context_start: _NativeTimeKey
    reason_code: str
    detail: str
    accepted: bool

    def __post_init__(self) -> None:
        """Validate structured, bounded evidence without interpreting time."""
        if not isinstance(self.requested_context_start, _NativeTimeKey):
            raise _ViewBoundsError(
                "shortfall requested_context_start must be a NativeTimeKey"
            )
        if not isinstance(self.resolved_context_start, _NativeTimeKey):
            raise _ViewBoundsError(
                "shortfall resolved_context_start must be a NativeTimeKey"
            )
        if self.resolved_context_start <= self.requested_context_start:
            raise _ViewBoundsError(
                "shortfall resolved context boundary must follow the requested boundary"
            )
        if (
            not isinstance(self.reason_code, str)
            or not self.reason_code
            or len(self.reason_code) > _MAX_SHORTFALL_REASON_CODE_LENGTH
            or _SHORTFALL_REASON_CODE.fullmatch(self.reason_code) is None
        ):
            raise _ViewCompositionError(
                "context-shortfall reason_code must be a lower-case hyphenated slug "
                f"of at most {_MAX_SHORTFALL_REASON_CODE_LENGTH} characters"
            )
        if not isinstance(self.detail, str):
            raise _ViewCompositionError("context-shortfall detail must be a string")
        trimmed = self.detail.strip()
        if not trimmed:
            raise _ViewCompositionError("context-shortfall detail must not be empty")
        if len(trimmed.encode("utf-8")) > _MAX_SHORTFALL_DETAIL_BYTES:
            raise _ViewCompositionError(
                "context-shortfall detail exceeds "
                f"{_MAX_SHORTFALL_DETAIL_BYTES} UTF-8 bytes"
            )
        if not isinstance(self.accepted, bool):
            raise _ViewCompositionError(
                "context-shortfall accepted status must be a bool"
            )
        object.__setattr__(self, "detail", trimmed)


@_dataclass(frozen=True, slots=True, init=False)
class ContextResolution:
    """Immutable result of resolving requested backward context."""

    _requested_context_start: _NativeTimeKey
    _resolved_context_start: _NativeTimeKey
    _shortfall: ContextShortfall | None

    def __init__(self) -> None:
        """Refuse construction outside the bounded composition route."""
        raise TypeError("ContextResolution instances are issued by open_bounded_view")

    @classmethod
    def _resolved(
        cls,
        *,
        requested_context_start: _NativeTimeKey,
        resolved_context_start: _NativeTimeKey,
        shortfall: ContextShortfall | None,
    ) -> _Self:
        resolution = object.__new__(cls)
        object.__setattr__(
            resolution,
            "_requested_context_start",
            requested_context_start,
        )
        object.__setattr__(
            resolution,
            "_resolved_context_start",
            resolved_context_start,
        )
        object.__setattr__(resolution, "_shortfall", shortfall)
        return resolution

    @property
    def requested_context_start(self) -> _NativeTimeKey:
        """The caller's requested earliest readable boundary."""
        return self._requested_context_start

    @property
    def resolved_context_start(self) -> _NativeTimeKey:
        """The effective earliest readable boundary after resolution."""
        return self._resolved_context_start

    @property
    def shortfall(self) -> ContextShortfall | None:
        """Accepted structured evidence, or ``None`` when context was complete."""
        return self._shortfall

    @property
    def short_context_accepted(self) -> bool:
        """Whether the returned view explicitly accepted shorter context."""
        return self._shortfall is not None and self._shortfall.accepted


@_dataclass(frozen=True, slots=True, init=False)
class BoundedView:
    """Ordered bounded segments plus an evaluation-only frame.

    No loader, grant, registry, manifest, policy, or filesystem location is
    retained.  There is deliberately no cross-member ``frame()`` accessor.
    """

    _segments: tuple[BoundedSegment, ...]
    _eval_frame: _MarketFrame
    _context: ContextResolution
    _context_start: _NativeTimeKey
    _eval_start: _NativeTimeKey
    _eval_end: _NativeTimeKey

    def __init__(self) -> None:
        """Refuse construction outside the bounded composition route."""
        raise TypeError("BoundedView instances are issued by open_bounded_view")

    @classmethod
    def _from_bounded(
        cls,
        *,
        segments: tuple[BoundedSegment, ...],
        eval_frame: _MarketFrame,
        context: ContextResolution,
        context_start: _NativeTimeKey,
        eval_start: _NativeTimeKey,
        eval_end: _NativeTimeKey,
    ) -> _Self:
        view = object.__new__(cls)
        object.__setattr__(view, "_segments", segments)
        object.__setattr__(view, "_eval_frame", eval_frame)
        object.__setattr__(view, "_context", context)
        object.__setattr__(view, "_context_start", context_start)
        object.__setattr__(view, "_eval_start", eval_start)
        object.__setattr__(view, "_eval_end", eval_end)
        return view

    @property
    def segments(self) -> tuple[BoundedSegment, ...]:
        """Ordered immutable sequence of individually identified segments."""
        return self._segments

    @property
    def eval_frame(self) -> _MarketFrame:
        """Evaluation member rows bounded exactly to the requested interval."""
        return self._eval_frame

    @property
    def context(self) -> ContextResolution:
        """Immutable context resolution and any accepted shortfall evidence."""
        return self._context

    @property
    def context_start(self) -> _NativeTimeKey:
        """Requested earliest context boundary."""
        return self._context_start

    @property
    def eval_start(self) -> _NativeTimeKey:
        """First native key eligible for evaluation output."""
        return self._eval_start

    @property
    def eval_end(self) -> _NativeTimeKey:
        """Point-in-time upper boundary; no exposed frame can exceed it."""
        return self._eval_end


@_dataclass(frozen=True, slots=True, init=False)
class ViewUnionMember:
    """One exact grant-validated, hash-pinned fitting-union member.

    The grant is consumed during construction.  Only the member reference and
    exact policy identity needed for later union validation are retained.
    """

    _dataset_ref: _DatasetRef
    _split_policy_identity: _SplitPolicyIdentity

    def __init__(self, *, dataset_ref: _DatasetRef, grant: _AccessGrant) -> None:
        if not isinstance(dataset_ref, _DatasetRef):
            raise _ViewMemberError("ViewUnionMember requires an exact DatasetRef")
        if type(grant) is not _AccessGrant:
            raise _ViewMemberError("ViewUnionMember requires an exact AccessGrant")
        if grant.dataset_ref != dataset_ref:
            raise _ViewMemberError(
                "ViewUnionMember DatasetRef does not exactly match its AccessGrant"
            )
        grant.require_matches(
            registered_manifest=grant.registered_manifest,
            split_policy_identity=grant.split_policy_identity,
        )
        if dataset_ref.role is _ViewRole.OOS and (
            grant.classification is not _AccessClassification.OOS_EXPLICIT
            or grant.audit_event_id is None
        ):
            raise _ViewMemberError(
                "OOS union membership requires explicit audited authorization"
            )
        object.__setattr__(self, "_dataset_ref", dataset_ref)
        object.__setattr__(
            self,
            "_split_policy_identity",
            grant.split_policy_identity,
        )

    @property
    def dataset_ref(self) -> _DatasetRef:
        """Exact hash-pinned identity of this union member."""
        return self._dataset_ref

    @property
    def role(self) -> _ViewRole:
        """Manifest-validated fitting/promotion role."""
        return self._dataset_ref.role


@_dataclass(frozen=True, slots=True, init=False)
class ViewUnion:
    """Validated ordered representation of adjacent fitting members.

    It contains no rows, performs no load, accepts no hand-written range, and
    has no aggregate identity or fingerprint in Slice 1E.
    """

    _members: tuple[ViewUnionMember, ...]

    def __init__(self, *, members: _Sequence[ViewUnionMember]) -> None:
        if not isinstance(members, _Sequence):
            raise _ViewMemberError("ViewUnion members must be a sequence")
        ordered = tuple(members)
        if not ordered:
            raise _ViewMemberError("ViewUnion requires at least one member")
        if any(type(member) is not ViewUnionMember for member in ordered):
            raise _ViewMemberError(
                "every ViewUnion member must be a validated ViewUnionMember"
            )

        roles = tuple(member.role for member in ordered)
        for role in roles:
            if not _union_role_allowed(role):
                raise _ViewMemberError(
                    f"role {role.value!r} is not a legal V1 fitting-union member"
                )
        if len(set(roles)) != len(roles):
            raise _ViewMemberError("ViewUnion contains a duplicate role")
        identities = tuple(member.dataset_ref.canonical_identity for member in ordered)
        if len(set(identities)) != len(identities):
            raise _ViewMemberError("ViewUnion contains a duplicate identity")

        policy_identity = ordered[0]._split_policy_identity
        if any(
            member._split_policy_identity != policy_identity
            for member in ordered[1:]
        ):
            raise _ViewMemberError(
                "ViewUnion members were authorized under different split-policy bytes"
            )
        if roles[0] is not _ViewRole.TRAIN:
            raise _ViewMemberError("a V1 fitting/promotion ViewUnion must begin with TRAIN")
        for left, right in _pairwise(roles):
            if _tier_one_role_position(right) != _tier_one_role_position(left) + 1:
                raise _ViewMemberError(
                    "ViewUnion members must be ordered adjacent split-policy roles"
                )
        object.__setattr__(self, "_members", ordered)

    @property
    def members(self) -> tuple[ViewUnionMember, ...]:
        """Ordered immutable sequence of individually pinned members."""
        return self._members


def open_bounded_view(
    *,
    loader: _MarketFrameLoader,
    grants: _Sequence[_AccessGrant],
    context_start: _NativeTimeKey,
    eval_start: _NativeTimeKey,
    eval_end: _NativeTimeKey,
    columns: _Sequence[str] | None = None,
    context_shortfall: ContextShortfall | None = None,
) -> BoundedView:
    """Compose exact authorized members into one bounded evaluation view.

    ``grants`` is ordered from earliest context member to the evaluation
    member; the final grant always denotes the evaluation member.  Every
    actual row load delegates to the supplied loader with that member's exact
    grant.  This function neither decodes rows nor reauthorizes access.
    """
    policy = _validated_loader_policy(loader)
    members = _validated_grants(grants, policy)
    _validate_bound_order(context_start, eval_start, eval_end)
    for label, bound in (
        ("context_start", context_start),
        ("eval_start", eval_start),
        ("eval_end", eval_end),
    ):
        _require_policy_axis(bound, label, policy)

    role_order = _policy_role_order(policy)
    _validate_member_chain(members, role_order)
    evaluation_manifest = members[-1].registered_manifest.manifest
    _validate_evaluation_bounds(
        evaluation_manifest,
        policy,
        eval_start=eval_start,
        eval_end=eval_end,
    )

    earliest = _native_key(evaluation_manifest.admitted_native_range.start, policy)
    for member in members[:-1]:
        candidate = _native_key(
            member.registered_manifest.manifest.admitted_native_range.start,
            policy,
        )
        if candidate < earliest:
            earliest = candidate
    context = _resolve_context(
        requested=context_start,
        earliest_available=earliest,
        supplied_shortfall=context_shortfall,
    )

    segments: list[BoundedSegment] = []
    eval_frame: _MarketFrame | None = None
    for index, member in enumerate(members):
        manifest = member.registered_manifest.manifest
        lower = max(
            context.resolved_context_start.value,
            manifest.admitted_native_range.start,
        )
        upper = min(eval_end.value, manifest.admitted_native_range.end)
        if lower > upper:
            continue
        frame = loader.load(
            member,
            columns=columns,
            lo=_native_key(lower, policy),
            hi=_native_key(upper, policy),
        )
        segment = BoundedSegment._from_bounded(frame)
        segments.append(segment)
        if index == len(members) - 1:
            eval_frame = frame.slice_by_keys(eval_start, eval_end)

    if eval_frame is None or eval_frame.num_rows == 0:
        raise _ViewBoundsError(
            "evaluation interval contains zero observed rows in the evaluation member"
        )

    return BoundedView._from_bounded(
        segments=tuple(segments),
        eval_frame=eval_frame,
        context=context,
        context_start=context_start,
        eval_start=eval_start,
        eval_end=eval_end,
    )


def _validated_loader_policy(loader: _MarketFrameLoader) -> _SplitPolicy:
    """Return the supplied loader's already-resolved exact policy."""
    if type(loader) is not _MarketFrameLoader:
        raise _ViewCompositionError(
            "open_bounded_view requires an exact configured MarketFrameLoader"
        )
    policy = loader._policy
    if type(policy) is not _SplitPolicy:
        raise _ViewCompositionError(
            "configured MarketFrameLoader carries no resolved SplitPolicy"
        )
    return policy


def _validated_grants(
    grants: _Sequence[_AccessGrant],
    policy: _SplitPolicy,
) -> tuple[_AccessGrant, ...]:
    """Copy and validate the exact, non-optional authorization sequence."""
    if not isinstance(grants, _Sequence):
        raise _ViewMemberError("grants must be a non-optional Sequence[AccessGrant]")
    members = tuple(grants)
    if not members:
        raise _ViewMemberError("at least one exact AccessGrant is required")
    for member in members:
        if type(member) is not _AccessGrant:
            raise _ViewMemberError("every bounded-view member requires an exact AccessGrant")
        if not _bounded_role_allowed(member.dataset_ref.role):
            raise _ViewMemberError(
                f"role {member.dataset_ref.role.value!r} is not a Tier-1 evaluation member"
            )
        if member.split_policy_identity != policy.identity:
            raise _ViewMemberError(
                "bounded-view member was authorized under different split-policy bytes"
            )
        member.require_matches(
            registered_manifest=member.registered_manifest,
            split_policy_identity=policy.identity,
        )
    return members


def _validate_bound_order(
    context_start: _NativeTimeKey,
    eval_start: _NativeTimeKey,
    eval_end: _NativeTimeKey,
) -> None:
    """Enforce the three-boundary order through NativeTimeKey semantics."""
    for label, bound in (
        ("context_start", context_start),
        ("eval_start", eval_start),
        ("eval_end", eval_end),
    ):
        if not isinstance(bound, _NativeTimeKey):
            raise _ViewBoundsError(f"{label} must be a NativeTimeKey")
    if context_start > eval_start:
        raise _ViewBoundsError("context_start must not follow eval_start")
    if eval_start > eval_end:
        raise _ViewBoundsError("eval_start must not follow eval_end")


def _require_policy_axis(
    bound: _NativeTimeKey,
    label: str,
    policy: _SplitPolicy,
) -> None:
    """Require one native boundary to use the resolved policy axis."""
    if bound.unit is not policy.time_unit:
        raise _TimeUnitMismatchError(
            f"{label} uses {bound.unit.value!r}; split policy uses "
            f"{policy.time_unit.value!r}"
        )
    if bound.clock_domain != policy.clock_domain:
        raise _ClockDomainError(
            f"{label} uses clock domain {bound.clock_domain.identifier!r}; "
            f"split policy uses {policy.clock_domain.identifier!r}"
        )


def _policy_role_order(policy: _SplitPolicy) -> tuple[_ViewRole, ...]:
    """Derive Tier-1 adjacency from this resolved policy's declared ranges."""
    roles = (
        _ViewRole.TRAIN,
        _ViewRole.VALIDATION,
        _ViewRole.OOS,
        _ViewRole.LOCKBOX,
    )
    return tuple(sorted(roles, key=lambda role: policy.range_for(role).start))


def _validate_member_chain(
    members: tuple[_AccessGrant, ...],
    role_order: tuple[_ViewRole, ...],
) -> None:
    """Require unique members consecutive in resolved policy-role order."""
    roles = tuple(member.dataset_ref.role for member in members)
    if len(set(roles)) != len(roles):
        raise _ViewMemberError("bounded-view members contain a duplicate role")
    identities = tuple(member.dataset_ref.canonical_identity for member in members)
    if len(set(identities)) != len(identities):
        raise _ViewMemberError("bounded-view members contain a duplicate identity")
    positions = {role: index for index, role in enumerate(role_order)}
    for left, right in _pairwise(roles):
        if positions[right] != positions[left] + 1:
            raise _ViewMemberError(
                "bounded-view members must be ordered consecutive split-policy roles"
            )


def _validate_evaluation_bounds(
    manifest: _NormalizedManifest,
    policy: _SplitPolicy,
    *,
    eval_start: _NativeTimeKey,
    eval_end: _NativeTimeKey,
) -> None:
    """Require evaluation bounds to remain in the final member's admission."""
    role = manifest.role
    admitted = manifest.admitted_native_range
    allowed = policy.range_for(role)
    if eval_start.value < allowed.start or eval_end.value > allowed.end:
        raise _ViewBoundsError(
            "evaluation bounds leave the evaluation role's split-policy range"
        )
    if eval_start.value < admitted.start or eval_end.value > admitted.end:
        raise _ViewBoundsError(
            "evaluation bounds leave the evaluation member's observed admitted range"
        )


def _resolve_context(
    *,
    requested: _NativeTimeKey,
    earliest_available: _NativeTimeKey,
    supplied_shortfall: ContextShortfall | None,
) -> ContextResolution:
    """Fail closed or retain exact accepted evidence for shorter context."""
    if requested >= earliest_available:
        if supplied_shortfall is not None:
            raise _ViewCompositionError(
                "context-shortfall evidence was supplied although full context is available"
            )
        return ContextResolution._resolved(
            requested_context_start=requested,
            resolved_context_start=requested,
            shortfall=None,
        )

    if supplied_shortfall is None:
        raise _InsufficientContextError(
            "requested context starts before the earliest authorized observed boundary: "
            f"requested {requested.value}, resolved {earliest_available.value}"
        )
    if (
        supplied_shortfall.requested_context_start != requested
        or supplied_shortfall.resolved_context_start != earliest_available
        or not supplied_shortfall.accepted
    ):
        raise _InsufficientContextError(
            "context-shortfall evidence must exactly match the requested and resolved "
            "boundaries and explicitly accept the shortfall"
        )
    return ContextResolution._resolved(
        requested_context_start=requested,
        resolved_context_start=earliest_available,
        shortfall=supplied_shortfall,
    )


def _native_key(value: int, policy: _SplitPolicy) -> _NativeTimeKey:
    """Tag one opaque integer with the resolved policy axis, without conversion."""
    return _NativeTimeKey(
        value=value,
        unit=policy.time_unit,
        clock_domain=policy.clock_domain,
    )


def _bounded_role_allowed(role: _ViewRole) -> bool:
    """Handle every ViewRole explicitly; future roles get no fallback."""
    match role:
        case _ViewRole.TRAIN:
            return True
        case _ViewRole.VALIDATION:
            return True
        case _ViewRole.OOS:
            return True
        case _ViewRole.LOCKBOX:
            return True
        case _ViewRole.RAW:
            return False
        case _ViewRole.CURATED:
            return False
        case _ViewRole.SNAPSHOT:
            return False


def _union_role_allowed(role: _ViewRole) -> bool:
    """Handle every ViewRole explicitly for V1 fitting/promotion unions."""
    match role:
        case _ViewRole.TRAIN:
            return True
        case _ViewRole.VALIDATION:
            return True
        case _ViewRole.OOS:
            return True
        case _ViewRole.LOCKBOX:
            return False
        case _ViewRole.RAW:
            return False
        case _ViewRole.CURATED:
            return False
        case _ViewRole.SNAPSHOT:
            return False


def _tier_one_role_position(role: _ViewRole) -> int:
    """Return the role position guaranteed by an exact resolved split policy.

    ``SplitPolicy`` admits only records whose four role ranges occur in this
    order.  Members retain that policy's exact identity, so this exhaustive
    mapping applies the resolved contract without embedding any date or key.
    """
    match role:
        case _ViewRole.TRAIN:
            return 0
        case _ViewRole.VALIDATION:
            return 1
        case _ViewRole.OOS:
            return 2
        case _ViewRole.LOCKBOX:
            return 3
        case _ViewRole.RAW:
            raise _ViewMemberError("RAW has no Tier-1 policy position")
        case _ViewRole.CURATED:
            raise _ViewMemberError("CURATED has no Tier-1 policy position")
        case _ViewRole.SNAPSHOT:
            raise _ViewMemberError("SNAPSHOT has no Tier-1 policy position")
