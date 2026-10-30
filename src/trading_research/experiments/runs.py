"""Crash-safe files-first run records, state transitions, and scanning."""

from __future__ import annotations

import errno as _errno
import fcntl as _fcntl
import hashlib as _hashlib
import json as _json
import os as _os
import re as _re
import stat as _stat
import time as _time
import typing as _typing
import uuid as _uuid
from contextlib import suppress as _suppress
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from pathlib import Path as _Path

from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.artifacts.identity import _canonical_identity_json
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.experiments.evaluation import (
    _evaluation_inventory_document,
    _EvaluationInventory,
    _inventory_summary_from_document,
    _InventorySummary,
    _pre_evaluation_failure_inventory,
    _RunOutcomeClass,
)
from trading_research.experiments.run_identity import (
    _ExperimentIdentity,
    _identity_summary_from_document,
    _IdentitySummary,
    _mint_run_id,
    _validated_run_id,
)

__all__ = ["RunManifest", "RunState", "scan_runs"]

_STATE_SCHEMA_V1 = "research-run-state-v1"
_STATE_SCHEMA = "research-run-state-v2"
_HEARTBEAT_SCHEMA_V1 = "research-run-heartbeat-v1"
_HEARTBEAT_SCHEMA = "research-run-heartbeat-v2"
_MANIFEST_SCHEMA_V1 = "research-run-manifest-v1"
_MANIFEST_SCHEMA_V2 = "research-run-manifest-v2"
_MANIFEST_SCHEMA = "research-run-manifest-v3"
_DEFAULT_HEARTBEAT_INTERVAL_NS = 5_000_000_000
_STALE_HEARTBEAT_MULTIPLIER = 10
_RUN_LOCK_TIMEOUT_SECONDS = 5.0
_RUN_LOCK_RETRY_SECONDS = 0.01
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_TEMP = _re.compile(r"^\..+\.[0-9a-f]{16}\.tmp$")
_REGISTER_TEMP = _re.compile(r"^\.register-[0-7][0-9A-HJKMNP-TV-Z]{25}-[0-9a-f]{16}\.tmp$")
_ROOT_FILES = frozenset(
    {
        "config.resolved.json",
        "determinism_report.json",
        "evaluation_inventory.json",
        "heartbeat.json",
        "identity.json",
        "manifest.json",
        "state.json",
    }
)
_PAYLOAD_DIRECTORIES = ("artifacts", "logs", "metrics")
# Measured supported V1 shapes include the 45,268,047-byte (~43.2 MiB) Phase-2
# three-column artifact and the approximately 99.94 MB Phase-6 eight-column
# Zone Trader feature frame. Keep one bounded writer/reader ceiling above them.
_RUN_FILE_LIMIT_BYTES = 134_217_728  # 128 MiB
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
_WRITE_FLAGS = (
    _os.O_WRONLY
    | _os.O_CREAT
    | _os.O_EXCL
    | getattr(_os, "O_CLOEXEC", 0)
    | getattr(_os, "O_NOFOLLOW", 0)
)


class _RunStoreError(_TradingResearchError):
    """A run record cannot preserve the files-first storage contract."""


class _RunLockTimeoutError(_RunStoreError):
    """A run-directory lock was not acquired before its fixed deadline."""


class _RunCorruptionError(_RunStoreError):
    """A visible run record is malformed, unsafe, or hash-inconsistent."""


class _RunStateError(_RunStoreError):
    """A requested run-state transition is not legal."""


class _RunIdCollisionError(_RunStoreError):
    """A concurrently published run already owns the generated ULID."""


class RunState(_StrEnum):
    """The closed V1 run-state machine."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


_TERMINAL_STATES = frozenset({RunState.COMPLETE, RunState.FAILED, RunState.INTERRUPTED})


@_dataclass(frozen=True, slots=True)
class _AccessAuditRef:
    _session_id: str
    _audit_event_id: str | None

    def __post_init__(self) -> None:
        _validated_uuid4(self._session_id, label="access audit session_id")
        if self._audit_event_id is not None:
            _validated_uuid4(self._audit_event_id, label="access audit event_id")


@_dataclass(frozen=True, slots=True)
class _StateRecord:
    _run_id: str
    _state: RunState
    _created_unix_ns: int
    _updated_unix_ns: int
    _started_unix_ns: int | None
    _finished_unix_ns: int | None
    _oos_touched: bool
    _lockbox_touched: bool
    _access_audit_refs: tuple[_AccessAuditRef, ...]
    _failure_reason: str | None

    def __post_init__(self) -> None:
        _validated_run_id(self._run_id)
        if type(self._state) is not RunState:
            raise _ContractViolationError("state must be an exact RunState")
        for value, label in (
            (self._created_unix_ns, "created_unix_ns"),
            (self._updated_unix_ns, "updated_unix_ns"),
        ):
            _validated_unix_ns(value, label=label)
        if self._updated_unix_ns < self._created_unix_ns:
            raise _ContractViolationError("state update cannot precede run creation")
        for value, label in (
            (self._oos_touched, "oos_touched"),
            (self._lockbox_touched, "lockbox_touched"),
        ):
            if type(value) is not bool:
                raise _ContractViolationError(f"{label} must be an exact bool")
        _validated_access_audit_refs(self._access_audit_refs)
        if self._state is RunState.PENDING:
            if (
                self._started_unix_ns is not None
                or self._finished_unix_ns is not None
                or self._failure_reason is not None
                or self._oos_touched
                or self._lockbox_touched
                or self._access_audit_refs
            ):
                raise _ContractViolationError("PENDING state carries later-stage fields")
            return
        if type(self._started_unix_ns) is not int:
            raise _ContractViolationError("non-PENDING state requires started_unix_ns")
        _validated_unix_ns(self._started_unix_ns, label="started_unix_ns")
        if not self._created_unix_ns <= self._started_unix_ns <= self._updated_unix_ns:
            raise _ContractViolationError("run start time lies outside state chronology")
        if self._state is RunState.RUNNING:
            if self._finished_unix_ns is not None or self._failure_reason is not None:
                raise _ContractViolationError("RUNNING state cannot be terminal")
            return
        if type(self._finished_unix_ns) is not int:
            raise _ContractViolationError("terminal state requires finished_unix_ns")
        _validated_unix_ns(self._finished_unix_ns, label="finished_unix_ns")
        if not self._started_unix_ns <= self._finished_unix_ns <= self._updated_unix_ns:
            raise _ContractViolationError("run finish time lies outside state chronology")
        if self._state is RunState.COMPLETE:
            if self._failure_reason is not None:
                raise _ContractViolationError("COMPLETE state cannot carry a failure reason")
        elif (
            type(self._failure_reason) is not str
            or not self._failure_reason.strip()
            or self._failure_reason != self._failure_reason.strip()
        ):
            raise _ContractViolationError(
                "FAILED and INTERRUPTED states require a trimmed failure reason"
            )


@_dataclass(frozen=True, slots=True)
class RunManifest:
    """Terminal, immutable summary of one exact run directory."""

    run_id: str
    experiment_fingerprint: str
    state: RunState
    canonical: bool
    platform_identity: _PlatformIdentity
    dependency_lock_sha256: str
    declared_lockbox_evaluation: bool
    oos_touched: bool
    lockbox_touched: bool
    access_audit_refs: tuple[_AccessAuditRef, ...]
    cost_semantics_unverified: bool
    binding_id: str | None
    binding_digest: str | None
    execution_price_model: str | None
    created_unix_ns: int
    started_unix_ns: int
    finished_unix_ns: int
    outcome_class: _RunOutcomeClass
    evaluation_count: int
    policy_execution_json: tuple[str, ...]
    failure_reason: str | None
    root_file_hashes: tuple[tuple[str, str], ...]
    artifact_hashes: tuple[tuple[str, str], ...]
    log_hashes: tuple[tuple[str, str], ...]
    metric_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Validate every terminal identity, state, and file summary."""
        _validated_run_id(self.run_id)
        _validated_digest(self.experiment_fingerprint, label="experiment fingerprint")
        if self.state not in _TERMINAL_STATES:
            raise _ContractViolationError("RunManifest state must be terminal")
        if type(self.canonical) is not bool:
            raise _ContractViolationError("RunManifest canonical must be an exact bool")
        if type(self.platform_identity) is not _PlatformIdentity:
            raise _ContractViolationError("RunManifest platform identity must be exact")
        _validated_digest(self.dependency_lock_sha256, label="dependency lock")
        for bool_value, label in (
            (self.declared_lockbox_evaluation, "declared_lockbox_evaluation"),
            (self.oos_touched, "oos_touched"),
            (self.lockbox_touched, "lockbox_touched"),
            (self.cost_semantics_unverified, "cost_semantics_unverified"),
        ):
            if type(bool_value) is not bool:
                raise _ContractViolationError(f"{label} must be an exact bool")
        _validated_access_audit_refs(self.access_audit_refs)
        simulation_fields = (
            self.binding_id,
            self.binding_digest,
            self.execution_price_model,
        )
        if all(value is None for value in simulation_fields):
            if self.cost_semantics_unverified:
                raise _ContractViolationError(
                    "non-simulation RunManifest cannot mark cost semantics"
                )
        else:
            if (
                type(self.binding_id) is not str
                or not self.binding_id
                or type(self.execution_price_model) is not str
                or not self.execution_price_model
            ):
                raise _ContractViolationError(
                    "simulation RunManifest requires binding and price-model identity"
                )
            if self.binding_digest is not None:
                _validated_digest(self.binding_digest, label="binding digest")
            if self.cost_semantics_unverified and self.binding_digest is not None:
                raise _ContractViolationError("unverified RunManifest binding digest must be None")
        for timestamp, label in (
            (self.created_unix_ns, "created_unix_ns"),
            (self.started_unix_ns, "started_unix_ns"),
            (self.finished_unix_ns, "finished_unix_ns"),
        ):
            _validated_unix_ns(timestamp, label=label)
        if not self.created_unix_ns <= self.started_unix_ns <= self.finished_unix_ns:
            raise _ContractViolationError("RunManifest timestamps are out of order")
        if type(self.outcome_class) is not _RunOutcomeClass:
            raise _ContractViolationError("RunManifest outcome class must be exact")
        if type(self.evaluation_count) is not int or self.evaluation_count < 0:
            raise _ContractViolationError("RunManifest evaluation_count must be a non-negative int")
        if (self.outcome_class is _RunOutcomeClass.EVALUATED) != (self.evaluation_count > 0):
            raise _ContractViolationError(
                "RunManifest evaluated class and inventory count disagree"
            )
        if type(self.policy_execution_json) is not tuple:
            raise _ContractViolationError("policy executions must be an exact tuple")
        for encoded in self.policy_execution_json:
            parsed = _parse_canonical_text(
                encoded,
                label="manifest policy execution",
            )
            if _canonical_identity_json(parsed) != encoded:
                raise _ContractViolationError("manifest policy execution must be canonical JSON")
        if self.state is RunState.COMPLETE:
            if self.failure_reason is not None:
                raise _ContractViolationError("COMPLETE RunManifest cannot carry a failure reason")
        elif (
            type(self.failure_reason) is not str
            or not self.failure_reason.strip()
            or self.failure_reason != self.failure_reason.strip()
        ):
            raise _ContractViolationError(
                "non-COMPLETE RunManifest requires a trimmed failure reason"
            )
        for entries, label in (
            (self.root_file_hashes, "root file hashes"),
            (self.artifact_hashes, "artifact hashes"),
            (self.log_hashes, "log hashes"),
            (self.metric_hashes, "metric hashes"),
        ):
            _validated_hash_entries(entries, label=label)


