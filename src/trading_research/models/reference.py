"""Small deterministic reference models proving the V1 fit/infer lifecycle."""

from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.research import ModelInputs as _ModelInputs
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame
from trading_research.core.research_frames import _make_model_output_frame
from trading_research.models.registry import RESEARCH_MODELS as _RESEARCH_MODELS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

__all__ = [
    "GapSensitiveGateV1",
    "MultiConfluenceGateV1",
    "ReferenceLibraryModel",
    "StatelessRuleModel",
    "SupervisedMeanModel",
    "UlsPushGateV1",
    "ZoneEventGateV1",
]

_FEATURE = "log_return_1"
_GSL_FEATURES = (
    "gsl_event_signed",
    "gsl_long_score",
    "gsl_short_score",
    "gsl_conflict",
)
_MC_FEATURES = ("mc_event_signed", "mc_long_score", "mc_short_score")
_ZONE_EVENT_FEATURE = "zt_event_signed"
_ULS_EVENT_FEATURE = "up_event_signed"
_OUTPUT = "score"
_ULS_VALUE_FEATURES = (
    "up_source",
    "up_score",
    "up_entry_ref",
    "up_sweep_extreme",
    "up_poi_top",
    "up_poi_bot",
    "up_struct_level",
    "up_opp_liquidity",
    "up_atr_exec",
    "up_atr_sig",
    "up_liq_price",
    "up_liq_touches",
    "up_sweep_dist_atr",
    "up_wick_ratio",
    "up_disp_atr",
    "up_vol_rel",
    "up_vol_rank",
    "up_bos",
    "up_tl",
    "up_cisd",
    "up_key_ok",
)
_ULS_OUTPUT_COLUMNS = (
    _OUTPUT,
    *(f"value_{index}" for index in range(len(_ULS_VALUE_FEATURES))),
)
_REPRESENTATION = "standardized_log_return_1"


class StatelessRuleModel:
    """A no-op-fit row-local transform proving stateless models pay no ML tax."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "stateless_rule_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
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
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that this model consumes no representation."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic model output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap fitting."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Return an empty learned payload and refuse an unused target channel."""
        if training_targets is not None:
            raise _ContractViolationError("stateless_rule_v1 does not accept training targets")
        return _FittedPayload(arrays={}, metadata={"state_schema": "stateless_rule_v1"})

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Apply one fixed row-local nonlinear rule."""
        _require_schema(fitted_state, "stateless_rule_v1")
        values = _np.tanh(inputs.features.column(_FEATURE)).astype(_np.float64, copy=False)
        return _output_frame(self, view=view, keys=inputs.features.keys, values=values)


class SupervisedMeanModel:
    """A trivial supervised baseline proving the platform target channel."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "supervised_mean_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
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
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that this model consumes no representation."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic model output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap fitting."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Learn the finite-pair mean through the platform target channel."""
        targets = _single_target(training_targets)
        features = inputs.features.column(_FEATURE)
        available = _np.isfinite(features) & _np.isfinite(targets)
        if not available.any():
            raise _ContractViolationError("supervised_mean_v1 has no finite training pairs")
        mean = _np.array([targets[available].mean(dtype=_np.float64)], dtype=_np.float64)
        return _FittedPayload(
            arrays={"target_mean": mean},
            metadata={"state_schema": "supervised_mean_v1"},
        )

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Emit the fixed learned mean wherever the declared feature is available."""
        _require_schema(fitted_state, "supervised_mean_v1")
        mean = _scalar_array(fitted_state, "target_mean")
        feature = inputs.features.column(_FEATURE)
        values = _np.where(_np.isfinite(feature), mean, _np.nan).astype(_np.float64)
        return _output_frame(self, view=view, keys=inputs.features.keys, values=values)


class ReferenceLibraryModel:
    """Nearest-reference lookup over a fixed fit-time library."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "reference_library_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing parameter mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the declared feature input used for availability."""
        return (_FEATURE,)

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Return the sole learned-representation input."""
        return (_REPRESENTATION,)

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic model output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare the reference-library fit expensive enough to cache."""
        return _CostHint.EXPENSIVE

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Store the finite fit-time representation and target pairs."""
        if inputs.representation is None:
            raise _ContractViolationError("reference_library_v1 requires a representation")
        representation = inputs.representation.column(_REPRESENTATION)
        feature = inputs.features.column(_FEATURE)
        targets = _single_target(training_targets)
        available = _np.isfinite(feature) & _np.isfinite(representation) & _np.isfinite(targets)
        if not available.any():
            raise _ContractViolationError("reference_library_v1 has no finite training pairs")
        return _FittedPayload(
            arrays={
                "library_inputs": representation[available],
                "library_targets": targets[available],
            },
            metadata={"state_schema": "reference_library_v1"},
        )

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Query the fixed library independently for every evaluation row."""
        _require_schema(fitted_state, "reference_library_v1")
        if inputs.representation is None:
            raise _ContractViolationError("reference_library_v1 requires a representation")
        library_inputs = fitted_state.payload.array("library_inputs")
        library_targets = fitted_state.payload.array("library_targets")
        if (
            library_inputs.ndim != 1
            or library_targets.ndim != 1
            or library_inputs.shape != library_targets.shape
            or library_inputs.size == 0
        ):
            raise _ContractViolationError("reference_library_v1 fitted library is malformed")
        query = inputs.representation.column(_REPRESENTATION)
        values = _np.full(query.shape, _np.nan, dtype=_np.float64)
        for index, value in enumerate(query):
            if _np.isfinite(value):
                nearest = int(_np.argmin(_np.abs(library_inputs - value)))
                values[index] = float(library_targets[nearest])
        return _output_frame(self, view=view, keys=inputs.features.keys, values=values)


