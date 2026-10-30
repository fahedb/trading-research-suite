"""Crash-safe immutable publication of already-prepared artifact bytes.

The helper in this module is deliberately internal.  It publishes one complete
byte string under an absent final name without ever replacing an existing
artifact.  A same-directory temporary inode makes the write and file ``fsync``
happen before publication; a hard link supplies the atomic no-replace step that
ordinary POSIX ``rename`` does not.
"""

from __future__ import annotations

import os as _os
import stat as _stat
import tempfile as _tempfile
from pathlib import Path as _Path

__all__: list[str] = []

_READ_CHUNK_BYTES = 64 * 1024

# Bind low-level operations once so every durability boundary is independently
# fault-injectable without monkeypatching process-global ``os`` functions.
_close_fd = _os.close
_fstat_fd = _os.fstat
_link_file = _os.link
_lstat_path = _os.lstat
_make_temp = _tempfile.mkstemp
_make_dir = _os.mkdir
_open_fd = _os.open
_read_fd = _os.read
_sync_fd = _os.fsync
_unlink_file = _os.unlink
_write_fd = _os.write


class _AtomicPublicationError(OSError):
    """One immutable-publication durability step failed."""


class _PublicationConflictError(_AtomicPublicationError):
    """The final name already contains different bytes."""


class _DirectoryCreationError(_AtomicPublicationError):
    """A generated artifact directory is absent, unsafe, or not durable."""


def _ensure_durable_directory(path: _Path) -> bool:
    """Create one plain child directory and durably record its parent entry.

    The parent must already be a real directory rather than a symlink.  An
    existing real child directory is accepted and its parent is re-synced so a
    caller can safely retry after an earlier directory-sync failure.

    Returns:
        ``True`` when this call created ``path``, or ``False`` when the same
        directory already existed.

    Raises:
        TypeError: If ``path`` is not a :class:`pathlib.Path`.
        _DirectoryCreationError: If the parent/child is unsafe or any creation
            or durability step fails.
    """
    if not isinstance(path, _Path):
        raise TypeError(f"path must be pathlib.Path, got {type(path).__name__}")
    if path.name in {"", ".", ".."}:
        raise _DirectoryCreationError(
            f"generated directory must name one concrete child: {path}"
        )

    parent = path.parent
    _require_plain_directory(parent, label="generated-directory parent")

    created = False
    status = _plain_directory_status(path, label="generated directory")
    if status is None:
        try:
            _make_dir(path, 0o755)
        except FileExistsError:
            # A concurrent creator may have won after the absence check.  It
            # is accepted only after the same no-symlink/type validation.
            _require_plain_directory(path, label="generated directory")
        except OSError as exc:
            raise _DirectoryCreationError(
                f"cannot create generated directory {path}: {exc}"
            ) from exc
        else:
            created = True
            _require_plain_directory(path, label="created generated directory")

    try:
        _sync_parent(parent)
    except _AtomicPublicationError as exc:
        raise _DirectoryCreationError(
            f"cannot make generated directory entry durable at {path}: {exc}"
        ) from exc
    return created


def _plain_directory_status(path: _Path, *, label: str) -> bool | None:
    """Return whether a path is a plain directory, or ``None`` when absent."""
    try:
        metadata = _lstat_path(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _DirectoryCreationError(f"cannot inspect {label} {path}: {exc}") from exc
    if _stat.S_ISLNK(metadata.st_mode):
        raise _DirectoryCreationError(f"{label} must not be a symlink: {path}")
    if not _stat.S_ISDIR(metadata.st_mode):
        raise _DirectoryCreationError(f"{label} is not a directory: {path}")
    return True


def _require_plain_directory(path: _Path, *, label: str) -> None:
    """Require one existing non-symlink directory entry."""
    if _plain_directory_status(path, label=label) is None:
        raise _DirectoryCreationError(f"{label} does not exist: {path}")


def _publish_immutable_bytes(path: _Path, payload: bytes) -> bool:
    """Publish ``payload`` once, or accept an identical existing artifact.

    Args:
        path: Final artifact filename.  Its parent directory must already
            exist.
        payload: Complete bytes prepared before any publication operation.

    Returns:
        ``True`` when this call created the final name, or ``False`` when an
        identical immutable artifact already occupied it.

    Raises:
        TypeError: If ``path`` or ``payload`` has the wrong type.
        _AtomicPublicationError: If temporary writing, synchronization,
            publication, inspection, or cleanup fails.
        _PublicationConflictError: If ``path`` already contains different
            bytes.  Existing bytes are never overwritten.
    """
    if not isinstance(path, _Path):
        raise TypeError(f"path must be pathlib.Path, got {type(path).__name__}")
    if type(payload) is not bytes:
        raise TypeError(f"payload must be exact bytes, got {type(payload).__name__}")

    parent = path.parent
    if not parent.is_dir():
        raise _AtomicPublicationError(
            f"publication parent directory does not exist: {parent}"
        )

    existing = _existing_bytes_match(path, payload)
    if existing is True:
        # A prior call may have linked the complete inode and then failed its
        # directory fsync.  Re-sync even though the final bytes need no write.
        _sync_parent(parent)
        return False
    if existing is False:
        raise _PublicationConflictError(
            f"immutable publication conflict at {path}: existing bytes differ"
        )

    descriptor: int | None = None
    temporary: _Path | None = None
    try:
        try:
            descriptor, temporary_name = _make_temp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=parent,
            )
        except OSError as exc:
            raise _AtomicPublicationError(
                f"cannot create same-directory publication temp for {path}: {exc}"
            ) from exc
        temporary = _Path(temporary_name)

        _write_complete(descriptor, payload, path=path)
        try:
            _sync_fd(descriptor)
        except OSError as exc:
            raise _AtomicPublicationError(
                f"cannot fsync complete publication temp for {path}: {exc}"
            ) from exc
        try:
            _close_fd(descriptor)
        except OSError as exc:
            raise _AtomicPublicationError(
                f"cannot close complete publication temp for {path}: {exc}"
            ) from exc
        descriptor = None

        created = _link_or_compare(temporary, path, payload)
    except BaseException:
        _cleanup_temp(descriptor, temporary, suppress_errors=True)
        raise

    cleanup_error = _cleanup_temp(descriptor, temporary, suppress_errors=False)
    sync_error: _AtomicPublicationError | None = None
    try:
        _sync_parent(parent)
    except _AtomicPublicationError as exc:
        sync_error = exc

    if cleanup_error is not None:
        raise cleanup_error
    if sync_error is not None:
        raise sync_error
    return created