@_dataclass(frozen=True, slots=True)
class _RunRecord:
    _run_id: str
    _experiment_fingerprint: str
    _attempt_number: int
    _state: RunState
    _canonical: bool
    _outcome_class: _RunOutcomeClass | None
    _evaluation_count: int
    _policy_execution_count: int
    _oos_touched: bool
    _lockbox_touched: bool
    _failure_reason: str | None
    _heartbeat_age_ns: int
    _heartbeat_stale: bool


@_dataclass(frozen=True, slots=True)
class _RunProjection:
    """Validated read-only projection of one files-first run directory."""

    _record: _RunRecord
    _state: _StateRecord
    _identity: _IdentitySummary
    _resolved_config: dict[str, object]
    _inventory: dict[str, object] | None
    _manifest: RunManifest | None
    _record_digest: str


@_dataclass(frozen=True, slots=True)
class _RunProjectionError:
    """One validly named run directory that could not be projected."""

    _run_id: str
    _error_kind: str
    _detail: str


@_dataclass(frozen=True, slots=True)
class _RunProjectionScan:
    """Projected runs plus explicit per-run failures."""

    _projections: tuple[_RunProjection, ...]
    _errors: tuple[_RunProjectionError, ...]


@_dataclass(frozen=True, slots=True)
class _RunLifecycleObservation:
    """Cheap locked lifecycle observation; rich validation remains scan-owned."""

    _state: RunState
    _heartbeat_stale: bool


def _register_new_run(
    *,
    runs_root: _Path,
    identity: _ExperimentIdentity,
    now_unix_ns: int,
) -> str:
    """Atomically publish one fully initialized PENDING run directory."""
    if type(identity) is not _ExperimentIdentity:
        raise _ContractViolationError("identity must be an exact ExperimentIdentity")
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    for _attempt in range(32):
        run_id = _mint_run_id()
        try:
            _register_run_id(
                runs_root=runs_root,
                identity=identity,
                run_id=run_id,
                now_unix_ns=now_unix_ns,
            )
        except _RunIdCollisionError:
            continue
        return run_id
    raise _RunStoreError("could not mint a unique run_id after 32 attempts")


def _register_run_id(
    *,
    runs_root: _Path,
    identity: _ExperimentIdentity,
    run_id: str,
    now_unix_ns: int,
) -> None:
    if type(identity) is not _ExperimentIdentity:
        raise _ContractViolationError("identity must be an exact ExperimentIdentity")
    checked_run_id = _validated_run_id(run_id)
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    collection_fd = _open_run_collection(runs_root, create=True)
    if collection_fd is None:  # pragma: no cover - create=True is total
        raise AssertionError("run collection creation returned no descriptor")
    temp_name = f".register-{checked_run_id}-{_os.urandom(8).hex()}.tmp"
    temp_fd: int | None = None
    created = False
    try:
        try:
            _os.mkdir(temp_name, 0o700, dir_fd=collection_fd)
        except OSError as exc:
            raise _RunStoreError("cannot create private run-registration directory") from exc
        created = True
        temp_fd = _open_child_directory(collection_fd, temp_name, label="registration")
        _publish_immutable_bytes_at(
            temp_fd,
            "config.resolved.json",
            _canonical_text_bytes(identity._resolved_config_document_json()),
        )
        _publish_immutable_bytes_at(
            temp_fd,
            "identity.json",
            _canonical_text_bytes(identity._identity_document_json()),
        )
        state = _StateRecord(
            checked_run_id,
            RunState.PENDING,
            now_unix_ns,
            now_unix_ns,
            None,
            None,
            False,
            False,
            (),
            None,
        )
        _publish_immutable_bytes_at(
            temp_fd,
            "state.json",
            _canonical_json_bytes(_state_document(state)),
        )
        _publish_immutable_bytes_at(
            temp_fd,
            "heartbeat.json",
            _canonical_json_bytes(
                {
                    "schema": _HEARTBEAT_SCHEMA,
                    "run_id": checked_run_id,
                    "interval_ns": None,
                }
            ),
        )
        _touch_file_at(temp_fd, "heartbeat.json", now_unix_ns=now_unix_ns)
        for child in _PAYLOAD_DIRECTORIES:
            _os.mkdir(child, 0o700, dir_fd=temp_fd)
        _os.fsync(temp_fd)
        _os.close(temp_fd)
        temp_fd = None
        try:
            _os.rename(
                temp_name,
                checked_run_id,
                src_dir_fd=collection_fd,
                dst_dir_fd=collection_fd,
            )
        except OSError as exc:
            if exc.errno in {_errno.EEXIST, _errno.ENOTEMPTY}:
                raise _RunIdCollisionError(f"run_id {checked_run_id} already exists") from exc
            raise _RunStoreError("cannot publish initialized run directory") from exc
        created = False
        _os.fsync(collection_fd)
    finally:
        if temp_fd is not None:
            _os.close(temp_fd)
        if created:
            _cleanup_registration_temp(collection_fd, temp_name)
        _os.close(collection_fd)


def _start_run(
    *,
    runs_root: _Path,
    run_id: str,
    now_unix_ns: int,
    heartbeat_interval_ns: int = _DEFAULT_HEARTBEAT_INTERVAL_NS,
) -> None:
    checked_run_id = _validated_run_id(run_id)
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    _validated_heartbeat_interval_ns(heartbeat_interval_ns)
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.PENDING:
            raise _RunStateError("only a PENDING run can start")
        running = _StateRecord(
            checked_run_id,
            RunState.RUNNING,
            state._created_unix_ns,
            now_unix_ns,
            now_unix_ns,
            None,
            False,
            False,
            (),
            None,
        )
        _atomic_replace_bytes_at(
            run_fd,
            "state.json",
            _canonical_json_bytes(_state_document(running)),
        )
        _atomic_replace_bytes_at(
            run_fd,
            "heartbeat.json",
            _canonical_json_bytes(
                {
                    "schema": _HEARTBEAT_SCHEMA,
                    "run_id": checked_run_id,
                    "interval_ns": heartbeat_interval_ns,
                }
            ),
        )
        _touch_file_at(run_fd, "heartbeat.json", now_unix_ns=now_unix_ns)
    finally:
        _unlock_and_close(run_fd)


def _heartbeat_run(*, runs_root: _Path, run_id: str, now_unix_ns: int) -> None:
    checked_run_id = _validated_run_id(run_id)
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("heartbeats are legal only while RUNNING")
        if now_unix_ns < state._updated_unix_ns:
            raise _RunStateError("heartbeat time cannot precede run state")
        heartbeat = _safe_stat_at(run_fd, "heartbeat.json", label="heartbeat")
        if now_unix_ns < heartbeat.st_mtime_ns:
            raise _RunStateError("heartbeat time cannot move backward")
        _touch_file_at(run_fd, "heartbeat.json", now_unix_ns=now_unix_ns)
    finally:
        _unlock_and_close(run_fd)


def _record_run_access_attempt(
    *,
    runs_root: _Path,
    run_id: str,
    session_id: str,
    now_unix_ns: int,
) -> None:
    """Persist an authorization session before the gate can write or raise."""
    checked_run_id = _validated_run_id(run_id)
    checked_session_id = _validated_uuid4(session_id, label="access audit session_id")
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("access attempts can be recorded only while RUNNING")
        if now_unix_ns < state._updated_unix_ns:
            raise _RunStateError("access-attempt time cannot precede run state")
        if any(ref._session_id == checked_session_id for ref in state._access_audit_refs):
            raise _RunStateError("access audit session_id is already recorded for this run")
        updated = _StateRecord(
            checked_run_id,
            RunState.RUNNING,
            state._created_unix_ns,
            now_unix_ns,
            state._started_unix_ns,
            None,
            state._oos_touched,
            state._lockbox_touched,
            (*state._access_audit_refs, _AccessAuditRef(checked_session_id, None)),
            None,
        )
        _atomic_replace_bytes_at(
            run_fd,
            "state.json",
            _canonical_json_bytes(_state_document(updated)),
        )
    finally:
        _unlock_and_close(run_fd)


