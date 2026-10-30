"""Read run metadata and TradeLog artifacts from files. Files are truth.

No platform reader exists for a TradeLog — the `.npz` + `__meta__` container is a
versioned, self-describing format, and the Research Machine's own test suite reads
it exactly this way (tests/test_experiments.py). `format` and `format_version` are
asserted on every read so a format change fails loudly instead of silently.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from explorer.config import RUNS_ROOT

ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

TRADE_ARTIFACT = re.compile(r"^fold-(\d{4})-policy-(\d{4})-trades\.npz$")

EXIT_REASONS = {
    1: "TAKE_PROFIT",
    2: "STOP_LOSS",
    3: "TARGET_CHANGE",
    4: "BOUNDARY_FORCED_CLOSE",
}

TRADE_LOG_FORMAT = "trading-research-trade-log"
# Version 1 has twelve columns and no recorded bracket levels, so its TP/SL must
# be reconstructed from the run's single global ExecutionSpec. Version 2 records
# the exact levels the simulator used on every trade, which is the only way a
# per-trade bracket can be shown at all. Both are readable; anything else is not.
SUPPORTED_TRADE_LOG_VERSIONS = (1, 2)
RECORDED_BRACKETS_VERSION = 2

# How a trade's displayed TP/SL was obtained. This travels with every trade so
# the viewer can say which it is showing rather than implying they are the same.
BRACKET_RECORDED = "recorded"
BRACKET_RECONSTRUCTED = "reconstructed_v1"


class RunNotFoundError(LookupError):
    """No such run directory, or it is not in a state this viewer can show."""


def run_dir(run_id: str) -> Path:
    """Resolve a run id to its directory, refusing anything but a bare ULID.

    The pattern check is a containment control, not cosmetics: it is what stops a
    crafted id from escaping the run collection via traversal.
    """
    if not ULID.match(run_id):
        raise RunNotFoundError(f"not a run identifier: {run_id!r}")
    path = RUNS_ROOT / "runs" / run_id
    if not path.is_dir():
        raise RunNotFoundError(f"no such run: {run_id}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def float_param(value: object) -> float | None:
    """Decode the platform's exact float-hex parameter encoding."""
    if value is None:
        return None
    if isinstance(value, dict) and "__float_hex__" in value:
        return float.fromhex(value["__float_hex__"])
    if isinstance(value, (int, float)):
        return float(value)
    return None


def run_detail(run_id: str) -> dict[str, Any]:
    """Return one run's identity and configuration, read from its own files."""
    directory = run_dir(run_id)
    state = _read_json(directory / "state.json")
    identity = _read_json(directory / "identity.json")
    manifest = _read_json(directory / "manifest.json")
    resolved = identity["fingerprint_inputs"]["resolved_config"]
    dataset = resolved["dataset"]
    execution = resolved.get("execution") or {}
    binding = execution.get("cost_binding") or {}

    artifacts = manifest.get("files", {}).get("artifacts", {})
    trade_files = sorted(name for name in artifacts if TRADE_ARTIFACT.match(name))

    return {
        "run_id": run_id,
        "state": state["state"],
        "canonical": bool(manifest.get("canonical")),
        "experiment_fingerprint": identity["experiment_fingerprint"],
        "oos_touched": bool(state.get("oos_touched")),
        "lockbox_touched": bool(state.get("lockbox_touched")),
        "finished_unix_ns": state.get("finished_unix_ns"),
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "manifest_sha256": dataset["manifest_sha256"],
            "role": dataset["role"],
        },
        "plugins": {
            "features": [f["name"] for f in resolved.get("features", [])],
            "model": (resolved.get("model") or {}).get("name"),
            "policies": [p["name"] for p in resolved.get("policies", [])],
        },
        "execution": {
            "execution_id": execution.get("execution_id"),
            "take_profit_pips": float_param(execution.get("take_profit_pips")),
            "stop_loss_pips": float_param(execution.get("stop_loss_pips")),
            "lot_size": float_param(execution.get("lot_size")),
            "price_units_per_pip": float_param(binding.get("price_units_per_pip")),
            "cost_semantics": binding.get("cost_semantics"),
            "execution_price_model": binding.get("execution_price_model"),
        },
        "platform_git_commit": identity["fingerprint_inputs"]["platform"]["git_commit"],
        "trade_artifact_count": len(trade_files),
    }


