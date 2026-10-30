"""Slice 4C/4D hand-computed truth for the pure bracket simulator."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

if TYPE_CHECKING:
    from numpy.typing import NDArray

from trading_research.core import (
    ClockDomain,
    CommissionBasis,
    ContractViolationError,
    CostSemanticsStatus,
    DatasetIdentity,
    EquityCurve,
    ExecutionCostBinding,
    ExecutionPriceModel,
    ExecutionSpec,
    IntentFrame,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    TradeExitReason,
    ViewSpan,
)
from trading_research.core.research_frames import _make_intent_frame
from trading_research.core.simulation import (
    SimulationRequest,
    TradeLog,
    _make_simulation_request,
    _trade_exit_reason_code,
)
from trading_research.evaluation.trade_reference import TradeSummary
from trading_research.simulation import (
    SUPPORTED_INTENT_SCHEMA_IDS,
    simulate_bracket_non_interruptible_v1,
    simulate_bracket_single_position_v1,
)

DOMAIN = ClockDomain("synthetic.execution")
IDENTITY = DatasetIdentity("SYNTHETIC_PHASE4", "4" * 64)
ORIGIN = StageOrigin("synthetic_policy_v1", 1, {})
TP = _trade_exit_reason_code(TradeExitReason.TAKE_PROFIT)
SL = _trade_exit_reason_code(TradeExitReason.STOP_LOSS)
TARGET_CHANGE = _trade_exit_reason_code(TradeExitReason.TARGET_CHANGE)
BOUNDARY = _trade_exit_reason_code(TradeExitReason.BOUNDARY_FORCED_CLOSE)


def _binding(
    *,
    contract_size: float = 1.0,
    price_units_per_pip: float = 1.0,
    conversion: float = 1.0,
    spread_pips: float = 0.0,
    commission_per_lot: float = 0.0,
    commission_basis: CommissionBasis = CommissionBasis.PER_ROUND_TURN,
) -> ExecutionCostBinding:
    return ExecutionCostBinding(
        binding_id="synthetic-fixture-v1",
        binding_digest=None,
        cost_semantics=CostSemanticsStatus.UNVERIFIED,
        execution_price_model=ExecutionPriceModel.SYNTHETIC_DECLARED,
        contract_size=contract_size,
        price_units_per_pip=price_units_per_pip,
        account_currency_per_quote_currency=conversion,
        spread_pips=spread_pips,
        commission_per_lot=commission_per_lot,
        commission_basis=commission_basis,
    )


def _spec(
    *,
    execution_id: str = "bracket_single_position_v1",
    take_profit_pips: float | None = None,
    stop_loss_pips: float | None = None,
    lot_size: float = 1.0,
    binding: ExecutionCostBinding | None = None,
) -> ExecutionSpec:
    return ExecutionSpec(
        execution_id=execution_id,
        version=1,
        lot_size=lot_size,
        take_profit_pips=take_profit_pips,
        stop_loss_pips=stop_loss_pips,
        cost_binding=_binding() if binding is None else binding,
    )


def _request(
    rows: tuple[tuple[float, float, float, float], ...],
    *,
    keys: tuple[int, ...] | None = None,
    domain: ClockDomain = DOMAIN,
) -> SimulationRequest:
    actual_keys = tuple(range(len(rows))) if keys is None else keys
    matrix = np.asarray(rows, dtype=np.float64)
    return _make_simulation_request(
        keys=np.asarray(actual_keys, dtype=np.int64),
        unit=TimeUnit.SECONDS,
        clock_domain=domain,
        source_identities=(IDENTITY,),
        open_values=matrix[:, 0].copy(),
        high_values=matrix[:, 1].copy(),
        low_values=matrix[:, 2].copy(),
        close_values=matrix[:, 3].copy(),
    )


def _intents(
    request: SimulationRequest,
    statements: tuple[tuple[int, float], ...],
    *,
    schema_id: str = "target_exposure_intent_v1",
    domain: ClockDomain | None = None,
) -> IntentFrame:
    keys = request.keys
    actual_domain = request.clock_domain if domain is None else domain
    view = ViewSpan(
        request.source_identities,
        request.unit,
        actual_domain,
        NativeTimeKey(int(keys[0]), request.unit, actual_domain),
        NativeTimeKey(int(keys[0]), request.unit, actual_domain),
        NativeTimeKey(int(keys[-1]), request.unit, actual_domain),
    )
    return _make_intent_frame(
        view=view,
        intent_schema_id=schema_id,
        keys=np.asarray([key for key, _ in statements], dtype=np.int64),
        target_exposure=np.asarray([value for _, value in statements], dtype=np.float64),
    )


def _run(
    request: SimulationRequest,
    statements: tuple[tuple[int, float], ...],
    spec: ExecutionSpec,
    *,
    fold_index: int = 0,
) -> tuple[TradeLog, EquityCurve]:
    return simulate_bracket_single_position_v1(
        request,
        _intents(request, statements),
        spec,
        fold_index=fold_index,
        policy_origin=ORIGIN,
    )


def _run_non_interruptible(
    request: SimulationRequest,
    statements: tuple[tuple[int, float], ...],
    spec: ExecutionSpec,
    *,
    fold_index: int = 0,
) -> tuple[TradeLog, EquityCurve]:
    return simulate_bracket_non_interruptible_v1(
        request,
        _intents(request, statements),
        spec,
        fold_index=fold_index,
        policy_origin=ORIGIN,
    )


def _basic_rows() -> tuple[tuple[float, float, float, float], ...]:
    return (
        (100.0, 101.0, 99.0, 100.0),
        (101.0, 102.0, 100.0, 101.5),
        (102.0, 103.0, 101.0, 102.5),
        (103.0, 104.0, 102.0, 103.5),
        (104.0, 105.0, 103.0, 104.5),
    )


def test_simulator_surface_and_project_import_boundary_are_exact() -> None:
    assert SUPPORTED_INTENT_SCHEMA_IDS == (
        "target_exposure_intent_v1",
        "bracketed_exposure_intent_v1",
    )
    module = Path("src/trading_research/simulation/bracket.py")
    tree = ast.parse(module.read_text(), filename=str(module))
    internal_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("trading_research")
    }
    assert internal_imports
    assert all(name.startswith("trading_research.core") for name in internal_imports)
    forbidden = {"open", "print", "input", "random", "datetime", "GapIndex"}
    assert not forbidden.intersection(module.read_text().split())


def test_f1_next_open_entry_and_boundary_close_match_literal_truth() -> None:
    log, curve = _run(_request(_basic_rows()), ((0, 1.0),), _spec())

    np.testing.assert_array_equal(log.entry_key, np.array([1], dtype=np.int64))
    np.testing.assert_array_equal(log.exit_key, np.array([4], dtype=np.int64))
    np.testing.assert_array_equal(log.entry_price, np.array([101.0]))
    np.testing.assert_array_equal(log.exit_price, np.array([104.5]))
    np.testing.assert_array_equal(log.exit_reason, np.array([BOUNDARY], dtype=np.int8))
    np.testing.assert_array_equal(log.gross_pnl, np.array([3.5]))
    np.testing.assert_array_equal(log.net_pnl, np.array([3.5]))
    np.testing.assert_array_equal(curve.equity, np.array([0.0, 0.5, 1.5, 2.5, 3.5]))


def test_f2_take_profit_uses_level_not_bar_high() -> None:
    request = _request(
        (
            (99.0, 100.0, 98.0, 99.0),
            (100.0, 101.0, 99.0, 100.5),
            (100.5, 101.5, 99.5, 101.0),
            (101.0, 103.0, 100.0, 102.5),
            (102.0, 103.0, 101.0, 102.0),
        )
    )
    log, _ = _run(request, ((0, 1.0),), _spec(take_profit_pips=2.0))
    np.testing.assert_array_equal(log.exit_key, np.array([3]))
    np.testing.assert_array_equal(log.exit_price, np.array([102.0]))
    np.testing.assert_array_equal(log.exit_reason, np.array([TP], dtype=np.int8))
    np.testing.assert_array_equal(log.gross_pnl, np.array([2.0]))


def test_f3_stop_loss_uses_level() -> None:
    request = _request(
        (
            (99.0, 100.0, 98.0, 99.0),
            (100.0, 101.0, 99.5, 100.0),
            (99.5, 100.5, 97.0, 98.5),
            (98.0, 99.0, 97.0, 98.0),
        )
    )
    log, _ = _run(request, ((0, 1.0),), _spec(stop_loss_pips=2.0))
    np.testing.assert_array_equal(log.exit_key, np.array([2]))
    np.testing.assert_array_equal(log.exit_price, np.array([98.0]))
    np.testing.assert_array_equal(log.exit_reason, np.array([SL], dtype=np.int8))
    np.testing.assert_array_equal(log.gross_pnl, np.array([-2.0]))


def test_f4_reversal_closes_then_opens_at_one_row_and_charges_each_trade() -> None:
    binding = _binding(spread_pips=1.0, commission_per_lot=2.0)
    request = _request(
        (
            (99.0, 100.0, 98.0, 99.0),
            (100.0, 101.0, 99.0, 100.0),
            (101.0, 102.0, 100.0, 101.0),
            (100.0, 101.0, 98.0, 99.0),
        )
    )
    log, curve = _run(
        request,
        ((0, 1.0), (1, -1.0)),
        _spec(binding=binding),
    )
    np.testing.assert_array_equal(log.entry_key, np.array([1, 2]))
    np.testing.assert_array_equal(log.exit_key, np.array([2, 3]))
    np.testing.assert_array_equal(log.direction, np.array([1, -1], dtype=np.int8))
    np.testing.assert_array_equal(log.entry_price, np.array([100.0, 101.0]))
    np.testing.assert_array_equal(log.exit_price, np.array([101.0, 99.0]))
    np.testing.assert_array_equal(log.exit_reason, np.array([TARGET_CHANGE, BOUNDARY]))
    np.testing.assert_array_equal(log.spread_cost, np.array([1.0, 1.0]))
    np.testing.assert_array_equal(log.commission_cost, np.array([2.0, 2.0]))
    np.testing.assert_array_equal(log.net_pnl, np.array([-2.0, -1.0]))
    np.testing.assert_array_equal(curve.equity, np.array([0.0, -3.0, -5.0, -3.0]))


def test_f5_stopout_reenters_only_on_a_later_explicit_long_statement() -> None:
    request = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (99.5, 100.0, 98.5, 99.0),
            (101.0, 101.5, 100.5, 101.0),
            (102.0, 103.0, 101.0, 102.0),
        )
    )
    log, _ = _run(
        request,
        ((0, 1.0), (2, 1.0)),
        _spec(stop_loss_pips=1.0),
    )
    np.testing.assert_array_equal(log.entry_key, np.array([1, 3]))
    np.testing.assert_array_equal(log.exit_key, np.array([2, 4]))
    np.testing.assert_array_equal(log.exit_reason, np.array([SL, BOUNDARY]))

    held_log, _ = _run(request, ((0, 1.0),), _spec(stop_loss_pips=1.0))
    assert held_log.num_trades == 1


def test_f6_f7_sparse_hold_and_trichotomy_counters_are_observable() -> None:
    request = _request(_basic_rows())
    log, _ = _run(
        request,
        ((0, 1.0), (1, float("nan")), (3, 0.0)),
        _spec(),
    )
    np.testing.assert_array_equal(log.entry_key, np.array([1]))
    np.testing.assert_array_equal(log.exit_key, np.array([4]))
    np.testing.assert_array_equal(log.exit_reason, np.array([TARGET_CHANGE]))
    assert (
        log.intent_rows_long,
        log.intent_rows_flat,
        log.intent_rows_short,
        log.intent_rows_abstain,
        log.intent_rows_absent,
    ) == (1, 1, 0, 1, 2)


def test_f8_flat_while_flat_does_nothing_and_negative_zero_is_flat() -> None:
    request = _request(_basic_rows())
    log, curve = _run(request, ((0, -0.0), (2, 0.0)), _spec())
    assert log.num_trades == 0
    assert log.intent_rows_flat == 2
    np.testing.assert_array_equal(curve.equity, np.zeros(5, dtype=np.float64))


@pytest.mark.parametrize("final_value", [1.0, 0.0, -1.0, float("nan")])
def test_f9_every_final_row_statement_including_nan_is_diagnostically_counted(
    final_value: float,
) -> None:
    request = _request(_basic_rows())
    log, curve = _run(request, ((4, final_value),), _spec())
    assert log.num_trades == 0
    assert log.unexecuted_final_row_intents == 1
    np.testing.assert_array_equal(curve.equity, np.zeros(5, dtype=np.float64))


@pytest.mark.parametrize(
    ("direction", "take_profit", "stop_loss", "expected_reason", "expected_exit"),
    [
        (1.0, 2.0, None, TP, 102.0),
        (-1.0, 2.0, None, TP, 98.0),
        (1.0, None, 2.0, SL, 98.0),
        (-1.0, None, 2.0, SL, 102.0),
    ],
)
def test_f10_f12_long_short_and_one_sided_brackets_are_exact(
    direction: float,
    take_profit: float | None,
    stop_loss: float | None,
    expected_reason: int,
    expected_exit: float,
) -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 103.0, 97.0, 100.0),
        )
    )
    log, _ = _run(
        request,
        ((0, direction),),
        _spec(take_profit_pips=take_profit, stop_loss_pips=stop_loss),
    )
    np.testing.assert_array_equal(log.exit_reason, np.array([expected_reason]))
    np.testing.assert_array_equal(log.exit_price, np.array([expected_exit]))


def test_f11_fresh_entry_can_touch_both_brackets_and_stop_wins() -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 103.0, 97.0, 100.0),
            (100.0, 101.0, 99.0, 100.0),
        )
    )
    log, _ = _run(
        request,
        ((0, 1.0),),
        _spec(take_profit_pips=2.0, stop_loss_pips=2.0),
    )
    np.testing.assert_array_equal(log.entry_key, np.array([1]))
    np.testing.assert_array_equal(log.exit_key, np.array([1]))
    np.testing.assert_array_equal(log.exit_reason, np.array([SL]))
    np.testing.assert_array_equal(log.exit_price, np.array([98.0]))
    np.testing.assert_array_equal(log.ambiguous_exit, np.array([True]))


@pytest.mark.parametrize("direction", [1.0, -1.0], ids=["long", "short"])
def test_ambiguous_brackets_are_inclusive_and_stop_wins_for_both_directions(
    direction: float,
) -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 102.0, 98.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
        )
    )
    log, _ = _run(
        request,
        ((0, direction),),
        _spec(take_profit_pips=2.0, stop_loss_pips=2.0),
    )
    expected_price = 98.0 if direction > 0.0 else 102.0
    np.testing.assert_array_equal(log.exit_reason, np.array([SL], dtype=np.int8))
    np.testing.assert_array_equal(log.exit_price, np.array([expected_price]))
    np.testing.assert_array_equal(log.ambiguous_exit, np.array([True]))


def test_many_ambiguous_trades_produce_the_hand_counted_rate() -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 102.0, 98.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 102.0, 98.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
        )
    )
    log, curve = _run(
        request,
        ((0, 1.0), (2, 1.0), (4, 1.0)),
        _spec(take_profit_pips=2.0, stop_loss_pips=2.0),
    )
    metrics = TradeSummary(min_effective_trades=1).evaluate(log, curve)
    assert log.num_trades == 3
    np.testing.assert_array_equal(log.ambiguous_exit, np.array([True, True, False]))
    assert metrics["ambiguous_exit_rate"].value == 2.0 / 3.0


@pytest.mark.parametrize(
    ("direction", "bar", "expected_reason", "expected_price"),
    [
        (1.0, (97.0, 98.0, 96.0, 97.0), SL, 97.0),
        (1.0, (103.0, 104.0, 102.0, 103.0), TP, 103.0),
        (-1.0, (103.0, 104.0, 102.0, 103.0), SL, 103.0),
        (-1.0, (97.0, 98.0, 96.0, 97.0), TP, 97.0),
    ],
)
def test_g1_g3_gapped_brackets_fill_at_observed_open(
    direction: float,
    bar: tuple[float, float, float, float],
    expected_reason: int,
    expected_price: float,
) -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            bar,
        )
    )
    log, _ = _run(
        request,
        ((0, direction),),
        _spec(take_profit_pips=2.0, stop_loss_pips=2.0),
    )
    np.testing.assert_array_equal(log.exit_reason, np.array([expected_reason]))
    np.testing.assert_array_equal(log.exit_price, np.array([expected_price]))
    np.testing.assert_array_equal(log.gapped_exit, np.array([True]))
    np.testing.assert_array_equal(log.ambiguous_exit, np.array([False]))


def test_target_change_precedes_gap_check_and_owns_the_reason() -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (95.0, 96.0, 94.0, 95.0),
            (95.0, 96.0, 94.0, 95.0),
        )
    )
    log, _ = _run(
        request,
        ((0, 1.0), (1, 0.0)),
        _spec(stop_loss_pips=2.0),
    )
    np.testing.assert_array_equal(log.exit_price, np.array([95.0]))
    np.testing.assert_array_equal(log.exit_reason, np.array([TARGET_CHANGE]))
    np.testing.assert_array_equal(log.gapped_exit, np.array([False]))


def test_non_interruptible_trade_ignores_target_changes_until_take_profit() -> None:
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (101.0, 102.0, 99.0, 101.0),
            (102.0, 111.0, 101.0, 110.0),
            (109.0, 110.0, 108.0, 109.0),
            (108.0, 109.0, 107.0, 108.0),
        )
    )
    log, _ = _run_non_interruptible(
        request,
        ((0, 1.0), (1, -1.0), (2, 0.0), (3, -1.0)),
        _spec(
            execution_id="bracket_non_interruptible_v1",
            take_profit_pips=10.0,
            stop_loss_pips=10.0,
        ),
    )

    np.testing.assert_array_equal(log.entry_key, np.array([1, 4]))
    np.testing.assert_array_equal(log.exit_key, np.array([3, 5]))
    np.testing.assert_array_equal(log.direction, np.array([1, -1], dtype=np.int8))
    np.testing.assert_array_equal(log.exit_reason, np.array([TP, BOUNDARY], dtype=np.int8))
    assert TARGET_CHANGE not in log.exit_reason


def test_each_simulator_refuses_the_other_execution_identity() -> None:
    request = _request(_basic_rows())
    intents = _intents(request, ((0, 1.0),))
    old_spec = _spec()
    new_spec = _spec(execution_id="bracket_non_interruptible_v1")

    with pytest.raises(ContractViolationError, match="bracket_non_interruptible_v1"):
        simulate_bracket_non_interruptible_v1(
            request,
            intents,
            old_spec,
            fold_index=0,
            policy_origin=ORIGIN,
        )
    with pytest.raises(ContractViolationError, match="bracket_single_position_v1"):
        simulate_bracket_single_position_v1(
            request,
            intents,
            new_spec,
            fold_index=0,
            policy_origin=ORIGIN,
        )


def test_cost_arithmetic_and_entry_recognition_match_literal_operand_contract() -> None:
    binding = _binding(
        contract_size=100.0,
        price_units_per_pip=0.01,
        conversion=2.0,
        spread_pips=3.0,
        commission_per_lot=4.0,
        commission_basis=CommissionBasis.PER_SIDE,
    )
    request = _request(
        (
            (10.0, 10.5, 9.5, 10.0),
            (10.0, 10.5, 9.5, 10.25),
            (11.0, 11.5, 10.5, 11.0),
        )
    )
    log, curve = _run(request, ((0, 1.0),), _spec(lot_size=0.5, binding=binding))
    # money/unit = 100 * 0.5 * 2 = 100; gross = 1 * 1 * 100 = 100.
    # spread = 3 * .01 * 100 = 3; commission = 4 * .5 * 2 = 4.
    np.testing.assert_array_equal(log.gross_pnl, np.array([100.0]))
    np.testing.assert_array_equal(log.spread_cost, np.array([3.0]))
    np.testing.assert_array_equal(log.commission_cost, np.array([4.0]))
    np.testing.assert_array_equal(log.net_pnl, np.array([93.0]))
    np.testing.assert_array_equal(curve.equity, np.array([0.0, 18.0, 93.0]))


def test_round_turn_commission_is_exactly_half_per_side_commission() -> None:
    rows = _basic_rows()
    per_side, _ = _run(
        _request(rows),
        ((0, 1.0),),
        _spec(binding=_binding(commission_per_lot=6.0, commission_basis=CommissionBasis.PER_SIDE)),
    )
    round_turn, _ = _run(
        _request(rows),
        ((0, 1.0),),
        _spec(
            binding=_binding(
                commission_per_lot=6.0,
                commission_basis=CommissionBasis.PER_ROUND_TURN,
            )
        ),
    )
    assert per_side.commission_cost[0] == 12.0
    assert round_turn.commission_cost[0] == 6.0


def test_lot_contract_conversion_and_direction_cost_scaling_are_exact() -> None:
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (101.0, 101.5, 100.5, 101.0),
    )

    def run_scaled(
        *,
        lot_size: float = 0.5,
        contract_size: float = 100.0,
        conversion: float = 2.0,
        direction: float = 1.0,
    ) -> TradeLog:
        log, _ = _run(
            _request(rows),
            ((0, direction),),
            _spec(
                lot_size=lot_size,
                binding=_binding(
                    contract_size=contract_size,
                    price_units_per_pip=0.01,
                    conversion=conversion,
                    spread_pips=3.0,
                    commission_per_lot=4.0,
                ),
            ),
        )
        return log

    base = run_scaled()
    doubled_lot = run_scaled(lot_size=1.0)
    doubled_contract = run_scaled(contract_size=200.0)
    doubled_conversion = run_scaled(conversion=4.0)
    short = run_scaled(direction=-1.0)

    np.testing.assert_array_equal(base.gross_pnl, np.array([100.0]))
    np.testing.assert_array_equal(base.spread_cost, np.array([3.0]))
    np.testing.assert_array_equal(base.commission_cost, np.array([2.0]))
    np.testing.assert_array_equal(doubled_lot.gross_pnl, np.array([200.0]))
    np.testing.assert_array_equal(doubled_lot.spread_cost, np.array([6.0]))
    np.testing.assert_array_equal(doubled_lot.commission_cost, np.array([4.0]))
    for changed in (doubled_contract, doubled_conversion):
        np.testing.assert_array_equal(changed.gross_pnl, np.array([200.0]))
        np.testing.assert_array_equal(changed.spread_cost, np.array([6.0]))
        np.testing.assert_array_equal(changed.commission_cost, np.array([2.0]))
    np.testing.assert_array_equal(short.gross_pnl, np.array([-100.0]))
    np.testing.assert_array_equal(short.spread_cost, base.spread_cost)
    np.testing.assert_array_equal(short.commission_cost, base.commission_cost)


def test_overlapping_fold_requests_remain_independent_and_bitwise_equal() -> None:
    request = _request(_basic_rows())
    intents = _intents(request, ((0, 1.0),))
    first_log, first_curve = simulate_bracket_single_position_v1(
        request,
        intents,
        _spec(),
        fold_index=0,
        policy_origin=ORIGIN,
    )
    second_log, second_curve = simulate_bracket_single_position_v1(
        request,
        intents,
        _spec(),
        fold_index=1,
        policy_origin=ORIGIN,
    )
    assert first_log.fold_index == 0
    assert second_log.fold_index == 1
    for name in (
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
        "take_profit_price",
        "stop_loss_price",
    ):
        assert getattr(first_log, name).tobytes() == getattr(second_log, name).tobytes()
    assert first_curve.equity.tobytes() == second_curve.equity.tobytes()


def test_large_native_key_discontinuity_is_just_the_next_observed_row() -> None:
    request = _request(_basic_rows(), keys=(10, 20, 1_000_000, 2_000_000, 9_000_000))
    log, _ = _run(request, ((10, 1.0),), _spec())
    np.testing.assert_array_equal(log.entry_key, np.array([20]))
    np.testing.assert_array_equal(log.exit_key, np.array([9_000_000]))


def test_every_preloop_refusal_happens_before_any_output(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(_basic_rows())
    fractional = _intents(request, ((0, 0.5),))
    calls = 0

    original_open = type(request).open

    def observed_open(self: SimulationRequest) -> NDArray[np.float64]:
        nonlocal calls
        calls += 1
        return cast("NDArray[np.float64]", original_open.__get__(self, type(self)))

    monkeypatch.setattr(type(request), "open", property(observed_open))
    with pytest.raises(ContractViolationError, match="exactly"):
        simulate_bracket_single_position_v1(
            request,
            fractional,
            _spec(),
            fold_index=0,
            policy_origin=ORIGIN,
        )
    assert calls == 0


def test_axis_tag_mismatch_and_out_of_axis_intent_are_refused() -> None:
    request = _request(_basic_rows())
    other_domain = ClockDomain("other.synthetic")
    mismatched = _intents(request, ((0, 1.0),), domain=other_domain)
    with pytest.raises(ContractViolationError, match="unit and clock_domain"):
        simulate_bracket_single_position_v1(
            request,
            mismatched,
            _spec(),
            fold_index=0,
            policy_origin=ORIGIN,
        )

    view = ViewSpan(
        (IDENTITY,),
        TimeUnit.SECONDS,
        DOMAIN,
        NativeTimeKey(0, TimeUnit.SECONDS, DOMAIN),
        NativeTimeKey(0, TimeUnit.SECONDS, DOMAIN),
        NativeTimeKey(99, TimeUnit.SECONDS, DOMAIN),
    )
    outside = _make_intent_frame(
        view=view,
        intent_schema_id="target_exposure_intent_v1",
        keys=np.array([99], dtype=np.int64),
        target_exposure=np.array([1.0], dtype=np.float64),
    )
    with pytest.raises(ContractViolationError, match="subset"):
        simulate_bracket_single_position_v1(
            request,
            outside,
            _spec(),
            fold_index=0,
            policy_origin=ORIGIN,
        )


def test_repeated_calls_are_bitwise_identical_and_folds_start_flat() -> None:
    request = _request(_basic_rows())
    intents = _intents(request, ((0, 1.0), (2, -1.0)))
    spec = _spec(take_profit_pips=10.0, stop_loss_pips=10.0)

    first_log, first_curve = simulate_bracket_single_position_v1(
        request,
        intents,
        spec,
        fold_index=0,
        policy_origin=ORIGIN,
    )
    second_log, second_curve = simulate_bracket_single_position_v1(
        request,
        intents,
        spec,
        fold_index=0,
        policy_origin=ORIGIN,
    )
    for name in (
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
        "take_profit_price",
        "stop_loss_price",
    ):
        assert getattr(first_log, name).tobytes() == getattr(second_log, name).tobytes()
    assert first_curve.equity.tobytes() == second_curve.equity.tobytes()

    next_fold_log, next_fold_curve = simulate_bracket_single_position_v1(
        request,
        _intents(request, ()),
        spec,
        fold_index=1,
        policy_origin=ORIGIN,
    )
    assert next_fold_log.num_trades == 0
    np.testing.assert_array_equal(next_fold_curve.equity, np.zeros(5))


def test_fresh_processes_and_hash_seeds_emit_identical_simulation_bytes() -> None:
    script = r"""