def _record_run_access(
    *,
    runs_root: _Path,
    run_id: str,
    oos_touched: bool,
    lockbox_touched: bool,
    now_unix_ns: int,
    session_id: str | None = None,
    audit_event_id: str | None = None,
) -> None:
    checked_run_id = _validated_run_id(run_id)
    for value, label in (
        (oos_touched, "oos_touched"),
        (lockbox_touched, "lockbox_touched"),
    ):
        if type(value) is not bool:
            raise _ContractViolationError(f"{label} must be an exact bool")
    if session_id is None:
        if audit_event_id is not None:
            raise _ContractViolationError("audit_event_id requires a recorded session_id")
    else:
        session_id = _validated_uuid4(session_id, label="access audit session_id")
        if audit_event_id is not None:
            audit_event_id = _validated_uuid4(audit_event_id, label="access audit event_id")
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("access flags can change only while RUNNING")
        if now_unix_ns < state._updated_unix_ns:
            raise _RunStateError("access-record time cannot precede run state")
        refs = state._access_audit_refs
        if session_id is not None:
            matches = tuple(
                index for index, ref in enumerate(refs) if ref._session_id == session_id
            )
            if len(matches) != 1:
                raise _RunStateError("access result requires one pre-recorded audit session")
            match = matches[0]
            previous = refs[match]
            if previous._audit_event_id not in {None, audit_event_id}:
                raise _RunStateError("access audit event_id cannot be replaced")
            refs = (
                *refs[:match],
                _AccessAuditRef(session_id, audit_event_id),
                *refs[match + 1 :],
            )
        updated = _StateRecord(
            checked_run_id,
            RunState.RUNNING,
            state._created_unix_ns,
            now_unix_ns,
            state._started_unix_ns,
            None,
            state._oos_touched or oos_touched,
            state._lockbox_touched or lockbox_touched,
            refs,
            None,
        )
        _atomic_replace_bytes_at(
            run_fd,
            "state.json",
            _canonical_json_bytes(_state_document(updated)),
        )
    finally:
        _unlock_and_close(run_fd)


def _finish_run(
    *,
    runs_root: _Path,
    run_id: str,
    terminal_state: RunState,
    inventory: _EvaluationInventory | None,
    failure_reason: str | None,
    now_unix_ns: int,
) -> RunManifest:
    checked_run_id = _validated_run_id(run_id)
    if terminal_state not in _TERMINAL_STATES:
        raise _ContractViolationError("terminal_state must be COMPLETE, FAILED, or INTERRUPTED")
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    reason = _validated_terminal_reason(terminal_state, failure_reason)
    if inventory is not None and type(inventory) is not _EvaluationInventory:
        raise _ContractViolationError("inventory must be exact or None")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("only a RUNNING run can become terminal")
        if inventory is not None:
            document = _evaluation_inventory_document(inventory)
            summary = _inventory_summary_from_document(document)
        elif _regular_file_exists(run_fd, "evaluation_inventory.json"):
            document = _read_json_document_at(run_fd, "evaluation_inventory.json")
            summary = _validated_inventory_document(document)
        else:
            if reason is None:
                raise _ContractViolationError("COMPLETE run requires an evaluation inventory")
            document = _evaluation_inventory_document(
                _pre_evaluation_failure_inventory(reason=reason)
            )
            summary = _inventory_summary_from_document(document)
        if (
            summary._outcome_class is _RunOutcomeClass.PRE_EVALUATION_FAILURE
            and summary._failure_reason != reason
        ):
            raise _ContractViolationError("pre-evaluation failure reason must match terminal state")
        if (
            terminal_state is RunState.COMPLETE
            and summary._outcome_class is _RunOutcomeClass.PRE_EVALUATION_FAILURE
        ):
            raise _ContractViolationError("COMPLETE run cannot be a pre-evaluation failure")
        return _finish_locked(
            run_fd,
            state=state,
            terminal_state=terminal_state,
            inventory_document=document,
            inventory_summary=summary,
            failure_reason=reason,
            now_unix_ns=now_unix_ns,
        )
    finally:
        _unlock_and_close(run_fd)


def _record_evaluation_inventory(
    *,
    runs_root: _Path,
    run_id: str,
    inventory: _EvaluationInventory,
) -> None:
    """Permanently publish completed evaluations before later work can fail."""
    checked_run_id = _validated_run_id(run_id)
    if type(inventory) is not _EvaluationInventory:
        raise _ContractViolationError("inventory must be exact")
    document = _evaluation_inventory_document(inventory)
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("evaluation inventory can be recorded only while RUNNING")
        _publish_immutable_bytes_at(
            run_fd,
            "evaluation_inventory.json",
            _canonical_json_bytes(document),
        )
    finally:
        _unlock_and_close(run_fd)


def _record_determinism_report(
    *,
    runs_root: _Path,
    run_id: str,
    report: dict[str, object],
) -> None:
    """Store one immutable canonical report supplied by the Slice-3I verifier."""
    checked_run_id = _validated_run_id(run_id)
    if type(report) is not dict or not report:
        raise _ContractViolationError("determinism report must be a non-empty exact JSON object")
    schema = report.get("schema")
    if type(schema) is not str or not schema:
        raise _ContractViolationError("determinism report requires a non-empty schema")
    payload = _canonical_json_bytes(report)
    parsed = _parse_canonical_bytes(payload, label="determinism report")
    if parsed != report:
        raise _ContractViolationError("determinism report must contain only exact JSON values")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("determinism report can be recorded only while RUNNING")
        _publish_immutable_bytes_at(
            run_fd,
            "determinism_report.json",
            payload,
        )
    finally:
        _unlock_and_close(run_fd)


def _record_run_payload(
    *,
    runs_root: _Path,
    run_id: str,
    category: str,
    name: str,
    payload: bytes,
) -> None:
    """Publish one immutable flat artifact, log, or metric while RUNNING."""
    checked_run_id = _validated_run_id(run_id)
    if type(category) is not str or category not in _PAYLOAD_DIRECTORIES:
        raise _ContractViolationError(
            f"run payload category must be one of {_PAYLOAD_DIRECTORIES!r}"
        )
    checked_name = _validated_filename(name)
    if _PRIVATE_TEMP.fullmatch(checked_name) is not None:
        raise _ContractViolationError("run payload name cannot use private-temp syntax")
    if type(payload) is not bytes or not payload:
        raise _ContractViolationError("run payload must be non-empty exact bytes")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        if state._state is not RunState.RUNNING:
            raise _RunStateError("run payloads can be recorded only while RUNNING")
        category_fd = _open_child_directory(run_fd, category, label=category)
        try:
            _publish_immutable_bytes_at(category_fd, checked_name, payload)
        finally:
            _os.close(category_fd)
    finally:
        _unlock_and_close(run_fd)


def _read_registered_config(
    *,
    runs_root: _Path,
    run_id: str,
) -> dict[str, object]:
    """Read and revalidate one registered config through pinned descriptors."""
    checked_run_id = _validated_run_id(run_id)
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        identity = _validated_identity_document(_read_json_document_at(run_fd, "identity.json"))
        config = _read_json_document_at(run_fd, "config.resolved.json")
        if _canonical_identity_json(config) != identity._resolved_config_json:
            raise _RunCorruptionError("resolved config differs from fingerprint identity")
        return config
    finally:
        _unlock_and_close(run_fd)


def _read_registered_identity(
    *,
    runs_root: _Path,
    run_id: str,
) -> _IdentitySummary:
    """Return the fully revalidated identity summary for one registered run."""
    checked_run_id = _validated_run_id(run_id)
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        return _validated_identity_document(_read_json_document_at(run_fd, "identity.json"))
    finally:
        _unlock_and_close(run_fd)


def scan_runs(
    runs_root: _Path,
    *,
    now_unix_ns: int,
    stale_after_ns: int,
    reconcile: bool = True,
) -> tuple[_RunRecord, ...]:
    """Reconstruct run records, optionally reconciling stale RUNNING runs."""
    scan = _scan_run_projections(
        runs_root,
        now_unix_ns=now_unix_ns,
        stale_after_ns=stale_after_ns,
        reconcile=reconcile,
        tolerate_corruption=False,
    )
    return tuple(projection._record for projection in scan._projections)


def _scan_run_projections(
    runs_root: _Path,
    *,
    now_unix_ns: int,
    stale_after_ns: int,
    reconcile: bool,
    tolerate_corruption: bool,
) -> _RunProjectionScan:
    """Return rich validated projections without giving derived state authority."""
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    if type(stale_after_ns) is not int or stale_after_ns <= 0:
        raise _ContractViolationError("stale_after_ns must be an exact positive int")
    if type(reconcile) is not bool or type(tolerate_corruption) is not bool:
        raise _ContractViolationError("projection mode flags must be exact bools")
    if reconcile and tolerate_corruption:
        raise _ContractViolationError("a reconciling scan cannot tolerate corruption")
    collection_fd = _open_run_collection(runs_root, create=False)
    if collection_fd is None:
        return _RunProjectionScan((), ())
    try:
        run_ids = _listed_run_ids(collection_fd)
        if reconcile:
            _projection_pass(
                collection_fd,
                runs_root=runs_root,
                run_ids=run_ids,
                now_unix_ns=now_unix_ns,
                stale_after_ns=stale_after_ns,
                reconcile=False,
                tolerate_corruption=False,
            )
        provisional, errors = _projection_pass(
            collection_fd,
            runs_root=runs_root,
            run_ids=run_ids,
            now_unix_ns=now_unix_ns,
            stale_after_ns=stale_after_ns,
            reconcile=reconcile,
            tolerate_corruption=tolerate_corruption,
        )
    finally:
        _os.close(collection_fd)

    attempts: dict[str, int] = {}
    projections: list[_RunProjection] = []
    for projection in sorted(provisional, key=lambda item: item._record._run_id):
        record = projection._record
        attempt = attempts.get(record._experiment_fingerprint, 0) + 1
        attempts[record._experiment_fingerprint] = attempt
        projections.append(
            _RunProjection(
                _RunRecord(
                    record._run_id,
                    record._experiment_fingerprint,
                    attempt,
                    record._state,
                    record._canonical,
                    record._outcome_class,
                    record._evaluation_count,
                    record._policy_execution_count,
                    record._oos_touched,
                    record._lockbox_touched,
                    record._failure_reason,
                    record._heartbeat_age_ns,
                    record._heartbeat_stale,
                ),
                projection._state,
                projection._identity,
                projection._resolved_config,
                projection._inventory,
                projection._manifest,
                projection._record_digest,
            )
        )
    return _RunProjectionScan(tuple(projections), tuple(errors))


