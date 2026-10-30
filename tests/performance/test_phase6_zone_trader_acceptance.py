"""Explicit TRAIN-only acceptance evidence for the Zone Trader v13 feature."""

from __future__ import annotations

import getpass
import json
import os
import socket
import subprocess
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import pytest

from tests.performance._measure_child import (
    POLICY_PATH,
    REGISTRY_ROOTS,
    TRAIN_EXPECTED_ROWS,
    TRAIN_REF,
)
from trading_research.artifacts import PlatformIdentity
from trading_research.core import NativeTimeKey, ViewRole
from trading_research.data import (
    AccessClassification,
    AccessContext,
    AccessRequest,
    ContextShortfall,
    DatasetRegistry,
    MarketFrameLoader,
    authorize_access,
    open_bounded_view,
    read_split_policy,
)
from trading_research.experiments.features import compute_features
from trading_research.features.reference import (
    ZoneTraderV13,
    _aggregate_complete_strategy_bars,
    _AggregatedStrategyBars,
    _run_zone_trader_state,
    _truncated_wilder_atr,
    _zone_trader_primitives,
    _ZoneTraderPrimitives,
    _ZoneTraderStrategyOutput,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy.typing as npt

    from trading_research.data import BoundedView, SplitPolicy

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).parents[2]
_RUNNING_FILE = Path(__file__).resolve()
_REQUIRED_MARKET_COLUMNS = ("open", "high", "low", "close", "tick_volume")
_EVIDENCE_SCHEMA = "phase6-zone-trader-atr-acceptance-v1"
_ATR_PERIOD = 14
_W_ATR = 300
_VISIBLE_M1_REACH = 26_534


class _RejectingAuditWriter:
    """Prove that fixed published-TRAIN access cannot append an audit event."""

    def append(self, event: Mapping[str, object]) -> None:
        del event
        raise AssertionError("published TRAIN access unexpectedly attempted an audit write")


