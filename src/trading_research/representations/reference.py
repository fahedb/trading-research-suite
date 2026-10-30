"""One deterministic fit-time standardizer proving learned preprocessing."""

from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import RepresentationFrame as _RepresentationFrame
from trading_research.core.research_frames import _make_representation_frame
from trading_research.representations.registry import (
    REPRESENTATION_MODELS as _REPRESENTATION_MODELS,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame

__all__ = ["TrailingStandardizer"]

_FEATURE = "log_return_1"
_OUTPUT = "standardized_log_return_1"


class TrailingStandardizer:
    """Standardize each row with location and scale learned on the fit view."""

    @property
    def representation_id(self) -> str:
        """Return the stable representation identity."""
        return "trailing_standardizer_v1"

    @property
    def version(self) -> int:
        """Return the representation contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing parameter mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the sole declared feature input."""
        return (_FEATURE,)

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole dense representation output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap fitting."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(self, view: _ViewSpan, features: _FeatureFrame) -> _FittedPayload:
        """Learn finite location and scale from the supplied fit view only."""
        values = features.column(_FEATURE)
        finite = values[_np.isfinite(values)]
        if finite.size == 0:
            raise _ContractViolationError("trailing_standardizer_v1 has no finite fit values")
        location = float(finite.mean(dtype=_np.float64))
        scale = float(finite.std(dtype=_np.float64))
        if scale == 0.0:
            scale = 1.0
        return _FittedPayload(
            arrays={
                "location": _np.array([location], dtype=_np.float64),
                "scale": _np.array([scale], dtype=_np.float64),
            },
            metadata={"state_schema": "trailing_standardizer_v1"},
        )

    def encode(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        features: _FeatureFrame,
    ) -> _RepresentationFrame:
        """Standardize each row from its value and the fixed fit-time state."""
        if fitted_state.payload.metadata != {"state_schema": "trailing_standardizer_v1"}:
            raise _ContractViolationError("fitted payload is not standardizer state")
        location = _scalar(fitted_state, "location")
        scale = _scalar(fitted_state, "scale")
        if scale <= 0.0:
            raise _ContractViolationError("standardizer scale must be positive")
        output = ((features.column(_FEATURE) - location) / scale).astype(_np.float64)
        origin = _StageOrigin(self.representation_id, self.version, self.params)
        return _make_representation_frame(
            view=view,
            features=features,
            columns={_OUTPUT: output},
            origins={_OUTPUT: origin},
        )


def _scalar(state: _FittedState, name: str) -> float:
    values = state.payload.array(name)
    if values.shape != (1,) or values.dtype != _np.dtype(_np.float64):
        raise _ContractViolationError(f"standardizer {name} must be one float64 scalar")
    return float(values[0])


_REPRESENTATION_MODELS.add("trailing_standardizer_v1", TrailingStandardizer)
