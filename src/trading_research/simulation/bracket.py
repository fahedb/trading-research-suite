"""Pure single-position bracket simulation with closed Phase-4 semantics."""

from __future__ import annotations

import math as _math
import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.simulation import EquityCurve as _EquityCurve
from trading_research.core.simulation import ExecutionSpec as _ExecutionSpec
from trading_research.core.simulation import SimulationRequest as _SimulationRequest
from trading_research.core.simulation import TradeExitReason as _TradeExitReason
from trading_research.core.simulation import TradeLog as _TradeLog
from trading_research.core.simulation import (
    _make_equity_curve,
    _make_trade_log,
    _trade_exit_reason_code,
)

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

__all__ = [
    "SUPPORTED_INTENT_SCHEMA_IDS",
    "simulate_bracket_non_interruptible_v1",
    "simulate_bracket_single_position_v1",
]

SUPPORTED_INTENT_SCHEMA_IDS: _typing.Final[tuple[str, ...]] = (
    "target_exposure_intent_v1",
    "bracketed_exposure_intent_v1",
)

_FLAT = 0
_LONG = 1
_SHORT = -1
_INTERRUPTIBLE_EXECUTION_ID = "bracket_single_position_v1"
_NON_INTERRUPTIBLE_EXECUTION_ID = "bracket_non_interruptible_v1"


def _prevalidate(
    request: _SimulationRequest,
    intents: _IntentFrame,
    spec: _ExecutionSpec,
    *,
    fold_index: int,
    policy_origin: _StageOrigin,
) -> tuple[
    _npt.NDArray[_np.int64],
    _npt.NDArray[_np.int64],
    _npt.NDArray[_np.float64],
    _npt.NDArray[_np.float64] | None,
    _npt.NDArray[_np.float64] | None,
]:
    """Validate every refusal detectable before the first simulated row."""
    if type(request) is not _SimulationRequest:
        raise _ContractViolationError("request must be an exact SimulationRequest")
    if type(intents) is not _IntentFrame:
        raise _ContractViolationError("intents must be an exact IntentFrame")
    if type(spec) is not _ExecutionSpec:
        raise _ContractViolationError("spec must be an exact ExecutionSpec")
    if type(fold_index) is not int or fold_index < 0:
        raise _ContractViolationError("fold_index must be a non-negative Python int")
    if type(policy_origin) is not _StageOrigin:
        raise _ContractViolationError("policy_origin must be an exact StageOrigin")
    if intents.intent_schema_id not in SUPPORTED_INTENT_SCHEMA_IDS:
        raise _ContractViolationError(f"unsupported intent_schema_id {intents.intent_schema_id!r}")
    if intents.unit is not request.unit or intents.clock_domain != request.clock_domain:
        raise _ContractViolationError(
            "request and intents must have identical unit and clock_domain tags"
        )

    request_keys = request.keys
    intent_keys = intents.keys
    intent_values = intents.target_exposure
    if intent_keys.size > 1 and _np.any(intent_keys[1:] <= intent_keys[:-1]):
        raise _ContractViolationError("intent keys must be strictly increasing")
    if (
        intent_keys.size
        and not _np.isin(
            intent_keys,
            request_keys,
            assume_unique=True,
        ).all()
    ):
        raise _ContractViolationError("intent keys must be a subset of the execution axis")
    if _np.isinf(intent_values).any():
        raise _ContractViolationError("target_exposure contains a non-finite non-NaN value")
    finite = intent_values[_np.isfinite(intent_values)]
    if finite.size and not _np.isin(finite, (-1.0, 0.0, 1.0)).all():
        raise _ContractViolationError(
            "finite target_exposure must equal exactly -1.0, 0.0, or +1.0"
        )
    take_profit_distance = intents.take_profit_distance
    stop_loss_distance = intents.stop_loss_distance
    if (take_profit_distance is None) != (stop_loss_distance is None):
        raise _ContractViolationError("intent bracket channels must both be present or absent")
    return (
        request_keys,
        intent_keys,
        intent_values,
        take_profit_distance,
        stop_loss_distance,
    )


