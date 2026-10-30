"""Exact policy selection for datasets from independent MT5 clock domains."""

from __future__ import annotations

from pathlib import Path

import pytest
from trading_research.core.identity import DatasetRef, ViewRole

from explorer import bars
from explorer.config import VIEWS_ROOT


@pytest.mark.parametrize(
    ("dataset_id", "manifest_sha256", "policy_version", "clock_domain"),
    [
        (
            "XAUUSD_S_M1_TRAIN_V1",
            "30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e",
            "v1",
            "mt5.justmarkets-demo3.wallclock",
        ),
        (
            "BTCUSD_S_M1_TRAIN_V1",
            "73454e8dacf36b233144f1d7b7866ec30cf0c60f7a47efd366c43bc63b632b9b",
            "btc-v1",
            "mt5.justmarkets-demo.wallclock",
        ),
    ],
)
def test_train_view_resolves_its_own_exact_policy(
    dataset_id: str,
    manifest_sha256: str,
    policy_version: str,
    clock_domain: str,
) -> None:
    if not (Path(VIEWS_ROOT) / dataset_id / "manifest.json").is_file():
        pytest.skip(f"real view {dataset_id} is not installed")

    with bars._lock:
        bars._ensure_registry()
        assert bars._registry is not None
        registered = bars._registry.resolve(
            DatasetRef(
                dataset_id=dataset_id,
                manifest_sha256=manifest_sha256,
                role=ViewRole.TRAIN,
            )
        )
        policy_key = bars._policy_key(registered.manifest)
        policy = bars._policies[policy_key]

    assert policy.identity.policy_version == policy_version
    assert policy.clock_domain.identifier == clock_domain
