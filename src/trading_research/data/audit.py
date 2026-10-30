"""Durable append-only JSONL persistence for access-decision audit events.

This module knows how to persist one already-constructed event.  It does not
classify data, decide whether access is allowed, or interpret native market
time.  The only civil timestamp here is operational provenance describing
when an audit decision was recorded.

The writer deliberately uses low-level file-descriptor operations.  Buffered
text I/O can split one logical event across several writes, while the audit
contract requires one complete JSON object to be appended by exactly one
``write(2)`` call and made durable before access may proceed.
"""

from __future__ import annotations

import errno
import fcntl
import json
import math
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from trading_research.core.errors import (
    AuditCorruptionError,
    AuditLockError,
    AuditOpenError,
    AuditSyncError,
    AuditWriteError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["JsonlAuditWriter", "operational_event_time_utc"]

_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
_DEFAULT_MAX_LINE_BYTES = 16 * 1024
_LOCK_POLL_SECONDS = 0.01

# Bound aliases make the low-level failure points independently injectable in
# tests without replacing functions on the process-global ``os`` module.
_close_fd = os.close
_effective_uid = os.geteuid
_fchmod_fd = os.fchmod
_flock_fd = fcntl.flock
_fstat_fd = os.fstat
_monotonic = time.monotonic
_open_fd = os.open
_pread_fd = os.pread
_sleep = time.sleep
_sync_fd = os.fsync
_write_fd = os.write


def operational_event_time_utc() -> str:
    """Return the current operational time as an RFC 3339 UTC timestamp.

    This clock describes the audit operation, not any market observation.  It
    therefore has no relationship to ``NativeTimeKey`` or a dataset's broker
    wall-clock domain.
    """
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reject_nonfinite_json_constant(value: str) -> None:
    """Reject non-standard JSON constants while validating an existing tail."""
    raise ValueError(f"non-finite JSON constant {value!r} is not permitted")


def _contains_nonfinite_number(value: object) -> bool:
    """Return whether decoded JSON contains an overflowed non-finite float."""
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_nonfinite_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_nonfinite_number(item) for item in value)
    return False


