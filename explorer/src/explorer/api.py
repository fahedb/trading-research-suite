"""Read-only HTTP API for the Trade Explorer.

Every route reads. Nothing here writes to a research root, creates a run, calls
the orchestrator, or performs a reconciling run scan — the reconciling path takes
exclusive run-directory locks and can terminalise a live RUNNING run, so it is
never invoked (ADR-0017 Amendment 1 clause 3).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from explorer import bars as bars_module
from explorer import index_read
from explorer import signals as signals_module
from explorer import trades as trades_module
from explorer.config import STATIC_DIR

app = FastAPI(
    title="Trade Explorer",
    description="Read-only viewer for simulated trades produced by the Research Machine.",
    version="0.2.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


# The whole-run trade list is the largest payload the phone fetches; gzip takes
# it from roughly 350 KiB to well under 100 KiB over the tailnet.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Report index availability so the UI can say something honest when it is missing."""
    try:
        return {"ok": True, "index": index_read.index_meta()}
    except index_read.IndexUnavailableError as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/experiments")
def list_experiments(limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    """Runs grouped by the plugin identity their own config declares.

    Fold metrics come straight from the index; see index_read for which of them
    may honestly be summed and which are reported as a range instead.
    """
    try:
        runs = index_read.run_summaries(limit=limit)
    except index_read.IndexUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    groups = index_read.group_by_plugin_family(runs)
    return {
        "run_count": len(runs),
        "group_count": len(groups),
        "aggregation": {
            "additive": "summed over the run's evaluation folds",
            "win_rate": "observed range across folds; never averaged",
            "max_drawdown": "worst single fold; never combined",
        },
        "groups": groups,
    }


@app.get("/api/runs")
def list_runs(limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    """List completed runs that produced simulated trades, newest first."""
    try:
        runs = index_read.list_runs(limit=limit)
    except index_read.IndexUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"count": len(runs), "runs": runs}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Return one run's identity and configuration, read from its own files."""
    try:
        return trades_module.run_detail(run_id)
    except trades_module.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/trades")
def get_trades(run_id: str) -> dict[str, Any]:
    """Return every trade for one run, chronologically, with its bracket levels.

    A version-2 TradeLog carries the exact levels the simulator used and they are
    read straight through. A version-1 TradeLog predates recorded levels, so its
    brackets are reconstructed from the run's single global ExecutionSpec and
    every such trade says so in ``bracket_source``.
    """
    try:
        return trades_module.trade_log(run_id)
    except trades_module.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/overview")
def get_overview(run_id: str) -> dict[str, Any]:
    """Summarise one run: recorded fold metrics plus a TradeLog transcription."""
    try:
        folds = index_read.fold_metrics(run_id)
    except index_read.IndexUnavailableError:
        folds = []          # the overview still works from files alone
    try:
        return trades_module.overview(run_id, folds)
    except trades_module.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/signals")
def get_signals(run_id: str) -> dict[str, Any]:
    """Return the recorded decision-time context for each trade in one run.

    Every value is transcribed from feature columns the provider declared and the
    run stored. Context the provider computed but never declared is reported under
    ``unrecorded`` rather than reconstructed.
    """
    try:
        return signals_module.signal_context(run_id)
    except trades_module.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/bars")
def get_bars(
    run_id: str,
    entry_key: int = Query(..., description="native broker wall-clock key of the trade entry"),
    exit_key: int = Query(..., description="native broker wall-clock key of the trade exit"),
    timeframe: str = Query("M1", description="M1, M5, M15 or H1"),
    before: int = Query(600, ge=0, le=3000),
    after: int = Query(400, ge=0, le=3000),
) -> dict[str, Any]:
    """Return a bounded candle window around one trade, from gated market data.

    M1 is the recorded data. Coarser timeframes are aggregated server-side on the
    broker's own native key grid; the browser never performs aggregation and is
    never the authority on bucket semantics.
    """
    try:
        detail = trades_module.run_detail(run_id)
        return bars_module.window(
            detail["dataset"],
            entry_key=entry_key,
            exit_key=exit_key,
            timeframe=timeframe,
            before=before,
            after=after,
        )
    except trades_module.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except bars_module.UnknownTimeframe as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except bars_module.DataAccessRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/")
def experiments_page() -> FileResponse:
    """The default page is the experiment browser."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/family")
def family_page() -> FileResponse:
    """The experiments table for one plugin family, opened from the Plugins page."""
    return FileResponse(STATIC_DIR / "family.html")


@app.get("/run")
def run_page() -> FileResponse:
    """The run overview, opened from the experiments table."""
    return FileResponse(STATIC_DIR / "run.html")


@app.get("/chart")
def chart_page() -> FileResponse:
    """The chart viewer for one run, opened from the experiments table."""
    return FileResponse(STATIC_DIR / "chart.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
