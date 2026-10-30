"""What the dataset catalog discovers, refuses, and resolves.

The catalog is metadata only. A test that obtained rows, a file handle or a
frame from any method here would be testing something Slice 1B must not be
able to do.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from tests.manifest_fixtures import (
    curated_manifest,
    raw_manifest,
    v11_manifest,
    write_manifest,
)
from trading_research.core import (
    DatasetIdentity,
    DatasetNotFoundError,
    DatasetRef,
    DatasetRefMismatchError,
    DuplicateDatasetIdError,
    ManifestLocationError,
    ParentResolutionError,
    RegistryRootError,
    ViewRole,
)
from trading_research.data import DatasetRegistry
from trading_research.data.manifests import read_manifest

DATASET_ROOT = Path("/path/to/research-data")
PARENT_ID = "SYNTH_PARENT_V1"

real_dataset = pytest.mark.skipif(
    not (DATASET_ROOT / "XAUUSD_S_M1_MT5_V1" / "manifest.json").exists(),
    reason="real dataset root not present on this machine",
)


def _build_root(tmp_path: Path, *derived: dict[str, Any], name: str = "root") -> Path:
    """Write a raw parent plus derived v1.1 views into one root.

    The parent is written first so its digest can be pinned into each derived
    manifest, mirroring how real generation works: a view can only pin
    lineage that already exists. Both pins — ``parent`` and
    ``partition_source`` — are filled wherever they name the parent id.
    """
    root = tmp_path / name
    parent_path = write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
    parent_digest = read_manifest(parent_path).manifest_sha256
    for index, document in enumerate(derived):
        for lineage_field in ("parent", "partition_source"):
            lineage = document.get(lineage_field)
            if isinstance(lineage, dict) and lineage.get("dataset_id") == PARENT_ID:
                lineage["manifest_sha256"] = parent_digest
        write_manifest(root / f"view{index}", document)
    return root


def _digest_of(root: Path, subdirectory: str) -> str:
    """Return the manifest digest of one written fixture."""
    return read_manifest(root / subdirectory / "manifest.json").manifest_sha256


class TestRoots:
    def test_at_least_one_root_is_required(self) -> None:
        with pytest.raises(RegistryRootError, match="at least one registry root"):
            DatasetRegistry.discover([])

    def test_a_missing_root_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryRootError, match="does not exist"):
            DatasetRegistry.discover([tmp_path / "absent"])

    def test_a_file_as_root_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "afile"
        target.write_text("x")
        with pytest.raises(RegistryRootError, match="not a directory"):
            DatasetRegistry.discover([target])

    def test_a_missing_root_is_never_created(self, tmp_path: Path) -> None:
        """Creating it would turn a misconfiguration into a silent empty catalog."""
        absent = tmp_path / "absent"
        with pytest.raises(RegistryRootError):
            DatasetRegistry.discover([absent])
        assert not absent.exists()


class TestDiscovery:
    def test_manifests_are_discovered_under_a_configured_root(self, tmp_path: Path) -> None:
        root = _build_root(tmp_path, v11_manifest())
        registry = DatasetRegistry.discover([root])
        assert {e.manifest.dataset_id for e in registry.all_manifests_for_diagnostics()} == {
            PARENT_ID,
            "SYNTH_TRAIN_V1",
        }

    def test_only_files_named_manifest_json_are_read(self, tmp_path: Path) -> None:
        """Metadata is found by name; data is never found by scanning."""
        root = _build_root(tmp_path, v11_manifest())
        (root / "view0" / "not-a-manifest.json").write_text("{invalid")
        (root / "view0" / "manifest.json.bak").write_text("{invalid")
        (root / "view0" / "p0.parquet").write_bytes(b"PAR1-not-really")
        assert len(DatasetRegistry.discover([root]).all_manifests_for_diagnostics()) == 2

    def test_discovery_spans_multiple_roots(self, tmp_path: Path) -> None:
        first = _build_root(tmp_path, name="a")
        second = tmp_path / "b"
        parent_digest = _digest_of(first, "parent")
        document = v11_manifest(parent_sha256=parent_digest, source_sha256=parent_digest)
        write_manifest(second / "view", document)
        registry = DatasetRegistry.discover([first, second])
        assert len(registry.all_manifests_for_diagnostics()) == 2

    def test_duplicate_ids_are_fatal_and_name_both_locations(self, tmp_path: Path) -> None:
        """Identical bytes included: otherwise which copy ran depends on scan order."""
        first = _build_root(tmp_path, name="a")
        second = _build_root(tmp_path, name="b")
        with pytest.raises(DuplicateDatasetIdError) as excinfo:
            DatasetRegistry.discover([first, second])
        message = str(excinfo.value)
        assert str(first / "parent" / "manifest.json") in message
        assert str(second / "parent" / "manifest.json") in message


class TestDefaultDiscovery:
    def test_only_default_discoverable_manifests_are_returned(self, tmp_path: Path) -> None:
        root = _build_root(
            tmp_path,
            v11_manifest(view_id="SYNTH_TRAIN_V1", role="train", discovery_default=True),
            v11_manifest(view_id="SYNTH_VAL_V1", role="validation", discovery_default=True),
            v11_manifest(view_id="SYNTH_OOS_V1", role="oos", discovery_default=False),
            v11_manifest(view_id="SYNTH_LOCKBOX_V1", role="lockbox", discovery_default=False),
        )
        registry = DatasetRegistry.discover([root])
        assert {e.manifest.dataset_id for e in registry.default_views()} == {
            "SYNTH_TRAIN_V1",
            "SYNTH_VAL_V1",
        }

    @pytest.mark.parametrize("role", ["oos", "snapshot", "lockbox", "raw", "curated"])
    def test_gated_roles_are_never_default_discoverable(
        self, tmp_path: Path, role: str
    ) -> None:
        root = _build_root(
            tmp_path,
            v11_manifest(view_id="SYNTH_GATED_V1", role=role, discovery_default=False),
        )
        registry = DatasetRegistry.discover([root])
        assert registry.default_views() == ()
        with pytest.raises(DatasetNotFoundError):
            registry.resolve_role(ViewRole(role))

    def test_a_role_resolves_to_its_single_default_view(self, tmp_path: Path) -> None:
        root = _build_root(tmp_path, v11_manifest(view_id="SYNTH_TRAIN_V1", role="train"))
        registry = DatasetRegistry.discover([root])
        assert registry.resolve_role(ViewRole.TRAIN).manifest.dataset_id == "SYNTH_TRAIN_V1"

    def test_an_ambiguous_role_is_refused(self, tmp_path: Path) -> None:
        """Which of two TRAIN views a run used must never be implicit."""
        root = _build_root(
            tmp_path,
            v11_manifest(view_id="SYNTH_TRAIN_A", role="train"),
            v11_manifest(view_id="SYNTH_TRAIN_B", role="train"),
        )
        registry = DatasetRegistry.discover([root])
        with pytest.raises(DuplicateDatasetIdError, match="ambiguous"):
            registry.resolve_role(ViewRole.TRAIN)

    def test_diagnostics_listing_shows_what_discovery_hides(self, tmp_path: Path) -> None:
        """Administrative visibility is not access, and must stay separate from discovery."""
        root = _build_root(
            tmp_path,
            v11_manifest(view_id="SYNTH_LOCKBOX_V1", role="lockbox", discovery_default=False),
        )
        registry = DatasetRegistry.discover([root])
        assert registry.default_views() == ()
        assert len(registry.all_manifests_for_diagnostics()) == 2


class TestDatasetRefResolution:
    def _registry_and_ref(self, tmp_path: Path) -> tuple[DatasetRegistry, DatasetRef]:
        root = _build_root(tmp_path, v11_manifest())
        registry = DatasetRegistry.discover([root])
        return registry, DatasetRef(
            dataset_id="SYNTH_TRAIN_V1",
            manifest_sha256=_digest_of(root, "view0"),
            role=ViewRole.TRAIN,
        )

    def test_an_exact_reference_resolves_to_metadata(self, tmp_path: Path) -> None:
        registry, ref = self._registry_and_ref(tmp_path)
        entry = registry.resolve(ref)
        assert entry.manifest.dataset_id == ref.dataset_id
        assert entry.identity == DatasetIdentity(
            dataset_id=ref.dataset_id, manifest_sha256=ref.manifest_sha256
        )

    def test_an_unknown_id_is_refused(self, tmp_path: Path) -> None:
        registry, ref = self._registry_and_ref(tmp_path)
        unknown = DatasetRef(
            dataset_id="NOT_PRESENT_V1",
            manifest_sha256=ref.manifest_sha256,
            role=ViewRole.TRAIN,
        )
        with pytest.raises(DatasetNotFoundError, match="NOT_PRESENT_V1"):
            registry.resolve(unknown)

    def test_a_hash_mismatch_is_an_identity_mismatch(self, tmp_path: Path) -> None:
        """If the bytes changed, this is not the data the reference was written for."""
        registry, ref = self._registry_and_ref(tmp_path)
        stale = DatasetRef(
            dataset_id=ref.dataset_id, manifest_sha256="c" * 64, role=ViewRole.TRAIN
        )
        with pytest.raises(DatasetRefMismatchError, match="pins manifest"):
            registry.resolve(stale)

    def test_a_role_mismatch_is_a_contract_error(self, tmp_path: Path) -> None:
        registry, ref = self._registry_and_ref(tmp_path)
        wrong_role = DatasetRef(
            dataset_id=ref.dataset_id,
            manifest_sha256=ref.manifest_sha256,
            role=ViewRole.LOCKBOX,
        )
        with pytest.raises(DatasetRefMismatchError, match="claims role 'lockbox'"):
            registry.resolve(wrong_role)

    def test_resolution_returns_no_rows_handle_or_frame(self, tmp_path: Path) -> None:
        registry, ref = self._registry_and_ref(tmp_path)
        entry = registry.resolve(ref)
        for absent in ("read", "load", "rows", "frame", "open", "to_frame"):
            assert not hasattr(entry, absent), f"{absent} would make 1B a loader"

    def test_resolving_a_gated_identity_is_not_authorization(self, tmp_path: Path) -> None:
        """Metadata resolution succeeds; permission is a separate decision in 1C."""
        root = _build_root(
            tmp_path,
            v11_manifest(view_id="SYNTH_LOCKBOX_V1", role="lockbox", discovery_default=False),
        )
        registry = DatasetRegistry.discover([root])
        entry = registry.resolve(
            DatasetRef(
                dataset_id="SYNTH_LOCKBOX_V1",
                manifest_sha256=_digest_of(root, "view0"),
                role=ViewRole.LOCKBOX,
            )
        )
        assert entry.manifest.role is ViewRole.LOCKBOX
        assert not hasattr(entry, "authorized")


class TestIdentityIsNotLocation:
    def test_identity_carries_no_path(self, tmp_path: Path) -> None:
        root = _build_root(tmp_path, v11_manifest())
        entry = DatasetRegistry.discover([root]).resolve_role(ViewRole.TRAIN)
        assert {f.name for f in fields(DatasetIdentity)} == {"dataset_id", "manifest_sha256"}
        assert str(tmp_path) not in repr(entry.identity)

    def test_moving_a_manifest_preserves_canonical_identity(self, tmp_path: Path) -> None:
        here = _build_root(tmp_path, v11_manifest(), name="a")
        there = _build_root(tmp_path, v11_manifest(), name="b")
        # Same bytes, different depth under a different root.
        (there / "view0").rename(there / "moved")
        first = DatasetRegistry.discover([here]).resolve_role(ViewRole.TRAIN)
        second = DatasetRegistry.discover([there]).resolve_role(ViewRole.TRAIN)
        assert first.identity == second.identity
        assert first.manifest_path != second.manifest_path

    def test_a_declared_parent_path_escaping_every_root_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The last declarable path must stay inside the configured roots.

        Only the legacy curated format still declares a parent location.
        """
        root = tmp_path / "root"
        write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
        write_manifest(
            root / "curated",
            curated_manifest(parent_relative_path="../../outside/parent"),
        )
        with pytest.raises(ManifestLocationError, match="outside every configured root"):
            DatasetRegistry.discover([root])

    def test_a_declared_parent_path_inside_the_root_is_accepted(
        self, tmp_path: Path
    ) -> None:
        """The real curated view points at a sibling directory; that is legal."""
        root = tmp_path / "root"
        write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
        write_manifest(root / "curated", curated_manifest(parent_relative_path="../parent"))
        registry = DatasetRegistry.discover([root])
        assert len(registry.all_manifests_for_diagnostics()) == 2

    def test_v11_manifests_have_no_path_to_escape_with(self, tmp_path: Path) -> None:
        """Identity-only lineage leaves the escape check nothing to inspect.

        Nothing in a 1.1 file can name a location, by construction.
        """
        root = _build_root(tmp_path, v11_manifest())
        entry = DatasetRegistry.discover([root]).resolve_role(ViewRole.TRAIN)
        assert entry.manifest.declared_parent is not None
        assert entry.manifest.declared_parent.relative_path is None


