#!/usr/bin/env python3
"""Owner-operated BTCUSD.s Tier-1 view-generation composition root.

This composes the accepted Stage-B implementation with exact BTC dataset and
split-policy identities.  Unlike the original XAU bootstrap, execution may
run from any clean commit because strategy development occurs on a dedicated
branch; the exact committed HEAD remains pinned in every generated manifest.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import generate_views
from trading_research.core.identity import DatasetIdentity, DatasetRef, ViewRole
from trading_research.data import views_generation
from trading_research.data.gating import SplitPolicyIdentity

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATASET_ROOT = Path("/path/to/research-data")

_CONFIG = generate_views._StageBConfig(
    repo_root=_REPO_ROOT,
    script_path=Path(__file__).resolve(),
    dataset_root=_DATASET_ROOT,
    views_root=Path("/path/to/research-views"),
    audit_path=Path("/path/to/research-runs/audit.jsonl"),
    policy_path=_REPO_ROOT / "config/split-policies/adr-0012-btc-v1.json",
    source_ref=DatasetRef(
        dataset_id="BTCUSD_S_M1_CURATED_V1",
        manifest_sha256="83642af0a449842ffb92c80804e1edef8fac1bfd7470ee40457084c55ddafc68",
        role=ViewRole.CURATED,
    ),
    partition_source=DatasetIdentity(
        dataset_id="BTCUSD_S_M1_MT5_V1",
        manifest_sha256="e5dfd3cd97a192776975fb718927f43f90a5e3e6db20c3b513dceafa44ef3b9b",
    ),
    policy_identity=SplitPolicyIdentity(
        adr="ADR-0012",
        policy_version="btc-v1",
        policy_sha256="3896cd9bedfe9357279c02df4904cda92d698b2d079c65e7bed6455503d55899",
    ),
    view_ids=views_generation._ViewIds(
        snapshot="BTCUSD_S_M1_SNAPSHOT_V1",
        train="BTCUSD_S_M1_TRAIN_V1",
        validation="BTCUSD_S_M1_VALIDATION_V1",
        oos="BTCUSD_S_M1_OOS_V1",
        lockbox="BTCUSD_S_M1_LOCKBOX_V1",
    ),
    generator_name="slice1f-view-generator",
    generator_version="1",
    policy_cap_encoded="2026-07-31T23:59:00",
    expected_source_fingerprint=(
        "2edb0a03ab4b503ef40eef6c24dc1ac1dd6d4e2fd98e7726e1ad0ad9e62e9743"
    ),
    require_canonical_main=False,
)


if __name__ == "__main__":
    raise SystemExit(generate_views.main(sys.argv[1:], config=_CONFIG))
