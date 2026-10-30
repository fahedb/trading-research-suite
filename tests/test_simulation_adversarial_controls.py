"""Release-blocking test-only adversarial controls for the Phase-4 simulator."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from tests.test_simulation_engine import (
    BOUNDARY,
    ORIGIN,
    _binding,
    _intents,
    _request,
    _spec,
)
from tests.test_simulation_engine import (
    SL as STOP_LOSS_CODE,
)
from tests.test_simulation_per_trade_brackets import _bracketed_intents
from trading_research.core import ContractViolationError
from trading_research.core.simulation import _make_simulation_request
from trading_research.simulation import simulate_bracket_single_position_v1

if TYPE_CHECKING:
    from collections.abc import Callable

    from trading_research.core import EquityCurve, ExecutionSpec, IntentFrame, TradeLog
    from trading_research.core.simulation import SimulationRequest

    Simulator = Callable[
        [SimulationRequest, IntentFrame, ExecutionSpec],
        tuple[TradeLog, EquityCurve],
    ]

_SIMULATOR_PATH = Path("src/trading_research/simulation/bracket.py")


class _BitMismatchError(AssertionError):
    """Raised when a release-blocking exact comparison catches a control."""


class _SourceControl:
    """One bounded test-only mutation of the real simulator source."""

    replacements: tuple[tuple[str, str], ...] = ()


class DecisionRowFillControl(_SourceControl):
    """Use the decision-row intent immediately instead of one row later."""

    replacements = (
        (
            "prior_intent = float(aligned[index - 1])",
            "prior_intent = float(aligned[index])",
        ),
    )


class TakeProfitWinsAmbiguityControl(_SourceControl):
    """Choose take-profit when both bracket sides touch."""

    replacements = (
        (
            """if stop_touched:
                    if stop_loss_level is None:
                        raise AssertionError("a touched stop must have a level")
                    bracket_exit = (
                        stop_loss_level,
                        _TradeExitReason.STOP_LOSS,
                        bool(take_profit_touched),
                        False,
                    )
                elif take_profit_touched:
                    if take_profit_level is None:
                        raise AssertionError("a touched take profit must have a level")
                    bracket_exit = (
                        take_profit_level,
                        _TradeExitReason.TAKE_PROFIT,
                        False,
                        False,
                    )""",
            """if take_profit_touched:
                    if take_profit_level is None:
                        raise AssertionError("a touched take profit must have a level")
                    bracket_exit = (
                        take_profit_level,
                        _TradeExitReason.TAKE_PROFIT,
                        bool(stop_touched),
                        False,
                    )
                elif stop_touched:
                    if stop_loss_level is None:
                        raise AssertionError("a touched stop must have a level")
                    bracket_exit = (
                        stop_loss_level,
                        _TradeExitReason.STOP_LOSS,
                        False,
                        False,
                    )""",
        ),
    )


class BracketPriceThroughGapControl(_SourceControl):
    """Fill a long stop gap at the unreachable bracket level."""

    replacements = (
        (
            """if position == _LONG:
                    if stop_loss_level is not None and open_price <= stop_loss_level:
                        bracket_exit = (
                            open_price,""",
            """if position == _LONG:
                    if stop_loss_level is not None and open_price <= stop_loss_level:
                        bracket_exit = (
                            stop_loss_level,""",
        ),
    )


class CrossFoldCarryControl:
    """Erase the fold seam by simulating two folds as one request."""

    @staticmethod
    def run(first: SimulationRequest, second: SimulationRequest) -> TradeLog:
        keys = np.concatenate((first.keys, second.keys)).astype(np.int64)
        request = _make_simulation_request(
            keys=keys,
            unit=first.unit,
            clock_domain=first.clock_domain,
            source_identities=first.source_identities,
            open_values=np.concatenate((first.open, second.open)).astype(np.float64),
            high_values=np.concatenate((first.high, second.high)).astype(np.float64),
            low_values=np.concatenate((first.low, second.low)).astype(np.float64),
            close_values=np.concatenate((first.close, second.close)).astype(np.float64),
        )
        log, _curve = simulate_bracket_single_position_v1(
            request,
            _intents(request, ((int(keys[0]), 1.0),)),
            _spec(),
            fold_index=0,
            policy_origin=ORIGIN,
        )
        return log


class SingleCostChargeControl(_SourceControl):
    """Record configured costs but omit them from per-trade net PnL."""

    replacements = (
        (
            "net_pnl = gross_pnl - spread_cost_per_trade - commission_cost_per_trade",
            "net_pnl = gross_pnl",
        ),
    )


class FractionalSignRoundingControl(_SourceControl):
    """Accept a fractional exposure and round it to its sign."""

    replacements = (
        (
            "if finite.size and not _np.isin(finite, (-1.0, 0.0, 1.0)).all():",
            "if False and finite.size and not _np.isin(finite, (-1.0, 0.0, 1.0)).all():",
        ),
        (
            "target = int(prior_intent)",
            "target = _LONG if prior_intent > 0.0 else _SHORT if prior_intent < 0.0 else _FLAT",
        ),
        (
            "explicit = aligned[present]",
            "explicit = _np.sign(aligned[present])",
        ),
    )


class FutureRowExitControl(_SourceControl):
    """Use row R+1's high to decide row R's long take-profit exit."""

    replacements = (
        (
            "and float(high_values[index]) >= take_profit_level",
            "and float(high_values[min(index + 1, request_keys.size - 1)]) >= take_profit_level",
        ),
    )


class BeyondBoundaryFillControl:
    """Append a hidden row so a final-row intent can fill beyond eval_end."""

    @staticmethod
    def run(request: SimulationRequest) -> TradeLog:
        keys = np.append(request.keys, request.keys[-1] + 1).astype(np.int64)
        request_with_future = _make_simulation_request(
            keys=keys,
            unit=request.unit,
            clock_domain=request.clock_domain,
            source_identities=request.source_identities,
            open_values=np.append(request.open, request.open[-1]).astype(np.float64),
            high_values=np.append(request.high, request.high[-1]).astype(np.float64),
            low_values=np.append(request.low, request.low[-1]).astype(np.float64),
            close_values=np.append(request.close, request.close[-1]).astype(np.float64),
        )
        log, _curve = simulate_bracket_single_position_v1(
            request_with_future,
            _intents(request_with_future, ((int(request.keys[-1]), 1.0),)),
            _spec(),
            fold_index=0,
            policy_origin=ORIGIN,
        )
        return log


class BracketCarryForwardControl(_SourceControl):
    """Let a trade inherit the previous trade's bracket when it names none.

    This is the shape the per-trade bracket bug actually takes: keep the levels
    in simulator state, and only overwrite them when a new instruction supplies
    one. Nothing refuses it at any contract boundary, so only a fixture in which
    a later unbracketed trade would be exited by an earlier trade's stop can see
    it happen.
    """

    replacements = (
        (
            """        take_profit_level = None
        stop_loss_level = None
""",
            "",
        ),
        (
            """        if take_profit_distance is None:
            take_profit_level = None
        else:
            take_profit_level = price + direction * take_profit_distance
        if stop_loss_distance is None:
            stop_loss_level = None
        else:
            stop_loss_level = price - direction * stop_loss_distance""",
            """        if take_profit_distance is not None:
            take_profit_level = price + direction * take_profit_distance
        if stop_loss_distance is not None:
            stop_loss_level = price - direction * stop_loss_distance""",
        ),
    )


class BracketMutationControl(_SourceControl):
    """Rebind an already-open trade's stop from a later instruction row."""

    replacements = (
        (
            "        if position != _FLAT and (take_profit_level is not None",
            """        if (
            position != _FLAT
            and index > 0
            and not _math.isnan(float(aligned_stop_loss[index - 1]))
        ):
            stop_loss_level = entry_price - position * float(aligned_stop_loss[index - 1])
        if position != _FLAT and (take_profit_level is not None""",
        ),
    )


CONTROL_CLASSES = frozenset(
    {
        DecisionRowFillControl,
        TakeProfitWinsAmbiguityControl,
        BracketPriceThroughGapControl,
        CrossFoldCarryControl,
        SingleCostChargeControl,
        FractionalSignRoundingControl,
        FutureRowExitControl,
        BeyondBoundaryFillControl,
        BracketCarryForwardControl,
        BracketMutationControl,
    }
)

def _mutant(control: type[_SourceControl]) -> Simulator:
    source = _SIMULATOR_PATH.read_text()
    for old, new in control.replacements:
        assert source.count(old) == 1, f"non-unique mutation anchor for {control.__name__}"
        source = source.replace(old, new, 1)
    namespace: dict[str, object] = {
        "__name__": f"{__name__}.{control.__name__}",
        "__package__": __package__,
    }
    exec(compile(source, str(_SIMULATOR_PATH), "exec"), namespace)
    raw = namespace["simulate_bracket_single_position_v1"]

    def invoke(
        request: SimulationRequest,
        intents: IntentFrame,
        spec: ExecutionSpec,
    ) -> tuple[TradeLog, EquityCurve]:
        function = cast("Callable[..., tuple[TradeLog, EquityCurve]]", raw)
        return function(
            request,
            intents,
            spec,
            fold_index=0,
            policy_origin=ORIGIN,
        )

    return invoke


def _assert_equal(actual: object, expected: object, *, label: str) -> None:
    if actual != expected:
        raise _BitMismatchError(f"{label} differs: {actual!r} != {expected!r}")


def _assert_future_intent_safe(simulator: Simulator) -> None:
    request = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (101.0, 102.0, 100.0, 101.0),
            (102.0, 103.0, 101.0, 102.0),
            (103.0, 104.0, 102.0, 103.0),
        )
    )
    first, _ = simulator(request, _intents(request, ((1, 1.0),)), _spec())
    second, _ = simulator(request, _intents(request, ((1, -1.0),)), _spec())
    first_prefix = first.direction[first.entry_key <= 1].tobytes()
    second_prefix = second.direction[second.entry_key <= 1].tobytes()
    if first_prefix != second_prefix:
        raise _BitMismatchError("a future intent changed a trade entered by the cutoff")


