"""Slice-3G Plane-A evaluator semantics and inventory goldens."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from trading_research.core import (
    BoundaryPolicy,
    ClockDomain,
    ContractViolationError,
    DatasetIdentity,
    Evaluator,
    MetricValue,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewSpan,
)
from trading_research.core.research_frames import (
    ModelOutputFrame,
    OutcomeFrame,
    _make_model_output_frame,
    _make_outcome_frame,
)
from trading_research.decisions.reference import AbstainPolicy, ThresholdPolicy
from trading_research.evaluation import EVALUATORS
from trading_research.evaluation.execution import (
    _execute_evaluator,
    _snapshot_evaluator,
)
from trading_research.evaluation.reference import (
    RankIC,
    SignAccuracy,
    _average_ranks,
)
from trading_research.experiments.decisions import _fan_out_model_output
from trading_research.experiments.evaluation import (
    _descriptive_inventory,
    _evaluate_plane_a_inventory,
    _FittedOutput,
    _OutcomeTarget,
    _PolicyExecutionRecord,
    _pre_evaluation_failure_inventory,
    _preflight_plane_a,
    _record_policy_executions,
    _RunOutcomeClass,
)
from trading_research.outcomes.reference import ForwardLogReturn

if TYPE_CHECKING:
    from collections.abc import Mapping

DOMAIN = ClockDomain("evaluation.native")
SOURCE = DatasetIdentity("TRAIN_EVALUATION", "1" * 64)
OUTPUT_COLUMN = "score"
OUTCOME_COLUMN = "target.forward_log_return"


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _view(*, stop: int = 30) -> ViewSpan:
    return ViewSpan(
        (SOURCE,),
        TimeUnit.SECONDS,
        DOMAIN,
        _key(10),
        _key(10),
        _key(stop),
    )


def _spec(horizon: int = 1) -> ForwardLogReturn:
    return ForwardLogReturn(
        horizon_rows=horizon,
        decision_delay_rows=1,
        reference_price_column="close",
        boundary_policy=BoundaryPolicy.NAN,
    )


def _model_output(
    values: tuple[float, ...],
    *,
    keys: tuple[int, ...] | None = None,
    view: ViewSpan | None = None,
) -> ModelOutputFrame:
    actual_keys = tuple(range(10, 10 + len(values))) if keys is None else keys
    return _make_model_output_frame(
        view=_view() if view is None else view,
        keys=np.asarray(actual_keys, dtype=np.int64),
        columns={OUTPUT_COLUMN: np.asarray(values, dtype=np.float64)},
        origins={OUTPUT_COLUMN: StageOrigin("model_v1", 1, {})},
    )


def _outcomes(
    values: tuple[float, ...],
    *,
    spec: ForwardLogReturn | None = None,
    keys: tuple[int, ...] | None = None,
    view: ViewSpan | None = None,
) -> OutcomeFrame:
    actual_spec = _spec() if spec is None else spec
    actual_keys = tuple(range(10, 10 + len(values))) if keys is None else keys
    return _make_outcome_frame(
        view=_view() if view is None else view,
        keys=np.asarray(actual_keys, dtype=np.int64),
        columns={OUTCOME_COLUMN: np.asarray(values, dtype=np.float64)},
        origins={
            OUTCOME_COLUMN: StageOrigin(
                actual_spec.outcome_id,
                actual_spec.version,
                actual_spec.params,
            )
        },
    )


def _rank(*, minimum: int = 2) -> RankIC:
    return RankIC(
        output_column=OUTPUT_COLUMN,
        outcome_column=OUTCOME_COLUMN,
        min_effective_samples=minimum,
    )


def _sign(*, minimum: int = 1) -> SignAccuracy:
    return SignAccuracy(
        output_column=OUTPUT_COLUMN,
        outcome_column=OUTCOME_COLUMN,
        min_effective_samples=minimum,
    )


def _evaluate(
    evaluator: object,
    output: ModelOutputFrame,
    outcome: OutcomeFrame,
) -> MetricValue:
    plan = _snapshot_evaluator(evaluator)
    return _execute_evaluator(
        evaluator,
        plan,
        model_output=output,
        outcome_targets=outcome,
    )[0]


class _ObservedRankIC(RankIC):
    def __init__(self, *, output_column: str = OUTPUT_COLUMN) -> None:
        super().__init__(
            output_column=output_column,
            outcome_column=OUTCOME_COLUMN,
            min_effective_samples=2,
        )
        self.calls = 0

    def evaluate(
        self,
        model_output: ModelOutputFrame,
        outcome_targets: OutcomeFrame,
    ) -> Mapping[str, MetricValue]:
        self.calls += 1
        return super().evaluate(model_output, outcome_targets)


def test_registry_contains_exactly_the_two_closed_reference_evaluators() -> None:
    assert EVALUATORS.names() == {"rank_ic_v1", "sign_accuracy_v1"}
    rank = EVALUATORS.create(
        "rank_ic_v1",
        output_column=OUTPUT_COLUMN,
        outcome_column=OUTCOME_COLUMN,
        min_effective_samples=2,
    )
    sign = EVALUATORS.create(
        "sign_accuracy_v1",
        output_column=OUTPUT_COLUMN,
        outcome_column=OUTCOME_COLUMN,
        min_effective_samples=1,
    )
    assert isinstance(rank, Evaluator)
    assert isinstance(sign, Evaluator)


def test_average_tie_ranks_are_computed_explicitly() -> None:
    ranks = _average_ranks(np.array([1.0, 2.0, 2.0, 4.0], dtype=np.float64))
    np.testing.assert_array_equal(ranks, np.array([1.0, 2.5, 2.5, 4.0]))


def test_rank_ic_tie_golden_and_effective_count() -> None:
    metric = _evaluate(
        _rank(),
        _model_output((1.0, 2.0, 2.0, 4.0)),
        _outcomes((1.0, 2.0, 3.0, 4.0)),
    )
    assert metric.name == "rank_ic"
    assert metric.effective_samples == 4
    assert metric.value == 3.0 / np.sqrt(10.0)


@pytest.mark.parametrize(
    ("output", "outcome", "minimum", "effective"),
    [
        ((np.nan, np.nan), (np.nan, np.nan), 2, 0),
        ((1.0, np.nan), (2.0, 3.0), 2, 1),
        ((1.0, 1.0, 1.0), (1.0, 2.0, 3.0), 2, 3),
    ],
    ids=("all-nan", "below-minimum", "constant-vector"),
)
def test_rank_ic_undefined_edge_cases_return_nan_with_honest_count(
    output: tuple[float, ...],
    outcome: tuple[float, ...],
    minimum: int,
    effective: int,
) -> None:
    metric = _evaluate(
        _rank(minimum=minimum),
        _model_output(output),
        _outcomes(outcome),
    )
    assert np.isnan(metric.value)
    assert metric.effective_samples == effective


def test_sparse_key_alignment_occurs_before_pair_filtering() -> None:
    metric = _evaluate(
        _rank(),
        _model_output((1.0, 3.0), keys=(10, 12)),
        _outcomes((1.0, 99.0, 3.0), keys=(10, 11, 12)),
    )
    assert metric.value == 1.0
    assert metric.effective_samples == 2


def test_sign_accuracy_excludes_nan_exact_zero_and_signed_zero() -> None:
    output = _model_output((1.0, -1.0, 0.0, -0.0, np.nan, 1.0))
    outcome = _outcomes((2.0, 2.0, 3.0, -4.0, 1.0, np.nan))
    rank = _evaluate(_rank(), output, outcome)
    sign = _evaluate(_sign(), output, outcome)

    assert rank.effective_samples == 4
    assert sign.effective_samples == 2
    assert sign.value == 0.5


def test_sign_accuracy_below_minimum_returns_nan() -> None:
    metric = _evaluate(
        _sign(minimum=3),
        _model_output((1.0, -1.0, 0.0)),
        _outcomes((2.0, -2.0, 4.0)),
    )
    assert metric.effective_samples == 2
    assert np.isnan(metric.value)


@pytest.mark.parametrize(
    ("evaluator", "minimum"),
    [(RankIC, 1), (SignAccuracy, 0), (RankIC, True)],
)
def test_minimum_effective_sample_contract_is_exact(
    evaluator: type[RankIC] | type[SignAccuracy],
    minimum: object,
) -> None:
    with pytest.raises(ContractViolationError, match="min_effective_samples"):
        evaluator(
            output_column=OUTPUT_COLUMN,
            outcome_column=OUTCOME_COLUMN,
            min_effective_samples=minimum,  # type: ignore[arg-type]
        )


def test_requirement_mismatch_refuses_before_evaluator_or_inference_work() -> None:
    evaluator = _ObservedRankIC(output_column="missing")
    with pytest.raises(ContractViolationError, match="missing model output"):
        _preflight_plane_a(
            output_column_names=(OUTPUT_COLUMN,),
            outcome_column_names=(OUTCOME_COLUMN,),
            evaluators=(evaluator,),
        )
    assert evaluator.calls == 0


def _policy_records(
    output: ModelOutputFrame,
    count: int,
) -> tuple[_PolicyExecutionRecord, ...]:
    candidates = (
        AbstainPolicy(),
        ThresholdPolicy(output_column=OUTPUT_COLUMN, threshold=0.1),
        ThresholdPolicy(output_column=OUTPUT_COLUMN, threshold=0.2),
    )[:count]
    intents = _fan_out_model_output(model_output=output, policies=candidates)
    return _record_policy_executions(policies=candidates, intents=intents)


def test_plane_a_inventory_is_exactly_f_times_s_times_e_without_policy_axis() -> None:
    output = _model_output((1.0, -1.0, 0.5, -0.5))
    outputs = (
        _FittedOutput("a" * 64, "b" * 64, output),
        _FittedOutput("c" * 64, "d" * 64, output),
    )
    first_spec = _spec(1)
    second_spec = _spec(2)
    targets = (
        _OutcomeTarget(first_spec, _outcomes((1.0, -1.0, 1.0, -1.0), spec=first_spec)),
        _OutcomeTarget(second_spec, _outcomes((2.0, -2.0, 2.0, -2.0), spec=second_spec)),
    )
    evaluators = (_rank(), _sign())

    without_policies = _evaluate_plane_a_inventory(
        fitted_outputs=outputs,
        outcome_targets=targets,
        evaluators=evaluators,
        policy_executions=(),
    )
    with_policies = _evaluate_plane_a_inventory(
        fitted_outputs=outputs,
        outcome_targets=targets,
        evaluators=evaluators,
        policy_executions=_policy_records(output, 3),
    )

    assert len(without_policies._entries) == 2 * 2 * 2
    assert len(with_policies._entries) == 2 * 2 * 2
    assert len(with_policies._policy_executions) == 3
    assert all(not hasattr(entry, "_policy") for entry in with_policies._entries)
    assert {
        (
            entry._fitted_state_identity_key,
            entry._outcome_origin.params["horizon_rows"],
            entry._evaluator_origin.plugin_id,
        )
        for entry in with_policies._entries
    } == {
        (fitted, horizon, evaluator)
        for fitted in ("a" * 64, "c" * 64)
        for horizon in (1, 2)
        for evaluator in ("rank_ic_v1", "sign_accuracy_v1")
    }


def test_descriptive_and_pre_evaluation_failure_records_have_zero_trials() -> None:
    output = _model_output((1.0,))
    records = _policy_records(output, 1)
    descriptive = _descriptive_inventory(policy_executions=records)
    failed = _pre_evaluation_failure_inventory(
        reason="dependency unavailable",
        policy_executions=records,
    )

    assert descriptive._outcome_class is _RunOutcomeClass.DESCRIPTIVE
    assert descriptive._entries == ()
    assert descriptive._policy_executions == records
    assert descriptive._failure_reason is None
    assert failed._outcome_class is _RunOutcomeClass.PRE_EVALUATION_FAILURE
    assert failed._entries == ()
    assert failed._policy_executions == records
    assert failed._failure_reason == "dependency unavailable"