def _aligned_bracket_channel(
    request_keys: _npt.NDArray[_np.int64],
    intent_keys: _npt.NDArray[_np.int64],
    values: _npt.NDArray[_np.float64] | None,
) -> _npt.NDArray[_np.float64]:
    """Project one sparse bracket channel onto the execution axis.

    A missing channel and a channel of NaN both align to NaN, which is exactly
    the intent: neither supplies a distance, so both defer to the execution
    configuration under the single resolution rule in ``_resolve_distance``.
    """
    aligned = _np.full(request_keys.size, _np.nan, dtype=_np.float64)
    if values is not None and intent_keys.size:
        aligned[_np.searchsorted(request_keys, intent_keys)] = values
    return aligned


def _intent_axis(
    request_keys: _npt.NDArray[_np.int64],
    intent_keys: _npt.NDArray[_np.int64],
    intent_values: _npt.NDArray[_np.float64],
) -> tuple[_npt.NDArray[_np.float64], tuple[int, int, int, int, int], int]:
    """Align sparse intent statements while retaining trichotomy counters."""
    aligned = _np.full(request_keys.size, _np.nan, dtype=_np.float64)
    present = _np.zeros(request_keys.size, dtype=_np.bool_)
    if intent_keys.size:
        positions = _np.searchsorted(request_keys, intent_keys)
        aligned[positions] = intent_values
        present[positions] = True

    explicit = aligned[present]
    counters = (
        int(_np.count_nonzero(explicit == 1.0)),
        int(_np.count_nonzero(explicit == 0.0)),
        int(_np.count_nonzero(explicit == -1.0)),
        int(_np.count_nonzero(_np.isnan(explicit))),
        int(_np.count_nonzero(~present)),
    )
    unexecuted_final = int(bool(present[-1]))
    return aligned, counters, unexecuted_final


def _resolve_distance(
    instructed: float,
    configured: float | None,
) -> float | None:
    """Resolve one side's bracket distance for exactly one opening instruction.

    The rule is per side, deterministic, and has no hidden third case: an
    instruction that supplies a finite distance wins; otherwise the run's fixed
    execution configuration supplies it; if neither does, that side has no
    bracket at all. Because the resolved level itself is recorded on the trade,
    a completed trade never leaves its bracket in doubt.
    """
    if not _math.isnan(instructed):
        return instructed
    return configured


