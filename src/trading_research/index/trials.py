"""Mechanical Trial Ledger derived from files or the disposable run index."""

from __future__ import annotations

import json as _json
import sqlite3 as _sqlite3
import time as _time
import typing as _typing
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.experiments.runs import (
    _RunProjection,
    _scan_run_projections,
)
from trading_research.experiments.sweeps import _read_sweep_plan
from trading_research.index.build import _open_fresh_index

if _typing.TYPE_CHECKING:
    from collections.abc import Iterable as _Iterable

__all__: list[str] = []

_CAVEAT = (
    "This is a count of evaluated (hypothesis, question) pairs in the declared "
    "scope. Folds and attempts are collapsed. Related variants within a sweep are "
    "counted individually and are NOT statistically independent, so the true "
    "effective number of independent trials is smaller than this figure and is "
    "not estimated by this platform. No multiple-testing correction has been "
    "applied to any metric shown."
)
_PROTECTED_ROLES = frozenset({"oos", "lockbox"})
_PROTECTED_AUDIT_CLASSIFICATIONS = {
    "oos": "oos_explicit",
    "lockbox": "fully_gated",
}
_AUDIT_SCHEMA_VERSION = "1"
_ACCESS_AUDIT_EVENT_TYPE = "dataset_access_decision"
_NON_ACCESS_AUDIT_EVENT_TYPES = frozenset({"snapshot_binding"})


class _TrialLedgerError(_TradingResearchError):
    """The ledger input is malformed, stale, or internally contradictory."""


@_dataclass(frozen=True, slots=True)
class _LedgerAccessRef:
    _session_id: str
    _audit_event_id: str | None


@_dataclass(frozen=True, slots=True)
class _LedgerEvaluation:
    _entry_ordinal: int
    _plane: str
    _fitted_state_identity: str
    _model_output_identity: str
    _question_json: str
    _entry_question_json: str
    _metric_name: str
    _effective_samples: int
    _cost_semantics_unverified: bool
    _policy_origin_json: str | None


@_dataclass(frozen=True, slots=True)
class _LedgerPolicyExecution:
    _policy_origin_json: str


@_dataclass(frozen=True, slots=True)
class _LedgerRun:
    _run_id: str
    _experiment_fingerprint: str
    _created_unix_ns: int
    _state: str
    _canonical: bool
    _outcome_class: str | None
    _dataset_role: str
    _oos_touched: bool
    _lockbox_touched: bool
    _access_refs: tuple[_LedgerAccessRef, ...]
    _evaluations: tuple[_LedgerEvaluation, ...]
    _policy_executions: tuple[_LedgerPolicyExecution, ...]


@_dataclass(frozen=True, slots=True)
class _LedgerInput:
    _runs: tuple[_LedgerRun, ...]
    _duplicates_collapsed: int
    _scan_errors: tuple[dict[str, object], ...]


@_dataclass(frozen=True, slots=True)
class _TrialLedger:
    _document: dict[str, object]
    _discrepancies: tuple[dict[str, object], ...]

    @property
    def _has_errors(self) -> bool:
        return bool(self._discrepancies or self._document["scan_errors"])


@_dataclass(frozen=True, slots=True)
class _AuditCounts:
    _oos_linked_in_scope: int
    _lockbox_linked_in_scope: int
    _oos_audit_total: int | None
    _lockbox_audit_total: int | None


def _files_trial_ledger(
    runs_root: _Path,
    *,
    audit_path: _Path | None = None,
    sweep_id: str | None = None,
    since_unix_ns: int | None = None,
    now_unix_ns: int | None = None,
    stale_after_ns: int = 60_000_000_000,
) -> _TrialLedger:
    """Build the authoritative reference report from run directories alone."""
    root, scope = _validated_scope(
        runs_root,
        audit_path=audit_path,
        sweep_id=sweep_id,
        since_unix_ns=since_unix_ns,
    )
    observed_now = _time.time_ns() if now_unix_ns is None else now_unix_ns
    if type(observed_now) is not int or observed_now <= 0:
        raise _TrialLedgerError("now_unix_ns must be an exact positive int")
    scan = _scan_run_projections(
        root,
        now_unix_ns=observed_now,
        stale_after_ns=stale_after_ns,
        reconcile=False,
        tolerate_corruption=True,
    )
    fingerprints = _scope_fingerprints(root, sweep_id)
    projections = tuple(
        projection
        for projection in scan._projections
        if _projection_in_scope(
            projection,
            fingerprints=fingerprints,
            since_unix_ns=since_unix_ns,
        )
    )
    runs = tuple(_run_from_projection(projection) for projection in projections)
    errors: tuple[dict[str, object], ...] = tuple(
        {
            "run_dir_name": error._run_id,
            "error_kind": error._error_kind,
            "detail": error._detail,
        }
        for error in scan._errors
    )
    source = _LedgerInput(
        runs,
        _files_duplicate_count(root, sweep_id=sweep_id, since_unix_ns=since_unix_ns),
        errors,
    )
    return _build_ledger(source, scope=scope, audit_path=audit_path)


