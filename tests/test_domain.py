"""Validation of the small Phase-3 descriptor and declaration contracts."""

from __future__ import annotations

import math
from typing import cast

import pytest

from trading_research.core import (
    BoundaryPolicy,
    ClockDomain,
    ContractViolationError,
    DatasetIdentity,
    GapPolicy,
    MetricValue,
    NativeTimeKey,
    ParamValue,
    StageOrigin,
    TimeUnit,
    ToleranceSpec,
    ViewSpan,
)

DOMAIN = ClockDomain("test.native")
IDENTITY = DatasetIdentity("TRAIN_TEST", "1" * 64)


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def test_view_span_is_a_validated_descriptor_without_data_authority() -> None:
    span = ViewSpan((IDENTITY,), TimeUnit.SECONDS, DOMAIN, _key(1), _key(2), _key(4))

    assert span.source_identities == (IDENTITY,)
    assert not any(hasattr(span, name) for name in ("column", "load", "grant", "rows"))


@pytest.mark.parametrize(
    "values",
    [(_key(3), _key(2), _key(4)), (_key(1), _key(5), _key(4))],
)
def test_view_span_refuses_inverted_boundaries(
    values: tuple[NativeTimeKey, NativeTimeKey, NativeTimeKey],
) -> None:
    with pytest.raises(ContractViolationError):
        ViewSpan((IDENTITY,), TimeUnit.SECONDS, DOMAIN, *values)


def test_stage_origin_detaches_and_sorts_params() -> None:
    params: dict[str, ParamValue] = {"z": 2, "a": (1, 2)}
    origin = StageOrigin("stage", 1, params)
    params["z"] = 9

    assert tuple(origin.params) == ("a", "z")
    assert origin.params["z"] == 2
    with pytest.raises(TypeError):
        cast("dict[str, object]", origin.params)["x"] = 3


@pytest.mark.parametrize(("atol", "rtol"), [(-0.0 - 1.0, 0.0), (0.0, math.inf)])
def test_tolerance_refuses_negative_or_nonfinite_values(atol: float, rtol: float) -> None:
    with pytest.raises(ContractViolationError):
        ToleranceSpec(atol=atol, rtol=rtol, reason="needed")


def test_metric_value_allows_nan_and_requires_a_sample_count() -> None:
    value = MetricValue("rank_ic", float("nan"), 0)
    assert math.isnan(value.value)
    with pytest.raises(ContractViolationError, match="effective_samples"):
        MetricValue("rank_ic", 0.2, -1)


def test_outcome_and_feature_boundary_policies_are_distinct_types() -> None:
    assert id(BoundaryPolicy) != id(GapPolicy)
    assert BoundaryPolicy.NAN.value == "nan"
    assert {member.value for member in GapPolicy} == {"cross", "break"}
