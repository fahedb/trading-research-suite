"""Plane-A evaluation orchestration and honest inventory construction."""

from __future__ import annotations

import json as _json
import re as _re
import struct as _struct
import typing as _typing
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum

from trading_research.artifacts.container import _decode_params
from trading_research.artifacts.identity import _canonical_identity_json
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.research import MetricValue as _MetricValue
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import OutcomeFrame as _OutcomeFrame
from trading_research.core.simulation import CostSemanticsStatus as _CostSemanticsStatus
from trading_research.core.simulation import EquityCurve as _EquityCurve
from trading_research.core.simulation import ExecutionSpec as _ExecutionSpec
from trading_research.core.simulation import TradeLog as _TradeLog
from trading_research.core.simulation import _execution_spec_params_json
from trading_research.decisions.execution import _snapshot_policy
from trading_research.evaluation.execution import (
    _EvaluatorPlan,
    _execute_evaluator,
    _execute_trade_evaluator,
    _snapshot_evaluator,
    _snapshot_trade_evaluator,
    _TradeEvaluatorPlan,
    _validate_artifacts,
    _validate_evaluator_columns,
)
from trading_research.experiments.config import _optional_execution_spec
from trading_research.outcomes.execution import _validated_spec

if _typing.TYPE_CHECKING:
    from trading_research.core.interfaces import OutcomeSpec as _OutcomeSpec

__all__: list[str] = []

_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_FLOAT64_BITS = _re.compile(r"^[0-9a-f]{16}$")
_INVENTORY_SCHEMA_V1 = "plane-a-evaluation-inventory-v1"
_INVENTORY_SCHEMA = "research-evaluation-inventory-v2"


class _EvaluationPlane(_StrEnum):
    A = "A"
    B = "B"


class _RunOutcomeClass(_StrEnum):
    EVALUATED = "evaluated"
    DESCRIPTIVE = "descriptive"
    PRE_EVALUATION_FAILURE = "pre_evaluation_failure"