def _index_trial_ledger(
    runs_root: _Path,
    *,
    audit_path: _Path | None = None,
    sweep_id: str | None = None,
    since_unix_ns: int | None = None,
) -> _TrialLedger:
    """Build the same report through the disposable index for equivalence checks."""
    root, scope = _validated_scope(
        runs_root,
        audit_path=audit_path,
        sweep_id=sweep_id,
        since_unix_ns=since_unix_ns,
    )
    connection = _open_fresh_index(root, allow_scan_errors=True)
    try:
        connection.row_factory = _sqlite3.Row
        run_rows = _index_run_rows(
            connection,
            sweep_id=sweep_id,
            since_unix_ns=since_unix_ns,
        )
        runs = tuple(_run_from_index(connection, row) for row in run_rows)
        duplicate_count = _index_duplicate_count(
            connection,
            sweep_id=sweep_id,
            since_unix_ns=since_unix_ns,
        )
        errors = tuple(
            dict(row)
            for row in connection.execute(
                "SELECT run_dir_name, error_kind, detail FROM scan_error ORDER BY run_dir_name"
            )
        )
    finally:
        connection.close()
    return _build_ledger(
        _LedgerInput(runs, duplicate_count, errors),
        scope=scope,
        audit_path=audit_path,
    )


def _build_ledger(
    source: _LedgerInput,
    *,
    scope: dict[str, object],
    audit_path: _Path | None,
) -> _TrialLedger:
    trial_folds: dict[tuple[str, str, str], set[str]] = {}
    model_outputs: dict[tuple[str, str, str, str], str] = {}
    effective_samples: dict[tuple[str, str, str, str], int] = {}
    evaluated_run_ids: set[str] = set()
    evaluated_attempts_by_fingerprint: dict[str, set[str]] = {}
    evaluated_hypotheses: set[str] = set()
    unverified_entries: set[tuple[str, str, str, str]] = set()
    plane_b_policy_origins: dict[str, set[str]] = {}
    executed_policies: set[tuple[str, str]] = set()

    for run in source._runs:
        _assert_fold_invariant(run)
        for execution in run._policy_executions:
            executed_policies.add(
                (run._experiment_fingerprint, execution._policy_origin_json)
            )
        for evaluation in run._evaluations:
            trial = (
                run._experiment_fingerprint,
                evaluation._plane,
                evaluation._question_json,
            )
            trial_folds.setdefault(trial, set()).add(evaluation._fitted_state_identity)
            output_key = (*trial, evaluation._fitted_state_identity)
            prior_output = model_outputs.setdefault(
                output_key,
                evaluation._model_output_identity,
            )
            if prior_output != evaluation._model_output_identity:
                raise _TrialLedgerError(
                    "one hypothesis/question/fitted state maps to multiple model outputs"
                )
            sample_key = (
                run._experiment_fingerprint,
                evaluation._question_json,
                evaluation._fitted_state_identity,
                evaluation._metric_name,
            )
            prior_samples = effective_samples.setdefault(
                sample_key,
                evaluation._effective_samples,
            )
            if prior_samples != evaluation._effective_samples:
                raise _TrialLedgerError(
                    "attempts disagree on effective_samples for one folded question"
                )
            evaluated_run_ids.add(run._run_id)
            evaluated_hypotheses.add(run._experiment_fingerprint)
            evaluated_attempts_by_fingerprint.setdefault(
                run._experiment_fingerprint,
                set(),
            ).add(run._run_id)
            if evaluation._cost_semantics_unverified:
                unverified_entries.add(
                    (
                        run._experiment_fingerprint,
                        evaluation._fitted_state_identity,
                        evaluation._entry_question_json,
                        evaluation._plane,
                    )
                )
            if evaluation._plane == "B" and evaluation._policy_origin_json is not None:
                plane_b_policy_origins.setdefault(
                    run._experiment_fingerprint,
                    set(),
                ).add(evaluation._policy_origin_json)

    plane_a_trials = sum(1 for _fingerprint, plane, _question in trial_folds if plane == "A")
    plane_b_trials = sum(1 for _fingerprint, plane, _question in trial_folds if plane == "B")
    fold_counts = tuple(len(values) for values in trial_folds.values())
    sample_values = tuple(effective_samples.values())
    attempts_collapsed = sum(
        max(0, len(run_ids) - 1) for run_ids in evaluated_attempts_by_fingerprint.values()
    )
    unevaluated_policy_executions = sum(
        1
        for fingerprint, policy_origin_json in executed_policies
        if policy_origin_json not in plane_b_policy_origins.get(fingerprint, set())
    )
    audit_counts, discrepancies = _audit_cross_check(source._runs, audit_path=audit_path)
    document: dict[str, object] = {
        "schema": "research-trial-ledger-v1",
        "scope": scope,
        "plane_a_trials": plane_a_trials,
        "plane_b_trials": plane_b_trials,
        "distinct_hypotheses": len(evaluated_hypotheses),
        "runs_examined": len(source._runs),
        "attempts_collapsed": attempts_collapsed,
        "fold_multiplicity": _distribution(fold_counts),
        "descriptive_runs": sum(
            run._outcome_class == "descriptive" for run in source._runs
        ),
        "pre_evaluation_failures": sum(
            run._outcome_class == "pre_evaluation_failure" for run in source._runs
        ),
        "policies_executed_not_evaluated": unevaluated_policy_executions,
        "oos_evaluated_runs": sum(
            run._run_id in evaluated_run_ids and run._oos_touched for run in source._runs
        ),
        "oos_access_events": audit_counts._oos_linked_in_scope,
        "oos_access_events_audit_total": audit_counts._oos_audit_total,
        "lockbox_evaluated_runs": sum(
            run._run_id in evaluated_run_ids and run._lockbox_touched for run in source._runs
        ),
        "lockbox_access_events": audit_counts._lockbox_linked_in_scope,
        "lockbox_access_events_audit_total": audit_counts._lockbox_audit_total,
        "unverified_cost_semantics_entries": len(unverified_entries),
        "noncanonical_runs": sum(not run._canonical for run in source._runs),
        "duplicates_collapsed": source._duplicates_collapsed,
        "effective_samples": _distribution(sample_values),
        "audit_discrepancies": list(discrepancies),
        "scan_errors": list(source._scan_errors),
        "caveat": _CAVEAT,
    }
    return _TrialLedger(document, discrepancies)


