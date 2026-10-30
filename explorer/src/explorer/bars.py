"""Gated market-data access for the chart.

Bars are loaded only through the Research Machine's documented path:
    classify_access -> authorize_access -> AccessGrant -> MarketFrameLoader
Access is classified first and anything that is not NORMAL (discovery-default)
is refused, so OOS, LOCKBOX, SNAPSHOT and the raw/curated parents have no code
path here at all.

One grant and one decoded frame are held per dataset identity for the life of
the process. That is the documented composition pattern, not an optimisation:
MarketFrameLoader.load() re-validates the grant via require_matches() on every
call, and its own contract states that authorization already happened when the
grant was issued. NORMAL access writes no audit event, so reuse suppresses
nothing.
"""

from __future__ import annotations

import getpass
import os
import socket
import threading
from typing import Any
from uuid import uuid4

import numpy as np
from trading_research.core.frames import MarketFrame
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.data import (
    AccessClassification,
    AccessContext,
    AccessRequest,
    DatasetRegistry,
    JsonlAuditWriter,
    MarketFrameLoader,
    authorize_access,
    classify_access,
    read_split_policy,
)

from explorer.config import AUDIT_PATH, DATA_ROOT, SPLIT_POLICIES, VIEWS_ROOT

COLUMNS = ("open", "high", "low", "close", "tick_volume")

# Display timeframes, as native-key grid widths in seconds. The grid is arithmetic
# on the broker's own wall-clock seconds; it is NOT a UTC or calendar grid, and no
# session, holiday or closure is claimed anywhere (ADR-0005).
TIMEFRAMES: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
}

# Every display timeframe uses phase 0, matching the grid the Zone Trader
# provider was configured with (bar_phase_offset_seconds = 0).
PHASE_OFFSET_SECONDS = 0

_lock = threading.Lock()
_registry: DatasetRegistry | None = None
_policies: dict[tuple[str, str, str], Any] = {}
_loaders: dict[tuple[str, str, str], MarketFrameLoader] = {}
_frames: dict[tuple[str, str], MarketFrame] = {}
_series: dict[tuple[str, str, int], dict[str, np.ndarray]] = {}


class DataAccessRefused(PermissionError):
    """The requested dataset is not discovery-default; the viewer refuses it."""


class UnknownTimeframe(ValueError):
    """The requested timeframe is not one this viewer builds."""


def bucket_numbers(keys: np.ndarray, *, grid_seconds: int,
                   phase_offset_seconds: int = PHASE_OFFSET_SECONDS) -> np.ndarray:
    """Return floor((key - phase) / grid) without signed-int64 subtraction.

    This is deliberately the same arithmetic the platform's own feature provider
    uses (features/reference.py::_arithmetic_bucket_numbers), so a display bucket
    and a research bucket always have identical boundaries. Keeping the two in
    step is the point; tests assert it on real keys.
    """
    quotient = np.floor_divide(keys, grid_seconds)
    remainder = np.remainder(keys, grid_seconds)
    return (quotient - (remainder < phase_offset_seconds)).astype(np.int64)


def aggregate_ohlc(
    keys: np.ndarray, opened: np.ndarray, high: np.ndarray,
    low: np.ndarray, close: np.ndarray, *, grid_seconds: int,
) -> dict[str, np.ndarray]:
    """Aggregate observed M1 rows into display candles on an arithmetic grid.

    DISPLAY ONLY — deliberately NOT the research aggregation. The platform's
    provider implements ID-1 predicate A: a bucket becomes usable only once a
    later-bucket row has been observed, so the final bucket is never returned.
    That is a no-lookahead rule for *research*. A researcher inspecting completed
    history wants to see the last candle, so this keeps it. Boundaries are
    identical; only the final-bucket rule differs, and nothing here feeds a model,
    a policy or a fit.

    A bucket with no observed rows produces no candle. Bars are never invented to
    fill a market closure.
    """
    if keys.size == 0:
        empty_i = np.empty(0, dtype=np.int64)
        empty_f = np.empty(0, dtype=np.float64)
        return {"keys": empty_i, "open": empty_f, "high": empty_f.copy(),
                "low": empty_f.copy(), "close": empty_f.copy()}

    buckets = bucket_numbers(keys, grid_seconds=grid_seconds)
    # Group boundaries: a new bucket starts wherever the bucket number changes.
    starts = np.concatenate((
        np.array([0], dtype=np.int64),
        (np.flatnonzero(buckets[1:] != buckets[:-1]) + 1).astype(np.int64),
    ))
    ends = np.concatenate((starts[1:], np.array([keys.size], dtype=np.int64)))

    return {
        # The candle is stamped with its bucket START key, not the first observed
        # row inside it, so candle times sit on the grid exactly.
        "keys": (buckets[starts] * np.int64(grid_seconds)
                 + np.int64(PHASE_OFFSET_SECONDS)).astype(np.int64),
        "open": opened[starts].astype(np.float64),
        "high": np.maximum.reduceat(high, starts).astype(np.float64),
        "low": np.minimum.reduceat(low, starts).astype(np.float64),
        "close": close[ends - 1].astype(np.float64),
    }


def _ensure_registry() -> None:
    global _registry
    if _registry is None:
        _registry = DatasetRegistry.discover([DATA_ROOT, VIEWS_ROOT])
        for path in SPLIT_POLICIES:
            policy = read_split_policy(path)
            identity = policy.identity
            key = (identity.adr, identity.policy_version, identity.policy_sha256)
            if key in _policies:
                raise RuntimeError(f"duplicate split-policy identity configured: {key}")
            _policies[key] = policy
            _loaders[key] = MarketFrameLoader(registry=_registry, split_policy=policy)


