"""Read-only access to the disposable Phase-5 run index.

The index is a listing accelerator and nothing more (ADR-0017, INV-IDX-1). It is
opened read-only, its schema version is checked, and no value taken from it is
ever presented as a research fact — run detail, trades and bracket levels are
always read from files.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from explorer.config import INDEX_PATH, INDEX_SCHEMA_VERSION

_SUPERSEDED_SWEEP_ID = (
    "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-v1"
)


class IndexUnavailableError(RuntimeError):
    """The index is missing, unreadable, or a schema this reader does not know."""


def _connect() -> sqlite3.Connection:
    if not INDEX_PATH.is_file():
        raise IndexUnavailableError(
            f"run index not found at {INDEX_PATH}; "
            "build it with: trading-research index rebuild"
        )
    connection = sqlite3.connect(f"file:{INDEX_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    version = connection.execute(
        "SELECT value FROM index_meta WHERE key = 'schema_version'"
    ).fetchone()
    if version is None or version["value"] != INDEX_SCHEMA_VERSION:
        connection.close()
        found = None if version is None else version["value"]
        raise IndexUnavailableError(
            f"run index schema {found!r} is not the expected {INDEX_SCHEMA_VERSION!r}"
        )
    return connection


def index_meta() -> dict[str, Any]:
    """Return index build metadata, including how stale it may be."""
    with _connect() as connection:
        meta = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM index_meta")
        }
        runs = connection.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"]
    return {
        "schema_version": meta.get("schema_version"),
        "generation": meta.get("generation"),
        "built_unix_ns": meta.get("built_unix_ns"),
        "indexed_run_count": runs,
    }


def list_runs(limit: int = 500) -> list[dict[str, Any]]:
    """List completed runs that actually produced simulated trades.

    A run is only useful to this viewer if it ran Plane B, so the listing is
    restricted to runs with at least one recorded policy execution. Whether the
    TradeLog files are really present is confirmed from the run manifest when the
    run is opened; the index only narrows the candidates.
    """
    query = """
        SELECT run_id, state, canonical, outcome_class,
               evaluation_count, policy_execution_count,
               dataset_id, dataset_role, experiment_fingerprint,
               finished_unix_ns, cost_semantics_unverified,
               json_extract(resolved_config_json, '$.model.name')       AS model_name,
               json_extract(resolved_config_json, '$.features[0].name') AS feature_name,
               json_extract(resolved_config_json, '$.policies[0].name') AS policy_name,
               json_extract(resolved_config_json, '$.execution.take_profit_pips') AS tp_raw,
               json_extract(resolved_config_json, '$.execution.stop_loss_pips')   AS sl_raw
          FROM run
         WHERE state = 'COMPLETE'
           AND policy_execution_count > 0
           AND lockbox_touched = 0
           AND oos_touched = 0
           AND NOT EXISTS (
               SELECT 1
                 FROM sweep_member excluded
                WHERE excluded.experiment_fingerprint = run.experiment_fingerprint
                  AND excluded.sweep_id = ?
           )
         ORDER BY finished_unix_ns DESC
         LIMIT ?
    """
    with _connect() as connection:
        rows = connection.execute(query, (_SUPERSEDED_SWEEP_ID, limit)).fetchall()

    runs: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["canonical"] = bool(record["canonical"])
        record["cost_semantics_unverified"] = bool(record["cost_semantics_unverified"])
        for source, target in (("tp_raw", "take_profit_pips"), ("sl_raw", "stop_loss_pips")):
            record[target] = _float_param(record.pop(source))
        runs.append(record)
    return runs


def _float_param(raw: object) -> float | None:
    """Decode the platform's exact float-hex parameter encoding, if present."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        document = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    if isinstance(document, dict) and "__float_hex__" in document:
        return float.fromhex(document["__float_hex__"])
    if isinstance(document, (int, float)):
        return float(document)
    return None


# Metrics the Research Machine records per fold, and how a run-level figure may
# honestly be formed from them. This distinction is load-bearing, not cosmetic:
# the platform records NOTHING at run level (simulation-summary.json is per-fold
# too), so any run-level number here is an aggregation the viewer performs.
#
#   ADDITIVE   counts and money over disjoint evaluation folds. Summing them is
#              arithmetic on recorded values, not a new statistic.
#   NON-ADDITIVE  ratios and drawdown. A mean across folds with unequal trade
#              counts would be a fabricated statistic, so these are reported as
#              an observed range / worst fold and never collapsed into one
#              invented number (ADR-0024: never manufacture a statistic).
_ADDITIVE = ("trade_count", "net_pnl", "gross_pnl", "total_cost")