@_dataclass(frozen=True, slots=True)
class _FittedOutput:
    _fitted_state_identity_key: str
    _model_output_artifact_identity_key: str
    _frame: _ModelOutputFrame

    def __post_init__(self) -> None:
        for value, label in (
            (self._fitted_state_identity_key, "fitted-state identity"),
            (self._model_output_artifact_identity_key, "model-output artifact identity"),
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise _ContractViolationError(f"{label} must be lowercase SHA-256")
        if type(self._frame) is not _ModelOutputFrame:
            raise _ContractViolationError("FittedOutput requires an exact ModelOutputFrame")


@_dataclass(frozen=True, slots=True)
class _OutcomeTarget:
    _spec: _OutcomeSpec
    _frame: _OutcomeFrame

    def __post_init__(self) -> None:
        spec = _validated_spec(self._spec)
        if type(self._frame) is not _OutcomeFrame:
            raise _ContractViolationError("OutcomeTarget requires an exact OutcomeFrame")
        if self._frame.column_names != spec.target_column_names:
            raise _ContractViolationError(
                "OutcomeFrame columns must exactly match its OutcomeSpec declaration"
            )
        for name in self._frame.column_names:
            origin = self._frame.origin(name)
            if (
                origin.plugin_id != spec.outcome_id
                or origin.version != spec.version
                or _canonical_params_json(origin.params) != _canonical_params_json(spec.params)
            ):
                raise _ContractViolationError(
                    f"OutcomeFrame origin for {name!r} differs from its OutcomeSpec"
                )


@_dataclass(frozen=True, slots=True)
class _PolicyExecutionRecord:
    _origin: _StageOrigin
    _intent_schema_id: str
    _intent_rows: int
    _first_key: int | None
    _last_key: int | None


@_dataclass(frozen=True, slots=True)
class _EvaluationInventoryEntry:
    _fitted_state_identity_key: str
    _model_output_artifact_identity_key: str
    _evaluated_output_columns: tuple[str, ...]
    _outcome_origin: _StageOrigin
    _horizon_rows: int
    _decision_delay_rows: int
    _evaluator_origin: _StageOrigin
    _metric_names: tuple[str, ...]
    _comparison_tolerance: _ToleranceSpec | None
    _metrics: tuple[_MetricValue, ...]


@_dataclass(frozen=True, slots=True)
class _SimulatedPolicyResult:
    _fitted_state_identity_key: str
    _model_output_artifact_identity_key: str
    _policy_origin: _StageOrigin
    _execution_spec: _ExecutionSpec
    _trade_log_artifact_identity_key: str
    _equity_curve_artifact_identity_key: str
    _trade_log: _TradeLog
    _equity_curve: _EquityCurve

    def __post_init__(self) -> None:
        for value, label in (
            (self._fitted_state_identity_key, "fitted-state identity"),
            (self._model_output_artifact_identity_key, "model-output artifact identity"),
            (self._trade_log_artifact_identity_key, "TradeLog artifact identity"),
            (self._equity_curve_artifact_identity_key, "EquityCurve artifact identity"),
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise _ContractViolationError(f"{label} must be lowercase SHA-256")
        if type(self._policy_origin) is not _StageOrigin:
            raise _ContractViolationError("simulated policy origin must be exact")
        if type(self._execution_spec) is not _ExecutionSpec:
            raise _ContractViolationError("simulated execution spec must be exact")
        if type(self._trade_log) is not _TradeLog or type(self._equity_curve) is not _EquityCurve:
            raise _ContractViolationError("simulated artifacts must use exact core carriers")
        if (
            self._trade_log.execution_spec != self._execution_spec
            or self._trade_log.policy_origin != self._policy_origin
            or self._equity_curve.policy_origin != self._policy_origin
            or self._trade_log.fold_index != self._equity_curve.fold_index
        ):
            raise _ContractViolationError("simulated result metadata is inconsistent")


@_dataclass(frozen=True, slots=True)
class _TradeEvaluationInventoryEntry:
    _fitted_state_identity_key: str
    _model_output_artifact_identity_key: str
    _policy_origin: _StageOrigin
    _execution_spec: _ExecutionSpec
    _trade_log_artifact_identity_key: str
    _equity_curve_artifact_identity_key: str
    _evaluator_origin: _StageOrigin
    _metric_names: tuple[str, ...]
    _comparison_tolerance: _ToleranceSpec | None
    _metrics: tuple[_MetricValue, ...]


@_dataclass(frozen=True, slots=True)
class _EvaluationInventory:
    _outcome_class: _RunOutcomeClass
    _entries: tuple[_EvaluationInventoryEntry, ...]
    _policy_executions: tuple[_PolicyExecutionRecord, ...]
    _failure_reason: str | None
    _trade_entries: tuple[_TradeEvaluationInventoryEntry, ...] = ()

    def __post_init__(self) -> None:
        if type(self._outcome_class) is not _RunOutcomeClass:
            raise _ContractViolationError("inventory outcome class must be exact")
        if any(type(entry) is not _EvaluationInventoryEntry for entry in self._entries):
            raise _ContractViolationError("inventory entries must be exact")
        if any(type(entry) is not _TradeEvaluationInventoryEntry for entry in self._trade_entries):
            raise _ContractViolationError("trade inventory entries must be exact")
        if any(type(record) is not _PolicyExecutionRecord for record in self._policy_executions):
            raise _ContractViolationError("policy execution records must be exact")
        if self._outcome_class is _RunOutcomeClass.EVALUATED:
            if not (self._entries or self._trade_entries) or self._failure_reason is not None:
                raise _ContractViolationError(
                    "evaluated inventory requires entries and no failure reason"
                )
            return
        if self._entries or self._trade_entries:
            raise _ContractViolationError(
                "descriptive and pre-evaluation failure records have no evaluations"
            )
        if self._outcome_class is _RunOutcomeClass.DESCRIPTIVE:
            if self._failure_reason is not None:
                raise _ContractViolationError(
                    "descriptive inventory must not carry a failure reason"
                )
            return
        if type(self._failure_reason) is not str or not self._failure_reason.strip():
            raise _ContractViolationError(
                "pre-evaluation failure requires a non-empty permanent reason"
            )


@_dataclass(frozen=True, slots=True)
class _InventorySummary:
    _outcome_class: _RunOutcomeClass
    _evaluation_count: int
    _policy_execution_json: tuple[str, ...]
    _failure_reason: str | None


def _preflight_plane_a(
    *,
    output_column_names: tuple[str, ...],
    outcome_column_names: tuple[str, ...],
    evaluators: _Sequence[object],
) -> tuple[_EvaluatorPlan, ...]:
    """Refuse every incompatible evaluator before upstream execution starts."""
    evaluator_objects = _ordered_nonempty(evaluators, label="evaluators")
    plans = tuple(_snapshot_evaluator(evaluator) for evaluator in evaluator_objects)
    for plan in plans:
        _validate_evaluator_columns(
            plan,
            output_columns=output_column_names,
            outcome_columns=outcome_column_names,
        )
    return plans


def _evaluate_plane_a_inventory(
    *,
    fitted_outputs: _Sequence[_FittedOutput],
    outcome_targets: _Sequence[_OutcomeTarget],
    evaluators: _Sequence[object],
    policy_executions: tuple[_PolicyExecutionRecord, ...],
) -> _EvaluationInventory:
    """Evaluate exactly F x S x E artifact combinations, with no policy axis."""
    outputs = _exact_sequence(fitted_outputs, _FittedOutput, label="fitted outputs")
    targets = _exact_sequence(outcome_targets, _OutcomeTarget, label="outcome targets")
    evaluator_objects = _ordered_nonempty(evaluators, label="evaluators")
    records = _validated_policy_records(policy_executions)
    plans = tuple(_snapshot_evaluator(evaluator) for evaluator in evaluator_objects)
    _require_unique_subjects(outputs, targets, plans)

    for output in outputs:
        for target in targets:
            for plan in plans:
                _validate_artifacts(
                    plan,
                    model_output=output._frame,
                    outcome_targets=target._frame,
                )

    entries: list[_EvaluationInventoryEntry] = []
    for output in outputs:
        for target in targets:
            spec = _validated_spec(target._spec)
            outcome_origin = _StageOrigin(spec.outcome_id, spec.version, spec.params)
            for evaluator, plan in zip(evaluator_objects, plans, strict=True):
                metrics = _execute_evaluator(
                    evaluator,
                    plan,
                    model_output=output._frame,
                    outcome_targets=target._frame,
                )
                entries.append(
                    _EvaluationInventoryEntry(
                        _fitted_state_identity_key=output._fitted_state_identity_key,
                        _model_output_artifact_identity_key=(
                            output._model_output_artifact_identity_key
                        ),
                        _evaluated_output_columns=plan._required_output_columns,
                        _outcome_origin=outcome_origin,
                        _horizon_rows=spec.horizon_rows,
                        _decision_delay_rows=spec.decision_delay_rows,
                        _evaluator_origin=plan._origin,
                        _metric_names=plan._metric_names,
                        _comparison_tolerance=plan._comparison_tolerance,
                        _metrics=metrics,
                    )
                )
    expected = len(outputs) * len(targets) * len(plans)
    if len(entries) != expected:
        raise AssertionError("Plane-A evaluation inventory differs from F x S x E")
    return _EvaluationInventory(
        _outcome_class=_RunOutcomeClass.EVALUATED,
        _entries=tuple(entries),
        _policy_executions=records,
        _failure_reason=None,
    )


def _preflight_plane_b(evaluators: _Sequence[object]) -> tuple[_TradeEvaluatorPlan, ...]:
    """Snapshot every configured Plane-B declaration before upstream work."""
    evaluator_objects = _ordered_nonempty(evaluators, label="trade evaluators")
    plans = tuple(_snapshot_trade_evaluator(evaluator) for evaluator in evaluator_objects)
    keys = tuple(
        (
            plan._origin.plugin_id,
            plan._origin.version,
            _canonical_params_json(plan._origin.params),
        )
        for plan in plans
    )
    if len(set(keys)) != len(keys):
        raise _ContractViolationError("Plane-B evaluator instances must be distinct")
    return plans


def _evaluate_plane_b_inventory(
    *,
    base: _EvaluationInventory,
    simulated: _Sequence[_SimulatedPolicyResult],
    evaluators: _Sequence[object],
) -> _EvaluationInventory:
    """Add exactly F x P x B Plane-B rows to the existing Plane-A inventory."""
    if type(base) is not _EvaluationInventory:
        raise _ContractViolationError("base inventory must be exact")
    results = _exact_sequence(
        simulated,
        _SimulatedPolicyResult,
        label="simulated policy results",
    )
    evaluator_objects = _ordered_nonempty(evaluators, label="trade evaluators")
    plans = _preflight_plane_b(evaluator_objects)
    subject_keys = tuple(
        (
            result._fitted_state_identity_key,
            result._model_output_artifact_identity_key,
            _canonical_identity_json(_origin_document(result._policy_origin)),
        )
        for result in results
    )
    if len(set(subject_keys)) != len(subject_keys):
        raise _ContractViolationError("Plane-B fold-policy subjects must be distinct")

    entries: list[_TradeEvaluationInventoryEntry] = []
    for result in results:
        for evaluator, plan in zip(evaluator_objects, plans, strict=True):
            metrics = _execute_trade_evaluator(
                evaluator,
                plan,
                trade_log=result._trade_log,
                equity_curve=result._equity_curve,
            )
            entries.append(
                _TradeEvaluationInventoryEntry(
                    _fitted_state_identity_key=result._fitted_state_identity_key,
                    _model_output_artifact_identity_key=(
                        result._model_output_artifact_identity_key
                    ),
                    _policy_origin=result._policy_origin,
                    _execution_spec=result._execution_spec,
                    _trade_log_artifact_identity_key=(result._trade_log_artifact_identity_key),
                    _equity_curve_artifact_identity_key=(
                        result._equity_curve_artifact_identity_key
                    ),
                    _evaluator_origin=plan._origin,
                    _metric_names=plan._metric_names,
                    _comparison_tolerance=plan._comparison_tolerance,
                    _metrics=metrics,
                )
            )
    expected = len(results) * len(plans)
    if len(entries) != expected:
        raise AssertionError("Plane-B evaluation inventory differs from F x P x B")
    return _EvaluationInventory(
        _outcome_class=_RunOutcomeClass.EVALUATED,
        _entries=base._entries,
        _policy_executions=base._policy_executions,
        _failure_reason=None,
        _trade_entries=tuple(entries),
    )


def _record_policy_executions(
    *,
    policies: _Sequence[object],
    intents: _Sequence[_IntentFrame],
) -> tuple[_PolicyExecutionRecord, ...]:
    policy_objects = _ordered_nonempty(policies, label="policies")
    intent_frames = _exact_sequence(intents, _IntentFrame, label="intents")
    if len(policy_objects) != len(intent_frames):
        raise _ContractViolationError("policy and IntentFrame counts must match")
    records: list[_PolicyExecutionRecord] = []
    for policy, intent in zip(policy_objects, intent_frames, strict=True):
        plan = _snapshot_policy(policy)
        if intent.intent_schema_id != plan._intent_schema_id:
            raise _ContractViolationError("executed policy schema differs from its IntentFrame")
        keys = intent.keys
        records.append(
            _PolicyExecutionRecord(
                _origin=plan._origin,
                _intent_schema_id=plan._intent_schema_id,
                _intent_rows=int(intent.num_rows),
                _first_key=None if not keys.size else int(keys[0]),
                _last_key=None if not keys.size else int(keys[-1]),
            )
        )
    return tuple(records)


def _descriptive_inventory(
    *,
    policy_executions: tuple[_PolicyExecutionRecord, ...],
) -> _EvaluationInventory:
    return _EvaluationInventory(
        _outcome_class=_RunOutcomeClass.DESCRIPTIVE,
        _entries=(),
        _policy_executions=_validated_policy_records(policy_executions),
        _failure_reason=None,
    )


def _pre_evaluation_failure_inventory(
    *,
    reason: str,
    policy_executions: tuple[_PolicyExecutionRecord, ...] = (),
) -> _EvaluationInventory:
    if type(reason) is not str or not reason.strip():
        raise _ContractViolationError("pre-evaluation failure reason must be non-empty")
    return _EvaluationInventory(
        _outcome_class=_RunOutcomeClass.PRE_EVALUATION_FAILURE,
        _entries=(),
        _policy_executions=_validated_policy_records(policy_executions),
        _failure_reason=reason.strip(),
    )


def _validated_policy_records(
    records: tuple[_PolicyExecutionRecord, ...],
) -> tuple[_PolicyExecutionRecord, ...]:
    if type(records) is not tuple or any(
        type(record) is not _PolicyExecutionRecord for record in records
    ):
        raise _ContractViolationError(
            "policy_executions must be an exact tuple of execution records"
        )
    return records


def _require_unique_subjects(
    outputs: tuple[_FittedOutput, ...],
    targets: tuple[_OutcomeTarget, ...],
    plans: tuple[_EvaluatorPlan, ...],
) -> None:
    fitted_keys = tuple(output._fitted_state_identity_key for output in outputs)
    artifact_keys = tuple(output._model_output_artifact_identity_key for output in outputs)
    if len(set(fitted_keys)) != len(fitted_keys) or len(set(artifact_keys)) != len(artifact_keys):
        raise _ContractViolationError("Plane-A fitted outputs must have unique identities")
    outcome_keys = tuple(
        (
            target._spec.outcome_id,
            target._spec.version,
            _canonical_params_json(target._spec.params),
        )
        for target in targets
    )
    if len(set(outcome_keys)) != len(outcome_keys):
        raise _ContractViolationError("Plane-A OutcomeSpec instances must be distinct")
    evaluator_keys = tuple(
        (
            plan._origin.plugin_id,
            plan._origin.version,
            _canonical_params_json(plan._origin.params),
            plan._comparison_tolerance,
        )
        for plan in plans
    )
    if len(set(evaluator_keys)) != len(evaluator_keys):
        raise _ContractViolationError("Plane-A evaluator instances must be distinct")


def _ordered_nonempty(value: object, *, label: str) -> tuple[object, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError(f"{label} must be an ordered sequence")
    ordered = tuple(value)
    if not ordered:
        raise _ContractViolationError(f"{label} must not be empty")
    return ordered


def _exact_sequence[T](
    value: object,
    expected_type: type[T],
    *,
    label: str,
) -> tuple[T, ...]:
    ordered = _ordered_nonempty(value, label=label)
    if any(type(item) is not expected_type for item in ordered):
        raise _ContractViolationError(f"{label} contain an unexpected value")
    return _typing.cast("tuple[T, ...]", ordered)


def _evaluation_inventory_document(
    inventory: _EvaluationInventory,
) -> dict[str, object]:
    """Encode exact inventory state without non-standard JSON NaN tokens."""
    if type(inventory) is not _EvaluationInventory:
        raise _ContractViolationError("inventory must be an exact EvaluationInventory")
    document: dict[str, object] = {
        "schema": _INVENTORY_SCHEMA,
        "outcome_class": inventory._outcome_class.value,
        "entries": [
            *(_inventory_entry_document(entry) for entry in inventory._entries),
            *(_trade_inventory_entry_document(entry) for entry in inventory._trade_entries),
        ],
        "policy_executions": [
            _policy_execution_document(record) for record in inventory._policy_executions
        ],
        "failure_reason": inventory._failure_reason,
    }
    _inventory_summary_from_document(document)
    return document


def _inventory_entry_document(
    entry: _EvaluationInventoryEntry,
) -> dict[str, object]:
    if type(entry) is not _EvaluationInventoryEntry:
        raise _ContractViolationError("inventory entry must be exact")
    return {
        "plane": _EvaluationPlane.A.value,
        "fitted_state_identity": entry._fitted_state_identity_key,
        "model_output_artifact_identity": (entry._model_output_artifact_identity_key),
        "evaluated_output_columns": list(entry._evaluated_output_columns),
        "outcome": _origin_document(entry._outcome_origin),
        "horizon_rows": entry._horizon_rows,
        "decision_delay_rows": entry._decision_delay_rows,
        "evaluator": _origin_document(entry._evaluator_origin),
        "metric_names": list(entry._metric_names),
        "comparison_tolerance": _tolerance_document(entry._comparison_tolerance),
        "metrics": [
            {
                "name": metric.name,
                "value_float64_bits": _struct.pack(">d", metric.value).hex(),
                "effective_samples": metric.effective_samples,
            }
            for metric in entry._metrics
        ],
    }


def _trade_inventory_entry_document(
    entry: _TradeEvaluationInventoryEntry,
) -> dict[str, object]:
    if type(entry) is not _TradeEvaluationInventoryEntry:
        raise _ContractViolationError("trade inventory entry must be exact")
    binding = entry._execution_spec.cost_binding
    return {
        "plane": _EvaluationPlane.B.value,
        "fitted_state_identity": entry._fitted_state_identity_key,
        "model_output_artifact_identity": entry._model_output_artifact_identity_key,
        "policy": _origin_document(entry._policy_origin),
        "execution_spec": _json.loads(_execution_spec_params_json(entry._execution_spec)),
        "trade_log_artifact_identity": entry._trade_log_artifact_identity_key,
        "equity_curve_artifact_identity": entry._equity_curve_artifact_identity_key,
        "cost_semantics_unverified": (binding.cost_semantics is _CostSemanticsStatus.UNVERIFIED),
        "binding_id": binding.binding_id,
        "binding_digest": binding.binding_digest,
        "execution_price_model": binding.execution_price_model.value,
        "evaluator": _origin_document(entry._evaluator_origin),
        "metric_names": list(entry._metric_names),
        "comparison_tolerance": _tolerance_document(entry._comparison_tolerance),
        "metrics": [
            {
                "name": metric.name,
                "value_float64_bits": _struct.pack(">d", metric.value).hex(),
                "effective_samples": metric.effective_samples,
            }
            for metric in entry._metrics
        ],
    }


def _policy_execution_document(
    record: _PolicyExecutionRecord,
) -> dict[str, object]:
    if type(record) is not _PolicyExecutionRecord:
        raise _ContractViolationError("policy execution record must be exact")
    return {
        "policy": _origin_document(record._origin),
        "intent_schema_id": record._intent_schema_id,
        "intent_shape": {
            "rows": record._intent_rows,
            "first_key": record._first_key,
            "last_key": record._last_key,
        },
    }


def _origin_document(origin: _StageOrigin) -> dict[str, object]:
    if type(origin) is not _StageOrigin:
        raise _ContractViolationError("inventory origin must be exact")
    return {
        "plugin_id": origin.plugin_id,
        "version": origin.version,
        "params": _json.loads(_canonical_params_json(origin.params)),
    }


def _tolerance_document(tolerance: _ToleranceSpec | None) -> object:
    if tolerance is None:
        return None
    if type(tolerance) is not _ToleranceSpec:
        raise _ContractViolationError("inventory tolerance must be exact or None")
    return {
        "atol": tolerance.atol,
        "rtol": tolerance.rtol,
        "reason": tolerance.reason,
    }


def _inventory_summary_from_document(value: object) -> _InventorySummary:
    """Validate a decoded V1 or V2 inventory and return its compact summary."""
    document = _exact_dict(
        value,
        fields={
            "entries",
            "failure_reason",
            "outcome_class",
            "policy_executions",
            "schema",
        },
        label="evaluation inventory",
    )
    schema = document["schema"]
    if type(schema) is not str or schema not in {
        _INVENTORY_SCHEMA_V1,
        _INVENTORY_SCHEMA,
    }:
        raise _ContractViolationError("evaluation inventory schema is unsupported")
    raw_outcome_class = document["outcome_class"]
    if type(raw_outcome_class) is not str:
        raise _ContractViolationError("evaluation inventory outcome class is invalid")
    try:
        outcome_class = _RunOutcomeClass(raw_outcome_class)
    except (TypeError, ValueError) as exc:
        raise _ContractViolationError("evaluation inventory outcome class is invalid") from exc
    entries = _exact_list(document["entries"], label="inventory entries")
    entry_json = tuple(
        _validate_inventory_entry(entry, index=index, schema=schema)
        for index, entry in enumerate(entries)
    )
    if len(set(entry_json)) != len(entry_json):
        raise _ContractViolationError("evaluation inventory contains duplicate rows")
    policy_values = _exact_list(
        document["policy_executions"],
        label="policy executions",
    )
    policy_json = tuple(
        _validate_policy_execution(record, index=index)
        for index, record in enumerate(policy_values)
    )
    reason = document["failure_reason"]
    if outcome_class is _RunOutcomeClass.EVALUATED:
        if not entries or reason is not None:
            raise _ContractViolationError("evaluated inventory requires rows and no failure reason")
    elif entries:
        raise _ContractViolationError(
            "descriptive and pre-evaluation failure inventories require zero rows"
        )
    elif outcome_class is _RunOutcomeClass.DESCRIPTIVE:
        if reason is not None:
            raise _ContractViolationError("descriptive inventory must not carry a failure reason")
    elif type(reason) is not str or not reason.strip() or reason != reason.strip():
        raise _ContractViolationError("pre-evaluation failure inventory requires a trimmed reason")
    return _InventorySummary(
        outcome_class,
        len(entries),
        policy_json,
        reason,
    )


def _validate_inventory_entry(value: object, *, index: int, schema: str) -> str:
    label = f"inventory entry {index}"
    if schema == _INVENTORY_SCHEMA_V1:
        return _validate_plane_a_entry(value, label=label, include_plane=False)
    if type(value) is not dict:
        raise _ContractViolationError(f"{label} fields differ from V2 schema")
    plane = value.get("plane")
    if plane == _EvaluationPlane.A.value:
        return _validate_plane_a_entry(value, label=label, include_plane=True)
    if plane == _EvaluationPlane.B.value:
        return _validate_plane_b_entry(value, label=label)
    raise _ContractViolationError(f"{label} plane must be exactly 'A' or 'B'")


def _validate_plane_a_entry(
    value: object,
    *,
    label: str,
    include_plane: bool,
) -> str:
    fields = {
        "comparison_tolerance",
        "decision_delay_rows",
        "evaluated_output_columns",
        "evaluator",
        "fitted_state_identity",
        "horizon_rows",
        "metric_names",
        "metrics",
        "model_output_artifact_identity",
        "outcome",
    }
    if include_plane:
        fields.add("plane")
    entry = _exact_dict(
        value,
        fields=fields,
        label=label,
    )
    if include_plane and entry["plane"] != _EvaluationPlane.A.value:
        raise _ContractViolationError(f"{label} Plane-A marker is invalid")
    _validated_digest_field(entry["fitted_state_identity"], label=label)
    _validated_digest_field(entry["model_output_artifact_identity"], label=label)
    _validated_json_names(
        entry["evaluated_output_columns"],
        label=f"{label} evaluated output columns",
    )
    _validated_origin_document(entry["outcome"], label=f"{label} outcome")
    _validated_origin_document(entry["evaluator"], label=f"{label} evaluator")
    for field, minimum in (("horizon_rows", 1), ("decision_delay_rows", 0)):
        scalar = entry[field]
        if type(scalar) is not int or scalar < minimum:
            raise _ContractViolationError(f"{label} {field} must be an exact int >= {minimum}")
    metric_names = _validated_json_names(
        entry["metric_names"],
        label=f"{label} metric names",
    )
    _validated_tolerance_document(
        entry["comparison_tolerance"],
        label=f"{label} tolerance",
    )
    metrics = _exact_list(entry["metrics"], label=f"{label} metrics")
    observed_names = tuple(
        _validate_metric_document(metric, label=f"{label} metric {metric_index}")
        for metric_index, metric in enumerate(metrics)
    )
    if observed_names != metric_names:
        raise _ContractViolationError(f"{label} metric rows must exactly follow metric_names")
    return _canonical_identity_json(entry)


def _validate_plane_b_entry(value: object, *, label: str) -> str:
    entry = _exact_dict(
        value,
        fields={
            "binding_digest",
            "binding_id",
            "comparison_tolerance",
            "cost_semantics_unverified",
            "equity_curve_artifact_identity",
            "evaluator",
            "execution_price_model",
            "execution_spec",
            "fitted_state_identity",
            "metric_names",
            "metrics",
            "model_output_artifact_identity",
            "plane",
            "policy",
            "trade_log_artifact_identity",
        },
        label=label,
    )
    if entry["plane"] != _EvaluationPlane.B.value:
        raise _ContractViolationError(f"{label} Plane-B marker is invalid")
    for field in (
        "fitted_state_identity",
        "model_output_artifact_identity",
        "trade_log_artifact_identity",
        "equity_curve_artifact_identity",
    ):
        _validated_digest_field(entry[field], label=f"{label} {field}")
    _validated_origin_document(entry["policy"], label=f"{label} policy")
    _validated_origin_document(entry["evaluator"], label=f"{label} evaluator")
    spec = _optional_execution_spec(entry["execution_spec"])
    if spec is None:
        raise _ContractViolationError(f"{label} execution_spec must not be null")
    binding = spec.cost_binding
    expected_unverified = binding.cost_semantics is _CostSemanticsStatus.UNVERIFIED
    if type(entry["cost_semantics_unverified"]) is not bool or (
        entry["cost_semantics_unverified"] is not expected_unverified
    ):
        raise _ContractViolationError(f"{label} cost-semantics marking is inconsistent")
    if (
        entry["binding_id"] != binding.binding_id
        or entry["binding_digest"] != binding.binding_digest
        or entry["execution_price_model"] != binding.execution_price_model.value
    ):
        raise _ContractViolationError(f"{label} execution binding fields are inconsistent")
    if entry["comparison_tolerance"] is not None:
        raise _ContractViolationError(f"{label} Plane-B tolerance must be null")
    metric_names = _validated_json_names(
        entry["metric_names"],
        label=f"{label} metric names",
    )
    metrics = _exact_list(entry["metrics"], label=f"{label} metrics")
    observed_names = tuple(
        _validate_metric_document(metric, label=f"{label} metric {metric_index}")
        for metric_index, metric in enumerate(metrics)
    )
    if observed_names != metric_names:
        raise _ContractViolationError(f"{label} metric rows must exactly follow metric_names")
    return _canonical_identity_json(entry)


def _validate_policy_execution(value: object, *, index: int) -> str:
    label = f"policy execution {index}"
    record = _exact_dict(
        value,
        fields={"intent_schema_id", "intent_shape", "policy"},
        label=label,
    )
    _validated_origin_document(record["policy"], label=f"{label} origin")
    schema = record["intent_schema_id"]
    if type(schema) is not str or not schema:
        raise _ContractViolationError(f"{label} intent schema must be non-empty")
    shape = _exact_dict(
        record["intent_shape"],
        fields={"first_key", "last_key", "rows"},
        label=f"{label} shape",
    )
    rows = shape["rows"]
    if type(rows) is not int or rows < 0:
        raise _ContractViolationError(f"{label} rows must be a non-negative int")
    first = shape["first_key"]
    last = shape["last_key"]
    if rows == 0:
        if first is not None or last is not None:
            raise _ContractViolationError(f"{label} empty shape cannot carry bounds")
    elif type(first) is not int or type(last) is not int or first > last:
        raise _ContractViolationError(f"{label} non-empty shape requires ordered keys")
    return _canonical_identity_json(record)


def _validated_origin_document(value: object, *, label: str) -> None:
    origin = _exact_dict(
        value,
        fields={"params", "plugin_id", "version"},
        label=label,
    )
    params = origin["params"]
    try:
        decoded_params = _decode_params(params)
        _StageOrigin(
            _typing.cast("str", origin["plugin_id"]),
            _typing.cast("int", origin["version"]),
            decoded_params,
        )
    except Exception as exc:
        raise _ContractViolationError(f"{label} is malformed") from exc


def _validated_tolerance_document(value: object, *, label: str) -> None:
    if value is None:
        return
    tolerance = _exact_dict(
        value,
        fields={"atol", "reason", "rtol"},
        label=label,
    )
    _ToleranceSpec(
        _typing.cast("float", tolerance["atol"]),
        _typing.cast("float", tolerance["rtol"]),
        _typing.cast("str", tolerance["reason"]),
    )


def _validate_metric_document(value: object, *, label: str) -> str:
    metric = _exact_dict(
        value,
        fields={"effective_samples", "name", "value_float64_bits"},
        label=label,
    )
    name = metric["name"]
    bits = metric["value_float64_bits"]
    effective = metric["effective_samples"]
    if type(name) is not str or not name:
        raise _ContractViolationError(f"{label} name must be non-empty")
    if type(bits) is not str or _FLOAT64_BITS.fullmatch(bits) is None:
        raise _ContractViolationError(f"{label} value bits must be 16 lowercase hex")
    value_float = _struct.unpack(">d", bytes.fromhex(bits))[0]
    _MetricValue(
        name,
        value_float,
        _typing.cast("int", effective),
    )
    return name


def _validated_json_names(value: object, *, label: str) -> tuple[str, ...]:
    items = _exact_list(value, label=label)
    if not items or any(type(item) is not str or not item for item in items):
        raise _ContractViolationError(f"{label} must contain non-empty strings")
    names = _typing.cast("tuple[str, ...]", tuple(items))
    if len(set(names)) != len(names):
        raise _ContractViolationError(f"{label} must contain unique names")
    return names


def _validated_digest_field(value: object, *, label: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _ContractViolationError(f"{label} identity must be lowercase SHA-256")


def _exact_dict(
    value: object,
    *,
    fields: set[str],
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _ContractViolationError(f"{label} fields differ from V1 schema")
    return _typing.cast("dict[str, object]", value)


def _exact_list(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise _ContractViolationError(f"{label} must be an exact JSON list")
    return _typing.cast("list[object]", value)
