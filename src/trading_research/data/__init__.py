"""Market data catalog, authorization, audit, and integrity-gated loading.

The catalog remains metadata-only, the gate turns one exact reference into a
policy-bound grant, and :class:`MarketFrameLoader` remains the sole public
route from that grant to decoded market rows. ``open_bounded_view`` is the
single composition route over those verified frames. Physical adapters and
PyArrow stay private; callers receive only bounded compact ``MarketFrame``
contracts.
"""

from trading_research.data.audit import JsonlAuditWriter
from trading_research.data.gaps import GapIndex
from trading_research.data.gating import (
    AccessClassification,
    AccessContext,
    AccessGrant,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    SplitPolicy,
    SplitPolicyIdentity,
    authorize_access,
    classify_access,
    read_split_policy,
)
from trading_research.data.integrity import MarketFrameLoader
from trading_research.data.registry import (
    DatasetRegistry,
    PartitionSourceLocation,
    RegisteredManifest,
)
from trading_research.data.views import (
    BoundedSegment,
    BoundedView,
    ContextResolution,
    ContextShortfall,
    ViewUnion,
    ViewUnionMember,
    open_bounded_view,
)

__all__ = [
    "AccessClassification",
    "AccessContext",
    "AccessGrant",
    "AccessReason",
    "AccessRequest",
    "BoundedSegment",
    "BoundedView",
    "ContextResolution",
    "ContextShortfall",
    "DatasetRegistry",
    "GapIndex",
    "GatedUnlock",
    "JsonlAuditWriter",
    "MarketFrameLoader",
    "PartitionSourceLocation",
    "RegisteredManifest",
    "SplitPolicy",
    "SplitPolicyIdentity",
    "ViewUnion",
    "ViewUnionMember",
    "authorize_access",
    "classify_access",
    "open_bounded_view",
    "read_split_policy",
]
