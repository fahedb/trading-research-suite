"""The two closed reference evaluators for Plane-B simulation artifacts."""

from __future__ import annotations

import math as _math
import typing as _typing

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import MetricValue as _MetricValue
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.simulation import EquityCurve as _EquityCurve
from trading_research.core.simulation import TradeExitReason as _TradeExitReason
from trading_research.core.simulation import TradeLog as _TradeLog
from trading_research.core.simulation import _trade_exit_reason_code
from trading_research.evaluation.registry import TRADE_EVALUATORS as _TRADE_EVALUATORS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    from trading_research.core.features import ParamValue as _ParamValue

__all__ = ["EquityDrawdown", "GrossRSummary", "TradeSummary"]

_TRADE_SUMMARY_METRICS = (
    "trade_count",
    "gross_pnl",
    "total_cost",
    "net_pnl",
    "win_rate",
    "ambiguous_exit_rate",
    "gapped_exit_rate",
    "boundary_forced_close_rate",
)
_GROSS_R_METRICS = (
    "gross_r_total",
    "gross_r_per_trade",
    "gross_r_max_drawdown",
    "choice_0_rate",
    "choice_1_rate",
    "choice_2_rate",
)


def _validate_artifacts(trade_log: object, equity_curve: object) -> None:
    if type(trade_log) is not _TradeLog:
        raise _ContractViolationError("trade evaluator requires an exact TradeLog")
    if type(equity_curve) is not _EquityCurve:
        raise _ContractViolationError("trade evaluator requires an exact EquityCurve")
    if (
        trade_log.unit is not equity_curve.unit
        or trade_log.clock_domain != equity_curve.clock_domain
        or trade_log.source_identities != equity_curve.source_identities
        or trade_log.fold_index != equity_curve.fold_index
        or trade_log.policy_origin != equity_curve.policy_origin
        or trade_log.execution_rows != equity_curve.num_rows
    ):
        raise _ContractViolationError(
            "TradeLog and EquityCurve identity and axis metadata must exactly match"
        )


class TradeSummary:
    """Summarize chronological trades and the three execution-honesty rates."""

    __slots__ = ("_min_effective_trades",)

    def __init__(self, *, min_effective_trades: int) -> None:
        if type(min_effective_trades) is not int or min_effective_trades < 0:
            raise _ContractViolationError("min_effective_trades must be an exact int >= 0")
        self._min_effective_trades = min_effective_trades

    @property
    def evaluator_id(self) -> str:
        """Return the stable Plane-B evaluator identity."""
        return "trade_summary_v1"

    @property
    def version(self) -> int:
        """Return the evaluator contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the sole explicit identity-bearing parameter."""
        return {"min_effective_trades": self._min_effective_trades}

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return the complete closed metric order."""
        return _TRADE_SUMMARY_METRICS

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact metric comparison."""
        return None

    def evaluate(
        self,
        trade_log: _TradeLog,
        equity_curve: _EquityCurve,
    ) -> _Mapping[str, _MetricValue]:
        """Compute literal chronological sums and execution rates."""
        _validate_artifacts(trade_log, equity_curve)
        effective = trade_log.num_trades
        gross_pnl = 0.0
        total_cost = 0.0
        net_pnl = 0.0
        wins = 0
        ambiguous = 0
        gapped = 0
        boundary = 0
        boundary_code = _trade_exit_reason_code(_TradeExitReason.BOUNDARY_FORCED_CLOSE)
        gross_values = trade_log.gross_pnl
        spread_values = trade_log.spread_cost
        commission_values = trade_log.commission_cost
        net_values = trade_log.net_pnl
        ambiguous_values = trade_log.ambiguous_exit
        gapped_values = trade_log.gapped_exit
        exit_reasons = trade_log.exit_reason
        for index in range(effective):
            gross_pnl += float(gross_values[index])
            total_cost += float(spread_values[index] + commission_values[index])
            net_value = float(net_values[index])
            net_pnl += net_value
            wins += int(net_value > 0.0)
            ambiguous += int(ambiguous_values[index])
            gapped += int(gapped_values[index])
            boundary += int(exit_reasons[index] == boundary_code)

        below_minimum = effective < self._min_effective_trades
        nan = float("nan")
        if below_minimum:
            gross_value = total_cost_value = net_value_sum = nan
        else:
            gross_value = gross_pnl
            total_cost_value = total_cost
            net_value_sum = net_pnl
        if effective == 0 or below_minimum:
            win_rate = ambiguous_rate = gapped_rate = boundary_rate = nan
        else:
            win_rate = wins / effective
            ambiguous_rate = ambiguous / effective
            gapped_rate = gapped / effective
            boundary_rate = boundary / effective

        values = (
            float(effective),
            gross_value,
            total_cost_value,
            net_value_sum,
            float(win_rate),
            float(ambiguous_rate),
            float(gapped_rate),
            float(boundary_rate),
        )
        return {
            name: _MetricValue(name, value, effective)
            for name, value in zip(_TRADE_SUMMARY_METRICS, values, strict=True)
        }