def _run_from_projection(projection: _RunProjection) -> _LedgerRun:
    config = projection._resolved_config
    dataset = _mapping(config.get("dataset"), label="resolved dataset")
    role = _text(dataset.get("role"), label="dataset role").lower()
    inventory = projection._inventory
    evaluations: tuple[_LedgerEvaluation, ...] = ()
    policies: tuple[_LedgerPolicyExecution, ...] = ()
    if inventory is not None:
        entries = _sequence(inventory.get("entries"), label="inventory entries")
        evaluations = tuple(
            evaluation
            for ordinal, entry in enumerate(entries)
            for evaluation in _evaluations_from_document(entry, entry_ordinal=ordinal)
        )
        policy_rows = _sequence(
            inventory.get("policy_executions"),
            label="policy executions",
        )
        policies = tuple(_policy_from_document(row) for row in policy_rows)
    return _LedgerRun(
        projection._record._run_id,
        projection._record._experiment_fingerprint,
        projection._state._created_unix_ns,
        projection._record._state.value,
        projection._record._canonical,
        (
            None
            if projection._record._outcome_class is None
            else projection._record._outcome_class.value
        ),
        role,
        projection._record._oos_touched,
        projection._record._lockbox_touched,
        tuple(
            _LedgerAccessRef(ref._session_id, ref._audit_event_id)
            for ref in projection._state._access_audit_refs
        ),
        evaluations,
        policies,
    )


