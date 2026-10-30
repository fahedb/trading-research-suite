"""Narrow partition-source location resolution for the row loader."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any

import pytest

from tests.manifest_fixtures import (
    curated_manifest,
    raw_manifest,
    v11_manifest,
    write_manifest,
)
from trading_research.core import ViewRole
from trading_research.data import DatasetRegistry, PartitionSourceLocation
from trading_research.data.manifests import read_manifest

PARENT_ID = "SYNTH_PARENT_V1"


def _root_with_v11(
    tmp_path: Path,
    *,
    name: str = "root",
    document: dict[str, Any] | None = None,
) -> Path:
    """Write a raw source and an exact-source-pinned v1.1 target."""
    root = tmp_path / name
    parent_path = write_manifest(root / "source", raw_manifest(dataset_id=PARENT_ID))
    parent_digest = read_manifest(parent_path).manifest_sha256
    target = v11_manifest() if document is None else document
    target["parent"]["manifest_sha256"] = parent_digest
    target["partition_source"]["manifest_sha256"] = parent_digest
    write_manifest(root / "target", target)
    return root


class TestPartitionSourceLocation:
    def test_v11_source_resolves_to_identity_and_directory_only(self, tmp_path: Path) -> None:
        root = _root_with_v11(tmp_path)
        registry = DatasetRegistry.discover([root])
        target = registry.resolve_role(ViewRole.TRAIN)

        location = registry.resolve_partition_source_location(target)

        assert isinstance(location, PartitionSourceLocation)
        assert location.identity == target.manifest.partition_source
        assert location.directory == (root / "source").resolve()
        assert [field.name for field in fields(location)] == ["identity", "directory"]
        for absent in ("manifest", "partitions", "filename", "sha256", "rows", "byte_size"):
            assert not hasattr(location, absent)
        with pytest.raises(FrozenInstanceError):
            location.directory = tmp_path  # type: ignore[misc]

    def test_legacy_curated_resolves_to_registered_parent_directory(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "root"
        write_manifest(root / "source", raw_manifest(dataset_id=PARENT_ID))
        write_manifest(
            root / "curated",
            curated_manifest(parent_id=PARENT_ID, parent_relative_path="../source"),
        )
        registry = DatasetRegistry.discover([root])
        target = next(
            entry
            for entry in registry.all_manifests_for_diagnostics()
            if entry.manifest.role is ViewRole.CURATED
        )

        location = registry.resolve_partition_source_location(target)

        assert location.identity == target.resolved_parent
        assert location.directory == (root / "source").resolve()

    def test_legacy_raw_resolves_to_its_own_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        write_manifest(root / "source", raw_manifest(dataset_id=PARENT_ID))
        registry = DatasetRegistry.discover([root])
        target = next(
            entry
            for entry in registry.all_manifests_for_diagnostics()
            if entry.manifest.role is ViewRole.RAW
        )

        location = registry.resolve_partition_source_location(target)

        assert location.identity == target.identity
        assert location.directory == (root / "source").resolve()

    def test_relocation_changes_only_the_local_directory(self, tmp_path: Path) -> None:
        first_root = _root_with_v11(tmp_path, name="first")
        second_root = _root_with_v11(tmp_path, name="second")
        first_registry = DatasetRegistry.discover([first_root])
        second_registry = DatasetRegistry.discover([second_root])
        first_target = first_registry.resolve_role(ViewRole.TRAIN)
        second_target = second_registry.resolve_role(ViewRole.TRAIN)

        first = first_registry.resolve_partition_source_location(first_target)
        second = second_registry.resolve_partition_source_location(second_target)

        assert first.identity == second.identity
        assert first.directory != second.directory
        assert first.directory == (first_root / "source").resolve()
        assert second.directory == (second_root / "source").resolve()

    def test_target_only_filename_and_digest_are_not_looked_up_on_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = v11_manifest()
        partition = document["admitted_partitions"][0]
        partition["filename"] = "target-only.parquet"
        partition["sha256"] = "d" * 64
        partition["bytes"] = 999
        root = _root_with_v11(tmp_path, document=document)
        registry = DatasetRegistry.discover([root])
        target = registry.resolve_role(ViewRole.TRAIN)
        expected_directory = (root / "source").resolve()

        def forbidden_exists(_path: Path) -> bool:
            raise AssertionError("location resolution must not inspect a partition file")

        monkeypatch.setattr(Path, "exists", forbidden_exists)
        location = registry.resolve_partition_source_location(target)

        assert location.directory == expected_directory
        assert target.manifest.partitions[0].filename == "target-only.parquet"
        assert target.manifest.partitions[0].sha256 == "d" * 64
        assert target.manifest.partitions[0].byte_size == 999