def _simulate_bracket(
    request: _SimulationRequest,
    intents: _IntentFrame,
    spec: _ExecutionSpec,
    *,
    fold_index: int,
    policy_origin: _StageOrigin,
    expected_execution_id: str,
    interrupt_on_target_change: bool,
) -> tuple[_TradeLog, _EquityCurve]:
    """Simulate one flat-starting fold-policy pair over observed OHLC rows."""
    (
        request_keys,
        intent_keys,
        intent_values,
        intent_take_profit,
        intent_stop_loss,
    ) = _prevalidate(
        request,
        intents,
        spec,
        fold_index=fold_index,
        policy_origin=policy_origin,
    )
    if spec.execution_id != expected_execution_id:
        raise _ContractViolationError(
            f"simulator requires execution_id {expected_execution_id!r}"
        )
    aligned, counters, unexecuted_final = _intent_axis(
        request_keys,
        intent_keys,
        intent_values,
    )
    aligned_take_profit = _aligned_bracket_channel(
        request_keys,
        intent_keys,
        intent_take_profit,
    )
    aligned_stop_loss = _aligned_bracket_channel(
        request_keys,
        intent_keys,
        intent_stop_loss,
    )

    open_values = request.open
    high_values = request.high
    low_values = request.low
    close_values = request.close
    binding = spec.cost_binding
    money_per_price_unit = (
        binding.contract_size * spec.lot_size * binding.account_currency_per_quote_currency
    )
    spread_cost_per_trade = binding.spread_pips * binding.price_units_per_pip * money_per_price_unit
    commission_cost_per_trade = (
        binding.commission_per_lot
        * spec.lot_size
        * (1.0 if binding.commission_basis.value == "per_round_turn" else 2.0)
    )
    # Multiplying by the exactly-representable direction (+1/-1) is exact, so
    # hoisting the pip conversion out of the per-trade path leaves every fixed
    # bracket level bit-identical to the pre-existing arithmetic.
    configured_take_profit_distance = (
        None
        if spec.take_profit_pips is None
        else spec.take_profit_pips * binding.price_units_per_pip
    )
    configured_stop_loss_distance = (
        None
        if spec.stop_loss_pips is None
        else spec.stop_loss_pips * binding.price_units_per_pip
    )

    rows: dict[str, list[int | float | bool]] = {
        "entry_key": [],
        "exit_key": [],
        "direction": [],
        "entry_price": [],
        "exit_price": [],
        "exit_reason": [],
        "ambiguous_exit": [],
        "gapped_exit": [],
        "gross_pnl": [],
        "spread_cost": [],
        "commission_cost": [],
        "net_pnl": [],
        "take_profit_price": [],
        "stop_loss_price": [],
    }
    equity = _np.empty(request_keys.size, dtype=_np.float64)

    position = _FLAT
    entry_key = 0
    entry_price = 0.0
    take_profit_level: float | None = None
    stop_loss_level: float | None = None
    realized_gross = 0.0
    charged = 0.0

    def close_trade(
        *,
        exit_key: int,
        exit_price: float,
        reason: _TradeExitReason,
        ambiguous: bool = False,
        gapped: bool = False,
    ) -> None:
        nonlocal position, realized_gross
        nonlocal take_profit_level, stop_loss_level
        direction = position
        gross_pnl = direction * (exit_price - entry_price) * money_per_price_unit
        net_pnl = gross_pnl - spread_cost_per_trade - commission_cost_per_trade
        rows["entry_key"].append(entry_key)
        rows["exit_key"].append(exit_key)
        rows["direction"].append(direction)
        rows["entry_price"].append(entry_price)
        rows["exit_price"].append(exit_price)
        rows["exit_reason"].append(_trade_exit_reason_code(reason))
        rows["ambiguous_exit"].append(ambiguous)
        rows["gapped_exit"].append(gapped)
        rows["gross_pnl"].append(gross_pnl)
        rows["spread_cost"].append(spread_cost_per_trade)
        rows["commission_cost"].append(commission_cost_per_trade)
        rows["net_pnl"].append(net_pnl)
        rows["take_profit_price"].append(
            _math.nan if take_profit_level is None else take_profit_level
        )
        rows["stop_loss_price"].append(
            _math.nan if stop_loss_level is None else stop_loss_level
        )
        realized_gross += gross_pnl
        position = _FLAT
        # Clearing here is what makes bracket carry-forward structurally
        # impossible: only open_trade can set a level, and it always sets both.
        take_profit_level = None
        stop_loss_level = None

    def open_trade(*, direction: int, key: int, price: float, instruction: int) -> None:
        nonlocal position, entry_key, entry_price
        nonlocal take_profit_level, stop_loss_level, charged
        position = direction
        entry_key = key
        entry_price = price
        take_profit_distance = _resolve_distance(
            float(aligned_take_profit[instruction]),
            configured_take_profit_distance,
        )
        stop_loss_distance = _resolve_distance(
            float(aligned_stop_loss[instruction]),
            configured_stop_loss_distance,
        )
        if take_profit_distance is None:
            take_profit_level = None
        else:
            take_profit_level = price + direction * take_profit_distance
        if stop_loss_distance is None:
            stop_loss_level = None
        else:
            stop_loss_level = price - direction * stop_loss_distance
        charged += spread_cost_per_trade + commission_cost_per_trade

    for index in range(request_keys.size):
        key = int(request_keys[index])
        open_price = float(open_values[index])
        opened_this_row = False

        if index > 0:
            prior_intent = float(aligned[index - 1])
            if not _math.isnan(prior_intent):
                target = int(prior_intent)
                if target != position:
                    if position != _FLAT and interrupt_on_target_change:
                        close_trade(
                            exit_key=key,
                            exit_price=open_price,
                            reason=_TradeExitReason.TARGET_CHANGE,
                        )
                    if target != _FLAT and (
                        position == _FLAT or interrupt_on_target_change
                    ):
                        open_trade(
                            direction=target,
                            key=key,
                            price=open_price,
                            instruction=index - 1,
                        )
                        opened_this_row = True

        if position != _FLAT and (take_profit_level is not None or stop_loss_level is not None):
            bracket_exit: tuple[float, _TradeExitReason, bool, bool] | None = None
            if not opened_this_row:
                if position == _LONG:
                    if stop_loss_level is not None and open_price <= stop_loss_level:
                        bracket_exit = (
                            open_price,
                            _TradeExitReason.STOP_LOSS,
                            False,
                            True,
                        )
                    elif take_profit_level is not None and open_price >= take_profit_level:
                        bracket_exit = (
                            open_price,
                            _TradeExitReason.TAKE_PROFIT,
                            False,
                            True,
                        )
                elif stop_loss_level is not None and open_price >= stop_loss_level:
                    bracket_exit = (
                        open_price,
                        _TradeExitReason.STOP_LOSS,
                        False,
                        True,
                    )
                elif take_profit_level is not None and open_price <= take_profit_level:
                    bracket_exit = (
                        open_price,
                        _TradeExitReason.TAKE_PROFIT,
                        False,
                        True,
                    )

            if bracket_exit is None:
                if position == _LONG:
                    stop_touched = (
                        stop_loss_level is not None and float(low_values[index]) <= stop_loss_level
                    )
                    take_profit_touched = (
                        take_profit_level is not None
                        and float(high_values[index]) >= take_profit_level
                    )
                else:
                    stop_touched = (
                        stop_loss_level is not None and float(high_values[index]) >= stop_loss_level
                    )
                    take_profit_touched = (
                        take_profit_level is not None
                        and float(low_values[index]) <= take_profit_level
                    )
                if stop_touched:
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
                    )

            if bracket_exit is not None:
                exit_price, reason, ambiguous, gapped = bracket_exit
                close_trade(
                    exit_key=key,
                    exit_price=exit_price,
                    reason=reason,
                    ambiguous=ambiguous,
                    gapped=gapped,
                )

        if index == request_keys.size - 1 and position != _FLAT:
            close_trade(
                exit_key=key,
                exit_price=float(close_values[index]),
                reason=_TradeExitReason.BOUNDARY_FORCED_CLOSE,
            )

        unrealized = 0.0
        if position != _FLAT:
            unrealized = (
                position * (float(close_values[index]) - entry_price) * money_per_price_unit
            )
        equity[index] = realized_gross - charged + unrealized

    dtypes = {
        "entry_key": _np.int64,
        "exit_key": _np.int64,
        "direction": _np.int8,
        "entry_price": _np.float64,
        "exit_price": _np.float64,
        "exit_reason": _np.int8,
        "ambiguous_exit": _np.bool_,
        "gapped_exit": _np.bool_,
        "gross_pnl": _np.float64,
        "spread_cost": _np.float64,
        "commission_cost": _np.float64,
        "net_pnl": _np.float64,
        "take_profit_price": _np.float64,
        "stop_loss_price": _np.float64,
    }
    columns: dict[str, _npt.NDArray[_typing.Any]] = {
        name: _np.asarray(values, dtype=dtypes[name]) for name, values in rows.items()
    }
    trade_log = _make_trade_log(
        columns=columns,
        unit=request.unit,
        clock_domain=request.clock_domain,
        source_identities=request.source_identities,
        fold_index=fold_index,
        policy_origin=policy_origin,
        intent_schema_id=intents.intent_schema_id,
        execution_spec=spec,
        execution_rows=request.num_rows,
        intent_rows_long=counters[0],
        intent_rows_flat=counters[1],
        intent_rows_short=counters[2],
        intent_rows_abstain=counters[3],
        intent_rows_absent=counters[4],
        unexecuted_final_row_intents=unexecuted_final,
    )
    equity_curve = _make_equity_curve(
        keys=request_keys,
        equity=equity,
        unit=request.unit,
        clock_domain=request.clock_domain,
        source_identities=request.source_identities,
        fold_index=fold_index,
        policy_origin=policy_origin,
    )
    return trade_log, equity_curve


def simulate_bracket_single_position_v1(
    request: _SimulationRequest,
    intents: _IntentFrame,
    spec: _ExecutionSpec,
    *,
    fold_index: int,
    policy_origin: _StageOrigin,
) -> tuple[_TradeLog, _EquityCurve]:
    """Simulate target-state execution where later intents may interrupt a trade."""
    return _simulate_bracket(
        request,
        intents,
        spec,
        fold_index=fold_index,
        policy_origin=policy_origin,
        expected_execution_id=_INTERRUPTIBLE_EXECUTION_ID,
        interrupt_on_target_change=True,
    )


def simulate_bracket_non_interruptible_v1(
    request: _SimulationRequest,
    intents: _IntentFrame,
    spec: _ExecutionSpec,
    *,
    fold_index: int,
    policy_origin: _StageOrigin,
) -> tuple[_TradeLog, _EquityCurve]:
    """Hold each active trade until its bracket or the evaluation boundary closes it."""
    return _simulate_bracket(
        request,
        intents,
        spec,
        fold_index=fold_index,
        policy_origin=policy_origin,
        expected_execution_id=_NON_INTERRUPTIBLE_EXECUTION_ID,
        interrupt_on_target_change=False,
    )
