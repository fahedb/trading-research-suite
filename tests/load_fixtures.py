"""Synthetic Parquet catalogs and grants for data-layer tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from tests.manifest_fixtures import (
    curated_manifest,
    raw_manifest,
    v11_manifest,
    write_manifest,
)
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.data.gating import (
    AccessContext,
    AccessGrant,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    SplitPolicy,
    authorize_access,
    read_split_policy,
)
from trading_research.data.integrity import MarketFrameLoader
from trading_research.data.manifests import read_manifest
from trading_research.data.registry import DatasetRegistry, RegisteredManifest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

_SPREAD_INT32 = np.dtype("int32")
_MT5_LEGACY_COLUMNS = (
    "mt5_time_raw",
    "timestamp_mt5_encoded",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)
_TIER_ONE_ROLES = (
    ViewRole.TRAIN,
    ViewRole.VALIDATION,
    ViewRole.OOS,
    ViewRole.LOCKBOX,
)
_DEFAULT_POLICY_RANGES = MappingProxyType(
    {
        ViewRole.TRAIN: (0, 999),
        ViewRole.VALIDATION: (1_000, 1_999),
        ViewRole.OOS: (2_000, 2_999),
        ViewRole.LOCKBOX: (3_000, 3_999),
    }
)


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    """Physical keys and target-admitted metadata for one synthetic file."""

    key: str
    filename: str
    physical_keys: tuple[int, ...]
    first_key: int
    last_key: int
    rows: int
    declared_sha256: str | None = None
    declared_size_delta: int | None = 0


class RecordingAuditWriter:
    """In-memory gate writer; NORMAL access leaves it empty."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


@dataclass(frozen=True, slots=True)
class LoadCase:
    """One exact target, source location, policy, and preissued grant."""

    registry: DatasetRegistry
    policy: SplitPolicy
    grant: AccessGrant
    target: RegisteredManifest
    source_directory: Path
    partition_paths: tuple[Path, ...]
    audit: RecordingAuditWriter


@dataclass(frozen=True, slots=True)
class MultiRoleLoadCase:
    """One shared policy/catalog with four policy-adjacent Tier-1 views."""

    registry: DatasetRegistry
    policy: SplitPolicy
    grants: Mapping[ViewRole, AccessGrant]
    targets: Mapping[ViewRole, RegisteredManifest]
    source_directory: Path
    partition_paths: Mapping[ViewRole, Path]
    audit: RecordingAuditWriter


@dataclass(frozen=True, slots=True)
class GenerationCase:
    """Synthetic legacy CURATED family ready for Slice 1F generation tests.

    Building a case performs no authorization and writes no audit event. Tests
    choose when to call :meth:`authorize_curated`, which goes through the
    ordinary gate and binds the returned grant to this case's exact source,
    policy, and access context.
    """

    source_root: Path
    views_root: Path
    partition_directory: Path
    partition_paths: tuple[Path, ...]
    partition_specs: tuple[PartitionSpec, ...]
    registry: DatasetRegistry
    policy: SplitPolicy
    loader: MarketFrameLoader
    context: AccessContext
    audit: RecordingAuditWriter
    partition_source: RegisteredManifest
    curated_source: RegisteredManifest
    curated_ref: DatasetRef
    generated_view_ids: Mapping[ViewRole, str]

    def authorize_curated(self) -> AccessGrant:
        """Authorize this exact CURATED source through the normal gate."""
        return authorize_generation_curated(self)

    def rediscover_with_views(self) -> DatasetRegistry:
        """Discover source and generated manifests after bundle publication."""
        return DatasetRegistry.discover([self.source_root, self.views_root])


def _policy_document(
    *,
    ranges: Mapping[ViewRole, tuple[int, int]] | None = None,
    clock_domain: str = "mt5.synth-server.wallclock",
    adr: str = "ADR-0012",
    policy_version: str = "v1",
) -> dict[str, object]:
    chosen_ranges = _DEFAULT_POLICY_RANGES if ranges is None else ranges
    if set(chosen_ranges) != set(_TIER_ONE_ROLES):
        raise ValueError("synthetic policy ranges must define all four Tier-1 roles")
    return {
        "schema_version": "1",
        "adr": adr,
        "policy_version": policy_version,
        "clock_domain": clock_domain,
        "time_unit": "seconds",
        "calendar_time_policy": "native-wallclock-v1",
        "ranges": {
            role.value: {
                "start_inclusive_key": chosen_ranges[role][0],
                "end_inclusive_key": chosen_ranges[role][1],
            }
            for role in _TIER_ONE_ROLES
        },
        "provenance": {
            "normative_source": "tests/load_fixtures.py",
            "ratified_on": "2026-08-08",
        },
    }


