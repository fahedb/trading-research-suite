"""Trial-counting matrix, files/index equivalence, and audit joins."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.test_run_index_query import _complete
from tests.test_runs import BASE_NS, _evaluated_inventory, _identity, _register, _start
from trading_research.experiments.runs import (
    RunState,
    _finish_run,
    _record_run_access,
    _record_run_access_attempt,
)
from trading_research.index.build import _rebuild_index
from trading_research.index.trials import (
    _build_ledger,
    _files_trial_ledger,
    _index_trial_ledger,
    _LedgerEvaluation,
    _LedgerInput,
    _LedgerPolicyExecution,
    _LedgerRun,
    _policy_from_document,
    _TrialLedgerError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _evaluation(
    *,
    plane: str = "A",
    fitted: str = "fit-1",
    question: str = "question-1",
    metric: str = "metric-1",
    policy: str | None = None,
    cost_unverified: bool = False,
) -> _LedgerEvaluation:
    entry_question = json.dumps(
        {"entry": question},
        sort_keys=True,
        separators=(",", ":"),
    )
    full_question = json.dumps(
        {"entry": question, "metric_name": metric},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _LedgerEvaluation(
        0,
        plane,
        fitted,
        f"output-{fitted}",
        full_question,
        entry_question,
        metric,
        10,
        cost_unverified,
        policy,
    )


def _run(
    fingerprint: str,
    *,
    run_id: str,
    evaluations: tuple[_LedgerEvaluation, ...] = (),
    outcome_class: str | None = "evaluated",
    policies: tuple[_LedgerPolicyExecution, ...] = (),
) -> _LedgerRun:
    return _LedgerRun(
        run_id,
        fingerprint,
        BASE_NS,
        "COMPLETE",
        True,
        outcome_class,
        "train",
        False,
        False,
        (),
        evaluations,
        policies,
    )


def _ledger(*runs: _LedgerRun) -> dict[str, object]:
    return _build_ledger(
        _LedgerInput(tuple(runs), 0, ()),
        scope={
            "kind": "all",
            "sweep_id": None,
            "since_unix_ns": None,
            "audit_path": None,
        },
        audit_path=None,
    )._document


def test_trial_counting_matrix_collapses_folds_and_attempts() -> None:
    one_fold = _ledger(_run("h1", run_id="r1", evaluations=(_evaluation(),)))
    assert one_fold["plane_a_trials"] == 1

    five_folds = _ledger(
        _run(
            "h1",
            run_id="r1",
            evaluations=tuple(_evaluation(fitted=f"fit-{index}") for index in range(5)),
        )
    )
    assert five_folds["plane_a_trials"] == 1
    assert five_folds["fold_multiplicity"] == {
        "count": 1,
        "min": 5,
        "median": 5,
        "max": 5,
    }

    questions = tuple(
        _evaluation(question=f"outcome-{outcome}-evaluator-{evaluator}")
        for outcome in range(2)
        for evaluator in range(3)
    )
    six_questions = _ledger(_run("h1", run_id="r1", evaluations=questions))
    assert six_questions["plane_a_trials"] == 6

    attempts = _ledger(
        *(
            _run("h1", run_id=f"r{index}", evaluations=(_evaluation(),))
            for index in range(3)
        )
    )
    assert attempts["plane_a_trials"] == 1
    assert attempts["distinct_hypotheses"] == 1
    assert attempts["attempts_collapsed"] == 2


def test_trial_counting_matrix_exclusions_policy_fanout_and_plane_b() -> None:
    policy_origin = json.dumps(
        {"params": {"threshold": 1}, "plugin_id": "threshold_v1", "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    execution = _LedgerPolicyExecution(policy_origin)
    excluded = _ledger(
        _run("h1", run_id="descriptive", evaluations=(), outcome_class="descriptive"),
        _run(
            "h2",
            run_id="failed",
            evaluations=(),
            outcome_class="pre_evaluation_failure",
        ),
        _run(
            "h3",
            run_id="policies-only",
            evaluations=(),
            outcome_class="descriptive",
            policies=(execution, execution, execution, execution),
        ),
    )
    assert excluded["plane_a_trials"] == 0
    assert excluded["plane_b_trials"] == 0
    assert excluded["descriptive_runs"] == 2
    assert excluded["pre_evaluation_failures"] == 1
    # Identical attempts of one execution are collapsed mechanically.
    assert excluded["policies_executed_not_evaluated"] == 1

    plane_b_entries = tuple(
        _evaluation(
            plane="B",
            fitted=f"fit-{fold}",
            question=f"policy-{policy}-evaluator-{evaluator}",
            metric="trade_count",
            policy=json.dumps(
                {"params": {}, "plugin_id": f"policy-{policy}", "version": 1},
                sort_keys=True,
                separators=(",", ":"),
            ),
            cost_unverified=True,
        )
        for fold in range(5)
        for policy in range(4)
        for evaluator in range(2)
    )
    plane_b = _ledger(_run("h1", run_id="r1", evaluations=plane_b_entries))
    assert plane_b["plane_a_trials"] == 0
    assert plane_b["plane_b_trials"] == 8
    assert plane_b["unverified_cost_semantics_entries"] == 40


def test_executed_policy_diagnostic_collapses_fold_varying_intent_shapes() -> None:
    policy_origin = json.dumps(
        {"params": {"threshold": 1}, "plugin_id": "threshold_v1", "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    executions = tuple(
        _policy_from_document(
            {
                "policy": json.loads(policy_origin),
                "intent_schema_id": "target_exposure_intent_v1",
                "intent_shape": {
                    "rows": rows,
                    "first_key": first_key,
                    "last_key": first_key + rows - 1,
                },
            }
        )
        for rows, first_key in ((3, 10), (4, 20), (5, 30))
    )
    report = _ledger(
        _run(
            "h1",
            run_id="fold-varying-policy",
            evaluations=(_evaluation(),),
            policies=executions,
        )
    )
    assert report["policies_executed_not_evaluated"] == 1
    assert report["plane_a_trials"] == 1
    assert report["plane_b_trials"] == 0


def test_one_hundred_hypotheses_remain_one_hundred_trials() -> None:
    report = _ledger(
        *(
            _run(
                f"hypothesis-{index}",
                run_id=f"run-{index}",
                evaluations=(_evaluation(),),
            )
            for index in range(100)
        )
    )
    assert report["plane_a_trials"] == 100
    assert report["distinct_hypotheses"] == 100
    assert "NOT statistically independent" in str(report["caveat"])


def test_files_only_and_index_ledgers_are_identical(tmp_path: Path) -> None:
    _complete(tmp_path, run_index=1, parameter=1, value=0.25)
    _complete(tmp_path, run_index=2, parameter=1, value=0.25)
    _complete(tmp_path, run_index=3, parameter=2, value=0.75)
    _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 10_000,
    )
    files = _files_trial_ledger(tmp_path)
    indexed = _index_trial_ledger(tmp_path)
    assert files._document == indexed._document
    assert files._document["plane_a_trials"] == 2
    assert files._document["attempts_collapsed"] == 1


def test_plural_audit_refs_join_allowed_and_denied_events(tmp_path: Path) -> None:
    identity = _identity(
        config={
            "dataset": {
                "dataset_id": "OOS_FIXTURE",
                "manifest_sha256": "1" * 64,
                "role": "oos",
            },
            "model": {"parameter": 1},
        }
    )
    run_id = _register(tmp_path, run_index=1, identity=identity)
    _start(tmp_path, run_id)
    allowed_session = "00000000-0000-4000-8000-000000000001"
    denied_session = "00000000-0000-4000-8000-000000000002"
    allowed_event = "00000000-0000-4000-8000-000000000003"
    denied_event = "00000000-0000-4000-8000-000000000004"
    _record_run_access_attempt(
        runs_root=tmp_path,
        run_id=run_id,
        session_id=allowed_session,
        now_unix_ns=BASE_NS + 110,
    )
    _record_run_access(
        runs_root=tmp_path,
        run_id=run_id,
        oos_touched=True,
        lockbox_touched=False,
        session_id=allowed_session,
        audit_event_id=allowed_event,
        now_unix_ns=BASE_NS + 111,
    )
    _record_run_access_attempt(
        runs_root=tmp_path,
        run_id=run_id,
        session_id=denied_session,
        now_unix_ns=BASE_NS + 112,
    )
    _finish_run(
        runs_root=tmp_path,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=0.25),
        failure_reason=None,
        now_unix_ns=BASE_NS + 200,
    )
    audit_path = (tmp_path / "audit.jsonl").resolve()
    events = (
        _audit_event(
            event_id=allowed_event,
            session_id=allowed_session,
            outcome="allowed",
        ),
        _audit_event(
            event_id=denied_event,
            session_id=denied_session,
            outcome="denied",
        ),
        {
            "schema_version": "1",
            "event_type": "snapshot_binding",
            "event_id": "00000000-0000-4000-8000-000000000005",
            "source_access_event_id": allowed_event,
        },
        _audit_event(
            event_id="00000000-0000-4000-8000-000000000006",
            session_id="00000000-0000-4000-8000-000000000007",
            outcome="allowed",
            role="snapshot",
            classification="fully_gated",
        ),
    )
    events[0]["reason"] = "בדיקת OOS מאושרת"
    audit_path.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )

    report = _files_trial_ledger(tmp_path, audit_path=audit_path)
    assert report._document["oos_access_events"] == 2
    assert report._document["oos_access_events_audit_total"] == 2
    assert report._document["lockbox_access_events"] == 0
    assert report._document["lockbox_access_events_audit_total"] == 0
    assert report._document["oos_evaluated_runs"] == 1
    assert report._document["audit_discrepancies"] == []

    denied_caller = events[1]["caller"]
    assert type(denied_caller) is dict
    denied_caller["session_id"] = "00000000-0000-4000-8000-000000000099"
    audit_path.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )
    discrepant = _files_trial_ledger(tmp_path, audit_path=audit_path)
    assert discrepant._has_errors
    assert discrepant._document["audit_discrepancies"]


def test_unreferenced_protected_audit_event_is_a_discrepancy(tmp_path: Path) -> None:
    audit_path = (tmp_path / "audit.jsonl").resolve()
    event_id = "00000000-0000-4000-8000-000000000011"
    session_id = "00000000-0000-4000-8000-000000000012"
    audit_path.write_text(
        json.dumps(
            _audit_event(
                event_id=event_id,
                session_id=session_id,
                outcome="allowed",
            ),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = _files_trial_ledger(tmp_path, audit_path=audit_path)
    assert report._has_errors
    assert report._document["oos_access_events"] == 0
    assert report._document["oos_access_events_audit_total"] == 1
    assert report._document["audit_discrepancies"] == [
        {
            "kind": "audit_unreferenced",
            "audit_event_id": event_id,
            "session_id": session_id,
            "dataset_role": "oos",
            "detail": (
                "protected audit access event is not referenced by an in-scope run"
            ),
        }
    ]


def test_narrowed_scope_shows_audit_wide_total_and_out_of_scope_reference(
    tmp_path: Path,
) -> None:
    events: list[dict[str, object]] = []
    for ordinal, run_index in enumerate((20, 21), start=1):
        identity = _identity(
            config={
                "dataset": {
                    "dataset_id": f"OOS_FIXTURE_{ordinal}",
                    "manifest_sha256": str(ordinal) * 64,
                    "role": "oos",
                },
                "model": {"parameter": ordinal},
            }
        )
        run_id = _register(tmp_path, run_index=run_index, identity=identity)
        start_offset = 1_000 + ordinal * 100
        _start(tmp_path, run_id, offset=start_offset)
        session_id = f"00000000-0000-4000-8000-{ordinal:012d}"
        event_id = f"00000000-0000-4000-9000-{ordinal:012d}"
        _record_run_access_attempt(
            runs_root=tmp_path,
            run_id=run_id,
            session_id=session_id,
            now_unix_ns=BASE_NS + start_offset + 1,
        )
        _record_run_access(
            runs_root=tmp_path,
            run_id=run_id,
            oos_touched=True,
            lockbox_touched=False,
            session_id=session_id,
            audit_event_id=event_id,
            now_unix_ns=BASE_NS + start_offset + 2,
        )
        _finish_run(
            runs_root=tmp_path,
            run_id=run_id,
            terminal_state=RunState.COMPLETE,
            inventory=_evaluated_inventory(value=ordinal / 10),
            failure_reason=None,
            now_unix_ns=BASE_NS + start_offset + 3,
        )
        events.append(
            _audit_event(
                event_id=event_id,
                session_id=session_id,
                outcome="allowed",
            )
        )
    audit_path = (tmp_path / "audit.jsonl").resolve()
    audit_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    report = _files_trial_ledger(
        tmp_path,
        audit_path=audit_path,
        since_unix_ns=BASE_NS + 21,
    )
    assert report._has_errors
    assert report._document["runs_examined"] == 1
    assert report._document["oos_access_events"] == 1
    assert report._document["oos_access_events_audit_total"] == 2
    discrepancies = report._document["audit_discrepancies"]
    assert type(discrepancies) is list
    assert [item["kind"] for item in discrepancies] == ["audit_unreferenced"]
    assert discrepancies[0]["audit_event_id"] == events[0]["event_id"]


def test_protected_run_without_authorization_reference_is_a_discrepancy(
    tmp_path: Path,
) -> None:
    protected = _LedgerRun(
        "protected-run",
        "protected-hypothesis",
        BASE_NS,
        "COMPLETE",
        True,
        "evaluated",
        "oos",
        True,
        False,
        (),
        (_evaluation(),),
        (),
    )
    report = _build_ledger(
        _LedgerInput((protected,), 0, ()),
        scope={
            "kind": "all",
            "sweep_id": None,
            "since_unix_ns": None,
            "audit_path": str((tmp_path / "audit.jsonl").resolve()),
        },
        audit_path=(tmp_path / "audit.jsonl").resolve(),
    )
    assert report._has_errors
    assert report._document["audit_discrepancies"] == [
        {
            "kind": "access_ref_missing",
            "run_id": "protected-run",
            "detail": "protected run has no recorded authorization session",
        }
    ]


def _audit_event(
    *,
    event_id: str,
    session_id: str,
    outcome: str,
    role: str = "oos",
    classification: str = "oos_explicit",
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "event_type": "dataset_access_decision",
        "event_id": event_id,
        "outcome": outcome,
        "caller": {"session_id": session_id},
        "dataset_ref": {"role": role},
        "classification": classification,
    }


def test_fold_invariant_canary_fails_loudly() -> None:
    run = _run(
        "h1",
        run_id="r1",
        evaluations=(
            _evaluation(fitted="fit-a", question="question-a"),
            _evaluation(fitted="fit-b", question="question-b"),
        ),
    )
    with pytest.raises(_TrialLedgerError, match="fold invariant failed"):
        _ledger(run)
