"""What a dataset or view *is*, independent of where its bytes happen to sit.

Research results are only reproducible if the data they were computed from can
be named unambiguously years later. A filesystem path cannot do that: paths
move between machines, get restructured, and say nothing about whether the
bytes changed. A dataset identifier alone cannot either, because "the curated
dataset" silently means something different after any correction.

So canonical identity is the pair ``(dataset_id, manifest_sha256)``: what it
is called, and exactly which manifest was in force. Any change to the admitted
data changes the manifest, which changes the hash, which yields a different
identity — which is the point. Corrections never mutate an identity; they
create a new one.

:class:`DatasetRef` additionally carries the view ``role``, because callers
need to know whether they are holding TRAIN or LOCKBOX. Role is a validated
constraint, not a third component of identity: the role is already recorded
inside the manifest, so changing it already changes ``manifest_sha256``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from trading_research.core.errors import ContractViolationError

__all__ = [
    "DatasetIdentity",
    "DatasetRef",
    "ViewRole",
]

# Dataset identifiers appear in manifests, run records and audit events. They
# must be stable, readable, and incapable of being mistaken for a location:
# anything containing a path separator is refused, so a path can never be
# smuggled in where an identity is expected.
_DATASET_ID = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
_MAX_DATASET_ID_LENGTH = 128

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class ViewRole(StrEnum):
    """What part a dataset or view plays in the research process.

    Roles are declared by a manifest and carried on references to it. They
    govern discovery and access, never arithmetic: nothing about a role
    changes how data is read, only whether it may be.
    """

    RAW = "raw"
    CURATED = "curated"
    SNAPSHOT = "snapshot"
    TRAIN = "train"
    VALIDATION = "validation"
    OOS = "oos"
    LOCKBOX = "lockbox"


def _validated_dataset_id(dataset_id: str) -> str:
    """Return ``dataset_id`` unchanged, or raise if it cannot be an identity.

    Raises:
        ContractViolationError: If it is not a string, is empty, is over-long,
            or contains whitespace, a path separator, or other characters
            outside the permitted set.
    """
    if not isinstance(dataset_id, str):
        raise ContractViolationError(
            f"dataset_id must be a string, got {type(dataset_id).__name__}"
        )
    if not dataset_id:
        raise ContractViolationError("dataset_id must not be empty")
    if len(dataset_id) > _MAX_DATASET_ID_LENGTH:
        raise ContractViolationError(
            f"dataset_id exceeds {_MAX_DATASET_ID_LENGTH} characters: {len(dataset_id)}"
        )
    if _DATASET_ID.match(dataset_id) is None:
        raise ContractViolationError(
            f"dataset_id {dataset_id!r} is not a valid identifier; expected alphanumeric "
            "ends with '.', '_' or '-' inside, and no whitespace or path separators"
        )
    return dataset_id


def _normalized_manifest_sha256(manifest_sha256: str) -> str:
    """Return the digest lower-cased, or raise if it is not a SHA-256 digest.

    Casing is normalized rather than rejected: ``hashlib`` emits lower-case
    hex, but digests copied from other tooling may arrive upper-cased, and the
    two spellings denote the same manifest. Normalizing on the way in keeps
    identity comparison total — two references to one manifest can never
    compare unequal because of how a digest was typed.

    Raises:
        ContractViolationError: If it is not a string, or is not exactly 64
            hexadecimal characters.
    """
    if not isinstance(manifest_sha256, str):
        raise ContractViolationError(
            f"manifest_sha256 must be a string, got {type(manifest_sha256).__name__}"
        )
    if _SHA256_HEX.match(manifest_sha256) is None:
        raise ContractViolationError(
            f"manifest_sha256 {manifest_sha256!r} is not a SHA-256 digest; "
            "expected 64 hexadecimal characters"
        )
    return manifest_sha256.lower()


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    """The whole of canonical dataset/view identity, and nothing else.

    This type exists so that identity can be *passed around* without carrying
    anything that is not identity. Anything computing a fingerprint or keying
    a cache consumes this, which makes it structurally impossible for a role —
    or a path, or a mutable flag — to leak into a research identity by
    accident. Adding a field here is an architectural change.
    """

    dataset_id: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        """Validate the identifier and normalize the digest's casing.

        Raises:
            ContractViolationError: If either component is malformed.
        """
        object.__setattr__(self, "dataset_id", _validated_dataset_id(self.dataset_id))
        object.__setattr__(
            self, "manifest_sha256", _normalized_manifest_sha256(self.manifest_sha256)
        )


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """A reference to one dataset or view: its identity plus its declared role.

    Equality here is ordinary Python value equality over **all three** fields,
    which is deliberately broader than :attr:`canonical_identity`. Two refs
    naming one manifest under different roles are therefore unequal objects
    that share one canonical identity — and at most one of them is correct,
    because a manifest declares exactly one role. Collapsing them under ``==``
    would hide that disagreement; keeping them distinct surfaces it.

    Checking a ref's role against the role its manifest declares requires
    reading the manifest, so that check belongs to the manifest layer and is
    deliberately absent here.

    Attributes:
        dataset_id: Stable name of the dataset or view.
        manifest_sha256: Digest of the manifest in force, lower-cased.
        role: The role this reference claims. Metadata and a constraint —
            never a component of identity.
    """

    dataset_id: str
    manifest_sha256: str
    role: ViewRole

    def __post_init__(self) -> None:
        """Validate all three fields and normalize the digest's casing.

        Raises:
            ContractViolationError: If any field is malformed.
        """
        object.__setattr__(self, "dataset_id", _validated_dataset_id(self.dataset_id))
        object.__setattr__(
            self, "manifest_sha256", _normalized_manifest_sha256(self.manifest_sha256)
        )
        if not isinstance(self.role, ViewRole):
            raise ContractViolationError(
                f"DatasetRef.role must be a ViewRole, got {type(self.role).__name__}"
            )

    @property
    def canonical_identity(self) -> DatasetIdentity:
        """The pair that identifies this data for research purposes.

        Role is absent by construction: see the class docstring for why that
        is a correctness property rather than an omission.
        """
        return DatasetIdentity(
            dataset_id=self.dataset_id, manifest_sha256=self.manifest_sha256
        )
