"""Release-blocking, test-only Phase-4 harness-inversion canaries."""

from __future__ import annotations

import math

import numpy as np

from tests.test_simulation_engine import ORIGIN, _binding, _intents, _request, _spec
from trading_research.evaluation.trade_reference import TradeSummary
from trading_research.simulation import simulate_bracket_single_position_v1

CANARY_IDS = frozenset({"K1_ORACLE", "K2_RANDOM", "K3_FLAT", "K4_ABSTAIN"})
_K2_MAX_GROSS_DEVIATION_FRACTION_OF_COST = 0.02


def _rows_from_opens(opens: np.ndarray) -> tuple[tuple[float, float, float, float], ...]:
    return tuple(
        (float(value), float(value + 0.25), float(value - 0.25), float(value))
        for value in opens
    )


def test_k1_cheating_oracle_wins_absurdly_when_harness_measures_next_row_fills() -> None:
    opens = np.array(
        [100.0 + (10.0 if index % 2 else -10.0) for index in range(102)],
        dtype=np.float64,
    )
    request = _request(_rows_from_opens(opens))
    statements = tuple(
        (
            index,
            1.0 if opens[index + 2] > opens[index + 1] else -1.0,
        )
        for index in range(opens.size - 2)
    )
    log, curve = simulate_bracket_single_position_v1(
        request,
        _intents(request, statements),
        _spec(),
        fold_index=0,
        policy_origin=ORIGIN,
    )
    metrics = TradeSummary(min_effective_trades=1).evaluate(log, curve)

    assert log.num_trades == 100
    assert metrics["gross_pnl"].value == 2_000.0
    assert metrics["total_cost"].value == 0.0
    assert metrics["net_pnl"].value == 2_000.0
    assert curve.equity[-1] == 2_000.0


def test_k2_seeded_random_policy_loses_within_relative_cost_band() -> None:
    price_rng = np.random.default_rng(20260815)
    policy_rng = np.random.default_rng(15082026)
    increments = price_rng.choice(np.array([-1.0, 1.0]), size=2_000)
    opens = 100.0 + np.concatenate((np.array([0.0]), np.cumsum(increments)))
    request = _request(_rows_from_opens(opens))
    targets = policy_rng.choice(np.array([-1.0, 1.0]), size=opens.size)
    statements = tuple((index, float(value)) for index, value in enumerate(targets))
    spec = _spec(
        take_profit_pips=5.0,
        stop_loss_pips=5.0,
        binding=_binding(spread_pips=1.0, commission_per_lot=1.0),
    )
    log, curve = simulate_bracket_single_position_v1(
        request,
        _intents(request, statements),
        spec,
        fold_index=0,
        policy_origin=ORIGIN,
    )
    metrics = TradeSummary(min_effective_trades=1).evaluate(log, curve)
    total_cost = metrics["total_cost"].value
    net_pnl = metrics["net_pnl"].value

    assert log.num_trades == 989
    assert metrics["gross_pnl"].value == -25.0
    assert total_cost == 1_978.0
    assert total_cost > 0.0
    # The deterministic gross drift is about 1.27% of cost. A 2% ceiling leaves
    # real fixture margin while still requiring the loss to be cost-dominated.
    assert abs(net_pnl + total_cost) <= _K2_MAX_GROSS_DEVIATION_FRACTION_OF_COST * total_cost
    assert net_pnl < 0.0


def test_k3_always_flat_has_no_trades_costs_or_defined_rates() -> None:
    opens = np.full(64, 100.0, dtype=np.float64)
    request = _request(_rows_from_opens(opens))
    statements = tuple((index, 0.0) for index in range(opens.size))
    log, curve = simulate_bracket_single_position_v1(
        request,
        _intents(request, statements),
        _spec(binding=_binding(spread_pips=1.0, commission_per_lot=1.0)),
        fold_index=0,
        policy_origin=ORIGIN,
    )
    metrics = TradeSummary(min_effective_trades=0).evaluate(log, curve)

    assert log.num_trades == 0
    assert metrics["total_cost"].value == 0.0
    assert metrics["net_pnl"].value == 0.0
    assert all(
        math.isnan(metrics[name].value)
        for name in (
            "win_rate",
            "ambiguous_exit_rate",
            "gapped_exit_rate",
            "boundary_forced_close_rate",
        )
    )
    np.testing.assert_array_equal(curve.equity, np.zeros(opens.size))


def test_k4_abstain_and_fully_absent_are_both_flat_but_counted_separately() -> None:
    opens = np.full(64, 100.0, dtype=np.float64)
    request = _request(_rows_from_opens(opens))
    dense_abstain = tuple((index, float("nan")) for index in range(opens.size))
    abstain_log, abstain_curve = simulate_bracket_single_position_v1(
        request,
        _intents(request, dense_abstain),
        _spec(),
        fold_index=0,
        policy_origin=ORIGIN,
    )
    absent_log, absent_curve = simulate_bracket_single_position_v1(
        request,
        _intents(request, ()),
        _spec(),
        fold_index=0,
        policy_origin=ORIGIN,
    )

    assert abstain_log.num_trades == absent_log.num_trades == 0
    assert (abstain_log.intent_rows_abstain, abstain_log.intent_rows_absent) == (64, 0)
    assert (absent_log.intent_rows_abstain, absent_log.intent_rows_absent) == (0, 64)
    assert abstain_log.unexecuted_final_row_intents == 1
    assert absent_log.unexecuted_final_row_intents == 0
    np.testing.assert_array_equal(abstain_curve.equity, absent_curve.equity)


def test_canary_set_is_exact_and_never_registered_in_production() -> None:
    assert {"K1_ORACLE", "K2_RANDOM", "K3_FLAT", "K4_ABSTAIN"} == CANARY_IDS
