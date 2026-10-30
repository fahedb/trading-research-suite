"""Atomic construction of the disposable files-derived run index."""

from __future__ import annotations

import fcntl as _fcntl
import hashlib as _hashlib
import json as _json
import math as _math
import os as _os
import sqlite3 as _sqlite3
import stat as _stat
import struct as _struct
import time as _time
import typing as _typing
import uuid as _uuid
from contextlib import suppress as _suppress
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.experiments.runs import (
    _RunProjection,
    _RunProjectionScan,
    _scan_run_projections,
)
from trading_research.experiments.sweeps import _read_sweep_plan
from trading_research.index.schema import _SCHEMA_SQL, _SCHEMA_VERSION, _TABLES

if _typing.TYPE_CHECKING:
    from typing import Protocol as _Protocol

    class _Digest(_Protocol):
        def update(self, value: bytes) -> None: ...

__all__: list[str] = []

_BUSY_TIMEOUT_MS = 10_000
_DEFAULT_STALE_AFTER_NS = 60_000_000_000
_BUILD_BATCH_SIZE = 200
_INDEX_DIRECTORY = "index"
_INDEX_FILENAME = "runs.sqlite3"
_BUILD_LOCK = ".build.lock"
_GIT_COMMIT_LENGTHS = {40, 64}


class _IndexError(_TradingResearchError):
    """The derived index could not be built or trusted."""


class _IndexRebuildRequiredError(_IndexError):
    """The current index is absent, stale, corrupt, or has the wrong schema."""


@_dataclass(frozen=True, slots=True)
class _IndexBuildResult:
    _path: _Path
    _mode: str
    _generation: int
    _run_count: int
    _scan_error_count: int
    _sweep_count: int
    _source_token: str
    _built_unix_ns: int


def _rebuild_index(
    runs_root: _Path,
    *,
    builder_git_commit: str,
    incremental: bool,
    now_unix_ns: int | None = None,
    stale_after_ns: int = _DEFAULT_STALE_AFTER_NS,
) -> _IndexBuildResult:
    root = _validated_runs_root(runs_root)
    commit = _validated_git_commit(builder_git_commit)
    if type(incremental) is not bool:
        raise _IndexError("incremental must be an exact bool")
    built_unix_ns = _time.time_ns() if now_unix_ns is None else now_unix_ns
    if type(built_unix_ns) is not int or built_unix_ns <= 0:
        raise _IndexError("now_unix_ns must be an exact positive int")
    if type(stale_after_ns) is not int or stale_after_ns <= 0:
        raise _IndexError("stale_after_ns must be an exact positive int")

    index_directory = _open_index_directory(root)
    lock_fd = _open_build_lock(index_directory)
    try:
        _lock_builder(lock_fd)
        index_path = root / _INDEX_DIRECTORY / _INDEX_FILENAME
        source_token_before = _source_token(root)
        try:
            scan = _scan_run_projections(
                root,
                now_unix_ns=built_unix_ns,
                stale_after_ns=stale_after_ns,
                reconcile=False,
                tolerate_corruption=True,
            )
        except _TradingResearchError as exc:
            raise _IndexError(str(exc)) from exc
        sweeps = _read_sweep_rows(root)
        source_token_after = _source_token(root)
        if source_token_before != source_token_after:
            raise _IndexError("run or sweep files changed during index projection; retry rebuild")

        requested_mode = "incremental" if incremental else "full"
        temp_name = f".runs.{_uuid.uuid4().hex}.sqlite3.tmp"
        temp_path = root / _INDEX_DIRECTORY / temp_name
        generation = 1
        mode = requested_mode
        connection: _sqlite3.Connection | None = None
        try:
            if incremental and index_path.is_file():
                try:
                    generation = _copy_current_generation(index_path, temp_path) + 1
                    connection = _connect_staging(temp_path, create=False)
                    _validate_schema(connection)
                except (_sqlite3.Error, OSError, _IndexRebuildRequiredError):
                    _unlink_temp(temp_path)
                    connection = _connect_staging(temp_path, create=True)
                    _create_schema(connection)
                    generation = 1
                    mode = "full-recovery"
            else:
                connection = _connect_staging(temp_path, create=True)
                _create_schema(connection)
            _refresh_staging(
                connection,
                scan=scan,
                sweeps=sweeps,
                runs_root=root,
                builder_git_commit=commit,
                source_token=source_token_after,
                built_unix_ns=built_unix_ns,
                generation=generation,
            )
            _validate_staging(connection)
            connection.commit()
            connection.close()
            connection = None
            _fsync_file(temp_path)
            _publish_generation(index_directory, temp_name=temp_name)
        except BaseException:
            if connection is not None:
                connection.close()
            _unlink_temp(temp_path)
            raise
        return _IndexBuildResult(
            index_path,
            mode,
            generation,
            len(scan._projections),
            len(scan._errors),
            len(sweeps),
            source_token_after,
            built_unix_ns,
        )
    finally:
        _unlock_and_close(lock_fd)
        _os.close(index_directory)