_SUMMARY_SQL = """
    SELECT m.run_id                                                          AS run_id,
           SUM(CASE WHEN m.metric_name='trade_count' THEN m.value_real END)  AS trades_sum,
           SUM(CASE WHEN m.metric_name='net_pnl'     THEN m.value_real END)  AS net_pnl_sum,
           SUM(CASE WHEN m.metric_name='gross_pnl'   THEN m.value_real END)  AS gross_pnl_sum,
           SUM(CASE WHEN m.metric_name='total_cost'  THEN m.value_real END)  AS total_cost_sum,
           MIN(CASE WHEN m.metric_name='win_rate'    THEN m.value_real END)  AS win_rate_min,
           MAX(CASE WHEN m.metric_name='win_rate'    THEN m.value_real END)  AS win_rate_max,
           MAX(CASE WHEN m.metric_name='max_drawdown' THEN m.value_real END) AS max_drawdown_worst,
           COUNT(CASE WHEN m.metric_name='trade_count' THEN 1 END)           AS fold_count
      FROM metric m
      JOIN evaluation e
        ON e.run_id = m.run_id AND e.entry_ordinal = m.entry_ordinal
     WHERE e.plane = 'B'
     GROUP BY m.run_id
"""

_SWEEP_SQL = """
    SELECT r.run_id AS run_id, sm.sweep_id AS sweep_id
      FROM run r
      JOIN sweep_member sm ON sm.experiment_fingerprint = r.experiment_fingerprint
"""


def run_summaries(limit: int = 500) -> list[dict[str, Any]]:
    """Return listed runs enriched with their recorded Plane-B fold metrics.

    Every number here is either a value the Research Machine recorded, or a
    labelled aggregation of such values. Nothing is recomputed from trades.
    """
    runs = list_runs(limit=limit)
    by_id = {run["run_id"]: run for run in runs}
    if not by_id:
        return []

    with _connect() as connection:
        for row in connection.execute(_SUMMARY_SQL):
            run = by_id.get(row["run_id"])
            if run is None:
                continue
            summary = dict(row)
            summary.pop("run_id")
            trades = summary.get("trades_sum")
            summary["trades_sum"] = None if trades is None else int(trades)
            run["metrics"] = summary
        for row in connection.execute(_SWEEP_SQL):
            run = by_id.get(row["run_id"])
            if run is not None:
                run.setdefault("sweeps", []).append(row["sweep_id"])

    for run in runs:
        run.setdefault("metrics", {})
        run.setdefault("sweeps", [])
    return runs


def group_by_plugin_family(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group runs by dataset and the research identity the repository records.

    Dataset identity remains separate because one plugin family may be evaluated
    on independent instruments and clock domains.
    """
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for run in runs:
        key = (
            run.get("dataset_id") or "—",
            run.get("feature_name") or "—",
            run.get("model_name") or "—",
            run.get("policy_name") or "—",
        )
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": "|".join(key),
                "dataset_id": key[0],
                "feature_name": key[1],
                "model_name": key[2],
                "policy_name": key[3],
                "runs": [],
            }
        group["runs"].append(run)

    ordered = sorted(groups.values(), key=lambda g: -len(g["runs"]))
    for group in ordered:
        group["run_count"] = len(group["runs"])
    return ordered


_FOLD_METRICS_SQL = """
    SELECT e.entry_ordinal                                                    AS ordinal,
           MAX(CASE WHEN m.metric_name='trade_count'  THEN m.value_real END)  AS trade_count,
           MAX(CASE WHEN m.metric_name='net_pnl'      THEN m.value_real END)  AS net_pnl,
           MAX(CASE WHEN m.metric_name='gross_pnl'    THEN m.value_real END)  AS gross_pnl,
           MAX(CASE WHEN m.metric_name='total_cost'   THEN m.value_real END)  AS total_cost,
           MAX(CASE WHEN m.metric_name='win_rate'     THEN m.value_real END)  AS win_rate,
           MAX(CASE WHEN m.metric_name='max_drawdown' THEN m.value_real END)  AS max_drawdown
      FROM evaluation e
      LEFT JOIN metric m
        ON m.run_id = e.run_id AND m.entry_ordinal = e.entry_ordinal
     WHERE e.run_id = ? AND e.plane = 'B'
     GROUP BY e.entry_ordinal
     ORDER BY e.entry_ordinal
"""


def fold_metrics(run_id: str) -> list[dict[str, Any]]:
    """Return the Plane-B metrics the platform recorded for each evaluation fold.

    Every value here is transcribed, never derived: the platform records
    trade_count, net_pnl, win_rate and max_drawdown per fold, and those are what
    is returned. Folds are emitted in evaluation order; the caller cross-checks
    that order against the run's own simulation summary before trusting it.

    Two evaluators contribute to one fold (trade_summary_v1 and
    equity_drawdown_v1), so consecutive rows are merged into a single fold record.
    """
    with _connect() as connection:
        rows = [dict(row) for row in connection.execute(_FOLD_METRICS_SQL, (run_id,))]

    folds: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        row.pop("ordinal")
        if row.get("trade_count") is not None:
            current = {key: value for key, value in row.items() if value is not None}
            folds.append(current)
        elif current is not None:
            for key, value in row.items():
                if value is not None:
                    current[key] = value
    for position, fold in enumerate(folds):
        fold["fold_index"] = position
        if fold.get("trade_count") is not None:
            fold["trade_count"] = int(fold["trade_count"])
    return folds
