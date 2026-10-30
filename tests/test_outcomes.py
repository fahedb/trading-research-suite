"""Goldens and refusal paths for the single V1 forward outcome."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest

from trading_research.core import (
    BoundaryPolicy,
    ClockDomain,
    ContractViolationError,
    DatasetRef,
    NativeTimeKey,
    OutcomeFrame,
    TimeUnit,
    TrainingTargetFrame,
    ViewRole,
    ViewSpan,
)
from trading_research.core.frames import MarketFrame, _make_market_frame
from trading_research.experiments.outcomes import compute_outcomes, compute_training_targets
from trading_research.outcomes import OUTCOME_SPECS
from trading_research.outcomes.execution import _forward_rows, _target_end_position
from trading_research.outcomes.reference import ForwardLogReturn

if TYPE_CHECKING:
    import numpy.typing as npt

DOMAIN = ClockDomain("test.native")
REF = DatasetRef("TRAIN_TEST", "1" * 64, ViewRole.TRAIN)


def _frame(
    prices: npt.NDArray[np.float64] | None = None,
) -> MarketFrame:
    keys = np.array([10, 20, 100, 110, 120, 130, 140], dtype=np.int64)
    values = (
        np.array([90.0, 100.0, 121.0, 133.1, 146.41, 161.051, 177.1561])
        if prices is None
        else prices
    )
    return _make_market_frame(
        dataset_ref=REF,
        clock_domain=DOMAIN,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=keys,
        columns={"close": values},
        admitted_lo=10,
        admitted_hi=140,
    )


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _view(*, start: int = 10, end: int = 140) -> ViewSpan:
    return ViewSpan(
        (REF.canonical_identity,),
        TimeUnit.SECONDS,
        DOMAIN,
        _key(start),
        _key(start),
        _key(end),
    )


def _spec(*, horizon_rows: int = 1) -> ForwardLogReturn:
    return ForwardLogReturn(
        horizon_rows=horizon_rows,
        decision_delay_rows=1,
        reference_price_column="close",
        boundary_policy=BoundaryPolicy.NAN,
    )


def test_registry_contains_exactly_the_single_explicit_v1_outcome() -> None:
    assert OUTCOME_SPECS.names() == {"forward_log_return_v1"}
    spec = OUTCOME_SPECS.create(
        "forward_log_return_v1",
        horizon_rows=1,
        decision_delay_rows=1,
        reference_price_column="close",
        boundary_policy=BoundaryPolicy.NAN,
    )
    assert isinstance(spec, ForwardLogReturn)


def test_horizon_one_uses_x_plus_two_over_x_plus_one_and_keeps_decision_keys() -> None:
    targets = compute_training_targets(_frame(), _view(), _spec())
    values = targets.column("target.forward_log_return")

    assert targets.keys.tolist() == [10, 20, 100, 110, 120, 130, 140]
    assert values[0] == math.log(121.0 / 100.0)
    assert values[0] != 0.0


def test_incomplete_horizons_are_nan_and_never_truncated() -> None:
    values = compute_outcomes(_frame(), _view(), _spec(horizon_rows=3)).column(
        "target.forward_log_return"
    )

    assert values[0] == math.log(146.41 / 100.0)
    assert np.isnan(values[-4:]).all()


def test_observed_key_discontinuity_does_not_make_the_horizon_unavailable() -> None:
    value = compute_outcomes(_frame(), _view(start=10, end=10), _spec()).column(
        "target.forward_log_return"
    )[0]
    assert value == math.log(121.0 / 100.0)


def test_training_and_evaluation_target_channels_are_distinct() -> None:
    training = compute_training_targets(_frame(), _view(start=10, end=120), _spec())
    evaluation = compute_outcomes(_frame(), _view(start=10, end=120), _spec())

    assert type(training) is TrainingTargetFrame
    assert type(evaluation) is OutcomeFrame
    assert np.array_equal(
        training.column("target.forward_log_return"),
        evaluation.column("target.forward_log_return"),
        equal_nan=True,
    )


def test_target_end_derivation_is_the_exact_declared_sum() -> None:
    spec = _spec(horizon_rows=20)
    assert _forward_rows(spec) == 21
    assert _target_end_position(7, spec) == 28


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"horizon_rows": 0}, "horizon_rows"),
        ({"decision_delay_rows": 0}, "exactly 1"),
        ({"reference_price_column": ""}, "reference_price_column"),
        ({"reference_price_column": "target.leak"}, "reserved target"),
    ],
)
def test_declaration_refusal_paths(kwargs: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "horizon_rows": 1,
        "decision_delay_rows": 1,
        "reference_price_column": "close",
        "boundary_policy": BoundaryPolicy.NAN,
    }
    values.update(kwargs)
    with pytest.raises(ContractViolationError, match=message):
        cast("Any", ForwardLogReturn)(**values)


def test_nonpositive_endpoint_price_is_refused_instead_of_fabricating_a_return() -> None:
    prices = np.array([90.0, 100.0, 0.0, 133.1, 146.41, 161.051, 177.1561])
    with pytest.raises(ContractViolationError, match="strictly positive"):
        compute_outcomes(_frame(prices), _view(start=10, end=10), _spec())


def test_outputs_are_immutable_and_detached_from_source_mutation() -> None:
    prices = np.array([90.0, 100.0, 121.0, 133.1, 146.41, 161.051, 177.1561])
    output = compute_outcomes(_frame(prices), _view(start=10, end=120), _spec())
    prices[:] = 1.0
    values = output.column("target.forward_log_return")

    assert values[0] == math.log(121.0 / 100.0)
    with pytest.raises(ValueError, match="read-only"):
        values[0] = 0.0
