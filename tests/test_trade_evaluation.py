"""Slice 4E closed Plane-B evaluator semantics and execution guards."""

from __future__ import annotations

import inspect
import math
from typing import TYPE_CHECKING

import numpy as np
import pytest

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
    MetricValue,
    StageOrigin,
    TimeUnit,
    TradeEvaluator,
    TradeExitReason,
    TradeLog,
)
from trading_research.core.simulation import (
    _make_equity_curve,
    _make_trade_log,
    _trade_exit_reason_code,
)
from trading_research.evaluation import TRADE_EVALUATORS
from trading_research.evaluation.execution import (
    _execute_trade_evaluator,
    _snapshot_trade_evaluator,
)
from trading_research.evaluation.trade_reference import EquityDrawdown, GrossRSummary, TradeSummary

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from numpy.typing import NDArray

    from trading_research.core import ParamValue, ToleranceSpec

DOMAIN = ClockDomain("synthetic.trade-evaluation")
IDENTITY = DatasetIdentity("SYNTHETIC_TRADE_EVALUATION", "5" * 64)
ORIGIN = StageOrigin("synthetic_policy_v1", 1, {})


def _spec() -> ExecutionSpec:
    binding = ExecutionCostBinding(
        binding_id="synthetic-evaluator-v1",
        binding_digest=None,
        cost_semantics=CostSemanticsStatus.UNVERIFIED,
        execution_price_model=ExecutionPriceModel.SYNTHETIC_DECLARED,
        contract_size=1.0,
        price_units_per_pip=1.0,
        account_currency_per_quote_currency=1.0,
        spread_pips=1.0,
        commission_per_lot=0.5,
        commission_basis=CommissionBasis.PER_ROUND_TURN,
    )
    return ExecutionSpec(
        execution_id="bracket_single_position_v1",
        version=1,
        lot_size=1.0,
        take_profit_pips=2.0,
        stop_loss_pips=2.0,
        cost_binding=binding,
    )


def _log(*, empty: bool = False, fold_index: int = 0, bracketed: bool = False) -> TradeLog:
    columns: dict[str, NDArray[Any]]
    if empty:
        columns = {
            "entry_key": np.array([], dtype=np.int64),
            "exit_key": np.array([], dtype=np.int64),
            "direction": np.array([], dtype=np.int8),
            "entry_price": np.array([], dtype=np.float64),
            "exit_price": np.array([], dtype=np.float64),
            "exit_reason": np.array([], dtype=np.int8),
            "ambiguous_exit": np.array([], dtype=np.bool_),
            "gapped_exit": np.array([], dtype=np.bool_),
            "gross_pnl": np.array([], dtype=np.float64),
            "spread_cost": np.array([], dtype=np.float64),
            "commission_cost": np.array([], dtype=np.float64),
            "net_pnl": np.array([], dtype=np.float64),
            "take_profit_price": np.array([], dtype=np.float64),
            "stop_loss_price": np.array([], dtype=np.float64),
        }
    else:
        gross = np.array([10.0, -4.0, 1.5, 8.0], dtype=np.float64)
        spread = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float64)
        commission = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float64)
        columns = {
            "entry_key": np.array([0, 1, 2, 3], dtype=np.int64),
            "exit_key": np.array([0, 2, 3, 4], dtype=np.int64),
            "direction": np.array([1, -1, 1, 1], dtype=np.int8),
            "entry_price": np.array([100.0, 100.0, 100.0, 100.0]),
            "exit_price": np.array([110.0, 104.0, 100.0, 108.0]),
            "exit_reason": np.array(
                [
                    _trade_exit_reason_code(TradeExitReason.STOP_LOSS),
                    _trade_exit_reason_code(TradeExitReason.TAKE_PROFIT),
                    _trade_exit_reason_code(TradeExitReason.TARGET_CHANGE),
                    _trade_exit_reason_code(TradeExitReason.BOUNDARY_FORCED_CLOSE),
                ],
                dtype=np.int8,
            ),
            "ambiguous_exit": np.array([True, False, False, False]),
            "gapped_exit": np.array([False, True, False, False]),
            "gross_pnl": gross,
            "spread_cost": spread,
            "commission_cost": commission,
            "net_pnl": gross - spread - commission,
            # These metric goldens are about money and flags, not brackets, so
            # every trade records an absent bracket on both sides.
            "take_profit_price": (
                np.array([102.0, 96.0, 108.0, 102.0], dtype=np.float64)
                if bracketed
                else np.full(4, np.nan, dtype=np.float64)
            ),
            "stop_loss_price": (
                np.array([96.0, 104.0, 96.0, 96.0], dtype=np.float64)
                if bracketed
                else np.full(4, np.nan, dtype=np.float64)
            ),
        }
    return _make_trade_log(
        columns=columns,
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(IDENTITY,),
        fold_index=fold_index,
        policy_origin=ORIGIN,
        intent_schema_id=(
            "bracketed_exposure_intent_v1" if bracketed else "target_exposure_intent_v1"
        ),
        execution_spec=_spec(),
        execution_rows=5,
        intent_rows_long=0,
        intent_rows_flat=0,
        intent_rows_short=0,
        intent_rows_abstain=0,
        intent_rows_absent=5,
        unexecuted_final_row_intents=0,
    )


