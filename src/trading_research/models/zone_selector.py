"""Deterministic corpus-fitted ridge selector for sparse Zone events."""

from __future__ import annotations

import math as _math
import typing as _typing

import numpy as _np
from sklearn.ensemble import (  # type: ignore[import-untyped]
    RandomForestRegressor as _RandomForestRegressor,
)

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.research import CorpusModelInputs as _CorpusModelInputs
from trading_research.core.research import ModelInputs as _ModelInputs
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import _make_model_output_frame
from trading_research.models.registry import CORPUS_RESEARCH_MODELS as _CORPUS_MODELS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

__all__ = ["ZoneForestSelectorV2", "ZoneRidgeSelectorV1"]

_FEATURE_NAMES = (
    "zt_event_signed",
    "zt_entry_price",
    "zt_stop_price",
    "zt_target_price",
    "zt_zone_top",
    "zt_zone_bottom",
    "zt_zone_kind",
    "zt_candidate_open",
)
_MARKET_INPUT_NAMES = (
    "event_signed",
    "risk_distance_over_entry",
    "reward_distance_over_entry",
    "zone_width_over_entry",
    "signed_entry_midpoint_over_entry",
    "zone_kind_0",
    "zone_kind_1",
    "zone_kind_2",
)
_NUMERIC_PARAM_NAMES = (
    "anchor_grid_seconds",
    "anchor_phase_offset_seconds",
    "bar_grid_seconds",
    "bar_phase_offset_seconds",
    "break_buffer_atr",
    "max_bars_wait",
    "max_base_atr",
    "max_base_body",
    "max_zone_age_rows",
    "max_zones",
    "min_fvg_atr",
    "min_move_atr",
    "minimum_wick",
    "reward_risk_target",
    "stop_buffer_atr",
    "use_demand_supply",
    "use_imbalance",
    "use_volume_zones",
    "vol_len",
    "vol_mult",
    "w_atr",
)
_CONFIRMATION_MODES = ("immediate_touch", "rejection_wick", "close_back_outside")
_PARAM_INPUT_NAMES = tuple(f"config.{name}" for name in _NUMERIC_PARAM_NAMES) + tuple(
    f"config.confirmation_mode.{name}" for name in _CONFIRMATION_MODES
)
_INPUT_NAMES = _MARKET_INPUT_NAMES + _PARAM_INPUT_NAMES
_TARGET_NAMES = ("value_0", "value_1", "value_2")
_OUTPUT_NAMES = ("score", *_TARGET_NAMES)
_STATE_SCHEMA = "zone_ridge_selector_v1"
_CONTEXT_FEATURE_NAMES = (
    "ztc_atr_over_entry",
    "ztc_zone_width_over_atr",
    "ztc_zone_age_bars",
    "ztc_zone_touch_count",
    "ztc_pending_bars",
    "ztc_entry_zone_position",
    "ztc_confirmation_wick_fraction",
    "ztc_volume_zscore_20",
    "ztc_atr_ratio_12_48",
    "ztc_directional_return_3",
    "ztc_directional_return_12",
    "ztc_directional_return_48",
    "ztc_room_to_swing_48_atr",
    "ztc_nearest_zone_room_atr",
    "ztc_nearest_zone_present",
    "ztc_minute_sin",
    "ztc_minute_cos",
)
_V2_FEATURE_NAMES = (*_FEATURE_NAMES, *_CONTEXT_FEATURE_NAMES)
_V2_MARKET_INPUT_NAMES = (*_MARKET_INPUT_NAMES, *_CONTEXT_FEATURE_NAMES)
_V2_INPUT_NAMES = (*_V2_MARKET_INPUT_NAMES, *_PARAM_INPUT_NAMES)
_V2_STATE_SCHEMA = "zone_forest_selector_v2"