class TestPartitionSourceResolution:
    """The v1.1 partition source resolves through identity, never a path."""

    def test_a_pinned_source_resolves_through_the_catalog(self, tmp_path: Path) -> None:
        root = _build_root(tmp_path, v11_manifest())
        entry = DatasetRegistry.discover([root]).resolve_role(ViewRole.TRAIN)
        assert entry.manifest.partition_source is not None
        assert entry.manifest.partition_source.dataset_id == PARENT_ID
        # Identity carries no location; where the files live follows from
        # where the registry discovered that identity.
        assert not hasattr(entry.manifest.partition_source, "relative_path")

    def test_a_missing_source_identity_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        document = v11_manifest(source_id="ABSENT_SOURCE_V1", source_sha256="d" * 64)
        write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
        parent_digest = read_manifest(root / "parent" / "manifest.json").manifest_sha256
        document["parent"]["manifest_sha256"] = parent_digest
        write_manifest(root / "view", document)
        with pytest.raises(ParentResolutionError, match="ABSENT_SOURCE_V1"):
            DatasetRegistry.discover([root])

    def test_a_source_digest_mismatch_is_refused(self, tmp_path: Path) -> None:
        """A pinned source that no longer matches is a different dataset."""
        root = tmp_path / "root"
        document = v11_manifest(source_id=PARENT_ID, source_sha256="e" * 64)
        write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
        parent_digest = read_manifest(root / "parent" / "manifest.json").manifest_sha256
        document["parent"]["manifest_sha256"] = parent_digest
        write_manifest(root / "view", document)
        with pytest.raises(ParentResolutionError, match="pins partition source"):
            DatasetRegistry.discover([root])

    def test_relocating_source_and_view_preserves_identity(self, tmp_path: Path) -> None:
        """Moving both identities to another root changes nothing canonical.

        That location-blindness is the point of identity-based lineage.
        """
        here = _build_root(tmp_path, v11_manifest(), name="a")
        there = _build_root(tmp_path, v11_manifest(), name="b")
        (there / "view0").rename(there / "moved")
        first = DatasetRegistry.discover([here]).resolve_role(ViewRole.TRAIN)
        second = DatasetRegistry.discover([there]).resolve_role(ViewRole.TRAIN)
        assert first.identity == second.identity
        assert first.manifest.partition_source == second.manifest.partition_source


