#!/usr/bin/env python3
"""Owner-operated Slice 1F Stage-B view-generation composition root.

Bare invocation is the metadata-only ``preflight`` mode.  The production
path is reachable only as ``execute --confirm "GENERATE VIEWS"``.  This
module deliberately owns no row scanning, admission arithmetic, manifest
construction, audit-event construction, or publication logic; those remain
in the accepted data-layer contracts it composes.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TextIO, cast
from uuid import uuid4

import trading_research.data.views_generation as _generation
from trading_research.core.errors import TradingResearchError
from trading_research.core.identity import DatasetIdentity, DatasetRef, ViewRole
from trading_research.data.audit import JsonlAuditWriter, operational_event_time_utc
from trading_research.data.gating import (
    AccessClassification,
    AccessContext,
    AccessGrant,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    SplitPolicy,
    SplitPolicyIdentity,
    authorize_access,
    classify_access,
    read_split_policy,
)
from trading_research.data.integrity import MarketFrameLoader
from trading_research.data.manifests import (
    ManifestFormat,
    NormalizedManifest,
    ViewManifestV11,
    read_manifest,
)
from trading_research.data.registry import DatasetRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_CONFIRMATION_LITERAL = "GENERATE VIEWS"
_GENERATOR_NAME = "slice1f-view-generator"
_GENERATOR_VERSION = "1"
_ACCESS_REASON_CODE = "slice1f-view-generation"
_ACCESS_REASON_DETAIL = (
    "Owner-authorized Slice 1F Stage-B keys-only CURATED read for SNAPSHOT "
    "binding and Tier-1 view generation."
)
_POLICY_CAP_ENCODED = "2026-07-31T23:59:00"
_CANONICAL_FINGERPRINT_PIPELINE = (
    "find . -mindepth 1 -printf '%y %m %s %p\\n' | LC_ALL=C sort | sha256sum"
)
_REAL_TRAIN_SMOKE_NODE = (
    "tests/test_integrity_real.py::test_ephemeral_real_train_target_loads_after_full_integrity"
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNING_SCRIPT = Path(__file__).resolve()
_DATASET_ROOT = Path("/path/to/research-data")
_VIEWS_ROOT = Path("/path/to/research-views")
_AUDIT_PATH = Path("/path/to/research-runs/audit.jsonl")
_POLICY_PATH = _REPO_ROOT / "config/split-policies/adr-0012-v1.json"

_RAW_IDENTITY = DatasetIdentity(
    dataset_id="XAUUSD_S_M1_MT5_V1",
    manifest_sha256="8f91f4516b9a1dbe46c82152fe78021151bc4a3188f623ea6ff9a2c659813cb0",
)
_CURATED_REF = DatasetRef(
    dataset_id="XAUUSD_S_M1_CURATED_V1",
    manifest_sha256="6f5c7b50e5ee40ee83913f97ba9164713d1296f234e9a759315c6182dbaf0da2",
    role=ViewRole.CURATED,
)
_POLICY_IDENTITY = SplitPolicyIdentity(
    adr="ADR-0012",
    policy_version="v1",
    policy_sha256="b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb",
)
_VIEW_IDS = _generation._ViewIds(
    snapshot="XAUUSD_S_M1_SNAPSHOT_V1",
    train="XAUUSD_S_M1_TRAIN_V1",
    validation="XAUUSD_S_M1_VALIDATION_V1",
    oos="XAUUSD_S_M1_OOS_V1",
    lockbox="XAUUSD_S_M1_LOCKBOX_V1",
)
_EXPECTED_SOURCE_FINGERPRINT = "2edb0a03ab4b503ef40eef6c24dc1ac1dd6d4e2fd98e7726e1ad0ad9e62e9743"

_TIER_ONE_ROLES = (
    ViewRole.TRAIN,
    ViewRole.VALIDATION,
    ViewRole.OOS,
    ViewRole.LOCKBOX,
)
_EXPECTED_DISCOVERY = {
    ViewRole.TRAIN: True,
    ViewRole.VALIDATION: True,
    ViewRole.OOS: False,
    ViewRole.LOCKBOX: False,
}
_EXPECTED_CLASSIFICATION = {
    ViewRole.TRAIN: AccessClassification.NORMAL,
    ViewRole.VALIDATION: AccessClassification.NORMAL,
    ViewRole.OOS: AccessClassification.OOS_EXPLICIT,
    ViewRole.LOCKBOX: AccessClassification.FULLY_GATED,
}

_USAGE = f"""Usage:
  uv run python scripts/generate_views.py
  uv run python scripts/generate_views.py preflight
  uv run python scripts/generate_views.py execute --confirm \"{_CONFIRMATION_LITERAL}\"

preflight is the safe default. It performs Git, manifest, policy, registry,
filesystem, and metadata-only fingerprint checks. Its only permitted side
effect is creation of the empty canonical views root when absent. It creates
no view directory, manifest, grant, audit record, or market-data read.