def _evaluations_from_document(
    value: object,
    *,
    entry_ordinal: int,
) -> tuple[_LedgerEvaluation, ...]:
    entry = _mapping(value, label="inventory entry")
    plane = entry.get("plane", "A")
    if plane not in {"A", "B"}:
        raise _TrialLedgerError("inventory plane is unsupported")
    fitted = _text(entry.get("fitted_state_identity"), label="fitted state")
    output = _text(entry.get("model_output_artifact_identity"), label="model output")
    evaluator = _origin(entry.get("evaluator"), label="evaluator")
    policy_origin_json: str | None = None
    if plane == "A":
        entry_question = {
            "evaluated_output_columns": _sequence(
                entry.get("evaluated_output_columns"),
                label="evaluated output columns",
            ),
            "outcome": _origin(entry.get("outcome"), label="outcome"),
            "horizon_rows": entry.get("horizon_rows"),
            "decision_delay_rows": entry.get("decision_delay_rows"),
            "evaluator": evaluator,
        }
    else:
        policy = _origin(entry.get("policy"), label="policy")
        policy_origin_json = _canonical_json(policy)
        entry_question = {
            "policy": policy,
            "execution_spec": entry.get("execution_spec"),
            "evaluator": evaluator,
        }
    metrics = _sequence(entry.get("metrics"), label="inventory metrics")
    rows: list[_LedgerEvaluation] = []
    for metric in metrics:
        metric_document = _mapping(metric, label="inventory metric")
        metric_name = _text(metric_document.get("name"), label="metric name")
        effective = metric_document.get("effective_samples")
        if type(effective) is not int or effective < 0:
            raise _TrialLedgerError("effective_samples must be a non-negative int")
        question = {**entry_question, "metric_name": metric_name}
        rows.append(
            _LedgerEvaluation(
                entry_ordinal,
                plane,
                fitted,
                output,
                _canonical_json(question),
                _canonical_json(entry_question),
                metric_name,
                effective,
                bool(entry.get("cost_semantics_unverified", False)),
                policy_origin_json,
            )
        )
    return tuple(rows)


def _policy_from_document(value: object) -> _LedgerPolicyExecution:
    document = _mapping(value, label="policy execution")
    origin = _origin(document.get("policy"), label="executed policy")
    return _LedgerPolicyExecution(_canonical_json(origin))


def _index_run_rows(
    connection: _sqlite3.Connection,
    *,
    sweep_id: str | None,
    since_unix_ns: int | None,
) -> tuple[_sqlite3.Row, ...]:
    conditions = ["1 = 1"]
    parameters: list[object] = []
    if sweep_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM sweep_member AS sm "
            "WHERE sm.sweep_id = ? AND sm.experiment_fingerprint = r.experiment_fingerprint)"
        )
        parameters.append(sweep_id)
    if since_unix_ns is not None:
        conditions.append("r.created_unix_ns >= ?")
        parameters.append(since_unix_ns)
    statement = f"""
        SELECT r.* FROM run AS r
        WHERE {' AND '.join(conditions)}
        ORDER BY r.run_id
    """
    return tuple(connection.execute(statement, parameters))