def _assert_future_price_safe(simulator: Simulator) -> None:
    original = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.0, 100.0),
        )
    )
    perturbed = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 103.0, 99.0, 100.0),
        )
    )
    first, _ = simulator(original, _intents(original, ((0, 1.0),)), _spec(take_profit_pips=2.0))
    second, _ = simulator(
        perturbed,
        _intents(perturbed, ((0, 1.0),)),
        _spec(take_profit_pips=2.0),
    )
    first_prefix = first.exit_key[first.exit_key <= 2].tobytes()
    second_prefix = second.exit_key[second.exit_key <= 2].tobytes()
    if first_prefix != second_prefix:
        raise _BitMismatchError("a future price changed a trade exited by the cutoff")


def test_live_simulator_passes_both_exact_timing_rigs() -> None:
    def live(
        request: SimulationRequest,
        intents: IntentFrame,
        spec: ExecutionSpec,
    ) -> tuple[TradeLog, EquityCurve]:
        return simulate_bracket_single_position_v1(
            request,
            intents,
            spec,
            fold_index=0,
            policy_origin=ORIGIN,
        )

    _assert_future_intent_safe(live)
    _assert_future_price_safe(live)


def _observed_ac1_catches() -> frozenset[str]:
    mutant = _mutant(DecisionRowFillControl)
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (101.0, 101.5, 100.5, 101.0),
            (102.0, 102.5, 101.5, 102.0),
        )
    )
    log, _ = mutant(request, _intents(request, ((1, 1.0),)), _spec())
    with pytest.raises(_BitMismatchError, match="entry key differs"):
        _assert_equal(int(log.entry_key[0]), 2, label="entry key")
    with pytest.raises(_BitMismatchError, match="future intent"):
        _assert_future_intent_safe(mutant)
    return frozenset({"F1/F2", "future-intent-rig"})


