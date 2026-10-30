"""Slice 4B execution values, private carriers, evidence gate, and protocol."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from collections import OrderedDict
from dataclasses import FrozenInstanceError, replace
from typing import TYPE_CHECKING

import numpy as np
import pytest

import trading_research.core.simulation as simulation_contracts
from trading_research.core import (
    VERIFIED_COST_BINDING,
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
    SimulationRequest,
    StageOrigin,
    TimeUnit,
    ToleranceSpec,
    TradeEvaluator,
    TradeExitReason,
    TradeLog,
)
from trading_research.core.simulation import (
    _execution_cost_binding_digest,
    _execution_spec_params_json,
    _make_equity_curve,
    _make_simulation_request,
    _make_trade_log,
    _trade_exit_reason_code,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from trading_research.core import ParamValue

DOMAIN = ClockDomain("test.native")
IDENTITY = DatasetIdentity("TRAIN_TEST", "1" * 64)
ORIGIN = StageOrigin("threshold_v1", 1, {"threshold": 0.5})


def _binding(**overrides: object) -> ExecutionCostBinding:
    values: dict[str, object] = {
        "binding_id": "unverified-audit-input-v1",
        "binding_digest": None,
        "cost_semantics": CostSemanticsStatus.UNVERIFIED,
        "execution_price_model": ExecutionPriceModel.RECORDED_PATH_UNVERIFIED,
        "contract_size": 100.0,
        "price_units_per_pip": 0.01,
        "account_currency_per_quote_currency": 1.0,
        "spread_pips": 20.0,
        "commission_per_lot": 6.0,
        "commission_basis": CommissionBasis.PER_ROUND_TURN,
    }
    values.update(overrides)
    return ExecutionCostBinding(**values)  # type: ignore[arg-type]


def _verified_binding(**overrides: object) -> ExecutionCostBinding:
    values: dict[str, object] = {
        "binding_id": "test-verified-binding-v1",
        "binding_digest": "0" * 64,
        "cost_semantics": CostSemanticsStatus.VERIFIED,
        "execution_price_model": ExecutionPriceModel.SYNTHETIC_DECLARED,
        "contract_size": 100.0,
        "price_units_per_pip": 0.01,
        "account_currency_per_quote_currency": 1.0,
        "spread_pips": 20.0,
        "commission_per_lot": 6.0,
        "commission_basis": CommissionBasis.PER_SIDE,
    }
    values.update(overrides)
    provisional = ExecutionCostBinding(**values)  # type: ignore[arg-type]
    return replace(
        provisional,
        binding_digest=_execution_cost_binding_digest(provisional),
    )


def _spec(**overrides: object) -> ExecutionSpec:
    binding = overrides.pop("binding", None)
    values: dict[str, object] = {
        "execution_id": "bracket_single_position_v1",
        "version": 1,
        "lot_size": 0.01,
        "take_profit_pips": 50.0,
        "stop_loss_pips": 25.0,
        "cost_binding": _binding() if binding is None else binding,
    }
    values.update(overrides)
    return ExecutionSpec(**values)  # type: ignore[arg-type]


def _request(**overrides: object) -> SimulationRequest:
    values: dict[str, object] = {
        "keys": np.array([10, 20, 30], dtype=np.int64),
        "unit": TimeUnit.SECONDS,
        "clock_domain": DOMAIN,
        "source_identities": (IDENTITY,),
        "open_values": np.array([100.0, 101.0, 102.0], dtype=np.float64),
        "high_values": np.array([101.0, 102.0, 103.0], dtype=np.float64),
        "low_values": np.array([99.0, 100.0, 101.0], dtype=np.float64),
        "close_values": np.array([100.5, 101.5, 102.5], dtype=np.float64),
    }
    values.update(overrides)
    return _make_simulation_request(**values)  # type: ignore[arg-type]


def _trade_columns() -> dict[str, np.ndarray[tuple[int], np.dtype[np.generic]]]:
    return {
        "entry_key": np.array([20], dtype=np.int64),
        "exit_key": np.array([30], dtype=np.int64),
        "direction": np.array([1], dtype=np.int8),
        "entry_price": np.array([100.0], dtype=np.float64),
        "exit_price": np.array([102.0], dtype=np.float64),
        "exit_reason": np.array(
            [_trade_exit_reason_code(TradeExitReason.TAKE_PROFIT)],
            dtype=np.int8,
        ),
        "ambiguous_exit": np.array([False], dtype=np.bool_),
        "gapped_exit": np.array([False], dtype=np.bool_),
        "gross_pnl": np.array([2.0], dtype=np.float64),
        "spread_cost": np.array([0.5], dtype=np.float64),
        "commission_cost": np.array([0.25], dtype=np.float64),
        "net_pnl": np.array([1.25], dtype=np.float64),
        "take_profit_price": np.array([np.nan], dtype=np.float64),
        "stop_loss_price": np.array([np.nan], dtype=np.float64),
    }


def _trade_log(**overrides: object) -> TradeLog:
    values: dict[str, object] = {
        "columns": _trade_columns(),
        "unit": TimeUnit.SECONDS,
        "clock_domain": DOMAIN,
        "source_identities": (IDENTITY,),
        "fold_index": 0,
        "policy_origin": ORIGIN,
        "intent_schema_id": "target_exposure_intent_v1",
        "execution_spec": _spec(),
        "execution_rows": 3,
        "intent_rows_long": 1,
        "intent_rows_flat": 0,
        "intent_rows_short": 0,
        "intent_rows_abstain": 0,
        "intent_rows_absent": 2,
        "unexecuted_final_row_intents": 0,
    }
    values.update(overrides)
    return _make_trade_log(**values)  # type: ignore[arg-type]


def _equity_curve(**overrides: object) -> EquityCurve:
    values: dict[str, object] = {
        "keys": np.array([10, 20, 30], dtype=np.int64),
        "equity": np.array([0.0, -0.75, 1.25], dtype=np.float64),
        "unit": TimeUnit.SECONDS,
        "clock_domain": DOMAIN,
        "source_identities": (IDENTITY,),
        "fold_index": 0,
        "policy_origin": ORIGIN,
    }
    values.update(overrides)
    return _make_equity_curve(**values)  # type: ignore[arg-type]


class _TradeEvaluator:
    evaluator_id = "trade_summary_v1"
    version = 1
    params: Mapping[str, ParamValue] = {"min_effective_trades": 1}
    metric_names = ("trade_count",)
    comparison_tolerance: ToleranceSpec | None = None

    def evaluate(
        self,
        trade_log: TradeLog,
        equity_curve: EquityCurve,
    ) -> Mapping[str, MetricValue]:
        raise NotImplementedError


def test_phase4_enums_and_exit_reason_codes_are_closed_and_pinned() -> None:
    assert tuple(CommissionBasis) == (
        CommissionBasis.PER_SIDE,
        CommissionBasis.PER_ROUND_TURN,
    )
    assert tuple(CostSemanticsStatus) == (
        CostSemanticsStatus.UNVERIFIED,
        CostSemanticsStatus.VERIFIED,
    )
    assert tuple(ExecutionPriceModel) == (
        ExecutionPriceModel.RECORDED_PATH_UNVERIFIED,
        ExecutionPriceModel.SYNTHETIC_DECLARED,
    )
    assert tuple(TradeExitReason) == (
        TradeExitReason.TAKE_PROFIT,
        TradeExitReason.STOP_LOSS,
        TradeExitReason.TARGET_CHANGE,
        TradeExitReason.BOUNDARY_FORCED_CLOSE,
    )
    assert {
        reason: _trade_exit_reason_code(reason) for reason in TradeExitReason
    } == {
        TradeExitReason.TAKE_PROFIT: 1,
        TradeExitReason.STOP_LOSS: 2,
        TradeExitReason.TARGET_CHANGE: 3,
        TradeExitReason.BOUNDARY_FORCED_CLOSE: 4,
    }


def test_execution_values_have_no_constructor_defaults_and_are_frozen() -> None:
    for value_type in (ExecutionCostBinding, ExecutionSpec):
        parameters = inspect.signature(value_type).parameters.values()
        assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)

    binding = _binding()
    spec = _spec(binding=binding)
    with pytest.raises(FrozenInstanceError):
        binding.spread_pips = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        spec.lot_size = 1.0  # type: ignore[misc]


def test_unverified_binding_and_spec_keep_every_explicit_field() -> None:
    binding = _binding(spread_pips=0.0, commission_per_lot=0.0)
    spec = _spec(binding=binding, take_profit_pips=None, stop_loss_pips=None)

    assert binding.binding_digest is None
    assert binding.cost_semantics is CostSemanticsStatus.UNVERIFIED
    assert binding.spread_pips == 0.0
    assert spec.execution_id == "bracket_single_position_v1"
    assert spec.take_profit_pips is None
    assert spec.stop_loss_pips is None
    assert spec.cost_binding is binding


def test_execution_spec_accepts_the_non_interruptible_identity() -> None:
    spec = _spec(execution_id="bracket_non_interruptible_v1")
    assert spec.execution_id == "bracket_non_interruptible_v1"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("binding_id", "", "binding_id"),
        ("binding_id", None, "binding_id"),
        ("cost_semantics", "unverified", "cost_semantics"),
        ("execution_price_model", "recorded_path_unverified", "execution_price_model"),
        ("commission_basis", "per_side", "commission_basis"),
        ("contract_size", 0.0, "contract_size"),
        ("contract_size", -1.0, "contract_size"),
        ("contract_size", 1, "contract_size"),
        ("contract_size", float("nan"), "contract_size"),
        ("price_units_per_pip", 0.0, "price_units_per_pip"),
        (
            "account_currency_per_quote_currency",
            float("inf"),
            "account_currency_per_quote_currency",
        ),
        ("spread_pips", -0.01, "spread_pips"),
        ("spread_pips", False, "spread_pips"),
        ("commission_per_lot", -1.0, "commission_per_lot"),
    ],
)
def test_execution_cost_binding_refuses_invalid_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ContractViolationError, match=message):
        _binding(**{field: value})


def test_binding_digest_presence_matches_cost_semantics_exactly() -> None:
    with pytest.raises(ContractViolationError, match="must be None"):
        _binding(binding_digest="0" * 64)

    for digest in (None, "A" * 64, "0" * 63, "g" * 64):
        with pytest.raises(ContractViolationError, match="binding_digest"):
            _binding(
                cost_semantics=CostSemanticsStatus.VERIFIED,
                execution_price_model=ExecutionPriceModel.SYNTHETIC_DECLARED,
                binding_digest=digest,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_id", "other"),
        ("execution_id", 1),
        ("version", 0),
        ("version", False),
        ("lot_size", 0.0),
        ("lot_size", 1),
        ("lot_size", float("inf")),
        ("take_profit_pips", 0.0),
        ("take_profit_pips", -1.0),
        ("take_profit_pips", 1),
        ("stop_loss_pips", float("nan")),
        ("cost_binding", object()),
    ],
)
def test_execution_spec_refuses_every_invalid_field(field: str, value: object) -> None:
    with pytest.raises(ContractViolationError):
        _spec(**{field: value})


def test_canonical_execution_document_nests_the_complete_binding() -> None:
    spec = _spec()
    document = json.loads(_execution_spec_params_json(spec))

    assert set(document) == {
        "cost_binding",
        "execution_id",
        "lot_size",
        "stop_loss_pips",
        "take_profit_pips",
        "version",
    }
    assert set(document["cost_binding"]) == {
        "account_currency_per_quote_currency",
        "binding_digest",
        "binding_id",
        "commission_basis",
        "commission_per_lot",
        "contract_size",
        "cost_semantics",
        "execution_price_model",
        "price_units_per_pip",
        "spread_pips",
    }
    assert document["cost_binding"]["binding_digest"] is None
    assert document["cost_binding"]["cost_semantics"] == "unverified"
    assert document["lot_size"] == {"__float_hex__": 0.01.hex()}


def test_verified_semantics_are_fail_closed_while_repository_binding_is_none() -> None:
    assert VERIFIED_COST_BINDING is None
    assert simulation_contracts.VERIFIED_COST_BINDING is None
    with pytest.raises(ContractViolationError, match="VERIFIED_COST_BINDING is None"):
        _spec(binding=_verified_binding())


def test_recorded_path_can_never_back_verified_semantics() -> None:
    binding = _verified_binding(
        execution_price_model=ExecutionPriceModel.RECORDED_PATH_UNVERIFIED
    )
    with pytest.raises(ContractViolationError, match="cannot back VERIFIED"):
        _spec(binding=binding)


def test_test_injected_verified_binding_accepts_only_exact_digest_bound_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = _verified_binding()
    monkeypatch.setattr(simulation_contracts, "VERIFIED_COST_BINDING", verified)

    assert _spec(binding=verified).cost_binding is verified

    stale = replace(verified, binding_digest="f" * 64)
    with pytest.raises(ContractViolationError, match="recomputed"):
        _spec(binding=stale)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_id", "different-binding"),
        ("execution_price_model", ExecutionPriceModel.RECORDED_PATH_UNVERIFIED),
        ("contract_size", 101.0),
        ("price_units_per_pip", 0.1),
        ("account_currency_per_quote_currency", 2.0),
        ("spread_pips", 21.0),
        ("commission_per_lot", 7.0),
        ("commission_basis", CommissionBasis.PER_ROUND_TURN),
    ],
)
def test_verified_binding_refuses_every_field_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    expected = _verified_binding()
    monkeypatch.setattr(simulation_contracts, "VERIFIED_COST_BINDING", expected)
    actual = _verified_binding(**{field: value})

    with pytest.raises(ContractViolationError):
        _spec(binding=actual)


def test_binding_digest_is_reproducible_excludes_itself_and_changes_with_bound_fields() -> None:
    original = _verified_binding()
    different_declared_digest = replace(original, binding_digest="f" * 64)
    changed = _verified_binding(spread_pips=21.0)

    assert _execution_cost_binding_digest(original) == original.binding_digest
    assert _execution_cost_binding_digest(different_declared_digest) == original.binding_digest
    assert _execution_cost_binding_digest(changed) != original.binding_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_id", "changed-binding-v2"),
        ("cost_semantics", CostSemanticsStatus.UNVERIFIED),
        ("execution_price_model", ExecutionPriceModel.RECORDED_PATH_UNVERIFIED),
        ("contract_size", 101.0),
        ("price_units_per_pip", 0.1),
        ("account_currency_per_quote_currency", 2.0),
        ("spread_pips", 21.0),
        ("commission_per_lot", 7.0),
        ("commission_basis", CommissionBasis.PER_ROUND_TURN),
    ],
)
def test_binding_digest_changes_when_any_bound_field_changes(
    field: str,
    value: object,
) -> None:
    original = _verified_binding()
    if field == "cost_semantics":
        changed = replace(
            original,
            cost_semantics=CostSemanticsStatus.UNVERIFIED,
            binding_digest=None,
        )
    else:
        changed = _verified_binding(**{field: value})
    assert _execution_cost_binding_digest(changed) != _execution_cost_binding_digest(original)


def test_binding_digest_is_stable_across_processes_and_hash_seeds() -> None:
    script = """