def _validated_runs_root(value: object) -> _Path:
    if not isinstance(value, _Path) or not value.is_absolute():
        raise _IndexError("runs_root must be an absolute Path")
    try:
        value.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = value.lstat()
    except OSError as exc:
        raise _IndexError("runs_root cannot be opened") from exc
    if not _stat.S_ISDIR(metadata.st_mode) or _stat.S_ISLNK(metadata.st_mode):
        raise _IndexError("runs_root must be a concrete directory")
    return value.resolve(strict=True)


def _existing_runs_root(value: object) -> _Path:
    """Validate a read target without creating any directory."""
    if not isinstance(value, _Path) or not value.is_absolute():
        raise _IndexError("runs_root must be an absolute Path")
    try:
        metadata = value.lstat()
    except OSError as exc:
        raise _IndexError("runs_root does not exist or cannot be opened") from exc
    if not _stat.S_ISDIR(metadata.st_mode) or _stat.S_ISLNK(metadata.st_mode):
        raise _IndexError("runs_root must be a concrete directory")
    return value.resolve(strict=True)


def _validated_git_commit(value: object) -> str:
    if (
        type(value) is not str
        or len(value) not in _GIT_COMMIT_LENGTHS
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _IndexError("builder_git_commit must be a lowercase Git digest")
    return value


def _open_index_directory(runs_root: _Path) -> int:
    path = runs_root / _INDEX_DIRECTORY
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        metadata = path.lstat()
        if not _stat.S_ISDIR(metadata.st_mode) or _stat.S_ISLNK(metadata.st_mode):
            raise _IndexError("index path must be a concrete directory")
        return _os.open(
            path,
            _os.O_RDONLY
            | _os.O_DIRECTORY
            | getattr(_os, "O_CLOEXEC", 0)
            | getattr(_os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise _IndexError("cannot create or open index directory") from exc


def _open_build_lock(index_directory_fd: int) -> int:
    try:
        descriptor = _os.open(
            _BUILD_LOCK,
            _os.O_RDWR
            | _os.O_CREAT
            | getattr(_os, "O_CLOEXEC", 0)
            | getattr(_os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=index_directory_fd,
        )
    except OSError as exc:
        raise _IndexError("cannot open index build lock") from exc
    try:
        metadata = _os.fstat(descriptor)
    except OSError as exc:
        _os.close(descriptor)
        raise _IndexError("cannot inspect index build lock") from exc
    if not _stat.S_ISREG(metadata.st_mode):
        _os.close(descriptor)
        raise _IndexError("index build lock must be a regular file")
    return descriptor


def _lock_builder(lock_fd: int) -> None:
    try:
        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
    except OSError as exc:
        raise _IndexError("cannot acquire index build lock") from exc


def _unlock_and_close(lock_fd: int) -> None:
    try:
        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
    finally:
        _os.close(lock_fd)


def _connect_staging(path: _Path, *, create: bool) -> _sqlite3.Connection:
    if create:
        try:
            descriptor = _os.open(
                path,
                _os.O_RDWR
                | _os.O_CREAT
                | _os.O_EXCL
                | getattr(_os, "O_CLOEXEC", 0)
                | getattr(_os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            raise _IndexError("cannot create private index generation") from exc
        _os.close(descriptor)
    connection = _sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1_000)
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_schema(connection: _sqlite3.Connection) -> None:
    try:
        connection.executescript(_SCHEMA_SQL)
    except _sqlite3.Error as exc:
        raise _IndexError("cannot create index schema") from exc


def _copy_current_generation(source_path: _Path, target_path: _Path) -> int:
    source = _open_readonly(source_path)
    target: _sqlite3.Connection | None = None
    try:
        _validate_schema(source)
        generation = _meta_int(source, "generation")
        target = _connect_staging(target_path, create=True)
        source.backup(target)
        target.commit()
        return generation
    finally:
        source.close()
        if target is not None:
            target.close()


def _open_readonly(path: _Path) -> _sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise _IndexRebuildRequiredError("index is missing or unsafe; run a full rebuild")
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = _sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1_000)
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _validate_schema(connection: _sqlite3.Connection) -> None:
    try:
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if integrity != ("ok",):
            raise _IndexRebuildRequiredError("index integrity check failed; run a full rebuild")
        observed = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?",
                ("table",),
            )
            if type(row[0]) is str and not row[0].startswith("sqlite_")
        }
        if observed != set(_TABLES):
            raise _IndexRebuildRequiredError("index schema differs; run a full rebuild")
        schema_version = connection.execute(
            "SELECT value FROM index_meta WHERE key = ?",
            ("schema_version",),
        ).fetchone()
    except _sqlite3.Error as exc:
        raise _IndexRebuildRequiredError("index is corrupt; run a full rebuild") from exc
    if schema_version != (_SCHEMA_VERSION,):
        raise _IndexRebuildRequiredError("index schema version differs; run a full rebuild")


def _meta_int(connection: _sqlite3.Connection, key: str) -> int:
    row = connection.execute(
        "SELECT value FROM index_meta WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None or len(row) != 1 or type(row[0]) is not str:
        raise _IndexRebuildRequiredError(f"index metadata {key!r} is missing")
    try:
        value = int(row[0])
    except ValueError as exc:
        raise _IndexRebuildRequiredError(f"index metadata {key!r} is malformed") from exc
    if value < 1:
        raise _IndexRebuildRequiredError(f"index metadata {key!r} is malformed")
    return value


def _refresh_staging(
    connection: _sqlite3.Connection,
    *,
    scan: _RunProjectionScan,
    sweeps: tuple[dict[str, object], ...],
    runs_root: _Path,
    builder_git_commit: str,
    source_token: str,
    built_unix_ns: int,
    generation: int,
) -> None:
    run_ids = tuple(projection._record._run_id for projection in scan._projections)
    _delete_absent_runs(connection, run_ids)
    for offset in range(0, len(scan._projections), _BUILD_BATCH_SIZE):
        for projection in scan._projections[offset : offset + _BUILD_BATCH_SIZE]:
            if not _terminal_projection_is_unchanged(connection, projection):
                _upsert_projection(connection, projection)
        connection.commit()
    connection.execute("DELETE FROM scan_error")
    connection.executemany(
        "INSERT INTO scan_error(run_dir_name, error_kind, detail) VALUES (?, ?, ?)",
        tuple((error._run_id, error._error_kind, error._detail) for error in scan._errors),
    )
    connection.execute("DELETE FROM sweep_member")
    connection.execute("DELETE FROM sweep")
    for sweep in sweeps:
        _insert_sweep(connection, sweep)
    connection.execute("DELETE FROM index_meta")
    connection.executemany(
        "INSERT INTO index_meta(key, value) VALUES (?, ?)",
        (
            ("schema_version", _SCHEMA_VERSION),
            ("built_unix_ns", str(built_unix_ns)),
            ("generation", str(generation)),
            ("runs_root", str(runs_root)),
            ("builder_git_commit", builder_git_commit),
            ("source_token", source_token),
        ),
    )
    connection.commit()


def _terminal_projection_is_unchanged(
    connection: _sqlite3.Connection,
    projection: _RunProjection,
) -> bool:
    if projection._record._state.value not in {"COMPLETE", "FAILED", "INTERRUPTED"}:
        return False
    row = connection.execute(
        "SELECT record_digest, attempt_number FROM run WHERE run_id = ?",
        (projection._record._run_id,),
    ).fetchone()
    return bool(
        row
        == (
            projection._record_digest,
            projection._record._attempt_number,
        )
    )


def _delete_absent_runs(connection: _sqlite3.Connection, run_ids: tuple[str, ...]) -> None:
    if not run_ids:
        connection.execute("DELETE FROM run")
        return
    connection.execute("CREATE TEMP TABLE current_run_id(run_id TEXT PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO current_run_id(run_id) VALUES (?)",
        ((run_id,) for run_id in run_ids),
    )
    connection.execute("DELETE FROM run WHERE run_id NOT IN (SELECT run_id FROM current_run_id)")
    connection.execute("DROP TABLE current_run_id")


def _upsert_projection(connection: _sqlite3.Connection, projection: _RunProjection) -> None:
    record = projection._record
    state = projection._state
    identity = projection._identity
    config = projection._resolved_config
    dataset_value = config.get("dataset")
    dataset = dataset_value if type(dataset_value) is dict else {}
    manifest = projection._manifest
    connection.execute("DELETE FROM run WHERE run_id = ?", (record._run_id,))
    connection.execute(
        """
        INSERT INTO run(
          run_id, experiment_fingerprint, attempt_number, state, canonical,
          outcome_class, evaluation_count, policy_execution_count,
          oos_touched, lockbox_touched, declared_lockbox_evaluation,
          cost_semantics_unverified, binding_id, binding_digest,
          execution_price_model, dataset_id, dataset_manifest_sha256,
          dataset_role, platform_git_commit, platform_git_dirty,
          platform_diff_sha256, dependency_lock_sha256, created_unix_ns,
          started_unix_ns, finished_unix_ns, failure_reason,
          heartbeat_age_ns, heartbeat_stale, resolved_config_json, record_digest
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?
        )
        """,
        (
            record._run_id,
            record._experiment_fingerprint,
            record._attempt_number,
            record._state.value,
            int(record._canonical),
            None if record._outcome_class is None else record._outcome_class.value,
            record._evaluation_count,
            record._policy_execution_count,
            int(record._oos_touched),
            int(record._lockbox_touched),
            int(identity._declared_lockbox_evaluation),
            int(False if manifest is None else manifest.cost_semantics_unverified),
            None if manifest is None else manifest.binding_id,
            None if manifest is None else manifest.binding_digest,
            None if manifest is None else manifest.execution_price_model,
            dataset.get("dataset_id"),
            dataset.get("manifest_sha256"),
            dataset.get("role"),
            identity._platform_identity.git_commit,
            int(identity._platform_identity.git_dirty),
            identity._platform_identity.diff_sha256,
            identity._dependency_lock_sha256,
            state._created_unix_ns,
            state._started_unix_ns,
            state._finished_unix_ns,
            state._failure_reason,
            record._heartbeat_age_ns,
            int(record._heartbeat_stale),
            _canonical_json(config),
            projection._record_digest,
        ),
    )
    connection.executemany(
        """
        INSERT INTO access_audit_ref(run_id, ordinal, session_id, audit_event_id)
        VALUES (?, ?, ?, ?)
        """,
        tuple(
            (record._run_id, ordinal, ref._session_id, ref._audit_event_id)
            for ordinal, ref in enumerate(state._access_audit_refs)
        ),
    )
    if projection._inventory is not None:
        _insert_inventory(connection, record._run_id, projection._inventory)


def _insert_inventory(
    connection: _sqlite3.Connection,
    run_id: str,
    inventory: dict[str, object],
) -> None:
    entries = _sequence(inventory.get("entries"), label="inventory entries")
    for entry_ordinal, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, label="inventory entry")
        plane_value = entry.get("plane", "A")
        if plane_value not in {"A", "B"}:
            raise _IndexError("validated inventory has an unknown plane")
        plane = plane_value
        evaluator = _mapping(entry.get("evaluator"), label="inventory evaluator")
        outcome = None if plane == "B" else _mapping(entry.get("outcome"), label="outcome")
        policy = None if plane == "A" else _mapping(entry.get("policy"), label="policy")
        connection.execute(
            """
            INSERT INTO evaluation(
              run_id, entry_ordinal, plane, fitted_state_identity,
              model_output_identity, evaluated_columns_json, outcome_plugin_id,
              outcome_version, outcome_params_json, horizon_rows,
              decision_delay_rows, policy_plugin_id, policy_version,
              policy_params_json, execution_spec_json, evaluator_plugin_id,
              evaluator_version, evaluator_params_json,
              comparison_tolerance_json, cost_semantics_unverified
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                entry_ordinal,
                plane,
                entry.get("fitted_state_identity"),
                entry.get("model_output_artifact_identity"),
                None if plane == "B" else _canonical_json(entry.get("evaluated_output_columns")),
                None if outcome is None else outcome.get("plugin_id"),
                None if outcome is None else outcome.get("version"),
                None if outcome is None else _canonical_json(outcome.get("params")),
                None if plane == "B" else entry.get("horizon_rows"),
                None if plane == "B" else entry.get("decision_delay_rows"),
                None if policy is None else policy.get("plugin_id"),
                None if policy is None else policy.get("version"),
                None if policy is None else _canonical_json(policy.get("params")),
                None if plane == "A" else _canonical_json(entry.get("execution_spec")),
                evaluator.get("plugin_id"),
                evaluator.get("version"),
                _canonical_json(evaluator.get("params")),
                _canonical_json(entry.get("comparison_tolerance")),
                int(bool(entry.get("cost_semantics_unverified", False))),
            ),
        )
        metrics = _sequence(entry.get("metrics"), label="inventory metrics")
        for raw_metric in metrics:
            metric = _mapping(raw_metric, label="inventory metric")
            bits = metric.get("value_float64_bits")
            if type(bits) is not str:
                raise _IndexError("validated metric bits are malformed")
            numeric = _struct.unpack(">d", bytes.fromhex(bits))[0]
            connection.execute(
                """
                INSERT INTO metric(
                  run_id, entry_ordinal, metric_name, value_real,
                  value_bits_hex, effective_samples
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    entry_ordinal,
                    metric.get("name"),
                    None if _math.isnan(numeric) else numeric,
                    bits,
                    metric.get("effective_samples"),
                ),
            )
    policies = _sequence(inventory.get("policy_executions"), label="policy executions")
    for ordinal, raw_policy in enumerate(policies):
        execution = _mapping(raw_policy, label="policy execution")
        policy = _mapping(execution.get("policy"), label="policy origin")
        shape = _mapping(execution.get("intent_shape"), label="intent shape")
        connection.execute(
            """
            INSERT INTO policy_execution(
              run_id, ordinal, plugin_id, version, params_json,
              intent_schema_id, intent_rows, first_key, last_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ordinal,
                policy.get("plugin_id"),
                policy.get("version"),
                _canonical_json(policy.get("params")),
                execution.get("intent_schema_id"),
                shape.get("rows"),
                shape.get("first_key"),
                shape.get("last_key"),
            ),
        )


def _read_sweep_rows(runs_root: _Path) -> tuple[dict[str, object], ...]:
    collection = runs_root / "sweeps"
    if not collection.exists():
        return ()
    if not collection.is_dir() or collection.is_symlink():
        raise _IndexError("sweeps path must be a concrete directory")
    rows: list[dict[str, object]] = []
    for child in sorted(collection.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.is_symlink():
            raise _IndexError(f"unexpected sweep collection entry {child.name!r}")
        try:
            plan = _read_sweep_plan(runs_root, child.name)
        except _TradingResearchError as exc:
            raise _IndexError(f"cannot read sweep {child.name!r}") from exc
        rows.append(plan._document)
    return tuple(rows)


def _insert_sweep(connection: _sqlite3.Connection, sweep: dict[str, object]) -> None:
    children = _sequence(sweep.get("children"), label="sweep children")
    duplicates = _sequence(
        sweep.get("duplicates_collapsed"),
        label="sweep collapsed duplicates",
    )
    connection.execute(
        """
        INSERT INTO sweep(
          sweep_id, spec_digest, git_commit, child_count, duplicate_count, created_unix_ns
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            sweep.get("sweep_id"),
            sweep.get("spec_digest"),
            _mapping(sweep.get("platform"), label="sweep platform").get("git_commit"),
            len(children),
            len(duplicates),
            sweep.get("created_unix_ns"),
        ),
    )
    for child in children:
        row = _mapping(child, label="sweep child")
        connection.execute(
            """
            INSERT INTO sweep_member(sweep_id, child_ordinal, experiment_fingerprint)
            VALUES (?, ?, ?)
            """,
            (sweep.get("sweep_id"), row.get("child_ordinal"), row.get("experiment_fingerprint")),
        )


def _source_token(runs_root: _Path) -> str:
    digest = _hashlib.sha256()
    runs = runs_root / "runs"
    if not runs.exists():
        digest.update(b"runs:absent\n")
    else:
        _token_walk(digest, runs_root=runs_root, collection=runs)
    sweeps = runs_root / "sweeps"
    if not sweeps.exists():
        digest.update(b"sweeps:absent\n")
    else:
        if not sweeps.is_dir() or sweeps.is_symlink():
            raise _IndexError("sweeps path must be a concrete directory")
        for child in sorted(sweeps.iterdir(), key=lambda path: path.name):
            if not child.is_dir() or child.is_symlink():
                raise _IndexError(f"unexpected sweep collection entry {child.name!r}")
            _token_path(digest, runs_root=runs_root, path=child, include_times=False)
            manifest = child / "sweep.json"
            _token_path(digest, runs_root=runs_root, path=manifest, include_times=True)
    return digest.hexdigest()


def _token_walk(
    digest: _Digest,
    *,
    runs_root: _Path,
    collection: _Path,
) -> None:
    if not collection.is_dir() or collection.is_symlink():
        raise _IndexError(f"{collection.name} path must be a concrete directory")
    for root_text, directories, files in _os.walk(collection, followlinks=False):
        directories.sort()
        files.sort()
        root = _Path(root_text)
        for name in (*directories, *files):
            _token_path(
                digest,
                runs_root=runs_root,
                path=root / name,
                include_times=True,
            )


def _token_path(
    digest: _Digest,
    *,
    runs_root: _Path,
    path: _Path,
    include_times: bool,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _IndexError("source token input disappeared") from exc
    if _stat.S_ISLNK(metadata.st_mode):
        raise _IndexError("source token refuses symbolic links")
    relative = path.relative_to(runs_root).as_posix()
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(_stat.S_IFMT(metadata.st_mode)).encode("ascii"))
    if include_times:
        digest.update(b":")
        digest.update(str(metadata.st_size).encode("ascii"))
        digest.update(b":")
        digest.update(str(metadata.st_mtime_ns).encode("ascii"))
    digest.update(b"\n")


def _validate_staging(connection: _sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise _IndexError("staging index violates foreign-key constraints")
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity != ("ok",):
        raise _IndexError("staging index failed its integrity check")


def _publish_generation(index_directory_fd: int, *, temp_name: str) -> None:
    try:
        _os.replace(
            temp_name,
            _INDEX_FILENAME,
            src_dir_fd=index_directory_fd,
            dst_dir_fd=index_directory_fd,
        )
        _os.fsync(index_directory_fd)
        for suffix in ("-wal", "-shm"):
            with _suppress(FileNotFoundError):
                _os.unlink(f"{_INDEX_FILENAME}{suffix}", dir_fd=index_directory_fd)
        _os.fsync(index_directory_fd)
    except OSError as exc:
        raise _IndexError("cannot atomically publish index generation") from exc


def _fsync_file(path: _Path) -> None:
    descriptor = _os.open(
        path,
        _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0) | getattr(_os, "O_NOFOLLOW", 0),
    )
    try:
        _os.fsync(descriptor)
    finally:
        _os.close(descriptor)


def _unlink_temp(path: _Path) -> None:
    with _suppress(FileNotFoundError):
        path.unlink()


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _IndexError(f"{label} must be an object")
    return value


def _sequence(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise _IndexError(f"{label} must be an array")
    return value


def _canonical_json(value: object) -> str:
    try:
        return _json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _IndexError("derived index input is not canonical JSON data") from exc


def _index_status(runs_root: _Path) -> dict[str, object]:
    try:
        root = _existing_runs_root(runs_root)
    except _IndexError as exc:
        return {
            "path": str(runs_root / _INDEX_DIRECTORY / _INDEX_FILENAME),
            "available": False,
            "fresh": False,
            "error": str(exc),
        }
    path = root / _INDEX_DIRECTORY / _INDEX_FILENAME
    try:
        connection = _open_readonly(path)
        try:
            _validate_schema(connection)
            meta = dict(connection.execute("SELECT key, value FROM index_meta"))
            scan_errors = connection.execute("SELECT COUNT(*) FROM scan_error").fetchone()[0]
            run_count = connection.execute("SELECT COUNT(*) FROM run").fetchone()[0]
        finally:
            connection.close()
    except (_sqlite3.Error, _IndexRebuildRequiredError) as exc:
        return {
            "path": str(path),
            "available": False,
            "fresh": False,
            "error": str(exc),
        }
    current_token = _source_token(root)
    return {
        "path": str(path),
        "available": True,
        "fresh": meta.get("source_token") == current_token,
        "schema_version": meta.get("schema_version"),
        "generation": int(meta["generation"]),
        "built_unix_ns": int(meta["built_unix_ns"]),
        "builder_git_commit": meta.get("builder_git_commit"),
        "run_count": run_count,
        "scan_error_count": scan_errors,
        "source_token": meta.get("source_token"),
        "current_source_token": current_token,
    }


def _open_fresh_index(
    runs_root: _Path,
    *,
    allow_scan_errors: bool = False,
) -> _sqlite3.Connection:
    if type(allow_scan_errors) is not bool:
        raise _IndexError("allow_scan_errors must be an exact bool")
    root = _existing_runs_root(runs_root)
    path = root / _INDEX_DIRECTORY / _INDEX_FILENAME
    connection = _open_readonly(path)
    try:
        _validate_schema(connection)
        stored = connection.execute(
            "SELECT value FROM index_meta WHERE key = ?",
            ("source_token",),
        ).fetchone()
        current = _source_token(root)
        if stored != (current,):
            raise _IndexRebuildRequiredError("index is stale; rebuild before querying")
        errors = connection.execute("SELECT COUNT(*) FROM scan_error").fetchone()
        if errors is None or (errors[0] != 0 and not allow_scan_errors):
            raise _IndexRebuildRequiredError(
                "index contains scan errors; inspect them and rebuild after repairing files"
            )
        return connection
    except BaseException:
        connection.close()
        raise


def _canonical_table_dump(path: _Path) -> str:
    connection = _open_readonly(path)
    try:
        _validate_schema(connection)
        document: dict[str, object] = {}
        for table in _TABLES:
            columns = tuple(
                row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
            )
            order = ", ".join(f'"{column}"' for column in columns)
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()
            document[table] = [dict(zip(columns, row, strict=True)) for row in rows]
        return _canonical_json(document)
    finally:
        connection.close()