def _git_output(*arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssertionError(f"Git inspection failed for {arguments!r}: {exc}") from exc
    return completed.stdout


def _clean_committed_platform_identity() -> PlatformIdentity:
    """Bind reported evidence to the exact committed harness and clean tree."""
    head = _git_output("rev-parse", "HEAD").decode().strip()
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AssertionError("Zone Trader acceptance requires a clean repository worktree")
    try:
        relative = _RUNNING_FILE.relative_to(_REPO_ROOT).as_posix()
        committed = _git_output("show", f"{head}:{relative}")
        current = _RUNNING_FILE.read_bytes()
    except (OSError, ValueError) as exc:
        raise AssertionError("cannot verify the committed Zone Trader harness") from exc
    if committed != current:
        raise AssertionError("Zone Trader acceptance harness is not the committed HEAD version")
    return PlatformIdentity(git_commit=head, git_dirty=False, diff_sha256=None)


def _native_key(value: int, policy: SplitPolicy) -> NativeTimeKey:
    return NativeTimeKey(value, policy.time_unit, policy.clock_domain)


def _open_train_view(platform_identity: PlatformIdentity) -> BoundedView:
    policy = read_split_policy(POLICY_PATH)
    registry = DatasetRegistry.discover(REGISTRY_ROOTS)
    registered = registry.resolve(TRAIN_REF)
    assert registered.manifest.role is ViewRole.TRAIN
    assert registered.manifest.expected_row_count == TRAIN_EXPECTED_ROWS
    grant = authorize_access(
        registry=registry,
        request=AccessRequest(dataset_ref=TRAIN_REF),
        policy=policy,
        context=AccessContext(
            session_id=uuid4(),
            os_user=getpass.getuser(),
            hostname=socket.gethostname(),
            pid=os.getpid(),
            platform_git_commit=platform_identity.git_commit,
            platform_git_dirty=False,
        ),
        audit_writer=_RejectingAuditWriter(),
    )
    assert grant.classification is AccessClassification.NORMAL
    assert grant.audit_event_id is None

    admitted = grant.registered_manifest.manifest.admitted_native_range
    eval_start = _native_key(admitted.start, policy)
    eval_end = _native_key(admitted.end, policy)
    requested_context_start = _native_key(admitted.start - 1, policy)
    shortfall = ContextShortfall(
        requested_context_start=requested_context_start,
        resolved_context_start=eval_start,
        reason_code="published-train-initial-boundary",
        detail=(
            "The full published TRAIN view begins at its earliest observed row; "
            "the Zone Trader acceptance run records the provider warm-up."
        ),
        accepted=True,
    )
    loader = MarketFrameLoader(registry=registry, split_policy=policy)
    view = open_bounded_view(
        loader=loader,
        grants=(grant,),
        context_start=requested_context_start,
        eval_start=eval_start,
        eval_end=eval_end,
        columns=_REQUIRED_MARKET_COLUMNS,
        context_shortfall=shortfall,
    )
    assert len(view.segments) == 1
    assert view.eval_frame.dataset_ref == TRAIN_REF
    assert view.eval_frame.num_rows == TRAIN_EXPECTED_ROWS
    assert view.eval_frame.column_names == _REQUIRED_MARKET_COLUMNS
    return view


def _recursive_wilder_atr(
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Return a long-history recursive Wilder/RMA ATR(14) reference."""
    true_range = np.empty(close.size, dtype=np.float64)
    true_range[0] = high[0] - low[0]
    true_range[1:] = np.maximum.reduce(
        (
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        )
    )
    result = np.full(close.size, np.nan, dtype=np.float64)
    result[_ATR_PERIOD - 1] = true_range[:_ATR_PERIOD].mean(dtype=np.float64)
    for index in range(_ATR_PERIOD, close.size):
        result[index] = (
            result[index - 1] * (_ATR_PERIOD - 1) + true_range[index]
        ) / _ATR_PERIOD
    return result


def _primitives(
    bars: _AggregatedStrategyBars,
    atr: npt.NDArray[np.float64],
) -> _ZoneTraderPrimitives:
    return _zone_trader_primitives(
        bars,
        atr,
        bar_grid_seconds=300,
        bar_phase_offset_seconds=0,
        anchor_grid_seconds=3600,
        anchor_phase_offset_seconds=0,
        vol_len=20,
        vol_mult=2.0,
        min_fvg_atr=0.25,
        max_base_body=0.5,
        max_base_atr=0.8,
        min_move_atr=1.2,
        use_volume_zones=True,
        use_imbalance=True,
        use_demand_supply=True,
    )


def _strategy(
    bars: _AggregatedStrategyBars,
    atr: npt.NDArray[np.float64],
    primitives: _ZoneTraderPrimitives,
) -> _ZoneTraderStrategyOutput:
    return _run_zone_trader_state(
        bars.opened,
        bars.high,
        bars.low,
        bars.close,
        atr,
        primitives,
        max_zone_age_rows=5000,
        max_zones=15,
        break_buffer_atr=0.5,
        confirmation_mode="rejection_wick",
        minimum_wick=0.35,
        max_bars_wait=6,
        stop_buffer_atr=0.5,
        reward_risk_target=2.0,
    )


def _difference_summary(values: npt.NDArray[np.float64]) -> dict[str, float | int]:
    return {
        "count": int(values.size),
        "mean": float(values.mean(dtype=np.float64)),
        "median": float(np.quantile(values, 0.5)),
        "p90": float(np.quantile(values, 0.9)),
        "p99": float(np.quantile(values, 0.99)),
        "p999": float(np.quantile(values, 0.999)),
        "maximum": float(values.max()),
    }


def _event_records(
    output: _ZoneTraderStrategyOutput,
    *,
    start: int,
) -> dict[tuple[int, int, int, int, int, int], int]:
    """Key events by stable zone identity plus per-zone occurrence number."""
    occurrence: defaultdict[tuple[int, int, int, int, int], int] = defaultdict(int)
    records: dict[tuple[int, int, int, int, int, int], int] = {}
    for row in np.flatnonzero(output.event_signed[start:] != 0.0) + start:
        created_at = int(output.event_zone_created_at[row])
        assert created_at >= 0
        identity = (
            created_at,
            int(output.zone_kind[row]),
            int(output.zone_top[row].view(np.uint64)),
            int(output.zone_bottom[row].view(np.uint64)),
            int(output.event_signed[row]),
        )
        ordinal = occurrence[identity]
        occurrence[identity] += 1
        records[(*identity, ordinal)] = int(row)
    return records


def _event_difference_summary(
    finite: _ZoneTraderStrategyOutput,
    recursive: _ZoneTraderStrategyOutput,
    *,
    start: int,
) -> dict[str, int]:
    finite_records = _event_records(finite, start=start)
    recursive_records = _event_records(recursive, start=start)
    common = finite_records.keys() & recursive_records.keys()
    moved_distances = [
        abs(finite_records[identity] - recursive_records[identity])
        for identity in common
        if finite_records[identity] != recursive_records[identity]
    ]
    return {
        "finite_events": len(finite_records),
        "recursive_events": len(recursive_records),
        "unchanged": sum(
            finite_records[identity] == recursive_records[identity] for identity in common
        ),
        "moved": len(moved_distances),
        "maximum_move_m5_rows": max(moved_distances, default=0),
        "added": len(finite_records.keys() - recursive_records.keys()),
        "removed": len(recursive_records.keys() - finite_records.keys()),
    }


def test_zone_trader_train_acceptance_and_feature_wall_time() -> None:
    """Report G-2's seven comparisons and one full-TRAIN feature-stage timing."""
    platform_identity = _clean_committed_platform_identity()
    view = _open_train_view(platform_identity)
    provider = ZoneTraderV13()
    assert provider.window.max_prior_rows == _VISIBLE_M1_REACH

    started = perf_counter()
    feature_frame = compute_features(view, (provider,))
    feature_wall_seconds = perf_counter() - started
    assert feature_frame.num_rows == TRAIN_EXPECTED_ROWS
    assert feature_frame.column_names == provider.feature_names
    for name in feature_frame.column_names:
        values = feature_frame.column(name)
        assert values.dtype == np.float64
        assert values.shape == (TRAIN_EXPECTED_ROWS,)
        assert not np.isinf(values).any()
    assert np.isnan(feature_frame.column("zt_event_signed")[:_VISIBLE_M1_REACH]).all()
    del feature_frame

    market = view.eval_frame
    tick_volume = np.asarray(market.column("tick_volume"), dtype=np.float64)
    assert np.isfinite(tick_volume).all()
    assert (tick_volume >= 0.0).all()
    assert float(tick_volume.max()) > float(tick_volume.min())

    bars = _aggregate_complete_strategy_bars(
        market.keys,
        np.asarray(market.column("open"), dtype=np.float64),
        np.asarray(market.column("high"), dtype=np.float64),
        np.asarray(market.column("low"), dtype=np.float64),
        np.asarray(market.column("close"), dtype=np.float64),
        tick_volume,
        bar_grid_seconds=300,
        bar_phase_offset_seconds=0,
    )
    assert bars.close.size > _W_ATR
    finite_atr = _truncated_wilder_atr(bars.high, bars.low, bars.close, _W_ATR)
    recursive_atr = _recursive_wilder_atr(bars.high, bars.low, bars.close)
    common_atr = np.isfinite(finite_atr) & np.isfinite(recursive_atr)
    atr_difference = np.abs(finite_atr[common_atr] - recursive_atr[common_atr])
    assert atr_difference.size == bars.close.size - _W_ATR
    assert np.isfinite(atr_difference).all()

    finite_primitives = _primitives(bars, finite_atr)
    recursive_primitives = _primitives(bars, recursive_atr)
    finite_strategy = _strategy(bars, finite_atr, finite_primitives)
    recursive_strategy = _strategy(bars, recursive_atr, recursive_primitives)
    visible_m5_start = int(
        np.searchsorted(bars.usable_positions, _VISIBLE_M1_REACH, side="left")
    )
    assert visible_m5_start >= _W_ATR

    evidence = {
        "schema": _EVIDENCE_SCHEMA,
        "platform_git_commit": platform_identity.git_commit,
        "train_identity": {
            "dataset_id": TRAIN_REF.dataset_id,
            "manifest_sha256": TRAIN_REF.manifest_sha256,
        },
        "train_rows": TRAIN_EXPECTED_ROWS,
        "strategy_m5_bars": int(bars.close.size),
        "visible_m5_start": visible_m5_start,
        "tick_volume": {
            "finite": True,
            "non_negative": True,
            "non_constant": True,
        },
        "feature_wall_seconds": feature_wall_seconds,
        "atr_absolute_difference": _difference_summary(atr_difference),
        "classification_row_differences": {
            "fvg_size_ok": int(
                np.count_nonzero(
                    finite_primitives.fvg_size_ok[_W_ATR:]
                    != recursive_primitives.fvg_size_ok[_W_ATR:]
                )
            ),
            "is_base": int(
                np.count_nonzero(
                    finite_primitives.is_base[_W_ATR:]
                    != recursive_primitives.is_base[_W_ATR:]
                )
            ),
            "impulse_up": int(
                np.count_nonzero(
                    finite_primitives.impulse_up[_W_ATR:]
                    != recursive_primitives.impulse_up[_W_ATR:]
                )
            ),
            "impulse_down": int(
                np.count_nonzero(
                    finite_primitives.impulse_down[_W_ATR:]
                    != recursive_primitives.impulse_down[_W_ATR:]
                )
            ),
        },
        "zone_break_differences": {
            "rows": int(
                np.count_nonzero(
                    finite_strategy.zone_break_count[visible_m5_start:]
                    != recursive_strategy.zone_break_count[visible_m5_start:]
                )
            ),
            "finite_breaks": int(
                finite_strategy.zone_break_count[visible_m5_start:].sum(dtype=np.int64)
            ),
            "recursive_breaks": int(
                recursive_strategy.zone_break_count[visible_m5_start:].sum(dtype=np.int64)
            ),
        },
        "confirmation_row_differences": int(
            np.count_nonzero(
                finite_strategy.confirmation_observed[visible_m5_start:]
                != recursive_strategy.confirmation_observed[visible_m5_start:]
            )
        ),
        "event_row_differences": _event_difference_summary(
            finite_strategy,
            recursive_strategy,
            start=visible_m5_start,
        ),
    }
    print("ZONE_TRADER_ACCEPTANCE_JSON=" + json.dumps(evidence, sort_keys=True))
