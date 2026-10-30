"""Generate the E1 static chart fixture from real TRAIN data. Read-only.

This script imports the Research Machine package and uses its documented gated
data path. It never writes anywhere except into this project's ``fixture/``
directory, and it refuses any dataset whose access classification is not NORMAL
(discovery-default). OOS, LOCKBOX, SNAPSHOT and the raw/curated parents are out
of scope for E1 and are rejected rather than requested.
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import socket
import sys
from pathlib import Path
from uuid import uuid4

import numpy as np
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.core.time import NativeTimeKey
from trading_research.data import (
    AccessClassification,
    AccessContext,
    AccessRequest,
    DatasetRegistry,
    JsonlAuditWriter,
    MarketFrameLoader,
    authorize_access,
    classify_access,
    open_bounded_view,
    read_split_policy,
)

from explorer.config import AUDIT_PATH, DATA_ROOT, RUNS_ROOT, SPLIT_POLICY, VIEWS_ROOT

PROJECT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = PROJECT / "fixture" / "explorer-fixture.json"

# ---------------------------------------------------------------- defaults
DEFAULT_ARTIFACT = "fold-0000-policy-0000-trades.npz"
DEFAULT_TRADE = 0
TARGET_BARS = 3000
# Weight the window so there is real history before entry and real outcome after
# exit; the researcher inspects both sides.
BARS_BEFORE = 1800
SECONDS_PER_M1 = 60

EXIT_REASONS = {1: "TAKE_PROFIT", 2: "STOP_LOSS", 3: "TARGET_CHANGE", 4: "BOUNDARY_FORCED_CLOSE"}


def _hexfloat(value: object) -> float:
    """Decode the platform's exact float-hex parameter encoding."""
    if isinstance(value, dict) and "__float_hex__" in value:
        return float.fromhex(value["__float_hex__"])
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"not a float parameter: {value!r}")


def load_trade_log(run_id: str, artifact: str) -> tuple[dict, dict]:
    """Read one TradeLog artifact and its metadata header, validating the format."""
    path = RUNS_ROOT / "runs" / run_id / "artifacts" / artifact
    if not path.is_file():
        raise SystemExit(f"TradeLog artifact not found: {path}")
    with np.load(path, allow_pickle=False) as payload:
        meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        if meta.get("format") != "trading-research-trade-log":
            raise SystemExit(f"unexpected artifact format: {meta.get('format')!r}")
        if meta.get("format_version") not in (1, 2):
            raise SystemExit(
                f"unsupported trade-log format_version: {meta.get('format_version')!r}"
            )
        columns = {name: payload[name].copy() for name in payload.files if name != "__meta__"}
    return columns, meta


def bracket_levels(
    entry_price: float, direction: int, spec: dict
) -> tuple[float | None, float | None]:
    """LEGACY: reconstruct bracket levels for a version-1 trade log only.

    Mirrors ``simulation/bracket.py`` ``open_trade`` exactly:
        take_profit = price + direction * take_profit_pips * price_units_per_pip
        stop_loss   = price - direction * stop_loss_pips  * price_units_per_pip
    Version-2 logs record the levels the simulator actually used, so this runs
    only for the older container. It is cross-checked against recorded exit
    prices in ``verify_brackets``.
    """
    binding = spec["cost_binding"]
    upp = _hexfloat(binding["price_units_per_pip"])
    tp_pips = spec.get("take_profit_pips")
    sl_pips = spec.get("stop_loss_pips")
    tp = None if tp_pips is None else entry_price + direction * _hexfloat(tp_pips) * upp
    sl = None if sl_pips is None else entry_price - direction * _hexfloat(sl_pips) * upp
    return tp, sl


def trade_brackets(
    columns: dict, spec: dict, index: int, version: int
) -> tuple[float | None, float | None]:
    """Return one trade's levels: recorded when present, reconstructed otherwise."""
    if version >= 2:
        tp = float(columns["take_profit_price"][index])
        sl = float(columns["stop_loss_price"][index])
        return (None if math.isnan(tp) else tp), (None if math.isnan(sl) else sl)
    return bracket_levels(
        float(columns["entry_price"][index]), int(columns["direction"][index]), spec
    )


