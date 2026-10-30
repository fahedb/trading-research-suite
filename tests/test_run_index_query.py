from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.test_experiments import _execution_document
from tests.test_runs import BASE_NS, _evaluated_inventory, _identity, _register, _start
from trading_research.core import MetricValue, StageOrigin
from trading_research.experiments.config import _optional_execution_spec
from trading_research.experiments.evaluation import (
    _EvaluationInventory,
    _RunOutcomeClass,
    _TradeEvaluationInventoryEntry,
)
from trading_research.experiments.runs import (
    RunState,
    _finish_run,
    _record_run_access,
)
from trading_research.index.build import _IndexError, _rebuild_index
from trading_research.index.query import _compare_metric, _query_runs

if TYPE_CHECKING:
    from pathlib import Path


def _config(*, parameter: int, role: str = "TRAIN") -> dict[str, object]:
    return {
        "dataset": {
            "dataset_id": f"{role}_FIXTURE",
            "manifest_sha256": "1" * 64,
            "role": role,
        },
        "model": {"parameter": parameter},
    }


def _complete(
    root: Path,
    *,
    run_index: int,
    parameter: int,
    value: float,
    protected: bool = False,
    inventory: _EvaluationInventory | None = None,
) -> tuple[str, str]:
    identity = _identity(config=_config(parameter=parameter))
    run_id = _register(root, run_index=run_index, identity=identity)
    _start(root, run_id)
    if protected:
        _record_run_access(
            runs_root=root,
            run_id=run_id,
            oos_touched=True,
            lockbox_touched=False,
            now_unix_ns=BASE_NS + 110 + run_index,
        )
    _finish_run(
        runs_root=root,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=value) if inventory is None else inventory,
        failure_reason=None,
        now_unix_ns=BASE_NS + 200 + run_index,
    )
    return run_id, identity._experiment_fingerprint


def _build(root: Path) -> None:
    _rebuild_index(
        root,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 10_000,
    )


def test_query_filters_config_and_excludes_protected_rows_by_default(tmp_path: Path) -> None:
    first_id, first_fp = _complete(
        tmp_path,
        run_index=1,
        parameter=1,
        value=0.25,
    )
    _complete(
        tmp_path,
        run_index=2,
        parameter=2,
        value=0.75,
        protected=True,
    )
    _build(tmp_path)

    result = _query_runs(
        tmp_path,
        states=("COMPLETE",),
        canonical=True,
        dataset_roles=("TRAIN",),
        config_predicates=("/model/parameter=1",),
    )

    assert [row["run_id"] for row in result._rows] == [first_id]
    assert result._rows[0]["experiment_fingerprint"] == first_fp
    included = _query_runs(tmp_path, include_protected=True, limit=10)
    assert len(included._rows) == 2
    assert sum(bool(row["protected"]) for row in included._rows) == 1


def test_metric_query_preserves_nan_bits_effective_samples_and_order(tmp_path: Path) -> None:
    _complete(tmp_path, run_index=1, parameter=1, value=float("nan"))
    best_id, _best_fp = _complete(tmp_path, run_index=2, parameter=2, value=0.75)
    _build(tmp_path)

    result = _query_runs(
        tmp_path,
        metric_name="rank_ic",
        plane="A",
        order_by="metric",
    )

    assert result._rows[0]["run_id"] == best_id
    assert result._rows[0]["value"] == 0.75
    assert result._rows[0]["effective_samples"] == 4
    assert result._rows[1]["value"] is None
    assert result._rows[1]["value_is_nan"] is True
    assert result._rows[1]["value_bits_hex"] == "7ff8000000000000"


def test_compare_groups_by_the_approved_question_and_labels_plain_mean(tmp_path: Path) -> None:
    _first_id, first_fp = _complete(
        tmp_path,
        run_index=1,
        parameter=1,
        value=0.25,
    )
    _second_id, second_fp = _complete(
        tmp_path,
        run_index=2,
        parameter=2,
        value=0.75,
    )
    _build(tmp_path)

    result = _compare_metric(
        tmp_path,
        metric_name="rank_ic",
        fingerprints=(first_fp, second_fp),
        plane="A",
    )

    assert [group["descriptive_cross_fold_mean"] for group in result._groups] == [0.75, 0.25]
    assert all(group["descriptive_summary_only"] is True for group in result._groups)
    assert all(group["fold_measurement_count"] == 1 for group in result._groups)
    questions = [cast("dict[str, object]", group["question"]) for group in result._groups]
    folds = [
        cast("list[dict[str, object]]", group["fold_measurements"])
        for group in result._groups
    ]
    assert all(question["metric_name"] == "rank_ic" for question in questions)
    assert all(group[0]["effective_samples"] == 4 for group in folds)


def test_config_predicate_is_bound_data_not_an_sql_surface(tmp_path: Path) -> None:
    _complete(tmp_path, run_index=1, parameter=1, value=0.25)
    _build(tmp_path)

    shaped_like_injection = "/model/parameter=1' OR 1=1 --"
    result = _query_runs(tmp_path, config_predicates=(shaped_like_injection,))

    assert result._rows == ()
    assert _query_runs(tmp_path)._rows
    with pytest.raises(_IndexError, match="JSON pointer"):
        _query_runs(tmp_path, config_predicates=("model/parameter=1",))


def test_positive_protected_filter_requires_explicit_inclusion(tmp_path: Path) -> None:
    _complete(
        tmp_path,
        run_index=1,
        parameter=1,
        value=0.25,
        protected=True,
    )
    _build(tmp_path)
    with pytest.raises(_IndexError, match="include_protected"):
        _query_runs(tmp_path, oos_touched=True)
    result = _query_runs(tmp_path, oos_touched=True, include_protected=True)
    assert len(result._rows) == 1
    assert result._rows[0]["protected"] is True


def test_plane_b_rows_never_lose_unverified_cost_marking(tmp_path: Path) -> None:
    execution_spec = _optional_execution_spec(_execution_document())
    assert execution_spec is not None
    inventory = _EvaluationInventory(
        _outcome_class=_RunOutcomeClass.EVALUATED,
        _entries=(),
        _policy_executions=(),
        _failure_reason=None,
        _trade_entries=(
            _TradeEvaluationInventoryEntry(
                _fitted_state_identity_key="5" * 64,
                _model_output_artifact_identity_key="6" * 64,
                _policy_origin=StageOrigin("threshold_v1", 1, {"threshold": 0.1}),
                _execution_spec=execution_spec,
                _trade_log_artifact_identity_key="7" * 64,
                _equity_curve_artifact_identity_key="8" * 64,
                _evaluator_origin=StageOrigin(
                    "trade_summary_v1",
                    1,
                    {"min_effective_trades": 0},
                ),
                _metric_names=("trade_count",),
                _comparison_tolerance=None,
                _metrics=(MetricValue("trade_count", 3.0, 3),),
            ),
        ),
    )
    _run_id, fingerprint = _complete(
        tmp_path,
        run_index=1,
        parameter=1,
        value=0.0,
        inventory=inventory,
    )
    _build(tmp_path)

    queried = _query_runs(tmp_path, metric_name="trade_count", plane="B")
    assert queried._rows[0]["cost_semantics_unverified"] is True
    compared = _compare_metric(
        tmp_path,
        metric_name="trade_count",
        fingerprints=(fingerprint,),
        plane="B",
    )
    assert compared._groups[0]["cost_semantics_unverified"] is True
    question = cast("dict[str, object]", compared._groups[0]["question"])
    assert "outcome" not in question
    assert set(question) == {
        "policy",
        "execution_spec",
        "evaluator",
        "metric_name",
    }