def _policy_key(manifest: Any) -> tuple[str, str, str]:
    """Resolve a view only through its exact pinned policy identity."""
    ref = manifest.split_policy_ref
    if ref is None:
        raise DataAccessRefused(
            f"{manifest.dataset_id} has no exact split-policy reference"
        )
    return (ref.adr, ref.policy_version, ref.policy_sha256)


def _frame_for(dataset: dict[str, str]) -> MarketFrame:
    """Return the decoded frame for one dataset identity, loading it once."""
    key = (dataset["dataset_id"], dataset["manifest_sha256"])
    with _lock:
        _ensure_registry()
        assert _registry is not None
        cached = _frames.get(key)
        if cached is not None:
            return cached

        ref = DatasetRef(
            dataset_id=dataset["dataset_id"],
            manifest_sha256=dataset["manifest_sha256"],
            role=ViewRole(dataset["role"]),
        )
        registered = _registry.resolve(ref)
        policy_key = _policy_key(registered.manifest)
        policy = _policies.get(policy_key)
        loader = _loaders.get(policy_key)
        if policy is None or loader is None:
            raise DataAccessRefused(
                f"{dataset['dataset_id']} pins an unconfigured split policy: {policy_key}"
            )

        classification = classify_access(registered.manifest, policy)
        if classification is not AccessClassification.NORMAL:
            raise DataAccessRefused(
                f"{dataset['dataset_id']} classifies as {classification.name}; "
                "the Trade Explorer serves discovery-default data only"
            )

        grant = authorize_access(
            registry=_registry,
            request=AccessRequest(ref),
            policy=policy,
            context=AccessContext(
                session_id=uuid4(),
                os_user=getpass.getuser(),
                hostname=socket.gethostname(),
                pid=os.getpid(),
                platform_git_commit="0" * 40,
                platform_git_dirty=False,
            ),
            audit_writer=JsonlAuditWriter(AUDIT_PATH),
        )
        if grant.audit_event_id is not None:
            raise DataAccessRefused("unexpected audit event for NORMAL access")

        frame = loader.load(grant, columns=COLUMNS)
        _frames[key] = frame
        return frame


def _series_for(dataset: dict[str, str], grid_seconds: int) -> dict[str, np.ndarray]:
    """Return the display candle series for one dataset and timeframe.

    M1 is the recorded data and is passed through untouched. Coarser timeframes
    are aggregated once per process and cached; the whole TRAIN view collapses to
    roughly 283k M5, 94k M15 or 24k H1 candles, so this stays small.
    """
    frame = _frame_for(dataset)
    key = (dataset["dataset_id"], dataset["manifest_sha256"], grid_seconds)
    with _lock:
        cached = _series.get(key)
        if cached is not None:
            return cached
        if grid_seconds == TIMEFRAMES["M1"]:
            series = {
                "keys": frame.keys,
                "open": frame.column("open"),
                "high": frame.column("high"),
                "low": frame.column("low"),
                "close": frame.column("close"),
            }
        else:
            series = aggregate_ohlc(
                frame.keys,
                frame.column("open"),
                frame.column("high"),
                frame.column("low"),
                frame.column("close"),
                grid_seconds=grid_seconds,
            )
        _series[key] = series
        return series


def window(
    dataset: dict[str, str],
    *,
    entry_key: int,
    exit_key: int,
    timeframe: str = "M1",
    before: int = 600,
    after: int = 400,
) -> dict[str, Any]:
    """Return a bounded candle window around one trade, measured in candles.

    Counting in candles rather than seconds keeps the amount of visible history
    stable across market closures, where a fixed time span would collapse to a
    handful of bars. It also means switching timeframe keeps the same number of
    candles on screen rather than the same elapsed time.
    """
    grid_seconds = TIMEFRAMES.get(timeframe)
    if grid_seconds is None:
        raise UnknownTimeframe(
            f"unknown timeframe {timeframe!r}; expected one of {', '.join(TIMEFRAMES)}"
        )

    frame = _frame_for(dataset)
    series = _series_for(dataset, grid_seconds)
    keys = series["keys"]

    # The candle containing a key is the last one whose bucket start is <= it.
    def containing(key: int) -> int:
        position = int(np.searchsorted(keys, key, side="right")) - 1
        return max(0, min(position, keys.size - 1))

    start_at = containing(entry_key)
    end_at = max(start_at, containing(exit_key))

    lo = max(0, start_at - before)
    hi = min(keys.size, end_at + after + 1)

    ks = keys[lo:hi]
    o, h, low_, c = (series[name][lo:hi] for name in ("open", "high", "low", "close"))

    return {
        "timeframe": timeframe,
        "grid_seconds": grid_seconds,
        "clock_domain": frame.clock_domain.identifier,
        "unit": frame.unit.value,
        "count": int(ks.size),
        "first_key": int(ks[0]) if ks.size else None,
        "last_key": int(ks[-1]) if ks.size else None,
        # Bucket-snapped marker anchors, so the browser never has to know the
        # bucket rule to place the entry and exit markers on the right candle.
        "entry_bar_key": int(keys[start_at]) if keys.size else None,
        "exit_bar_key": int(keys[end_at]) if keys.size else None,
        "bars": [
            {
                "t": int(ks[i]),
                "o": float(o[i]),
                "h": float(h[i]),
                "l": float(low_[i]),
                "c": float(c[i]),
            }
            for i in range(ks.size)
        ],
    }
