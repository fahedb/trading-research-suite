"""Crash-safe immutable storage for reviewed canonical artifact bytes.

The public API owns ``cache/features`` below an explicit runs root. Reviewed
composition-root code may reuse the same hardened machinery through a closed
private namespace allowlist. Every path component is opened relative to a
pinned directory descriptor with symlink following disabled. Publication
writes a private same-directory temporary file, synchronizes it, links it under
an absent final content-hash name, synchronizes the identity directory, and
removes only that private temporary name.
"""

from __future__ import annotations

import fcntl as _fcntl
import hashlib as _hashlib
import os as _os
import re as _re
import stat as _stat
from pathlib import Path as _Path
from typing import Protocol as _Protocol

from trading_research.artifacts.container import (
    _ArtifactContainerError,
    _decode_feature_artifact,
)
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.errors import TradingResearchError as _TradingResearchError

__all__ = ["ArtifactStore"]

_IDENTITY_KEY = _re.compile(r"^[0-9a-f]{64}$")
_FINAL_NAME = _re.compile(r"^([0-9a-f]{64})\.npz$")
_TEMP_PREFIX = ".feature-artifact-"
_TEMP_SUFFIX = ".tmp"
_READ_CHUNK_BYTES = 64 * 1024
_PRIVATE_NAMESPACES = frozenset({"fitted-states"})

_DIRECTORY_FLAGS = (
    _os.O_RDONLY
    | getattr(_os, "O_CLOEXEC", 0)
    | getattr(_os, "O_DIRECTORY", 0)
    | getattr(_os, "O_NOFOLLOW", 0)
)
_READ_FLAGS = (
    _os.O_RDONLY
    | getattr(_os, "O_CLOEXEC", 0)
    | getattr(_os, "O_NOFOLLOW", 0)
    | getattr(_os, "O_NONBLOCK", 0)
)
_TEMP_FLAGS = (
    _os.O_WRONLY
    | _os.O_CREAT
    | _os.O_EXCL
    | getattr(_os, "O_CLOEXEC", 0)
    | getattr(_os, "O_NOFOLLOW", 0)
)

# Bound once so every durability step remains independently fault-injectable.
_close_fd = _os.close
_fstat_fd = _os.fstat
_list_dir = _os.listdir
_link_at = _os.link
_lock_fd = _fcntl.flock
_make_dir_at = _os.mkdir
_open_fd = _os.open
_random_bytes = _os.urandom
_read_fd = _os.read
_sync_fd = _os.fsync
_unlink_at = _os.unlink
_write_fd = _os.write


class _ArtifactStoreError(_TradingResearchError):
    """An artifact cache operation could not preserve its contract."""


class _ArtifactCorruptionError(_ArtifactStoreError):
    """A visible cache entry is malformed, unsafe, or hash-inconsistent."""


class _ArtifactConflictError(_ArtifactStoreError):
    """One semantic identity has more than one possible immutable output."""


class _ArtifactPublicationError(_ArtifactStoreError):
    """An immutable publication or durability step failed."""


class _ArtifactValidator(_Protocol):
    def __call__(self, payload: bytes, *, expected_identity_key: str) -> object:
        """Validate one exact identity-bound artifact payload."""
        ...


