"""Isolated UI preview using made-up data; never reads a research data root.

This application illustrates the viewer. Its calculations are fixture creation,
not the research simulator, and cannot be used as research evidence.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parents[2] / "prototype"
START = 1704700800  # Encoded civil fields in a synthetic clock domain, not market time.
FAMILY = "synthetic-examples"
DOMAIN = "synthetic.demo.wallclock"
RUN_IDS = tuple(f"demo-example-{n:02d}" for n in range(1, 4))
GRIDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}


def _candles() -> list[dict[str, Any]]:
    candles = []
    previous = 100.0
    for i in range(3600):
        close = round(100 + 3 * math.sin(i / 63) + 1.4 * math.sin(i / 13)
                      + 0.35 * math.sin(i * 2.17) + i * 0.0005, 2)
        wick = round(0.12 + abs(math.sin(i * 1.37)) * 0.23, 2)
        candles.append({"t": START + i * 60, "o": previous, "c": close,
                        "h": round(max(previous, close) + wick, 2),
                        "l": round(min(previous, close) - wick, 2)})
        previous = close
    return candles


BARS = _candles()


def _trades(variant: int) -> list[dict[str, Any]]:
    result = []
    for i in range(12):
        at = 500 + i * 180
        direction = 1 if i % 2 == 0 else -1
        entry = BARS[at]["o"]
        target = round(entry + direction * (1.2 + variant * 0.8), 2)
        stop = round(entry - direction * 1.2, 2)
        end, reason = at + 55, "BOUNDARY_FORCED_CLOSE"
        price = BARS[end]["c"]
        for j in range(at, end + 1):
            bar = BARS[j]
            stopped = bar["l"] <= stop if direction > 0 else bar["h"] >= stop
            reached = bar["h"] >= target if direction > 0 else bar["l"] <= target
            if stopped or reached:
                end = j
                reason = "STOP_LOSS" if stopped else "TAKE_PROFIT"
                price = stop if stopped else target
                break
        gross = round((price - entry) * direction, 6)
        result.append({
            "synthetic": True, "index": i, "fold_index": i // 4, "policy_index": 0,
            "entry_key": BARS[at]["t"], "exit_key": BARS[end]["t"],
            "entry_price": entry, "exit_price": price, "direction": direction,
            "take_profit": target, "stop_loss": stop,
            "take_profit_distance": round(abs(target - entry), 2),
            "stop_loss_distance": round(abs(stop - entry), 2),
            "gross_pnl": gross, "spread_cost": 0.04, "commission_cost": 0.02,
            "net_pnl": round(gross - 0.06, 6), "exit_reason": reason,
            "gapped_exit": False, "ambiguous_exit": False,
            "bracket_source": "synthetic_demo", "lot_size": 1.0,
        })
    return result


TRADES = {run: _trades(i) for i, run in enumerate(RUN_IDS)}


def _check(run_id: str) -> None:
    if run_id not in TRADES:
        raise HTTPException(404, "Unknown synthetic example")


def _total(rows: list[dict[str, Any]]) -> float:
    return round(sum(t["net_pnl"] for t in rows), 6)


def _folds(run_id: str) -> list[dict[str, Any]]:
    folds = []
    for i in range(3):
        rows = TRADES[run_id][i * 4:(i + 1) * 4]
        equity = peak = drawdown = 0.0
        for row in rows:
            equity += row["net_pnl"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        folds.append({"fold_index": i, "trade_count": len(rows), "net_pnl": _total(rows),
                      "win_rate": sum(t["net_pnl"] > 0 for t in rows) / len(rows),
                      "max_drawdown": round(drawdown, 6)})
    return folds


app = FastAPI(title="Trade Explorer — synthetic demo",
              description="Made-up interface examples. No recorded research data.",
              docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "synthetic": True, "mode": "demo"}


@app.get("/api/runs/{run_id}")
def detail(run_id: str) -> dict[str, Any]:
    _check(run_id)
    return {
        "synthetic": True, "run_id": run_id, "state": "DEMO", "canonical": False,
        "experiment_fingerprint": "synthetic-example-not-a-research-fingerprint",
        "oos_touched": False, "lockbox_touched": False,
        "finished_unix_ns": (START + len(BARS) * 60) * 1_000_000_000,
        "dataset": {"dataset_id": "SYNTHETIC_DEMO", "role": "demo"},
        "plugins": {"features": ["Synthetic examples"], "model": "Illustrative model",
                    "policies": ["Illustrative policy"]},
        "execution": {"take_profit_pips": 120 + RUN_IDS.index(run_id) * 80,
                      "stop_loss_pips": 120, "lot_size": 1.0, "price_units_per_pip": 0.01,
                      "cost_semantics": "synthetic", "execution_price_model": "synthetic_demo"},
        "platform_git_commit": "demo", "trade_artifact_count": 0,
    }


@app.get("/api/runs")
def runs() -> dict[str, Any]:
    rows = []
    for run in RUN_IDS:
        item, folds = detail(run), _folds(run)
        rows.append({
            "synthetic": True, "run_id": run, "state": "DEMO", "dataset_role": "demo",
            "dataset_id": "SYNTHETIC_DEMO", "finished_unix_ns": item["finished_unix_ns"],
            "take_profit_pips": item["execution"]["take_profit_pips"], "stop_loss_pips": 120,
            "metrics": {"trades_sum": 12, "net_pnl_sum": _total(TRADES[run]), "fold_count": 3,
                        "win_rate_min": min(f["win_rate"] for f in folds),
                        "win_rate_max": max(f["win_rate"] for f in folds),
                        "max_drawdown_worst": max(f["max_drawdown"] for f in folds)},
        })
    return {"synthetic": True, "count": len(rows), "runs": rows}


@app.get("/api/experiments")
def experiments() -> dict[str, Any]:
    return {"synthetic": True, "group_count": 1, "run_count": len(RUN_IDS), "groups": [{
        "key": FAMILY, "dataset_id": "SYNTHETIC_DEMO", "feature_name": "Synthetic examples",
        "model_name": "Illustrative model", "policy_name": "Illustrative policy",
        "run_count": len(RUN_IDS), "runs": runs()["runs"],
    }]}


@app.get("/api/runs/{run_id}/trades")
def trades(run_id: str) -> dict[str, Any]:
    _check(run_id)
    return {"synthetic": True, "run_id": run_id, "count": len(TRADES[run_id]),
            "bracket_mode": "uniform", "trade_log_format_version": None,
            "trades": TRADES[run_id]}


@app.get("/api/runs/{run_id}/signals")
def signals(run_id: str) -> dict[str, Any]:
    _check(run_id)
    return {"synthetic": True, "available": False, "contexts": {},
            "reason": "this synthetic preview has no recorded research signals"}


@app.get("/api/runs/{run_id}/overview")
def overview(run_id: str) -> dict[str, Any]:
    _check(run_id)
    rows = TRADES[run_id]
    cumulative = 0.0
    curve = []
    for row in rows:
        cumulative += row["net_pnl"]
        curve.append({"t": row["entry_key"], "v": round(cumulative, 6), "i": row["index"]})
    def summary(selected: list[dict[str, Any]]) -> dict[str, Any]:
        return {"count": len(selected), "net_pnl": _total(selected)}
    return {
        "synthetic": True, "run": detail(run_id), "folds": _folds(run_id),
        "fold_order_verified": False, "trade_count": len(rows), "net_pnl_sum": _total(rows),
        "first_entry_key": rows[0]["entry_key"], "last_exit_key": rows[-1]["exit_key"],
        "direction": {"long": summary([t for t in rows if t["direction"] > 0]),
                      "short": summary([t for t in rows if t["direction"] < 0])},
        "exits": {reason: summary([t for t in rows if t["exit_reason"] == reason])
                  for reason in sorted({t["exit_reason"] for t in rows})},
        "flags": {"gapped_exit": 0, "ambiguous_exit": 0}, "curve": curve,
        "fold_boundaries": [{"fold_index": t["fold_index"], "t": t["entry_key"], "i": i}
                            for i, t in enumerate(rows) if i % 4 == 0],
    }


@app.get("/api/runs/{run_id}/bars")
def bars(run_id: str, entry_key: int, exit_key: int, timeframe: str = "M1",
         before: int = Query(600, ge=0, le=3000),
         after: int = Query(400, ge=0, le=3000)) -> dict[str, Any]:
    _check(run_id)
    if timeframe not in GRIDS:
        raise HTTPException(400, "Unknown timeframe")
    if not BARS[0]["t"] <= entry_key <= exit_key <= BARS[-1]["t"]:
        raise HTTPException(400, "Requested window is outside the synthetic example")
    grid = GRIDS[timeframe]
    entry, exit_ = entry_key // grid * grid, exit_key // grid * grid
    buckets: dict[int, dict[str, Any]] = {}
    for bar in BARS:
        key = bar["t"] // grid * grid
        if not entry - before * grid <= key <= exit_ + after * grid:
            continue
        if key not in buckets:
            buckets[key] = {**bar, "t": key}
        else:
            buckets[key].update(h=max(buckets[key]["h"], bar["h"]),
                                l=min(buckets[key]["l"], bar["l"]), c=bar["c"])
    selected = list(buckets.values())
    return {"synthetic": True, "clock_domain": DOMAIN, "unit": "seconds",
            "timeframe": timeframe, "grid_seconds": grid, "count": len(selected),
            "first_key": selected[0]["t"], "last_key": selected[-1]["t"],
            "entry_bar_key": entry, "exit_bar_key": exit_, "bars": selected}


def _page(name: str) -> HTMLResponse:
    text = (STATIC / name).read_text()
    banner = (
        '<div role="note" id="demo-banner" style="flex:0 0 auto;padding:10px 16px;'
        'background:#2b2417;color:#f8ce80;font:13px system-ui;text-align:center">'
        '<strong>Synthetic demo</strong> · Made-up prices and trades. '
        'Illustrates the interface only.</div>'
    )
    text = text.replace('</head>', '<script>window.SYNTHETIC_DEMO = true;</script></head>')
    text = re.sub(r'(<body[^>]*>)', lambda m: m[1] + banner, text, count=1)
    text = text.replace('recorded metrics', 'illustrative metrics')
    text = text.replace('recorded trade fields', 'synthetic trade fields')
    return HTMLResponse(text)


@app.get("/", response_class=HTMLResponse)
def home_page() -> HTMLResponse:
    return _page("index.html")


@app.get("/family", response_class=HTMLResponse)
def family_page() -> HTMLResponse:
    return _page("family.html")


@app.get("/run", response_class=HTMLResponse)
def run_page() -> HTMLResponse:
    return _page("run.html")


@app.get("/chart", response_class=HTMLResponse)
def chart_page() -> HTMLResponse:
    return _page("chart.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