def write_policy(
    path: Path,
    *,
    alternate_bytes: bool = False,
    ranges: Mapping[ViewRole, tuple[int, int]] | None = None,
    clock_domain: str = "mt5.synth-server.wallclock",
    adr: str = "ADR-0012",
    policy_version: str = "v1",
) -> SplitPolicy:
    """Write and resolve one synthetic policy with selectable JSON bytes."""
    indentation = 4 if alternate_bytes else 2
    path.write_text(
        json.dumps(
            _policy_document(
                ranges=ranges,
                clock_domain=clock_domain,
                adr=adr,
                policy_version=policy_version,
            ),
            indent=indentation,
        )
    )
    return read_split_policy(path)


def _default_specs(role: ViewRole) -> tuple[PartitionSpec, ...]:
    if role is ViewRole.OOS:
        base = 2_100
    elif role is ViewRole.LOCKBOX:
        base = 3_100
    elif role is ViewRole.VALIDATION:
        base = 1_100
    else:
        base = 100
    return (
        PartitionSpec("p0", "target-0.parquet", (base, base + 1, base + 2), base, base + 2, 3),
        PartitionSpec(
            "p1",
            "target-1.parquet",
            (base + 100, base + 101, base + 102),
            base + 100,
            base + 102,
            3,
        ),
    )


