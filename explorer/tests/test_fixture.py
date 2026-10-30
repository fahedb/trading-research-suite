"""Structural and research-integrity checks on the generated E1 fixture.

These assert what the prototype is allowed to rely on: the fixture describes a
completed canonical TRAIN run, its bars are ordered and gap-bearing, and the
displayed TP/SL levels reproduce the simulator's own recorded exits exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent.parent / "fixture" / "explorer-fixture.json"


@pytest.fixture(scope="module")
def fx() -> dict:
    if not FIXTURE.is_file():
        pytest.skip("fixture not generated; run tools/make_fixture.py")
    return json.loads(FIXTURE.read_text())


def test_fixture_version(fx: dict) -> None:
    assert fx["fixture_version"] == 1


def test_provenance_is_a_complete_canonical_train_run(fx: dict) -> None:
    assert fx["provenance"]["run_state"] == "COMPLETE"
    assert fx["provenance"]["canonical"] is True
    assert fx["dataset"]["role"] == "train"


def test_access_was_discovery_default_only(fx: dict) -> None:
    """E1 must never touch OOS, LOCKBOX, SNAPSHOT or a parent dataset."""
    assert fx["dataset"]["access_classification"] == "NORMAL"
    assert "OOS" not in fx["dataset"]["dataset_id"].upper()
    assert "LOCKBOX" not in fx["dataset"]["dataset_id"].upper()
    assert "SNAPSHOT" not in fx["dataset"]["dataset_id"].upper()


def test_clock_domain_is_recorded_and_not_utc(fx: dict) -> None:
    domain = fx["time"]["clock_domain"]
    assert domain == "mt5.justmarkets-demo3.wallclock"
    assert "utc" not in domain.lower()
    assert fx["time"]["unit"] == "seconds"
    assert fx["time"]["calendar_time_policy"] == "native-wallclock-v1"


def test_bars_are_ordered_unique_and_plausible(fx: dict) -> None:
    data = fx["bars"]["data"]
    assert len(data) == fx["bars"]["count"] == 3000
    keys = [b["t"] for b in data]
    assert keys == sorted(keys), "bar keys must be ascending"
    assert len(set(keys)) == len(keys), "bar keys must be unique"
    for b in data:
        assert b["l"] <= b["o"] <= b["h"], "open must sit inside the bar range"
        assert b["l"] <= b["c"] <= b["h"], "close must sit inside the bar range"


def test_bars_contain_a_real_closure_gap(fx: dict) -> None:
    """A weekend closure is the hardest case for a time axis; E1 must include one."""
    assert fx["bars"]["max_gap_seconds"] >= 24 * 3600, (
        "fixture must span at least one genuine market-closure gap"
    )


def test_trade_lies_inside_the_bar_window_with_context_on_both_sides(fx: dict) -> None:
    t, b = fx["trade"], fx["bars"]
    assert b["first_key"] < t["entry_key"], "need history before entry"
    assert b["last_key"] > t["exit_key"], "need bars after exit"
    assert t["entry_key"] <= t["exit_key"]


def test_bracket_levels_reproduce_the_simulator_exactly(fx: dict) -> None:
    """The generator cross-checked every non-gapped bracket exit in the fold."""
    check = fx["bracket_verification"]
    assert check["non_gapped_bracket_exits"] > 0
    assert check["exact_matches"] == check["non_gapped_bracket_exits"]


def test_selected_trade_bracket_derivation(fx: dict) -> None:
    """Re-derive TP/SL here, independently of the generator."""
    t, e = fx["trade"], fx["execution"]
    upp = e["price_units_per_pip"]
    if e["take_profit_pips"] is not None:
        assert t["take_profit"] == t["entry_price"] + t["direction"] * e["take_profit_pips"] * upp
    if e["stop_loss_pips"] is not None:
        assert t["stop_loss"] == t["entry_price"] - t["direction"] * e["stop_loss_pips"] * upp


def test_stop_loss_exit_landed_on_the_stop(fx: dict) -> None:
    t = fx["trade"]
    if t["exit_reason"] == "STOP_LOSS" and not t["gapped_exit"]:
        assert t["exit_price"] == t["stop_loss"]
    if t["exit_reason"] == "TAKE_PROFIT" and not t["gapped_exit"]:
        assert t["exit_price"] == t["take_profit"]


def test_cost_semantics_are_carried_and_unverified(fx: dict) -> None:
    """ADR-0020: money from an open cost audit must be labelled, never implied real."""
    assert fx["execution"]["cost_semantics"] == "unverified"
    assert fx["execution"]["execution_price_model"] == "recorded_path_unverified"


def test_net_pnl_is_consistent_with_its_components(fx: dict) -> None:
    t = fx["trade"]
    assert t["net_pnl"] == pytest.approx(
        t["gross_pnl"] - t["spread_cost"] - t["commission_cost"], abs=1e-9
    )
