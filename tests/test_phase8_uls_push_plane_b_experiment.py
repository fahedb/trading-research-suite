"""Pre-registration guard for the ULS + PUSH fixed-bracket TRAIN simulation."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from trading_research.experiments.config import load_resolved_config


def test_uls_push_v7_tp400_sl400_train_simulation_is_fully_preregistered() -> None:
    repository_root = Path(__file__).parents[1]
    base = load_resolved_config(
        repository_root
        / "config"
        / "experiments"
        / "uls-push-v7-h20m5-train-exploratory-v1.json"
    )
    simulation = load_resolved_config(
        repository_root
        / "config"
        / "experiments"
        / "uls-push-v7-train-plane-b-tp400-sl400-v1.json"
    )

    expected = deepcopy(base.document())
    expected["evaluation_outcomes"] = []
    expected["evaluators"] = []
    expected["execution"] = {
        "cost_binding": {
            "account_currency_per_quote_currency": {
                "__float_hex__": "0x1.0000000000000p+0"
            },
            "binding_digest": None,
            "binding_id": "unverified-audit-input-v1",
            "commission_basis": "per_round_turn",
            "commission_per_lot": {"__float_hex__": "0x1.8000000000000p+2"},
            "contract_size": {"__float_hex__": "0x1.9000000000000p+6"},
            "cost_semantics": "unverified",
            "execution_price_model": "recorded_path_unverified",
            "price_units_per_pip": {"__float_hex__": "0x1.47ae147ae147bp-7"},
            "spread_pips": {"__float_hex__": "0x1.4000000000000p+4"},
        },
        "execution_id": "bracket_single_position_v1",
        "lot_size": {"__float_hex__": "0x1.47ae147ae147bp-7"},
        "stop_loss_pips": {"__float_hex__": "0x1.9000000000000p+8"},
        "take_profit_pips": {"__float_hex__": "0x1.9000000000000p+8"},
        "version": 1,
    }
    expected["trade_evaluators"] = [
        {
            "name": "trade_summary_v1",
            "params": {"min_effective_trades": 1},
            "version": 1,
        },
        {
            "name": "equity_drawdown_v1",
            "params": {"min_effective_rows": 1},
            "version": 1,
        },
    ]

    assert simulation.document() == expected
    assert sha256(simulation.canonical_json().encode()).hexdigest() == (
        "9bed953127cc60aba69698196003638f79e5fccadb6069aeb41d007f37f80d9a"
    )
