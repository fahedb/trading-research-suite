"""Both TradeLog container versions, against synthetic run directories.

Version 1 predates recorded bracket levels: it has twelve columns and one global
ExecutionSpec, so its TP/SL are reconstructed. Version 2 records the exact levels
the simulator used, which is the only thing that can describe a per-trade bracket
at all. Both must remain readable, each under its own semantics, and the payload
must say which it used.

These build their own run directories rather than reading the machine's real runs
root, so the version handling is covered on any machine.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from explorer import trades as trades_module

RUN_ID = "01M0N374XM73PQSYGGEFPQKH3C"
HEX_ONE_HUNDREDTH = {"__float_hex__": 0.01.hex()}
V1_COLUMNS = (
    "entry_key",
    "exit_key",
    "direction",
    "entry_price",
    "exit_price",
    "exit_reason",
    "ambiguous_exit",
    "gapped_exit",
    "gross_pnl",
    "spread_cost",
    "commission_cost",
    "net_pnl",
)


def _execution_spec(*, take_profit_pips: float | None, stop_loss_pips: float | None) -> dict:
    def pips(value: float | None) -> Any:
        return None if value is None else {"__float_hex__": float(value).hex()}

    return {
        "execution_id": "bracket_single_position_v1",
        "version": 1,
        "lot_size": HEX_ONE_HUNDREDTH,
        "take_profit_pips": pips(take_profit_pips),
        "stop_loss_pips": pips(stop_loss_pips),
        "cost_binding": {
            "binding_id": "unverified-audit-input-v1",
            "binding_digest": None,
            "cost_semantics": "unverified",
            "execution_price_model": "recorded_path_unverified",
            "contract_size": {"__float_hex__": 100.0.hex()},
            "price_units_per_pip": HEX_ONE_HUNDREDTH,
            "account_currency_per_quote_currency": {"__float_hex__": 1.0.hex()},
            "spread_pips": {"__float_hex__": 20.0.hex()},
            "commission_per_lot": {"__float_hex__": 6.0.hex()},
            "commission_basis": "per_round_turn",
        },
    }


def _write_run(
    root: Path,
    *,
    version: int,
    directions: tuple[int, ...],
    take_profit_prices: tuple[float, ...] | None = None,
    stop_loss_prices: tuple[float, ...] | None = None,
    take_profit_pips: float | None = 800.0,
    stop_loss_pips: float | None = 400.0,
) -> None:
    run = root / "runs" / RUN_ID
    (run / "artifacts").mkdir(parents=True, exist_ok=True)
    spec = _execution_spec(take_profit_pips=take_profit_pips, stop_loss_pips=stop_loss_pips)
    count = len(directions)

    (run / "state.json").write_text(
        json.dumps(
            {
                "state": "COMPLETE",
                "oos_touched": False,
                "lockbox_touched": False,
                "finished_unix_ns": 1,
            }
        )
    )
    (run / "identity.json").write_text(
        json.dumps(
            {
                "experiment_fingerprint": "f" * 64,
                "fingerprint_inputs": {
                    "resolved_config": {
                        "dataset": {
                            "dataset_id": "XAUUSD_S_M1_TRAIN_V1",
                            "manifest_sha256": "3" * 64,
                            "role": "train",
                        },
                        "features": [{"name": "log_return_1"}],
                        "model": {"name": "stateless_rule_v1"},
                        "policies": [{"name": "bracket_threshold_v1"}],
                        "execution": spec,
                    },
                    "platform": {"git_commit": "c" * 40},
                },
            }
        )
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "canonical": True,
                "files": {"artifacts": {"fold-0000-policy-0000-trades.npz": "d" * 64}},
            }
        )
    )

    arrays: dict[str, np.ndarray] = {
        "entry_key": np.arange(count, dtype=np.int64) * 60 + 1_580_437_860,
        "exit_key": np.arange(count, dtype=np.int64) * 60 + 1_580_437_920,
        "direction": np.asarray(directions, dtype=np.int8),
        "entry_price": np.full(count, 1577.0, dtype=np.float64),
        "exit_price": np.full(count, 1578.0, dtype=np.float64),
        "exit_reason": np.full(count, 4, dtype=np.int8),
        "ambiguous_exit": np.zeros(count, dtype=np.bool_),
        "gapped_exit": np.zeros(count, dtype=np.bool_),
        "gross_pnl": np.full(count, 1.0, dtype=np.float64),
        "spread_cost": np.full(count, 0.2, dtype=np.float64),
        "commission_cost": np.full(count, 0.06, dtype=np.float64),
        "net_pnl": np.full(count, 0.74, dtype=np.float64),
    }
    if version >= 2:
        assert take_profit_prices is not None
        assert stop_loss_prices is not None
        arrays["take_profit_price"] = np.asarray(take_profit_prices, dtype=np.float64)
        arrays["stop_loss_price"] = np.asarray(stop_loss_prices, dtype=np.float64)

    meta = json.dumps(
        {
            "format": "trading-research-trade-log",
            "format_version": version,
            "identity_key": "a" * 64,
            "time_unit": "seconds",
            "clock_domain": "mt5.justmarkets-demo3.wallclock",
            "fold_index": 0,
            "execution_spec": spec,
            "execution_rows": 100,
        }
    ).encode("ascii")
    np.savez(
        run / "artifacts" / "fold-0000-policy-0000-trades.npz",
        __meta__=np.frombuffer(meta, dtype=np.uint8),
        **arrays,
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(trades_module, "RUNS_ROOT", tmp_path)
    # trade_log memoises a whole run; the cache must not survive between cases.
    trades_module.trade_log.cache_clear()


def test_a_legacy_v1_log_is_still_readable_and_says_it_was_reconstructed(
    tmp_path: Path,
) -> None:
    _write_run(tmp_path, version=1, directions=(1, -1))
    log = trades_module.trade_log(RUN_ID)

    assert log["trade_log_format_version"] == 1
    assert log["bracket_mode"] == "uniform"
    long_trade, short_trade = log["trades"]
    for trade in (long_trade, short_trade):
        assert trade["bracket_source"] == "reconstructed_v1"
    # 800 pips at 0.01 price units per pip is 8.00; 400 pips is 4.00.
    assert long_trade["take_profit"] == pytest.approx(1585.0)
    assert long_trade["stop_loss"] == pytest.approx(1573.0)
    assert short_trade["take_profit"] == pytest.approx(1569.0)
    assert short_trade["stop_loss"] == pytest.approx(1581.0)


def test_a_v2_log_uses_recorded_levels_and_never_reconstructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded levels must win even when a global spec would disagree."""
    _write_run(
        tmp_path,
        version=2,
        directions=(1, -1),
        take_profit_prices=(1580.0, 1571.0),
        stop_loss_prices=(1574.0, 1583.0),
    )

    def _forbidden(*_args: object, **_kwargs: object) -> tuple[None, None]:
        raise AssertionError("a version-2 trade log must never be reconstructed")

    monkeypatch.setattr(trades_module, "bracket_levels", _forbidden)
    log = trades_module.trade_log(RUN_ID)

    assert log["trade_log_format_version"] == 2
    long_trade, short_trade = log["trades"]
    assert long_trade["bracket_source"] == "recorded"
    assert long_trade["take_profit"] == pytest.approx(1580.0)
    assert long_trade["stop_loss"] == pytest.approx(1574.0)
    assert short_trade["take_profit"] == pytest.approx(1571.0)
    assert short_trade["stop_loss"] == pytest.approx(1583.0)
    # The global spec says 8.00/4.00, which is not what these trades used.
    assert long_trade["take_profit_distance"] == pytest.approx(3.0)
    assert long_trade["stop_loss_distance"] == pytest.approx(3.0)


