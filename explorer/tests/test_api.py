"""End-to-end checks on the read-only API against the real runs root.

These run against real research data on this machine. They are skipped rather
than failed if the runs root or index is absent, so the suite stays meaningful
on a machine that has the code but not the data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from explorer import index_read
from explorer.api import app
from explorer.config import INDEX_PATH, RUNS_ROOT


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not RUNS_ROOT.is_dir() or not INDEX_PATH.is_file():
        pytest.skip("runs root or run index not present on this machine")
    return TestClient(app)


@pytest.fixture(scope="module")
def run_id(client: TestClient) -> str:
    groups = client.get("/api/experiments?limit=2000").json()["groups"]
    run = next(
        (
            run
            for group in groups
            for run in group["runs"]
            if (run["metrics"].get("trades_sum") or 0) > 0
        ),
        None,
    )
    if run is None:
        pytest.skip("no completed run with recorded trades")
    return run["run_id"]


def test_health_reports_index(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["index"]["schema_version"] == index_read.INDEX_SCHEMA_VERSION


def test_run_listing_is_complete_train_only(client: TestClient) -> None:
    """The viewer must never offer a protected or unfinished run."""
    runs = client.get("/api/runs").json()["runs"]
    assert runs
    for run in runs:
        assert run["state"] == "COMPLETE"
        assert run["policy_execution_count"] > 0
        assert run["dataset_role"] in {"train", "validation"}


def test_run_detail_comes_from_files(client: TestClient, run_id: str) -> None:
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run_id"] == run_id
    assert detail["state"] == "COMPLETE"
    assert detail["oos_touched"] is False
    assert detail["lockbox_touched"] is False
    assert detail["trade_artifact_count"] > 0


def test_trades_are_chronological_and_indexed(client: TestClient, run_id: str) -> None:
    log = client.get(f"/api/runs/{run_id}/trades").json()
    assert log["count"] == len(log["trades"]) > 0
    keys = [t["entry_key"] for t in log["trades"]]
    assert keys == sorted(keys)
    assert [t["index"] for t in log["trades"]] == list(range(log["count"]))


def test_bracket_levels_match_recorded_exits(client: TestClient, run_id: str) -> None:
    """Derived TP/SL must reproduce the simulator's own recorded bracket exits."""
    log = client.get(f"/api/runs/{run_id}/trades").json()
    checked = 0
    for trade in log["trades"]:
        if trade["gapped_exit"]:
            continue
        if trade["exit_reason"] == "TAKE_PROFIT" and trade["take_profit"] is not None:
            assert trade["exit_price"] == trade["take_profit"]
            checked += 1
        elif trade["exit_reason"] == "STOP_LOSS" and trade["stop_loss"] is not None:
            assert trade["exit_price"] == trade["stop_loss"]
            checked += 1
    assert checked > 0, "expected at least one bracket exit to verify"


def test_money_is_labelled_unverified_while_the_cost_audit_is_open(
    client: TestClient, run_id: str
) -> None:
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["execution"]["cost_semantics"] == "unverified"
    assert detail["execution"]["execution_price_model"] == "recorded_path_unverified"


def test_bars_window_brackets_the_trade(client: TestClient, run_id: str) -> None:
    log = client.get(f"/api/runs/{run_id}/trades").json()
    trade = log["trades"][0]
    bars = client.get(
        f"/api/runs/{run_id}/bars",
        params={"entry_key": trade["entry_key"], "exit_key": trade["exit_key"]},
    ).json()
    assert bars["timeframe"] == "M1"
    assert bars["clock_domain"] == "mt5.justmarkets-demo3.wallclock"
    assert "utc" not in bars["clock_domain"].lower()
    assert bars["count"] > 0
    assert bars["first_key"] <= trade["entry_key"]
    assert bars["last_key"] >= trade["exit_key"]
    keys = [b["t"] for b in bars["bars"]]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_bad_run_id_is_refused_without_touching_the_filesystem(client: TestClient) -> None:
    """Path traversal must be rejected by the identifier pattern, not by luck."""
    for bad in ("../../etc", "not-a-ulid", "01M0JQD46JK989RKYF7JTF238J/../../x"):
        assert client.get(f"/api/runs/{bad}").status_code in (404, 400)


# --------------------------------------------------------------- experiments


def test_experiments_group_by_recorded_plugin_identity(client: TestClient) -> None:
    """Grouping must use identity the run's own config declares, not an invention."""
    data = client.get("/api/experiments").json()
    assert data["group_count"] >= 1
    for group in data["groups"]:
        assert group["feature_name"]
        assert group["model_name"]
        assert group["policy_name"]
        assert group["dataset_id"]
        assert group["run_count"] == len(group["runs"])
        assert {run["dataset_id"] for run in group["runs"]} == {group["dataset_id"]}
    total = sum(g["run_count"] for g in data["groups"])
    assert total == data["run_count"]