class ArtifactStore:
    """Filesystem-backed immutable artifact cache below one runs path."""

    __slots__ = ("_runs_root",)

    def __init__(self, runs_root: _Path) -> None:
        """Snapshot one concrete absolute runs root without touching it."""
        if not isinstance(runs_root, _Path):
            raise _ContractViolationError(
                f"runs_root must be a pathlib.Path, got {type(runs_root).__name__}"
            )
        if not runs_root.is_absolute() or runs_root == _Path(runs_root.anchor):
            raise _ContractViolationError(
                "runs_root must name a concrete absolute directory below the filesystem root"
            )
        if ".." in runs_root.parts:
            raise _ContractViolationError("runs_root must not contain parent-directory traversal")
        self._runs_root = _Path(*runs_root.parts)

    def get(self, identity_key: str) -> bytes | None:
        """Return one fully verified entry, or ``None`` only for a true miss."""
        key = _validated_identity_key(identity_key)
        identity_fd = _open_identity_directory(self._runs_root, key=key, create=False)
        if identity_fd is None:
            return None
        active_error: BaseException | None = None
        try:
            _acquire_lock(identity_fd, exclusive=False, identity_key=key)
            try:
                names = _final_names(identity_fd, identity_key=key)
                if not names:
                    return None
                if len(names) != 1:
                    raise _ArtifactConflictError(
                        f"identity {key} has {len(names)} immutable feature artifacts"
                    )
                return _verified_final_bytes(identity_fd, names[0], identity_key=key)
            finally:
                _release_lock(identity_fd, identity_key=key)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            _close_directory(identity_fd, active_error=active_error, label=f"identity {key}")

    def put(self, identity_key: str, payload: bytes) -> str:
        """Publish canonical bytes once and return their lowercase SHA-256."""
        key = _validated_identity_key(identity_key)
        if type(payload) is not bytes:
            raise _ContractViolationError(
                f"artifact payload must be exact bytes, got {type(payload).__name__}"
            )

        # Parsing happens before the first filesystem operation.  A caller can
        # never create cache directories for malformed or misbound bytes.
        _decode_feature_artifact(payload, expected_identity_key=key)
        content_hash = _hashlib.sha256(payload).hexdigest()
        final_name = f"{content_hash}.npz"

        identity_fd = _open_identity_directory(self._runs_root, key=key, create=True)
        if identity_fd is None:  # pragma: no cover - create=True makes this impossible
            raise AssertionError("identity directory creation returned no descriptor")
        active_error: BaseException | None = None
        try:
            _acquire_lock(identity_fd, exclusive=True, identity_key=key)
            try:
                names = _final_names(identity_fd, identity_key=key)
                if len(names) > 1:
                    raise _ArtifactConflictError(
                        f"identity {key} has {len(names)} immutable feature artifacts"
                    )
                if names:
                    existing = _verified_final_bytes(identity_fd, names[0], identity_key=key)
                    if names[0] == final_name and existing == payload:
                        _sync_directory(identity_fd, label=f"identity {key}")
                        return content_hash
                    raise _ArtifactConflictError(
                        f"identity {key} already has different immutable content {names[0]!r}"
                    )

                _publish_locked(
                    identity_fd,
                    identity_key=key,
                    final_name=final_name,
                    payload=payload,
                )
                settled = _final_names(identity_fd, identity_key=key)
                if settled != (final_name,):
                    raise _ArtifactConflictError(
                        f"identity {key} did not settle to exactly {final_name!r}: {settled}"
                    )
                published = _verified_final_bytes(identity_fd, final_name, identity_key=key)
                if published != payload:
                    raise _ArtifactCorruptionError(
                        f"published artifact {final_name!r} differs from the supplied bytes"
                    )
                return content_hash
            finally:
                _release_lock(identity_fd, identity_key=key)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            _close_directory(identity_fd, active_error=active_error, label=f"identity {key}")

    def _get_namespaced(
        self,
        identity_key: str,
        *,
        namespace: str,
        validate: _ArtifactValidator,
    ) -> bytes | None:
        """Private reuse of the immutable store for a reviewed artifact namespace."""
        key = _validated_identity_key(identity_key)
        checked_namespace = _validated_private_namespace(namespace)
        identity_fd = _open_namespaced_identity_directory(
            self._runs_root,
            namespace=checked_namespace,
            key=key,
            create=False,
        )
        if identity_fd is None:
            return None
        active_error: BaseException | None = None
        try:
            _acquire_lock(identity_fd, exclusive=False, identity_key=key)
            try:
                names = _final_names(identity_fd, identity_key=key)
                if not names:
                    return None
                if len(names) > 1:
                    raise _ArtifactConflictError(
                        f"identity {key} has {len(names)} immutable {checked_namespace} artifacts"
                    )
                return _verified_namespaced_final_bytes(
                    identity_fd,
                    names[0],
                    identity_key=key,
                    validate=validate,
                )
            finally:
                _release_lock(identity_fd, identity_key=key)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            _close_directory(identity_fd, active_error=active_error, label=f"identity {key}")

    def _put_namespaced(
        self,
        identity_key: str,
        payload: bytes,
        *,
        namespace: str,
        validate: _ArtifactValidator,
    ) -> str:
        """Private publication through the same crash-safe immutable store."""
        key = _validated_identity_key(identity_key)
        checked_namespace = _validated_private_namespace(namespace)
        if type(payload) is not bytes:
            raise _ContractViolationError(
                f"artifact payload must be exact bytes, got {type(payload).__name__}"
            )
        validate(payload, expected_identity_key=key)
        content_hash = _hashlib.sha256(payload).hexdigest()
        final_name = f"{content_hash}.npz"
        identity_fd = _open_namespaced_identity_directory(
            self._runs_root,
            namespace=checked_namespace,
            key=key,
            create=True,
        )
        if identity_fd is None:  # pragma: no cover - create=True makes this impossible
            raise AssertionError("identity directory creation returned no descriptor")
        active_error: BaseException | None = None
        try:
            _acquire_lock(identity_fd, exclusive=True, identity_key=key)
            try:
                names = _final_names(identity_fd, identity_key=key)
                if len(names) > 1:
                    raise _ArtifactConflictError(
                        f"identity {key} has {len(names)} immutable {checked_namespace} artifacts"
                    )
                if names:
                    existing = _verified_namespaced_final_bytes(
                        identity_fd,
                        names[0],
                        identity_key=key,
                        validate=validate,
                    )
                    if names[0] == final_name and existing == payload:
                        _sync_directory(identity_fd, label=f"identity {key}")
                        return content_hash
                    raise _ArtifactConflictError(
                        f"identity {key} already has different immutable content {names[0]!r}"
                    )
                _publish_namespaced_locked(
                    identity_fd,
                    identity_key=key,
                    final_name=final_name,
                    payload=payload,
                    validate=validate,
                )
                settled = _final_names(identity_fd, identity_key=key)
                if settled != (final_name,):
                    raise _ArtifactConflictError(
                        f"identity {key} did not settle to exactly {final_name!r}: {settled}"
                    )
                published = _verified_namespaced_final_bytes(
                    identity_fd,
                    final_name,
                    identity_key=key,
                    validate=validate,
                )
                if published != payload:
                    raise _ArtifactCorruptionError(
                        f"published artifact {final_name!r} differs from the supplied bytes"
                    )
                return content_hash
            finally:
                _release_lock(identity_fd, identity_key=key)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            _close_directory(identity_fd, active_error=active_error, label=f"identity {key}")


