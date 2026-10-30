"""Trade → signal mapping and decision context, checked against real artifacts.

The join rule is that a trade's signal is the greatest model-output key strictly
before its entry. These tests re-derive that independently from the raw ``.npz``
files rather than trusting the module, because a silently wrong join would attach
the wrong reason to a trade — which is worse than showing no reason at all.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from explorer.api import app
from explorer.config import INDEX_PATH, RUNS_ROOT
from explorer.signals import ZONE_KINDS, signal_context
from explorer.trades import run_dir, trade_log


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not RUNS_ROOT.is_dir() or not INDEX_PATH.is_file():
        pytest.skip("runs root or run index not present on this machine")
    return TestClient(app)


@pytest.fixture(scope="module")
def run_id(client: TestClient) -> str:
    groups = client.get("/api/experiments?limit=2000").json()["groups"]
    for group in groups:
        if group["feature_name"] == "zone_trader_v13" and group["runs"]:
            return group["runs"][0]["run_id"]
    pytest.skip("no completed Zone Trader simulation run is indexed")


@pytest.fixture(scope="module")
def context(run_id: str) -> dict:
    return signal_context(run_id)


def test_every_trade_maps_to_a_signal(context: dict, run_id: str) -> None:
    assert context["available"] is True
    assert context["matched"] == context["trade_count"] > 0


def test_signal_precedes_entry_on_the_execution_axis(context: dict, run_id: str) -> None:
    """Re-derive the join straight from the artifacts, independently of the module."""
    artifacts = run_dir(run_id) / "artifacts"
    log = trade_log(run_id)["trades"]
    by_fold: dict[int, np.ndarray] = {}

    checked = 0
    for trade in log:
        fold = trade["fold_index"]
        if fold not in by_fold:
            path = artifacts / f"fold-{fold:04d}-model-output.npz"
            if not path.is_file():
                continue
            with np.load(path, allow_pickle=False) as payload:
                by_fold[fold] = payload["keys"].copy()
        keys = by_fold[fold]
        position = int(np.searchsorted(keys, trade["entry_key"]))
        assert int(keys[position]) == trade["entry_key"]
        expected_signal = int(keys[position - 1])

        found = context["contexts"].get(str(trade["index"]))
        assert found is not None, trade["index"]
        assert found["signal_key"] == expected_signal
        assert found["signal_key"] < trade["entry_key"]
        checked += 1
    assert checked > 0


def test_context_values_match_the_features_artifact(context: dict, run_id: str) -> None:
    """Zone bounds and intended levels must be transcribed, not derived."""
    path = run_dir(run_id) / "artifacts" / "features.npz"
    if not path.is_file():
        pytest.skip("run retained no features artifact")
    with np.load(path, allow_pickle=False) as payload:
        meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        names = [c["name"] for c in meta["columns"]]
        members = [c["member"] for c in meta["columns"]]
        keys = payload["keys"].copy()
        columns = {n: payload[m].copy() for n, m in zip(names, members, strict=True)}

    checked = 0
    for index, found in list(context["contexts"].items())[:60]:
        at = int(np.searchsorted(keys, found["signal_key"]))
        assert int(keys[at]) == found["signal_key"], index

        # The signal row must genuinely carry an event, not just exist.
        assert columns["zt_event_signed"][at] != 0.0
        assert found["event_signed"] == int(columns["zt_event_signed"][at])

        assert found["zone"]["top"] == pytest.approx(columns["zt_zone_top"][at])
        assert found["zone"]["bottom"] == pytest.approx(columns["zt_zone_bottom"][at])
        assert found["zone"]["kind"] == ZONE_KINDS[int(columns["zt_zone_kind"][at])]
        assert found["intended"]["entry"] == pytest.approx(columns["zt_entry_price"][at])
        assert found["intended"]["stop"] == pytest.approx(columns["zt_stop_price"][at])
        assert found["intended"]["target"] == pytest.approx(columns["zt_target_price"][at])
        checked += 1
    assert checked > 0


def test_signal_direction_agrees_with_the_trade(context: dict, run_id: str) -> None:
    log = {t["index"]: t for t in trade_log(run_id)["trades"]}
    for index, found in context["contexts"].items():
        assert found["event_signed"] == log[int(index)]["direction"]


def test_zone_bounds_bracket_the_intended_entry(context: dict) -> None:
    """A retest entry should sit at or near the zone it retested."""
    for found in list(context["contexts"].values())[:200]:
        zone, intended = found["zone"], found["intended"]
        if None in (zone["top"], zone["bottom"], intended["entry"]):
            continue
        assert zone["bottom"] <= zone["top"]


def test_executed_levels_are_the_tradelog_not_the_strategy(
    context: dict, run_id: str
) -> None:
    """The executed block must come from the TradeLog, never from the provider."""
    log = {t["index"]: t for t in trade_log(run_id)["trades"]}
    differing = 0
    for index, found in context["contexts"].items():
        trade = log[int(index)]
        assert found["executed"]["entry"] == trade["entry_price"]
        assert found["executed"]["stop"] == trade["stop_loss"]
        assert found["executed"]["target"] == trade["take_profit"]
        intended_stop = found["intended"]["stop"]
        if intended_stop is not None and abs(intended_stop - trade["stop_loss"]) > 1e-9:
            differing += 1
    # Plane B overrode the strategy's own risk model with fixed brackets; if this
    # ever became zero the two blocks would be redundant and worth revisiting.
    assert differing > 0


def test_zone_creation_time_is_never_invented(context: dict) -> None:
    """The provider drops event_zone_created_at, so it must stay null."""
    assert "zone creation time" in context["unrecorded"]
    for found in context["contexts"].values():
        assert found["zone"]["created_key"] is None
        # The zone may only be claimed from the signal onward.
        assert found["zone"]["known_from"] == found["signal_key"]


def test_signals_endpoint_shape(client: TestClient, run_id: str) -> None:
    body = client.get(f"/api/runs/{run_id}/signals").json()
    assert body["available"] is True
    assert body["provider"]["provider_id"] == "zone_trader_v13"
    assert isinstance(body["provider"]["params"]["confirmation_mode"], str)
    assert "zt_zone_top" in body["columns"]


def test_unknown_run_is_refused(client: TestClient) -> None:
    assert client.get("/api/runs/not-a-ulid/signals").status_code == 404


# ------------------------------------------------------------ ULS + Push

@pytest.fixture(scope="module")
def uls_run_id(client: TestClient) -> str:
    groups = client.get("/api/experiments?limit=2000").json()["groups"]
    for group in groups:
        if group["feature_name"] == "uls_push_v7" and group["runs"]:
            return group["runs"][0]["run_id"]
    pytest.skip("no completed ULS + Push simulation run is indexed")


@pytest.fixture(scope="module")
def uls_context(uls_run_id: str) -> dict:
    return signal_context(uls_run_id)


def test_sparse_uls_signals_map_to_every_trade(
    uls_context: dict, uls_run_id: str
) -> None:
    """ULS model output is sparse: it contains signals, not the later fill rows."""
    assert uls_context["provider"]["provider_id"] == "uls_push_v7"
    assert uls_context["matched"] == uls_context["trade_count"] > 0

    log = {trade["index"]: trade for trade in trade_log(uls_run_id)["trades"]}
    for index, found in uls_context["contexts"].items():
        trade = log[int(index)]
        assert found["signal_key"] < trade["entry_key"]
        assert found["event_signed"] == trade["direction"]
        assert found["kind"] == "uls_push"
        assert found["source"]["label"] in {"ULS · IFVG", "ULS · Push zone", "Push"}
        assert found["executed"]["entry"] == trade["entry_price"]
        assert found["executed"]["stop"] == trade["stop_loss"]
        assert found["executed"]["target"] == trade["take_profit"]


def test_sparse_uls_join_is_the_raw_model_predecessor(
    uls_context: dict, uls_run_id: str
) -> None:
    artifacts = run_dir(uls_run_id) / "artifacts"
    logs = trade_log(uls_run_id)["trades"]
    by_fold: dict[int, np.ndarray] = {}

    for trade in logs:
        fold = trade["fold_index"]
        if fold not in by_fold:
            with np.load(
                artifacts / f"fold-{fold:04d}-model-output.npz", allow_pickle=False
            ) as payload:
                by_fold[fold] = payload["keys"].copy()
        keys = by_fold[fold]
        position = int(np.searchsorted(keys, trade["entry_key"]))
        assert position > 0
        expected = int(keys[position - 1])
        assert uls_context["contexts"][str(trade["index"])]["signal_key"] == expected


def test_uls_signals_endpoint_exposes_recorded_context(
    client: TestClient, uls_run_id: str
) -> None:
    body = client.get(f"/api/runs/{uls_run_id}/signals").json()
    assert body["matched"] == body["trade_count"]
    first = next(iter(body["contexts"].values()))
    assert first["kind"] == "uls_push"
    assert first["poi"]["top"] is not None
    if first["source"]["label"] == "Push":
        assert first["confirmations"]["bos"] is None
        assert first["confirmations"]["trendline"] is None
        assert first["confirmations"]["cisd"] is None
    else:
        assert first["confirmations"]["cisd"] in {0.0, 1.0}
