"""Deterministic sweep expansion and immutable orchestration manifests."""

from __future__ import annotations

import hashlib as _hashlib
import itertools as _itertools
import json as _json
import math as _math
import os as _os
import re as _re
import time as _time
import typing as _typing
import uuid as _uuid
from contextlib import suppress as _suppress
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from trading_research.artifacts.identity import PlatformIdentity as _PlatformIdentity
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.experiments.config import _config_from_document
from trading_research.experiments.orchestration import (
    _prepare_experiment,
    _RuntimeEnvironment,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Sequence as _Sequence

__all__: list[str] = []

_REQUEST_SCHEMA = "research-sweep-request-v1"
_MANIFEST_SCHEMA = "research-sweep-spec-v1"
_EXPANSION_ORDER = "declared_axes_last_fastest"
_SWEEP_ID = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_GIT_DIGEST = _re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_COMBINATIONS = frozenset({"cartesian", "zip"})


@_dataclass(frozen=True, slots=True)
class _SweepRuntimePaths:
    _data_roots: tuple[_Path, ...]
    _runs_root: _Path
    _split_policy_path: _Path
    _audit_path: _Path
    _repository_root: _Path
    _dependency_lock_path: _Path
    _heartbeat_interval_ns: int

    def __post_init__(self) -> None:
        if type(self._data_roots) is not tuple or not self._data_roots:
            raise _ContractViolationError("sweep runtime requires data roots")
        for path in (
            *self._data_roots,
            self._runs_root,
            self._split_policy_path,
            self._audit_path,
            self._repository_root,
            self._dependency_lock_path,
        ):
            if not isinstance(path, _Path) or not path.is_absolute():
                raise _ContractViolationError("sweep runtime paths must be absolute")
        if type(self._heartbeat_interval_ns) is not int or self._heartbeat_interval_ns <= 0:
            raise _ContractViolationError("sweep heartbeat interval must be a positive int")


@_dataclass(frozen=True, slots=True)
class _PlannedChild:
    _child_ordinal: int
    _config_digest: str
    _experiment_fingerprint: str
    _config_document: dict[str, object]


@_dataclass(frozen=True, slots=True)
class _SweepPlan:
    _document: dict[str, object]
    _children: tuple[_PlannedChild, ...]


def _load_sweep_request(path: _Path) -> dict[str, object]:
    if not isinstance(path, _Path) or not path.is_absolute():
        raise _ContractViolationError("sweep request path must be absolute")
    try:
        raw = path.read_bytes()
        value = _json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
    except (OSError, UnicodeError, _json.JSONDecodeError) as exc:
        raise _ContractViolationError("cannot read sweep request JSON") from exc
    document = _exact_dict(
        value,
        fields={"axes", "base_config", "combination", "schema", "sweep_id"},
        label="sweep request",
    )
    if document["schema"] != _REQUEST_SCHEMA:
        raise _ContractViolationError("sweep request schema is unsupported")
    _validated_sweep_id(document["sweep_id"])
    _validated_axes(document["axes"])
    if document["combination"] not in _COMBINATIONS:
        raise _ContractViolationError("sweep combination must be cartesian or zip")
    _config_from_document(document["base_config"])
    return _detached_document(document)


def _plan_sweep(
    request: dict[str, object],
    *,
    runtime: _RuntimeEnvironment,
    runtime_paths: _SweepRuntimePaths,
    spend_oos: bool,
    max_protected_children: int | None,
    protected_reason: str | None,
    now_unix_ns: int | None = None,
) -> _SweepPlan:
    if type(runtime) is not _RuntimeEnvironment:
        raise _ContractViolationError("sweep planning requires an exact runtime")
    if type(runtime_paths) is not _SweepRuntimePaths:
        raise _ContractViolationError("sweep planning requires exact runtime paths")
    if runtime._runs_root.resolve() != runtime_paths._runs_root.resolve():
        raise _ContractViolationError("sweep runtime roots disagree")
    if type(spend_oos) is not bool:
        raise _ContractViolationError("spend_oos must be an exact bool")
    created = _time.time_ns() if now_unix_ns is None else now_unix_ns
    if type(created) is not int or created <= 0:
        raise _ContractViolationError("sweep created_unix_ns must be a positive int")
    checked_request = _validated_request_document(request)
    expanded = _expand_request(checked_request)

    unique_children: list[_PlannedChild] = []
    duplicates: list[dict[str, int]] = []
    first_by_fingerprint: dict[str, int] = {}
    oos_children = 0
    for ordinal, child_document in expanded:
        role = _dataset_role(child_document)
        if role == "lockbox":
            raise _ContractViolationError("LOCKBOX sweeps are refused")
        config = _config_from_document(child_document)
        prepared = _prepare_experiment(config, runtime)
        fingerprint = prepared._identity._experiment_fingerprint
        digest = _digest_json(child_document)
        first = first_by_fingerprint.get(fingerprint)
        if first is not None:
            duplicates.append(
                {
                    "ordinal": ordinal,
                    "duplicate_of_ordinal": first,
                }
            )
            continue
        first_by_fingerprint[fingerprint] = ordinal
        unique_children.append(_PlannedChild(ordinal, digest, fingerprint, child_document))
        if role == "oos":
            oos_children += 1

    checked_limit, checked_reason = _validated_protected_spend(
        oos_children=oos_children,
        spend_oos=spend_oos,
        max_protected_children=max_protected_children,
        protected_reason=protected_reason,
    )
    _refuse_repository_runs_root(runtime_paths)
    platform = runtime._platform_identity
    document: dict[str, object] = {
        "schema": _MANIFEST_SCHEMA,
        "sweep_id": checked_request["sweep_id"],
        "spec_digest": "",
        "created_unix_ns": created,
        "base_config": checked_request["base_config"],
        "axes": checked_request["axes"],
        "combination": checked_request["combination"],
        "expansion_order": _EXPANSION_ORDER,
        "platform": _platform_document(platform),
        "dependency_lock_sha256": runtime._dependency_lock_sha256,
        "runtime": _runtime_paths_document(runtime_paths),
        "children": [
            {
                "child_ordinal": child._child_ordinal,
                "config_digest": child._config_digest,
                "experiment_fingerprint": child._experiment_fingerprint,
            }
            for child in unique_children
        ],
        "duplicates_collapsed": duplicates,
        "protection": {
            "spend_oos": spend_oos,
            "max_protected_children": checked_limit,
            "reason": checked_reason,
            "oos_child_count": oos_children,
            "oos_access_events_at_plan": _count_oos_access_events(runtime_paths._audit_path),
        },
    }
    document["spec_digest"] = _manifest_digest(document)
    _write_sweep_manifest(runtime_paths._runs_root, document)
    return _SweepPlan(document, tuple(unique_children))


def _read_sweep_plan(runs_root: _Path, sweep_id: str) -> _SweepPlan:
    checked_id = _validated_sweep_id(sweep_id)
    if not isinstance(runs_root, _Path) or not runs_root.is_absolute():
        raise _ContractViolationError("runs_root must be absolute")
    path = runs_root / "sweeps" / checked_id / "sweep.json"
    try:
        value = _json.loads(path.read_text(encoding="ascii"), parse_constant=_reject_nonfinite)
    except (OSError, UnicodeError, _json.JSONDecodeError) as exc:
        raise _ContractViolationError(f"cannot read sweep {checked_id!r}") from exc
    document = _validated_manifest_document(value)
    if document["sweep_id"] != checked_id:
        raise _ContractViolationError("sweep directory and manifest IDs differ")
    children = _children_from_manifest(document)
    return _SweepPlan(document, children)


def _runtime_paths_from_manifest(document: dict[str, object]) -> _SweepRuntimePaths:
    runtime = _exact_dict(
        document["runtime"],
        fields={
            "audit_path",
            "data_roots",
            "dependency_lock_path",
            "heartbeat_interval_ns",
            "repository_root",
            "runs_root",
            "split_policy_path",
        },
        label="sweep runtime",
    )
    data_roots = _exact_list(runtime["data_roots"], label="sweep data roots")
    return _SweepRuntimePaths(
        tuple(_absolute_path(value, label="data root") for value in data_roots),
        _absolute_path(runtime["runs_root"], label="runs root"),
        _absolute_path(runtime["split_policy_path"], label="split policy path"),
        _absolute_path(runtime["audit_path"], label="audit path"),
        _absolute_path(runtime["repository_root"], label="repository root"),
        _absolute_path(runtime["dependency_lock_path"], label="dependency lock path"),
        _exact_positive_int(runtime["heartbeat_interval_ns"], label="heartbeat interval"),
    )


def _verify_sweep_fingerprints(plan: _SweepPlan, runtime: _RuntimeEnvironment) -> None:
    for child in plan._children:
        config = _config_from_document(child._config_document)
        observed = _prepare_experiment(config, runtime)._identity._experiment_fingerprint
        if observed != child._experiment_fingerprint:
            raise _ContractViolationError(
                "sweep child fingerprint differs from the current complete runtime identity"
            )


def _validated_request_document(value: object) -> dict[str, object]:
    document = _exact_dict(
        value,
        fields={"axes", "base_config", "combination", "schema", "sweep_id"},
        label="sweep request",
    )
    if document["schema"] != _REQUEST_SCHEMA:
        raise _ContractViolationError("sweep request schema is unsupported")
    _validated_sweep_id(document["sweep_id"])
    _validated_axes(document["axes"])
    if document["combination"] not in _COMBINATIONS:
        raise _ContractViolationError("sweep combination must be cartesian or zip")
    _config_from_document(document["base_config"])
    return _detached_document(document)


def _expand_request(request: dict[str, object]) -> tuple[tuple[int, dict[str, object]], ...]:
    base = _detached_mapping(request["base_config"], label="base config")
    axes = _validated_axes(request["axes"])
    combination = request["combination"]
    value_sets = tuple(axis[1] for axis in axes)
    if combination == "zip":
        lengths = {len(values) for values in value_sets}
        if len(lengths) != 1:
            raise _ContractViolationError("zip sweep axes must have equal lengths")
        combinations: _Sequence[tuple[object, ...]] = tuple(zip(*value_sets, strict=True))
    else:
        combinations = tuple(_itertools.product(*value_sets))
    expanded: list[tuple[int, dict[str, object]]] = []
    for ordinal, values in enumerate(combinations):
        child = _detached_mapping(base, label="base config")
        for (pointer, _axis_values), replacement in zip(axes, values, strict=True):
            _replace_pointer(child, pointer, replacement)
        _config_from_document(child)
        expanded.append((ordinal, child))
    return tuple(expanded)


def _validated_axes(value: object) -> tuple[tuple[str, tuple[object, ...]], ...]:
    rows = _exact_list(value, label="sweep axes")
    if not rows:
        raise _ContractViolationError("sweep requires at least one axis")
    axes: list[tuple[str, tuple[object, ...]]] = []
    paths: list[tuple[str, ...]] = []
    for index, row in enumerate(rows):
        axis = _exact_dict(
            row,
            fields={"json_pointer", "values"},
            label=f"sweep axis {index}",
        )
        pointer = _validated_pointer(axis["json_pointer"])
        values = _exact_list(axis["values"], label=f"sweep axis {index} values")
        if not values:
            raise _ContractViolationError("sweep axis values must not be empty")
        decoded = _pointer_parts(pointer)
        if decoded in paths:
            raise _ContractViolationError("sweep axis JSON pointers must be unique")
        if any(_paths_overlap(decoded, prior) for prior in paths):
            raise _ContractViolationError("sweep axes must not target ancestor paths")
        paths.append(decoded)
        axes.append((pointer, tuple(_detached_value(item, label="axis value") for item in values)))
    return tuple(axes)


def _replace_pointer(document: dict[str, object], pointer: str, replacement: object) -> None:
    parts = _pointer_parts(pointer)
    current: object = document
    for part in parts[:-1]:
        if type(current) is dict:
            if part not in current:
                raise _ContractViolationError(f"sweep pointer {pointer!r} does not exist")
            current = current[part]
        elif type(current) is list:
            current = current[_list_index(part, current, pointer=pointer)]
        else:
            raise _ContractViolationError(f"sweep pointer {pointer!r} traverses a scalar")
    final = parts[-1]
    detached = _detached_value(replacement, label="axis replacement")
    if type(current) is dict:
        if final not in current:
            raise _ContractViolationError(f"sweep pointer {pointer!r} does not exist")
        current[final] = detached
    elif type(current) is list:
        current[_list_index(final, current, pointer=pointer)] = detached
    else:
        raise _ContractViolationError(f"sweep pointer {pointer!r} targets a scalar parent")


def _pointer_parts(pointer: str) -> tuple[str, ...]:
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/"))


def _validated_pointer(value: object) -> str:
    if type(value) is not str or not value.startswith("/") or value == "/":
        raise _ContractViolationError("sweep axis requires a non-root JSON pointer")
    for raw in value[1:].split("/"):
        if not raw:
            raise _ContractViolationError("sweep JSON pointer segments must not be empty")
        index = 0
        while index < len(raw):
            if raw[index] != "~":
                if ord(raw[index]) < 32:
                    raise _ContractViolationError("sweep JSON pointer must be printable")
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise _ContractViolationError("sweep JSON pointer escape is invalid")
            index += 2
    return value


def _list_index(value: str, items: list[object], *, pointer: str) -> int:
    if not value.isascii() or not value.isdecimal() or (value.startswith("0") and value != "0"):
        raise _ContractViolationError(f"sweep pointer {pointer!r} has an invalid array index")
    index = int(value)
    if index >= len(items):
        raise _ContractViolationError(f"sweep pointer {pointer!r} is outside its array")
    return index


def _paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    shorter = min(len(left), len(right))
    return left[:shorter] == right[:shorter]


def _validated_protected_spend(
    *,
    oos_children: int,
    spend_oos: bool,
    max_protected_children: int | None,
    protected_reason: str | None,
) -> tuple[int | None, str | None]:
    if oos_children == 0:
        if spend_oos or max_protected_children is not None or protected_reason is not None:
            raise _ContractViolationError("OOS spend flags are legal only when an OOS child exists")
        return None, None
    if not spend_oos:
        raise _ContractViolationError("OOS sweep planning requires explicit spend_oos")
    if type(max_protected_children) is not int or max_protected_children <= 0:
        raise _ContractViolationError("OOS sweep requires max_protected_children with no default")
    if oos_children > max_protected_children:
        raise _ContractViolationError("OOS child count exceeds max_protected_children")
    if (
        type(protected_reason) is not str
        or not protected_reason.strip()
        or protected_reason != protected_reason.strip()
    ):
        raise _ContractViolationError("OOS sweep requires a trimmed protected reason")
    return max_protected_children, protected_reason


def _dataset_role(document: dict[str, object]) -> str:
    dataset = _exact_dict(
        document.get("dataset"),
        fields={"dataset_id", "manifest_sha256", "role"},
        label="sweep child dataset",
    )
    role = dataset["role"]
    if role not in {"train", "validation", "oos", "lockbox"}:
        raise _ContractViolationError("sweep child dataset role is invalid")
    return role


def _validated_manifest_document(value: object) -> dict[str, object]:
    document = _exact_dict(
        value,
        fields={
            "axes",
            "base_config",
            "children",
            "combination",
            "created_unix_ns",
            "dependency_lock_sha256",
            "duplicates_collapsed",
            "expansion_order",
            "platform",
            "protection",
            "runtime",
            "schema",
            "spec_digest",
            "sweep_id",
        },
        label="sweep manifest",
    )
    if document["schema"] != _MANIFEST_SCHEMA:
        raise _ContractViolationError("sweep manifest schema is unsupported")
    _validated_sweep_id(document["sweep_id"])
    _exact_positive_int(document["created_unix_ns"], label="sweep created time")
    _validated_sha256(document["dependency_lock_sha256"], label="dependency lock")
    _validated_sha256(document["spec_digest"], label="sweep spec")
    if document["spec_digest"] != _manifest_digest(document):
        raise _ContractViolationError("sweep manifest digest differs")
    if document["expansion_order"] != _EXPANSION_ORDER:
        raise _ContractViolationError("sweep expansion-order rule differs")
    if document["combination"] not in _COMBINATIONS:
        raise _ContractViolationError("sweep combination is unsupported")
    _validated_axes(document["axes"])
    _config_from_document(document["base_config"])
    _validated_platform_document(document["platform"])
    _runtime_paths_from_manifest(document)
    _validated_protection(document["protection"])
    return _detached_document(document)


def _children_from_manifest(document: dict[str, object]) -> tuple[_PlannedChild, ...]:
    request = {
        "schema": _REQUEST_SCHEMA,
        "sweep_id": document["sweep_id"],
        "base_config": document["base_config"],
        "axes": document["axes"],
        "combination": document["combination"],
    }
    expanded = dict(_expand_request(request))
    duplicate_rows = _exact_list(
        document["duplicates_collapsed"],
        label="sweep duplicates",
    )
    duplicate_ordinals: set[int] = set()
    for raw in duplicate_rows:
        row = _exact_dict(
            raw,
            fields={"duplicate_of_ordinal", "ordinal"},
            label="sweep duplicate",
        )
        ordinal = _exact_nonnegative_int(row["ordinal"], label="duplicate ordinal")
        original = _exact_nonnegative_int(
            row["duplicate_of_ordinal"],
            label="original ordinal",
        )
        if ordinal not in expanded or original not in expanded or original >= ordinal:
            raise _ContractViolationError("sweep duplicate ordinals are invalid")
        duplicate_ordinals.add(ordinal)
    rows = _exact_list(document["children"], label="sweep children")
    children: list[_PlannedChild] = []
    observed_ordinals: set[int] = set()
    for raw in rows:
        row = _exact_dict(
            raw,
            fields={"child_ordinal", "config_digest", "experiment_fingerprint"},
            label="sweep child",
        )
        ordinal = _exact_nonnegative_int(row["child_ordinal"], label="child ordinal")
        if ordinal in observed_ordinals or ordinal in duplicate_ordinals or ordinal not in expanded:
            raise _ContractViolationError("sweep child ordinal is duplicated or invalid")
        observed_ordinals.add(ordinal)
        config = expanded[ordinal]
        digest = _validated_sha256(row["config_digest"], label="child config")
        fingerprint = _validated_sha256(
            row["experiment_fingerprint"],
            label="child fingerprint",
        )
        if digest != _digest_json(config):
            raise _ContractViolationError("sweep child config digest differs")
        children.append(_PlannedChild(ordinal, digest, fingerprint, config))
    expected = set(expanded) - duplicate_ordinals
    if observed_ordinals != expected:
        raise _ContractViolationError("sweep child and duplicate coverage is incomplete")
    return tuple(children)


def _validated_protection(value: object) -> None:
    document = _exact_dict(
        value,
        fields={
            "max_protected_children",
            "oos_access_events_at_plan",
            "oos_child_count",
            "reason",
            "spend_oos",
        },
        label="sweep protection",
    )
    count = _exact_nonnegative_int(document["oos_child_count"], label="OOS child count")
    _exact_nonnegative_int(
        document["oos_access_events_at_plan"],
        label="plan-time OOS access count",
    )
    spend = document["spend_oos"]
    if type(spend) is not bool:
        raise _ContractViolationError("sweep spend_oos must be a bool")
    _validated_protected_spend(
        oos_children=count,
        spend_oos=spend,
        max_protected_children=_typing.cast("int | None", document["max_protected_children"]),
        protected_reason=_typing.cast("str | None", document["reason"]),
    )


def _runtime_paths_document(paths: _SweepRuntimePaths) -> dict[str, object]:
    return {
        "data_roots": [str(path) for path in paths._data_roots],
        "runs_root": str(paths._runs_root),
        "split_policy_path": str(paths._split_policy_path),
        "audit_path": str(paths._audit_path),
        "repository_root": str(paths._repository_root),
        "dependency_lock_path": str(paths._dependency_lock_path),
        "heartbeat_interval_ns": paths._heartbeat_interval_ns,
    }


def _platform_document(platform: _PlatformIdentity) -> dict[str, object]:
    return {
        "git_commit": platform.git_commit,
        "git_dirty": platform.git_dirty,
        "diff_sha256": platform.diff_sha256,
    }


def _validated_platform_document(value: object) -> _PlatformIdentity:
    document = _exact_dict(
        value,
        fields={"diff_sha256", "git_commit", "git_dirty"},
        label="sweep platform",
    )
    commit = document["git_commit"]
    if type(commit) is not str or _GIT_DIGEST.fullmatch(commit) is None:
        raise _ContractViolationError("sweep Git commit is invalid")
    dirty = document["git_dirty"]
    if type(dirty) is not bool:
        raise _ContractViolationError("sweep Git dirty flag is invalid")
    diff = document["diff_sha256"]
    if dirty:
        _validated_sha256(diff, label="sweep Git diff")
    elif diff is not None:
        raise _ContractViolationError("clean sweep platform cannot carry a diff digest")
    return _PlatformIdentity(commit, dirty, _typing.cast("str | None", diff))


def _manifest_digest(document: dict[str, object]) -> str:
    payload = dict(document)
    payload.pop("spec_digest", None)
    return _digest_json(payload)


def _write_sweep_manifest(runs_root: _Path, document: dict[str, object]) -> None:
    sweep_id = _validated_sweep_id(document["sweep_id"])
    root = runs_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    collection = root / "sweeps"
    collection.mkdir(mode=0o700, exist_ok=True)
    if collection.is_symlink():
        raise _ContractViolationError("sweep collection must not be a symbolic link")
    temp = collection / f".plan-{_uuid.uuid4().hex}.tmp"
    target = collection / sweep_id
    try:
        temp.mkdir(mode=0o700)
        path = temp / "sweep.json"
        payload = (_canonical_json(document) + "\n").encode("ascii")
        descriptor = _os.open(
            path,
            _os.O_WRONLY
            | _os.O_CREAT
            | _os.O_EXCL
            | getattr(_os, "O_CLOEXEC", 0)
            | getattr(_os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_all(descriptor, payload)
            _os.fsync(descriptor)
        finally:
            _os.close(descriptor)
        _os.chmod(path, 0o400)
        directory_fd = _os.open(temp, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            _os.fsync(directory_fd)
        finally:
            _os.close(directory_fd)
        if target.exists():
            raise _ContractViolationError(f"sweep {sweep_id!r} already exists")
        temp.rename(target)
        # The manifest itself is immutable (0400).  The containing directory
        # remains writable for append-only invocation receipts and the
        # transient dispatcher lock; neither is consulted as scheduler state.
        _os.chmod(target, 0o700)
        collection_fd = _os.open(collection, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            _os.fsync(collection_fd)
        finally:
            _os.close(collection_fd)
    except BaseException:
        _cleanup_private_plan(temp)
        raise


def _cleanup_private_plan(path: _Path) -> None:
    if not path.name.startswith(".plan-") or not path.name.endswith(".tmp"):
        raise _ContractViolationError("refusing to clean an unowned sweep path")
    with _suppress(FileNotFoundError):
        child = path / "sweep.json"
        with _suppress(FileNotFoundError):
            child.unlink()
        path.rmdir()


def _count_oos_access_events(path: _Path) -> int:
    if not path.exists():
        return 0
    if not path.is_file() or path.is_symlink():
        raise _ContractViolationError("audit path must be a concrete file")
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.endswith("\n"):
                    raise _ContractViolationError("audit log has an incomplete final line")
                value = _json.loads(line, parse_constant=_reject_nonfinite)
                if type(value) is not dict:
                    raise _ContractViolationError(
                        f"audit event at line {line_number} is not an object"
                    )
                if value.get("outcome") == "allowed" and value.get("classification") in {
                    "oos_explicit",
                    "fully_gated",
                }:
                    count += 1
    except (OSError, UnicodeError, _json.JSONDecodeError) as exc:
        raise _ContractViolationError("cannot read the append-only audit log") from exc
    return count


def _refuse_repository_runs_root(paths: _SweepRuntimePaths) -> None:
    runs_root = paths._runs_root.resolve()
    repository = paths._repository_root.resolve()
    if runs_root == repository or runs_root.is_relative_to(repository):
        raise _ContractViolationError("sweep manifests must live outside the repository")


def _absolute_path(value: object, *, label: str) -> _Path:
    if type(value) is not str:
        raise _ContractViolationError(f"{label} must be an absolute path string")
    path = _Path(value)
    if not path.is_absolute():
        raise _ContractViolationError(f"{label} must be absolute")
    return path


def _validated_sweep_id(value: object) -> str:
    if type(value) is not str or _SWEEP_ID.fullmatch(value) is None:
        raise _ContractViolationError("sweep_id must be 1-64 safe name characters")
    return value


def _validated_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _ContractViolationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _exact_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise _ContractViolationError(f"{label} must be an exact positive int")
    return value


def _exact_nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise _ContractViolationError(f"{label} must be an exact non-negative int")
    return value


def _detached_document(value: object) -> dict[str, object]:
    return _detached_mapping(value, label="document")


def _detached_mapping(value: object, *, label: str) -> dict[str, object]:
    detached = _detached_value(value, label=label)
    if type(detached) is not dict:
        raise _ContractViolationError(f"{label} must be an object")
    return detached


def _detached_value(value: object, *, label: str) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not _math.isfinite(value):
            raise _ContractViolationError(f"{label} contains a non-finite float")
        return value
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise _ContractViolationError(f"{label} has an invalid object key")
            result[key] = _detached_value(item, label=f"{label}.{key}")
        return result
    if type(value) is list:
        return [
            _detached_value(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    raise _ContractViolationError(f"{label} contains a non-JSON value")


def _exact_dict(value: object, *, fields: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _ContractViolationError(f"{label} fields differ from the closed schema")
    return value


def _exact_list(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise _ContractViolationError(f"{label} must be an array")
    return value


def _digest_json(value: object) -> str:
    return _hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _canonical_json(value: object) -> str:
    try:
        return _json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _ContractViolationError("sweep document is not canonical JSON data") from exc


def _reject_nonfinite(value: str) -> _typing.NoReturn:
    raise _json.JSONDecodeError("non-finite JSON number", value, 0)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = _os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while publishing sweep manifest")
        view = view[written:]