def _validated_identity_key(value: object) -> str:
    if type(value) is not str or _IDENTITY_KEY.fullmatch(value) is None:
        raise _ContractViolationError(
            "artifact identity_key must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _validated_private_namespace(value: object) -> str:
    if type(value) is not str or value not in _PRIVATE_NAMESPACES:
        raise _ContractViolationError("artifact namespace is not a reviewed private namespace")
    return value


def _open_identity_directory(root: _Path, *, key: str, create: bool) -> int | None:
    current = _open_runs_root(root)
    for name in ("cache", "features", key):
        try:
            child = _open_child_directory(current, name=name)
        except BaseException as exc:
            _close_directory(current, active_error=exc, label="cache path")
            raise
        if child is None:
            if not create:
                _close_directory(current, active_error=None, label=f"parent of {name!r}")
                return None
            try:
                child = _create_child_directory(current, name=name)
            except BaseException as exc:
                _close_directory(current, active_error=exc, label="cache path")
                raise
        elif create:
            # A retry after an earlier parent-fsync failure must make the
            # already-present directory entry durable before proceeding.
            try:
                _sync_directory(current, label=f"parent of {name!r}")
            except BaseException as exc:
                _close_directory(child, active_error=exc, label=f"cache component {name!r}")
                _close_directory(current, active_error=exc, label="cache path")
                raise

        parent = current
        current = child
        try:
            _close_directory(parent, active_error=None, label=f"parent of {name!r}")
        except BaseException as exc:
            # Ownership has already moved to ``current``.  Retaining that
            # descriptor here would leak it whenever closing the parent fails.
            _close_directory(current, active_error=exc, label=f"cache component {name!r}")
            raise
    return current


def _open_namespaced_identity_directory(
    root: _Path,
    *,
    namespace: str,
    key: str,
    create: bool,
) -> int | None:
    checked_namespace = _validated_private_namespace(namespace)
    current = _open_runs_root(root)
    for name in ("cache", checked_namespace, key):
        try:
            child = _open_child_directory(current, name=name)
        except BaseException as exc:
            _close_directory(current, active_error=exc, label="cache path")
            raise
        if child is None:
            if not create:
                _close_directory(current, active_error=None, label=f"parent of {name!r}")
                return None
            try:
                child = _create_child_directory(current, name=name)
            except BaseException as exc:
                _close_directory(current, active_error=exc, label="cache path")
                raise
        elif create:
            try:
                _sync_directory(current, label=f"parent of {name!r}")
            except BaseException as exc:
                _close_directory(child, active_error=exc, label=f"cache component {name!r}")
                _close_directory(current, active_error=exc, label="cache path")
                raise
        parent = current
        current = child
        try:
            _close_directory(parent, active_error=None, label=f"parent of {name!r}")
        except BaseException as exc:
            _close_directory(current, active_error=exc, label=f"cache component {name!r}")
            raise
    return current


def _open_runs_root(root: _Path) -> int:
    try:
        current = _open_fd(root.anchor, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise _ArtifactStoreError(
            f"cannot open filesystem root for runs path {root}: {exc}"
        ) from exc
    for name in root.parts[1:]:
        try:
            child = _open_child_directory(current, name=name)
        except BaseException as exc:
            _close_directory(current, active_error=exc, label=f"runs_root {root}")
            raise
        if child is None:
            error = _ArtifactStoreError(f"runs_root does not exist: {root}")
            _close_directory(current, active_error=error, label=f"runs_root {root}")
            raise error
        parent = current
        current = child
        try:
            _close_directory(parent, active_error=None, label=f"ancestor of runs_root {root}")
        except BaseException as exc:
            _close_directory(current, active_error=exc, label=f"runs_root {root}")
            raise
    return current


def _open_child_directory(parent_fd: int, *, name: str) -> int | None:
    try:
        return _open_fd(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _ArtifactStoreError(
            f"cache path component {name!r} is not a safe plain directory: {exc}"
        ) from exc


def _create_child_directory(parent_fd: int, *, name: str) -> int:
    try:
        _make_dir_at(name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise _ArtifactPublicationError(
            f"cannot create cache directory component {name!r}: {exc}"
        ) from exc
    child = _open_child_directory(parent_fd, name=name)
    if child is None:
        raise _ArtifactPublicationError(
            f"cache directory component {name!r} disappeared after creation"
        )
    try:
        _sync_directory(parent_fd, label=f"parent of {name!r}")
    except BaseException as exc:
        _close_directory(child, active_error=exc, label=f"new cache component {name!r}")
        raise
    return child


def _acquire_lock(descriptor: int, *, exclusive: bool, identity_key: str) -> None:
    operation = _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH
    try:
        _lock_fd(descriptor, operation)
    except OSError as exc:
        raise _ArtifactStoreError(f"cannot lock artifact identity {identity_key}: {exc}") from exc


def _release_lock(descriptor: int, *, identity_key: str) -> None:
    try:
        _lock_fd(descriptor, _fcntl.LOCK_UN)
    except OSError as exc:
        raise _ArtifactStoreError(f"cannot unlock artifact identity {identity_key}: {exc}") from exc


def _final_names(identity_fd: int, *, identity_key: str) -> tuple[str, ...]:
    try:
        entries = _list_dir(identity_fd)
    except OSError as exc:
        raise _ArtifactStoreError(
            f"cannot inspect artifact identity {identity_key}: {exc}"
        ) from exc
    finals: list[str] = []
    for name in entries:
        if not name.endswith(".npz"):
            continue
        if _FINAL_NAME.fullmatch(name) is None:
            raise _ArtifactCorruptionError(
                f"identity {identity_key} contains malformed final artifact name {name!r}"
            )
        finals.append(name)
    return tuple(sorted(finals))


def _verified_final_bytes(identity_fd: int, name: str, *, identity_key: str) -> bytes:
    match = _FINAL_NAME.fullmatch(name)
    if match is None:
        raise _ArtifactCorruptionError(f"malformed feature-artifact name {name!r}")
    expected_hash = match.group(1)
    payload = _read_regular_file(identity_fd, name=name, identity_key=identity_key)
    observed_hash = _hashlib.sha256(payload).hexdigest()
    if observed_hash != expected_hash:
        raise _ArtifactCorruptionError(
            f"artifact {name!r} content hashes to {observed_hash}, not its filename"
        )
    try:
        _decode_feature_artifact(payload, expected_identity_key=identity_key)
    except _ArtifactContainerError as exc:
        raise _ArtifactCorruptionError(f"artifact {name!r} has invalid container bytes") from exc
    return payload


def _verified_namespaced_final_bytes(
    identity_fd: int,
    name: str,
    *,
    identity_key: str,
    validate: _ArtifactValidator,
) -> bytes:
    match = _FINAL_NAME.fullmatch(name)
    if match is None:
        raise _ArtifactCorruptionError(f"malformed namespaced-artifact name {name!r}")
    expected_hash = match.group(1)
    payload = _read_regular_file(identity_fd, name=name, identity_key=identity_key)
    observed_hash = _hashlib.sha256(payload).hexdigest()
    if observed_hash != expected_hash:
        raise _ArtifactCorruptionError(
            f"artifact {name!r} content hashes to {observed_hash}, not its filename"
        )
    try:
        validate(payload, expected_identity_key=identity_key)
    except _ContractViolationError as exc:
        raise _ArtifactCorruptionError(
            f"artifact {name!r} has invalid namespaced container bytes"
        ) from exc
    return payload


def _read_regular_file(identity_fd: int, *, name: str, identity_key: str) -> bytes:
    try:
        descriptor = _open_fd(name, _READ_FLAGS, dir_fd=identity_fd)
    except OSError as exc:
        raise _ArtifactCorruptionError(
            f"cannot safely open artifact {name!r} for identity {identity_key}: {exc}"
        ) from exc
    active_error: BaseException | None = None
    try:
        try:
            metadata = _fstat_fd(descriptor)
        except OSError as exc:
            raise _ArtifactCorruptionError(f"cannot inspect artifact {name!r}: {exc}") from exc
        if not _stat.S_ISREG(metadata.st_mode):
            raise _ArtifactCorruptionError(f"artifact {name!r} is not a regular file")
        chunks: list[bytes] = []
        while True:
            try:
                chunk = _read_fd(descriptor, _READ_CHUNK_BYTES)
            except OSError as exc:
                raise _ArtifactCorruptionError(f"cannot read artifact {name!r}: {exc}") from exc
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        _close_file(descriptor, active_error=active_error, label=f"artifact {name!r}")


def _publish_locked(
    identity_fd: int,
    *,
    identity_key: str,
    final_name: str,
    payload: bytes,
) -> None:
    temporary_name, descriptor = _create_private_temp(identity_fd, final_name=final_name)
    temporary_exists = True
    final_linked = False
    active_error: BaseException | None = None
    try:
        _write_complete(descriptor, payload=payload, identity_key=identity_key)
        try:
            _sync_fd(descriptor)
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot fsync complete artifact temp for identity {identity_key}: {exc}"
            ) from exc
        # Relinquish ownership before close(2).  On Linux a close that reports
        # an error may still have released the descriptor; retrying the same
        # integer from ``finally`` could then close an unrelated reused FD.
        closing_descriptor = descriptor
        descriptor = -1
        _close_file(closing_descriptor, active_error=None, label="artifact temp")

        try:
            _link_at(
                temporary_name,
                final_name,
                src_dir_fd=identity_fd,
                dst_dir_fd=identity_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            existing = _verified_final_bytes(
                identity_fd,
                final_name,
                identity_key=identity_key,
            )
            if existing != payload:
                raise _ArtifactConflictError(
                    f"concurrent artifact publication differs at {final_name!r}"
                ) from exc
            final_linked = True
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot atomically publish artifact {final_name!r}: {exc}"
            ) from exc
        else:
            final_linked = True

        _sync_directory(identity_fd, label=f"identity {identity_key}")
        try:
            _unlink_at(temporary_name, dir_fd=identity_fd)
        except OSError:
            # Publication is already complete and durable. Cleanup is
            # best-effort; a private temp cannot be observed as a cache entry.
            pass
        else:
            temporary_exists = False
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        if descriptor >= 0:
            _close_file(descriptor, active_error=active_error, label="artifact temp")
        if temporary_exists:
            try:
                _unlink_at(temporary_name, dir_fd=identity_fd)
            except FileNotFoundError:
                pass
            except OSError:
                # Cleanup is best effort while another publication error is
                # active.  The private temp remains inert to every lookup.
                if active_error is None and not final_linked:
                    raise


def _publish_namespaced_locked(
    identity_fd: int,
    *,
    identity_key: str,
    final_name: str,
    payload: bytes,
    validate: _ArtifactValidator,
) -> None:
    temporary_name, descriptor = _create_private_temp(identity_fd, final_name=final_name)
    temporary_exists = True
    final_linked = False
    active_error: BaseException | None = None
    try:
        _write_complete(descriptor, payload=payload, identity_key=identity_key)
        try:
            _sync_fd(descriptor)
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot fsync complete artifact temp for identity {identity_key}: {exc}"
            ) from exc
        closing_descriptor = descriptor
        descriptor = -1
        _close_file(closing_descriptor, active_error=None, label="artifact temp")
        try:
            _link_at(
                temporary_name,
                final_name,
                src_dir_fd=identity_fd,
                dst_dir_fd=identity_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            existing = _verified_namespaced_final_bytes(
                identity_fd,
                final_name,
                identity_key=identity_key,
                validate=validate,
            )
            if existing != payload:
                raise _ArtifactConflictError(
                    f"concurrent artifact publication differs at {final_name!r}"
                ) from exc
            final_linked = True
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot atomically publish artifact {final_name!r}: {exc}"
            ) from exc
        else:
            final_linked = True
        _sync_directory(identity_fd, label=f"namespaced identity {identity_key}")
        try:
            _unlink_at(temporary_name, dir_fd=identity_fd)
        except OSError:
            pass
        else:
            temporary_exists = False
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        if descriptor >= 0:
            _close_file(descriptor, active_error=active_error, label="artifact temp")
        if temporary_exists:
            try:
                _unlink_at(temporary_name, dir_fd=identity_fd)
            except FileNotFoundError:
                pass
            except OSError:
                if active_error is None and not final_linked:
                    raise


def _create_private_temp(identity_fd: int, *, final_name: str) -> tuple[str, int]:
    for _attempt in range(128):
        try:
            random_token = _random_bytes(16).hex()
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot generate private temp name for {final_name!r}: {exc}"
            ) from exc
        name = f"{_TEMP_PREFIX}{random_token}{_TEMP_SUFFIX}"
        try:
            descriptor = _open_fd(name, _TEMP_FLAGS, 0o600, dir_fd=identity_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot create same-directory temp for {final_name!r}: {exc}"
            ) from exc
        return name, descriptor
    raise _ArtifactPublicationError(
        f"cannot allocate a unique same-directory temp for {final_name!r}"
    )


def _write_complete(descriptor: int, *, payload: bytes, identity_key: str) -> None:
    offset = 0
    while offset < len(payload):
        remaining = payload[offset:]
        try:
            written = _write_fd(descriptor, remaining)
        except OSError as exc:
            raise _ArtifactPublicationError(
                f"cannot write artifact temp for identity {identity_key}: {exc}"
            ) from exc
        if written <= 0 or written > len(remaining):
            raise _ArtifactPublicationError(
                f"invalid artifact temp write for identity {identity_key}: "
                f"wrote {written} of {len(remaining)} bytes"
            )
        offset += written


def _sync_directory(descriptor: int, *, label: str) -> None:
    try:
        _sync_fd(descriptor)
    except OSError as exc:
        raise _ArtifactPublicationError(f"cannot fsync {label} directory: {exc}") from exc


def _close_file(descriptor: int, *, active_error: BaseException | None, label: str) -> None:
    try:
        _close_fd(descriptor)
    except OSError as exc:
        if active_error is None:
            raise _ArtifactStoreError(f"cannot close {label}: {exc}") from exc


def _close_directory(descriptor: int, *, active_error: BaseException | None, label: str) -> None:
    _close_file(descriptor, active_error=active_error, label=f"{label} directory")