def _observe_run_lifecycle(
    runs_root: _Path,
    *,
    run_id: str,
    now_unix_ns: int,
    stale_after_ns: int,
) -> _RunLifecycleObservation:
    """Read one run's scheduling state without hashing its terminal payload."""
    checked_run_id = _validated_run_id(run_id)
    _validated_unix_ns(now_unix_ns, label="now_unix_ns")
    if type(stale_after_ns) is not int or stale_after_ns <= 0:
        raise _ContractViolationError("stale_after_ns must be an exact positive int")
    run_fd = _open_run_directory(runs_root, checked_run_id)
    try:
        _lock_run_shared(
            run_fd,
            run_directory=runs_root / "runs" / checked_run_id,
        )
        state = _read_state(run_fd, expected_run_id=checked_run_id)
        _validate_run_layout(
            run_fd,
            terminal=state._state in _TERMINAL_STATES,
        )
        heartbeat_stale = False
        if state._state is RunState.RUNNING:
            heartbeat_interval_ns = _read_heartbeat_interval_ns(
                run_fd,
                expected_run_id=checked_run_id,
            )
            if heartbeat_interval_ns is None:
                raise _RunCorruptionError("RUNNING heartbeat has no declared interval")
            minimum_stale_after_ns = heartbeat_interval_ns * _STALE_HEARTBEAT_MULTIPLIER
            if stale_after_ns < minimum_stale_after_ns:
                raise _ContractViolationError(
                    "stale_after_ns must be at least 10 times every RUNNING heartbeat "
                    f"interval ({minimum_stale_after_ns} ns required for {checked_run_id})"
                )
            heartbeat = _safe_stat_at(run_fd, "heartbeat.json", label="heartbeat")
            heartbeat_stale = now_unix_ns - heartbeat.st_mtime_ns > stale_after_ns
        return _RunLifecycleObservation(state._state, heartbeat_stale)
    finally:
        _unlock_and_close(run_fd)


def _listed_run_ids(collection_fd: int) -> tuple[str, ...]:
    run_ids: list[str] = []
    for name in sorted(_os.listdir(collection_fd)):
        if _REGISTER_TEMP.fullmatch(name) is not None:
            continue
        try:
            run_ids.append(_validated_run_id(name))
        except _ContractViolationError as exc:
            raise _RunCorruptionError(f"unexpected entry in run collection: {name!r}") from exc
    return tuple(run_ids)


def _projection_pass(
    collection_fd: int,
    *,
    runs_root: _Path,
    run_ids: tuple[str, ...],
    now_unix_ns: int,
    stale_after_ns: int,
    reconcile: bool,
    tolerate_corruption: bool,
) -> tuple[list[_RunProjection], list[_RunProjectionError]]:
    projections: list[_RunProjection] = []
    errors: list[_RunProjectionError] = []
    for run_id in run_ids:
        run_fd: int | None = None
        try:
            run_fd = _open_child_directory(collection_fd, run_id, label="run")
            if reconcile:
                _lock_run(
                    run_fd,
                    run_directory=runs_root / "runs" / run_id,
                )
            else:
                _lock_run_shared(
                    run_fd,
                    run_directory=runs_root / "runs" / run_id,
                )
            projections.append(
                _scan_locked_run(
                    run_fd,
                    run_id=run_id,
                    now_unix_ns=now_unix_ns,
                    stale_after_ns=stale_after_ns,
                    reconcile=reconcile,
                )
            )
        except (_RunStoreError, _ContractViolationError, OSError) as exc:
            if not tolerate_corruption:
                raise
            detail = " ".join(str(exc).split()) or "no detail"
            errors.append(_RunProjectionError(run_id, type(exc).__name__, detail[:2_000]))
        finally:
            if run_fd is not None:
                _unlock_and_close(run_fd)
    return projections, errors


def _scan_locked_run(
    run_fd: int,
    *,
    run_id: str,
    now_unix_ns: int,
    stale_after_ns: int,
    reconcile: bool,
) -> _RunProjection:
    _validate_run_layout(run_fd, terminal=False)
    identity_document = _read_json_document_at(run_fd, "identity.json")
    identity = _validated_identity_document(identity_document)
    resolved_config = _read_json_document_at(run_fd, "config.resolved.json")
    if _canonical_identity_json(resolved_config) != identity._resolved_config_json:
        raise _RunCorruptionError("resolved config differs from fingerprint identity")
    state = _read_state(run_fd, expected_run_id=run_id)
    heartbeat_interval_ns = _read_heartbeat_interval_ns(
        run_fd,
        expected_run_id=run_id,
    )
    heartbeat_metadata = _safe_stat_at(run_fd, "heartbeat.json", label="heartbeat")
    heartbeat_age_ns = now_unix_ns - heartbeat_metadata.st_mtime_ns
    heartbeat_stale = state._state is RunState.RUNNING and heartbeat_age_ns > stale_after_ns
    if state._state is RunState.RUNNING:
        if heartbeat_interval_ns is None:
            raise _RunCorruptionError("RUNNING heartbeat has no declared interval")
        minimum_stale_after_ns = heartbeat_interval_ns * _STALE_HEARTBEAT_MULTIPLIER
        if stale_after_ns < minimum_stale_after_ns:
            raise _ContractViolationError(
                "stale_after_ns must be at least 10 times every RUNNING heartbeat interval "
                f"({minimum_stale_after_ns} ns required for {run_id})"
            )
    manifest_exists = _regular_file_exists(run_fd, "manifest.json")
    manifest: RunManifest | None = None
    if state._state is RunState.RUNNING and manifest_exists:
        manifest = _read_and_validate_manifest(
            run_fd,
            state=state,
            identity=identity,
            permit_running_reconciliation=True,
        )
        if reconcile:
            state = _state_from_manifest(manifest, updated_unix_ns=manifest.finished_unix_ns)
            _atomic_replace_bytes_at(
                run_fd,
                "state.json",
                _canonical_json_bytes(_state_document(state)),
            )
            _seal_run_directory(run_fd)
    elif state._state is RunState.RUNNING and heartbeat_stale and reconcile:
        reason = f"stale heartbeat exceeded {stale_after_ns} ns"
        failure_inventory_document, inventory_summary = _existing_or_failure_inventory(
            run_fd,
            reason=reason,
        )
        manifest = _finish_locked(
            run_fd,
            state=state,
            terminal_state=RunState.INTERRUPTED,
            inventory_document=failure_inventory_document,
            inventory_summary=inventory_summary,
            failure_reason=reason,
            now_unix_ns=now_unix_ns,
        )
        state = _state_from_manifest(manifest, updated_unix_ns=now_unix_ns)
    elif state._state in _TERMINAL_STATES and not manifest_exists:
        raise _RunCorruptionError("terminal run is missing manifest.json")

    inventory_document: dict[str, object] | None = None
    summary: _InventorySummary | None = None
    if _regular_file_exists(run_fd, "evaluation_inventory.json"):
        inventory_document = _read_json_document_at(run_fd, "evaluation_inventory.json")
        summary = _validated_inventory_document(inventory_document)
    canonical = _terminal_canonical(identity, lockbox_touched=state._lockbox_touched)
    if state._state in _TERMINAL_STATES:
        manifest = _read_and_validate_manifest(
            run_fd,
            state=state,
            identity=identity,
            permit_running_reconciliation=False,
        )
        canonical = manifest.canonical
        summary = _InventorySummary(
            manifest.outcome_class,
            manifest.evaluation_count,
            manifest.policy_execution_json,
            summary._failure_reason if summary is not None else None,
        )
        _validate_run_layout(run_fd, terminal=True)
    return _RunProjection(
        _RunRecord(
            run_id,
            identity._experiment_fingerprint,
            0,
            state._state,
            canonical,
            None if summary is None else summary._outcome_class,
            0 if summary is None else summary._evaluation_count,
            0 if summary is None else len(summary._policy_execution_json),
            state._oos_touched,
            state._lockbox_touched,
            state._failure_reason,
            heartbeat_age_ns,
            heartbeat_stale,
        ),
        state,
        identity,
        resolved_config,
        inventory_document,
        manifest,
        _projection_record_digest(run_fd),
    )