def _physical_table(
    keys: Sequence[int],
    *,
    spread_dtype: np.dtype[Any] = _SPREAD_INT32,
    null_close_index: int | None = None,
    null_tick_volume_index: int | None = None,
    null_spread_index: int | None = None,
    close_offset: float = 0.0,
) -> pa.Table:
    size = len(keys)
    key_values = np.asarray(keys, dtype=np.int64)
    timestamp = key_values.astype("datetime64[s]").astype("datetime64[ns]")
    names = [
        "mt5_time_raw",
        "timestamp_mt5_encoded",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    close_values: list[float | None] = [
        float(value) + 1.5 + close_offset for value in range(size)
    ]
    if null_close_index is not None:
        close_values[null_close_index] = None
    tick_volume_values: list[int | None] = list(range(10, 10 + size))
    if null_tick_volume_index is not None:
        tick_volume_values[null_tick_volume_index] = None
    spread_values: list[int | None] = list(range(size))
    if null_spread_index is not None:
        spread_values[null_spread_index] = None
    arrays: list[pa.Array] = [
        pa.array(key_values, type=pa.int64()),
        pa.array(timestamp, type=pa.timestamp("ns")),
        pa.array(np.arange(size, dtype=np.float64) + 1.0),
        pa.array(np.arange(size, dtype=np.float64) + 2.0),
        pa.array(np.arange(size, dtype=np.float64) + 0.5),
        pa.array(close_values, type=pa.float64()),
        pa.array(tick_volume_values, type=pa.int64()),
        pa.array(spread_values, type=pa.from_numpy_dtype(spread_dtype)),
        pa.array(np.arange(size, dtype=np.int64)),
    ]
    return pa.Table.from_arrays(arrays, names=names)


def write_physical_partition(
    path: Path,
    keys: Sequence[int],
    *,
    spread_dtype: np.dtype[Any] = _SPREAD_INT32,
    null_close_index: int | None = None,
    null_tick_volume_index: int | None = None,
    null_spread_index: int | None = None,
    close_offset: float = 0.0,
) -> None:
    """Write one exporter-shaped synthetic Parquet partition."""
    pq.write_table(
        _physical_table(
            keys,
            spread_dtype=spread_dtype,
            null_close_index=null_close_index,
            null_tick_volume_index=null_tick_volume_index,
            null_spread_index=null_spread_index,
            close_offset=close_offset,
        ),
        path,
    )


def _target_columns() -> list[dict[str, str]]:
    return [
        {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
        {"name": "timestamp_mt5_encoded", "dtype": "datetime64[ns]", "purpose": "display_only"},
        {"name": "open", "dtype": "float64", "purpose": "value"},
        {"name": "high", "dtype": "float64", "purpose": "value"},
        {"name": "low", "dtype": "float64", "purpose": "value"},
        {"name": "close", "dtype": "float64", "purpose": "value"},
        {"name": "tick_volume", "dtype": "int64", "purpose": "value"},
        {"name": "spread", "dtype": "int32", "purpose": "value"},
        {"name": "real_volume", "dtype": "int64", "purpose": "value"},
    ]


def _default_generation_specs() -> tuple[PartitionSpec, ...]:
    """Return a gapped, boundary-straddling, post-cap physical layout."""
    first_keys = (100, 200, 999, 1_000, 1_100, 1_999)
    second_keys = (2_000, 2_100, 2_999, 3_000, 3_100, 3_999, 4_000, 4_001)
    return (
        PartitionSpec(
            key="wide-first",
            filename="alpha-history.parquet",
            physical_keys=first_keys,
            first_key=first_keys[0],
            last_key=first_keys[-1],
            rows=len(first_keys),
        ),
        PartitionSpec(
            key="wide-second",
            filename="omega-history.parquet",
            physical_keys=second_keys,
            first_key=second_keys[0],
            last_key=second_keys[-1],
            rows=len(second_keys),
        ),
    )


def _generation_view_ids(curated_dataset_id: str) -> Mapping[ViewRole, str]:
    return MappingProxyType(
        {
            ViewRole.SNAPSHOT: f"{curated_dataset_id}_SNAPSHOT",
            ViewRole.TRAIN: f"{curated_dataset_id}_TRAIN",
            ViewRole.VALIDATION: f"{curated_dataset_id}_VALIDATION",
            ViewRole.OOS: f"{curated_dataset_id}_OOS",
            ViewRole.LOCKBOX: f"{curated_dataset_id}_LOCKBOX",
        }
    )


def make_generation_case(
    tmp_path: Path,
    *,
    specs: Sequence[PartitionSpec] | None = None,
    policy_ranges: Mapping[ViewRole, tuple[int, int]] | None = None,
    symbol: str = "SYNTH",
    timeframe: str = "M1",
    raw_dataset_id: str = "SYNTH_GENERATION_RAW_V1",
    curated_dataset_id: str = "SYNTH_GENERATION_CURATED_V1",
    trade_server: str = "Synth-Server",
    clock_domain: str | None = None,
    source_root: Path | None = None,
    views_root: Path | None = None,
    policy_adr: str = "ADR-0012",
    policy_version: str = "synthetic-v1",
    platform_git_commit: str = "a" * 40,
    generated_view_ids: Mapping[ViewRole, str] | None = None,
) -> GenerationCase:
    """Build one generalized, exporter-shaped legacy CURATED source family.

    ``PartitionSpec`` controls both arbitrary physical keys/filenames and the
    admitted metadata presented by the legacy source. The default layout has
    gaps, crosses non-snapshot Tier-1 boundaries, and contains physical rows
    beyond the policy cap. Supplying specs and ranges replaces those defaults
    without selecting any asset-specific branch in generation code.
    """
    supplied_specs = tuple(specs) if specs is not None else _default_generation_specs()
    chosen_specs = tuple(sorted(supplied_specs, key=lambda spec: spec.first_key))
    if not chosen_specs:
        raise ValueError("generation fixture requires at least one physical partition")

    resolved_source_root = (source_root or tmp_path / "generation-source").resolve()
    resolved_views_root = (views_root or tmp_path / "generation-views").resolve()
    if (
        resolved_source_root == resolved_views_root
        or resolved_source_root in resolved_views_root.parents
        or resolved_views_root in resolved_source_root.parents
    ):
        raise ValueError("synthetic source and views roots must be separate")
    resolved_source_root.mkdir(parents=True, exist_ok=True)
    resolved_views_root.mkdir(parents=True, exist_ok=True)

    partition_directory = resolved_source_root / "partition-source"
    curated_directory = resolved_source_root / "curated-source"
    partition_directory.mkdir()
    curated_directory.mkdir()

    raw_partitions: list[dict[str, object]] = []
    curated_partitions: list[dict[str, object]] = []
    partition_paths: list[Path] = []
    for spec in chosen_specs:
        partition_path = partition_directory / spec.filename
        write_physical_partition(partition_path, spec.physical_keys)
        raw_bytes = partition_path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        declared_digest = spec.declared_sha256 or digest
        declared_bytes = (
            None
            if spec.declared_size_delta is None
            else len(raw_bytes) + spec.declared_size_delta
        )
        raw_partition: dict[str, object] = {
            "year": spec.key,
            "filename": spec.filename,
            "rows": spec.rows,
            "first_raw": spec.first_key,
            "last_raw": spec.last_key,
            "sha256": declared_digest,
        }
        curated_partition: dict[str, object] = {
            "key": spec.key,
            "filename": spec.filename,
            "rows": spec.rows,
            "first_raw": spec.first_key,
            "last_raw": spec.last_key,
            "sha256": declared_digest,
        }
        if declared_bytes is not None:
            raw_partition["bytes"] = declared_bytes
            curated_partition["bytes"] = declared_bytes
        raw_partitions.append(raw_partition)
        curated_partitions.append(curated_partition)
        partition_paths.append(partition_path)

    first_key = chosen_specs[0].first_key
    last_key = chosen_specs[-1].last_key
    total_rows = sum(spec.rows for spec in chosen_specs)
    raw_document = raw_manifest(
        dataset_id=raw_dataset_id,
        first_key=first_key,
        last_key=last_key,
    )
    raw_document.update(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "trade_server": trade_server,
            "partitions": raw_partitions,
            "total_row_count": total_rows,
        }
    )
    raw_path = write_manifest(partition_directory, raw_document)

    curated_document = curated_manifest(
        dataset_id=curated_dataset_id,
        parent_id=raw_dataset_id,
        parent_relative_path="../partition-source",
        first_key=first_key,
        last_key=last_key,
    )
    curated_document.update(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "trade_server": trade_server,
            "columns": list(_MT5_LEGACY_COLUMNS),
            "included_partitions": curated_partitions,
            "total_usable_rows": total_rows,
        }
    )
    curated_path = write_manifest(curated_directory, curated_document)

    source_clock_domain = f"mt5.{trade_server.lower()}.wallclock"
    policy = write_policy(
        resolved_source_root / "split-policy.json",
        ranges=policy_ranges,
        clock_domain=source_clock_domain if clock_domain is None else clock_domain,
        adr=policy_adr,
        policy_version=policy_version,
    )
    registry = DatasetRegistry.discover([resolved_source_root])
    raw_manifest_model = read_manifest(raw_path)
    curated_manifest_model = read_manifest(curated_path)
    raw_ref = DatasetRef(
        dataset_id=raw_manifest_model.dataset_id,
        manifest_sha256=raw_manifest_model.manifest_sha256,
        role=raw_manifest_model.role,
    )
    curated_ref = DatasetRef(
        dataset_id=curated_manifest_model.dataset_id,
        manifest_sha256=curated_manifest_model.manifest_sha256,
        role=curated_manifest_model.role,
    )
    chosen_view_ids = (
        _generation_view_ids(curated_dataset_id)
        if generated_view_ids is None
        else MappingProxyType(dict(generated_view_ids))
    )
    expected_roles = {ViewRole.SNAPSHOT, *_TIER_ONE_ROLES}
    if set(chosen_view_ids) != expected_roles:
        raise ValueError("generated_view_ids must define SNAPSHOT and all four Tier-1 roles")

    context = AccessContext(
        session_id=uuid4(),
        os_user="tester",
        hostname="synthetic-generation-vm",
        pid=1234,
        platform_git_commit=platform_git_commit,
        platform_git_dirty=False,
    )
    audit = RecordingAuditWriter()
    return GenerationCase(
        source_root=resolved_source_root,
        views_root=resolved_views_root,
        partition_directory=partition_directory.resolve(),
        partition_paths=tuple(partition_paths),
        partition_specs=chosen_specs,
        registry=registry,
        policy=policy,
        loader=MarketFrameLoader(registry=registry, split_policy=policy),
        context=context,
        audit=audit,
        partition_source=registry.resolve(raw_ref),
        curated_source=registry.resolve(curated_ref),
        curated_ref=curated_ref,
        generated_view_ids=chosen_view_ids,
    )