class JsonlAuditWriter:
    """Append complete JSON objects durably to one process-shared audit log.

    Args:
        path: Audit JSONL path.  Its parent must already exist.
        lock_timeout_seconds: Maximum wait for the advisory exclusive lock.
        max_line_bytes: Maximum UTF-8 byte size of one encoded line, including
            its trailing newline.

    The log file is locked directly; no sidecar is created.  Locks are
    advisory, so every platform writer must use this class.  V1 never rotates,
    compacts, truncates, or repairs the log.
    """

    __slots__ = ("_lock_timeout_seconds", "_max_line_bytes", "_path")

    def __init__(
        self,
        path: Path,
        lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
        max_line_bytes: int = _DEFAULT_MAX_LINE_BYTES,
    ) -> None:
        if not isinstance(path, Path):
            raise TypeError(f"path must be pathlib.Path, got {type(path).__name__}")
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, int | float)
            or not math.isfinite(lock_timeout_seconds)
            or lock_timeout_seconds < 0
            or lock_timeout_seconds > _DEFAULT_LOCK_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "lock_timeout_seconds must be finite and between 0 and "
                f"{_DEFAULT_LOCK_TIMEOUT_SECONDS:g} seconds"
            )
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
            raise ValueError("max_line_bytes must be an integer")
        if not 3 <= max_line_bytes <= _DEFAULT_MAX_LINE_BYTES:
            raise ValueError(
                f"max_line_bytes must be between 3 and {_DEFAULT_MAX_LINE_BYTES} bytes"
            )
        self._path = path
        self._lock_timeout_seconds = float(lock_timeout_seconds)
        self._max_line_bytes = max_line_bytes

    def append(self, event: Mapping[str, object]) -> None:
        """Append ``event`` and return only after its bytes are durable.

        Serialization and the encoded-size check happen before the filesystem
        is touched.  Any open, lock, corruption, write, or synchronization
        failure raises a dedicated audit exception; callers must treat every
        such failure as access denied.

        Args:
            event: One JSON-compatible access-decision object.

        Raises:
            AuditOpenError: The path cannot safely be opened or inspected.
            AuditLockError: The exclusive lock cannot be acquired or released.
            AuditCorruptionError: The existing final record is malformed or
                torn and therefore cannot safely be followed by another line.
            AuditWriteError: Serialization or the single append write fails.
            AuditSyncError: File or parent-directory synchronization fails.
        """
        payload = self._serialize(event)
        parent = self._path.parent
        if not parent.is_dir():
            raise AuditOpenError(f"audit parent directory does not exist: {parent}")

        fd, created = self._open()
        locked = False
        active_error: BaseException | None = None
        try:
            self._validate_target(fd, created=created)
            self._acquire_lock(fd)
            locked = True
            self._validate_existing_tail(fd)
            self._append_once(fd, payload)
            self._sync_file(fd)
            # Syncing the directory after every successful append closes the
            # concurrent-first-create race: a process that opened the inode
            # after another process created it must not return before the
            # directory entry itself is durable. It also lets a safe retry
            # complete durability after an earlier directory-fsync failure.
            self._sync_parent(parent)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            self._release(fd, locked=locked, suppress_errors=active_error is not None)

    def _serialize(self, event: Mapping[str, object]) -> bytes:
        """Build exactly one deterministic UTF-8 JSON line before any open."""
        try:
            document = dict(event)
            encoded = json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise AuditWriteError(f"audit event is not valid deterministic JSON: {exc}") from exc
        payload = encoded + b"\n"
        if len(payload) > self._max_line_bytes:
            raise AuditWriteError(
                f"encoded audit line is {len(payload)} bytes; maximum is "
                f"{self._max_line_bytes} bytes"
            )
        return payload

    def _open(self) -> tuple[int, bool]:
        """Open the audit target without following a final-component symlink."""
        flags = os.O_RDWR | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = _open_fd(self._path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                return _open_fd(self._path, flags), False
            except OSError as exc:
                raise AuditOpenError(f"cannot safely open audit log {self._path}: {exc}") from exc
        except OSError as exc:
            raise AuditOpenError(f"cannot create audit log {self._path}: {exc}") from exc
        return fd, True

    def _validate_target(self, fd: int, *, created: bool) -> None:
        """Require a private, regular file owned by the effective caller."""
        try:
            if created:
                _fchmod_fd(fd, 0o600)
            metadata = _fstat_fd(fd)
        except OSError as exc:
            raise AuditOpenError(f"cannot inspect audit log {self._path}: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise AuditOpenError(f"audit target is not a regular file: {self._path}")
        expected_uid = _effective_uid()
        if metadata.st_uid != expected_uid:
            raise AuditOpenError(
                f"audit target owner uid {metadata.st_uid} does not match effective uid "
                f"{expected_uid}: {self._path}"
            )
        permissions = stat.S_IMODE(metadata.st_mode)
        if permissions != 0o600:
            raise AuditOpenError(
                f"audit target must be private mode 0600 (found {permissions:04o}): "
                f"{self._path}"
            )

    def _acquire_lock(self, fd: int) -> None:
        """Acquire the advisory exclusive lock within the configured timeout."""
        deadline = _monotonic() + self._lock_timeout_seconds
        while True:
            try:
                _flock_fd(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                pass
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise AuditLockError(f"cannot lock audit log {self._path}: {exc}") from exc
            remaining = deadline - _monotonic()
            if remaining <= 0:
                raise AuditLockError(
                    f"timed out after {self._lock_timeout_seconds:g}s acquiring audit lock: "
                    f"{self._path}"
                )
            _sleep(min(_LOCK_POLL_SECONDS, remaining))

    def _validate_existing_tail(self, fd: int) -> None:
        """Fail closed if the last existing JSONL record is incomplete or invalid."""
        try:
            size = _fstat_fd(fd).st_size
            if size == 0:
                return
            read_size = min(size, self._max_line_bytes + 1)
            tail = _pread_fd(fd, read_size, size - read_size)
        except OSError as exc:
            raise AuditCorruptionError(
                f"cannot validate existing audit tail {self._path}: {exc}"
            ) from exc
        if len(tail) != read_size:
            raise AuditCorruptionError(
                f"could not read the complete existing audit tail: {self._path}"
            )
        if not tail.endswith(b"\n"):
            raise AuditCorruptionError(
                f"existing audit log has a torn final line; refusing repair: {self._path}"
            )

        without_final_newline = tail[:-1]
        previous_newline = without_final_newline.rfind(b"\n")
        if previous_newline >= 0:
            record = without_final_newline[previous_newline + 1 :]
        else:
            if size > read_size:
                raise AuditCorruptionError(
                    f"existing final audit line exceeds {self._max_line_bytes} bytes: "
                    f"{self._path}"
                )
            record = without_final_newline
        if not record or len(record) + 1 > self._max_line_bytes:
            raise AuditCorruptionError(f"existing final audit record is invalid: {self._path}")

        try:
            text = record.decode("utf-8")
            parsed = json.loads(text, parse_constant=_reject_nonfinite_json_constant)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise AuditCorruptionError(
                f"existing final audit record is malformed; refusing repair: {self._path}"
            ) from exc
        if not isinstance(parsed, dict):
            raise AuditCorruptionError(
                f"existing final audit record is not a JSON object: {self._path}"
            )
        if _contains_nonfinite_number(parsed):
            raise AuditCorruptionError(
                f"existing final audit record contains a non-finite number: {self._path}"
            )

    def _append_once(self, fd: int, payload: bytes) -> None:
        """Perform the event's one and only low-level append write."""
        try:
            written = _write_fd(fd, payload)
        except OSError as exc:
            raise AuditWriteError(f"cannot append audit event to {self._path}: {exc}") from exc
        if written != len(payload):
            raise AuditWriteError(
                f"short audit write to {self._path}: wrote {written} of {len(payload)} bytes"
            )

    def _sync_file(self, fd: int) -> None:
        """Make the appended file data and metadata durable."""
        try:
            _sync_fd(fd)
        except OSError as exc:
            raise AuditSyncError(f"cannot fsync audit log {self._path}: {exc}") from exc

    def _sync_parent(self, parent: Path) -> None:
        """Make the audit file's directory entry durable."""
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
        try:
            directory_fd = _open_fd(parent, flags)
        except OSError as exc:
            raise AuditSyncError(f"cannot open audit parent directory {parent}: {exc}") from exc
        sync_error: AuditSyncError | None = None
        try:
            _sync_fd(directory_fd)
        except OSError as exc:
            sync_error = AuditSyncError(f"cannot fsync audit parent directory {parent}: {exc}")
            sync_error.__cause__ = exc
        finally:
            try:
                _close_fd(directory_fd)
            except OSError as exc:
                if sync_error is None:
                    sync_error = AuditSyncError(
                        f"cannot close audit parent directory {parent}: {exc}"
                    )
                    sync_error.__cause__ = exc
        if sync_error is not None:
            raise sync_error

    def _release(self, fd: int, *, locked: bool, suppress_errors: bool) -> None:
        """Unlock and close, preserving an already-active primary failure."""
        cleanup_error: AuditLockError | AuditOpenError | None = None
        if locked:
            try:
                _flock_fd(fd, fcntl.LOCK_UN)
            except OSError as exc:
                cleanup_error = AuditLockError(
                    f"cannot release audit lock for {self._path}: {exc}"
                )
                cleanup_error.__cause__ = exc
        try:
            _close_fd(fd)
        except OSError as exc:
            if cleanup_error is None:
                cleanup_error = AuditOpenError(f"cannot close audit log {self._path}: {exc}")
                cleanup_error.__cause__ = exc
        if cleanup_error is not None and not suppress_errors:
            raise cleanup_error
