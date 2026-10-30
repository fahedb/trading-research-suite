from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from trading_research.core import ContractViolationError
from trading_research.experiments.resources import (
    _assert_worker_environment,
    _disk_usage,
    _effective_worker_count,
    _enforce_disk_preflight,
    _worker_environment,
)

if TYPE_CHECKING:
    from pathlib import Path


CLAMPS = {
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
}


def test_worker_environment_overrides_every_thread_control_and_preserves_base() -> None:
    base = {"PATH": "/bin", **dict.fromkeys(CLAMPS, "99")}

    environment = _worker_environment(base)

    assert environment["PATH"] == "/bin"
    assert all(environment[name] == "1" for name in CLAMPS)
    assert base["OMP_NUM_THREADS"] == "99"
    _assert_worker_environment(environment)


def test_worker_environment_assertion_fails_closed() -> None:
    for missing in sorted(CLAMPS):
        environment = dict.fromkeys(CLAMPS, "1")
        environment.pop(missing)
        with pytest.raises(ContractViolationError, match=missing):
            _assert_worker_environment(environment)
    environment = dict.fromkeys(CLAMPS, "1")
    environment["OMP_NUM_THREADS"] = "2"
    with pytest.raises(ContractViolationError, match="OMP_NUM_THREADS"):
        _assert_worker_environment(environment)


def test_effective_worker_count_enforces_cpu_memory_and_total_memory_ceiling() -> None:
    gib = 1_073_741_824
    assert (
        _effective_worker_count(
            requested_workers=10,
            memory_budget_bytes=16 * gib,
            assumed_peak_rss_bytes=gib,
            cpu_count=12,
            total_memory_bytes=24 * gib,
        )
        == 10
    )
    assert (
        _effective_worker_count(
            requested_workers=8,
            memory_budget_bytes=2 * gib,
            assumed_peak_rss_bytes=gib,
            cpu_count=12,
            total_memory_bytes=24 * gib,
        )
        == 2
    )
    assert (
        _effective_worker_count(
            requested_workers=8,
            memory_budget_bytes=16 * gib,
            assumed_peak_rss_bytes=gib,
            cpu_count=2,
            total_memory_bytes=24 * gib,
        )
        == 1
    )
    with pytest.raises(ContractViolationError, match="80 percent"):
        _effective_worker_count(
            requested_workers=8,
            memory_budget_bytes=20 * gib,
            assumed_peak_rss_bytes=gib,
            cpu_count=12,
            total_memory_bytes=24 * gib,
        )


@pytest.mark.parametrize(
    ("available_blocks", "warning", "refusal"),
    [
        (3_010, False, False),
        (3_000, True, False),
        (1_010, True, False),
        (1_000, True, True),
    ],
)
def test_disk_thresholds_are_exact_at_70_and_90_percent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    available_blocks: int,
    warning: bool,
    refusal: bool,
) -> None:
    statistics = os.statvfs_result((1, 1, 10_000, 10_000, available_blocks, 0, 0, 0, 0, 255))
    monkeypatch.setattr(os, "statvfs", lambda _path: statistics)

    usage = _disk_usage(tmp_path)

    assert usage._warning is warning
    assert usage._refusal is refusal
    if refusal:
        with pytest.raises(ContractViolationError, match="override"):
            _enforce_disk_preflight(tmp_path, allow_critical_usage=False)
        assert _enforce_disk_preflight(tmp_path, allow_critical_usage=True) == usage
    else:
        assert _enforce_disk_preflight(tmp_path, allow_critical_usage=False) == usage