def test_experiment_metrics_are_recorded_not_recomputed(client: TestClient) -> None:
    """Only metric names the Research Machine actually records may appear."""
    data = client.get("/api/experiments").json()
    run = data["groups"][0]["runs"][0]
    assert set(run["metrics"]) == {
        "trades_sum", "net_pnl_sum", "gross_pnl_sum", "total_cost_sum",
        "win_rate_min", "win_rate_max", "max_drawdown_worst", "fold_count",
    }
    assert run["metrics"]["fold_count"] > 0


def test_non_additive_metrics_are_never_collapsed(client: TestClient) -> None:
    """Win rate stays a range and drawdown stays a worst-fold figure.

    Averaging either across folds with unequal trade counts would manufacture a
    statistic the platform never measured (ADR-0024).
    """
    data = client.get("/api/experiments").json()
    assert "range" in data["aggregation"]["win_rate"]
    assert "worst" in data["aggregation"]["max_drawdown"]
    for group in data["groups"]:
        for run in group["runs"]:
            metrics = run["metrics"]
            if metrics.get("win_rate_min") is not None:
                assert metrics["win_rate_min"] <= metrics["win_rate_max"]
            assert "win_rate" not in metrics       # no single collapsed ratio
            assert "max_drawdown" not in metrics   # no combined drawdown


def test_summed_trade_count_matches_the_trade_log(client: TestClient) -> None:
    """The summed fold trade_count must equal the actual number of trades."""
    data = client.get("/api/experiments").json()
    run = data["groups"][0]["runs"][0]
    log = client.get(f"/api/runs/{run['run_id']}/trades").json()
    assert run["metrics"]["trades_sum"] == log["count"]


def test_pages_are_served(client: TestClient) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert "Research Plugins" in root.text
    family = client.get("/family")
    assert family.status_code == 200
    assert "f-table" in family.text
    chart = client.get("/chart")
    assert chart.status_code == 200
    assert "run-head" in chart.text
    # The long run dropdown must be gone from the chart page.
    assert "run-select" not in chart.text


def test_family_grouping_key_round_trips_through_the_listing(client: TestClient) -> None:
    """The key the Plugins page links with must resolve back to its group."""
    data = client.get("/api/experiments").json()
    for group in data["groups"]:
        assert group["key"] == "|".join(
            (
                group["dataset_id"],
                group["feature_name"],
                group["model_name"],
                group["policy_name"],
            )
        )


# --------------------------------------------------------------- timeframes


@pytest.mark.parametrize("timeframe", ["M1", "M5", "M15", "H1"])
def test_bars_serve_every_timeframe(client: TestClient, run_id: str, timeframe: str) -> None:
    log = client.get(f"/api/runs/{run_id}/trades").json()
    trade = log["trades"][0]
    bars = client.get(
        f"/api/runs/{run_id}/bars",
        params={
            "entry_key": trade["entry_key"],
            "exit_key": trade["exit_key"],
            "timeframe": timeframe,
        },
    ).json()
    assert bars["timeframe"] == timeframe
    assert bars["count"] > 0
    keys = [b["t"] for b in bars["bars"]]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    grid = bars["grid_seconds"]
    # Every candle sits exactly on the arithmetic grid.
    assert all(k % grid == 0 for k in keys)
    for bar in bars["bars"]:
        assert bar["l"] <= bar["o"] <= bar["h"]
        assert bar["l"] <= bar["c"] <= bar["h"]


def test_marker_anchors_contain_the_fills(client: TestClient, run_id: str) -> None:
    """Entry/exit anchors must be the candle containing each fill, at any timeframe."""
    log = client.get(f"/api/runs/{run_id}/trades").json()
    trade = log["trades"][0]
    for timeframe in ("M1", "M5", "M15", "H1"):
        bars = client.get(
            f"/api/runs/{run_id}/bars",
            params={
                "entry_key": trade["entry_key"],
                "exit_key": trade["exit_key"],
                "timeframe": timeframe,
            },
        ).json()
        grid = bars["grid_seconds"]
        entry_bar, exit_bar = bars["entry_bar_key"], bars["exit_bar_key"]
        assert entry_bar <= trade["entry_key"] < entry_bar + grid
        assert exit_bar <= trade["exit_key"] < exit_bar + grid
        # Anchors must be real candles, or a marker would float off the series.
        keys = {b["t"] for b in bars["bars"]}
        assert entry_bar in keys and exit_bar in keys


def test_coarser_timeframes_cover_more_time_with_similar_candle_counts(
    client: TestClient, run_id: str
) -> None:
    """Switching timeframe keeps roughly the same candles on screen, not the same span."""
    log = client.get(f"/api/runs/{run_id}/trades").json()
    trade = log["trades"][0]
    spans = {}
    for timeframe in ("M1", "M5", "H1"):
        bars = client.get(
            f"/api/runs/{run_id}/bars",
            params={
                "entry_key": trade["entry_key"],
                "exit_key": trade["exit_key"],
                "timeframe": timeframe,
            },
        ).json()
        spans[timeframe] = (bars["last_key"] - bars["first_key"], bars["count"])
    assert spans["M1"][0] < spans["M5"][0] < spans["H1"][0]
    for _, count in spans.values():
        assert count > 500