execute performs the same preflight first. A complete valid bundle is a
terminal no-op. Otherwise, the exact confirmation is required before the real
TRAIN smoke, CURATED authorization, keys-only loader call, and accepted bundle
executor can run.
"""


class _ScriptError(RuntimeError):
    """The operational script must refuse before or during Stage B."""


class _UsageError(_ScriptError):
    """The command line does not match the approved two-mode interface."""


class _CommandMode(StrEnum):
    PREFLIGHT = "preflight"
    EXECUTE = "execute"


class _BundleStatus(StrEnum):
    FRESH = "fresh"
    PARTIAL_RESUME = "partial resume"
    COMPLETE = "already complete"


@dataclass(frozen=True, slots=True)
class _Arguments:
    mode: _CommandMode
    confirmation: str | None


@dataclass(frozen=True, slots=True)
class _GitState:
    head: str
    local_main: str
    origin_main: str
    clean: bool
    script_committed_at_head: bool


@dataclass(frozen=True, slots=True)
class _SourceFingerprint:
    sha256: str
    entry_count: int


@dataclass(frozen=True, slots=True)
class _ManifestDigests:
    raw: str
    curated: str


@dataclass(frozen=True, slots=True)
class _StageBConfig:
    repo_root: Path
    script_path: Path
    dataset_root: Path
    views_root: Path
    audit_path: Path
    policy_path: Path
    source_ref: DatasetRef
    partition_source: DatasetIdentity
    policy_identity: SplitPolicyIdentity
    view_ids: _generation._ViewIds
    generator_name: str
    generator_version: str
    policy_cap_encoded: str
    expected_source_fingerprint: str | None
    require_canonical_main: bool = True

    def __post_init__(self) -> None:
        for label, path in (
            ("repo_root", self.repo_root),
            ("script_path", self.script_path),
            ("dataset_root", self.dataset_root),
            ("views_root", self.views_root),
            ("audit_path", self.audit_path),
            ("policy_path", self.policy_path),
        ):
            if not path.is_absolute():
                raise ValueError(f"{label} must be an explicit absolute path")


class _AuditAppender(Protocol):
    def append(self, event: Mapping[str, object]) -> None:
        """Durably append one accepted audit event."""


class _InspectGitHook(Protocol):
    def __call__(self, repo_root: Path, script_path: Path) -> _GitState: ...


class _FingerprintHook(Protocol):
    def __call__(self, root: Path) -> _SourceFingerprint: ...


class _GeneratedAtHook(Protocol):
    def __call__(self) -> str: ...


class _PrepareHook(Protocol):
    def __call__(
        self,
        *,
        registry: DatasetRegistry,
        policy: SplitPolicy,
        spec: _generation._BundleSpec,
    ) -> _generation._PreparedBundle: ...


class _SmokeHook(Protocol):
    def __call__(self, repo_root: Path) -> None: ...


class _AuthorizeHook(Protocol):
    def __call__(
        self,
        *,
        registry: DatasetRegistry,
        request: AccessRequest,
        policy: SplitPolicy,
        context: AccessContext,
        audit_writer: _AuditAppender,
    ) -> AccessGrant: ...


class _ExecuteHook(Protocol):
    def __call__(
        self,
        *,
        prepared: _generation._PreparedBundle,
        loader: MarketFrameLoader,
        grant: AccessGrant,
        context: AccessContext,
        audit_writer: _AuditAppender,
    ) -> _generation._GenerationResult: ...


@dataclass(frozen=True, slots=True)
class _Hooks:
    inspect_git: _InspectGitHook
    fingerprint: _FingerprintHook
    generated_at_utc: _GeneratedAtHook
    prepare_bundle: _PrepareHook
    run_train_smoke: _SmokeHook
    authorize_curated: _AuthorizeHook
    execute_bundle: _ExecuteHook


@dataclass(frozen=True, slots=True)
class _PreflightReport:
    git: _GitState
    fingerprint: _SourceFingerprint
    manifest_digests: _ManifestDigests
    policy: SplitPolicy
    registry: DatasetRegistry
    prepared: _generation._PreparedBundle
    bundle_status: _BundleStatus
    existing_children: tuple[ViewRole, ...]
    generated_at_utc: str
    views_root_before: str
    views_root_created: bool
    audit_state: str


_PRODUCTION_CONFIG = _StageBConfig(
    repo_root=_REPO_ROOT,
    script_path=_RUNNING_SCRIPT,
    dataset_root=_DATASET_ROOT,
    views_root=_VIEWS_ROOT,
    audit_path=_AUDIT_PATH,
    policy_path=_POLICY_PATH,
    source_ref=_CURATED_REF,
    partition_source=_RAW_IDENTITY,
    policy_identity=_POLICY_IDENTITY,
    view_ids=_VIEW_IDS,
    generator_name=_GENERATOR_NAME,
    generator_version=_GENERATOR_VERSION,
    policy_cap_encoded=_POLICY_CAP_ENCODED,
    expected_source_fingerprint=_EXPECTED_SOURCE_FINGERPRINT,
)


def _parse_arguments(argv: Sequence[str]) -> _Arguments | None:
    """Parse only the approved two-mode interface and help flag."""
    if not argv:
        return _Arguments(mode=_CommandMode.PREFLIGHT, confirmation=None)
    if list(argv) in (["-h"], ["--help"]):
        return None
    if list(argv) == ["preflight"]:
        return _Arguments(mode=_CommandMode.PREFLIGHT, confirmation=None)
    if argv[0] != "execute":
        raise _UsageError("expected preflight or execute")
    if len(argv) == 1:
        return _Arguments(mode=_CommandMode.EXECUTE, confirmation=None)
    if len(argv) == 3 and argv[1] == "--confirm":
        return _Arguments(mode=_CommandMode.EXECUTE, confirmation=argv[2])
    raise _UsageError(f'execute accepts only --confirm "{_CONFIRMATION_LITERAL}"')


def _git_output(repo_root: Path, *arguments: str) -> bytes:
    """Return exact Git command output or fail closed."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _ScriptError(f"Git inspection failed for {arguments!r}: {exc}") from exc
    return completed.stdout


