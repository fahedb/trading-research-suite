"""Builders for synthetic manifests, so tests need no real dataset.

Real TRAIN/VALIDATION views do not exist until Slice 1F, and gated-path tests
must never depend on a real ceremony, so role and discovery behaviour is
exercised entirely on synthetic files written into ``tmp_path``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_POLICY = "c" * 64

# What the synthetic raw builder's trade server normalizes to, so parent and
# derived fixtures describe one coherent clock rather than two.
CLOCK_DOMAIN = "mt5.synth-server.wallclock"


def raw_manifest(
    *,
    dataset_id: str = "SYNTH_PARENT_V1",
    first_key: int = 1_000,
    last_key: int = 1_999,
) -> dict[str, Any]:
    """Return a minimal document in the legacy raw exporter format.

    Used wherever a test needs a parent: a raw dataset is the root of the
    lineage and declares no parent of its own, which is what makes it a
    terminating point for parent resolution.
    """
    return {
        "dataset_id": dataset_id,
        "dataset_builder_version": "1.0.0",
        "source": "MetaTrader5",
        "symbol": "SYNTH",
        "timeframe": "M1",
        "timestamp_policy": "raw_only",
        "trade_server": "Synth-Server",
        "terminal_time_model": {"classification": "BROKER_WALL_CLOCK_ENCODED_AS_EPOCH"},
        "range": {"first_raw": first_key, "last_raw": last_key},
        "total_row_count": 1_000,
        "partitions": [
            {
                "year": 2020,
                "filename": "p0.parquet",
                "rows": 1_000,
                "first_raw": first_key,
                "last_raw": last_key,
                "sha256": DIGEST_B,
                "bytes": 123,
            }
        ],
    }


def curated_manifest(
    *,
    dataset_id: str = "SYNTH_CURATED_V1",
    parent_id: str = "SYNTH_PARENT_V1",
    parent_relative_path: str = "../parent",
    first_key: int = 1_000,
    last_key: int = 1_999,
) -> dict[str, Any]:
    """Return a minimal document in the legacy curated-view format.

    The legacy curated view is the one format that declares a source-relative
    parent *path* (provenance, never identity) and pins no parent digest —
    tests of exactly those behaviours use this builder.
    """
    return {
        "kind": "curated_view",
        "curation_format_version": "1.0.0",
        "curated_dataset_id": dataset_id,
        "parent_raw_dataset_id": parent_id,
        "parent_raw_dataset_relative_path": parent_relative_path,
        "symbol": "SYNTH",
        "timeframe": "M1",
        "timestamp_policy": "raw_only",
        "trade_server": "Synth-Server",
        "columns": ["mt5_time_raw", "timestamp_mt5_encoded", "close"],
        "included_partitions": [
            {
                "key": "2020",
                "filename": "p0.parquet",
                "rows": 1_000,
                "first_raw": first_key,
                "last_raw": last_key,
                "sha256": DIGEST_B,
            }
        ],
        "first_usable_raw": first_key,
        "last_usable_raw": last_key,
        "total_usable_rows": 1_000,
    }


def v11_manifest(
    *,
    view_id: str = "SYNTH_TRAIN_V1",
    role: str = "train",
    discovery_default: bool = True,
    parent_id: str = "SYNTH_PARENT_V1",
    parent_sha256: str = DIGEST_A,
    source_id: str | None = None,
    source_sha256: str | None = None,
    policy_sha256: str = DIGEST_POLICY,
    first_key: int = 1_000,
    last_key: int = 1_999,
    rows: int = 1_000,
) -> dict[str, Any]:
    """Return a valid format-1.1 document, adjustable per test.

    ``partition_source`` defaults to the same identity as the parent, which
    mirrors the simple case; tests exercising the distinction pass
    ``source_id``/``source_sha256`` explicitly. Partition span, view range
    and row counts default mutually consistent, as the validators require.
    """
    return {
        "manifest_format_version": "1.1",
        "view_id": view_id,
        "role": role,
        "discovery_default": discovery_default,
        "adapter_id": "synth",
        "symbol": "SYNTH",
        "timeframe": "M1",
        "clock_domain": CLOCK_DOMAIN,
        "time_unit": "seconds",
        "calendar_time_policy": "native-wallclock-v1",
        "columns": [
            {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
            {"name": "close", "dtype": "float64", "purpose": "value"},
            {"name": "decoded_ts", "dtype": "string", "purpose": "display_only"},
        ],
        "admitted_partitions": [
            {
                "key": "p0",
                "filename": "p0.parquet",
                "sha256": DIGEST_B,
                "rows": rows,
                "first_key": first_key,
                "last_key": last_key,
            }
        ],
        "admitted_native_range": {"start": first_key, "end": last_key},
        "row_count_expected": rows,
        "parent": {"dataset_id": parent_id, "manifest_sha256": parent_sha256},
        "partition_source": {
            "dataset_id": source_id if source_id is not None else parent_id,
            "manifest_sha256": source_sha256 if source_sha256 is not None else parent_sha256,
        },
        "split_policy_ref": {
            "adr": "ADR-0012",
            "policy_version": "v1",
            "policy_sha256": policy_sha256,
        },
        "generated_at_utc": "2026-08-07T00:00:00+00:00",
        "generator": {
            "name": "synthetic-fixture",
            "version": "0",
            "platform_git_commit": "0" * 40,
        },
    }


def write_manifest(directory: Path, document: dict[str, Any]) -> Path:
    """Write ``document`` as ``manifest.json`` under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(json.dumps(document, indent=2))
    return path