def authorize_generation_curated(case: GenerationCase) -> AccessGrant:
    """Issue the exact gated CURATED grant used by fresh generation."""
    if not isinstance(case, GenerationCase):
        raise TypeError("case must be a GenerationCase")
    return authorize_access(
        registry=case.registry,
        request=AccessRequest(
            dataset_ref=case.curated_ref,
            reason=AccessReason(
                code="synthetic-generation",
                detail="Approved synthetic Slice 1F generation fixture",
            ),
            gated_unlock=GatedUnlock(target=case.curated_ref.canonical_identity),
        ),
        policy=case.policy,
        context=case.context,
        audit_writer=case.audit,
    )


def make_load_case(
    tmp_path: Path,
    *,
    role: ViewRole = ViewRole.TRAIN,
    specs: Sequence[PartitionSpec] | None = None,
    view_id: str | None = None,
    include_target_sizes: bool = True,
    spread_dtype: np.dtype[Any] = _SPREAD_INT32,
    null_close_index: int | None = None,
    null_tick_volume_index: int | None = None,
    null_spread_index: int | None = None,
    target_columns: list[dict[str, str]] | None = None,
    target_adapter_id: str = "mt5",
    alternate_policy_bytes: bool = False,
) -> LoadCase:
    """Build a target-authoritative catalog and issue its exact grant."""
    chosen = tuple(specs) if specs is not None else _default_specs(role)
    case_name = view_id or f"SYNTH_{role.value.upper()}_LOAD_V1"
    root = tmp_path / case_name.lower()
    source_directory = root / "source"
    source_directory.mkdir(parents=True)
    partition_paths: list[Path] = []
    target_partitions: list[dict[str, object]] = []
    for spec in chosen:
        path = source_directory / spec.filename
        write_physical_partition(
            path,
            spec.physical_keys,
            spread_dtype=spread_dtype,
            null_close_index=null_close_index,
            null_tick_volume_index=null_tick_volume_index,
            null_spread_index=null_spread_index,
        )
        raw_bytes = path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        declared_sha = spec.declared_sha256 or digest
        partition: dict[str, object] = {
            "key": spec.key,
            "filename": spec.filename,
            "sha256": declared_sha,
            "rows": spec.rows,
            "first_key": spec.first_key,
            "last_key": spec.last_key,
        }
        if include_target_sizes and spec.declared_size_delta is not None:
            partition["bytes"] = len(raw_bytes) + spec.declared_size_delta
        target_partitions.append(partition)
        partition_paths.append(path)

    # The source manifest deliberately lists different partition metadata.
    # Its identity resolves location; the target remains the sole authority.
    source_document = raw_manifest(first_key=0, last_key=3_999)
    source_document["total_row_count"] = 4_000
    source_document["partitions"][0]["rows"] = 4_000
    write_manifest(source_directory, source_document)
    source_manifest = read_manifest(source_directory / "manifest.json")

    policy = write_policy(
        root / "policy.json",
        alternate_bytes=alternate_policy_bytes,
    )
    target_document = v11_manifest(
        view_id=case_name,
        role=role.value,
        discovery_default=role in {ViewRole.TRAIN, ViewRole.VALIDATION},
        parent_sha256=source_manifest.manifest_sha256,
        source_sha256=source_manifest.manifest_sha256,
        policy_sha256=policy.identity.policy_sha256,
        first_key=chosen[0].first_key,
        last_key=chosen[-1].last_key,
        rows=sum(spec.rows for spec in chosen),
    )
    target_document["adapter_id"] = target_adapter_id
    target_document["columns"] = _target_columns() if target_columns is None else target_columns
    target_document["admitted_partitions"] = target_partitions
    target_document["admitted_native_range"] = {
        "start": chosen[0].first_key,
        "end": chosen[-1].last_key,
    }
    target_document["row_count_expected"] = sum(spec.rows for spec in chosen)
    target_path = write_manifest(root / "target", target_document)

    registry = DatasetRegistry.discover([root])
    target_manifest = read_manifest(target_path)
    ref = DatasetRef(
        dataset_id=target_manifest.dataset_id,
        manifest_sha256=target_manifest.manifest_sha256,
        role=target_manifest.role,
    )
    audit = RecordingAuditWriter()
    grant = _authorize_ref(
        registry=registry,
        ref=ref,
        policy=policy,
        audit=audit,
    )
    target = registry.resolve(ref)
    return LoadCase(
        registry=registry,
        policy=policy,
        grant=grant,
        target=target,
        source_directory=source_directory.resolve(),
        partition_paths=tuple(partition_paths),
        audit=audit,
    )