def verify_brackets(columns: dict, spec: dict, version: int = 1) -> dict:
    """Assert the levels shown reproduce recorded bracket exits bit-exactly.

    For a version-1 log this checks a reconstruction. For version 2 it checks the
    simulator's own recording, which is the stronger statement: the level a trade
    says it watched must be the price it actually exited at.
    """
    checked = matched = 0
    for i in range(len(columns["entry_key"])):
        reason = int(columns["exit_reason"][i])
        if reason not in (1, 2) or bool(columns["gapped_exit"][i]):
            continue
        tp, sl = trade_brackets(columns, spec, i, version)
        level = tp if reason == 1 else sl
        if level is None:
            continue
        checked += 1
        if float(columns["exit_price"][i]) == level:
            matched += 1
    return {"non_gapped_bracket_exits": checked, "exact_matches": matched}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT)
    parser.add_argument("--trade", type=int, default=DEFAULT_TRADE)
    parser.add_argument("--bars", type=int, default=TARGET_BARS)
    args = parser.parse_args()

    # ---- run identity (administrative metadata only) -----------------------
    run_dir = RUNS_ROOT / "runs" / args.run
    state = json.loads((run_dir / "state.json").read_text())
    identity = json.loads((run_dir / "identity.json").read_text())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    resolved = identity["fingerprint_inputs"]["resolved_config"]

    if state["state"] != "COMPLETE":
        raise SystemExit(f"run is not COMPLETE: {state['state']}")
    if not manifest.get("canonical"):
        raise SystemExit("run is not canonical")
    if state["oos_touched"] or state["lockbox_touched"]:
        raise SystemExit("run touched protected data; out of scope for E1")

    dataset = resolved["dataset"]
    if dataset["role"] != "train":
        raise SystemExit(f"E1 is TRAIN-only; run uses role {dataset['role']!r}")

    print(f"run          : {args.run}  ({state['state']}, canonical)")
    print(f"dataset      : {dataset['dataset_id']}  role={dataset['role']}")

    # ---- trade log ---------------------------------------------------------
    columns, meta = load_trade_log(args.run, args.artifact)
    n_trades = len(columns["entry_key"])
    if not 0 <= args.trade < n_trades:
        raise SystemExit(f"trade index {args.trade} out of range (0..{n_trades - 1})")
    spec = meta["execution_spec"]

    version = int(meta["format_version"])
    check = verify_brackets(columns, spec, version)
    print(f"trades       : {n_trades} in {args.artifact}")
    print(
        f"bracket check: {check['exact_matches']}/{check['non_gapped_bracket_exits']} "
        "non-gapped bracket exits reproduce exactly"
    )
    if check["exact_matches"] != check["non_gapped_bracket_exits"]:
        raise SystemExit("bracket derivation does not match the simulator; refusing to emit")

    i = args.trade
    entry_key = int(columns["entry_key"][i])
    exit_key = int(columns["exit_key"][i])
    direction = int(columns["direction"][i])
    entry_price = float(columns["entry_price"][i])
    exit_price = float(columns["exit_price"][i])
    reason_code = int(columns["exit_reason"][i])
    tp, sl = trade_brackets(columns, spec, i, version)

    trade = {
        "index": i,
        "fold_index": meta["fold_index"],
        "entry_key": entry_key,
        "exit_key": exit_key,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": EXIT_REASONS[reason_code],
        "ambiguous_exit": bool(columns["ambiguous_exit"][i]),
        "gapped_exit": bool(columns["gapped_exit"][i]),
        "take_profit": tp,
        "stop_loss": sl,
        "bracket_source": "recorded" if version >= 2 else "reconstructed_v1",
        "gross_pnl": float(columns["gross_pnl"][i]),
        "spread_cost": float(columns["spread_cost"][i]),
        "commission_cost": float(columns["commission_cost"][i]),
        "net_pnl": float(columns["net_pnl"][i]),
    }
    print(f"trade[{i}]     : entry {entry_price} @ {entry_key}  dir {direction:+d}")
    print(f"               exit  {exit_price} @ {exit_key}  {trade['exit_reason']}")
    print(f"               TP {tp}  SL {sl}  net_pnl {trade['net_pnl']}")

    # ---- gated market access ----------------------------------------------
    registry = DatasetRegistry.discover([DATA_ROOT, VIEWS_ROOT])
    policy = read_split_policy(SPLIT_POLICY)
    ref = DatasetRef(
        dataset_id=dataset["dataset_id"],
        manifest_sha256=dataset["manifest_sha256"],
        role=ViewRole(dataset["role"]),
    )
    registered = registry.resolve(ref)

    classification = classify_access(registered.manifest, policy)
    print(f"classification: {classification.name}")
    if classification is not AccessClassification.NORMAL:
        raise SystemExit(
            f"refusing non-NORMAL access ({classification.name}); E1 is discovery-default only"
        )

    audit_before = AUDIT_PATH.read_bytes() if AUDIT_PATH.exists() else b""

    loader = MarketFrameLoader(registry=registry, split_policy=policy)
    grant = authorize_access(
        registry=registry,
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
        raise SystemExit("unexpected audit event for NORMAL access; refusing to emit")

    unit = policy.time_unit
    domain = policy.clock_domain
    span = max(args.bars, 1) * SECONDS_PER_M1 * 4  # generous: gaps mean rows < minutes
    lo = entry_key - span
    hi = exit_key + span
    bounded = open_bounded_view(
        loader=loader,
        grants=(grant,),
        context_start=NativeTimeKey(lo, unit, domain),
        eval_start=NativeTimeKey(lo, unit, domain),
        eval_end=NativeTimeKey(hi, unit, domain),
        columns=("open", "high", "low", "close", "tick_volume"),
    )
    frame = bounded.eval_frame

    if AUDIT_PATH.exists() and AUDIT_PATH.read_bytes() != audit_before:
        raise SystemExit("audit log changed during NORMAL access; refusing to emit")

    keys = frame.keys
    entry_idx = int(np.searchsorted(keys, entry_key))
    if entry_idx >= keys.size or int(keys[entry_idx]) != entry_key:
        raise SystemExit(f"entry key {entry_key} is not an observed bar in this view")

    start = max(0, entry_idx - BARS_BEFORE)
    stop = min(keys.size, start + args.bars)
    start = max(0, stop - args.bars)

    ks = keys[start:stop]
    o = frame.column("open")[start:stop]
    h = frame.column("high")[start:stop]
    lo_c = frame.column("low")[start:stop]
    c = frame.column("close")[start:stop]
    v = frame.column("tick_volume")[start:stop]

    deltas = np.diff(ks)
    max_gap = int(deltas.max()) if deltas.size else 0
    gaps = [
        {"after_key": int(ks[j]), "gap_seconds": int(deltas[j])}
        for j in np.argsort(deltas)[::-1][:5]
    ] if deltas.size else []

    bars = [
        {
            "t": int(ks[j]),
            "o": float(o[j]),
            "h": float(h[j]),
            "l": float(lo_c[j]),
            "c": float(c[j]),
            "v": float(v[j]),
        }
        for j in range(ks.size)
    ]

    fixture = {
        "fixture_version": 1,
        "generated_by": "trading-research-explorer/tools/make_fixture.py",
        "provenance": {
            "run_id": args.run,
            "experiment_fingerprint": identity["experiment_fingerprint"],
            "artifact": args.artifact,
            "trade_index": args.trade,
            "run_state": state["state"],
            "canonical": bool(manifest["canonical"]),
            "platform_git_commit": identity["fingerprint_inputs"]["platform"]["git_commit"],
            "features": [f["name"] for f in resolved.get("features", [])],
            "model": (resolved.get("model") or {}).get("name"),
            "policies": [p["name"] for p in resolved.get("policies", [])],
        },
        "dataset": {
            "dataset_id": frame.dataset_ref.dataset_id,
            "manifest_sha256": frame.dataset_ref.manifest_sha256,
            "role": frame.dataset_ref.role,
            "access_classification": classification.name,
        },
        "time": {
            "clock_domain": frame.clock_domain.identifier,
            "unit": frame.unit.value,
            "calendar_time_policy": "native-wallclock-v1",
            "note": (
                "Keys are native broker wall-clock seconds. They are NOT UTC. "
                "Render civil time by mechanical decode using UTC-based getters "
                "only, so the encoded wall-clock fields are preserved literally. "
                "Never apply a local timezone offset."
            ),
        },
        "execution": {
            "execution_id": spec["execution_id"],
            "version": spec["version"],
            "lot_size": _hexfloat(spec["lot_size"]),
            "take_profit_pips": (
                None if spec.get("take_profit_pips") is None
                else _hexfloat(spec["take_profit_pips"])
            ),
            "stop_loss_pips": (
                None if spec.get("stop_loss_pips") is None
                else _hexfloat(spec["stop_loss_pips"])
            ),
            "price_units_per_pip": _hexfloat(spec["cost_binding"]["price_units_per_pip"]),
            "cost_semantics": spec["cost_binding"]["cost_semantics"],
            "execution_price_model": spec["cost_binding"]["execution_price_model"],
            "binding_id": spec["cost_binding"]["binding_id"],
        },
        "bracket_verification": check,
        "policy_origin": meta["policy_origin"],
        "intent_schema_id": meta["intent_schema_id"],
        "trade": trade,
        "bars": {
            "timeframe": "M1",
            "count": len(bars),
            "first_key": int(ks[0]),
            "last_key": int(ks[-1]),
            "max_gap_seconds": max_gap,
            "largest_gaps": gaps,
            "data": bars,
        },
    }

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(fixture, separators=(",", ":")) + "\n")

    print(f"bars         : {len(bars)}  keys {int(ks[0])}..{int(ks[-1])}")
    print(f"max gap      : {max_gap} s ({max_gap / 3600:.1f} h)")
    print(f"written      : {FIXTURE_PATH}  ({FIXTURE_PATH.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