def _curve(
    values: tuple[float, ...] = (-2.0, 3.0, 1.0, -4.0, 2.0),
    *,
    fold_index: int = 0,
) -> EquityCurve:
    return _make_equity_curve(
        keys=np.arange(len(values), dtype=np.int64),
        equity=np.asarray(values, dtype=np.float64),
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(IDENTITY,),
        fold_index=fold_index,
        policy_origin=ORIGIN,
    )


def test_trade_evaluator_registry_contains_exactly_the_closed_plugins() -> None:
    assert TRADE_EVALUATORS.names() == {
        "trade_summary_v1",
        "equity_drawdown_v1",
        "gross_r_summary_v1",
    }
    summary = TRADE_EVALUATORS.create(
        "trade_summary_v1",
        min_effective_trades=1,
    )
    drawdown = TRADE_EVALUATORS.create(
        "equity_drawdown_v1",
        min_effective_rows=1,
    )
    gross_r = TRADE_EVALUATORS.create(
        "gross_r_summary_v1",
        min_effective_trades=1,
        risk_distance=4.0,
    )
    assert isinstance(summary, TradeEvaluator)
    assert isinstance(drawdown, TradeEvaluator)
    assert isinstance(gross_r, TradeEvaluator)
    assert type(summary) is TradeSummary
    assert type(drawdown) is EquityDrawdown
    assert type(gross_r) is GrossRSummary


def test_trade_evaluator_parameters_are_explicit_and_exact() -> None:
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in inspect.signature(TradeSummary).parameters.values()
    )
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in inspect.signature(EquityDrawdown).parameters.values()
    )
    assert TradeSummary(min_effective_trades=0).params == {"min_effective_trades": 0}
    assert EquityDrawdown(min_effective_rows=2).params == {"min_effective_rows": 2}
    assert GrossRSummary(min_effective_trades=1, risk_distance=4.0).params == {
        "min_effective_trades": 1,
        "risk_distance": 4.0,
    }
    for value in (-1, True, 1.0):
        with pytest.raises(ContractViolationError, match="min_effective_trades"):
            TradeSummary(min_effective_trades=value)  # type: ignore[arg-type]
    for value in (0, -1, True, 1.0):
        with pytest.raises(ContractViolationError, match="min_effective_rows"):
            EquityDrawdown(min_effective_rows=value)  # type: ignore[arg-type]


def test_trade_summary_matches_hand_computed_chronological_goldens() -> None:
    evaluator = TradeSummary(min_effective_trades=1)
    metrics = evaluator.evaluate(_log(), _curve())

    assert tuple(metrics) == evaluator.metric_names
    expected = {
        "trade_count": 4.0,
        "gross_pnl": 15.5,
        "total_cost": 6.0,
        "net_pnl": 9.5,
        "win_rate": 0.5,
        "ambiguous_exit_rate": 0.25,
        "gapped_exit_rate": 0.25,
        "boundary_forced_close_rate": 0.25,
    }
    assert {name: metric.value for name, metric in metrics.items()} == expected
    assert all(metric.effective_samples == 4 for metric in metrics.values())
    assert evaluator.comparison_tolerance is None


def test_gross_r_summary_matches_hand_computed_trade_path() -> None:
    evaluator = GrossRSummary(min_effective_trades=1, risk_distance=4.0)
    metrics = evaluator.evaluate(_log(bracketed=True), _curve())

    assert {name: metric.value for name, metric in metrics.items()} == {
        "gross_r_total": 3.5,
        "gross_r_per_trade": 0.875,
        "gross_r_max_drawdown": 1.0,
        "choice_0_rate": 0.5,
        "choice_1_rate": 0.25,
        "choice_2_rate": 0.25,
    }


