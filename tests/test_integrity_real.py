"""Research-VM smoke over real bytes through an ephemeral TRAIN target."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from tests.load_fixtures import RecordingAuditWriter
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.data import DatasetRegistry, MarketFrameLoader
from trading_research.data.gating import (
    AccessContext,
    AccessRequest,
    authorize_access,
    read_split_policy,
)
from trading_research.data.manifests import read_manifest

if TYPE_CHECKING:
    from typing import BinaryIO

DATASET_ROOT = Path("/path/to/research-data")
VIEWS_ROOT = Path("/path/to/research-views")
REAL_AUDIT = Path("/path/to/research-runs/audit.jsonl")
RAW_MANIFEST = DATASET_ROOT / "XAUUSD_S_M1_MT5_V1" / "manifest.json"
CURATED_MANIFEST = DATASET_ROOT / "XAUUSD_S_M1_CURATED_V1" / "manifest.json"
POLICY_PATH = (
    Path(__file__).parents[1] / "config" / "split-policies" / "adr-0012-v1.json"
)

RAW_SHA256 = "8f91f4516b9a1dbe46c82152fe78021151bc4a3188f623ea6ff9a2c659813cb0"
CURATED_SHA256 = "6f5c7b50e5ee40ee83913f97ba9164713d1296f234e9a759315c6182dbaf0da2"
POLICY_SHA256 = "b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb"


def _filesystem_state(path: Path) -> tuple[object, ...]:
    """Capture metadata only, proving this test leaves an external path unchanged."""
    if not path.exists():
        return ("absent",)
    paths = (path, *sorted(path.rglob("*"))) if path.is_dir() else (path,)
    return tuple(
        (
            str(item.relative_to(path.parent)),
            item.lstat().st_mode,
            item.lstat().st_size,
            item.lstat().st_mtime_ns,
            item.lstat().st_ctime_ns,
        )
        for item in paths
    )


def _real_target_document() -> dict[str, Any]:
    """Derive the temporary target from authorized manifest metadata only."""
    raw = read_manifest(RAW_MANIFEST)
    curated = read_manifest(CURATED_MANIFEST)
    policy = read_split_policy(POLICY_PATH)
    assert raw.manifest_sha256 == RAW_SHA256
    assert curated.manifest_sha256 == CURATED_SHA256
    assert policy.identity.policy_sha256 == POLICY_SHA256

    selected = tuple(
        partition
        for partition in curated.partitions
        if partition.key in {"2020", "2021", "2022", "2023"}
    )
    assert tuple(partition.key for partition in selected) == ("2020", "2021", "2022", "2023")
    assert selected[0].first_key >= policy.train_range.start
    assert selected[-1].last_key <= policy.train_range.end
    return {
        "manifest_format_version": "1.1",
        "view_id": "XAUUSD_S_M1_TRAIN_SLICE1D_EPHEMERAL_V1",
        "role": "train",
        "discovery_default": False,
        "adapter_id": "mt5",
        "symbol": curated.symbol,
        "timeframe": curated.timeframe,
        "clock_domain": curated.clock_domain.identifier,
        "time_unit": curated.time_unit.value,
        "calendar_time_policy": policy.calendar_time_policy.value,
        "columns": [
            {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
            {
                "name": "timestamp_mt5_encoded",
                "dtype": "datetime64[ns]",
                "purpose": "display_only",
            },
            {"name": "open", "dtype": "float64", "purpose": "value"},
            {"name": "high", "dtype": "float64", "purpose": "value"},
            {"name": "low", "dtype": "float64", "purpose": "value"},
            {"name": "close", "dtype": "float64", "purpose": "value"},
            {"name": "tick_volume", "dtype": "int64", "purpose": "value"},
            {"name": "spread", "dtype": "int32", "purpose": "value"},
            {"name": "real_volume", "dtype": "int64", "purpose": "value"},
        ],
        "admitted_partitions": [
            {
                "key": partition.key,
                "filename": partition.filename,
                "sha256": partition.sha256,
                "rows": partition.rows,
                "first_key": partition.first_key,
                "last_key": partition.last_key,
            }
            for partition in selected
        ],
        "admitted_native_range": {
            "start": selected[0].first_key,
            "end": selected[-1].last_key,
        },
        "row_count_expected": sum(partition.rows for partition in selected),
        "parent": {
            "dataset_id": curated.dataset_id,
            "manifest_sha256": curated.manifest_sha256,
        },
        "partition_source": {
            "dataset_id": raw.dataset_id,
            "manifest_sha256": raw.manifest_sha256,
        },
        "split_policy_ref": {
            "adr": policy.identity.adr,
            "policy_version": policy.identity.policy_version,
            "policy_sha256": policy.identity.policy_sha256,
        },
        "generated_at_utc": "2026-08-08T00:00:00+00:00",
        "generator": {
            "name": "slice1d-ephemeral-test-fixture",
            "version": "1",
            "platform_git_commit": "0" * 40,
        },
        "notes": "Temporary exact-hash-pinned loader smoke; not a persistent Tier-1 view.",
    }


@pytest.mark.skipif(
    not all(path.is_file() for path in (RAW_MANIFEST, CURATED_MANIFEST, POLICY_PATH)),
    reason="research dataset or pinned policy is unavailable",
)
def test_ephemeral_real_train_target_loads_after_full_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hash and load all four TRAIN partitions without persistent artifacts."""
    import trading_research.data.integrity as integrity

    views_before = _filesystem_state(VIEWS_ROOT)
    audit_before = _filesystem_state(REAL_AUDIT)
    temporary_root = tmp_path / "ephemeral-registry"
    target_directory = temporary_root / "train"
    target_directory.mkdir(parents=True)
    target_path = target_directory / "manifest.json"
    target_path.write_text(json.dumps(_real_target_document(), indent=2))

    policy = read_split_policy(POLICY_PATH)
    registry = DatasetRegistry.discover([DATASET_ROOT, temporary_root])
    target = read_manifest(target_path)
    ref = DatasetRef(target.dataset_id, target.manifest_sha256, ViewRole.TRAIN)
    audit = RecordingAuditWriter()
    grant = authorize_access(
        registry=registry,
        request=AccessRequest(dataset_ref=ref),
        policy=policy,
        context=AccessContext(
            session_id=uuid4(),
            os_user="masterpiece",
            hostname="research-vm",
            pid=1234,
            platform_git_commit="a" * 40,
            platform_git_dirty=False,
        ),
        audit_writer=audit,
    )
    assert audit.events == []

    events: list[str] = []
    real_hash = integrity._sha256_partition
    real_decode = cast("Any", integrity._decode_partition)  # type: ignore[attr-defined]

    def traced_hash(source: BinaryIO) -> str:
        events.append("hash")
        return real_hash(source)

    def traced_decode(
        source: BinaryIO,
        *,
        path_label: str,
        value_columns: tuple[str, ...],
        admitted_first_key: int,
        admitted_last_key: int,
    ) -> object:
        events.append(f"decode:{Path(path_label).name}")
        return real_decode(
            source,
            path_label=path_label,
            value_columns=value_columns,
            admitted_first_key=admitted_first_key,
            admitted_last_key=admitted_last_key,
        )

    monkeypatch.setattr(integrity, "_sha256_partition", traced_hash)
    monkeypatch.setattr(integrity, "_decode_partition", traced_decode)
    frame = MarketFrameLoader(registry=registry, split_policy=policy).load(grant)

    assert events == [
        "hash",
        "hash",
        "hash",
        "hash",
        "decode:2020.parquet",
        "hash",
        "decode:2021.parquet",
        "hash",
        "decode:2022.parquet",
        "hash",
        "decode:2023.parquet",
        "hash",
    ]
    assert frame.num_rows == 1_414_538
    assert int(frame.keys[0]) == 1_577_926_800
    assert int(frame.keys[-1]) == 1_703_894_160
    assert frame.column_names == (
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    )
    assert frame.schema == {
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "tick_volume": "int64",
        "spread": "int32",
        "real_volume": "int64",
    }
    assert frame.clock_domain.identifier == "mt5.justmarkets-demo3.wallclock"
    assert frame.unit.value == "seconds"
    assert "timestamp_mt5_encoded" not in frame.column_names
    assert all(not frame.column(name).flags.writeable for name in frame.column_names)
    assert not frame.keys.flags.writeable
    assert target_path.is_file()
    assert temporary_root in target_path.parents
    assert audit.events == []
    assert _filesystem_state(VIEWS_ROOT) == views_before
    assert _filesystem_state(REAL_AUDIT) == audit_before