def test_unknown_timeframe_is_refused(client: TestClient, run_id: str) -> None:
    log = client.get(f"/api/runs/{run_id}/trades").json()
    trade = log["trades"][0]
    response = client.get(
        f"/api/runs/{run_id}/bars",
        params={
            "entry_key": trade["entry_key"],
            "exit_key": trade["exit_key"],
            "timeframe": "M3",
        },
    )
    assert response.status_code == 400
    assert "M3" in response.json()["detail"]


# ------------------------------------------------------------- run overview


@pytest.fixture(scope="module")
def overview(client: TestClient, run_id: str) -> dict:
    return client.get(f"/api/runs/{run_id}/overview").json()


def test_overview_fold_order_is_verified_against_the_run_summary(overview: dict) -> None:
    """Fold ordering must be confirmed from the run's own file, not assumed."""
    assert overview["fold_order_verified"] is True
    assert [f["fold_index"] for f in overview["folds"]] == list(range(len(overview["folds"])))


def test_overview_fold_metrics_are_transcribed_recorded_values(overview: dict) -> None:
    """Only metric names the platform records may appear per fold."""
    allowed = {"fold_index", "trade_count", "net_pnl", "gross_pnl",
               "total_cost", "win_rate", "max_drawdown"}
    for fold in overview["folds"]:
        assert set(fold) <= allowed


def test_overview_totals_reconcile_with_recorded_fold_metrics(overview: dict) -> None:
    """The TradeLog transcription must agree with what the platform measured."""
    assert overview["trade_count"] == sum(f["trade_count"] for f in overview["folds"])
    assert overview["net_pnl_sum"] == pytest.approx(
        sum(f["net_pnl"] for f in overview["folds"]), abs=1e-6
    )


def test_cumulative_curve_is_a_running_total_in_trade_order(
    client: TestClient, run_id: str, overview: dict
) -> None:
    log = client.get(f"/api/runs/{run_id}/trades").json()["trades"]
    assert len(overview["curve"]) == len(log)
    running = 0.0
    for point, trade in zip(overview["curve"], log, strict=True):
        running += trade["net_pnl"]
        assert point["i"] == trade["index"]
        assert point["t"] == trade["entry_key"]
        assert point["v"] == pytest.approx(running, abs=1e-6)
    assert overview["curve"][-1]["v"] == pytest.approx(overview["net_pnl_sum"], abs=1e-6)
    keys = [p["t"] for p in overview["curve"]]
    assert keys == sorted(keys), "curve must be chronological"


def test_breakdowns_are_counts_of_recorded_columns(
    client: TestClient, run_id: str, overview: dict
) -> None:
    """Direction, exit-reason and flag figures must be plain transcription."""
    log = client.get(f"/api/runs/{run_id}/trades").json()["trades"]
    assert overview["direction"]["long"]["count"] == sum(1 for t in log if t["direction"] > 0)
    assert overview["direction"]["short"]["count"] == sum(1 for t in log if t["direction"] < 0)
    assert (overview["direction"]["long"]["count"]
            + overview["direction"]["short"]["count"]) == overview["trade_count"]
    assert sum(v["count"] for v in overview["exits"].values()) == overview["trade_count"]
    assert overview["flags"]["gapped_exit"] == sum(1 for t in log if t["gapped_exit"])
    assert overview["flags"]["ambiguous_exit"] == sum(1 for t in log if t["ambiguous_exit"])


def test_overview_introduces_no_derived_ratio(overview: dict) -> None:
    """No ratio may exist that the platform did not itself record.

    win_rate is recorded per fold and appears there. Nothing else on this page
    may be a ratio, and no significance or adjusted statistic may appear at all
    (ADR-0024).
    """
    forbidden = ("win_rate", "hit_rate", "sharpe", "expectancy", "profit_factor",
                 "p_value", "pvalue", "significance", "deflated", "t_stat")
    for section in ("direction", "exits", "flags"):
        assert not any(
            key in str(overview[section]).lower() for key in forbidden
        ), section
    assert "win_rate" not in overview
    assert "max_drawdown" not in overview


def test_fold_boundaries_mark_first_trade_of_each_fold(
    client: TestClient, run_id: str, overview: dict
) -> None:
    log = client.get(f"/api/runs/{run_id}/trades").json()["trades"]
    seen: set[int] = set()
    expected = []
    for trade in log:
        if trade["fold_index"] not in seen:
            seen.add(trade["fold_index"])
            expected.append((trade["fold_index"], trade["entry_key"], trade["index"]))
    actual = [(b["fold_index"], b["t"], b["i"]) for b in overview["fold_boundaries"]]
    assert actual == expected


def test_run_overview_page_is_served(client: TestClient) -> None:
    page = client.get("/run")
    assert page.status_code == 200
    assert "pnl-chart" in page.text
    assert "Explore trades" in page.text
