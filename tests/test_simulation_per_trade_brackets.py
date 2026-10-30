"""Per-trade bracket semantics and fixed-bracket regression for the one simulator.

The simulator keeps a single identity, ``bracket_single_position_v1``. What is new
is that an intent may now carry its own immutable bracket, and that every trade
records the exact levels the simulator compared its bars against. These tests pin
both halves: that an intent which supplies no bracket still executes exactly as it
did before, and that an intent which supplies one binds it at open and never again.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from tests.test_simulation_engine import (
    BOUNDARY,
    DOMAIN,
    IDENTITY,
    ORIGIN,
    SL,
    TARGET_CHANGE,
    TP,
    _binding,
    _intents,
    _request,
    _run,
    _spec,
)
from trading_research.core import (
    ContractViolationError,
    EquityCurve,
    ExecutionSpec,
    IntentFrame,
    NativeTimeKey,
    TimeUnit,
    TradeLog,
    ViewSpan,
)
from trading_research.core.research_frames import _make_intent_frame
from trading_research.simulation import simulate_bracket_single_position_v1

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from trading_research.core.simulation import SimulationRequest

BRACKETED = "bracketed_exposure_intent_v1"

# One statement is (key, target_exposure, take_profit_distance, stop_loss_distance).
Statement = tuple[int, float, float, float]


def _bracketed_intents(
    request: SimulationRequest,
    statements: tuple[Statement, ...],
) -> IntentFrame:
    keys = request.keys
    view = ViewSpan(
        request.source_identities,
        request.unit,
        request.clock_domain,
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[-1]), request.unit, request.clock_domain),
    )
    return _make_intent_frame(
        view=view,
        intent_schema_id=BRACKETED,
        keys=np.asarray([key for key, _, _, _ in statements], dtype=np.int64),
        target_exposure=np.asarray(
            [exposure for _, exposure, _, _ in statements], dtype=np.float64
        ),
        take_profit_distance=np.asarray(
            [take_profit for _, _, take_profit, _ in statements], dtype=np.float64
        ),
        stop_loss_distance=np.asarray(
            [stop_loss for _, _, _, stop_loss in statements], dtype=np.float64
        ),
    )


def _run_bracketed(
    request: SimulationRequest,
    statements: tuple[Statement, ...],
    spec: ExecutionSpec,
    *,
    fold_index: int = 0,
) -> tuple[TradeLog, EquityCurve]:
    return simulate_bracket_single_position_v1(
        request,
        _bracketed_intents(request, statements),
        spec,
        fold_index=fold_index,
        policy_origin=ORIGIN,
    )


def _rising() -> tuple[tuple[float, float, float, float], ...]:
    return (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 104.0, 96.0, 100.0),
        (100.0, 110.0, 90.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )


# --------------------------------------------------------------------------
# Fixed global brackets: unchanged behaviour, now additionally recorded
# --------------------------------------------------------------------------


def test_fixed_global_long_take_profit_is_unchanged_and_now_recorded() -> None:
    request = _request(_rising())
    log, _curve = _run(request, ((0, 1.0),), _spec(take_profit_pips=2.0, stop_loss_pips=5.0))

    assert log.num_trades == 1
    assert log.direction.tolist() == [1]
    assert log.entry_key.tolist() == [1]
    assert log.entry_price.tolist() == [100.0]
    assert log.exit_reason.tolist() == [TP]
    assert log.exit_price.tolist() == [102.0]
    # The levels are the ones the bars were compared against, recorded verbatim.
    assert log.take_profit_price.tolist() == [102.0]
    assert log.stop_loss_price.tolist() == [95.0]


def test_fixed_global_short_mirrors_the_long_levels() -> None:
    request = _request(_rising())
    log, _curve = _run(request, ((0, -1.0),), _spec(take_profit_pips=2.0, stop_loss_pips=5.0))

    assert log.direction.tolist() == [-1]
    assert log.entry_price.tolist() == [100.0]
    assert log.take_profit_price.tolist() == [98.0]
    assert log.stop_loss_price.tolist() == [105.0]


@pytest.mark.parametrize(
    ("take_profit_pips", "stop_loss_pips"),
    [(None, None), (2.0, None), (None, 5.0)],
)
def test_absent_fixed_side_records_nan_rather_than_a_level(
    take_profit_pips: float | None,
    stop_loss_pips: float | None,
) -> None:
    request = _request(_rising())
    log, _curve = _run(
        request,
        ((0, 1.0),),
        _spec(take_profit_pips=take_profit_pips, stop_loss_pips=stop_loss_pips),
    )
    assert log.num_trades == 1
    assert math.isnan(float(log.take_profit_price[0])) is (take_profit_pips is None)
    assert math.isnan(float(log.stop_loss_price[0])) is (stop_loss_pips is None)


def test_hoisted_pip_conversion_is_bitwise_identical_to_the_original_expression() -> None:
    """The refactor that hoisted the pip conversion must not move a single bit.

    Fixed bracket levels used to be computed as ``price + direction * pips * upp``
    inside the trade-opening path. They are now computed from a distance hoisted
    out of that path. Multiplying by an exactly representable +1/-1 is exact, so
    the two forms must agree bit-for-bit; this walks awkward values to prove it
    rather than asserting the algebra in a comment.
    """
    values = (0.1, 1.0 / 3.0, 1e-8, 12345.678, 0.007, 2.0**-30, 1e7 + 0.3)
    prices = (100.0, 1577.23, 0.0001, 98765.4321)
    for pips in values:
        for upp in values:
            for price in prices:
                for direction in (1, -1):
                    original_take_profit = price + direction * pips * upp
                    hoisted_take_profit = price + direction * (pips * upp)
                    original_stop_loss = price - direction * pips * upp
                    hoisted_stop_loss = price - direction * (pips * upp)
                    assert original_take_profit == hoisted_take_profit
                    assert original_stop_loss == hoisted_stop_loss


def test_a_bracketless_intent_schema_still_reaches_the_fixed_configuration() -> None:
    """The legacy schema carries no channel at all, so every trade uses the spec."""
    request = _request(_rising())
    intents = _intents(request, ((0, 1.0),))
    assert intents.carries_brackets is False
    assert intents.take_profit_distance is None
    assert intents.stop_loss_distance is None

    log, _curve = simulate_bracket_single_position_v1(
        request,
        intents,
        _spec(take_profit_pips=2.0, stop_loss_pips=5.0),
        fold_index=0,
        policy_origin=ORIGIN,
    )
    assert log.take_profit_price.tolist() == [102.0]
    assert log.stop_loss_price.tolist() == [95.0]


# --------------------------------------------------------------------------
# Per-trade brackets
# --------------------------------------------------------------------------


def test_two_consecutive_trades_carry_independent_brackets() -> None:
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 103.0, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 96.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )
    request = _request(rows)
    # Trade A: TP 2.0 / SL 8.0. Trade B: TP 9.0 / SL 3.0.
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 2.0, 8.0), (2, 1.0, 9.0, 3.0)),
        _spec(),
    )

    assert log.num_trades == 2
    assert log.take_profit_price.tolist() == [102.0, 109.0]
    assert log.stop_loss_price.tolist() == [92.0, 97.0]
    assert log.exit_reason.tolist() == [TP, SL]
    assert log.exit_price.tolist() == [102.0, 97.0]


@pytest.mark.parametrize("direction", [1.0, -1.0])
def test_per_trade_levels_are_signed_by_direction(direction: float) -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(request, ((0, direction, 3.0, 7.0),), _spec())
    expected_take_profit = 100.0 + direction * 3.0
    expected_stop_loss = 100.0 - direction * 7.0
    assert log.take_profit_price.tolist() == [expected_take_profit]
    assert log.stop_loss_price.tolist() == [expected_stop_loss]


def test_take_profit_only_leaves_the_stop_absent() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(request, ((0, 1.0, 4.0, math.nan),), _spec())
    assert log.take_profit_price.tolist() == [104.0]
    assert math.isnan(float(log.stop_loss_price[0]))
    assert log.exit_reason.tolist() == [TP]


def test_stop_loss_only_leaves_the_target_absent() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(request, ((0, 1.0, math.nan, 4.0),), _spec())
    assert math.isnan(float(log.take_profit_price[0]))
    assert log.stop_loss_price.tolist() == [96.0]
    assert log.exit_reason.tolist() == [SL]


def test_neither_side_runs_to_the_fold_boundary() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(request, ((0, 1.0, math.nan, math.nan),), _spec())
    assert log.num_trades == 1
    assert log.exit_reason.tolist() == [BOUNDARY]
    assert math.isnan(float(log.take_profit_price[0]))
    assert math.isnan(float(log.stop_loss_price[0]))


def test_reversal_binds_the_bracket_of_the_instruction_that_caused_it() -> None:
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )
    request = _request(rows)
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 2.0, 3.0), (1, -1.0, 6.0, 7.0)),
        _spec(),
    )

    assert log.num_trades == 2
    assert log.direction.tolist() == [1, -1]
    # The closed LONG keeps its own bracket; the new SHORT takes the reversing row's.
    assert log.exit_reason.tolist() == [TARGET_CHANGE, BOUNDARY]
    assert log.take_profit_price.tolist() == [102.0, 94.0]
    assert log.stop_loss_price.tolist() == [97.0, 107.0]


def test_stop_out_then_fresh_re_entry_uses_the_later_instruction() -> None:
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 96.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )
    request = _request(rows)
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 20.0, 2.0), (2, 1.0, 30.0, 9.0)),
        _spec(),
    )

    assert log.num_trades == 2
    assert log.exit_reason.tolist() == [SL, BOUNDARY]
    assert log.stop_loss_price.tolist() == [98.0, 91.0]
    assert log.take_profit_price.tolist() == [120.0, 130.0]


def test_a_previous_bracket_never_carries_into_a_later_trade() -> None:
    """Release guard: the second trade names no bracket, so it must have none.

    This is the failure mode a naive implementation reaches for — keeping the
    levels in simulator state and only overwriting them when a new instruction
    supplies one. Here the first trade is tightly bracketed and the second names
    nothing at all, so any carry-forward turns the boundary close into a stop.
    """
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 97.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 90.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )
    request = _request(rows)
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 50.0, 2.0), (2, 1.0, math.nan, math.nan)),
        _spec(),
    )

    assert log.num_trades == 2
    assert log.exit_reason.tolist() == [SL, BOUNDARY]
    assert float(log.stop_loss_price[0]) == 98.0
    assert float(log.take_profit_price[0]) == 150.0
    assert math.isnan(float(log.stop_loss_price[1]))
    assert math.isnan(float(log.take_profit_price[1]))


def test_a_held_position_instruction_cannot_mutate_an_open_bracket() -> None:
    """A row that repeats the current target is a no-op, bracket included."""
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 97.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )
    request = _request(rows)
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 20.0, 4.0), (1, 1.0, 1.0, 1.0), (2, 1.0, 1.0, 1.0)),
        _spec(),
    )

    # One trade only, still holding its original 96.0 stop; the repeated rows
    # would have stopped it out at 99.0 had they been allowed to rebind.
    assert log.num_trades == 1
    assert log.stop_loss_price.tolist() == [96.0]
    assert log.take_profit_price.tolist() == [120.0]
    assert log.exit_reason.tolist() == [BOUNDARY]


def test_a_later_row_cannot_change_a_bracket_already_bound() -> None:
    """Prefix invariance at the simulator: future instructions cannot reach back."""
    rows = _rising()
    request = _request(rows)
    prefix_only, _ = _run_bracketed(request, ((0, 1.0, 4.0, 4.0),), _spec())
    with_future, _ = _run_bracketed(
        request,
        ((0, 1.0, 4.0, 4.0), (3, -1.0, 1.0, 1.0)),
        _spec(),
    )
    first = 0
    assert prefix_only.take_profit_price[first] == with_future.take_profit_price[first]
    assert prefix_only.stop_loss_price[first] == with_future.stop_loss_price[first]
    assert prefix_only.exit_price[first] == with_future.exit_price[first]
    assert prefix_only.exit_reason[first] == with_future.exit_reason[first]


# --------------------------------------------------------------------------
# The resolution rule between an instruction and the fixed configuration
# --------------------------------------------------------------------------


def test_an_instructed_distance_overrides_the_fixed_configuration() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 3.0, 6.0),),
        _spec(take_profit_pips=2.0, stop_loss_pips=5.0),
    )
    assert log.take_profit_price.tolist() == [103.0]
    assert log.stop_loss_price.tolist() == [94.0]


def test_an_absent_instructed_side_falls_back_to_the_fixed_configuration() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, math.nan, 6.0),),
        _spec(take_profit_pips=2.0, stop_loss_pips=5.0),
    )
    # Take profit defers to the run's fixed 2.0 pips; the stop is instructed.
    assert log.take_profit_price.tolist() == [102.0]
    assert log.stop_loss_price.tolist() == [94.0]


def test_neither_source_leaves_the_side_without_a_bracket() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, math.nan, math.nan),),
        _spec(take_profit_pips=None, stop_loss_pips=4.0),
    )
    assert math.isnan(float(log.take_profit_price[0]))
    assert log.stop_loss_price.tolist() == [96.0]


# --------------------------------------------------------------------------
# Refusals: the intent boundary reinterprets nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -1.0, -0.0, float("inf"), float("-inf")])
def test_malformed_bracket_distances_are_refused_before_any_simulation(bad: float) -> None:
    request = _request(_rising())
    with pytest.raises(ContractViolationError):
        _bracketed_intents(request, ((0, 1.0, bad, 4.0),))
    with pytest.raises(ContractViolationError):
        _bracketed_intents(request, ((0, 1.0, 4.0, bad),))


def test_a_bracketed_schema_requires_both_channels() -> None:
    request = _request(_rising())
    keys = request.keys
    view = ViewSpan(
        request.source_identities,
        request.unit,
        request.clock_domain,
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[-1]), request.unit, request.clock_domain),
    )
    with pytest.raises(ContractViolationError, match="both bracket channels"):
        _make_intent_frame(
            view=view,
            intent_schema_id=BRACKETED,
            keys=np.asarray([0], dtype=np.int64),
            target_exposure=np.asarray([1.0], dtype=np.float64),
            take_profit_distance=np.asarray([2.0], dtype=np.float64),
        )


def test_a_bracketless_schema_refuses_bracket_channels() -> None:
    request = _request(_rising())
    keys = request.keys
    view = ViewSpan(
        request.source_identities,
        request.unit,
        request.clock_domain,
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[-1]), request.unit, request.clock_domain),
    )
    with pytest.raises(ContractViolationError, match="does not carry bracket channels"):
        _make_intent_frame(
            view=view,
            intent_schema_id="target_exposure_intent_v1",
            keys=np.asarray([0], dtype=np.int64),
            target_exposure=np.asarray([1.0], dtype=np.float64),
            take_profit_distance=np.asarray([2.0], dtype=np.float64),
            stop_loss_distance=np.asarray([3.0], dtype=np.float64),
        )


def test_a_bracket_channel_of_the_wrong_length_is_refused() -> None:
    request = _request(_rising())
    keys = request.keys
    view = ViewSpan(
        request.source_identities,
        request.unit,
        request.clock_domain,
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[0]), request.unit, request.clock_domain),
        NativeTimeKey(int(keys[-1]), request.unit, request.clock_domain),
    )
    with pytest.raises(ContractViolationError, match="length must match"):
        _make_intent_frame(
            view=view,
            intent_schema_id=BRACKETED,
            keys=np.asarray([0, 1], dtype=np.int64),
            target_exposure=np.asarray([1.0, 0.0], dtype=np.float64),
            take_profit_distance=np.asarray([2.0], dtype=np.float64),
            stop_loss_distance=np.asarray([3.0, 4.0], dtype=np.float64),
        )


# --------------------------------------------------------------------------
# Trichotomy and boundary behaviour are untouched by the new channels
# --------------------------------------------------------------------------


def test_a_final_row_instruction_and_its_bracket_are_never_executed() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(request, ((4, 1.0, 2.0, 3.0),), _spec())
    assert log.num_trades == 0
    assert log.unexecuted_final_row_intents == 1


def test_an_explicit_abstention_holds_state_and_opens_nothing() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(
        request,
        ((0, math.nan, math.nan, math.nan),),
        _spec(take_profit_pips=2.0),
    )
    assert log.num_trades == 0
    assert log.intent_rows_abstain == 1
    assert log.intent_rows_absent == 4


def test_an_entirely_absent_frame_produces_no_trade() -> None:
    request = _request(_rising())
    log, _curve = _run_bracketed(request, (), _spec(take_profit_pips=2.0))
    assert log.num_trades == 0
    assert log.intent_rows_absent == request.num_rows


def test_a_flat_instruction_closes_without_rebinding() -> None:
    rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    )
    request = _request(rows)
    log, _curve = _run_bracketed(
        request,
        ((0, 1.0, 5.0, 6.0), (1, 0.0, math.nan, math.nan)),
        _spec(),
    )
    assert log.num_trades == 1
    assert log.exit_reason.tolist() == [TARGET_CHANGE]
    assert log.take_profit_price.tolist() == [105.0]
    assert log.stop_loss_price.tolist() == [94.0]


def test_ambiguous_and_gapped_semantics_hold_for_per_trade_brackets() -> None:
    # One bar touches both sides; the stop must win and the trade be flagged.
    ambiguous_rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 105.0, 95.0, 100.0),
    )
    request = _request(ambiguous_rows)
    log, _curve = _run_bracketed(request, ((0, 1.0, 3.0, 3.0),), _spec())
    assert log.exit_reason.tolist() == [SL]
    assert log.ambiguous_exit.tolist() == [True]
    assert log.exit_price.tolist() == [97.0]
    assert log.stop_loss_price.tolist() == [97.0]

    # A gapped open prices the exit at the observed open, not the level.
    gap_rows = (
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (90.0, 91.0, 89.0, 90.0),
    )
    request = _request(gap_rows)
    log, _curve = _run_bracketed(request, ((0, 1.0, math.nan, 4.0),), _spec())
    assert log.exit_reason.tolist() == [SL]
    assert log.gapped_exit.tolist() == [True]
    assert log.exit_price.tolist() == [90.0]
    # The recorded level stays the unreachable bracket, which is the truth of
    # what the simulator was watching for.
    assert log.stop_loss_price.tolist() == [96.0]


def test_fold_isolation_holds_with_per_trade_brackets() -> None:
    request = _request(_rising())
    first, _ = _run_bracketed(request, ((0, 1.0, 4.0, 4.0),), _spec(), fold_index=0)
    second, _ = _run_bracketed(request, ((0, 1.0, 4.0, 4.0),), _spec(), fold_index=3)
    assert first.fold_index == 0
    assert second.fold_index == 3
    assert first.take_profit_price.tobytes() == second.take_profit_price.tobytes()
    assert first.stop_loss_price.tobytes() == second.stop_loss_price.tobytes()


def test_deterministic_replay_is_bitwise_exact() -> None:
    request = _request(_rising())
    statements: tuple[Statement, ...] = (
        (0, 1.0, 3.0, 7.0),
        (2, -1.0, math.nan, 2.0),
    )
    spec = _spec(
        take_profit_pips=2.0,
        binding=_binding(spread_pips=1.0, commission_per_lot=0.5),
    )
    first, first_curve = _run_bracketed(request, statements, spec)
    second, second_curve = _run_bracketed(request, statements, spec)
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
        assert getattr(first, name).tobytes() == getattr(second, name).tobytes()
    assert first_curve.equity.tobytes() == second_curve.equity.tobytes()


def test_a_recorded_level_on_the_wrong_side_of_entry_is_refused() -> None:
    """The carrier refuses a sign error rather than letting it reach a chart."""
    from trading_research.core.simulation import _make_trade_log

    columns: dict[str, NDArray[Any]] = {
        "entry_key": np.array([0], dtype=np.int64),
        "exit_key": np.array([1], dtype=np.int64),
        "direction": np.array([1], dtype=np.int8),
        "entry_price": np.array([100.0], dtype=np.float64),
        "exit_price": np.array([101.0], dtype=np.float64),
        "exit_reason": np.array([BOUNDARY], dtype=np.int8),
        "ambiguous_exit": np.array([False], dtype=np.bool_),
        "gapped_exit": np.array([False], dtype=np.bool_),
        "gross_pnl": np.array([1.0], dtype=np.float64),
        "spread_cost": np.array([0.0], dtype=np.float64),
        "commission_cost": np.array([0.0], dtype=np.float64),
        "net_pnl": np.array([1.0], dtype=np.float64),
        # A long take profit below its entry price is impossible.
        "take_profit_price": np.array([95.0], dtype=np.float64),
        "stop_loss_price": np.array([np.nan], dtype=np.float64),
    }
    with pytest.raises(ContractViolationError, match="beyond entry_price"):
        _make_trade_log(
            columns=columns,
            unit=TimeUnit.SECONDS,
            clock_domain=DOMAIN,
            source_identities=(IDENTITY,),
            fold_index=0,
            policy_origin=ORIGIN,
            intent_schema_id=BRACKETED,
            execution_spec=_spec(),
            execution_rows=2,
            intent_rows_long=1,
            intent_rows_flat=0,
            intent_rows_short=0,
            intent_rows_abstain=0,
            intent_rows_absent=1,
            unexecuted_final_row_intents=0,
        )