def test_ac1_decision_row_fill_is_caught_by_fixture_and_future_intent_rig() -> None:
    assert _observed_ac1_catches() == frozenset({"F1/F2", "future-intent-rig"})


def _observed_ac2_catches() -> frozenset[str]:
    mutant = _mutant(TakeProfitWinsAmbiguityControl)
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 103.0, 97.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
        )
    )
    with pytest.raises(ContractViolationError, match=r"ambiguous.*STOP_LOSS"):
        mutant(
            request,
            _intents(request, ((0, 1.0),)),
            _spec(take_profit_pips=2.0, stop_loss_pips=2.0),
        )
    return frozenset({"A3"})


def test_ac2_take_profit_wins_control_is_caught_by_ambiguity_fixture() -> None:
    assert _observed_ac2_catches() == frozenset({"A3"})


def _observed_ac3_catches() -> frozenset[str]:
    mutant = _mutant(BracketPriceThroughGapControl)
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (95.0, 96.0, 94.0, 95.0),
        )
    )
    log, _ = mutant(
        request,
        _intents(request, ((0, 1.0),)),
        _spec(stop_loss_pips=2.0),
    )
    with pytest.raises(_BitMismatchError, match="gap fill differs"):
        _assert_equal(float(log.exit_price[0]), 95.0, label="gap fill")
    return frozenset({"G1/G2"})