from hashlib import sha256
from tests.test_simulation_engine import ORIGIN, _intents, _request, _spec
from trading_research.simulation import simulate_bracket_single_position_v1

request = _request((
    (100.0, 101.0, 99.0, 100.0),
    (101.0, 102.0, 100.0, 101.0),
    (102.0, 104.0, 100.0, 103.0),
    (103.0, 104.0, 102.0, 103.0),
))
log, curve = simulate_bracket_single_position_v1(
    request,
    _intents(request, ((0, 1.0), (2, -1.0))),
    _spec(take_profit_pips=2.0, stop_loss_pips=2.0),
    fold_index=0,
    policy_origin=ORIGIN,
)
digest = sha256()
for name in (
    'entry_key', 'exit_key', 'direction', 'entry_price', 'exit_price',
    'exit_reason', 'ambiguous_exit', 'gapped_exit', 'gross_pnl',
    'spread_cost', 'commission_cost', 'net_pnl',
):
    value = getattr(log, name)
    digest.update(name.encode('ascii'))
    digest.update(value.dtype.str.encode('ascii'))
    digest.update(value.tobytes())
digest.update(curve.keys.tobytes())
digest.update(curve.equity.tobytes())
print(digest.hexdigest())
"""
    outputs = []
    for seed in ("1", "987654321"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=Path.cwd(),
                env=environment,
            )
        )
    assert outputs[0] == outputs[1]


def test_trade_rows_do_not_change_when_only_strictly_future_prices_change() -> None:
    original = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 103.0, 99.0, 102.0),
            (102.0, 103.0, 101.0, 102.0),
            (102.0, 103.0, 101.0, 102.0),
        )
    )
    perturbed = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 103.0, 99.0, 102.0),
            (500.0, 600.0, 400.0, 550.0),
            (700.0, 800.0, 650.0, 750.0),
        )
    )
    expected, _ = _run(original, ((0, 1.0),), _spec(take_profit_pips=2.0))
    actual, _ = _run(perturbed, ((0, 1.0),), _spec(take_profit_pips=2.0))
    assert expected.exit_key[0] == 2
    for name in (
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
    ):
        assert getattr(expected, name).tobytes() == getattr(actual, name).tobytes()


def test_trade_entries_do_not_change_when_only_later_intents_change() -> None:
    request = _request(_basic_rows())
    first, _ = _run(request, ((0, 1.0),), _spec())
    second, _ = _run(request, ((0, 1.0), (3, -1.0)), _spec())
    assert first.entry_key[0] == second.entry_key[0] == 1
    assert first.entry_price[0].tobytes() == second.entry_price[0].tobytes()


def test_source_keeps_normative_cost_multiplication_order() -> None:
    tree = ast.parse(Path("src/trading_research/simulation/bracket.py").read_text())
    assignments = {
        target.id: ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert assignments["money_per_price_unit"] == (
        "binding.contract_size * spec.lot_size * binding.account_currency_per_quote_currency"
    )
    assert assignments["spread_cost_per_trade"] == (
        "binding.spread_pips * binding.price_units_per_pip * money_per_price_unit"
    )
    assert assignments["gross_pnl"] == (
        "direction * (exit_price - entry_price) * money_per_price_unit"
    )


def test_no_fixture_or_timing_rig_uses_skip_xfail_or_tolerance() -> None:
    tree = ast.parse(Path(__file__).read_text())
    calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not {"pytest.skip", "pytest.xfail", "pytest.approx"}.intersection(calls)
    assert not any(name.endswith("assert_allclose") for name in calls)
