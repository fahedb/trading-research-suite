"""Release-blocking forward-reach rigs and deliberately cheating controls."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pytest

from trading_research.core import (
    BoundaryPolicy,
    ClockDomain,
    DatasetRef,
    NativeTimeKey,
    TimeUnit,
    ViewRole,
    ViewSpan,
)
from trading_research.core.frames import MarketFrame, _make_market_frame
from trading_research.experiments.outcomes import compute_outcomes
from trading_research.outcomes.reference import ForwardLogReturn

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy.typing as npt

    from trading_research.core import OutcomeRequest

DOMAIN = ClockDomain("test.native")
REF = DatasetRef("TRAIN_TEST", "2" * 64, ViewRole.TRAIN)
KEYS = np.array([10, 20, 100, 110, 120, 130, 140], dtype=np.int64)
PRICES = np.array([90.0, 100.0, 121.0, 133.1, 146.41, 161.051, 177.1561])


def _frame(prices: npt.NDArray[np.float64]) -> MarketFrame:
    return _make_market_frame(
        dataset_ref=REF,
        clock_domain=DOMAIN,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=KEYS,
        columns={"close": prices},
        admitted_lo=10,
        admitted_hi=140,
    )


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _view(*, end: int = 140) -> ViewSpan:
    return ViewSpan(
        (REF.canonical_identity,),
        TimeUnit.SECONDS,
        DOMAIN,
        _key(10),
        _key(10),
        _key(end),
    )


class OffByOneOutcomeControl(ForwardLogReturn):
    """Uses the decision row as origin instead of X+1."""

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        prices = request.column("close")
        result = np.full(request.decision_length, np.nan)
        for local in range(request.decision_length):
            decision = request.decision_offset + local
            terminal = decision + self.horizon_rows
            if terminal < prices.size:
                result[local] = np.log(prices[terminal] / prices[decision])
        return {"target.forward_log_return": result}


class AlignmentInversionOutcomeControl(ForwardLogReturn):
    """Shifts valid values to the next decision row."""

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        correct = super().compute(request)["target.forward_log_return"]
        return {"target.forward_log_return": np.roll(correct, 1)}


class OverreachOutcomeControl(ForwardLogReturn):
    """Adds an undeclared row after the terminal position."""

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        correct = super().compute(request)["target.forward_log_return"]
        prices = request.column("close")
        for local in range(request.decision_length):
            after_terminal = (
                request.decision_offset
                + local
                + self.decision_delay_rows
                + self.horizon_rows
                + 1
            )
            if after_terminal < prices.size:
                correct[local] += prices[after_terminal] * 1e-9
        return {"target.forward_log_return": correct}


class TruncatingOutcomeControl(ForwardLogReturn):
    """Silently shortens incomplete horizons to the last observed row."""

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        prices = request.column("close")
        result = np.full(request.decision_length, np.nan)
        for local in range(request.decision_length):
            decision = request.decision_offset + local
            origin = decision + self.decision_delay_rows
            if origin >= prices.size:
                continue
            terminal = min(origin + self.horizon_rows, prices.size - 1)
            result[local] = np.log(prices[terminal] / prices[origin])
        return {"target.forward_log_return": result}


class ClosureInferringOutcomeControl(ForwardLogReturn):
    """Treats any arithmetic key discontinuity as a market closure."""

    def compute(self, request: OutcomeRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        correct = super().compute(request)["target.forward_log_return"]
        keys = request.keys
        for local in range(request.decision_length):
            decision = request.decision_offset + local
            terminal = decision + self.decision_delay_rows + self.horizon_rows
            if terminal < keys.size and np.any(np.diff(keys[decision : terminal + 1]) != 10):
                correct[local] = np.nan
        return {"target.forward_log_return": correct}


def _spec(spec_type: type[ForwardLogReturn] = ForwardLogReturn) -> ForwardLogReturn:
    return spec_type(
        horizon_rows=1,
        decision_delay_rows=1,
        reference_price_column="close",
        boundary_policy=BoundaryPolicy.NAN,
    )


def _bits(values: npt.NDArray[np.float64]) -> tuple[bytes, bytes]:
    return np.isnan(values).tobytes(), values.view(np.uint64).tobytes()


def _assert_goldens(spec: ForwardLogReturn) -> None:
    values = compute_outcomes(_frame(PRICES), _view(), spec).column(
        "target.forward_log_return"
    )
    assert values[0] == math.log(121.0 / 100.0)
    assert np.isnan(values[-2:]).all()


def _assert_forward_reach(spec: ForwardLogReturn) -> None:
    baseline = compute_outcomes(_frame(PRICES), _view(end=10), spec).column(
        "target.forward_log_return"
    )
    changed = PRICES.copy()
    changed[3:] *= 7.0
    perturbed = compute_outcomes(_frame(changed), _view(end=10), spec).column(
        "target.forward_log_return"
    )
    assert np.isfinite(baseline[0])
    assert _bits(baseline) == _bits(perturbed)


def _assert_pre_origin_blind(spec: ForwardLogReturn) -> None:
    baseline = compute_outcomes(_frame(PRICES), _view(end=100), spec).column(
        "target.forward_log_return"
    )
    changed = PRICES.copy()
    changed[0] *= 9.0
    perturbed = compute_outcomes(_frame(changed), _view(end=100), spec).column(
        "target.forward_log_return"
    )
    assert np.isfinite(baseline[0])
    assert _bits(baseline) == _bits(perturbed)


def test_reference_outcome_passes_forward_and_pre_origin_rigs_exactly() -> None:
    spec = _spec()
    _assert_forward_reach(spec)
    _assert_pre_origin_blind(spec)


@pytest.mark.parametrize(
    "control",
    [OffByOneOutcomeControl, AlignmentInversionOutcomeControl],
)
def test_goldens_catch_arithmetic_and_alignment_controls(
    control: type[ForwardLogReturn],
) -> None:
    with pytest.raises(AssertionError):
        _assert_goldens(_spec(control))


def test_forward_reach_rig_catches_overread_control() -> None:
    with pytest.raises(AssertionError):
        _assert_forward_reach(_spec(OverreachOutcomeControl))


def test_boundary_golden_catches_truncating_control() -> None:
    with pytest.raises(AssertionError):
        _assert_goldens(_spec(TruncatingOutcomeControl))


def test_discontinuity_golden_catches_closure_inference_control() -> None:
    with pytest.raises(AssertionError):
        _assert_goldens(_spec(ClosureInferringOutcomeControl))


def test_no_negative_control_is_registered_in_production() -> None:
    from trading_research.outcomes import OUTCOME_SPECS

    assert OUTCOME_SPECS.names() == {"forward_log_return_v1"}