def bracket_levels(
    entry_price: float, direction: int, tp_pips: float | None,
    sl_pips: float | None, price_units_per_pip: float | None,
) -> tuple[float | None, float | None]:
    """LEGACY: reconstruct bracket levels for a version-1 TradeLog only.

    Mirrors simulation/bracket.py open_trade() exactly:
        take_profit = price + direction * take_profit_pips * price_units_per_pip
        stop_loss   = price - direction * stop_loss_pips  * price_units_per_pip

    Version-1 artifacts predate recorded bracket levels and carry exactly one
    global ExecutionSpec, so this reproduces what their simulator did. It is
    never used for a version-2 artifact: there the levels are recorded, and once
    brackets can differ per trade a global spec cannot reconstruct them at all.
    This presents a decision that was already made. It is never a re-simulation,
    and there is no control anywhere that changes TP or SL.
    """
    if price_units_per_pip is None:
        return None, None
    tp = None if tp_pips is None else entry_price + direction * tp_pips * price_units_per_pip
    sl = None if sl_pips is None else entry_price - direction * sl_pips * price_units_per_pip
    return tp, sl


def _recorded(value: float) -> float | None:
    """Map a recorded level to JSON: NaN means this trade had no bracket there.

    NaN is not valid JSON. Emitting it would produce a body that json.loads
    accepts only in non-strict mode and that JSON.parse rejects outright, so the
    conversion happens here rather than being left to the serializer.
    """
    return None if math.isnan(value) else float(value)


@lru_cache(maxsize=8)
def trade_log(run_id: str) -> dict[str, Any]:
    """Read every fold's TradeLog for one run, in chronological order.

    A whole run is a small payload — about 1,000 trades and roughly 120 KiB of
    JSON — so the browser loads it once and Next/Previous become a local index
    change rather than a request per trade.
    """
    directory = run_dir(run_id)
    detail = run_detail(run_id)
    artifacts = directory / "artifacts"

    names = sorted(p.name for p in artifacts.glob("fold-*-trades.npz")
                   if TRADE_ARTIFACT.match(p.name))
    if not names:
        raise RunNotFoundError(f"run {run_id} has no TradeLog artifacts")

    trades: list[dict[str, Any]] = []
    execution_spec: dict[str, Any] | None = None
    format_version: int | None = None

    for name in names:
        match = TRADE_ARTIFACT.match(name)
        assert match is not None
        fold_index, policy_index = int(match.group(1)), int(match.group(2))
        with np.load(artifacts / name, allow_pickle=False) as payload:
            meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
            if meta.get("format") != TRADE_LOG_FORMAT:
                raise RunNotFoundError(f"unexpected artifact format in {name}")
            version = meta.get("format_version")
            if version not in SUPPORTED_TRADE_LOG_VERSIONS:
                raise RunNotFoundError(f"unsupported trade-log version in {name}")
            columns = {k: payload[k] for k in payload.files if k != "__meta__"}

        if execution_spec is None:
            execution_spec = meta["execution_spec"]
        if format_version is None:
            format_version = version
        spec = meta["execution_spec"]
        binding = spec["cost_binding"]
        tp_pips = float_param(spec.get("take_profit_pips"))
        sl_pips = float_param(spec.get("stop_loss_pips"))
        upp = float_param(binding.get("price_units_per_pip"))
        records_brackets = version >= RECORDED_BRACKETS_VERSION
        source = BRACKET_RECORDED if records_brackets else BRACKET_RECONSTRUCTED

        for i in range(len(columns["entry_key"])):
            entry_price = float(columns["entry_price"][i])
            direction = int(columns["direction"][i])
            if records_brackets:
                # Simulator truth. Never recomputed, and under per-trade brackets
                # never recomputable from the run's configuration in any case.
                tp = _recorded(columns["take_profit_price"][i])
                sl = _recorded(columns["stop_loss_price"][i])
            else:
                tp, sl = bracket_levels(entry_price, direction, tp_pips, sl_pips, upp)
            trades.append({
                "fold_index": fold_index,
                "policy_index": policy_index,
                "entry_key": int(columns["entry_key"][i]),
                "exit_key": int(columns["exit_key"][i]),
                "direction": direction,
                "entry_price": entry_price,
                "exit_price": float(columns["exit_price"][i]),
                "exit_reason": EXIT_REASONS.get(int(columns["exit_reason"][i]), "UNKNOWN"),
                "ambiguous_exit": bool(columns["ambiguous_exit"][i]),
                "gapped_exit": bool(columns["gapped_exit"][i]),
                "take_profit": tp,
                "stop_loss": sl,
                "take_profit_distance": None if tp is None else abs(tp - entry_price),
                "stop_loss_distance": None if sl is None else abs(entry_price - sl),
                "bracket_source": source,
                "gross_pnl": float(columns["gross_pnl"][i]),
                "spread_cost": float(columns["spread_cost"][i]),
                "commission_cost": float(columns["commission_cost"][i]),
                "net_pnl": float(columns["net_pnl"][i]),
            })

    trades.sort(key=lambda t: (t["entry_key"], t["fold_index"]))
    for position, trade in enumerate(trades):
        trade["index"] = position

    return {
        "run_id": run_id,
        "count": len(trades),
        "dataset": detail["dataset"],
        "execution": detail["execution"],
        "plugins": detail["plugins"],
        "trade_log_format_version": format_version,
        "bracket_mode": _bracket_mode(trades, execution_spec),
        "trades": trades,
    }