def _projection_record_digest(run_fd: int) -> str:
    digest = _hashlib.sha256()
    for name in (
        "config.resolved.json",
        "heartbeat.json",
        "identity.json",
        "state.json",
        "evaluation_inventory.json",
        "manifest.json",
    ):
        if not _regular_file_exists(run_fd, name):
            continue
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(_read_regular_bytes_at(run_fd, name))
        digest.update(b"\0")
    heartbeat = _safe_stat_at(run_fd, "heartbeat.json", label="heartbeat")
    digest.update(str(heartbeat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def _finish_locked(
    run_fd: int,
    *,
    state: _StateRecord,
    terminal_state: RunState,
    inventory_document: dict[str, object],
    inventory_summary: _InventorySummary,
    failure_reason: str | None,
    now_unix_ns: int,
) -> RunManifest:
    if state._state is not RunState.RUNNING or state._started_unix_ns is None:
        raise _RunStateError("terminalization requires a RUNNING state")
    if now_unix_ns < state._updated_unix_ns:
        raise _RunStateError("terminal time cannot precede current state")
    inventory_bytes = _canonical_json_bytes(inventory_document)
    _publish_immutable_bytes_at(
        run_fd,
        "evaluation_inventory.json",
        inventory_bytes,
    )
    identity_bytes = _read_regular_bytes_at(run_fd, "identity.json")
    identity = _validated_identity_document(
        _parse_canonical_bytes(identity_bytes, label="identity.json")
    )
    config_bytes = _read_regular_bytes_at(run_fd, "config.resolved.json")
    config_document = _parse_canonical_bytes(
        config_bytes,
        label="config.resolved.json",
    )
    if _canonical_identity_json(config_document) != identity._resolved_config_json:
        raise _RunCorruptionError("resolved config differs from fingerprint identity")
    (
        cost_semantics_unverified,
        binding_id,
        binding_digest,
        execution_price_model,
    ) = _manifest_simulation_fields(config_document)
    terminal = _StateRecord(
        state._run_id,
        terminal_state,
        state._created_unix_ns,
        now_unix_ns,
        state._started_unix_ns,
        now_unix_ns,
        state._oos_touched,
        state._lockbox_touched,
        state._access_audit_refs,
        failure_reason,
    )
    root_hashes = _root_payload_hashes(run_fd)
    manifest = RunManifest(
        run_id=state._run_id,
        experiment_fingerprint=identity._experiment_fingerprint,
        state=terminal_state,
        canonical=_terminal_canonical(
            identity,
            lockbox_touched=state._lockbox_touched,
        ),
        platform_identity=identity._platform_identity,
        dependency_lock_sha256=identity._dependency_lock_sha256,
        declared_lockbox_evaluation=identity._declared_lockbox_evaluation,
        oos_touched=state._oos_touched,
        lockbox_touched=state._lockbox_touched,
        access_audit_refs=state._access_audit_refs,
        cost_semantics_unverified=cost_semantics_unverified,
        binding_id=binding_id,
        binding_digest=binding_digest,
        execution_price_model=execution_price_model,
        created_unix_ns=state._created_unix_ns,
        started_unix_ns=state._started_unix_ns,
        finished_unix_ns=now_unix_ns,
        outcome_class=inventory_summary._outcome_class,
        evaluation_count=inventory_summary._evaluation_count,
        policy_execution_json=inventory_summary._policy_execution_json,
        failure_reason=failure_reason,
        root_file_hashes=root_hashes,
        artifact_hashes=_directory_hashes(run_fd, "artifacts"),
        log_hashes=_directory_hashes(run_fd, "logs"),
        metric_hashes=_directory_hashes(run_fd, "metrics"),
    )
    _publish_immutable_bytes_at(
        run_fd,
        "manifest.json",
        _canonical_json_bytes(_manifest_document(manifest)),
    )
    _atomic_replace_bytes_at(
        run_fd,
        "state.json",
        _canonical_json_bytes(_state_document(terminal)),
    )
    _seal_run_directory(run_fd)
    return manifest


def _manifest_simulation_fields(
    config_document: object,
) -> tuple[bool, str | None, str | None, str | None]:
    if type(config_document) is not dict:
        raise _RunCorruptionError("resolved config must be an object")
    execution = config_document.get("execution")
    if execution is None:
        return False, None, None, None
    if type(execution) is not dict or type(execution.get("cost_binding")) is not dict:
        raise _RunCorruptionError("resolved execution cost binding is malformed")
    binding = _typing.cast("dict[str, object]", execution["cost_binding"])
    binding_id = binding.get("binding_id")
    binding_digest = binding.get("binding_digest")
    cost_semantics = binding.get("cost_semantics")
    price_model = binding.get("execution_price_model")
    if (
        type(binding_id) is not str
        or not binding_id
        or (binding_digest is not None and type(binding_digest) is not str)
        or cost_semantics not in {"unverified", "verified"}
        or type(price_model) is not str
        or not price_model
    ):
        raise _RunCorruptionError("resolved execution marking is malformed")
    return (
        cost_semantics == "unverified",
        binding_id,
        binding_digest,
        price_model,
    )


def _existing_or_failure_inventory(
    run_fd: int,
    *,
    reason: str,
) -> tuple[dict[str, object], _InventorySummary]:
    if _regular_file_exists(run_fd, "evaluation_inventory.json"):
        document = _read_json_document_at(run_fd, "evaluation_inventory.json")
    else:
        document = _evaluation_inventory_document(_pre_evaluation_failure_inventory(reason=reason))
    summary = _validated_inventory_document(document)
    if (
        summary._outcome_class is _RunOutcomeClass.PRE_EVALUATION_FAILURE
        and summary._failure_reason != reason
    ):
        raise _RunCorruptionError(
            "pre-evaluation inventory reason differs from interruption reason"
        )
    return document, summary


def _state_from_manifest(
    manifest: RunManifest,
    *,
    updated_unix_ns: int,
) -> _StateRecord:
    return _StateRecord(
        manifest.run_id,
        manifest.state,
        manifest.created_unix_ns,
        updated_unix_ns,
        manifest.started_unix_ns,
        manifest.finished_unix_ns,
        manifest.oos_touched,
        manifest.lockbox_touched,
        manifest.access_audit_refs,
        manifest.failure_reason,
    )


def _terminal_canonical(
    identity: _IdentitySummary,
    *,
    lockbox_touched: bool,
) -> bool:
    return identity._registration_canonical and (
        not lockbox_touched or identity._declared_lockbox_evaluation
    )


def _validate_config_binding(run_fd: int, identity: _IdentitySummary) -> None:
    config = _read_json_document_at(run_fd, "config.resolved.json")
    if _canonical_identity_json(config) != identity._resolved_config_json:
        raise _RunCorruptionError("resolved config differs from fingerprint identity")


def _manifest_document(manifest: RunManifest) -> dict[str, object]:
    return {
        "schema": _MANIFEST_SCHEMA,
        "run_id": manifest.run_id,
        "experiment_fingerprint": manifest.experiment_fingerprint,
        "state": manifest.state.value,
        "canonical": manifest.canonical,
        "platform": {
            "git_commit": manifest.platform_identity.git_commit,
            "git_dirty": manifest.platform_identity.git_dirty,
            "diff_sha256": manifest.platform_identity.diff_sha256,
        },
        "dependency_lock_sha256": manifest.dependency_lock_sha256,
        "access": {
            "declared_lockbox_evaluation": manifest.declared_lockbox_evaluation,
            "oos_touched": manifest.oos_touched,
            "lockbox_touched": manifest.lockbox_touched,
            "audit_refs": [
                {
                    "session_id": ref._session_id,
                    "audit_event_id": ref._audit_event_id,
                }
                for ref in manifest.access_audit_refs
            ],
        },
        "simulation": (
            None
            if manifest.binding_id is None
            else {
                "cost_semantics_unverified": manifest.cost_semantics_unverified,
                "binding_id": manifest.binding_id,
                "binding_digest": manifest.binding_digest,
                "execution_price_model": manifest.execution_price_model,
            }
        ),
        "timestamps": {
            "created_unix_ns": manifest.created_unix_ns,
            "started_unix_ns": manifest.started_unix_ns,
            "finished_unix_ns": manifest.finished_unix_ns,
        },
        "evaluation": {
            "outcome_class": manifest.outcome_class.value,
            "entry_count": manifest.evaluation_count,
            "policy_executions": [
                _parse_canonical_text(encoded, label="policy execution")
                for encoded in manifest.policy_execution_json
            ],
        },
        "failure_reason": manifest.failure_reason,
        "files": {
            "root": dict(manifest.root_file_hashes),
            "artifacts": dict(manifest.artifact_hashes),
            "logs": dict(manifest.log_hashes),
            "metrics": dict(manifest.metric_hashes),
        },
    }


def _read_and_validate_manifest(
    run_fd: int,
    *,
    state: _StateRecord,
    identity: _IdentitySummary,
    permit_running_reconciliation: bool,
) -> RunManifest:
    document = _read_json_document_at(run_fd, "manifest.json")
    try:
        manifest = _manifest_from_document(document)
    except _ContractViolationError as exc:
        raise _RunCorruptionError("RunManifest fields are malformed") from exc
    if manifest.run_id != state._run_id:
        raise _RunCorruptionError("manifest run_id differs from state")
    if manifest.experiment_fingerprint != identity._experiment_fingerprint:
        raise _RunCorruptionError("manifest fingerprint differs from identity")
    if manifest.platform_identity != identity._platform_identity:
        raise _RunCorruptionError("manifest platform differs from identity")
    if manifest.dependency_lock_sha256 != identity._dependency_lock_sha256:
        raise _RunCorruptionError("manifest dependency lock differs from identity")
    expected_simulation = _manifest_simulation_fields(
        _read_json_document_at(run_fd, "config.resolved.json")
    )
    observed_simulation = (
        manifest.cost_semantics_unverified,
        manifest.binding_id,
        manifest.binding_digest,
        manifest.execution_price_model,
    )
    if observed_simulation != expected_simulation:
        raise _RunCorruptionError("manifest simulation marking differs from config")
    if manifest.canonical != _terminal_canonical(
        identity,
        lockbox_touched=manifest.lockbox_touched,
    ):
        raise _RunCorruptionError("manifest canonical flag is inconsistent")
    if state._state is RunState.RUNNING:
        if not permit_running_reconciliation:
            raise _RunCorruptionError("RUNNING state unexpectedly has a manifest")
        if (
            state._created_unix_ns != manifest.created_unix_ns
            or state._started_unix_ns != manifest.started_unix_ns
            or state._oos_touched != manifest.oos_touched
            or state._lockbox_touched != manifest.lockbox_touched
            or state._access_audit_refs != manifest.access_audit_refs
        ):
            raise _RunCorruptionError("published manifest differs from RUNNING state")
    elif (
        state._state is not manifest.state
        or state._created_unix_ns != manifest.created_unix_ns
        or state._started_unix_ns != manifest.started_unix_ns
        or state._finished_unix_ns != manifest.finished_unix_ns
        or state._oos_touched != manifest.oos_touched
        or state._lockbox_touched != manifest.lockbox_touched
        or state._access_audit_refs != manifest.access_audit_refs
        or state._failure_reason != manifest.failure_reason
    ):
        raise _RunCorruptionError("terminal state and manifest disagree")

    inventory = _validated_inventory_document(
        _read_json_document_at(run_fd, "evaluation_inventory.json")
    )
    if (
        manifest.outcome_class is not inventory._outcome_class
        or manifest.evaluation_count != inventory._evaluation_count
        or manifest.policy_execution_json != inventory._policy_execution_json
    ):
        raise _RunCorruptionError("manifest evaluation summary differs from inventory")
    if (
        inventory._outcome_class is _RunOutcomeClass.PRE_EVALUATION_FAILURE
        and inventory._failure_reason != manifest.failure_reason
    ):
        raise _RunCorruptionError("manifest failure differs from pre-evaluation inventory")
    if manifest.root_file_hashes != _root_payload_hashes(run_fd):
        raise _RunCorruptionError("manifest root-file hashes differ from run files")
    if manifest.artifact_hashes != _directory_hashes(run_fd, "artifacts"):
        raise _RunCorruptionError("manifest artifact hashes differ from run files")
    if manifest.log_hashes != _directory_hashes(run_fd, "logs"):
        raise _RunCorruptionError("manifest log hashes differ from run files")
    if manifest.metric_hashes != _directory_hashes(run_fd, "metrics"):
        raise _RunCorruptionError("manifest metric hashes differ from run files")
    return manifest


def _manifest_from_document(value: object) -> RunManifest:
    if type(value) is not dict:
        raise _RunCorruptionError("RunManifest must be an object")
    raw_document = _typing.cast("dict[str, object]", value)
    schema = raw_document.get("schema")
    if schema not in {_MANIFEST_SCHEMA_V1, _MANIFEST_SCHEMA_V2, _MANIFEST_SCHEMA}:
        raise _RunCorruptionError("RunManifest schema is unsupported")
    fields = {
        "access",
        "canonical",
        "dependency_lock_sha256",
        "evaluation",
        "experiment_fingerprint",
        "failure_reason",
        "files",
        "platform",
        "run_id",
        "schema",
        "state",
        "timestamps",
    }
    if schema in {_MANIFEST_SCHEMA_V2, _MANIFEST_SCHEMA}:
        fields.add("simulation")
    document = _exact_dict(
        value,
        fields=fields,
        label="RunManifest",
    )
    raw_state = document["state"]
    if type(raw_state) is not str:
        raise _RunCorruptionError("RunManifest state is invalid")
    try:
        state = RunState(raw_state)
    except (TypeError, ValueError) as exc:
        raise _RunCorruptionError("RunManifest state is invalid") from exc
    platform_value = _exact_dict(
        document["platform"],
        fields={"diff_sha256", "git_commit", "git_dirty"},
        label="RunManifest platform",
    )
    platform = _PlatformIdentity(
        git_commit=_typing.cast("str", platform_value["git_commit"]),
        git_dirty=_typing.cast("bool", platform_value["git_dirty"]),
        diff_sha256=_typing.cast("str | None", platform_value["diff_sha256"]),
    )
    access_fields = {
        "declared_lockbox_evaluation",
        "lockbox_touched",
        "oos_touched",
    }
    if schema == _MANIFEST_SCHEMA:
        access_fields.add("audit_refs")
    access = _exact_dict(document["access"], fields=access_fields, label="RunManifest access")
    access_audit_refs = (
        ()
        if schema != _MANIFEST_SCHEMA
        else _access_audit_refs_from_document(
            access["audit_refs"],
            label="RunManifest access audit_refs",
        )
    )
    timestamps = _exact_dict(
        document["timestamps"],
        fields={"created_unix_ns", "finished_unix_ns", "started_unix_ns"},
        label="RunManifest timestamps",
    )
    evaluation = _exact_dict(
        document["evaluation"],
        fields={"entry_count", "outcome_class", "policy_executions"},
        label="RunManifest evaluation",
    )
    raw_outcome_class = evaluation["outcome_class"]
    if type(raw_outcome_class) is not str:
        raise _RunCorruptionError("RunManifest outcome class is invalid")
    try:
        outcome_class = _RunOutcomeClass(raw_outcome_class)
    except (TypeError, ValueError) as exc:
        raise _RunCorruptionError("RunManifest outcome class is invalid") from exc
    policies = _exact_list(
        evaluation["policy_executions"],
        label="RunManifest policy executions",
    )
    policy_json = tuple(_canonical_identity_json(policy) for policy in policies)
    cost_semantics_unverified = False
    binding_id: str | None = None
    binding_digest: str | None = None
    execution_price_model: str | None = None
    if schema in {_MANIFEST_SCHEMA_V2, _MANIFEST_SCHEMA} and document["simulation"] is not None:
        simulation = _exact_dict(
            document["simulation"],
            fields={
                "binding_digest",
                "binding_id",
                "cost_semantics_unverified",
                "execution_price_model",
            },
            label="RunManifest simulation",
        )
        cost_semantics_unverified = _typing.cast("bool", simulation["cost_semantics_unverified"])
        binding_id = _typing.cast("str", simulation["binding_id"])
        binding_digest = _typing.cast("str | None", simulation["binding_digest"])
        execution_price_model = _typing.cast("str", simulation["execution_price_model"])
    files = _exact_dict(
        document["files"],
        fields={"artifacts", "logs", "metrics", "root"},
        label="RunManifest files",
    )
    return RunManifest(
        run_id=_typing.cast("str", document["run_id"]),
        experiment_fingerprint=_typing.cast("str", document["experiment_fingerprint"]),
        state=state,
        canonical=_typing.cast("bool", document["canonical"]),
        platform_identity=platform,
        dependency_lock_sha256=_typing.cast("str", document["dependency_lock_sha256"]),
        declared_lockbox_evaluation=_typing.cast("bool", access["declared_lockbox_evaluation"]),
        oos_touched=_typing.cast("bool", access["oos_touched"]),
        lockbox_touched=_typing.cast("bool", access["lockbox_touched"]),
        access_audit_refs=access_audit_refs,
        cost_semantics_unverified=cost_semantics_unverified,
        binding_id=binding_id,
        binding_digest=binding_digest,
        execution_price_model=execution_price_model,
        created_unix_ns=_typing.cast("int", timestamps["created_unix_ns"]),
        started_unix_ns=_typing.cast("int", timestamps["started_unix_ns"]),
        finished_unix_ns=_typing.cast("int", timestamps["finished_unix_ns"]),
        outcome_class=outcome_class,
        evaluation_count=_typing.cast("int", evaluation["entry_count"]),
        policy_execution_json=policy_json,
        failure_reason=_typing.cast("str | None", document["failure_reason"]),
        root_file_hashes=_hash_entries_from_document(files["root"], label="root"),
        artifact_hashes=_hash_entries_from_document(files["artifacts"], label="artifacts"),
        log_hashes=_hash_entries_from_document(files["logs"], label="logs"),
        metric_hashes=_hash_entries_from_document(files["metrics"], label="metrics"),
    )


def _validated_identity_document(value: object) -> _IdentitySummary:
    try:
        return _identity_summary_from_document(value)
    except _ContractViolationError as exc:
        raise _RunCorruptionError("experiment identity document is malformed") from exc


def _validated_inventory_document(value: object) -> _InventorySummary:
    try:
        return _inventory_summary_from_document(value)
    except _ContractViolationError as exc:
        raise _RunCorruptionError("evaluation inventory document is malformed") from exc


def _state_document(state: _StateRecord) -> dict[str, object]:
    return {
        "schema": _STATE_SCHEMA,
        "run_id": state._run_id,
        "state": state._state.value,
        "created_unix_ns": state._created_unix_ns,
        "updated_unix_ns": state._updated_unix_ns,
        "started_unix_ns": state._started_unix_ns,
        "finished_unix_ns": state._finished_unix_ns,
        "oos_touched": state._oos_touched,
        "lockbox_touched": state._lockbox_touched,
        "access_audit_refs": [
            {
                "session_id": ref._session_id,
                "audit_event_id": ref._audit_event_id,
            }
            for ref in state._access_audit_refs
        ],
        "failure_reason": state._failure_reason,
    }


def _read_state(run_fd: int, *, expected_run_id: str) -> _StateRecord:
    _read_heartbeat_interval_ns(run_fd, expected_run_id=expected_run_id)
    value = _read_json_document_at(run_fd, "state.json")
    if type(value) is not dict:
        raise _RunCorruptionError("run state must be an object")
    schema = value.get("schema")
    fields = {
        "created_unix_ns",
        "failure_reason",
        "finished_unix_ns",
        "lockbox_touched",
        "oos_touched",
        "run_id",
        "schema",
        "started_unix_ns",
        "state",
        "updated_unix_ns",
    }
    if schema == _STATE_SCHEMA:
        fields.add("access_audit_refs")
    elif schema != _STATE_SCHEMA_V1:
        raise _RunCorruptionError("run state schema is unsupported")
    document = _exact_dict(
        value,
        fields=fields,
        label="run state",
    )
    if document["run_id"] != expected_run_id:
        raise _RunCorruptionError("run state run_id differs")
    access_audit_refs = (
        ()
        if schema == _STATE_SCHEMA_V1
        else _access_audit_refs_from_document(
            document["access_audit_refs"],
            label="run state access_audit_refs",
        )
    )
    raw_state = document["state"]
    if type(raw_state) is not str:
        raise _RunCorruptionError("run state value is invalid")
    try:
        state = RunState(raw_state)
    except (TypeError, ValueError) as exc:
        raise _RunCorruptionError("run state value is invalid") from exc
    try:
        return _StateRecord(
            expected_run_id,
            state,
            _typing.cast("int", document["created_unix_ns"]),
            _typing.cast("int", document["updated_unix_ns"]),
            _typing.cast("int | None", document["started_unix_ns"]),
            _typing.cast("int | None", document["finished_unix_ns"]),
            _typing.cast("bool", document["oos_touched"]),
            _typing.cast("bool", document["lockbox_touched"]),
            access_audit_refs,
            _typing.cast("str | None", document["failure_reason"]),
        )
    except _ContractViolationError as exc:
        raise _RunCorruptionError("run state fields are malformed") from exc


def _read_heartbeat_interval_ns(run_fd: int, *, expected_run_id: str) -> int | None:
    value = _read_json_document_at(run_fd, "heartbeat.json")
    if type(value) is not dict:
        raise _RunCorruptionError("run heartbeat must be an object")
    raw = value
    schema = raw.get("schema")
    if schema == _HEARTBEAT_SCHEMA_V1:
        heartbeat = _exact_dict(
            value,
            fields={"run_id", "schema"},
            label="run heartbeat",
        )
        interval_ns: int | None = _DEFAULT_HEARTBEAT_INTERVAL_NS
    elif schema == _HEARTBEAT_SCHEMA:
        heartbeat = _exact_dict(
            value,
            fields={"interval_ns", "run_id", "schema"},
            label="run heartbeat",
        )
        raw_interval = heartbeat["interval_ns"]
        if raw_interval is None:
            interval_ns = None
        else:
            try:
                interval_ns = _validated_heartbeat_interval_ns(raw_interval)
            except _ContractViolationError as exc:
                raise _RunCorruptionError("run heartbeat interval is malformed") from exc
    else:
        raise _RunCorruptionError("run heartbeat schema is unsupported")
    if heartbeat["run_id"] != expected_run_id:
        raise _RunCorruptionError("run heartbeat run_id differs")
    return interval_ns


def _validated_heartbeat_interval_ns(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise _ContractViolationError("heartbeat_interval_ns must be an exact positive int")
    return value


def _validated_uuid4(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise _ContractViolationError(f"{label} must be a canonical UUID4 string")
    try:
        parsed = _uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _ContractViolationError(f"{label} must be a canonical UUID4 string") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise _ContractViolationError(f"{label} must be a canonical UUID4 string")
    return value


def _validated_access_audit_refs(value: object) -> tuple[_AccessAuditRef, ...]:
    if type(value) is not tuple:
        raise _ContractViolationError("access_audit_refs must be an exact tuple")
    refs = _typing.cast("tuple[object, ...]", value)
    sessions: set[str] = set()
    events: set[str] = set()
    checked: list[_AccessAuditRef] = []
    for ref in refs:
        if type(ref) is not _AccessAuditRef:
            raise _ContractViolationError("access_audit_refs entries must be exact")
        if ref._session_id in sessions:
            raise _ContractViolationError("access audit session IDs must be unique per run")
        sessions.add(ref._session_id)
        if ref._audit_event_id is not None:
            if ref._audit_event_id in events:
                raise _ContractViolationError("access audit event IDs must be unique per run")
            events.add(ref._audit_event_id)
        checked.append(ref)
    return tuple(checked)


def _access_audit_refs_from_document(value: object, *, label: str) -> tuple[_AccessAuditRef, ...]:
    rows = _exact_list(value, label=label)
    refs: list[_AccessAuditRef] = []
    for index, row in enumerate(rows):
        fields = _exact_dict(
            row,
            fields={"audit_event_id", "session_id"},
            label=f"{label} entry {index}",
        )
        refs.append(
            _AccessAuditRef(
                _typing.cast("str", fields["session_id"]),
                _typing.cast("str | None", fields["audit_event_id"]),
            )
        )
    return _validated_access_audit_refs(tuple(refs))


def _validated_terminal_reason(
    state: RunState,
    reason: object,
) -> str | None:
    if state is RunState.COMPLETE:
        if reason is not None:
            raise _ContractViolationError("COMPLETE transition cannot carry a reason")
        return None
    if type(reason) is not str or not reason.strip() or reason != reason.strip():
        raise _ContractViolationError("FAILED and INTERRUPTED transitions require a trimmed reason")
    return reason


def _heartbeat_is_stale(
    run_fd: int,
    *,
    now_unix_ns: int,
    stale_after_ns: int,
) -> bool:
    metadata = _safe_stat_at(run_fd, "heartbeat.json", label="heartbeat")
    return now_unix_ns - metadata.st_mtime_ns > stale_after_ns


def _touch_file_at(run_fd: int, name: str, *, now_unix_ns: int) -> None:
    _safe_stat_at(run_fd, name, label=name)
    try:
        _os.utime(
            name,
            ns=(now_unix_ns, now_unix_ns),
            dir_fd=run_fd,
            follow_symlinks=False,
        )
        descriptor = _os.open(name, _READ_FLAGS, dir_fd=run_fd)
        try:
            _os.fsync(descriptor)
        finally:
            _os.close(descriptor)
    except OSError as exc:
        raise _RunStoreError(f"cannot update heartbeat file {name!r}") from exc


def _root_payload_hashes(run_fd: int) -> tuple[tuple[str, str], ...]:
    names = {
        "config.resolved.json",
        "evaluation_inventory.json",
        "heartbeat.json",
        "identity.json",
    }
    if _regular_file_exists(run_fd, "determinism_report.json"):
        names.add("determinism_report.json")
    return tuple(
        (name, _hashlib.sha256(_read_regular_bytes_at(run_fd, name)).hexdigest())
        for name in sorted(names)
    )


def _directory_hashes(run_fd: int, name: str) -> tuple[tuple[str, str], ...]:
    directory_fd = _open_child_directory(run_fd, name, label=name)
    try:
        hashes: list[tuple[str, str]] = []
        for entry in sorted(_os.listdir(directory_fd)):
            if "/" in entry or entry in {"", ".", ".."}:
                raise _RunCorruptionError(f"unsafe {name} entry {entry!r}")
            metadata = _safe_stat_at(directory_fd, entry, label=f"{name} entry")
            if not _stat.S_ISREG(metadata.st_mode):
                raise _RunCorruptionError(f"{name} entry {entry!r} is not a file")
            hashes.append(
                (
                    entry,
                    _hashlib.sha256(_read_regular_bytes_at(directory_fd, entry)).hexdigest(),
                )
            )
        return tuple(hashes)
    finally:
        _os.close(directory_fd)


def _validate_run_layout(run_fd: int, *, terminal: bool) -> None:
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for name in _os.listdir(run_fd):
        metadata = _safe_lstat_at(run_fd, name, label="run entry")
        if _stat.S_ISREG(metadata.st_mode):
            if _PRIVATE_TEMP.fullmatch(name) is not None:
                continue
            observed_files.add(name)
        elif _stat.S_ISDIR(metadata.st_mode):
            observed_directories.add(name)
        else:
            raise _RunCorruptionError(f"unsafe run-directory entry {name!r}")
    required = {
        "config.resolved.json",
        "heartbeat.json",
        "identity.json",
        "state.json",
    }
    if terminal:
        required.update({"evaluation_inventory.json", "manifest.json"})
    if not required <= observed_files:
        raise _RunCorruptionError(
            f"run directory is missing files {sorted(required - observed_files)!r}"
        )
    if observed_files - _ROOT_FILES:
        raise _RunCorruptionError(
            f"run directory has unexpected files {sorted(observed_files - _ROOT_FILES)!r}"
        )
    if observed_directories != set(_PAYLOAD_DIRECTORIES):
        raise _RunCorruptionError("run payload directory set differs from V1 layout")


def _seal_run_directory(run_fd: int) -> None:
    for name in _os.listdir(run_fd):
        metadata = _safe_lstat_at(run_fd, name, label="terminal run entry")
        if _stat.S_ISREG(metadata.st_mode):
            _os.chmod(name, 0o400, dir_fd=run_fd, follow_symlinks=False)
        elif _stat.S_ISDIR(metadata.st_mode):
            child_fd = _open_child_directory(run_fd, name, label="terminal payload")
            try:
                for child in _os.listdir(child_fd):
                    child_metadata = _safe_lstat_at(
                        child_fd,
                        child,
                        label="terminal payload entry",
                    )
                    if not _stat.S_ISREG(child_metadata.st_mode):
                        raise _RunCorruptionError(
                            "terminal payload directories may contain files only"
                        )
                    _os.chmod(
                        child,
                        0o400,
                        dir_fd=child_fd,
                        follow_symlinks=False,
                    )
                _os.fchmod(child_fd, 0o500)
                _os.fsync(child_fd)
            finally:
                _os.close(child_fd)
        else:
            raise _RunCorruptionError("terminal run contains an unsafe entry")
    _os.fchmod(run_fd, 0o500)
    _os.fsync(run_fd)


def _open_run_collection(runs_root: _Path, *, create: bool) -> int | None:
    root_fd = _open_concrete_root(runs_root)
    try:
        try:
            return _open_child_directory(root_fd, "runs", label="run collection")
        except FileNotFoundError:
            if not create:
                return None
            try:
                _os.mkdir("runs", 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise _RunStoreError("cannot create run collection") from exc
            _os.fsync(root_fd)
            return _open_child_directory(root_fd, "runs", label="run collection")
    finally:
        _os.close(root_fd)


def _open_concrete_root(path: _Path) -> int:
    if not isinstance(path, _Path):
        raise _ContractViolationError("runs_root must be a pathlib.Path")
    if not path.is_absolute() or path == _Path(path.anchor) or ".." in path.parts:
        raise _ContractViolationError(
            "runs_root must be a concrete absolute directory below filesystem root"
        )
    current = _os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            child = _open_child_directory(current, component, label="runs_root")
            _os.close(current)
            current = child
        return current
    except BaseException:
        _os.close(current)
        raise


def _open_run_directory(runs_root: _Path, run_id: str) -> int:
    collection_fd = _open_run_collection(runs_root, create=False)
    if collection_fd is None:
        raise _RunStoreError("run collection does not exist")
    try:
        return _open_child_directory(collection_fd, run_id, label="run")
    finally:
        _os.close(collection_fd)


def _open_child_directory(parent_fd: int, name: str, *, label: str) -> int:
    try:
        descriptor = _os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _RunStoreError(f"cannot safely open {label} directory {name!r}") from exc
    try:
        if not _stat.S_ISDIR(_os.fstat(descriptor).st_mode):
            raise _RunStoreError(f"{label} entry {name!r} is not a directory")
    except BaseException:
        _os.close(descriptor)
        raise
    return descriptor


def _publish_immutable_bytes_at(directory_fd: int, name: str, payload: bytes) -> None:
    _validated_filename(name)
    if type(payload) is not bytes:
        raise _ContractViolationError("run payload must be exact bytes")
    if len(payload) > _RUN_FILE_LIMIT_BYTES:
        raise _ContractViolationError(
            f"run file {name!r} exceeds the {_RUN_FILE_LIMIT_BYTES}-byte V1 limit"
        )
    if _regular_file_exists(directory_fd, name):
        existing = _read_regular_bytes_at(directory_fd, name)
        if existing != payload:
            raise _RunCorruptionError(f"immutable run file {name!r} differs")
        return
    temp_name, descriptor = _create_temp_file(directory_fd, final_name=name)
    try:
        _write_complete(descriptor, payload, label=name)
        _os.fsync(descriptor)
        _os.close(descriptor)
        descriptor = -1
        try:
            _os.link(
                temp_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            if _read_regular_bytes_at(directory_fd, name) != payload:
                raise _RunCorruptionError(
                    f"concurrent immutable run file {name!r} differs"
                ) from exc
        _os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            _os.close(descriptor)
        with _suppress(FileNotFoundError):
            _os.unlink(temp_name, dir_fd=directory_fd)


def _atomic_replace_bytes_at(directory_fd: int, name: str, payload: bytes) -> None:
    _validated_filename(name)
    if name not in {"heartbeat.json", "state.json"}:
        raise _ContractViolationError("replace semantics are restricted to run state")
    if _regular_file_exists(directory_fd, name):
        _safe_stat_at(directory_fd, name, label=name)
    temp_name, descriptor = _create_temp_file(directory_fd, final_name=name)
    try:
        _write_complete(descriptor, payload, label=name)
        _os.fsync(descriptor)
        _os.close(descriptor)
        descriptor = -1
        _os.replace(
            temp_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        _os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            _os.close(descriptor)
        with _suppress(FileNotFoundError):
            _os.unlink(temp_name, dir_fd=directory_fd)


def _create_temp_file(directory_fd: int, *, final_name: str) -> tuple[str, int]:
    for _attempt in range(32):
        name = f".{final_name}.{_os.urandom(8).hex()}.tmp"
        try:
            return name, _os.open(name, _WRITE_FLAGS, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise _RunStoreError(f"cannot create private temp for {final_name!r}") from exc
    raise _RunStoreError(f"cannot allocate a private temp for {final_name!r}")


def _write_complete(descriptor: int, payload: bytes, *, label: str) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = _os.write(descriptor, payload[offset:])
        except OSError as exc:
            raise _RunStoreError(f"cannot write complete {label!r} temp") from exc
        if written <= 0 or written > len(payload) - offset:
            raise _RunStoreError(f"short or invalid write for {label!r}")
        offset += written


def _read_regular_bytes_at(directory_fd: int, name: str) -> bytes:
    _validated_filename(name)
    try:
        descriptor = _os.open(name, _READ_FLAGS, dir_fd=directory_fd)
    except OSError as exc:
        raise _RunCorruptionError(f"cannot safely read run file {name!r}") from exc
    try:
        metadata = _os.fstat(descriptor)
        if not _stat.S_ISREG(metadata.st_mode) or metadata.st_size > _RUN_FILE_LIMIT_BYTES:
            raise _RunCorruptionError(f"run file {name!r} is unsafe or too large")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = _os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise _RunCorruptionError(f"run file {name!r} ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        if _os.read(descriptor, 1):
            raise _RunCorruptionError(f"run file {name!r} grew during read")
        return b"".join(chunks)
    finally:
        _os.close(descriptor)


def _read_json_document_at(directory_fd: int, name: str) -> dict[str, object]:
    value = _parse_canonical_bytes(
        _read_regular_bytes_at(directory_fd, name),
        label=name,
    )
    if type(value) is not dict:
        raise _RunCorruptionError(f"{name} must contain a JSON object")
    return _typing.cast("dict[str, object]", value)


def _parse_canonical_bytes(payload: bytes, *, label: str) -> object:
    if type(payload) is not bytes or not payload.endswith(b"\n"):
        raise _RunCorruptionError(f"{label} must be canonical JSON plus one newline")
    try:
        text = payload[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise _RunCorruptionError(f"{label} is not canonical ASCII JSON") from exc
    value = _parse_canonical_text(text, label=label)
    if _canonical_identity_json(value) != text:
        raise _RunCorruptionError(f"{label} is not canonically encoded")
    return value


def _parse_canonical_text(text: object, *, label: str) -> object:
    if type(text) is not str:
        raise _ContractViolationError(f"{label} must be exact text")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return _json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant {value}")
            ),
        )
    except (TypeError, ValueError, _json.JSONDecodeError) as exc:
        raise _RunCorruptionError(f"{label} is malformed JSON") from exc


def _canonical_json_bytes(document: object) -> bytes:
    return _canonical_text_bytes(_canonical_identity_json(document))


def _canonical_text_bytes(text: str) -> bytes:
    if type(text) is not str:
        raise _ContractViolationError("canonical JSON must be exact text")
    return text.encode("ascii") + b"\n"


def _regular_file_exists(directory_fd: int, name: str) -> bool:
    try:
        metadata = _safe_lstat_at(directory_fd, name, label=name)
    except FileNotFoundError:
        return False
    if not _stat.S_ISREG(metadata.st_mode):
        raise _RunCorruptionError(f"run entry {name!r} is not a regular file")
    return True


def _safe_stat_at(directory_fd: int, name: str, *, label: str) -> _os.stat_result:
    metadata = _safe_lstat_at(directory_fd, name, label=label)
    if not _stat.S_ISREG(metadata.st_mode):
        raise _RunCorruptionError(f"{label} must be a regular file")
    return metadata


def _safe_lstat_at(directory_fd: int, name: str, *, label: str) -> _os.stat_result:
    _validated_filename(name)
    try:
        return _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _RunCorruptionError(f"cannot inspect {label} {name!r}") from exc


def _validated_filename(name: object) -> str:
    if type(name) is not str or not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise _ContractViolationError("run filename must be one safe path component")
    return name


def _validated_unix_ns(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise _ContractViolationError(f"{label} must be an exact non-negative int")
    return value


def _validated_digest(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _ContractViolationError(f"{label} must be lowercase SHA-256")
    return value


def _validated_hash_entries(
    value: object,
    *,
    label: str,
) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple:
        raise _ContractViolationError(f"{label} must be an exact tuple")
    entries = _typing.cast("tuple[object, ...]", value)
    names: list[str] = []
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise _ContractViolationError(f"{label} entries must be exact pairs")
        name, digest = entry
        names.append(_validated_filename(name))
        _validated_digest(digest, label=f"{label} digest")
    if names != sorted(names) or len(set(names)) != len(names):
        raise _ContractViolationError(f"{label} names must be sorted and unique")
    return _typing.cast("tuple[tuple[str, str], ...]", value)


def _hash_entries_from_document(value: object, *, label: str) -> tuple[tuple[str, str], ...]:
    if type(value) is not dict:
        raise _RunCorruptionError(f"RunManifest {label} hashes must be an object")
    document = _typing.cast("dict[object, object]", value)
    entries = tuple(sorted(document.items()))
    try:
        return _validated_hash_entries(entries, label=f"RunManifest {label}")
    except _ContractViolationError as exc:
        raise _RunCorruptionError(f"RunManifest {label} hashes are invalid") from exc


def _exact_dict(value: object, *, fields: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _RunCorruptionError(f"{label} fields differ from V1 schema")
    return _typing.cast("dict[str, object]", value)


def _exact_list(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise _RunCorruptionError(f"{label} must be an exact list")
    return _typing.cast("list[object]", value)


def _lock_run(run_fd: int, *, run_directory: _Path) -> None:
    _acquire_run_lock(
        run_fd,
        operation=_fcntl.LOCK_EX,
        mode="exclusive",
        run_directory=run_directory,
    )


def _lock_run_shared(run_fd: int, *, run_directory: _Path) -> None:
    _acquire_run_lock(
        run_fd,
        operation=_fcntl.LOCK_SH,
        mode="shared",
        run_directory=run_directory,
    )


def _acquire_run_lock(
    run_fd: int,
    *,
    operation: int,
    mode: str,
    run_directory: _Path,
) -> None:
    deadline = _time.monotonic() + _RUN_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            _fcntl.flock(run_fd, operation | _fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {_errno.EACCES, _errno.EAGAIN}:
                raise _RunStoreError(
                    f"cannot acquire {mode} lock for run directory {run_directory}"
                ) from exc
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            raise _RunLockTimeoutError(
                f"timed out after {_RUN_LOCK_TIMEOUT_SECONDS:g} seconds acquiring "
                f"{mode} lock for run directory {run_directory}"
            )
        _time.sleep(min(_RUN_LOCK_RETRY_SECONDS, remaining))


def _unlock_and_close(run_fd: int) -> None:
    try:
        _fcntl.flock(run_fd, _fcntl.LOCK_UN)
    finally:
        _os.close(run_fd)


def _cleanup_registration_temp(collection_fd: int, name: str) -> None:
    if _REGISTER_TEMP.fullmatch(name) is None:
        raise _RunStoreError("refusing to clean an unowned registration path")
    try:
        temp_fd = _open_child_directory(collection_fd, name, label="registration cleanup")
    except FileNotFoundError:
        return
    try:
        observed = set(_os.listdir(temp_fd))
        allowed = {
            "artifacts",
            "config.resolved.json",
            "heartbeat.json",
            "identity.json",
            "logs",
            "metrics",
            "state.json",
        }
        if observed - allowed:
            return
        for entry in sorted(observed):
            metadata = _safe_lstat_at(temp_fd, entry, label="registration cleanup")
            if _stat.S_ISREG(metadata.st_mode):
                _os.unlink(entry, dir_fd=temp_fd)
            elif _stat.S_ISDIR(metadata.st_mode):
                child_fd = _open_child_directory(temp_fd, entry, label="registration cleanup")
                try:
                    if _os.listdir(child_fd):
                        return
                finally:
                    _os.close(child_fd)
                _os.rmdir(entry, dir_fd=temp_fd)
            else:
                return
    finally:
        _os.close(temp_fd)
    try:
        _os.rmdir(name, dir_fd=collection_fd)
        _os.fsync(collection_fd)
    except FileNotFoundError:
        pass