def _inspect_git(repo_root: Path, script_path: Path) -> _GitState:
    """Resolve the exact reviewed-code identity without changing Git state."""
    head = _git_output(repo_root, "rev-parse", "HEAD").decode().strip()
    local_main = _git_output(repo_root, "rev-parse", "refs/heads/main").decode().strip()
    origin_main = _git_output(repo_root, "rev-parse", "refs/remotes/origin/main").decode().strip()
    clean = not _git_output(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    try:
        relative_script = script_path.relative_to(repo_root)
    except ValueError:
        committed = False
    else:
        try:
            committed_bytes = _git_output(repo_root, "show", f"{head}:{relative_script.as_posix()}")
            committed = script_path.is_file() and script_path.read_bytes() == committed_bytes
        except (_ScriptError, OSError):
            committed = False
    return _GitState(
        head=head,
        local_main=local_main,
        origin_main=origin_main,
        clean=clean,
        script_committed_at_head=committed,
    )


def _canonical_source_fingerprint(root: Path) -> _SourceFingerprint:
    """Run the approved GNU-find/C-locale metadata fingerprint exactly."""
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        found = subprocess.run(
            ["find", ".", "-mindepth", "1", "-printf", "%y %m %s %p\n"],
            cwd=root,
            check=True,
            capture_output=True,
            env=environment,
        )
        ordered = subprocess.run(
            ["sort"],
            check=True,
            input=found.stdout,
            capture_output=True,
            env=environment,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _ScriptError(
            f"canonical immutable-source fingerprint failed for {root}: {exc}"
        ) from exc
    return _SourceFingerprint(
        sha256=hashlib.sha256(ordered).hexdigest(),
        entry_count=found.stdout.count(b"\n"),
    )


def _fingerprint_command(root: Path) -> str:
    """Render the exact D-6 method with its configured dataset root."""
    return f"cd {shlex.quote(str(root))} && {_CANONICAL_FINGERPRINT_PIPELINE}"


def _run_real_train_smoke(repo_root: Path) -> None:
    """Run the one accepted real TRAIN integration-smoke node."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", _REAL_TRAIN_SMOKE_NODE],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise _ScriptError(f"real TRAIN smoke could not start: {exc}") from exc
    evidence = f"{completed.stdout}\n{completed.stderr}"
    disallowed_outcomes = ("skipped", "xfailed", "xpassed", "deselected")
    one_pass_summary = re.search(
        r"(?m)^\s*1 passed(?:, [^\n]+)? in [^\n]+\s*$",
        evidence,
    )
    if (
        completed.returncode != 0
        or one_pass_summary is None
        or any(outcome in evidence.lower() for outcome in disallowed_outcomes)
    ):
        raise _ScriptError(
            "real TRAIN smoke did not report exactly one passing test; "
            f"pytest exit={completed.returncode}: {evidence.strip()}"
        )


def _authorize_existing(
    *,
    registry: DatasetRegistry,
    request: AccessRequest,
    policy: SplitPolicy,
    context: AccessContext,
    audit_writer: _AuditAppender,
) -> AccessGrant:
    """Delegate authorization to the accepted Slice 1C gate."""
    return authorize_access(
        registry=registry,
        request=request,
        policy=policy,
        context=context,
        audit_writer=audit_writer,
    )


_PRODUCTION_HOOKS = _Hooks(
    inspect_git=_inspect_git,
    fingerprint=_canonical_source_fingerprint,
    generated_at_utc=operational_event_time_utc,
    prepare_bundle=_generation._prepare_view_bundle,
    run_train_smoke=_run_real_train_smoke,
    authorize_curated=_authorize_existing,
    execute_bundle=_generation._execute_prepared_bundle,
)


def _validate_git_state(state: _GitState, *, require_canonical_main: bool) -> None:
    """Require committed clean code and, where configured, canonical main."""
    if not state.clean:
        raise _ScriptError("production repository worktree is dirty")
    if require_canonical_main:
        if state.local_main != state.origin_main:
            raise _ScriptError("local main differs from origin/main")
        if state.head != state.local_main:
            raise _ScriptError("HEAD is not the intended local-main code identity")
    if not state.script_committed_at_head:
        raise _ScriptError("configured generation script is not the committed version at HEAD")


def _path_state(path: Path) -> str:
    """Describe one path without opening its content."""
    if path.is_symlink():
        return "symlink"
    if not path.exists():
        return "absent"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"present but unreadable metadata ({exc})"
    if path.is_dir():
        return f"directory ({size} bytes metadata size)"
    if path.is_file():
        return f"regular file ({size} bytes)"
    return "present non-regular path"


def _ensure_empty_views_root(path: Path, *, stdout: TextIO) -> tuple[str, bool]:
    """Create only the one R-2-permitted empty canonical root when absent."""
    before = _path_state(path)
    if before == "absent":
        try:
            path.mkdir(mode=0o775)
        except OSError as exc:
            raise _ScriptError(f"cannot create canonical views root {path}: {exc}") from exc
        if path.resolve(strict=True) != path or not path.is_dir():
            raise _ScriptError("new views root is not a canonical real directory")
        if next(path.iterdir(), None) is not None:
            raise _ScriptError("new views root was not empty immediately after creation")
        print(
            "Preflight side effect: created only the empty canonical views root "
            f"{path}. No child view or research artifact was created.",
            file=stdout,
        )
        return before, True
    return before, False


def _reject_uncreated_root_overlap(
    *,
    path: Path,
    source_registry: DatasetRegistry,
    source_ref: DatasetRef,
) -> None:
    """Reuse the accepted overlap rule before R-2 may create a missing root."""
    if path.exists() or path.is_symlink():
        return
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise _ScriptError(f"cannot resolve proposed views-root parent: {exc}") from exc
    if parent != path.parent or not parent.is_dir():
        raise _ScriptError("proposed views-root parent must be a canonical real directory")
    source = source_registry.resolve(source_ref)
    partition_source = source_registry.resolve_partition_source_location(source)
    _generation._reject_source_root_overlap(
        views_root=parent / path.name,
        source_root=source.root.resolve(),
        partition_directory=partition_source.directory.resolve(),
    )


def _manifest_digest(path: Path) -> str:
    """Hash one manifest JSON file, never a Parquet payload."""
    if path.is_symlink() or not path.is_file():
        raise _ScriptError(f"required manifest is not a regular file: {path}")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise _ScriptError(f"cannot hash manifest {path}: {exc}") from exc


def _validate_audit_location(path: Path) -> None:
    """Reject an unsafe existing audit target before the real smoke."""
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.resolve(strict=True) != parent:
        raise _ScriptError("audit parent must be an existing canonical real directory")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _ScriptError("audit target must be absent or an existing regular file")


def _verified_manifest_digests(config: _StageBConfig) -> _ManifestDigests:
    """Pin the exact approved RAW and CURATED manifest identities."""
    raw_path = config.dataset_root / config.partition_source.dataset_id / "manifest.json"
    curated_path = config.dataset_root / config.source_ref.dataset_id / "manifest.json"
    digests = _ManifestDigests(
        raw=_manifest_digest(raw_path),
        curated=_manifest_digest(curated_path),
    )
    if digests.raw != config.partition_source.manifest_sha256:
        raise _ScriptError("configured RAW/partition-source manifest digest mismatches")
    if digests.curated != config.source_ref.manifest_sha256:
        raise _ScriptError("configured CURATED source manifest digest mismatches")
    return digests


def _bundle_spec(
    config: _StageBConfig,
    *,
    generated_at_utc: str,
    platform_git_commit: str,
) -> _generation._BundleSpec:
    """Build the explicit accepted generator request from approved inputs."""
    return _generation._BundleSpec(
        source_ref=config.source_ref,
        view_ids=config.view_ids,
        views_root=config.views_root,
        generated_at_utc=generated_at_utc,
        generator=_generation._GeneratorProvenance(
            name=config.generator_name,
            version=config.generator_version,
            platform_git_commit=platform_git_commit,
        ),
        policy_cap_encoded=config.policy_cap_encoded,
    )


def _policy_wire(policy: SplitPolicy) -> dict[str, str]:
    """Return the exact policy identity in manifest wire shape."""
    return {
        "adr": policy.identity.adr,
        "policy_version": policy.identity.policy_version,
        "policy_sha256": policy.identity.policy_sha256,
    }


def _identity_wire(identity: DatasetIdentity) -> dict[str, str]:
    """Return one canonical identity in manifest wire shape."""
    return {
        "dataset_id": identity.dataset_id,
        "manifest_sha256": identity.manifest_sha256,
    }


def _read_canonical_v11(
    path: Path,
) -> tuple[dict[str, object], ViewManifestV11, bytes]:
    """Read one strict, canonical v1.1 manifest without touching partitions."""
    try:
        payload = path.read_bytes()
        decoded: object = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _ScriptError(f"cannot read existing child manifest {path}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise _ScriptError(f"existing child manifest is not a JSON object: {path}")
    document = cast("dict[str, object]", decoded)
    try:
        strict = ViewManifestV11.model_validate(document)
        canonical = _generation._canonical_manifest_bytes(document)
    except Exception as exc:
        raise _ScriptError(f"existing child is not strict canonical v1.1: {path}: {exc}") from exc
    normalized_payload = _generation._canonical_manifest_bytes(
        strict.model_dump(mode="json", exclude_unset=True)
    )
    if normalized_payload != payload:
        raise _ScriptError(f"existing manifest uses coercive non-generator wire values: {path}")
    if canonical != payload:
        raise _ScriptError(f"existing child bytes are not canonical v1.1: {path}")
    return document, strict, payload


def _validate_physical_partition_metadata(
    *,
    target: NormalizedManifest,
    source: NormalizedManifest,
    label: str,
) -> None:
    """Match immutable physical identity and source containment metadata."""
    source_by_slot = {
        (partition.key, partition.filename): partition for partition in source.partitions
    }
    if len(source_by_slot) != len(source.partitions):
        raise _ScriptError("CURATED source has ambiguous physical partition slots")
    target_slots = [(partition.key, partition.filename) for partition in target.partitions]
    if len(set(target_slots)) != len(target_slots):
        raise _ScriptError(f"existing {label} repeats a physical partition slot")
    for partition in target.partitions:
        if partition.rows <= 0:
            raise _ScriptError(f"existing {label} has a zero-row partition contribution")
        source_partition = source_by_slot.get((partition.key, partition.filename))
        if source_partition is None:
            raise _ScriptError(f"existing {label} names an unknown physical partition")
        if (
            partition.sha256 != source_partition.sha256
            or partition.byte_size != source_partition.byte_size
        ):
            raise _ScriptError(f"existing {label} has conflicting physical partition identity")
        if (
            partition.first_key < source_partition.first_key
            or partition.last_key > source_partition.last_key
            or partition.rows > source_partition.rows
        ):
            raise _ScriptError(
                f"existing {label} admission metadata exceeds its CURATED source partition"
            )


def _validate_generator_partition_wire(
    *,
    document: dict[str, object],
    strict: ViewManifestV11,
    source: NormalizedManifest,
    label: str,
) -> None:
    """Require metadata conventions emitted by the accepted generator."""
    raw_partitions = document.get("admitted_partitions")
    if not isinstance(raw_partitions, list) or len(raw_partitions) != len(
        strict.admitted_partitions
    ):
        raise _ScriptError(f"existing {label} has malformed partition wire metadata")
    source_by_slot = {
        (partition.key, partition.filename): (index, partition)
        for index, partition in enumerate(source.partitions)
    }
    previous_source_index = -1
    for raw_partition, parsed in zip(raw_partitions, strict.admitted_partitions, strict=True):
        if not isinstance(raw_partition, dict):
            raise _ScriptError(f"existing {label} has malformed partition wire metadata")
        source_match = source_by_slot.get((parsed.key, parsed.filename))
        if source_match is None:
            raise _ScriptError(f"existing {label} names an unknown physical partition")
        source_index, source_partition = source_match
        if source_index <= previous_source_index:
            raise _ScriptError(f"existing {label} partitions are not in accepted source order")
        previous_source_index = source_index
        if raw_partition.get("sha256") != source_partition.sha256:
            raise _ScriptError(f"existing {label} has non-generator partition digest encoding")
        if source_partition.byte_size is None:
            if "bytes" in raw_partition:
                raise _ScriptError(
                    f"existing {label} has non-generator partition byte-size encoding"
                )
        elif raw_partition.get("bytes") != source_partition.byte_size:
            raise _ScriptError(f"existing {label} has non-generator partition byte-size encoding")


def _validate_existing_child(
    *,
    prepared: _generation._PreparedBundle,
    role: ViewRole,
    path: Path,
) -> None:
    """Validate immutable child identity/provenance for R-3 and OD-F6.

    This is deliberately metadata-only. It validates strict wire bytes,
    lineage, policy, family, provenance, and binding placement. It does not
    reconstruct admission facts; OD-F6 reserves that for the confirmed
    accepted keys-only execution path when a child is missing.
    """
    anchor = prepared.anchor
    if anchor is None:
        raise _ScriptError("existing child validation requires a SNAPSHOT anchor")
    document, strict, raw_bytes = _read_canonical_v11(path)
    normalized = read_manifest(path)
    try:
        current_bytes = path.read_bytes()
    except OSError as exc:
        raise _ScriptError(f"cannot re-read existing {role.value} child: {exc}") from exc
    if current_bytes != raw_bytes:
        raise _ScriptError(f"existing {role.value} child changed during metadata validation")
    expected_id = prepared.spec.view_ids.for_role(role)
    snapshot_identity = DatasetIdentity(
        dataset_id=prepared.spec.view_ids.snapshot,
        manifest_sha256=anchor.manifest_sha256,
    )
    expected_generator = anchor.generator.as_wire()
    expected_checks = (
        (strict.view_id == expected_id, "view id"),
        (strict.role == role.value, "role"),
        (strict.discovery_default is _EXPECTED_DISCOVERY[role], "discovery flag"),
        (document.get("parent") == _identity_wire(snapshot_identity), "SNAPSHOT parent"),
        (
            document.get("partition_source") == _identity_wire(prepared.partition_source),
            "partition-source identity",
        ),
        (document.get("split_policy_ref") == _policy_wire(prepared.policy), "policy identity"),
        (document.get("adapter_id") == prepared.source.manifest.adapter_id, "adapter id"),
        (document.get("symbol") == prepared.source.manifest.symbol, "symbol"),
        (document.get("timeframe") == prepared.source.manifest.timeframe, "timeframe"),
        (
            document.get("clock_domain") == prepared.source.manifest.clock_domain.identifier,
            "clock domain",
        ),
        (document.get("time_unit") == prepared.source.manifest.time_unit.value, "time unit"),
        (
            document.get("calendar_time_policy") == prepared.policy.calendar_time_policy.value,
            "calendar-time policy",
        ),
        (document.get("columns") == list(prepared.columns), "ordered column contract"),
        (document.get("generated_at_utc") == anchor.generated_at_utc, "bundle timestamp"),
        (document.get("generator") == expected_generator, "generator provenance"),
        ("notes" not in document, "unexpected notes field"),
    )
    for accepted, label in expected_checks:
        if not accepted:
            raise _ScriptError(f"existing {role.value} child has wrong {label}")
    binding = document.get("snapshot_binding")
    if role is ViewRole.LOCKBOX:
        if binding != dict(anchor.snapshot_binding):
            raise _ScriptError("existing lockbox child has wrong snapshot binding")
        if normalized.admitted_native_range.end != anchor.snapshot_binding.get("bound_key"):
            raise _ScriptError("existing lockbox admitted range does not end at the SNAPSHOT bound")
    elif "snapshot_binding" in document:
        raise _ScriptError(f"existing {role.value} child must not carry snapshot binding")
    if normalized.source_format is not ManifestFormat.VIEW_MANIFEST_V1_1:
        raise _ScriptError(f"existing {role.value} child did not normalize as v1.1")
    if normalized.identity.dataset_id != expected_id or normalized.role is not role:
        raise _ScriptError(f"existing {role.value} normalized identity is incompatible")
    _validate_physical_partition_metadata(
        target=normalized,
        source=prepared.source.manifest,
        label=f"{role.value} child",
    )
    _validate_generator_partition_wire(
        document=document,
        strict=strict,
        source=prepared.source.manifest,
        label=f"{role.value} child",
    )
    classification = classify_access(normalized, prepared.policy)
    if classification is not _EXPECTED_CLASSIFICATION[role]:
        raise _ScriptError(f"existing {role.value} child has unexpected access classification")


def _inspect_existing_bundle(
    prepared: _generation._PreparedBundle,
) -> tuple[_BundleStatus, tuple[ViewRole, ...]]:
    """Classify fresh/partial/complete state after accepted preparation."""
    if prepared.mode is _generation._GenerationMode.FRESH:
        return _BundleStatus.FRESH, ()
    anchor = prepared.anchor
    if anchor is None:
        raise _ScriptError("resume preparation has no validated SNAPSHOT anchor")
    try:
        current_anchor_bytes = anchor.path.read_bytes()
    except OSError as exc:
        raise _ScriptError(f"cannot re-read existing SNAPSHOT anchor: {exc}") from exc
    if current_anchor_bytes != anchor.raw_bytes:
        raise _ScriptError("existing SNAPSHOT anchor changed after bundle preparation")
    anchor_document, anchor_strict, validated_anchor_bytes = _read_canonical_v11(anchor.path)
    if validated_anchor_bytes != current_anchor_bytes:
        raise _ScriptError("existing SNAPSHOT anchor changed during metadata validation")
    if anchor_strict.discovery_default is not False:
        raise _ScriptError("existing SNAPSHOT anchor has wrong discovery flag")
    if "notes" in anchor_document:
        raise _ScriptError("existing SNAPSHOT anchor has unexpected notes field")
    _validate_generator_partition_wire(
        document=anchor_document,
        strict=anchor_strict,
        source=prepared.source.manifest,
        label="SNAPSHOT anchor",
    )
    anchor_manifest = read_manifest(anchor.path)
    try:
        stable_anchor_bytes = anchor.path.read_bytes()
    except OSError as exc:
        raise _ScriptError(f"cannot re-read existing SNAPSHOT anchor: {exc}") from exc
    if stable_anchor_bytes != current_anchor_bytes:
        raise _ScriptError("existing SNAPSHOT anchor changed during metadata validation")
    _validate_physical_partition_metadata(
        target=anchor_manifest,
        source=prepared.source.manifest,
        label="SNAPSHOT anchor",
    )
    existing: list[ViewRole] = []
    for role in _TIER_ONE_ROLES:
        path = _generation._manifest_path(
            prepared.spec.views_root,
            prepared.spec.view_ids.for_role(role),
        )
        if not path.exists():
            continue
        _validate_existing_child(prepared=prepared, role=role, path=path)
        existing.append(role)
    status = (
        _BundleStatus.COMPLETE
        if len(existing) == len(_TIER_ONE_ROLES)
        else _BundleStatus.PARTIAL_RESUME
    )
    return status, tuple(existing)


def _preflight(
    *,
    config: _StageBConfig,
    hooks: _Hooks,
    stdout: TextIO,
) -> _PreflightReport:
    """Perform every allowed metadata/Git/filesystem check before confirmation."""
    git = hooks.inspect_git(config.repo_root, config.script_path)
    _validate_git_state(git, require_canonical_main=config.require_canonical_main)
    manifest_digests = _verified_manifest_digests(config)
    fingerprint = hooks.fingerprint(config.dataset_root)
    if (
        config.expected_source_fingerprint is not None
        and fingerprint.sha256 != config.expected_source_fingerprint
    ):
        raise _ScriptError("immutable-source fingerprint differs from the approved value")
    policy = read_split_policy(
        config.policy_path,
        expected_identity=config.policy_identity,
    )
    source_registry = DatasetRegistry.discover([config.dataset_root])
    _reject_uncreated_root_overlap(
        path=config.views_root,
        source_registry=source_registry,
        source_ref=config.source_ref,
    )
    views_root_before, views_root_created = _ensure_empty_views_root(
        config.views_root,
        stdout=stdout,
    )
    registry = DatasetRegistry.discover([config.dataset_root, config.views_root])
    generated_at_utc = hooks.generated_at_utc()
    prepared = hooks.prepare_bundle(
        registry=registry,
        policy=policy,
        spec=_bundle_spec(
            config,
            generated_at_utc=generated_at_utc,
            platform_git_commit=git.head,
        ),
    )
    if prepared.partition_source != config.partition_source:
        raise _ScriptError("resolved partition-source identity is not the approved RAW identity")
    bundle_status, existing_children = _inspect_existing_bundle(prepared)
    _validate_audit_location(config.audit_path)
    return _PreflightReport(
        git=git,
        fingerprint=fingerprint,
        manifest_digests=manifest_digests,
        policy=policy,
        registry=registry,
        prepared=prepared,
        bundle_status=bundle_status,
        existing_children=existing_children,
        generated_at_utc=generated_at_utc,
        views_root_before=views_root_before,
        views_root_created=views_root_created,
        audit_state=_path_state(config.audit_path),
    )


def _expected_audit_outcome(status: _BundleStatus) -> str:
    """Describe the accepted logical audit delta for the prepared state."""
    if status is _BundleStatus.FRESH:
        return "1 dataset_access_decision, then 1 snapshot_binding"
    if status is _BundleStatus.PARTIAL_RESUME:
        return "1 dataset_access_decision, 0 snapshot_binding"
    return "0 events (terminal no-op)"


def _render_preflight(
    report: _PreflightReport,
    *,
    config: _StageBConfig,
    stdout: TextIO,
) -> None:
    """Display the exact proposed bundle and pre-confirmation evidence."""
    policy = report.policy
    prepared = report.prepared
    print("Slice 1F Stage-B preflight — metadata only; no Parquet opened", file=stdout)
    print(f"Git HEAD: {report.git.head}", file=stdout)
    print(f"Local main: {report.git.local_main}", file=stdout)
    print(f"Origin main: {report.git.origin_main}", file=stdout)
    print(f"Worktree clean: {str(report.git.clean).lower()}", file=stdout)
    print(
        f"Production script committed at HEAD: {str(report.git.script_committed_at_head).lower()}",
        file=stdout,
    )
    print(f"Generator code commit: {prepared.spec.generator.platform_git_commit}", file=stdout)
    print(
        f"Generator: {prepared.spec.generator.name} / {prepared.spec.generator.version}",
        file=stdout,
    )
    print(
        "CURATED identity: "
        f"{config.source_ref.dataset_id} / {config.source_ref.manifest_sha256} / "
        f"{config.source_ref.role.value}",
        file=stdout,
    )
    print(
        "Partition-source identity: "
        f"{prepared.partition_source.dataset_id} / "
        f"{prepared.partition_source.manifest_sha256}",
        file=stdout,
    )
    print(
        "SplitPolicy identity: "
        f"{policy.identity.adr} / {policy.identity.policy_version} / "
        f"{policy.identity.policy_sha256}",
        file=stdout,
    )
    print(
        "SplitPolicy axis: "
        f"{policy.clock_domain.identifier} / {policy.time_unit.value} / "
        f"{policy.calendar_time_policy.value}",
        file=stdout,
    )
    for role in _TIER_ONE_ROLES:
        span = policy.range_for(role)
        print(f"Policy {role.value}: [{span.start}, {span.end}] inclusive", file=stdout)
    print(f"Policy cap encoded: {config.policy_cap_encoded}", file=stdout)
    print("Approved view ids and final paths:", file=stdout)
    for role, view_id in config.view_ids.items():
        print(
            f"  {role.value}: {view_id} -> "
            f"{_generation._manifest_path(config.views_root, view_id)}",
            file=stdout,
        )
    print(f"Views root before preflight: {report.views_root_before}", file=stdout)
    print(f"Views root now: {_path_state(config.views_root)}", file=stdout)
    if report.views_root_created:
        print(
            "Permitted preflight side effect: empty canonical views root only.",
            file=stdout,
        )
    else:
        print(
            "Preflight side effect: none. Empty-root creation is the only permitted "
            "preflight side effect.",
            file=stdout,
        )
    print(f"Audit log state: {report.audit_state}", file=stdout)
    print("Registry collision state: clear", file=stdout)
    print(f"_prepare_view_bundle mode: {report.prepared.mode.value}", file=stdout)
    print(f"Bundle state: {report.bundle_status.value}", file=stdout)
    existing = ", ".join(role.value for role in report.existing_children) or "none"
    print(f"Validated existing children: {existing}", file=stdout)
    print(
        f"Immutable-source fingerprint command: {_fingerprint_command(config.dataset_root)}",
        file=stdout,
    )
    print(
        "Immutable-source fingerprint: "
        f"{report.fingerprint.sha256} ({report.fingerprint.entry_count} entries)",
        file=stdout,
    )
    print(
        f"RAW manifest SHA-256: {report.manifest_digests.raw}",
        file=stdout,
    )
    print(
        f"CURATED manifest SHA-256: {report.manifest_digests.curated}",
        file=stdout,
    )
    print(
        "generated_at_utc procedure: capture operational_event_time_utc() once per "
        "invocation for a fresh bundle; resume reuses the validated SNAPSHOT timestamp.",
        file=stdout,
    )
    print(f"Fresh-bundle proposed generated_at_utc: {report.generated_at_utc}", file=stdout)
    print(
        f"Expected audit outcome: {_expected_audit_outcome(report.bundle_status)}",
        file=stdout,
    )


def _require_confirmation(value: str | None) -> None:
    """Keep every production-capable hook behind one exact literal."""
    if value != _CONFIRMATION_LITERAL:
        raise _UsageError(f'execute requires exactly --confirm "{_CONFIRMATION_LITERAL}"')


def _production_context(git: _GitState) -> AccessContext:
    """Capture the future production caller only after smoke success."""
    return AccessContext(
        session_id=uuid4(),
        os_user=getpass.getuser(),
        hostname=socket.gethostname(),
        pid=os.getpid(),
        platform_git_commit=git.head,
        platform_git_dirty=False,
    )


def _render_result(
    result: _generation._GenerationResult,
    *,
    before: _PreflightReport,
    after_fingerprint: _SourceFingerprint,
    after_digests: _ManifestDigests,
    stdout: TextIO,
) -> None:
    """Render the bounded CP-4 evidence produced by accepted components."""
    print("Stage-B accepted bundle executor completed.", file=stdout)
    print(f"Execution mode: {result.mode.value}", file=stdout)
    print(f"Bound native key: {result.bound_key}", file=stdout)
    for role in (ViewRole.SNAPSHOT, *_TIER_ONE_ROLES):
        artifact = result.artifacts[role]
        disposition = "created" if artifact.created else "reused/no-op"
        print(
            f"  {role.value}: {artifact.view_id} / {artifact.manifest_sha256} / "
            f"{artifact.path} / {disposition}",
            file=stdout,
        )
    print(
        f"Audit outcome: {_expected_audit_outcome(before.bundle_status)}",
        file=stdout,
    )
    print(
        "Immutable-source fingerprint before/after: "
        f"{before.fingerprint.sha256} / {after_fingerprint.sha256}",
        file=stdout,
    )
    print(
        f"RAW manifest SHA-256 after: {after_digests.raw}",
        file=stdout,
    )
    print(
        f"CURATED manifest SHA-256 after: {after_digests.curated}",
        file=stdout,
    )
    print(
        "CP-4 evidence candidate rendered; checkpoint status is not changed by this script.",
        file=stdout,
    )


def _run(
    arguments: _Arguments,
    *,
    config: _StageBConfig,
    hooks: _Hooks,
    stdout: TextIO,
) -> int:
    """Run preflight, then cross the exact confirmation boundary if requested."""
    report = _preflight(config=config, hooks=hooks, stdout=stdout)
    _render_preflight(report, config=config, stdout=stdout)

    if report.bundle_status is _BundleStatus.COMPLETE:
        print("Bundle already complete — nothing to execute.", file=stdout)
        return 0
    if arguments.mode is _CommandMode.PREFLIGHT:
        print(
            "Preflight complete. No smoke, authorization, grant, loader, audit append, "
            "or view generation occurred.",
            file=stdout,
        )
        return 0

    _require_confirmation(arguments.confirmation)

    # R-1/D-5: this is the first real-data contact and precedes authorization.
    hooks.run_train_smoke(config.repo_root)

    post_smoke_git = hooks.inspect_git(config.repo_root, config.script_path)
    _validate_git_state(
        post_smoke_git,
        require_canonical_main=config.require_canonical_main,
    )
    if post_smoke_git != report.git:
        raise _ScriptError("production repository identity changed during the real TRAIN smoke")

    context = _production_context(post_smoke_git)
    audit_writer = JsonlAuditWriter(config.audit_path)
    request = AccessRequest(
        dataset_ref=config.source_ref,
        reason=AccessReason(
            code=_ACCESS_REASON_CODE,
            detail=_ACCESS_REASON_DETAIL,
        ),
        gated_unlock=GatedUnlock(target=config.source_ref.canonical_identity),
    )
    grant = hooks.authorize_curated(
        registry=report.registry,
        request=request,
        policy=report.policy,
        context=context,
        audit_writer=audit_writer,
    )
    loader = MarketFrameLoader(registry=report.registry, split_policy=report.policy)
    result = hooks.execute_bundle(
        prepared=report.prepared,
        loader=loader,
        grant=grant,
        context=context,
        audit_writer=audit_writer,
    )

    after_fingerprint = hooks.fingerprint(config.dataset_root)
    if after_fingerprint != report.fingerprint:
        raise _ScriptError("immutable-source fingerprint changed during Stage B")
    after_digests = _verified_manifest_digests(config)
    if after_digests != report.manifest_digests:
        raise _ScriptError("RAW/CURATED manifest identity changed during Stage B")
    _render_result(
        result,
        before=report,
        after_fingerprint=after_fingerprint,
        after_digests=after_digests,
        stdout=stdout,
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    config: _StageBConfig | None = None,
    hooks: _Hooks | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """CLI entry point with injectable synthetic-only test seams."""
    selected_stdout = sys.stdout if stdout is None else stdout
    selected_stderr = sys.stderr if stderr is None else stderr
    try:
        arguments = _parse_arguments(sys.argv[1:] if argv is None else argv)
        if arguments is None:
            print(_USAGE, file=selected_stdout)
            return 0
        return _run(
            arguments,
            config=_PRODUCTION_CONFIG if config is None else config,
            hooks=_PRODUCTION_HOOKS if hooks is None else hooks,
            stdout=selected_stdout,
        )
    except _UsageError as exc:
        print(f"REFUSED: {exc}", file=selected_stderr)
        print(_USAGE, file=selected_stderr)
        return 2
    except (
        _ScriptError,
        _generation._ViewGenerationError,
        TradingResearchError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"REFUSED: {exc}", file=selected_stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
