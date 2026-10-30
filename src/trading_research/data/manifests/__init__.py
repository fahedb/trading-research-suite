"""Reading dataset manifests, and the one shape they are normalized into.

Metadata only. Nothing in this package opens a partition file, decodes a row,
or decides whether a caller may read anything.
"""

from trading_research.data.manifests.models import (
    ColumnPurpose,
    DeclaredParent,
    ManifestColumn,
    ManifestFormat,
    NativeKeyRange,
    NormalizedManifest,
    PartitionMetadata,
    SplitPolicyRef,
)
from trading_research.data.manifests.readers import MANIFEST_FILENAME, read_manifest
from trading_research.data.manifests.v11_schema import ViewManifestV11

__all__ = [
    "MANIFEST_FILENAME",
    "ColumnPurpose",
    "DeclaredParent",
    "ManifestColumn",
    "ManifestFormat",
    "NativeKeyRange",
    "NormalizedManifest",
    "PartitionMetadata",
    "SplitPolicyRef",
    "ViewManifestV11",
    "read_manifest",
]