def _bracket_mode(trades: list[dict[str, Any]], spec: dict[str, Any] | None) -> str:
    """Say where a run's brackets came from, so the header cannot mislead.

    This is a question about provenance, not about whether the numbers happen to
    coincide. A run whose ExecutionSpec supplies no bracket on either side but
    whose trades carry one can only have got it from the intent, and that is
    true even when every trade was given the same distance — which is exactly
    the shape of a fixed-bracket policy. Keying on "do the distances differ"
    instead would have called such a run uniform and then printed no bracket at
    all, since there is no global pair to print.
    """
    if not trades:
        return "uniform"
    bracketed = [
        trade
        for trade in trades
        if trade["take_profit"] is not None or trade["stop_loss"] is not None
    ]
    if not bracketed:
        return "uniform"
    global_sides = (
        (None, None)
        if spec is None
        else (float_param(spec.get("take_profit_pips")), float_param(spec.get("stop_loss_pips")))
    )
    if all(side is None for side in global_sides):
        return "per_trade"
    distinct = {
        (trade["take_profit_distance"], trade["stop_loss_distance"]) for trade in trades
    }
    return "per_trade" if len(distinct) > 1 else "uniform"


def _fold_trade_counts(run_id: str) -> dict[int, int]:
    """Read each fold's recorded trade count from the run's own summary file."""
    directory = run_dir(run_id)
    path = directory / "metrics" / "simulation-summary.json"
    if not path.is_file():
        return {}
    document = _read_json(path)
    return {
        int(entry["fold_index"]): int(entry["num_trades"])
        for entry in document.get("results", [])
        if "fold_index" in entry and "num_trades" in entry
    }


def overview(run_id: str, fold_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe one run: recorded metrics plus a transcription of its TradeLog.

    Nothing here is a new research metric. The cumulative curve is a running total
    of the per-trade ``net_pnl`` the simulator already recorded, in the order the
    trades happened; the breakdowns are counts of recorded categorical columns
    (direction, exit reason, flags) and sums of recorded money. No ratio is
    derived that the platform did not itself record, and no significance or
    adjusted statistic exists anywhere in this payload (ADR-0024).

    The curve is a running total across folds, which is NOT a portfolio equity
    path: folds are independent evaluation windows and the model is refitted
    between them. It is labelled that way in the UI.
    """
    detail = run_detail(run_id)
    log = trade_log(run_id)
    trades = log["trades"]

    # Trust the run's own summary for fold ordering, not the index's row order.
    recorded_counts = _fold_trade_counts(run_id)
    folds = [dict(fold) for fold in fold_metrics]
    order_verified = bool(recorded_counts) and all(
        recorded_counts.get(fold["fold_index"]) == fold.get("trade_count")
        for fold in folds
    )

    cumulative = 0.0
    curve: list[dict[str, Any]] = []
    for trade in trades:
        cumulative += trade["net_pnl"]
        curve.append({
            "t": trade["entry_key"],
            "v": round(cumulative, 6),
            "i": trade["index"],
        })

    # First trade of each fold, so the timeline can mark where folds change.
    boundaries: list[dict[str, Any]] = []
    seen: set[int] = set()
    for trade in trades:
        if trade["fold_index"] not in seen:
            seen.add(trade["fold_index"])
            boundaries.append({
                "fold_index": trade["fold_index"],
                "t": trade["entry_key"],
                "i": trade["index"],
            })

    def money(rows: list[dict[str, Any]]) -> float:
        return round(sum(row["net_pnl"] for row in rows), 6)

    longs = [t for t in trades if t["direction"] > 0]
    shorts = [t for t in trades if t["direction"] < 0]

    exit_counts: dict[str, int] = {}
    exit_money: dict[str, float] = {}
    for trade in trades:
        reason = trade["exit_reason"]
        exit_counts[reason] = exit_counts.get(reason, 0) + 1
        exit_money[reason] = round(exit_money.get(reason, 0.0) + trade["net_pnl"], 6)

    return {
        "run": detail,
        "folds": folds,
        "fold_order_verified": order_verified,
        "trade_count": len(trades),
        "net_pnl_sum": money(trades),
        "first_entry_key": trades[0]["entry_key"] if trades else None,
        "last_exit_key": trades[-1]["exit_key"] if trades else None,
        "direction": {
            "long": {"count": len(longs), "net_pnl": money(longs)},
            "short": {"count": len(shorts), "net_pnl": money(shorts)},
        },
        "exits": {
            reason: {"count": count, "net_pnl": exit_money[reason]}
            for reason, count in sorted(exit_counts.items())
        },
        "flags": {
            "gapped_exit": sum(1 for t in trades if t["gapped_exit"]),
            "ambiguous_exit": sum(1 for t in trades if t["ambiguous_exit"]),
        },
        "curve": curve,
        "fold_boundaries": boundaries,
    }