def test_trades_with_different_brackets_report_per_trade_mode(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        version=2,
        directions=(1, 1),
        take_profit_prices=(1585.0, 1581.5),
        stop_loss_prices=(1574.0, 1570.0),
    )
    log = trades_module.trade_log(RUN_ID)
    assert log["bracket_mode"] == "per_trade"


def test_a_null_global_spec_reports_per_trade_even_when_distances_match(
    tmp_path: Path,
) -> None:
    """Provenance, not coincidence, decides the mode.

    A fixed-bracket policy gives every trade the same distance while the run's
    ExecutionSpec supplies nothing. Keying the mode on "do the distances differ"
    would call this uniform and then print no bracket at all, because there is
    no global pair to print — which is exactly what the header must not do.
    """
    _write_run(
        tmp_path,
        version=2,
        directions=(1, -1),
        # Same 8.00 / 4.00 geometry on both, from different entries.
        take_profit_prices=(1585.0, 1569.0),
        stop_loss_prices=(1573.0, 1581.0),
        take_profit_pips=None,
        stop_loss_pips=None,
    )
    log = trades_module.trade_log(RUN_ID)
    assert log["bracket_mode"] == "per_trade"
    assert {t["bracket_source"] for t in log["trades"]} == {"recorded"}
    assert {t["take_profit_distance"] for t in log["trades"]} == {8.0}
    assert {t["stop_loss_distance"] for t in log["trades"]} == {4.0}
    # The levels themselves still differ, which is what the chart draws.
    assert len({t["take_profit"] for t in log["trades"]}) == 2