class ZoneRidgeSelectorV1:
    """Predict three generic realized-value alternatives for each Zone event."""

    __slots__ = ("_ridge_lambda",)

    def __init__(self, *, ridge_lambda: float) -> None:
        if (
            type(ridge_lambda) is not float
            or not _math.isfinite(ridge_lambda)
            or ridge_lambda <= 0.0
        ):
            raise _ContractViolationError("ridge_lambda must be a finite positive Python float")
        self._ridge_lambda = ridge_lambda

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "zone_ridge_selector_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the fixed identity-bearing ridge penalty."""
        return {"ridge_lambda": self._ridge_lambda}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the complete Zone inference feature schema."""
        return _FEATURE_NAMES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation stage is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return one event score and three generic value estimates."""
        return _OUTPUT_NAMES

    @property
    def cost_hint(self) -> _CostHint:
        """Declare corpus fitting as expensive."""
        return _CostHint.EXPENSIVE

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact repeatability under the clamped runtime."""
        return None

    def fit_corpus(self, inputs: _CorpusModelInputs) -> _FittedPayload:
        """Fit one standardized weighted multi-output ridge state."""
        if type(inputs) is not _CorpusModelInputs:
            raise _ContractViolationError("zone ridge fit requires exact CorpusModelInputs")
        if inputs.input_names != _INPUT_NAMES or inputs.target_names != _TARGET_NAMES:
            raise _ContractViolationError("zone ridge corpus schema is not the frozen v1 schema")
        predictors = _np.column_stack([inputs.input(name) for name in _INPUT_NAMES])
        targets = _np.column_stack([inputs.target(name) for name in _TARGET_NAMES])
        weights = inputs.weights
        weight_sum = weights.sum(dtype=_np.float64)
        means = (weights[:, None] * predictors).sum(axis=0, dtype=_np.float64) / weight_sum
        centered = predictors - means
        variances = (weights[:, None] * centered * centered).sum(
            axis=0, dtype=_np.float64
        ) / weight_sum
        scales = _np.sqrt(variances, dtype=_np.float64)
        constant = _np.ptp(predictors, axis=0) == 0.0
        scales[constant] = 1.0
        standardized = centered / scales
        design = _np.column_stack((_np.ones(inputs.num_rows, dtype=_np.float64), standardized))
        weighted_design = design * weights[:, None]
        gram = design.T @ weighted_design
        penalty = _np.zeros(gram.shape[0], dtype=_np.float64)
        penalty[1:] = self._ridge_lambda
        gram[_np.diag_indices_from(gram)] += penalty
        rhs = design.T @ (weights[:, None] * targets)
        coefficients = _np.linalg.solve(gram, rhs).astype(_np.float64, copy=False)
        return _FittedPayload(
            arrays={
                "coefficients": coefficients,
                "input_means": means.astype(_np.float64, copy=False),
                "input_scales": scales.astype(_np.float64, copy=False),
            },
            metadata={
                "input_schema": ",".join(_INPUT_NAMES),
                "state_schema": _STATE_SCHEMA,
                "target_schema": ",".join(_TARGET_NAMES),
            },
        )

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Predict alternatives independently at every valid sparse Zone event."""
        _validate_state(fitted_state)
        frame = inputs.features
        origin = _shared_zone_origin(frame)
        selected, predictors = _predictors_from_feature_frame(frame, origin=origin)
        coefficients = fitted_state.payload.array("coefficients")
        means = fitted_state.payload.array("input_means")
        scales = fitted_state.payload.array("input_scales")
        standardized = (predictors - means) / scales
        design = _np.column_stack((_np.ones(selected.size, dtype=_np.float64), standardized))
        predictions = design @ coefficients
        event = frame.column("zt_event_signed")[selected]
        stage = _StageOrigin(self.model_id, self.version, self.params)
        columns = {
            "score": event.astype(_np.float64, copy=False),
            "value_0": predictions[:, 0].astype(_np.float64, copy=False),
            "value_1": predictions[:, 1].astype(_np.float64, copy=False),
            "value_2": predictions[:, 2].astype(_np.float64, copy=False),
        }
        return _make_model_output_frame(
            view=view,
            keys=frame.keys[selected],
            columns=columns,
            origins=dict.fromkeys(_OUTPUT_NAMES, stage),
        )


