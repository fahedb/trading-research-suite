"""Zone Trader on the per-trade bracket path — plumbing qualification.

The bracket here is a fixed pair of configured distances, not a stop model. Its
whole purpose is to prove the transport works end to end —
model output -> bracketed intent -> simulator -> recorded levels — while the
signal stays byte-for-byte what ``event_exposure_v1`` already produces. The real
Zone Trader stop and target are per-event quantities the provider computes and
the model does not yet pass through; reaching them is separate work.

The strongest test in this file is the equivalence one: given the same geometry,
the new per-trade path and the old global path must produce the identical trades.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pytest

from tests.test_simulation_engine import (
    BOUNDARY,
    IDENTITY,
    ORIGIN,
    SL,
    TARGET_CHANGE,
    TP,
    _binding,
    _request,
    _spec,
)
from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    EquityCurve,
    ExecutionCostBinding,
    ExecutionSpec,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    TradeLog,
    ViewSpan,
)
from trading_research.core.research_frames import (
    ModelOutputFrame,
    _make_model_output_frame,
)
from trading_research.decisions import DECISION_POLICIES
from trading_research.decisions.reference import (
    EventExposurePolicy,
    ZoneFixedBracketExposurePolicy,
)
from trading_research.simulation import simulate_bracket_single_position_v1

if TYPE_CHECKING:
    from trading_research.core import IntentFrame

# The frozen Zone Trader Plane-B binding records price_units_per_pip = 0.01, so
# the researched TP800/SL400 geometry is exactly 8.00 / 4.00 in price units.
PRICE_UNITS_PER_PIP = 0.01
TAKE_PROFIT_PIPS = 800.0
STOP_LOSS_PIPS = 400.0
TAKE_PROFIT_DISTANCE = 8.0
STOP_LOSS_DISTANCE = 4.0

DOMAIN = ClockDomain("synthetic.execution")

# A path chosen so all three exit reasons occur at three different entry prices,
# which is what makes "the recorded levels differ per trade" observable.
ROWS = (
    (1577.00, 1577.50, 1576.50, 1577.00),
    (1577.00, 1578.00, 1576.00, 1577.50),
    (1580.00, 1586.00, 1579.00, 1585.00),
    (1585.00, 1585.50, 1584.50, 1585.00),
    (1586.00, 1587.00, 1585.00, 1586.00),
    (1586.00, 1590.50, 1585.00, 1590.00),
    (1590.00, 1590.50, 1589.50, 1590.00),
    (1591.00, 1592.00, 1590.00, 1591.00),
    (1591.00, 1592.00, 1590.50, 1591.50),
    (1591.00, 1592.00, 1590.50, 1591.50),
)
SCORES = (1.0, math.nan, math.nan, -1.0, math.nan, math.nan, 1.0, math.nan, math.nan, math.nan)

TRADE_COLUMNS = (
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


def _model_output(scores: tuple[float, ...] = SCORES) -> ModelOutputFrame:
    keys = tuple(range(len(scores)))
    view = ViewSpan(
        (IDENTITY,),
        TimeUnit.SECONDS,
        DOMAIN,
        NativeTimeKey(keys[0], TimeUnit.SECONDS, DOMAIN),
        NativeTimeKey(keys[0], TimeUnit.SECONDS, DOMAIN),
        NativeTimeKey(keys[-1], TimeUnit.SECONDS, DOMAIN),
    )
    return _make_model_output_frame(
        view=view,
        keys=np.asarray(keys, dtype=np.int64),
        columns={"score": np.asarray(scores, dtype=np.float64)},
        origins={"score": StageOrigin("zone_event_gate_v1", 1, {})},
    )


def _zone_binding() -> ExecutionCostBinding:
    """The frozen Zone Trader Plane-B cost inputs, unchanged."""
    return _binding(
        contract_size=100.0,
        price_units_per_pip=PRICE_UNITS_PER_PIP,
        conversion=1.0,
        spread_pips=20.0,
        commission_per_lot=6.0,
    )


def _bracket_policy(
    *,
    take_profit_distance: float | None = TAKE_PROFIT_DISTANCE,
    stop_loss_distance: float | None = STOP_LOSS_DISTANCE,
) -> ZoneFixedBracketExposurePolicy:
    return ZoneFixedBracketExposurePolicy(
        take_profit_distance=take_profit_distance,
        stop_loss_distance=stop_loss_distance,
    )


def _simulate(intents: IntentFrame, spec: ExecutionSpec) -> tuple[TradeLog, EquityCurve]:
    return simulate_bracket_single_position_v1(
        _request(ROWS),
        intents,
        spec,
        fold_index=0,
        policy_origin=ORIGIN,
    )


# --------------------------------------------------------------------------
# Registration and contract
# --------------------------------------------------------------------------


def test_the_policy_is_registered_and_constructible_from_the_registry() -> None:
    assert "zone_fixed_bracket_exposure_v1" in DECISION_POLICIES.names()
    policy = DECISION_POLICIES.create(
        "zone_fixed_bracket_exposure_v1",
        take_profit_distance=TAKE_PROFIT_DISTANCE,
        stop_loss_distance=STOP_LOSS_DISTANCE,
    )
    assert type(policy) is ZoneFixedBracketExposurePolicy
    assert policy.policy_id == "zone_fixed_bracket_exposure_v1"
    assert policy.version == 1
    assert policy.required_output_columns == ("score",)


def test_the_policy_emits_the_bracketed_intent_schema() -> None:
    policy = _bracket_policy()
    assert policy.intent_schema_id == "bracketed_exposure_intent_v1"
    intent = policy.decide(_model_output())
    assert intent.intent_schema_id == "bracketed_exposure_intent_v1"
    assert intent.carries_brackets is True
    assert intent.take_profit_distance is not None
    assert intent.stop_loss_distance is not None


def test_the_bracket_distances_are_identity_bearing_parameters() -> None:
    """The distances must reach experiment identity, not hide in simulator code."""
    assert _bracket_policy().params == {
        "stop_loss_distance": STOP_LOSS_DISTANCE,
        "take_profit_distance": TAKE_PROFIT_DISTANCE,
    }
    other = _bracket_policy(take_profit_distance=6.0)
    assert other.params["take_profit_distance"] == 6.0


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("-inf"), float("nan")])
def test_a_malformed_configured_distance_is_refused_at_construction(bad: float) -> None:
    with pytest.raises(ContractViolationError):
        _bracket_policy(take_profit_distance=bad)
    with pytest.raises(ContractViolationError):
        _bracket_policy(stop_loss_distance=bad)


# --------------------------------------------------------------------------
# Exposure parity — the signal must not move
# --------------------------------------------------------------------------


def test_exposure_is_bitwise_identical_to_event_exposure_v1() -> None:
    """Only the brackets are new. The Zone Trader signal is untouched."""
    model_output = _model_output()
    baseline = EventExposurePolicy().decide(model_output)
    bracketed = _bracket_policy().decide(model_output)

    assert baseline.keys.tobytes() == bracketed.keys.tobytes()
    assert baseline.target_exposure.tobytes() == bracketed.target_exposure.tobytes()
    assert baseline.num_rows == bracketed.num_rows
    # The one intended difference.
    assert baseline.carries_brackets is False
    assert bracketed.carries_brackets is True


@pytest.mark.parametrize(
    "scores",
    [
        (1.0, -1.0, 1.0, -1.0),
        (math.nan, math.nan, math.nan, math.nan),
        (0.0, 0.0, 1.0, 0.0),
        (1.0, 0.0, math.nan, -1.0),
    ],
    ids=["alternating", "all-abstain", "zeros-and-one", "mixed-trichotomy"],
)
def test_exposure_parity_holds_across_the_trichotomy(scores: tuple[float, ...]) -> None:
    model_output = _model_output(scores)
    baseline = EventExposurePolicy().decide(model_output)
    bracketed = _bracket_policy().decide(model_output)
    assert baseline.keys.tobytes() == bracketed.keys.tobytes()
    assert baseline.target_exposure.tobytes() == bracketed.target_exposure.tobytes()


def test_every_emitted_instruction_carries_the_configured_distances() -> None:
    """Every row this policy emits opens a trade, so none may lack a bracket."""
    intent = _bracket_policy().decide(_model_output())
    exposure = intent.target_exposure
    take_profit = intent.take_profit_distance
    stop_loss = intent.stop_loss_distance
    assert take_profit is not None
    assert stop_loss is not None

    assert intent.num_rows == 3
    assert np.isin(exposure, (1.0, -1.0)).all(), "every emitted row is an opening instruction"
    assert np.array_equal(take_profit, np.full(3, TAKE_PROFIT_DISTANCE))
    assert np.array_equal(stop_loss, np.full(3, STOP_LOSS_DISTANCE))


def test_an_unset_side_is_emitted_as_absent_rather_than_invented() -> None:
    intent = _bracket_policy(take_profit_distance=None).decide(_model_output())
    take_profit = intent.take_profit_distance
    stop_loss = intent.stop_loss_distance
    assert take_profit is not None
    assert stop_loss is not None
    assert np.isnan(take_profit).all()
    assert np.array_equal(stop_loss, np.full(intent.num_rows, STOP_LOSS_DISTANCE))


# --------------------------------------------------------------------------
# Through the simulator
# --------------------------------------------------------------------------


def test_recorded_levels_differ_between_trades_although_distances_are_fixed() -> None:
    """Fixed distances, different fills — so the recorded prices must differ.

    This is what demonstrates the levels came from each trade's own binding
    rather than from one run-level pair.
    """
    spec = _spec(take_profit_pips=None, stop_loss_pips=None, binding=_zone_binding())
    log, _curve = _simulate(_bracket_policy().decide(_model_output()), spec)

    assert log.num_trades == 3
    assert log.direction.tolist() == [1, -1, 1]
    assert log.entry_price.tolist() == [1577.0, 1586.0, 1591.0]
    assert log.exit_reason.tolist() == [TP, SL, BOUNDARY]

    take_profit = log.take_profit_price.tolist()
    stop_loss = log.stop_loss_price.tolist()
    assert take_profit == [1585.0, 1578.0, 1599.0]
    assert stop_loss == [1573.0, 1590.0, 1587.0]
    assert len(set(take_profit)) == 3
    assert len(set(stop_loss)) == 3

    # Directionally correct on both sides, LONG and SHORT.
    direction = log.direction.astype(np.float64)
    entry = log.entry_price
    assert np.all(direction * (log.take_profit_price - entry) > 0.0)
    assert np.all(direction * (entry - log.stop_loss_price) > 0.0)


def test_the_qualification_geometry_cannot_reach_the_global_fallback() -> None:
    """With both ExecutionSpec sides null, every bracket must come from intent."""
    spec = _spec(take_profit_pips=None, stop_loss_pips=None, binding=_zone_binding())
    assert spec.take_profit_pips is None
    assert spec.stop_loss_pips is None

    # A bracketless policy against the same spec proves the fallback is empty:
    # no trade can exit on a bracket, because no bracket exists anywhere. The
    # trades still close, but only on a target change or the fold boundary.
    baseline = EventExposurePolicy().decide(_model_output())
    log, _curve = _simulate(baseline, spec)
    assert log.num_trades == 3
    assert log.exit_reason.tolist() == [TARGET_CHANGE, TARGET_CHANGE, BOUNDARY]
    assert TP not in log.exit_reason.tolist()
    assert SL not in log.exit_reason.tolist()
    assert bool(np.isnan(log.take_profit_price).all())
    assert bool(np.isnan(log.stop_loss_price).all())


def test_deterministic_replay_is_bitwise_exact_including_brackets() -> None:
    spec = _spec(take_profit_pips=None, stop_loss_pips=None, binding=_zone_binding())
    intent = _bracket_policy().decide(_model_output())
    first, first_curve = _simulate(intent, spec)
    second, second_curve = _simulate(_bracket_policy().decide(_model_output()), spec)
    for name in (*TRADE_COLUMNS, "take_profit_price", "stop_loss_price"):
        assert getattr(first, name).tobytes() == getattr(second, name).tobytes()
    assert first_curve.equity.tobytes() == second_curve.equity.tobytes()


# --------------------------------------------------------------------------
# Equivalence: the new path must not change trade semantics
# --------------------------------------------------------------------------


def test_per_trade_geometry_equals_global_tp800_sl400_exactly() -> None:
    """The load-bearing proof that the new plumbing changes nothing.

    Mode A is the researched configuration: event_exposure_v1 with TP800/SL400
    in the ExecutionSpec. Mode B is the qualification configuration: the fixed
    bracket policy emitting the same geometry in price units, with both global
    sides null. On identical inputs the two must be indistinguishable in every
    executed quantity.
    """
    binding = _zone_binding()
    model_output = _model_output()

    global_log, global_curve = _simulate(
        EventExposurePolicy().decide(model_output),
        _spec(
            take_profit_pips=TAKE_PROFIT_PIPS,
            stop_loss_pips=STOP_LOSS_PIPS,
            binding=binding,
        ),
    )
    per_trade_log, per_trade_curve = _simulate(
        _bracket_policy().decide(model_output),
        _spec(take_profit_pips=None, stop_loss_pips=None, binding=binding),
    )

    assert global_log.num_trades == per_trade_log.num_trades == 3
    for name in TRADE_COLUMNS:
        assert getattr(global_log, name).tobytes() == getattr(per_trade_log, name).tobytes(), (
            f"{name} differs between global and per-trade brackets"
        )
    assert global_curve.equity.tobytes() == per_trade_curve.equity.tobytes()
    assert global_curve.keys.tobytes() == per_trade_curve.keys.tobytes()

    # The recorded levels agree too, which the old mode could only derive.
    assert global_log.take_profit_price.tobytes() == per_trade_log.take_profit_price.tobytes()
    assert global_log.stop_loss_price.tobytes() == per_trade_log.stop_loss_price.tobytes()

    # The intended differences are provenance only.
    assert global_log.intent_schema_id == "target_exposure_intent_v1"
    assert per_trade_log.intent_schema_id == "bracketed_exposure_intent_v1"
    assert global_log.execution_spec.take_profit_pips == TAKE_PROFIT_PIPS
    assert per_trade_log.execution_spec.take_profit_pips is None


def test_the_pip_conversion_used_by_the_qualification_is_exact() -> None:
    """800 and 400 pips at the frozen binding are exactly 8.00 and 4.00."""
    assert TAKE_PROFIT_PIPS * PRICE_UNITS_PER_PIP == TAKE_PROFIT_DISTANCE
    assert STOP_LOSS_PIPS * PRICE_UNITS_PER_PIP == STOP_LOSS_DISTANCE