class TestParentResolution:
    def test_a_missing_parent_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        write_manifest(root / "view", v11_manifest(parent_id="ABSENT_PARENT_V1"))
        with pytest.raises(ParentResolutionError, match="ABSENT_PARENT_V1"):
            DatasetRegistry.discover([root])

    def test_a_pinned_parent_digest_must_match_the_discovered_parent(
        self, tmp_path: Path
    ) -> None:
        """A pinned parent that no longer matches is a different parent."""
        root = tmp_path / "root"
        write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
        parent_digest = read_manifest(root / "parent" / "manifest.json").manifest_sha256
        # Partition source pinned correctly, parent pinned wrongly — so the
        # failure is attributable to the parent pin specifically.
        write_manifest(
            root / "view",
            v11_manifest(parent_sha256="f" * 64, source_sha256=parent_digest),
        )
        with pytest.raises(ParentResolutionError, match="pins parent"):
            DatasetRegistry.discover([root])

    def test_a_resolved_parent_identity_is_attached(self, tmp_path: Path) -> None:
        root = _build_root(tmp_path, v11_manifest())
        entry = DatasetRegistry.discover([root]).resolve_role(ViewRole.TRAIN)
        assert entry.resolved_parent == DatasetIdentity(
            dataset_id=PARENT_ID, manifest_sha256=_digest_of(root, "parent")
        )

    def test_declared_and_resolved_parents_stay_distinct(self, tmp_path: Path) -> None:
        """Source-declared location must never be mistaken for resolved identity.

        Uses the legacy curated format — the only format that still declares
        a parent *path*; v1.1 lineage is identity-only by construction.
        """
        root = tmp_path / "root"
        write_manifest(root / "parent", raw_manifest(dataset_id=PARENT_ID))
        write_manifest(root / "curated", curated_manifest(parent_relative_path="../parent"))
        registry = DatasetRegistry.discover([root])
        entry = next(
            e
            for e in registry.all_manifests_for_diagnostics()
            if e.manifest.dataset_id == "SYNTH_CURATED_V1"
        )
        declared = entry.manifest.declared_parent
        assert declared is not None
        assert declared.relative_path == "../parent"
        assert declared.manifest_sha256 is None
        assert entry.resolved_parent is not None
        assert not hasattr(entry.resolved_parent, "relative_path")