def _run_from_index(
    connection: _sqlite3.Connection,
    row: _sqlite3.Row,
) -> _LedgerRun:
    run_id = _typing.cast("str", row["run_id"])
    evaluation_rows = connection.execute(
        """
        SELECT e.*, m.metric_name, m.effective_samples
        FROM evaluation AS e
        JOIN metric AS m
          ON m.run_id = e.run_id AND m.entry_ordinal = e.entry_ordinal
        WHERE e.run_id = ?
        ORDER BY e.entry_ordinal, m.metric_name
        """,
        (run_id,),
    )
    evaluations = tuple(_evaluation_from_index(item) for item in evaluation_rows)
    policy_rows = connection.execute(
        """
        SELECT plugin_id, version, params_json
        FROM policy_execution WHERE run_id = ? ORDER BY ordinal
        """,
        (run_id,),
    )
    policies = tuple(_policy_from_index(item) for item in policy_rows)
    access_rows = connection.execute(
        """
        SELECT session_id, audit_event_id
        FROM access_audit_ref WHERE run_id = ? ORDER BY ordinal
        """,
        (run_id,),
    )
    return _LedgerRun(
        run_id,
        _typing.cast("str", row["experiment_fingerprint"]),
        _typing.cast("int", row["created_unix_ns"]),
        _typing.cast("str", row["state"]),
        bool(row["canonical"]),
        _typing.cast("str | None", row["outcome_class"]),
        _typing.cast("str", row["dataset_role"]).lower(),
        bool(row["oos_touched"]),
        bool(row["lockbox_touched"]),
        tuple(
            _LedgerAccessRef(
                _typing.cast("str", item["session_id"]),
                _typing.cast("str | None", item["audit_event_id"]),
            )
            for item in access_rows
        ),
        evaluations,
        policies,
    )


def _evaluation_from_index(row: _sqlite3.Row) -> _LedgerEvaluation:
    plane = _typing.cast("str", row["plane"])
    evaluator = {
        "plugin_id": row["evaluator_plugin_id"],
        "version": row["evaluator_version"],
        "params": _decoded_json(row["evaluator_params_json"], label="evaluator params"),
    }
    policy_origin_json: str | None = None
    if plane == "A":
        entry_question = {
            "evaluated_output_columns": _decoded_json(
                row["evaluated_columns_json"],
                label="evaluated columns",
            ),
            "outcome": {
                "plugin_id": row["outcome_plugin_id"],
                "version": row["outcome_version"],
                "params": _decoded_json(row["outcome_params_json"], label="outcome params"),
            },
            "horizon_rows": row["horizon_rows"],
            "decision_delay_rows": row["decision_delay_rows"],
            "evaluator": evaluator,
        }
    elif plane == "B":
        policy = {
            "plugin_id": row["policy_plugin_id"],
            "version": row["policy_version"],
            "params": _decoded_json(row["policy_params_json"], label="policy params"),
        }
        policy_origin_json = _canonical_json(policy)
        entry_question = {
            "policy": policy,
            "execution_spec": _decoded_json(
                row["execution_spec_json"],
                label="execution spec",
            ),
            "evaluator": evaluator,
        }
    else:
        raise _TrialLedgerError("index evaluation plane is unsupported")
    metric_name = _typing.cast("str", row["metric_name"])
    question = {**entry_question, "metric_name": metric_name}
    return _LedgerEvaluation(
        _typing.cast("int", row["entry_ordinal"]),
        plane,
        _typing.cast("str", row["fitted_state_identity"]),
        _typing.cast("str", row["model_output_identity"]),
        _canonical_json(question),
        _canonical_json(entry_question),
        metric_name,
        _typing.cast("int", row["effective_samples"]),
        bool(row["cost_semantics_unverified"]),
        policy_origin_json,
    )


def _policy_from_index(row: _sqlite3.Row) -> _LedgerPolicyExecution:
    origin = {
        "plugin_id": row["plugin_id"],
        "version": row["version"],
        "params": _decoded_json(row["params_json"], label="executed policy params"),
    }
    return _LedgerPolicyExecution(_canonical_json(origin))


def _assert_fold_invariant(run: _LedgerRun) -> None:
    if not run._evaluations:
        return
    fitted_to_output: dict[str, str] = {}
    question_folds: dict[tuple[str, str], set[str]] = {}
    for evaluation in run._evaluations:
        prior = fitted_to_output.setdefault(
            evaluation._fitted_state_identity,
            evaluation._model_output_identity,
        )
        if prior != evaluation._model_output_identity:
            raise _TrialLedgerError(
                "fold invariant failed: one fitted state maps to multiple model outputs"
            )
        question_folds.setdefault(
            (evaluation._plane, evaluation._entry_question_json),
            set(),
        ).add(evaluation._fitted_state_identity)
    expected = set(fitted_to_output)
    if any(folds != expected for folds in question_folds.values()):
        raise _TrialLedgerError(
            "fold invariant failed: evaluation questions do not share one fitted-state set"
        )