def test_ac3_bracket_price_through_gap_is_caught_by_gap_fixture() -> None:
    assert _observed_ac3_catches() == frozenset({"G1/G2"})


def _observed_ac4_catches() -> frozenset[str]:
    first = _request(
        ((100.0, 101.0, 99.0, 100.0), (101.0, 102.0, 100.0, 101.0)),
        keys=(10, 11),
    )
    second = _request(
        ((110.0, 111.0, 109.0, 110.0), (111.0, 112.0, 110.0, 111.0)),
        keys=(20, 21),
    )
    log = CrossFoldCarryControl.run(first, second)
    with pytest.raises(_BitMismatchError, match="fold exit differs"):
        _assert_equal(int(log.exit_key[0]), 11, label="fold exit")
    assert int(log.exit_key[0]) == 21
    assert int(log.exit_reason[0]) == BOUNDARY
    return frozenset({"G6"})


def test_ac4_cross_fold_carry_is_caught_by_fold_boundary_fixture() -> None:
    assert _observed_ac4_catches() == frozenset({"G6"})


def _observed_ac5_catches() -> frozenset[str]:
    mutant = _mutant(SingleCostChargeControl)
    request = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (101.0, 102.0, 100.0, 101.0),
        )
    )
    with pytest.raises(ContractViolationError, match="net_pnl must equal"):
        mutant(
            request,
            _intents(request, ((0, 1.0),)),
            _spec(binding=_binding(spread_pips=1.0, commission_per_lot=2.0)),
        )
    return frozenset({"C1/C2"})


def test_ac5_cost_omission_is_caught_by_literal_cost_fixture() -> None:
    assert _observed_ac5_catches() == frozenset({"C1/C2"})


def _observed_ac6_catches() -> frozenset[str]:
    request = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.0, 100.0),
            (101.0, 102.0, 100.0, 101.0),
        )
    )
    intents = _intents(request, ((0, 0.7),))
    with pytest.raises(ContractViolationError, match="exactly"):
        simulate_bracket_single_position_v1(
            request,
            intents,
            _spec(),
            fold_index=0,
            policy_origin=ORIGIN,
        )
    log, _ = _mutant(FractionalSignRoundingControl)(request, intents, _spec())
    assert log.num_trades == 1
    return frozenset({"R1"})


def test_ac6_fractional_sign_rounding_is_caught_by_preloop_refusal() -> None:
    assert _observed_ac6_catches() == frozenset({"R1"})


def _observed_ac7_catches() -> frozenset[str]:
    with pytest.raises(_BitMismatchError, match="future price"):
        _assert_future_price_safe(_mutant(FutureRowExitControl))
    return frozenset({"future-price-rig"})


def test_ac7_future_row_exit_is_caught_only_by_future_price_rig() -> None:
    assert _observed_ac7_catches() == frozenset({"future-price-rig"})