@real_dataset
class TestRealDatasetRoot:
    def test_the_real_root_yields_raw_and_curated_metadata_only(self) -> None:
        registry = DatasetRegistry.discover([DATASET_ROOT])
        assert {e.manifest.dataset_id for e in registry.all_manifests_for_diagnostics()} == {
            "XAUUSD_S_M1_MT5_V1",
            "XAUUSD_S_M1_CURATED_V1",
            "BTCUSD_S_M1_MT5_V1",
            "BTCUSD_S_M1_CURATED_V1",
        }

    def test_no_working_view_exists_yet(self) -> None:
        """TRAIN/VALIDATION arrive in Slice 1F; until then discovery is legitimately empty."""
        registry = DatasetRegistry.discover([DATASET_ROOT])
        assert registry.default_views() == ()
        for role in (ViewRole.TRAIN, ViewRole.VALIDATION):
            with pytest.raises(DatasetNotFoundError):
                registry.resolve_role(role)

    @pytest.mark.parametrize("symbol", ["XAUUSD", "BTCUSD"])
    def test_the_curated_parent_resolves_to_the_discovered_raw_identity(
        self,
        symbol: str,
    ) -> None:
        registry = DatasetRegistry.discover([DATASET_ROOT])
        entries = {e.manifest.dataset_id: e for e in registry.all_manifests_for_diagnostics()}
        curated = entries[f"{symbol}_S_M1_CURATED_V1"]
        assert curated.resolved_parent == entries[f"{symbol}_S_M1_MT5_V1"].identity

    def test_the_legacy_parent_pin_is_not_fabricated(self) -> None:
        """The source names a parent without pinning it; the model must say so."""
        registry = DatasetRegistry.discover([DATASET_ROOT])
        curated = next(
            e
            for e in registry.all_manifests_for_diagnostics()
            if e.manifest.role is ViewRole.CURATED
        )
        declared = curated.manifest.declared_parent
        assert declared is not None
        assert declared.manifest_sha256 is None
        assert curated.resolved_parent is not None

    def test_a_real_reference_resolves_by_identity(self) -> None:
        registry = DatasetRegistry.discover([DATASET_ROOT])
        curated = next(
            e
            for e in registry.all_manifests_for_diagnostics()
            if e.manifest.role is ViewRole.CURATED
        )
        ref = DatasetRef(
            dataset_id=curated.manifest.dataset_id,
            manifest_sha256=curated.manifest.manifest_sha256,
            role=ViewRole.CURATED,
        )
        assert registry.resolve(ref).identity == curated.identity