def _audit_cross_check(
    runs: tuple[_LedgerRun, ...],
    *,
    audit_path: _Path | None,
) -> tuple[_AuditCounts, tuple[dict[str, object], ...]]:
    requires_audit = any(
        run._dataset_role in _PROTECTED_ROLES
        or any(ref._audit_event_id is not None for ref in run._access_refs)
        for run in runs
    )
    discrepancies: list[dict[str, object]] = [
        {
            "kind": "access_ref_missing",
            "run_id": run._run_id,
            "detail": "protected run has no recorded authorization session",
        }
        for run in runs
        if run._dataset_role in _PROTECTED_ROLES and not run._access_refs
    ]
    if audit_path is None:
        if requires_audit:
            discrepancies.append(
                {
                    "kind": "audit_path_required",
                    "detail": "protected audit references exist but no audit path was declared",
                }
            )
        return _AuditCounts(0, 0, None, None), tuple(discrepancies)
    events = _read_audit_events(audit_path)
    by_event: dict[str, list[dict[str, object]]] = {}
    by_session: dict[str, list[dict[str, object]]] = {}
    protected_events: list[tuple[dict[str, object], str]] = []
    for source_event in events:
        event_id = _text(source_event.get("event_id"), label="audit event ID")
        caller = _mapping(source_event.get("caller"), label="audit caller")
        session_id = _text(caller.get("session_id"), label="audit session ID")
        by_event.setdefault(event_id, []).append(source_event)
        by_session.setdefault(session_id, []).append(source_event)
        role = _protected_audit_role(source_event)
        if role is not None:
            protected_events.append((source_event, role))

    matched_event_ids: dict[str, tuple[str, int]] = {}
    linked_counts = {"oos": 0, "lockbox": 0}
    for run in runs:
        for ordinal, ref in enumerate(run._access_refs):
            matched_event: dict[str, object] | None = None
            if ref._audit_event_id is not None:
                candidates = by_event.get(ref._audit_event_id, [])
                if len(candidates) != 1:
                    discrepancies.append(
                        _audit_discrepancy(
                            run,
                            ordinal,
                            "event_id_match_count",
                            f"audit_event_id matched {len(candidates)} events",
                        )
                    )
                    continue
                matched_event = candidates[0]
            else:
                candidates = by_session.get(ref._session_id, [])
                expected = 1 if run._dataset_role in _PROTECTED_ROLES else 0
                if len(candidates) != expected:
                    discrepancies.append(
                        _audit_discrepancy(
                            run,
                            ordinal,
                            "session_match_count",
                            f"session_id matched {len(candidates)} events; expected {expected}",
                        )
                    )
                    continue
                if candidates:
                    matched_event = candidates[0]
            if matched_event is None:
                continue
            event_id = _text(matched_event.get("event_id"), label="audit event ID")
            prior = matched_event_ids.setdefault(event_id, (run._run_id, ordinal))
            if prior != (run._run_id, ordinal):
                discrepancies.append(
                    _audit_discrepancy(
                        run,
                        ordinal,
                        "event_reused",
                        f"audit event already linked to {prior[0]} ordinal {prior[1]}",
                    )
                )
                continue
            caller = _mapping(matched_event.get("caller"), label="audit caller")
            dataset = _mapping(matched_event.get("dataset_ref"), label="audit dataset")
            outcome = matched_event.get("outcome")
            expected_outcome = "allowed" if ref._audit_event_id is not None else "denied"
            contradictions: list[str] = []
            if caller.get("session_id") != ref._session_id:
                contradictions.append("session_id differs")
            if dataset.get("role") != run._dataset_role:
                contradictions.append("dataset role differs")
            if outcome != expected_outcome:
                contradictions.append("authorization outcome differs")
            if contradictions:
                discrepancies.append(
                    _audit_discrepancy(
                        run,
                        ordinal,
                        "audit_contradiction",
                        "; ".join(contradictions),
                    )
                )
                continue
            classification = matched_event.get("classification")
            if classification == "oos_explicit":
                linked_counts["oos"] += 1
            elif classification == "fully_gated":
                linked_counts["lockbox"] += 1
            else:
                discrepancies.append(
                    _audit_discrepancy(
                        run,
                        ordinal,
                        "audit_classification",
                        "linked protected event has an unsupported classification",
                    )
                )
    for event, role in protected_events:
        event_id = _text(event.get("event_id"), label="audit event ID")
        if event_id in matched_event_ids:
            continue
        caller = _mapping(event.get("caller"), label="audit caller")
        discrepancies.append(
            {
                "kind": "audit_unreferenced",
                "audit_event_id": event_id,
                "session_id": _text(
                    caller.get("session_id"),
                    label="audit session ID",
                ),
                "dataset_role": role,
                "detail": (
                    "protected audit access event is not referenced by an "
                    "in-scope run"
                ),
            }
        )
    return (
        _AuditCounts(
            linked_counts["oos"],
            linked_counts["lockbox"],
            sum(role == "oos" for _event, role in protected_events),
            sum(role == "lockbox" for _event, role in protected_events),
        ),
        tuple(discrepancies),
    )


