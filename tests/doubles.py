"""Minimal test-only implementation of the retained FeatureProvider protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from trading_research.core import CostHint, FeatureRequest, GapPolicy, ParamValue, WindowSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy.typing as npt


class ColumnarFeatureProvider:
    """Small deterministic provider used only for protocol conformance tests."""

    @property
    def provider_id(self) -> str:
        return "test_columnar"

    @property
    def version(self) -> int:
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        return ("test_value",)

    @property
    def required_columns(self) -> tuple[str, ...]:
        return ("close",)

    @property
    def window(self) -> WindowSpec:
        return WindowSpec(max_prior_rows=0, gap_policy=GapPolicy.CROSS)

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return {}

    @property
    def cost_hint(self) -> CostHint:
        return CostHint.CHEAP

    def compute(self, request: FeatureRequest) -> Mapping[str, npt.NDArray[np.float64]]:
        return {"test_value": np.zeros(request.eval_length, dtype=np.float64)}