def test_a_global_spec_with_uniform_trades_stays_uniform(tmp_path: Path) -> None:
    """A run that really is one global bracket must keep saying so."""
    _write_run(tmp_path, version=1, directions=(1, -1))
    assert trades_module.trade_log(RUN_ID)["bracket_mode"] == "uniform"


def test_a_run_without_any_bracket_is_not_called_per_trade(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        version=2,
        directions=(1,),
        take_profit_prices=(math.nan,),
        stop_loss_prices=(math.nan,),
        take_profit_pips=None,
        stop_loss_pips=None,
    )
    assert trades_module.trade_log(RUN_ID)["bracket_mode"] == "uniform"


@pytest.mark.parametrize(
    ("take_profit", "stop_loss", "expected"),
    [
        (1585.0, math.nan, (1585.0, None)),
        (math.nan, 1574.0, (None, 1574.0)),
        (math.nan, math.nan, (None, None)),
    ],
    ids=["take-profit-only", "stop-loss-only", "neither"],
)
def test_an_absent_recorded_side_becomes_json_null(
    tmp_path: Path,
    take_profit: float,
    stop_loss: float,
    expected: tuple[float | None, float | None],
) -> None:
    _write_run(
        tmp_path,
        version=2,
        directions=(1,),
        take_profit_prices=(take_profit,),
        stop_loss_prices=(stop_loss,),
    )
    trade = trades_module.trade_log(RUN_ID)["trades"][0]
    assert (trade["take_profit"], trade["stop_loss"]) == expected


def test_the_payload_is_strict_json_with_no_nan_token(tmp_path: Path) -> None:
    """A raw NaN would serialise to a token JSON.parse rejects in the browser."""
    _write_run(
        tmp_path,
        version=2,
        directions=(1, -1),
        take_profit_prices=(1585.0, math.nan),
        stop_loss_prices=(math.nan, 1583.0),
    )
    body = json.dumps(trades_module.trade_log(RUN_ID))
    assert "NaN" not in body
    assert "Infinity" not in body
    # parse_constant fires on exactly the non-standard tokens.
    reparsed = json.loads(
        body,
        parse_constant=lambda name: pytest.fail(f"non-standard JSON constant {name!r}"),
    )
    assert reparsed["trades"][0]["stop_loss"] is None
    assert reparsed["trades"][1]["take_profit"] is None


def test_an_unknown_future_version_still_fails_loudly(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        version=3,
        directions=(1,),
        take_profit_prices=(1585.0,),
        stop_loss_prices=(1574.0,),
    )
    with pytest.raises(trades_module.RunNotFoundError, match="unsupported trade-log version"):
        trades_module.trade_log(RUN_ID)


def test_entry_exit_and_reason_are_unchanged_across_versions(tmp_path: Path) -> None:
    """Only the bracket path differs between versions; the rest must not move."""
    _write_run(tmp_path, version=1, directions=(1,))
    legacy = trades_module.trade_log(RUN_ID)["trades"][0]
    fields = ("entry_key", "exit_key", "direction", "entry_price", "exit_price", "exit_reason")
    legacy_view = {name: legacy[name] for name in fields}

    trades_module.trade_log.cache_clear()
    _write_run(
        tmp_path,
        version=2,
        directions=(1,),
        take_profit_prices=(1585.0,),
        stop_loss_prices=(1573.0,),
    )
    recorded = trades_module.trade_log(RUN_ID)["trades"][0]
    assert {name: recorded[name] for name in fields} == legacy_view
    assert recorded["exit_reason"] == "BOUNDARY_FORCED_CLOSE"


def test_v1_column_set_is_the_one_this_reader_still_supports() -> None:
    """Guard the legacy shape so a silent column drop cannot pass unnoticed."""
    assert trades_module.SUPPORTED_TRADE_LOG_VERSIONS == (1, 2)
    assert trades_module.RECORDED_BRACKETS_VERSION == 2
    assert len(V1_COLUMNS) == 12