def _protected_audit_role(event: dict[str, object]) -> str | None:
    dataset = _mapping(event.get("dataset_ref"), label="audit dataset")
    role = _text(dataset.get("role"), label="audit dataset role").lower()
    expected = _PROTECTED_AUDIT_CLASSIFICATIONS.get(role)
    if expected is None:
        return None
    classification = _text(
        event.get("classification"),
        label="audit classification",
    )
    if classification != expected:
        raise _TrialLedgerError(
            f"{role} audit event requires classification {expected!r}"
        )
    outcome = _text(event.get("outcome"), label="audit outcome")
    if outcome not in {"allowed", "denied"}:
        raise _TrialLedgerError("protected audit outcome is unsupported")
    return role


def _read_audit_events(path: _Path) -> tuple[dict[str, object], ...]:
    if not isinstance(path, _Path) or not path.is_absolute():
        raise _TrialLedgerError("audit_path must be an absolute Path")
    if not path.exists():
        return ()
    if not path.is_file() or path.is_symlink():
        raise _TrialLedgerError("audit_path must be a concrete file")
    events: list[dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.endswith("\n"):
                    raise _TrialLedgerError("audit log has an incomplete final line")
                value = _json.loads(line, parse_constant=_reject_nonfinite)
                event = _mapping(value, label=f"audit event line {line_number}")
                if event.get("schema_version") != _AUDIT_SCHEMA_VERSION:
                    raise _TrialLedgerError("audit event schema is unsupported")
                event_type = event.get("event_type")
                if event_type == _ACCESS_AUDIT_EVENT_TYPE:
                    events.append(event)
                elif event_type not in _NON_ACCESS_AUDIT_EVENT_TYPES:
                    raise _TrialLedgerError("audit event type is unsupported")
    except (OSError, UnicodeError, _json.JSONDecodeError) as exc:
        raise _TrialLedgerError("cannot read append-only audit log") from exc
    return tuple(events)


def _audit_discrepancy(
    run: _LedgerRun,
    ordinal: int,
    kind: str,
    detail: str,
) -> dict[str, object]:
    return {
        "kind": kind,
        "run_id": run._run_id,
        "ordinal": ordinal,
        "detail": detail,
    }


def _validated_scope(
    runs_root: _Path,
    *,
    audit_path: _Path | None,
    sweep_id: str | None,
    since_unix_ns: int | None,
) -> tuple[_Path, dict[str, object]]:
    if not isinstance(runs_root, _Path) or not runs_root.is_absolute():
        raise _TrialLedgerError("runs_root must be an absolute Path")
    if not runs_root.is_dir() or runs_root.is_symlink():
        raise _TrialLedgerError("runs_root must be a concrete existing directory")
    if audit_path is not None and (
        not isinstance(audit_path, _Path) or not audit_path.is_absolute()
    ):
        raise _TrialLedgerError("audit_path must be an absolute Path or None")
    if sweep_id is not None and since_unix_ns is not None:
        raise _TrialLedgerError("trial scope accepts sweep_id or since_unix_ns, not both")
    if sweep_id is not None and (type(sweep_id) is not str or not sweep_id):
        raise _TrialLedgerError("sweep_id must be non-empty exact text")
    if since_unix_ns is not None and (type(since_unix_ns) is not int or since_unix_ns <= 0):
        raise _TrialLedgerError("since_unix_ns must be an exact positive int")
    kind = "sweep" if sweep_id is not None else "since" if since_unix_ns is not None else "all"
    return runs_root.resolve(), {
        "kind": kind,
        "sweep_id": sweep_id,
        "since_unix_ns": since_unix_ns,
        "audit_path": None if audit_path is None else str(audit_path),
    }


def _scope_fingerprints(runs_root: _Path, sweep_id: str | None) -> set[str] | None:
    if sweep_id is None:
        return None
    plan = _read_sweep_plan(runs_root, sweep_id)
    return {child._experiment_fingerprint for child in plan._children}


def _projection_in_scope(
    projection: _RunProjection,
    *,
    fingerprints: set[str] | None,
    since_unix_ns: int | None,
) -> bool:
    if (
        fingerprints is not None
        and projection._record._experiment_fingerprint not in fingerprints
    ):
        return False
    return since_unix_ns is None or projection._state._created_unix_ns >= since_unix_ns


def _files_duplicate_count(
    runs_root: _Path,
    *,
    sweep_id: str | None,
    since_unix_ns: int | None,
) -> int:
    collection = runs_root / "sweeps"
    if not collection.exists():
        return 0
    if not collection.is_dir() or collection.is_symlink():
        raise _TrialLedgerError("sweeps path must be a concrete directory")
    children = tuple(sorted(collection.iterdir(), key=lambda child: child.name))
    for child in children:
        if not child.is_dir() or child.is_symlink():
            raise _TrialLedgerError(
                f"unexpected sweep collection entry {child.name!r}"
            )
    names = (sweep_id,) if sweep_id is not None else tuple(child.name for child in children)
    total = 0
    for name in names:
        plan = _read_sweep_plan(runs_root, name)
        created = plan._document.get("created_unix_ns")
        if since_unix_ns is not None and (type(created) is not int or created < since_unix_ns):
            continue
        duplicates = _sequence(
            plan._document.get("duplicates_collapsed"),
            label="sweep collapsed duplicates",
        )
        total += len(duplicates)
    return total


def _index_duplicate_count(
    connection: _sqlite3.Connection,
    *,
    sweep_id: str | None,
    since_unix_ns: int | None,
) -> int:
    if sweep_id is not None:
        row = connection.execute(
            "SELECT duplicate_count FROM sweep WHERE sweep_id = ?",
            (sweep_id,),
        ).fetchone()
        if row is None:
            raise _TrialLedgerError(f"sweep {sweep_id!r} is absent from the index")
        return _typing.cast("int", row[0])
    if since_unix_ns is not None:
        row = connection.execute(
            "SELECT COALESCE(SUM(duplicate_count), 0) FROM sweep WHERE created_unix_ns >= ?",
            (since_unix_ns,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT COALESCE(SUM(duplicate_count), 0) FROM sweep"
        ).fetchone()
    return _typing.cast("int", row[0])


def _distribution(values: _Iterable[int]) -> dict[str, object]:
    ordered = tuple(sorted(values))
    if not ordered:
        return {"count": 0, "min": None, "median": None, "max": None}
    middle = len(ordered) // 2
    median: int | float = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": median,
        "max": ordered[-1],
    }


def _origin(value: object, *, label: str) -> dict[str, object]:
    document = _mapping(value, label=label)
    if set(document) != {"params", "plugin_id", "version"}:
        raise _TrialLedgerError(f"{label} origin fields differ")
    return document


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _TrialLedgerError(f"{label} must be an object")
    return value


def _sequence(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise _TrialLedgerError(f"{label} must be an array")
    return value


def _text(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise _TrialLedgerError(f"{label} must be non-empty exact text")
    return value


def _decoded_json(value: object, *, label: str) -> object:
    if type(value) is not str:
        raise _TrialLedgerError(f"{label} must be encoded JSON text")
    try:
        return _json.loads(value, parse_constant=_reject_nonfinite)
    except _json.JSONDecodeError as exc:
        raise _TrialLedgerError(f"{label} is malformed") from exc


def _canonical_json(value: object) -> str:
    try:
        return _json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _TrialLedgerError("ledger input is not canonical JSON data") from exc


def _reject_nonfinite(value: str) -> _typing.NoReturn:
    raise ValueError(f"non-finite JSON token {value!r} is forbidden")