def make_multi_role_load_case(tmp_path: Path) -> MultiRoleLoadCase:
    """Build TRAIN through LOCKBOX over one exact synthetic split policy.

    Policy roles are consecutive while observed rows are deliberately far
    apart.  VALIDATION contains an intra-view key gap, and TRAIN admits a
    narrower interval than its physical Parquet file.
    """
    root = tmp_path / "multi-role"
    source_directory = root / "source"
    source_directory.mkdir(parents=True)
    policy = write_policy(root / "policy.json")

    observed: dict[ViewRole, tuple[int, ...]] = {
        ViewRole.TRAIN: (100, 101, 102, 150, 200, 201, 202),
        ViewRole.VALIDATION: (1_100, 1_101, 1_102, 1_150, 1_200, 1_201, 1_202),
        ViewRole.OOS: (2_100, 2_101, 2_102, 2_150, 2_200, 2_201, 2_202),
        ViewRole.LOCKBOX: (3_100, 3_101, 3_102, 3_150, 3_200, 3_201, 3_202),
    }
    physical: dict[ViewRole, tuple[int, ...]] = {
        **observed,
        ViewRole.TRAIN: (99, *observed[ViewRole.TRAIN], 203),
    }
    partition_paths: dict[ViewRole, Path] = {}
    partition_documents: dict[ViewRole, dict[str, object]] = {}
    for role, keys in physical.items():
        path = source_directory / f"{role.value}.parquet"
        write_physical_partition(path, keys)
        admitted = observed[role]
        partition_paths[role] = path
        partition_documents[role] = {
            "key": role.value,
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "rows": len(admitted),
            "first_key": admitted[0],
            "last_key": admitted[-1],
        }

    source_document = raw_manifest(first_key=0, last_key=3_999)
    source_document["total_row_count"] = 4_000
    source_document["partitions"][0]["rows"] = 4_000
    write_manifest(source_directory, source_document)
    source_manifest = read_manifest(source_directory / "manifest.json")

    for role, admitted in observed.items():
        target_document = v11_manifest(
            view_id=f"SYNTH_{role.value.upper()}_V1",
            role=role.value,
            discovery_default=role in {ViewRole.TRAIN, ViewRole.VALIDATION},
            parent_sha256=source_manifest.manifest_sha256,
            source_sha256=source_manifest.manifest_sha256,
            policy_sha256=policy.identity.policy_sha256,
            first_key=admitted[0],
            last_key=admitted[-1],
            rows=len(admitted),
        )
        target_document["adapter_id"] = "mt5"
        target_document["columns"] = _target_columns()
        target_document["admitted_partitions"] = [partition_documents[role]]
        target_document["admitted_native_range"] = {
            "start": admitted[0],
            "end": admitted[-1],
        }
        target_document["row_count_expected"] = len(admitted)
        write_manifest(root / role.value, target_document)

    registry = DatasetRegistry.discover([root])
    targets = {
        entry.manifest.role: entry
        for entry in registry.all_manifests_for_diagnostics()
        if entry.manifest.role in observed
    }
    audit = RecordingAuditWriter()
    grants: dict[ViewRole, AccessGrant] = {}
    for role in observed:
        manifest = targets[role].manifest
        ref = DatasetRef(
            dataset_id=manifest.dataset_id,
            manifest_sha256=manifest.manifest_sha256,
            role=role,
        )
        grants[role] = _authorize_ref(
            registry=registry,
            ref=ref,
            policy=policy,
            audit=audit,
        )

    return MultiRoleLoadCase(
        registry=registry,
        policy=policy,
        grants=MappingProxyType(grants),
        targets=MappingProxyType(targets),
        source_directory=source_directory.resolve(),
        partition_paths=MappingProxyType(partition_paths),
        audit=audit,
    )


def _authorize_ref(
    *,
    registry: DatasetRegistry,
    ref: DatasetRef,
    policy: SplitPolicy,
    audit: RecordingAuditWriter,
) -> AccessGrant:
    """Issue the exact synthetic grant required by ``ref``'s role."""
    role = ref.role
    reason = None
    unlock = None
    if role in {
        ViewRole.OOS,
        ViewRole.CURATED,
        ViewRole.SNAPSHOT,
        ViewRole.LOCKBOX,
        ViewRole.RAW,
    }:
        reason = AccessReason(
            code="synthetic-test",
            detail="Approved synthetic loader test",
        )
    if role in {
        ViewRole.CURATED,
        ViewRole.SNAPSHOT,
        ViewRole.LOCKBOX,
        ViewRole.RAW,
    }:
        unlock = GatedUnlock(target=ref.canonical_identity)
    return authorize_access(
        registry=registry,
        request=AccessRequest(dataset_ref=ref, reason=reason, gated_unlock=unlock),
        policy=policy,
        context=AccessContext(
            session_id=uuid4(),
            os_user="tester",
            hostname="synthetic-vm",
            pid=1234,
            platform_git_commit="a" * 40,
            platform_git_dirty=False,
        ),
        audit_writer=audit,
    )
