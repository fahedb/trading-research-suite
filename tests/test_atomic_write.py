"""Fault-injected tests for immutable atomic byte publication."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING, NoReturn

import pytest

import trading_research.data.atomic_write as atomic_write

if TYPE_CHECKING:
    from pathlib import Path


def _temporary_names(parent: Path, target_name: str) -> tuple[Path, ...]:
    return tuple(parent.glob(f".{target_name}.*.tmp"))


class TestDurableDirectoryCreation:
    def test_absent_child_is_created_and_parent_is_fsynced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "generated-view"
        actual_sync = atomic_write._sync_fd
        directory_syncs = 0

        def recording_sync(descriptor: int) -> None:
            nonlocal directory_syncs
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_syncs += 1
            actual_sync(descriptor)

        monkeypatch.setattr(atomic_write, "_sync_fd", recording_sync)

        assert atomic_write._ensure_durable_directory(child) is True
        assert child.is_dir()
        assert not child.is_symlink()
        assert directory_syncs == 1

    def test_existing_plain_directory_is_a_durable_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "generated-view"
        child.mkdir()
        actual_sync_parent = atomic_write._sync_parent
        synced: list[Path] = []

        def recording_sync_parent(parent: Path) -> None:
            synced.append(parent)
            actual_sync_parent(parent)

        monkeypatch.setattr(atomic_write, "_sync_parent", recording_sync_parent)

        assert atomic_write._ensure_durable_directory(child) is False
        assert synced == [tmp_path]

    def test_child_symlink_is_refused(self, tmp_path: Path) -> None:
        real_directory = tmp_path / "real-directory"
        real_directory.mkdir()
        child = tmp_path / "generated-view"
        child.symlink_to(real_directory, target_is_directory=True)

        with pytest.raises(
            atomic_write._DirectoryCreationError,
            match="generated directory must not be a symlink",
        ):
            atomic_write._ensure_durable_directory(child)

        assert child.is_symlink()
        assert real_directory.is_dir()

    def test_non_directory_child_is_refused(self, tmp_path: Path) -> None:
        child = tmp_path / "generated-view"
        child.write_bytes(b"not a directory")

        with pytest.raises(
            atomic_write._DirectoryCreationError,
            match="generated directory is not a directory",
        ):
            atomic_write._ensure_durable_directory(child)

        assert child.read_bytes() == b"not a directory"

    def test_parent_symlink_is_refused(self, tmp_path: Path) -> None:
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        child = linked_parent / "generated-view"

        with pytest.raises(
            atomic_write._DirectoryCreationError,
            match="generated-directory parent must not be a symlink",
        ):
            atomic_write._ensure_durable_directory(child)

        assert not (real_parent / child.name).exists()

    def test_missing_parent_is_refused(self, tmp_path: Path) -> None:
        child = tmp_path / "missing-parent" / "generated-view"

        with pytest.raises(
            atomic_write._DirectoryCreationError,
            match="generated-directory parent does not exist",
        ):
            atomic_write._ensure_durable_directory(child)

        assert not child.exists()

    def test_creation_failure_has_a_clear_private_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "generated-view"

        def failed_mkdir(_path: Path, _mode: int) -> NoReturn:
            raise OSError("injected mkdir failure")

        monkeypatch.setattr(atomic_write, "_make_dir", failed_mkdir)

        with pytest.raises(
            atomic_write._DirectoryCreationError,
            match=r"cannot create generated directory.*injected mkdir failure",
        ):
            atomic_write._ensure_durable_directory(child)

        assert not child.exists()

    def test_parent_fsync_failure_leaves_a_complete_directory_and_retry_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "generated-view"
        actual_sync = atomic_write._sync_fd
        fail_directory_sync = True

        def failed_directory_sync(descriptor: int) -> None:
            if fail_directory_sync and stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("injected generated-directory fsync failure")
            actual_sync(descriptor)

        monkeypatch.setattr(atomic_write, "_sync_fd", failed_directory_sync)

        with pytest.raises(
            atomic_write._DirectoryCreationError,
            match="cannot make generated directory entry durable",
        ):
            atomic_write._ensure_durable_directory(child)

        assert child.is_dir()
        fail_directory_sync = False
        assert atomic_write._ensure_durable_directory(child) is False

    def test_path_must_be_pathlib_path(self) -> None:
        with pytest.raises(TypeError, match=r"path must be pathlib\.Path"):
            atomic_write._ensure_durable_directory("generated-view")  # type: ignore[arg-type]


class TestImmutablePublication:
    def test_absent_target_is_published_as_complete_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "manifest.json"
        payload = b'{"complete":true}\n'

        assert atomic_write._publish_immutable_bytes(target, payload) is True

        assert target.read_bytes() == payload
        assert _temporary_names(tmp_path, target.name) == ()

    def test_identical_target_is_a_noop_that_retries_parent_durability(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        payload = b'{"stable":true}\n'
        assert atomic_write._publish_immutable_bytes(target, payload) is True

        def forbidden_temp(**_kwargs: object) -> NoReturn:
            raise AssertionError("identical publication must not rewrite through a temp")

        actual_sync = atomic_write._sync_fd
        directory_syncs = 0

        def recording_sync(descriptor: int) -> None:
            nonlocal directory_syncs
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_syncs += 1
            actual_sync(descriptor)

        monkeypatch.setattr(atomic_write, "_make_temp", forbidden_temp)
        monkeypatch.setattr(atomic_write, "_sync_fd", recording_sync)

        assert atomic_write._publish_immutable_bytes(target, payload) is False
        assert target.read_bytes() == payload
        assert directory_syncs == 1

    def test_different_existing_target_is_refused_without_rewrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        original = b'{"identity":"first"}\n'
        target.write_bytes(original)

        def forbidden_temp(**_kwargs: object) -> NoReturn:
            raise AssertionError("conflict must refuse before making a temp")

        monkeypatch.setattr(atomic_write, "_make_temp", forbidden_temp)
        with pytest.raises(
            atomic_write._PublicationConflictError,
            match="existing bytes differ",
        ):
            atomic_write._publish_immutable_bytes(
                target,
                b'{"identity":"different"}\n',
            )

        assert target.read_bytes() == original

    def test_partial_low_level_writes_are_completed_before_publication(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        payload = b"a complete payload larger than one short write\n"
        actual_write = atomic_write._write_fd
        writes = 0

        def short_write(descriptor: int, remaining: bytes) -> int:
            nonlocal writes
            writes += 1
            return actual_write(descriptor, remaining[:3])

        monkeypatch.setattr(atomic_write, "_write_fd", short_write)

        assert atomic_write._publish_immutable_bytes(target, payload) is True
        assert writes > 1
        assert target.read_bytes() == payload

    def test_concurrent_identical_publisher_is_accepted_without_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        payload = b'{"same":"race"}\n'

        def racing_link(_temporary: Path, destination: Path) -> NoReturn:
            destination.write_bytes(payload)
            raise FileExistsError(destination)

        monkeypatch.setattr(atomic_write, "_link_file", racing_link)

        assert atomic_write._publish_immutable_bytes(target, payload) is False
        assert target.read_bytes() == payload
        assert _temporary_names(tmp_path, target.name) == ()

    def test_concurrent_different_publisher_is_refused_without_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        winner = b'{"winner":"other"}\n'

        def racing_link(_temporary: Path, destination: Path) -> NoReturn:
            destination.write_bytes(winner)
            raise FileExistsError(destination)

        monkeypatch.setattr(atomic_write, "_link_file", racing_link)

        with pytest.raises(
            atomic_write._PublicationConflictError,
            match="concurrent bytes differ",
        ):
            atomic_write._publish_immutable_bytes(target, b'{"winner":"this"}\n')

        assert target.read_bytes() == winner
        assert _temporary_names(tmp_path, target.name) == ()


class TestPublicationFailures:
    def test_write_failure_leaves_no_partial_final_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        actual_write = atomic_write._write_fd
        calls = 0

        def torn_then_failed(descriptor: int, remaining: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return actual_write(descriptor, remaining[:4])
            raise OSError("injected write failure")

        monkeypatch.setattr(atomic_write, "_write_fd", torn_then_failed)

        with pytest.raises(atomic_write._AtomicPublicationError, match="cannot write"):
            atomic_write._publish_immutable_bytes(target, b'{"never":"partial"}\n')

        assert not target.exists()
        assert _temporary_names(tmp_path, target.name) == ()

    def test_temp_fsync_failure_leaves_no_final_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        actual_sync = atomic_write._sync_fd

        def fail_regular_file_sync(descriptor: int) -> None:
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("injected temp fsync failure")
            actual_sync(descriptor)

        monkeypatch.setattr(atomic_write, "_sync_fd", fail_regular_file_sync)

        with pytest.raises(atomic_write._AtomicPublicationError, match="complete publication temp"):
            atomic_write._publish_immutable_bytes(target, b'{"not":"published"}\n')

        assert not target.exists()
        assert _temporary_names(tmp_path, target.name) == ()

    def test_atomic_publish_failure_leaves_no_final_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"

        def failed_link(_temporary: Path, _destination: Path) -> NoReturn:
            raise OSError("injected link failure")

        monkeypatch.setattr(atomic_write, "_link_file", failed_link)

        with pytest.raises(atomic_write._AtomicPublicationError, match="atomically publish"):
            atomic_write._publish_immutable_bytes(target, b'{"not":"linked"}\n')

        assert not target.exists()
        assert _temporary_names(tmp_path, target.name) == ()

    def test_parent_fsync_failure_exposes_only_complete_final_bytes_and_retry_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        payload = b'{"complete":"before-directory-sync"}\n'
        actual_sync = atomic_write._sync_fd
        fail_directory = True

        def failed_directory_sync(descriptor: int) -> None:
            if fail_directory and stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("injected parent fsync failure")
            actual_sync(descriptor)

        monkeypatch.setattr(atomic_write, "_sync_fd", failed_directory_sync)

        with pytest.raises(atomic_write._AtomicPublicationError, match="parent directory"):
            atomic_write._publish_immutable_bytes(target, payload)

        assert target.read_bytes() == payload
        assert _temporary_names(tmp_path, target.name) == ()

        fail_directory = False
        assert atomic_write._publish_immutable_bytes(target, payload) is False
        assert target.read_bytes() == payload

    def test_file_is_synced_before_atomic_publication_and_parent_afterward(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "manifest.json"
        events: list[str] = []
        actual_sync = atomic_write._sync_fd
        actual_link = atomic_write._link_file

        def recording_sync(descriptor: int) -> None:
            mode = os.fstat(descriptor).st_mode
            events.append("parent-fsync" if stat.S_ISDIR(mode) else "temp-fsync")
            actual_sync(descriptor)

        def recording_link(temporary: Path, destination: Path) -> None:
            events.append("publish")
            actual_link(temporary, destination)

        monkeypatch.setattr(atomic_write, "_sync_fd", recording_sync)
        monkeypatch.setattr(atomic_write, "_link_file", recording_link)

        assert atomic_write._publish_immutable_bytes(target, b"ordered\n") is True
        assert events == ["temp-fsync", "publish", "parent-fsync"]


class TestPublicationInputBoundary:
    @pytest.mark.parametrize("payload", [bytearray(b"x"), memoryview(b"x"), "x"])
    def test_payload_must_be_complete_exact_bytes(
        self, tmp_path: Path, payload: object
    ) -> None:
        with pytest.raises(TypeError, match="exact bytes"):
            atomic_write._publish_immutable_bytes(
                tmp_path / "manifest.json",
                payload,  # type: ignore[arg-type]
            )

    def test_parent_directory_must_already_exist(self, tmp_path: Path) -> None:
        target = tmp_path / "missing" / "manifest.json"
        with pytest.raises(atomic_write._AtomicPublicationError, match="does not exist"):
            atomic_write._publish_immutable_bytes(target, b"complete\n")
        assert not target.exists()