class ZoneEventGateV1:
    """Carry signed Zone Trader events through one exact row-local score gate."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "zone_event_gate_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing parameter mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the sole signed-event feature input."""
        return (_ZONE_EVENT_FEATURE,)

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that this model consumes no representation."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic model output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap fitting."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Return an empty learned payload and refuse an unused target channel."""
        if training_targets is not None:
            raise _ContractViolationError("zone_event_gate_v1 does not accept training targets")
        return _FittedPayload(arrays={}, metadata={"state_schema": "zone_event_gate_v1"})

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Map signed events to scores and turn zero or unavailable rows into NaN."""
        _require_schema(fitted_state, "zone_event_gate_v1")
        event = inputs.features.column(_ZONE_EVENT_FEATURE)
        values = _np.full(event.shape, _np.nan, dtype=_np.float64)
        values[event == -1.0] = -1.0
        values[event == 1.0] = 1.0
        return _output_frame(self, view=view, keys=inputs.features.keys, values=values)


class UlsPushGateV1:
    """Carry sparse ULS+PUSH events and neutral numeric geometry unchanged."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "uls_push_gate_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the signed event and every point-in-time numeric value."""
        return (_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES)

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return only generic model-output vocabulary."""
        return _ULS_OUTPUT_COLUMNS

    @property
    def cost_hint(self) -> _CostHint:
        """Declare the pass-through fit cheap."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require bitwise-exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Return empty state and refuse an unused target channel."""
        if training_targets is not None:
            raise _ContractViolationError("uls_push_gate_v1 does not accept training targets")
        return _FittedPayload(arrays={}, metadata={"state_schema": "uls_push_gate_v1"})

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Emit a strict sparse subset containing only signed setup rows."""
        _require_schema(fitted_state, "uls_push_gate_v1")
        event = inputs.features.column(_ULS_EVENT_FEATURE)
        malformed = _np.isfinite(event) & ~_np.isin(event, (-1.0, 0.0, 1.0))
        if malformed.any():
            raise _ContractViolationError("uls_push_gate_v1 received a malformed signed event")
        selected = _np.isin(event, (-1.0, 1.0))
        keys = inputs.features.keys[selected]
        columns = {_OUTPUT: event[selected].astype(_np.float64, copy=True)}
        for index, feature_name in enumerate(_ULS_VALUE_FEATURES):
            columns[f"value_{index}"] = inputs.features.column(feature_name)[selected].astype(
                _np.float64,
                copy=True,
            )
        origin = _StageOrigin(self.model_id, self.version, self.params)
        origins = dict.fromkeys(columns, origin)
        return _make_model_output_frame(
            view=view,
            keys=keys,
            columns=columns,
            origins=origins,
        )


class GapSensitiveGateV1:
    """Carry sparse Gap Sensitive events through one row-local generic score."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "gap_sensitive_gate_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the signed event and its point-in-time diagnostics."""
        return _GSL_FEATURES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic output column."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare the pass-through fit cheap."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require bitwise-exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Return empty state and refuse an unused target channel."""
        if training_targets is not None:
            raise _ContractViolationError(
                "gap_sensitive_gate_v1 does not accept training targets"
            )
        return _FittedPayload(
            arrays={},
            metadata={"state_schema": "gap_sensitive_gate_v1"},
        )

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Emit only rows carrying one well-formed non-conflicting event."""
        _require_schema(fitted_state, "gap_sensitive_gate_v1")
        event = inputs.features.column(_GSL_FEATURES[0])
        malformed_event = _np.isfinite(event) & ~_np.isin(event, (-1.0, 0.0, 1.0))
        if malformed_event.any():
            raise _ContractViolationError(
                "gap_sensitive_gate_v1 received a malformed signed event"
            )
        for name in _GSL_FEATURES[1:3]:
            score = inputs.features.column(name)
            malformed_score = _np.isfinite(score) & (
                (score < 0.0) | (score > 3.0) | (score != _np.floor(score))
            )
            if malformed_score.any():
                raise _ContractViolationError(
                    "gap_sensitive_gate_v1 received a malformed confirmation score"
                )
        conflict = inputs.features.column(_GSL_FEATURES[3])
        malformed_conflict = _np.isfinite(conflict) & ~_np.isin(conflict, (0.0, 1.0))
        if malformed_conflict.any() or _np.any((conflict == 1.0) & (event != 0.0)):
            raise _ContractViolationError(
                "gap_sensitive_gate_v1 received a malformed conflict diagnostic"
            )
        selected = _np.isin(event, (-1.0, 1.0))
        if any(
            ~_np.isfinite(inputs.features.column(name)[selected]).all()
            for name in _GSL_FEATURES
        ):
            raise _ContractViolationError(
                "gap_sensitive_gate_v1 event rows require finite diagnostics"
            )
        keys = inputs.features.keys[selected]
        values = event[selected].astype(_np.float64, copy=True)
        origin = _StageOrigin(self.model_id, self.version, self.params)
        return _make_model_output_frame(
            view=view,
            keys=keys,
            columns={_OUTPUT: values},
            origins={_OUTPUT: origin},
        )


class MultiConfluenceGateV1:
    """Carry sparse multi-confluence events through one row-local generic score."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "multi_confluence_gate_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the signed event and its point-in-time score diagnostics."""
        return _MC_FEATURES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic output column."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare the pass-through fit cheap."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require bitwise-exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Return empty state and refuse an unused target channel."""
        if training_targets is not None:
            raise _ContractViolationError(
                "multi_confluence_gate_v1 does not accept training targets"
            )
        return _FittedPayload(
            arrays={},
            metadata={"state_schema": "multi_confluence_gate_v1"},
        )

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Emit only rows carrying one well-formed signed confluence event."""
        _require_schema(fitted_state, "multi_confluence_gate_v1")
        event = inputs.features.column(_MC_FEATURES[0])
        malformed_event = _np.isfinite(event) & ~_np.isin(event, (-1.0, 0.0, 1.0))
        if malformed_event.any():
            raise _ContractViolationError(
                "multi_confluence_gate_v1 received a malformed signed event"
            )
        for name in _MC_FEATURES[1:]:
            score = inputs.features.column(name)
            malformed_score = _np.isfinite(score) & (
                (score < 0.0) | (score > 5.0) | (score != _np.floor(score))
            )
            if malformed_score.any():
                raise _ContractViolationError(
                    "multi_confluence_gate_v1 received a malformed component score"
                )
        selected = _np.isin(event, (-1.0, 1.0))
        if any(
            ~_np.isfinite(inputs.features.column(name)[selected]).all() for name in _MC_FEATURES
        ):
            raise _ContractViolationError(
                "multi_confluence_gate_v1 event rows require finite diagnostics"
            )
        keys = inputs.features.keys[selected]
        values = event[selected].astype(_np.float64, copy=True)
        origin = _StageOrigin(self.model_id, self.version, self.params)
        return _make_model_output_frame(
            view=view,
            keys=keys,
            columns={_OUTPUT: values},
            origins={_OUTPUT: origin},
        )


def _single_target(
    frame: _TrainingTargetFrame | None,
) -> _npt.NDArray[_np.float64]:
    if frame is None or frame.column_names != ("target.forward_log_return",):
        raise _ContractViolationError(
            "reference supervised models require target.forward_log_return"
        )
    return frame.column("target.forward_log_return")


def _require_schema(state: _FittedState, expected: str) -> None:
    if type(state) is not _FittedState or state.payload.metadata != {"state_schema": expected}:
        raise _ContractViolationError(f"fitted payload is not {expected!r} state")


def _scalar_array(state: _FittedState, name: str) -> float:
    values = state.payload.array(name)
    if values.shape != (1,) or values.dtype != _np.dtype(_np.float64):
        raise _ContractViolationError(f"fitted array {name!r} must be one float64 scalar")
    return float(values[0])


def _output_frame(
    model: (
        StatelessRuleModel
        | SupervisedMeanModel
        | ReferenceLibraryModel
        | UlsPushGateV1
        | ZoneEventGateV1
    ),
    *,
    view: _ViewSpan,
    keys: _npt.NDArray[_np.int64],
    values: _npt.NDArray[_np.float64],
) -> _ModelOutputFrame:
    origin = _StageOrigin(model.model_id, model.version, model.params)
    return _make_model_output_frame(
        view=view,
        keys=keys,
        columns={_OUTPUT: values},
        origins={_OUTPUT: origin},
    )


_RESEARCH_MODELS.add("stateless_rule_v1", StatelessRuleModel)
_RESEARCH_MODELS.add("supervised_mean_v1", SupervisedMeanModel)
_RESEARCH_MODELS.add("reference_library_v1", ReferenceLibraryModel)
_RESEARCH_MODELS.add("gap_sensitive_gate_v1", GapSensitiveGateV1)
_RESEARCH_MODELS.add("multi_confluence_gate_v1", MultiConfluenceGateV1)
_RESEARCH_MODELS.add("uls_push_gate_v1", UlsPushGateV1)
_RESEARCH_MODELS.add("zone_event_gate_v1", ZoneEventGateV1)