def _observed_ac8_catches() -> frozenset[str]:
    request = _request(
        (
            (100.0, 101.0, 99.0, 100.0),
            (101.0, 102.0, 100.0, 101.0),
        )
    )
    log = BeyondBoundaryFillControl.run(request)
    with pytest.raises(_BitMismatchError, match="trade count differs"):
        _assert_equal(log.num_trades, 0, label="trade count")
    return frozenset({"F9+bounded-request"})


def test_ac8_beyond_boundary_fill_is_caught_by_bounded_request_fixture() -> None:
    assert _observed_ac8_catches() == frozenset({"F9+bounded-request"})


def _bracketed(
    request: SimulationRequest,
    statements: tuple[tuple[int, float, float, float], ...],
) -> IntentFrame:
    return _bracketed_intents(request, statements)


def _observed_ac9_catches() -> frozenset[str]:
    """The unbracketed second trade must not inherit the first trade's stop."""
    mutant = _mutant(BracketCarryForwardControl)
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 97.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 90.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
        )
    )
    intents = _bracketed(
        request,
        ((0, 1.0, 50.0, 2.0), (2, 1.0, float("nan"), float("nan"))),
    )
    log, _ = mutant(request, intents, _spec())
    with pytest.raises(_BitMismatchError, match="second exit differs"):
        _assert_equal(int(log.exit_reason[1]), BOUNDARY, label="second exit")
    assert int(log.exit_reason[1]) == STOP_LOSS_CODE
    return frozenset({"B1-carry-forward"})


def test_ac9_bracket_carry_forward_is_caught_by_the_unbracketed_re_entry_fixture() -> None:
    assert _observed_ac9_catches() == frozenset({"B1-carry-forward"})


def _observed_ac10_catches() -> frozenset[str]:
    """A held-position row must not move an open trade's stop."""
    mutant = _mutant(BracketMutationControl)
    request = _request(
        (
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 100.5, 97.0, 100.0),
            (100.0, 100.5, 99.5, 100.0),
        )
    )
    intents = _bracketed(
        request,
        ((0, 1.0, 20.0, 4.0), (1, 1.0, 1.0, 1.0), (2, 1.0, 1.0, 1.0)),
    )
    log, _ = mutant(request, intents, _spec())
    with pytest.raises(_BitMismatchError, match="held-row exit differs"):
        _assert_equal(int(log.exit_reason[0]), BOUNDARY, label="held-row exit")
    assert int(log.exit_reason[0]) == STOP_LOSS_CODE
    assert float(log.stop_loss_price[0]) == 99.0
    return frozenset({"B2-open-bracket-mutation"})


def test_ac10_open_bracket_mutation_is_caught_by_the_held_position_fixture() -> None:
    assert _observed_ac10_catches() == frozenset({"B2-open-bracket-mutation"})


def test_control_inventory_and_rig_coverage_are_exact_and_non_vacuous() -> None:
    expected_classes = {
        "DecisionRowFillControl",
        "TakeProfitWinsAmbiguityControl",
        "BracketPriceThroughGapControl",
        "CrossFoldCarryControl",
        "SingleCostChargeControl",
        "FractionalSignRoundingControl",
        "FutureRowExitControl",
        "BeyondBoundaryFillControl",
        "BracketCarryForwardControl",
        "BracketMutationControl",
    }
    assert expected_classes == {control.__name__ for control in CONTROL_CLASSES}
    observed_catches = {
        "AC-1": _observed_ac1_catches(),
        "AC-2": _observed_ac2_catches(),
        "AC-3": _observed_ac3_catches(),
        "AC-4": _observed_ac4_catches(),
        "AC-5": _observed_ac5_catches(),
        "AC-6": _observed_ac6_catches(),
        "AC-7": _observed_ac7_catches(),
        "AC-8": _observed_ac8_catches(),
        "AC-9": _observed_ac9_catches(),
        "AC-10": _observed_ac10_catches(),
    }
    assert {f"AC-{index}" for index in range(1, 11)} == set(observed_catches)
    assert all(observed_catches.values())
    assert observed_catches["AC-7"] == frozenset({"future-price-rig"})