class ZoneForestSelectorV2:
    """Predict three generic realized-value alternatives with frozen trees."""

    __slots__ = (
        "_bootstrap",
        "_max_depth",
        "_max_features",
        "_min_samples_leaf",
        "_n_estimators",
        "_random_seed",
    )

    def __init__(
        self,
        *,
        n_estimators: int,
        max_depth: int,
        min_samples_leaf: int,
        max_features: float,
        bootstrap: bool,
        random_seed: int,
    ) -> None:
        for name, value in (
            ("n_estimators", n_estimators),
            ("max_depth", max_depth),
            ("min_samples_leaf", min_samples_leaf),
        ):
            if type(value) is not int or value <= 0:
                raise _ContractViolationError(f"{name} must be a positive Python int")
        if type(max_features) is not float or not 0.0 < max_features <= 1.0:
            raise _ContractViolationError("max_features must be a float in (0, 1]")
        if type(bootstrap) is not bool:
            raise _ContractViolationError("bootstrap must be a Python bool")
        if type(random_seed) is not int or random_seed < 0:
            raise _ContractViolationError("random_seed must be a non-negative Python int")
        self._n_estimators = n_estimators
        self._max_depth = max_depth
        self._min_samples_leaf = min_samples_leaf
        self._max_features = max_features
        self._bootstrap = bootstrap
        self._random_seed = random_seed

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "zone_forest_selector_v2"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return every identity-bearing forest parameter."""
        return {
            "bootstrap": self._bootstrap,
            "max_depth": self._max_depth,
            "max_features": self._max_features,
            "min_samples_leaf": self._min_samples_leaf,
            "n_estimators": self._n_estimators,
            "random_seed": self._random_seed,
        }

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the combined Zone and point-in-time context schema."""
        return _V2_FEATURE_NAMES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation stage is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return one event score and three generic value estimates."""
        return _OUTPUT_NAMES

    @property
    def cost_hint(self) -> _CostHint:
        """Declare corpus fitting as expensive."""
        return _CostHint.EXPENSIVE

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact repeatability in the clamped runtime."""
        return None

    def fit_corpus(self, inputs: _CorpusModelInputs) -> _FittedPayload:
        """Fit and export one deterministic weighted multi-output forest."""
        if type(inputs) is not _CorpusModelInputs:
            raise _ContractViolationError("zone forest fit requires exact CorpusModelInputs")
        if inputs.input_names != _V2_INPUT_NAMES or inputs.target_names != _TARGET_NAMES:
            raise _ContractViolationError("zone forest corpus schema is not the frozen v2 schema")
        predictors = _np.column_stack([inputs.input(name) for name in _V2_INPUT_NAMES])
        targets = _np.column_stack([inputs.target(name) for name in _TARGET_NAMES])
        if not _np.isfinite(predictors).all() or not _np.isfinite(targets).all():
            raise _ContractViolationError("zone forest corpus contains non-finite values")
        forest = _RandomForestRegressor(
            n_estimators=self._n_estimators,
            criterion="squared_error",
            max_depth=self._max_depth,
            min_samples_leaf=self._min_samples_leaf,
            max_features=self._max_features,
            bootstrap=self._bootstrap,
            random_state=self._random_seed,
            n_jobs=1,
        )
        forest.fit(predictors, targets, sample_weight=inputs.weights)
        return _forest_payload(forest, input_count=len(_V2_INPUT_NAMES))

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Predict alternatives independently at each valid Zone event."""
        _validate_v2_state(fitted_state)
        frame = inputs.features
        selected, predictors = _v2_predictors_from_feature_frame(frame)
        predictions = _forest_predict(fitted_state.payload, predictors)
        event = frame.column("zt_event_signed")[selected]
        stage = _StageOrigin(self.model_id, self.version, self.params)
        columns = {
            "score": event.astype(_np.float64, copy=False),
            "value_0": predictions[:, 0].astype(_np.float64, copy=False),
            "value_1": predictions[:, 1].astype(_np.float64, copy=False),
            "value_2": predictions[:, 2].astype(_np.float64, copy=False),
        }
        return _make_model_output_frame(
            view=view,
            keys=frame.keys[selected],
            columns=columns,
            origins=dict.fromkeys(_OUTPUT_NAMES, stage),
        )


def _shared_zone_origin(frame: _FeatureFrame) -> _FeatureOrigin:
    if type(frame) is not _FeatureFrame or frame.column_names != _FEATURE_NAMES:
        raise _ContractViolationError("zone ridge inference requires the exact Zone schema")
    first = frame.origin(_FEATURE_NAMES[0])
    if type(first) is not _FeatureOrigin or first.provider_id != "zone_trader_v13":
        raise _ContractViolationError("zone ridge inputs must come from zone_trader_v13")
    if any(frame.origin(name) != first for name in _FEATURE_NAMES[1:]):
        raise _ContractViolationError("zone ridge columns must share one exact FeatureOrigin")
    _parameter_values(first.params)
    return first


def _parameter_values(params: _Mapping[str, _ParamValue]) -> _npt.NDArray[_np.float64]:
    expected_names = frozenset((*_NUMERIC_PARAM_NAMES, "confirmation_mode"))
    if frozenset(params) != expected_names:
        raise _ContractViolationError("Zone feature parameters differ from the frozen v1 schema")
    values: list[float] = []
    for name in _NUMERIC_PARAM_NAMES:
        value = params[name]
        if not isinstance(value, bool | int | float) or not _math.isfinite(float(value)):
            raise _ContractViolationError(f"Zone parameter {name!r} is not finite numeric")
        values.append(float(value))
    mode = params["confirmation_mode"]
    if type(mode) is not str or mode not in _CONFIRMATION_MODES:
        raise _ContractViolationError("Zone confirmation_mode is unsupported")
    values.extend(1.0 if mode == candidate else 0.0 for candidate in _CONFIRMATION_MODES)
    return _np.asarray(values, dtype=_np.float64)


def _predictors_from_feature_frame(
    frame: _FeatureFrame,
    *,
    origin: _FeatureOrigin,
) -> tuple[_npt.NDArray[_np.int64], _npt.NDArray[_np.float64]]:
    event = frame.column("zt_event_signed")
    entry = frame.column("zt_entry_price")
    stop = frame.column("zt_stop_price")
    target = frame.column("zt_target_price")
    top = frame.column("zt_zone_top")
    bottom = frame.column("zt_zone_bottom")
    kind = frame.column("zt_zone_kind")
    valid = (
        _np.isin(event, (-1.0, 1.0))
        & _np.isfinite(entry)
        & (entry != 0.0)
        & _np.isfinite(stop)
        & _np.isfinite(target)
        & _np.isfinite(top)
        & _np.isfinite(bottom)
        & _np.isin(kind, (0.0, 1.0, 2.0))
    )
    selected = _np.flatnonzero(valid).astype(_np.int64, copy=False)
    if selected.size == 0:
        return selected, _np.empty((0, len(_INPUT_NAMES)), dtype=_np.float64)
    chosen_entry = entry[selected]
    chosen_event = event[selected]
    chosen_kind = kind[selected]
    market = _np.column_stack(
        (
            chosen_event,
            _np.abs(chosen_entry - stop[selected]) / chosen_entry,
            _np.abs(target[selected] - chosen_entry) / chosen_entry,
            (top[selected] - bottom[selected]) / chosen_entry,
            chosen_event
            * (chosen_entry - ((top[selected] + bottom[selected]) / 2.0))
            / chosen_entry,
            (chosen_kind == 0.0).astype(_np.float64),
            (chosen_kind == 1.0).astype(_np.float64),
            (chosen_kind == 2.0).astype(_np.float64),
        )
    )
    constants = _np.broadcast_to(
        _parameter_values(origin.params),
        (selected.size, len(_PARAM_INPUT_NAMES)),
    )
    predictors = _np.column_stack((market, constants)).astype(_np.float64, copy=False)
    if not _np.isfinite(predictors).all():
        raise _ContractViolationError("valid Zone event produced non-finite predictors")
    return selected, predictors


def _v2_predictors_from_feature_frame(
    frame: _FeatureFrame,
) -> tuple[_npt.NDArray[_np.int64], _npt.NDArray[_np.float64]]:
    """Return finite V2 predictors after validating both feature origins."""
    if type(frame) is not _FeatureFrame or frame.column_names != _V2_FEATURE_NAMES:
        raise _ContractViolationError("zone forest inference requires the exact V2 schema")
    zone_origin = frame.origin(_FEATURE_NAMES[0])
    context_origin = frame.origin(_CONTEXT_FEATURE_NAMES[0])
    if (
        type(zone_origin) is not _FeatureOrigin
        or zone_origin.provider_id != "zone_trader_v13"
        or zone_origin.version != 1
        or type(context_origin) is not _FeatureOrigin
        or context_origin.provider_id != "zone_context_v1"
        or context_origin.version != 1
    ):
        raise _ContractViolationError("zone forest inputs have unsupported feature origins")
    if any(frame.origin(name) != zone_origin for name in _FEATURE_NAMES):
        raise _ContractViolationError("zone forest Zone columns do not share one origin")
    if any(frame.origin(name) != context_origin for name in _CONTEXT_FEATURE_NAMES):
        raise _ContractViolationError("zone forest context columns do not share one origin")
    if zone_origin.params != context_origin.params:
        raise _ContractViolationError("zone forest Zone and context parameters differ")

    event = frame.column("zt_event_signed")
    entry = frame.column("zt_entry_price")
    stop = frame.column("zt_stop_price")
    target = frame.column("zt_target_price")
    top = frame.column("zt_zone_top")
    bottom = frame.column("zt_zone_bottom")
    kind = frame.column("zt_zone_kind")
    context = _np.column_stack([frame.column(name) for name in _CONTEXT_FEATURE_NAMES])
    valid = (
        _np.isin(event, (-1.0, 1.0))
        & _np.isfinite(entry)
        & (entry != 0.0)
        & _np.isfinite(stop)
        & _np.isfinite(target)
        & _np.isfinite(top)
        & _np.isfinite(bottom)
        & _np.isin(kind, (0.0, 1.0, 2.0))
        & _np.isfinite(context).all(axis=1)
    )
    selected = _np.flatnonzero(valid).astype(_np.int64, copy=False)
    if selected.size == 0:
        return selected, _np.empty((0, len(_V2_INPUT_NAMES)), dtype=_np.float64)
    chosen_entry = entry[selected]
    chosen_event = event[selected]
    chosen_kind = kind[selected]
    market = _np.column_stack(
        (
            chosen_event,
            _np.abs(chosen_entry - stop[selected]) / chosen_entry,
            _np.abs(target[selected] - chosen_entry) / chosen_entry,
            (top[selected] - bottom[selected]) / chosen_entry,
            chosen_event
            * (chosen_entry - ((top[selected] + bottom[selected]) / 2.0))
            / chosen_entry,
            (chosen_kind == 0.0).astype(_np.float64),
            (chosen_kind == 1.0).astype(_np.float64),
            (chosen_kind == 2.0).astype(_np.float64),
            *tuple(context[selected, index] for index in range(context.shape[1])),
        )
    )
    constants = _np.broadcast_to(
        _parameter_values(zone_origin.params),
        (selected.size, len(_PARAM_INPUT_NAMES)),
    )
    predictors = _np.column_stack((market, constants)).astype(_np.float64, copy=False)
    if predictors.shape != (selected.size, len(_V2_INPUT_NAMES)):
        raise AssertionError("Zone V2 predictor schema length drifted")
    if not _np.isfinite(predictors).all():
        raise _ContractViolationError("valid Zone V2 event produced non-finite predictors")
    return selected, predictors


def _forest_payload(
    forest: _RandomForestRegressor,
    *,
    input_count: int,
) -> _FittedPayload:
    """Export public sklearn tree arrays into one pickle-free payload."""
    offsets = [0]
    children_left: list[_npt.NDArray[_np.int64]] = []
    children_right: list[_npt.NDArray[_np.int64]] = []
    split_features: list[_npt.NDArray[_np.int64]] = []
    thresholds: list[_npt.NDArray[_np.float64]] = []
    values: list[_npt.NDArray[_np.float64]] = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        count = int(tree.node_count)
        offsets.append(offsets[-1] + count)
        children_left.append(_np.asarray(tree.children_left, dtype=_np.int64))
        children_right.append(_np.asarray(tree.children_right, dtype=_np.int64))
        split_features.append(_np.asarray(tree.feature, dtype=_np.int64))
        thresholds.append(_np.asarray(tree.threshold, dtype=_np.float64))
        raw_values = _np.asarray(tree.value, dtype=_np.float64)
        if raw_values.shape != (count, len(_TARGET_NAMES), 1):
            raise _ContractViolationError("zone forest exported an unexpected value shape")
        values.append(raw_values[:, :, 0])
    payload = _FittedPayload(
        arrays={
            "children_left": _np.concatenate(children_left),
            "children_right": _np.concatenate(children_right),
            "split_features": _np.concatenate(split_features),
            "thresholds": _np.concatenate(thresholds),
            "tree_offsets": _np.asarray(offsets, dtype=_np.int64),
            "values": _np.concatenate(values, axis=0),
        },
        metadata={
            "input_count": input_count,
            "input_schema": ",".join(_V2_INPUT_NAMES),
            "state_schema": _V2_STATE_SCHEMA,
            "target_schema": ",".join(_TARGET_NAMES),
            "tree_count": len(forest.estimators_),
        },
    )
    _validate_forest_payload(payload)
    return payload


def _forest_predict(
    payload: _FittedPayload,
    predictors: _npt.NDArray[_np.float64],
) -> _npt.NDArray[_np.float64]:
    """Infer exact leaf means from a validated concatenated forest payload."""
    _validate_forest_payload(payload)
    if predictors.ndim != 2 or predictors.shape[1] != len(_V2_INPUT_NAMES):
        raise _ContractViolationError("zone forest inference matrix shape differs")
    if not _np.isfinite(predictors).all():
        raise _ContractViolationError("zone forest inference matrix is non-finite")
    prediction = _np.zeros((predictors.shape[0], len(_TARGET_NAMES)), dtype=_np.float64)
    offsets = payload.array("tree_offsets")
    left = payload.array("children_left")
    right = payload.array("children_right")
    features = payload.array("split_features")
    thresholds = payload.array("thresholds")
    values = payload.array("values")
    for tree_index in range(offsets.size - 1):
        start = int(offsets[tree_index])
        nodes = _np.zeros(predictors.shape[0], dtype=_np.int64)
        while nodes.size:
            absolute = start + nodes
            active = left[absolute] != -1
            if not _np.any(active):
                break
            rows = _np.flatnonzero(active)
            active_absolute = absolute[rows]
            columns = features[active_absolute]
            go_left = predictors[rows, columns] <= thresholds[active_absolute]
            nodes[rows] = _np.where(
                go_left,
                left[active_absolute],
                right[active_absolute],
            )
        prediction += values[start + nodes]
    return prediction / float(offsets.size - 1)


def _validate_forest_payload(payload: _FittedPayload) -> None:
    """Refuse malformed tree topology before any inference."""
    expected_metadata = {
        "input_count": len(_V2_INPUT_NAMES),
        "input_schema": ",".join(_V2_INPUT_NAMES),
        "state_schema": _V2_STATE_SCHEMA,
        "target_schema": ",".join(_TARGET_NAMES),
    }
    if type(payload) is not _FittedPayload or any(
        payload.metadata.get(name) != value for name, value in expected_metadata.items()
    ):
        raise _ContractViolationError("fitted payload is not zone_forest_selector_v2 state")
    if payload.array_names != (
        "children_left",
        "children_right",
        "split_features",
        "thresholds",
        "tree_offsets",
        "values",
    ):
        raise _ContractViolationError("zone forest fitted arrays are missing or reordered")
    offsets = payload.array("tree_offsets")
    left = payload.array("children_left")
    right = payload.array("children_right")
    features = payload.array("split_features")
    thresholds = payload.array("thresholds")
    values = payload.array("values")
    tree_count = payload.metadata.get("tree_count")
    total = int(offsets[-1]) if offsets.size else -1
    if (
        type(tree_count) is not int
        or tree_count <= 0
        or offsets.dtype != _np.dtype(_np.int64)
        or offsets.shape != (tree_count + 1,)
        or offsets[0] != 0
        or _np.any(offsets[1:] <= offsets[:-1])
        or left.dtype != _np.dtype(_np.int64)
        or right.dtype != _np.dtype(_np.int64)
        or features.dtype != _np.dtype(_np.int64)
        or thresholds.dtype != _np.dtype(_np.float64)
        or values.dtype != _np.dtype(_np.float64)
        or left.shape != (total,)
        or right.shape != (total,)
        or features.shape != (total,)
        or thresholds.shape != (total,)
        or values.shape != (total, len(_TARGET_NAMES))
        or not _np.isfinite(thresholds).all()
        or not _np.isfinite(values).all()
    ):
        raise _ContractViolationError("zone forest fitted arrays are malformed")
    for tree_index in range(tree_count):
        start = int(offsets[tree_index])
        stop = int(offsets[tree_index + 1])
        tree_left = left[start:stop]
        tree_right = right[start:stop]
        tree_features = features[start:stop]
        leaf = tree_left == -1
        if (
            _np.any((tree_right == -1) != leaf)
            or _np.any(tree_left[~leaf] < 0)
            or _np.any(tree_right[~leaf] < 0)
            or _np.any(tree_left[~leaf] >= stop - start)
            or _np.any(tree_right[~leaf] >= stop - start)
            or _np.any(tree_features[~leaf] < 0)
            or _np.any(tree_features[~leaf] >= len(_V2_INPUT_NAMES))
        ):
            raise _ContractViolationError("zone forest tree topology is malformed")


def _validate_v2_state(state: _FittedState) -> None:
    if type(state) is not _FittedState:
        raise _ContractViolationError("zone forest inference requires exact FittedState")
    _validate_forest_payload(state.payload)


def _validate_state(state: _FittedState) -> None:
    if type(state) is not _FittedState or state.payload.metadata != {
        "input_schema": ",".join(_INPUT_NAMES),
        "state_schema": _STATE_SCHEMA,
        "target_schema": ",".join(_TARGET_NAMES),
    }:
        raise _ContractViolationError("fitted payload is not zone_ridge_selector_v1 state")
    coefficient_shape = (len(_INPUT_NAMES) + 1, len(_TARGET_NAMES))
    if state.payload.array_names != ("coefficients", "input_means", "input_scales"):
        raise _ContractViolationError("zone ridge fitted arrays are missing or reordered")
    coefficients = state.payload.array("coefficients")
    means = state.payload.array("input_means")
    scales = state.payload.array("input_scales")
    if (
        coefficients.dtype != _np.dtype(_np.float64)
        or coefficients.shape != coefficient_shape
        or means.dtype != _np.dtype(_np.float64)
        or means.shape != (len(_INPUT_NAMES),)
        or scales.dtype != _np.dtype(_np.float64)
        or scales.shape != (len(_INPUT_NAMES),)
        or not _np.isfinite(coefficients).all()
        or not _np.isfinite(means).all()
        or not _np.isfinite(scales).all()
        or _np.any(scales <= 0.0)
    ):
        raise _ContractViolationError("zone ridge fitted arrays are malformed")


_CORPUS_MODELS.add("zone_ridge_selector_v1", ZoneRidgeSelectorV1)
_CORPUS_MODELS.add("zone_forest_selector_v2", ZoneForestSelectorV2)