def _link_or_compare(temporary: _Path, path: _Path, payload: bytes) -> bool:
    """Atomically link an absent target, resolving a concurrent publisher."""
    try:
        _link_file(temporary, path)
    except FileExistsError as exc:
        existing = _existing_bytes_match(path, payload)
        if existing is True:
            return False
        if existing is False:
            raise _PublicationConflictError(
                f"immutable publication conflict at {path}: concurrent bytes differ"
            ) from exc
        raise _AtomicPublicationError(
            f"publication target {path} disappeared during conflict inspection"
        ) from exc
    except OSError as exc:
        raise _AtomicPublicationError(
            f"cannot atomically publish complete bytes at {path}: {exc}"
        ) from exc
    return True


def _write_complete(descriptor: int, payload: bytes, *, path: _Path) -> None:
    """Write every prepared byte to the unpublished temporary file."""
    offset = 0
    while offset < len(payload):
        remaining = payload[offset:]
        try:
            written = _write_fd(descriptor, remaining)
        except OSError as exc:
            raise _AtomicPublicationError(
                f"cannot write complete publication temp for {path}: {exc}"
            ) from exc
        if written <= 0 or written > len(remaining):
            raise _AtomicPublicationError(
                f"invalid temporary write for {path}: wrote {written} of "
                f"{len(remaining)} remaining bytes"
            )
        offset += written


def _existing_bytes_match(path: _Path, payload: bytes) -> bool | None:
    """Return exact-byte equality, or ``None`` when the final name is absent."""
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0) | getattr(_os, "O_NOFOLLOW", 0)
    try:
        descriptor = _open_fd(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _AtomicPublicationError(
            f"cannot safely inspect immutable publication target {path}: {exc}"
        ) from exc

    active_error: BaseException | None = None
    try:
        try:
            metadata = _fstat_fd(descriptor)
        except OSError as exc:
            raise _AtomicPublicationError(
                f"cannot inspect immutable publication target {path}: {exc}"
            ) from exc
        if not _stat.S_ISREG(metadata.st_mode):
            raise _AtomicPublicationError(
                f"immutable publication target is not a regular file: {path}"
            )
        if metadata.st_size != len(payload):
            return False

        offset = 0
        while offset < len(payload):
            try:
                observed = _read_fd(
                    descriptor,
                    min(_READ_CHUNK_BYTES, len(payload) - offset),
                )
            except OSError as exc:
                raise _AtomicPublicationError(
                    f"cannot read immutable publication target {path}: {exc}"
                ) from exc
            if not observed or observed != payload[offset : offset + len(observed)]:
                return False
            offset += len(observed)
        try:
            return _read_fd(descriptor, 1) == b""
        except OSError as exc:
            raise _AtomicPublicationError(
                f"cannot finish reading immutable publication target {path}: {exc}"
            ) from exc
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        try:
            _close_fd(descriptor)
        except OSError as exc:
            if active_error is None:
                raise _AtomicPublicationError(
                    f"cannot close immutable publication target {path}: {exc}"
                ) from exc


def _sync_parent(parent: _Path) -> None:
    """Fsync the directory containing the final publication name."""
    flags = (
        _os.O_RDONLY
        | getattr(_os, "O_CLOEXEC", 0)
        | getattr(_os, "O_DIRECTORY", 0)
    )
    try:
        descriptor = _open_fd(parent, flags)
    except OSError as exc:
        raise _AtomicPublicationError(
            f"cannot open publication parent directory {parent}: {exc}"
        ) from exc

    sync_error: _AtomicPublicationError | None = None
    try:
        try:
            _sync_fd(descriptor)
        except OSError as exc:
            sync_error = _AtomicPublicationError(
                f"cannot fsync publication parent directory {parent}: {exc}"
            )
            sync_error.__cause__ = exc
    finally:
        try:
            _close_fd(descriptor)
        except OSError as exc:
            if sync_error is None:
                sync_error = _AtomicPublicationError(
                    f"cannot close publication parent directory {parent}: {exc}"
                )
                sync_error.__cause__ = exc
    if sync_error is not None:
        raise sync_error


def _cleanup_temp(
    descriptor: int | None,
    temporary: _Path | None,
    *,
    suppress_errors: bool,
) -> _AtomicPublicationError | None:
    """Close/unlink an unpublished name and optionally report cleanup failure."""
    cleanup_error: _AtomicPublicationError | None = None
    if descriptor is not None:
        try:
            _close_fd(descriptor)
        except OSError as exc:
            cleanup_error = _AtomicPublicationError(
                f"cannot close publication temp descriptor: {exc}"
            )
            cleanup_error.__cause__ = exc
    if temporary is not None:
        try:
            _unlink_file(temporary)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if cleanup_error is None:
                cleanup_error = _AtomicPublicationError(
                    f"cannot remove publication temp {temporary}: {exc}"
                )
                cleanup_error.__cause__ = exc
    if suppress_errors:
        return None
    return cleanup_error