class EquityDrawdown:
    """Compute absolute row-close drawdown from the explicit zero baseline."""

    __slots__ = ("_min_effective_rows",)

    def __init__(self, *, min_effective_rows: int) -> None:
        if type(min_effective_rows) is not int or min_effective_rows < 1:
            raise _ContractViolationError("min_effective_rows must be an exact int >= 1")
        self._min_effective_rows = min_effective_rows

    @property
    def evaluator_id(self) -> str:
        """Return the stable Plane-B evaluator identity."""
        return "equity_drawdown_v1"

    @property
    def version(self) -> int:
        """Return the evaluator contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the sole explicit identity-bearing parameter."""
        return {"min_effective_rows": self._min_effective_rows}

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return the sole emitted metric name."""
        return ("max_drawdown",)

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact metric comparison."""
        return None

    def evaluate(
        self,
        trade_log: _TradeLog,
        equity_curve: _EquityCurve,
    ) -> _Mapping[str, _MetricValue]:
        """Scan the dense curve in row order with baseline peak zero."""
        _validate_artifacts(trade_log, equity_curve)
        effective = equity_curve.num_rows
        value = float("nan")
        if effective >= self._min_effective_rows:
            running_peak = 0.0
            max_drawdown = 0.0
            for item in equity_curve.equity:
                equity_value = float(item)
                running_peak = max(running_peak, equity_value)
                max_drawdown = max(max_drawdown, running_peak - equity_value)
            value = max_drawdown
        return {"max_drawdown": _MetricValue("max_drawdown", value, effective)}


class GrossRSummary:
    """Report cost-independent gross R and the frozen three-choice mix."""

    __slots__ = ("_min_effective_trades", "_risk_distance")

    def __init__(self, *, min_effective_trades: int, risk_distance: float) -> None:
        if type(min_effective_trades) is not int or min_effective_trades < 0:
            raise _ContractViolationError("min_effective_trades must be an exact int >= 0")
        if (
            type(risk_distance) is not float
            or not _math.isfinite(risk_distance)
            or risk_distance <= 0.0
        ):
            raise _ContractViolationError("risk_distance must be a finite positive float")
        self._min_effective_trades = min_effective_trades
        self._risk_distance = risk_distance

    @property
    def evaluator_id(self) -> str:
        """Return the stable Plane-B evaluator identity."""
        return "gross_r_summary_v1"

    @property
    def version(self) -> int:
        """Return the evaluator contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the explicit minimum and risk denominator."""
        return {
            "min_effective_trades": self._min_effective_trades,
            "risk_distance": self._risk_distance,
        }

    @property
    def metric_names(self) -> tuple[str, ...]:
        """Return total, per-trade, drawdown and choice-mix metrics."""
        return _GROSS_R_METRICS

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact metric comparison."""
        return None

    def evaluate(
        self,
        trade_log: _TradeLog,
        equity_curve: _EquityCurve,
    ) -> _Mapping[str, _MetricValue]:
        """Accumulate realized price movement in fixed-risk units."""
        _validate_artifacts(trade_log, equity_curve)
        effective = trade_log.num_trades
        total = 0.0
        running = 0.0
        peak = 0.0
        drawdown = 0.0
        choice_counts = [0, 0, 0]
        for index in range(effective):
            direction = int(trade_log.direction[index])
            realized = (
                direction
                * (float(trade_log.exit_price[index]) - float(trade_log.entry_price[index]))
                / self._risk_distance
            )
            total += realized
            running += realized
            peak = max(peak, running)
            drawdown = max(drawdown, peak - running)
            take_profit = float(trade_log.take_profit_price[index])
            if _math.isfinite(take_profit):
                distance = abs(take_profit - float(trade_log.entry_price[index]))
                for choice, expected in enumerate((2.0, 4.0, 8.0)):
                    if _math.isclose(distance, expected, rel_tol=0.0, abs_tol=1e-9):
                        choice_counts[choice] += 1
                        break
                else:
                    raise _ContractViolationError(
                        "gross_r_summary_v1 observed an unsupported bracket distance"
                    )
        below_minimum = effective < self._min_effective_trades
        if effective == 0 or below_minimum:
            values = (float("nan"),) * len(_GROSS_R_METRICS)
        else:
            values = (
                total,
                total / effective,
                drawdown,
                choice_counts[0] / effective,
                choice_counts[1] / effective,
                choice_counts[2] / effective,
            )
        return {
            name: _MetricValue(name, float(value), effective)
            for name, value in zip(_GROSS_R_METRICS, values, strict=True)
        }


_TRADE_EVALUATORS.add("trade_summary_v1", TradeSummary)
_TRADE_EVALUATORS.add("equity_drawdown_v1", EquityDrawdown)
_TRADE_EVALUATORS.add("gross_r_summary_v1", GrossRSummary)
