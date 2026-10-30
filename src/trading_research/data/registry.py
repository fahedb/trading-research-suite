"""Dataset catalog for manifest-backed market data.

The catalog is **metadata only**. It answers what exists, what a manifest
declares, and whether a reference is honest about what it names. It never
opens a partition file, never returns rows, and never decides that a caller
may read gated data — resolving an identity successfully is not permission to
read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from trading_research.core.errors import (
    DatasetNotFoundError,
    DatasetRefMismatchError,
    DuplicateDatasetIdError,
    ManifestLocationError,
    ParentResolutionError,
    RegistryRootError,
)
from trading_research.data.manifests import MANIFEST_FILENAME, ManifestFormat, read_manifest

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from trading_research.core.identity import DatasetIdentity, DatasetRef, ViewRole
    from trading_research.data.manifests import NormalizedManifest

__all__ = [
    "DatasetRegistry",
    "PartitionSourceLocation",
    "RegisteredManifest",
]


@dataclass(frozen=True, slots=True)
class RegisteredManifest:
    """A normalized manifest together with where this machine found it.

    The split is the point: :attr:`manifest` is what the dataset *is* and
    travels with the research record; :attr:`manifest_path` and :attr:`root`
    are where a particular machine happened to keep it and travel nowhere.
    Move the bytes to another registered root and the identity is unchanged.

    Attributes:
        manifest: The normalized metadata.
        manifest_path: Absolute path this manifest was read from.
        root: The configured root it was discovered under.
        resolved_parent: Identity of the parent as resolved by the registry
            against actually-discovered manifests — distinct from the parent
            the source manifest merely *claims*, which stays on
            ``manifest.declared_parent``.
    """

    manifest: NormalizedManifest
    manifest_path: Path
    root: Path
    resolved_parent: DatasetIdentity | None = None

    @property
    def identity(self) -> DatasetIdentity:
        """Canonical identity of this manifest. Contains no path."""
        return self.manifest.identity


@dataclass(frozen=True, slots=True)
class PartitionSourceLocation:
    """Where one exact partition-source identity lives on this machine.

    Deliberately carries no source manifest or partition metadata.  A target
    manifest is the sole authority for filenames, digests, admitted spans, row
    counts and optional byte sizes; the source identity contributes only the
    directory under which those target-declared bare filenames are resolved.
    """

    identity: DatasetIdentity
    directory: Path


class DatasetRegistry:
    """The catalog of manifests discovered under explicitly configured roots.

    Roots are always passed in. The registry never consults the working
    directory, an environment variable, or a machine-specific config file:
    library code that guesses where data lives is library code that reads
    different data depending on who invoked it.
    """

    def __init__(self, entries: Sequence[RegisteredManifest]) -> None:
        self._entries = tuple(entries)
        self._by_id = {entry.manifest.dataset_id: entry for entry in self._entries}

    @classmethod
    def discover(cls, roots: Iterable[Path]) -> DatasetRegistry:
        """Build a registry by discovering manifests under ``roots``.

        Searches each root recursively for files named exactly
        ``manifest.json``. Discovering *metadata* by name is legal; nothing
        here globs for partition files, because a manifest — not a directory
        listing — decides which data is admitted.

        Args:
            roots: Existing directories to search. Order is irrelevant:
                results are sorted, and a duplicate id is fatal regardless of
                which root supplied it.

        Returns:
            A registry over every manifest found.

        Raises:
            RegistryRootError: If ``roots`` is empty, or a root is missing or
                is not a directory.
            DuplicateDatasetIdError: If one id appears at two locations.
            ManifestLocationError: If a manifest declares a path escaping
                every configured root.
            ParentResolutionError: If a declared parent or partition source
                cannot be resolved against the discovered manifests.
        """
        resolved_roots = cls._validated_roots(roots)
        entries = cls._discover_entries(resolved_roots)
        cls._reject_duplicate_ids(entries)
        for entry in entries:
            cls._reject_escaping_paths(entry, resolved_roots)
        cls._validate_partition_sources(entries)
        return cls(cls._with_resolved_parents(entries))

    @staticmethod
    def _validated_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
        """Canonicalize the configured roots.

        Raises:
            RegistryRootError: If no root was supplied, or one is missing or
                is not a directory. Roots are never created here: a research
                platform silently creating an empty data root would report
                "nothing found" instead of "you pointed me somewhere wrong".
        """
        resolved: list[Path] = []
        for root in roots:
            candidate = root.expanduser().resolve()
            if not candidate.exists():
                raise RegistryRootError(f"registry root does not exist: {candidate}")
            if not candidate.is_dir():
                raise RegistryRootError(f"registry root is not a directory: {candidate}")
            resolved.append(candidate)
        if not resolved:
            raise RegistryRootError("at least one registry root must be configured")
        return tuple(resolved)

    @staticmethod
    def _discover_entries(roots: Sequence[Path]) -> tuple[RegisteredManifest, ...]:
        """Read every manifest under ``roots`` in a deterministic order."""
        entries: list[RegisteredManifest] = []
        for root in roots:
            for manifest_path in sorted(root.rglob(MANIFEST_FILENAME)):
                entries.append(
                    RegisteredManifest(
                        manifest=read_manifest(manifest_path),
                        manifest_path=manifest_path,
                        root=root,
                    )
                )
        return tuple(entries)

    @staticmethod
    def _reject_duplicate_ids(entries: Sequence[RegisteredManifest]) -> None:
        """Refuse a catalog where one id names two manifests.

        Fatal even when the bytes are identical: tolerating it would make
        which copy a run consumed depend on scan order.

        Raises:
            DuplicateDatasetIdError: If any id appears more than once.
        """
        seen: dict[str, Path] = {}
        for entry in entries:
            dataset_id = entry.manifest.dataset_id
            first = seen.get(dataset_id)
            if first is not None:
                raise DuplicateDatasetIdError(
                    f"dataset id {dataset_id!r} found at two locations: "
                    f"{first} and {entry.manifest_path}"
                )
            seen[dataset_id] = entry.manifest_path

    @staticmethod
    def _reject_escaping_paths(
        entry: RegisteredManifest, roots: Sequence[Path]
    ) -> None:
        """Refuse manifest-declared paths that leave every configured root.

        The only path a manifest can still declare is the legacy curated
        format's source-declared parent location — v1.1 lineage is
        identity-only and partitions everywhere carry bare filenames, which
        cannot escape a directory that is itself inside a root. Resolution
        only: no file is opened and no existence is checked.

        Raises:
            ManifestLocationError: If a declared parent path resolves
                outside all configured roots.
        """
        parent = entry.manifest.declared_parent
        if parent is None or parent.relative_path is None:
            return
        target = (entry.manifest_path.parent / parent.relative_path).resolve()
        if not any(target == root or root in target.parents for root in roots):
            raise ManifestLocationError(
                f"{entry.manifest.dataset_id}: declared parent path "
                f"{parent.relative_path!r} resolves to {target}, outside every configured "
                f"root {[str(r) for r in roots]}"
            )

    @staticmethod
    def _validate_partition_sources(entries: Sequence[RegisteredManifest]) -> None:
        """Require every pinned partition source to resolve through the catalog.

        Resolution is by identity, never by path: the source's ``dataset_id``
        must be discovered under the configured roots and its manifest must
        hash to the pinned digest. Where the files then live on this machine
        follows from where the registry found that manifest — which is what
        makes relocating a dataset a registry-configuration event instead of
        a manifest-regeneration event.

        Raises:
            ParentResolutionError: If a pinned partition source is absent
                from the catalog or its manifest digest disagrees with the
                pin.
        """
        by_id = {entry.manifest.dataset_id: entry for entry in entries}
        for entry in entries:
            source = entry.manifest.partition_source
            if source is None:
                continue
            source_entry = by_id.get(source.dataset_id)
            if source_entry is None:
                raise ParentResolutionError(
                    f"{entry.manifest.dataset_id} pins partition source "
                    f"{source.dataset_id!r}, which no configured root provides"
                )
            if source.manifest_sha256 != source_entry.identity.manifest_sha256:
                raise ParentResolutionError(
                    f"{entry.manifest.dataset_id} pins partition source "
                    f"{source.dataset_id!r} at manifest {source.manifest_sha256}, but the "
                    f"discovered manifest hashes to {source_entry.identity.manifest_sha256}"
                )

    @staticmethod
    def _with_resolved_parents(
        entries: Sequence[RegisteredManifest],
    ) -> tuple[RegisteredManifest, ...]:
        """Attach each manifest's parent identity, resolved against the catalog.

        The source manifest names its parent; the registry decides what that
        name currently resolves to. Where a manifest also pins the parent's
        digest, the pin is checked — a pinned parent that no longer matches is
        a different parent, not a detail.

        Raises:
            ParentResolutionError: If a declared parent is absent from the
                catalog, or its digest contradicts a pinned one.
        """
        by_id = {entry.manifest.dataset_id: entry for entry in entries}
        resolved: list[RegisteredManifest] = []
        for entry in entries:
            declared = entry.manifest.declared_parent
            if declared is None:
                resolved.append(entry)
                continue
            parent_entry = by_id.get(declared.dataset_id)
            if parent_entry is None:
                raise ParentResolutionError(
                    f"{entry.manifest.dataset_id} declares parent "
                    f"{declared.dataset_id!r}, which no configured root provides"
                )
            parent_identity = parent_entry.identity
            if (
                declared.manifest_sha256 is not None
                and declared.manifest_sha256 != parent_identity.manifest_sha256
            ):
                raise ParentResolutionError(
                    f"{entry.manifest.dataset_id} pins parent "
                    f"{declared.dataset_id!r} at manifest {declared.manifest_sha256}, but the "
                    f"discovered parent hashes to {parent_identity.manifest_sha256}"
                )
            resolved.append(
                RegisteredManifest(
                    manifest=entry.manifest,
                    manifest_path=entry.manifest_path,
                    root=entry.root,
                    resolved_parent=parent_identity,
                )
            )
        return tuple(resolved)

    # ------------------------------------------------------------- queries

    def resolve(self, ref: DatasetRef) -> RegisteredManifest:
        """Resolve an exact reference to its manifest metadata.

        Requires agreement on all three of id, manifest digest and role. The
        digest is what makes this a pin rather than a name lookup: if the
        bytes changed, this is not the data the reference was written for.

        Returning successfully is **not** authorization. Whether the caller
        may read a gated view is a separate decision, made elsewhere.

        Raises:
            DatasetNotFoundError: If no manifest carries that id.
            DatasetRefMismatchError: If the digest or the role disagrees.
        """
        entry = self._by_id.get(ref.dataset_id)
        if entry is None:
            raise DatasetNotFoundError(
                f"no manifest with dataset id {ref.dataset_id!r}; "
                f"known: {sorted(self._by_id)}"
            )
        manifest = entry.manifest
        if manifest.manifest_sha256 != ref.manifest_sha256:
            raise DatasetRefMismatchError(
                f"{ref.dataset_id}: reference pins manifest {ref.manifest_sha256}, but the "
                f"discovered manifest hashes to {manifest.manifest_sha256}"
            )
        if manifest.role is not ref.role:
            raise DatasetRefMismatchError(
                f"{ref.dataset_id}: reference claims role {ref.role.value!r}, but the manifest "
                f"declares {manifest.role.value!r}"
            )
        return entry

    def resolve_partition_source_location(
        self, target: RegisteredManifest
    ) -> PartitionSourceLocation:
        """Resolve only the local directory backing ``target``'s partitions.

        The target must name an exact manifest already present in this
        registry.  Generated v1.1 views resolve their pinned
        ``partition_source``; a legacy curated view resolves its registered
        parent; and a legacy raw manifest owns the files beside itself.

        The returned value intentionally exposes no source-manifest metadata.
        In particular, callers must use the *target* manifest for every
        partition filename, digest, admitted span, row count and optional byte
        size.  This method constructs a location only: it does not inspect a
        source partition list, check that a target filename exists, or open any
        file.

        Raises:
            ParentResolutionError: If the target is not this registry's exact
                manifest, has no resolvable partition source, or that source's
                exact identity is no longer present.
        """
        registered_target = self._by_id.get(target.manifest.dataset_id)
        if registered_target is None or registered_target.identity != target.identity:
            raise ParentResolutionError(
                f"partition source target {target.identity.dataset_id!r} is not the exact "
                "manifest registered in this catalog"
            )

        manifest = registered_target.manifest
        source_identity = manifest.partition_source
        if source_identity is None:
            if manifest.source_format is ManifestFormat.MT5_RAW_V1:
                source_identity = registered_target.identity
            elif manifest.source_format is ManifestFormat.CURATED_VIEW_V1:
                source_identity = registered_target.resolved_parent

        if source_identity is None:
            raise ParentResolutionError(
                f"{manifest.dataset_id}: manifest has no resolvable partition source"
            )

        source_entry = self._by_id.get(source_identity.dataset_id)
        if source_entry is None:
            raise ParentResolutionError(
                f"{manifest.dataset_id}: partition source {source_identity.dataset_id!r} "
                "is absent from this registry"
            )
        if source_entry.identity != source_identity:
            raise ParentResolutionError(
                f"{manifest.dataset_id}: partition source {source_identity.dataset_id!r} "
                f"pins manifest {source_identity.manifest_sha256}, but this registry has "
                f"{source_entry.identity.manifest_sha256}"
            )

        return PartitionSourceLocation(
            identity=source_entry.identity,
            directory=source_entry.manifest_path.parent,
        )

    def default_views(self) -> tuple[RegisteredManifest, ...]:
        """Manifests ordinary discovery may return, sorted by id.

        Only manifests declaring ``discovery_default``. Everything else must
        be named explicitly, which is what keeps gated data from arriving by
        accident.
        """
        return tuple(
            sorted(
                (e for e in self._entries if e.manifest.discovery_default),
                key=lambda e: e.manifest.dataset_id,
            )
        )

    def resolve_role(self, role: ViewRole) -> RegisteredManifest:
        """Resolve the single default-discoverable manifest for ``role``.

        Raises:
            DatasetNotFoundError: If no default-discoverable manifest has
                that role.
            DuplicateDatasetIdError: If more than one does — which of two
                candidate TRAIN views a run used must never be implicit.
        """
        candidates = [e for e in self.default_views() if e.manifest.role is role]
        if not candidates:
            raise DatasetNotFoundError(
                f"no default-discoverable manifest with role {role.value!r}"
            )
        if len(candidates) > 1:
            paths = [str(c.manifest_path) for c in candidates]
            raise DuplicateDatasetIdError(
                f"role {role.value!r} is ambiguous across {paths}"
            )
        return candidates[0]

    def all_manifests_for_diagnostics(self) -> tuple[RegisteredManifest, ...]:
        """Every discovered manifest, including gated ones, sorted by id.

        Deliberately verbose in name. This is an administrative listing for
        inspecting what a machine holds; it is **not** discovery, and its
        output must never be fed into a resolution path that ordinary
        discovery would have filtered.
        """
        return tuple(sorted(self._entries, key=lambda e: e.manifest.dataset_id))
