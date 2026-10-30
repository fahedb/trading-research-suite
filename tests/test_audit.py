"""Durability and refusal behavior of the Slice 1C JSONL audit writer.

Every path in this module is synthetic and rooted under ``tmp_path``.  The
tests never point at the configured runs root and never open market data.
"""

from __future__ import annotations

import errno
import fcntl
import importlib
import json
import multiprocessing
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

import trading_research.data.audit as audit_module
from trading_research.core.errors import (
    AuditCorruptionError,
    AuditLockError,
    AuditOpenError,
    AuditSyncError,
    AuditWriteError,
)
from trading_research.data.audit import JsonlAuditWriter, operational_event_time_utc


def _event(event_id: str = "event-0", *, detail: str = "synthetic reason") -> dict[str, object]:
    return {
        "authorization": {"reason": {"code": "synthetic", "detail": detail}},
        "event_id": event_id,
        "event_type": "dataset_access_decision",
        "outcome": "allowed",
        "schema_version": "access-decision-v1",
    }


def _existing_file(path: Path, content: bytes = b"") -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def _records(path: Path) -> list[dict[str, object]]:
    return [
        cast("dict[str, object]", json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _multiprocess_append(path_text: str, worker: int, count: int) -> None:
    writer = JsonlAuditWriter(Path(path_text))
    for index in range(count):
        writer.append(_event(f"worker-{worker}-event-{index}"))


class TestSerializationAndCreation:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"lock_timeout_seconds": 5.01}, "between 0 and 5"),
            ({"max_line_bytes": 16 * 1024 + 1}, "between 3 and 16384"),
        ],
    )
    def test_owner_fixed_limits_cannot_be_weakened(
        self, tmp_path: Path, kwargs: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            JsonlAuditWriter(tmp_path / "audit.jsonl", **kwargs)  # type: ignore[arg-type]

    def test_new_file_is_private_deterministic_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        event: dict[str, object] = {"z": 1, "a": "café"}

        JsonlAuditWriter(path).append(event)

        assert path.read_bytes() == b'{"a":"caf\xc3\xa9","z":1}\n'
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_append_preserves_every_existing_byte(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        first = _event("first")
        second = _event("second")
        writer = JsonlAuditWriter(path)
        writer.append(first)
        before = path.read_bytes()

        writer.append(second)

        after = path.read_bytes()
        assert after.startswith(before)
        assert [record["event_id"] for record in _records(path)] == ["first", "second"]

    def test_embedded_newline_is_escaped_into_one_physical_line(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        JsonlAuditWriter(path).append(_event(detail="line one\nline two"))

        assert path.read_bytes().count(b"\n") == 1
        authorization = cast("dict[str, object]", _records(path)[0]["authorization"])
        reason = cast("dict[str, object]", authorization["reason"])
        assert reason["detail"] == "line one\nline two"

    def test_nonfinite_json_is_refused_before_open(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        event: dict[str, object] = {"not_a_number": float("nan")}

        with pytest.raises(AuditWriteError, match="deterministic JSON"):
            JsonlAuditWriter(path).append(event)

        assert not path.exists()

    def test_oversize_line_is_refused_before_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"

        def forbidden_open(path_arg: Path, flags: int, mode: int = 0o777) -> int:
            del path_arg, flags, mode
            raise AssertionError("oversize validation must happen before open")

        monkeypatch.setattr(audit_module, "_open_fd", forbidden_open)
        with pytest.raises(AuditWriteError, match="maximum"):
            JsonlAuditWriter(path, max_line_bytes=32).append({"detail": "x" * 100})

        assert not path.exists()

    def test_operational_timestamp_is_rfc3339_utc(self) -> None:
        value = operational_event_time_utc()
        assert value.endswith("Z")
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        offset = parsed.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0


class TestSafeOpening:
    def test_parent_directory_is_not_created(self, tmp_path: Path) -> None:
        parent = tmp_path / "missing"
        path = parent / "audit.jsonl"

        with pytest.raises(AuditOpenError, match="parent directory"):
            JsonlAuditWriter(path).append(_event())

        assert not parent.exists()

    @pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW unavailable")
    def test_symlink_target_is_refused_without_touching_its_target(self, tmp_path: Path) -> None:
        target = tmp_path / "target.jsonl"
        _existing_file(target, b'{"existing":true}\n')
        link = tmp_path / "audit.jsonl"
        link.symlink_to(target)
        before = target.read_bytes()

        with pytest.raises(AuditOpenError):
            JsonlAuditWriter(link).append(_event())

        assert target.read_bytes() == before

    def test_non_regular_target_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        os.mkfifo(path, 0o600)

        with pytest.raises(AuditOpenError, match="not a regular file"):
            JsonlAuditWriter(path).append(_event())

    @pytest.mark.parametrize("mode", [0o620, 0o640, 0o601, 0o700])
    def test_nonprivate_existing_file_is_refused_unchanged(
        self, tmp_path: Path, mode: int
    ) -> None:
        path = tmp_path / "audit.jsonl"
        _existing_file(path, b'{"existing":true}\n')
        path.chmod(mode)
        before = path.read_bytes()

        with pytest.raises(AuditOpenError, match="private mode 0600"):
            JsonlAuditWriter(path).append(_event())

        assert path.read_bytes() == before

    def test_owner_mismatch_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"
        _existing_file(path)
        monkeypatch.setattr(audit_module, "_effective_uid", lambda: os.geteuid() + 1)

        with pytest.raises(AuditOpenError, match="owner uid"):
            JsonlAuditWriter(path).append(_event())

    def test_open_uses_append_close_on_exec_and_no_follow_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"
        actual_open = audit_module._open_fd
        target_flags: list[int] = []

        def recording_open(path_arg: Path, flags: int, mode: int = 0o777) -> int:
            if path_arg == path:
                target_flags.append(flags)
            return actual_open(path_arg, flags, mode)

        monkeypatch.setattr(audit_module, "_open_fd", recording_open)
        JsonlAuditWriter(path).append(_event())

        assert len(target_flags) == 1
        assert target_flags[0] & os.O_APPEND
        if hasattr(os, "O_CLOEXEC"):
            assert target_flags[0] & os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            assert target_flags[0] & os.O_NOFOLLOW
        assert not target_flags[0] & os.O_TRUNC


class TestExistingTail:
    @pytest.mark.parametrize(
        "content",
        [
            b'{"torn":true',
            b"not-json\n",
            b"[]\n",
            b'{"nonfinite":NaN}\n',
            b'{"overflow":1e999}\n',
            b'{"valid":true}\n\n',
        ],
        ids=[
            "torn",
            "malformed",
            "non-object",
            "non-finite",
            "overflowed-float",
            "empty-final-record",
        ],
    )
    def test_invalid_final_record_fails_closed_without_repair(
        self, tmp_path: Path, content: bytes
    ) -> None:
        path = tmp_path / "audit.jsonl"
        _existing_file(path, content)

        with pytest.raises(AuditCorruptionError):
            JsonlAuditWriter(path).append(_event())

        assert path.read_bytes() == content

    def test_valid_existing_final_record_allows_append(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        original = b'{"existing":true}\n'
        _existing_file(path, original)

        JsonlAuditWriter(path).append(_event())

        assert path.read_bytes().startswith(original)
        assert len(_records(path)) == 2


class TestLowLevelFailures:
    def test_exactly_one_write_and_lock_is_held_through_file_fsync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"
        _existing_file(path)
        actual_write = audit_module._write_fd
        actual_sync = audit_module._sync_fd
        actual_flock = audit_module._flock_fd
        write_calls = 0
        lock_operations: list[int] = []
        observed_locked_during_sync = False

        def recording_write(fd: int, payload: bytes) -> int:
            nonlocal write_calls
            write_calls += 1
            return actual_write(fd, payload)

        def recording_flock(fd: int, operation: int) -> None:
            lock_operations.append(operation)
            actual_flock(fd, operation)

        def checking_sync(fd: int) -> None:
            nonlocal observed_locked_during_sync
            contender = os.open(path, os.O_RDWR)
            try:
                try:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    observed_locked_during_sync = True
                else:
                    fcntl.flock(contender, fcntl.LOCK_UN)
            finally:
                os.close(contender)
            actual_sync(fd)

        monkeypatch.setattr(audit_module, "_write_fd", recording_write)
        monkeypatch.setattr(audit_module, "_flock_fd", recording_flock)
        monkeypatch.setattr(audit_module, "_sync_fd", checking_sync)

        JsonlAuditWriter(path).append(_event())

        assert write_calls == 1
        assert observed_locked_during_sync
        assert lock_operations[0] == fcntl.LOCK_EX | fcntl.LOCK_NB
        assert lock_operations[-1] == fcntl.LOCK_UN

    def test_short_write_is_fatal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "audit.jsonl"

        def short_write(fd: int, payload: bytes) -> int:
            del fd
            return len(payload) - 1

        monkeypatch.setattr(audit_module, "_write_fd", short_write)
        with pytest.raises(AuditWriteError, match="short audit write"):
            JsonlAuditWriter(path).append(_event())

    def test_write_error_is_fatal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "audit.jsonl"

        def failed_write(fd: int, payload: bytes) -> int:
            del fd, payload
            raise OSError(errno.ENOSPC, "synthetic full disk")

        monkeypatch.setattr(audit_module, "_write_fd", failed_write)
        with pytest.raises(AuditWriteError, match="cannot append"):
            JsonlAuditWriter(path).append(_event())

    def test_file_fsync_error_is_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"
        _existing_file(path)

        def failed_sync(fd: int) -> None:
            del fd
            raise OSError(errno.EIO, "synthetic sync failure")

        monkeypatch.setattr(audit_module, "_sync_fd", failed_sync)
        with pytest.raises(AuditSyncError, match="cannot fsync audit log"):
            JsonlAuditWriter(path).append(_event())

    def test_first_creation_directory_fsync_error_is_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"
        actual_sync = audit_module._sync_fd

        def fail_directory_sync(fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "synthetic directory sync failure")
            actual_sync(fd)

        monkeypatch.setattr(audit_module, "_sync_fd", fail_directory_sync)
        with pytest.raises(AuditSyncError, match="parent directory"):
            JsonlAuditWriter(path).append(_event())

    def test_retry_after_directory_fsync_failure_syncs_parent_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"
        actual_sync = audit_module._sync_fd
        directory_sync_calls = 0

        def fail_first_directory_sync(fd: int) -> None:
            nonlocal directory_sync_calls
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_sync_calls += 1
                if directory_sync_calls == 1:
                    raise OSError(errno.EIO, "synthetic first directory sync failure")
            actual_sync(fd)

        monkeypatch.setattr(audit_module, "_sync_fd", fail_first_directory_sync)
        writer = JsonlAuditWriter(path)
        with pytest.raises(AuditSyncError, match="parent directory"):
            writer.append(_event("first"))

        writer.append(_event("retry"))

        assert directory_sync_calls == 2
        assert [record["event_id"] for record in _records(path)] == ["first", "retry"]

    def test_lock_system_call_error_is_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "audit.jsonl"

        def failed_lock(fd: int, operation: int) -> None:
            del fd, operation
            raise OSError(errno.ENOLCK, "synthetic lock failure")

        monkeypatch.setattr(audit_module, "_flock_fd", failed_lock)
        with pytest.raises(AuditLockError, match="cannot lock"):
            JsonlAuditWriter(path).append(_event())

    def test_lock_timeout_is_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        _existing_file(path)
        holder = os.open(path, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            with pytest.raises(AuditLockError, match="timed out"):
                JsonlAuditWriter(path, lock_timeout_seconds=0.02).append(_event())
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)


class TestConcurrencyAndSideEffects:
    def test_concurrent_processes_produce_complete_unique_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        process_count = 4
        events_per_process = 12
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(
                target=_multiprocess_append,
                args=(str(path), worker, events_per_process),
            )
            for worker in range(process_count)
        ]

        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)

        assert all(process.exitcode == 0 for process in processes)
        records = _records(path)
        event_ids = [cast("str", record["event_id"]) for record in records]
        assert len(records) == process_count * events_per_process
        assert len(set(event_ids)) == len(event_ids)
        assert path.read_bytes().count(b"\n") == len(records)

    def test_import_performs_no_filesystem_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        actual_open = os.open
        calls: list[tuple[object, ...]] = []

        def forbidden_open(*args: object, **kwargs: object) -> int:
            calls.append((*args, kwargs))
            raise AssertionError("import must not open a filesystem path")

        monkeypatch.setattr(os, "open", forbidden_open)
        try:
            importlib.reload(audit_module)
        finally:
            monkeypatch.setattr(os, "open", actual_open)
            importlib.reload(audit_module)

        assert calls == []