from trading_research.core import CommissionBasis, CostSemanticsStatus
from trading_research.core import ExecutionCostBinding, ExecutionPriceModel
from trading_research.core.simulation import _execution_cost_binding_digest
b = ExecutionCostBinding(
    binding_id='stable-v1', binding_digest='0' * 64,
    cost_semantics=CostSemanticsStatus.VERIFIED,
    execution_price_model=ExecutionPriceModel.SYNTHETIC_DECLARED,
    contract_size=100.0, price_units_per_pip=0.01,
    account_currency_per_quote_currency=1.0, spread_pips=20.0,
    commission_per_lot=6.0, commission_basis=CommissionBasis.PER_SIDE,
)
print(_execution_cost_binding_digest(b))
"""
    outputs = []
    for seed in ("1", "987654"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = seed
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script],
                text=True,
                env=environment,
            ).strip()
        )
    assert outputs[0] == outputs[1]
    assert len(outputs[0]) == 64


@pytest.mark.parametrize("carrier", [SimulationRequest, TradeLog, EquityCurve])
def test_platform_carriers_refuse_direct_construction(carrier: type[object]) -> None:
    with pytest.raises(TypeError, match="issued only"):
        carrier()


def test_simulation_request_is_detached_read_only_and_descriptor_isolated() -> None:
    opens = np.array([100.0, 101.0, 102.0], dtype=np.float64)
    request = _request(open_values=opens)
    opens[0] = 999.0

    first = request.open
    with pytest.raises(ValueError, match="read-only"):
        first[0] = 1.0

    assert request.keys.tolist() == [10, 20, 30]
    assert request.unit is TimeUnit.SECONDS
    assert request.clock_domain == DOMAIN
    assert request.source_identities == (IDENTITY,)
    assert request.num_rows == 3
    assert request.open.tolist() == [100.0, 101.0, 102.0]
    assert request.high.tolist() == [101.0, 102.0, 103.0]
    assert request.low.tolist() == [99.0, 100.0, 101.0]
    assert request.close.tolist() == [100.5, 101.5, 102.5]
    assert first is not request.open


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("keys", np.array([10], dtype=np.int64), "at least two"),
        ("keys", np.array([10, 10], dtype=np.int64), "strictly increasing"),
        ("keys", np.array([20, 10], dtype=np.int64), "strictly increasing"),
        ("keys", np.array([10, 20], dtype=np.int32), "int64"),
        (
            "keys",
            np.array(
                [10, 20, 30],
                dtype=np.dtype("int64", metadata={"unit": "seconds"}),
            ),
            "metadata-free",
        ),
        ("keys", np.array([[10, 20]], dtype=np.int64), "one-dimensional"),
        ("open_values", np.array([100.0, 101.0], dtype=np.float64), "length"),
        ("open_values", np.array([100, 101, 102], dtype=np.int64), "float64"),
        (
            "open_values",
            np.array(
                [100.0, 101.0, 102.0],
                dtype=np.dtype("float64", metadata={"side": "unknown"}),
            ),
            "metadata-free",
        ),
        (
            "high_values",
            np.array([101.0, float("nan"), 103.0], dtype=np.float64),
            "non-finite",
        ),
        (
            "low_values",
            np.array([99.0, float("inf"), 101.0], dtype=np.float64),
            "non-finite",
        ),
        (
            "close_values",
            np.array([100.5, 101.5, float("-inf")], dtype=np.float64),
            "non-finite",
        ),
        ("unit", "seconds", "TimeUnit"),
        ("clock_domain", "test.native", "ClockDomain"),
        ("source_identities", (), "source_identities"),
        ("source_identities", (object(),), "DatasetIdentity"),
        ("source_identities", (IDENTITY, IDENTITY), "unique"),
    ],
)
def test_simulation_request_refuses_malformed_axes_and_columns(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ContractViolationError, match=message):
        _request(**{field: value})


@pytest.mark.parametrize(
    ("field", "values", "message"),
    [
        (
            "low_values",
            np.array([101.0, 100.0, 101.0], dtype=np.float64),
            "below low",
        ),
        (
            "high_values",
            np.array([99.0, 102.0, 103.0], dtype=np.float64),
            "above high",
        ),
        (
            "low_values",
            np.array([102.0, 100.0, 101.0], dtype=np.float64),
            "low greater than high",
        ),
    ],
)
def test_simulation_request_refuses_incoherent_bars(
    field: str,
    values: np.ndarray[tuple[int], np.dtype[np.float64]],
    message: str,
) -> None:
    with pytest.raises(ContractViolationError, match=message):
        _request(**{field: values})


def test_trade_log_preserves_closed_schema_metadata_and_trichotomy_counters() -> None:
    original = _trade_columns()
    reordered = OrderedDict(reversed(tuple(original.items())))
    log = _trade_log(columns=reordered)
    original["entry_price"][0] = 999.0

    assert log.entry_key.tolist() == [20]
    assert log.exit_key.tolist() == [30]
    assert log.direction.tolist() == [1]
    assert log.entry_price.tolist() == [100.0]
    assert log.exit_price.tolist() == [102.0]
    assert log.exit_reason.tolist() == [_trade_exit_reason_code(TradeExitReason.TAKE_PROFIT)]
    assert log.ambiguous_exit.tolist() == [False]
    assert log.gapped_exit.tolist() == [False]
    assert log.gross_pnl.tolist() == [2.0]
    assert log.spread_cost.tolist() == [0.5]
    assert log.commission_cost.tolist() == [0.25]
    assert log.net_pnl.tolist() == [1.25]
    assert log.unit is TimeUnit.SECONDS
    assert log.clock_domain == DOMAIN
    assert log.source_identities == (IDENTITY,)
    assert log.fold_index == 0
    assert log.policy_origin is ORIGIN
    assert log.intent_schema_id == "target_exposure_intent_v1"
    assert log.execution_spec == _spec()
    assert log.num_trades == 1
    assert log.execution_rows == 3
    assert (
        log.intent_rows_long,
        log.intent_rows_flat,
        log.intent_rows_short,
        log.intent_rows_abstain,
        log.intent_rows_absent,
        log.unexecuted_final_row_intents,
    ) == (1, 0, 0, 0, 2, 0)
    with pytest.raises(ValueError, match="read-only"):
        log.net_pnl[0] = 0.0
    assert log.net_pnl is not log.net_pnl


def test_trade_log_allows_zero_trades_without_erasing_intent_categories() -> None:
    columns = {
        name: np.array([], dtype=values.dtype) for name, values in _trade_columns().items()
    }
    log = _trade_log(
        columns=columns,
        intent_rows_long=0,
        intent_rows_abstain=1,
        intent_rows_absent=2,
    )
    assert log.num_trades == 0
    assert log.intent_rows_abstain == 1
    assert log.intent_rows_absent == 2


def test_trade_log_accepts_pinned_flag_reason_combinations() -> None:
    ambiguous = _trade_columns()
    ambiguous["exit_reason"] = np.array(
        [_trade_exit_reason_code(TradeExitReason.STOP_LOSS)], dtype=np.int8
    )
    ambiguous["ambiguous_exit"] = np.array([True], dtype=np.bool_)
    assert _trade_log(columns=ambiguous).ambiguous_exit.tolist() == [True]

    gapped = _trade_columns()
    gapped["gapped_exit"] = np.array([True], dtype=np.bool_)
    assert _trade_log(columns=gapped).gapped_exit.tolist() == [True]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_column", "fourteen-column"),
        ("wrong_dtype", "entry_key"),
        ("wrong_flag_dtype", "ambiguous_exit"),
        ("wrong_length", "length"),
        ("nonfinite", "non-finite"),
        ("negative_cost", "non-negative"),
        ("entry_after_exit", "must not follow"),
        ("unordered_entries", "ordered by entry_key"),
        ("bad_direction", "direction"),
        ("bad_reason", "exit_reason"),
        ("ambiguous_gap", "both ambiguous and gapped"),
        ("ambiguous_tp", "ambiguous"),
        ("gapped_target_change", "gapped"),
        ("wrong_net", "net_pnl"),
    ],
)
def test_trade_log_refuses_malformed_closed_columns(mutation: str, message: str) -> None:
    columns = _trade_columns()
    if mutation == "missing_column":
        columns.pop("net_pnl")
    elif mutation == "wrong_dtype":
        columns["entry_key"] = np.array([20], dtype=np.int32)
    elif mutation == "wrong_flag_dtype":
        columns["ambiguous_exit"] = np.array([0], dtype=np.int8)
    elif mutation == "wrong_length":
        columns["exit_key"] = np.array([30, 40], dtype=np.int64)
    elif mutation == "nonfinite":
        columns["gross_pnl"] = np.array([float("nan")], dtype=np.float64)
    elif mutation == "negative_cost":
        columns["spread_cost"] = np.array([-0.5], dtype=np.float64)
    elif mutation == "entry_after_exit":
        columns["entry_key"] = np.array([40], dtype=np.int64)
    elif mutation == "unordered_entries":
        columns = {
            name: np.repeat(values, 2).astype(values.dtype)
            for name, values in columns.items()
        }
        columns["entry_key"] = np.array([30, 20], dtype=np.int64)
        columns["exit_key"] = np.array([30, 30], dtype=np.int64)
    elif mutation == "bad_direction":
        columns["direction"] = np.array([0], dtype=np.int8)
    elif mutation == "bad_reason":
        columns["exit_reason"] = np.array([99], dtype=np.int8)
    elif mutation == "ambiguous_gap":
        columns["ambiguous_exit"] = np.array([True], dtype=np.bool_)
        columns["gapped_exit"] = np.array([True], dtype=np.bool_)
    elif mutation == "ambiguous_tp":
        columns["ambiguous_exit"] = np.array([True], dtype=np.bool_)
    elif mutation == "gapped_target_change":
        columns["exit_reason"] = np.array(
            [_trade_exit_reason_code(TradeExitReason.TARGET_CHANGE)], dtype=np.int8
        )
        columns["gapped_exit"] = np.array([True], dtype=np.bool_)
    elif mutation == "wrong_net":
        columns["net_pnl"] = np.array([1.5], dtype=np.float64)
    else:
        raise AssertionError(f"unknown test mutation {mutation}")

    with pytest.raises(ContractViolationError, match=message):
        _trade_log(columns=columns)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fold_index", -1, "fold_index"),
        ("fold_index", False, "fold_index"),
        ("policy_origin", object(), "policy_origin"),
        ("intent_schema_id", "", "intent_schema_id"),
        ("execution_spec", object(), "execution_spec"),
        ("execution_rows", 1, "execution_rows"),
        ("intent_rows_long", -1, "intent_rows_long"),
        ("intent_rows_absent", False, "intent_rows_absent"),
        ("unexecuted_final_row_intents", 2, "unexecuted_final"),
        ("unit", "seconds", "TimeUnit"),
        ("source_identities", (), "source_identities"),
    ],
)
def test_trade_log_refuses_invalid_frame_fields_and_counters(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ContractViolationError, match=message):
        _trade_log(**{field: value})


def test_trade_log_counters_must_partition_execution_rows() -> None:
    with pytest.raises(ContractViolationError, match="partition"):
        _trade_log(intent_rows_absent=1)
    with pytest.raises(ContractViolationError, match="explicit row"):
        _trade_log(
            intent_rows_long=0,
            intent_rows_absent=3,
            unexecuted_final_row_intents=1,
        )


def test_equity_curve_is_dense_detached_read_only_and_descriptor_isolated() -> None:
    source = np.array([0.0, -0.75, 1.25], dtype=np.float64)
    curve = _equity_curve(equity=source)
    source[0] = 999.0

    assert curve.keys.tolist() == [10, 20, 30]
    assert curve.equity.tolist() == [0.0, -0.75, 1.25]
    assert curve.unit is TimeUnit.SECONDS
    assert curve.clock_domain == DOMAIN
    assert curve.source_identities == (IDENTITY,)
    assert curve.fold_index == 0
    assert curve.policy_origin is ORIGIN
    assert curve.num_rows == 3
    with pytest.raises(ValueError, match="read-only"):
        curve.equity[0] = 1.0
    assert curve.equity is not curve.equity


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("keys", np.array([10], dtype=np.int64), "at least two"),
        ("keys", np.array([20, 10], dtype=np.int64), "strictly increasing"),
        ("keys", np.array([10, 20], dtype=np.int32), "int64"),
        (
            "keys",
            np.array(
                [10, 20, 30],
                dtype=np.dtype("int64", metadata={"unit": "seconds"}),
            ),
            "metadata-free",
        ),
        ("equity", np.array([0.0, 1.0], dtype=np.float64), "length"),
        ("equity", np.array([0, 1, 2], dtype=np.int64), "float64"),
        (
            "equity",
            np.array([0.0, float("nan"), 1.0], dtype=np.float64),
            "non-finite",
        ),
        ("fold_index", -1, "fold_index"),
        ("policy_origin", object(), "policy_origin"),
        ("clock_domain", "test.native", "ClockDomain"),
        ("source_identities", (), "source_identities"),
    ],
)
def test_equity_curve_refuses_invalid_shape_axis_and_metadata(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ContractViolationError, match=message):
        _equity_curve(**{field: value})


def test_trade_evaluator_protocol_has_only_closed_artifact_inputs() -> None:
    assert isinstance(_TradeEvaluator(), TradeEvaluator)
    signature = inspect.signature(TradeEvaluator.evaluate)
    assert tuple(signature.parameters) == ("self", "trade_log", "equity_curve")
    assert "TradeLog" in str(signature.parameters["trade_log"].annotation)
    assert "EquityCurve" in str(signature.parameters["equity_curve"].annotation)
    assert not any(
        hasattr(TradeEvaluator, name)
        for name in ("required_columns", "required_output_columns", "required_outcome_columns")
    )