def test_trade_summary_minimum_and_zero_trade_rules_are_exact() -> None:
    below = TradeSummary(min_effective_trades=5).evaluate(_log(), _curve())
    assert below["trade_count"].value == 4.0
    assert all(math.isnan(metric.value) for name, metric in below.items() if name != "trade_count")

    allowed_zero = TradeSummary(min_effective_trades=0).evaluate(_log(empty=True), _curve())
    assert allowed_zero["trade_count"].value == 0.0
    assert allowed_zero["gross_pnl"].value == 0.0
    assert allowed_zero["total_cost"].value == 0.0
    assert allowed_zero["net_pnl"].value == 0.0
    assert all(
        math.isnan(allowed_zero[name].value)
        for name in (
            "win_rate",
            "ambiguous_exit_rate",
            "gapped_exit_rate",
            "boundary_forced_close_rate",
        )
    )

    refused_zero = TradeSummary(min_effective_trades=1).evaluate(_log(empty=True), _curve())
    assert all(
        math.isnan(metric.value) for name, metric in refused_zero.items() if name != "trade_count"
    )


def test_zero_net_trade_is_not_a_win_but_remains_in_denominator() -> None:
    log = _log()
    # The fixed literal has two wins among four trades; the third trade has
    # gross 1.5 and costs 1.5, so its exact zero net is not counted as a win.
    metric = TradeSummary(min_effective_trades=1).evaluate(log, _curve())["win_rate"]
    assert metric.value == 0.5
    assert metric.effective_samples == 4


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((-2.0, 3.0, 1.0, -4.0, 2.0), 7.0),
        ((-1.0, -2.0, -3.0, -2.0, -1.0), 3.0),
        ((0.0, 1.0, 2.0, 3.0, 4.0), 0.0),
    ],
)
def test_equity_drawdown_includes_zero_baseline_and_scans_row_closes(
    values: tuple[float, ...],
    expected: float,
) -> None:
    metric = EquityDrawdown(min_effective_rows=1).evaluate(_log(), _curve(values))["max_drawdown"]
    assert metric.value == expected
    assert metric.effective_samples == 5


def test_equity_drawdown_below_minimum_is_nan() -> None:
    metric = EquityDrawdown(min_effective_rows=6).evaluate(_log(), _curve())["max_drawdown"]
    assert math.isnan(metric.value)
    assert metric.effective_samples == 5


def test_trade_evaluators_refuse_mismatched_artifact_identity() -> None:
    with pytest.raises(ContractViolationError, match="metadata"):
        TradeSummary(min_effective_trades=1).evaluate(
            _log(fold_index=0),
            _curve(fold_index=1),
        )
    with pytest.raises(ContractViolationError, match="metadata"):
        EquityDrawdown(min_effective_rows=1).evaluate(
            _log(fold_index=0),
            _curve(fold_index=1),
        )


class _BadNames:
    evaluator_id = "bad_trade_evaluator_v1"
    version = 1
    params: Mapping[str, ParamValue] = {}
    metric_names = ("declared",)
    comparison_tolerance: ToleranceSpec | None = None

    def evaluate(
        self,
        trade_log: TradeLog,
        equity_curve: EquityCurve,
    ) -> Mapping[str, MetricValue]:
        return {"undeclared": MetricValue("undeclared", 1.0, 1)}


class _TooManySamples(_BadNames):
    metric_names = ("declared",)

    def evaluate(
        self,
        trade_log: TradeLog,
        equity_curve: EquityCurve,
    ) -> Mapping[str, MetricValue]:
        return {"declared": MetricValue("declared", 1.0, equity_curve.num_rows + 1)}


def test_plane_b_execution_revalidates_names_values_and_effective_rows() -> None:
    bad_names = _BadNames()
    with pytest.raises(ContractViolationError, match="names and order"):
        _execute_trade_evaluator(
            bad_names,
            _snapshot_trade_evaluator(bad_names),
            trade_log=_log(),
            equity_curve=_curve(),
        )

    too_many = _TooManySamples()
    with pytest.raises(ContractViolationError, match="exceeds rows"):
        _execute_trade_evaluator(
            too_many,
            _snapshot_trade_evaluator(too_many),
            trade_log=_log(),
            equity_curve=_curve(),
        )


def test_plane_b_execution_returns_declared_metric_order() -> None:
    evaluator = TradeSummary(min_effective_trades=1)
    metrics = _execute_trade_evaluator(
        evaluator,
        _snapshot_trade_evaluator(evaluator),
        trade_log=_log(),
        equity_curve=_curve(),
    )
    assert tuple(metric.name for metric in metrics) == evaluator.metric_names
    assert tuple(metric.value for metric in metrics) == (
        4.0,
        15.5,
        6.0,
        9.5,
        0.5,
        0.25,
        0.25,
        0.25,
    )
