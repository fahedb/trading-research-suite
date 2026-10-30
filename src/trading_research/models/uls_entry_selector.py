"""Deterministic corpus-fitted entry gate for sparse ULS + PUSH events."""

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
from trading_research.models.reference import _ULS_EVENT_FEATURE, _ULS_VALUE_FEATURES
from trading_research.models.registry import CORPUS_RESEARCH_MODELS as _CORPUS_MODELS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

__all__ = ["UlsEntryForestV1"]

_CONTEXT_FEATURE_NAMES = (
    "umc_atr_over_close",
    "umc_volume_zscore_20",
    "umc_volume_ratio_5_20",
    "umc_true_range_ratio_12_48",
    "umc_log_return_1",
    "umc_log_return_3",
    "umc_log_return_12",
    "umc_log_return_48",
    "umc_ema_distance_atr",
    "umc_ema_slope_12_atr",
    "umc_range_over_atr",
    "umc_body_fraction",
    "umc_upper_wick_fraction",
    "umc_lower_wick_fraction",
    "umc_room_up_48_atr",
    "umc_room_down_48_atr",
    "umc_close_location_48",
)
_FEATURE_NAMES = (_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES, *_CONTEXT_FEATURE_NAMES)

_MARKET_INPUT_NAMES = (
    "event_signed",
    "source_uls_1",
    "source_uls_2",
    "source_push",
    "setup_score_scaled",
    "atr_exec_over_entry",
    "atr_signal_over_entry",
    "atr_exec_over_signal",
    "sweep_present",
    "signed_sweep_distance_atr",
    "poi_top_present",
    "signed_poi_top_distance_atr",
    "poi_bottom_present",
    "signed_poi_bottom_distance_atr",
    "structure_present",
    "signed_structure_distance_atr",
    "opposite_liquidity_present",
    "signed_opposite_liquidity_distance_atr",
    "liquidity_present",
    "signed_liquidity_distance_atr",
    "poi_width_over_atr",
    "liquidity_touches",
    "sweep_distance_atr",
    "wick_ratio",
    "displacement_atr",
    "volume_relative",
    "volatility_rank_scaled",
    "bos_flag",
    "trendline_flag",
    "cisd_flag",
    "key_level_flag",
)
_CONTEXT_INPUT_NAMES = (
    "context_atr_over_close",
    "context_volume_zscore_20",
    "context_volume_ratio_5_20",
    "context_true_range_ratio_12_48",
    "context_directional_return_1",
    "context_directional_return_3",
    "context_directional_return_12",
    "context_directional_return_48",
    "context_directional_ema_distance_atr",
    "context_directional_ema_slope_12_atr",
    "context_range_over_atr",
    "context_body_fraction",
    "context_directional_wick_fraction",
    "context_directional_room_48_atr",
    "context_directional_close_location_48",
)
_NUMERIC_PARAM_NAMES = (
    "atr_len",
    "atr_window",
    "bar_grid_seconds",
    "bar_phase_offset_seconds",
    "cancel_opposite",
    "cisd_max_age",
    "confirmation_wait",
    "displacement_atr",
    "ema_len",
    "ema_window",
    "equal_tolerance_atr",
    "fvg_age",
    "hour_baseline",
    "ifvg_search",
    "key_level_atr_multiple",
    "key_level_manual_step",
    "key_level_tolerance_atr",
    "liquidity_age",
    "max_ifvg_atr",
    "max_ifvg_distance_atr",
    "max_levels",
    "max_push_zones",
    "max_retracement_pct",
    "max_sweep_atr",
    "min_equal_touches",
    "min_ifvg_atr",
    "min_retracement_pct",
    "min_score",
    "min_sweep_atr",
    "min_trendline_touches",
    "min_volatility_rank",
    "minimum_tick",
    "momentum_len",
    "momentum_slope",
    "momentum_window",
    "pivot_len",
    "push_atr",
    "push_bars",
    "push_volume_multiple",
    "push_zone_age",
    "rejection_ratio",
    "require_key_level",
    "require_rejection",
    "return_bars",
    "session_filter",
    "total_setup_bars",
    "trendline_tolerance_atr",
    "use_displacement_filter",
    "use_ifvg_size",
    "use_key_levels",
    "use_momentum",
    "use_score_filter",
    "use_volume_filter",
    "volume_multiple",
    "zone_uses_wicks",
)
_CATEGORICAL_PARAM_OPTIONS = (
    ("engine_mode", ("uls", "push", "parallel", "merged")),
    ("liquidity_mode", ("structural", "equal", "both")),
    ("sweep_confirmation", ("wick", "close", "close_confirm")),
    ("inversion_mode", ("wick", "close")),
    ("key_level_mode", ("auto", "manual")),
    (
        "confirmation_mode",
        ("trendline", "bos", "cisd", "trendline_or_bos", "any", "two", "all"),
    ),
    ("trendline_break_mode", ("wick", "close")),
    ("ema_filter", ("off", "current", "higher")),
)
_PARAM_INPUT_NAMES = tuple(f"config.{name}" for name in _NUMERIC_PARAM_NAMES) + tuple(
    f"config.{name}.{option}" for name, options in _CATEGORICAL_PARAM_OPTIONS for option in options
)
_INPUT_NAMES = (*_MARKET_INPUT_NAMES, *_CONTEXT_INPUT_NAMES, *_PARAM_INPUT_NAMES)
_TARGET_NAMES = ("value_target",)
_OUTPUT_NAMES = ("score", "value_0", "value_1")
_STATE_SCHEMA = "uls_entry_forest_v1"


class UlsEntryForestV1:
    """Estimate conditional net value and abstain below one frozen cutoff."""

    __slots__ = (
        "_bootstrap",
        "_max_depth",
        "_max_features",
        "_min_samples_leaf",
        "_n_estimators",
        "_random_seed",
        "_selection_threshold",
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
        selection_threshold: float,
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
        if type(selection_threshold) is not float or not _math.isfinite(selection_threshold):
            raise _ContractViolationError("selection_threshold must be a finite Python float")
        self._n_estimators = n_estimators
        self._max_depth = max_depth
        self._min_samples_leaf = min_samples_leaf
        self._max_features = max_features
        self._bootstrap = bootstrap
        self._random_seed = random_seed
        self._selection_threshold = selection_threshold

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "uls_entry_forest_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return all identity-bearing forest and selection parameters."""
        return {
            "bootstrap": self._bootstrap,
            "max_depth": self._max_depth,
            "max_features": self._max_features,
            "min_samples_leaf": self._min_samples_leaf,
            "n_estimators": self._n_estimators,
            "random_seed": self._random_seed,
            "selection_threshold": self._selection_threshold,
        }

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the ULS event plus fixed market-context schema."""
        return _FEATURE_NAMES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation stage is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return gated, raw-direction and estimated-value outputs."""
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
        """Fit and export one deterministic weighted regression forest."""
        if type(inputs) is not _CorpusModelInputs:
            raise _ContractViolationError("ULS entry fit requires exact CorpusModelInputs")
        if inputs.input_names != _INPUT_NAMES or inputs.target_names != _TARGET_NAMES:
            raise _ContractViolationError("ULS entry corpus schema differs from frozen v1")
        predictors = _np.column_stack([inputs.input(name) for name in _INPUT_NAMES])
        targets = inputs.target(_TARGET_NAMES[0])
        if not _np.isfinite(predictors).all() or not _np.isfinite(targets).all():
            raise _ContractViolationError("ULS entry corpus contains non-finite values")
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
        return _forest_payload(forest)

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Gate each ULS event independently using only point-in-time inputs."""
        _validate_state(fitted_state)
        frame = inputs.features
        selected, predictors = _predictors_from_feature_frame(frame)
        prediction = _forest_predict(fitted_state.payload, predictors)
        event = frame.column(_ULS_EVENT_FEATURE)[selected]
        score = _np.full(event.shape, _np.nan, dtype=_np.float64)
        accepted = prediction > self._selection_threshold
        score[accepted] = event[accepted]
        stage = _StageOrigin(self.model_id, self.version, self.params)
        columns = {
            "score": score,
            "value_0": event.astype(_np.float64, copy=True),
            "value_1": prediction,
        }
        return _make_model_output_frame(
            view=view,
            keys=frame.keys[selected],
            columns=columns,
            origins=dict.fromkeys(_OUTPUT_NAMES, stage),
        )


def _parameter_values(params: _Mapping[str, _ParamValue]) -> _npt.NDArray[_np.float64]:
    expected = frozenset(
        (*_NUMERIC_PARAM_NAMES, *(name for name, _options in _CATEGORICAL_PARAM_OPTIONS))
    )
    if frozenset(params) != expected:
        raise _ContractViolationError("ULS feature parameters differ from frozen entry schema")
    values: list[float] = []
    for name in _NUMERIC_PARAM_NAMES:
        value = params[name]
        if not isinstance(value, bool | int | float) or not _math.isfinite(float(value)):
            raise _ContractViolationError(f"ULS parameter {name!r} is not finite numeric")
        values.append(float(value))
    for name, options in _CATEGORICAL_PARAM_OPTIONS:
        value = params[name]
        if type(value) is not str or value not in options:
            raise _ContractViolationError(f"ULS parameter {name!r} is unsupported")
        values.extend(1.0 if value == option else 0.0 for option in options)
    result = _np.asarray(values, dtype=_np.float64)
    if result.shape != (len(_PARAM_INPUT_NAMES),):
        raise AssertionError("ULS parameter predictor schema drifted")
    return result


def _predictor_rows_from_arrays(
    *,
    columns: _Mapping[str, _npt.NDArray[_np.float64]],
    positions: _npt.NDArray[_np.int64],
    params: _Mapping[str, _ParamValue],
    expected_directions: _npt.NDArray[_np.int8] | None = None,
) -> _npt.NDArray[_np.float64]:
    """Build the exact finite row-local predictor matrix used by fit and infer."""
    required = frozenset(_FEATURE_NAMES)
    if frozenset(columns) != required:
        raise _ContractViolationError("ULS entry predictor columns differ from frozen schema")
    lengths = {values.shape for values in columns.values()}
    if len(lengths) != 1 or any(values.ndim != 1 for values in columns.values()):
        raise _ContractViolationError("ULS entry predictor columns are not aligned vectors")
    (shape,) = lengths
    if (
        positions.ndim != 1
        or positions.dtype != _np.dtype(_np.int64)
        or _np.any(positions < 0)
        or _np.any(positions >= shape[0])
    ):
        raise _ContractViolationError("ULS entry predictor positions are malformed")
    event = columns[_ULS_EVENT_FEATURE][positions]
    if not _np.isin(event, (-1.0, 1.0)).all():
        raise _ContractViolationError("ULS entry predictor contains a malformed event")
    if expected_directions is not None and (
        expected_directions.shape != positions.shape
        or expected_directions.dtype != _np.dtype(_np.int8)
        or not _np.array_equal(event, expected_directions.astype(_np.float64))
    ):
        raise _ContractViolationError("ULS trade direction differs from its source event")

    def chosen(name: str) -> _npt.NDArray[_np.float64]:
        return columns[name][positions]

    source = chosen("up_source")
    score = chosen("up_score")
    entry = chosen("up_entry_ref")
    atr_exec = chosen("up_atr_exec")
    atr_signal = chosen("up_atr_sig")
    if (
        not _np.isin(source, (1.0, 2.0, 3.0)).all()
        or not _np.isfinite(score).all()
        or not _np.isfinite(entry).all()
        or _np.any(entry <= 0.0)
        or not _np.isfinite(atr_exec).all()
        or _np.any(atr_exec <= 0.0)
        or not _np.isfinite(atr_signal).all()
        or _np.any(atr_signal <= 0.0)
    ):
        raise _ContractViolationError("ULS event core geometry is malformed")

    optional: list[_npt.NDArray[_np.float64]] = []
    for name in (
        "up_sweep_extreme",
        "up_poi_top",
        "up_poi_bot",
        "up_struct_level",
        "up_opp_liquidity",
        "up_liq_price",
    ):
        raw = chosen(name)
        present = _np.isfinite(raw)
        optional.extend(
            (
                present.astype(_np.float64),
                _np.where(present, event * (raw - entry) / atr_signal, 0.0),
            )
        )
    poi_top = chosen("up_poi_top")
    poi_bottom = chosen("up_poi_bot")
    poi_width = _np.where(
        _np.isfinite(poi_top) & _np.isfinite(poi_bottom),
        (poi_top - poi_bottom) / atr_signal,
        0.0,
    )

    def finite_or_zero(name: str, *, scale: float = 1.0) -> _npt.NDArray[_np.float64]:
        raw = chosen(name)
        return _np.where(_np.isfinite(raw), raw / scale, 0.0)

    market = _np.column_stack(
        (
            event,
            (source == 1.0).astype(_np.float64),
            (source == 2.0).astype(_np.float64),
            (source == 3.0).astype(_np.float64),
            score / 100.0,
            atr_exec / entry,
            atr_signal / entry,
            atr_exec / atr_signal,
            *optional,
            poi_width,
            finite_or_zero("up_liq_touches"),
            finite_or_zero("up_sweep_dist_atr"),
            finite_or_zero("up_wick_ratio"),
            finite_or_zero("up_disp_atr"),
            finite_or_zero("up_vol_rel"),
            finite_or_zero("up_vol_rank", scale=100.0),
            finite_or_zero("up_bos"),
            finite_or_zero("up_tl"),
            finite_or_zero("up_cisd"),
            finite_or_zero("up_key_ok"),
        )
    )
    raw_context = _np.column_stack([chosen(name) for name in _CONTEXT_FEATURE_NAMES])
    if not _np.isfinite(raw_context).all():
        raise _ContractViolationError("ULS entry context is unavailable at an event")
    context_by_name = {
        name: raw_context[:, index] for index, name in enumerate(_CONTEXT_FEATURE_NAMES)
    }
    directional_wick = _np.where(
        event > 0.0,
        context_by_name["umc_lower_wick_fraction"],
        context_by_name["umc_upper_wick_fraction"],
    )
    directional_room = _np.where(
        event > 0.0,
        context_by_name["umc_room_up_48_atr"],
        context_by_name["umc_room_down_48_atr"],
    )
    context = _np.column_stack(
        (
            context_by_name["umc_atr_over_close"],
            context_by_name["umc_volume_zscore_20"],
            context_by_name["umc_volume_ratio_5_20"],
            context_by_name["umc_true_range_ratio_12_48"],
            *(event * context_by_name[f"umc_log_return_{lookback}"] for lookback in (1, 3, 12, 48)),
            event * context_by_name["umc_ema_distance_atr"],
            event * context_by_name["umc_ema_slope_12_atr"],
            context_by_name["umc_range_over_atr"],
            context_by_name["umc_body_fraction"],
            directional_wick,
            directional_room,
            event * context_by_name["umc_close_location_48"],
        )
    )
    constants = _np.broadcast_to(
        _parameter_values(params),
        (positions.size, len(_PARAM_INPUT_NAMES)),
    )
    predictors = _np.column_stack((market, context, constants)).astype(_np.float64, copy=False)
    if predictors.shape != (positions.size, len(_INPUT_NAMES)):
        raise AssertionError("ULS entry predictor schema length drifted")
    if not _np.isfinite(predictors).all():
        raise _ContractViolationError("ULS entry predictor matrix is non-finite")
    return predictors


def _predictors_from_feature_frame(
    frame: _FeatureFrame,
) -> tuple[_npt.NDArray[_np.int64], _npt.NDArray[_np.float64]]:
    if type(frame) is not _FeatureFrame or frame.column_names != _FEATURE_NAMES:
        raise _ContractViolationError("ULS entry inference requires the exact feature schema")
    uls_origin = frame.origin(_ULS_EVENT_FEATURE)
    context_origin = frame.origin(_CONTEXT_FEATURE_NAMES[0])
    if (
        type(uls_origin) is not _FeatureOrigin
        or uls_origin.provider_id != "uls_push_v7"
        or uls_origin.version != 1
        or type(context_origin) is not _FeatureOrigin
        or context_origin.provider_id != "uls_market_context_v1"
        or context_origin.version != 1
        or context_origin.params != {}
    ):
        raise _ContractViolationError("ULS entry inference feature origins are unsupported")
    if any(frame.origin(name) != uls_origin for name in (_ULS_EVENT_FEATURE, *_ULS_VALUE_FEATURES)):
        raise _ContractViolationError("ULS entry event columns do not share one origin")
    if any(frame.origin(name) != context_origin for name in _CONTEXT_FEATURE_NAMES):
        raise _ContractViolationError("ULS entry context columns do not share one origin")
    event = frame.column(_ULS_EVENT_FEATURE)
    selected = _np.flatnonzero(_np.isin(event, (-1.0, 1.0))).astype(_np.int64, copy=False)
    columns = {name: frame.column(name) for name in _FEATURE_NAMES}
    predictors = _predictor_rows_from_arrays(
        columns=columns,
        positions=selected,
        params=uls_origin.params,
    )
    return selected, predictors


def _forest_payload(forest: _RandomForestRegressor) -> _FittedPayload:
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
        raw = _np.asarray(tree.value, dtype=_np.float64)
        if raw.shape != (count, 1, 1):
            raise _ContractViolationError("ULS entry forest exported an unexpected value shape")
        values.append(raw[:, 0, 0])
    payload = _FittedPayload(
        arrays={
            "children_left": _np.concatenate(children_left),
            "children_right": _np.concatenate(children_right),
            "split_features": _np.concatenate(split_features),
            "thresholds": _np.concatenate(thresholds),
            "tree_offsets": _np.asarray(offsets, dtype=_np.int64),
            "values": _np.concatenate(values),
        },
        metadata={
            "input_count": len(_INPUT_NAMES),
            "input_schema": ",".join(_INPUT_NAMES),
            "state_schema": _STATE_SCHEMA,
            "target_schema": _TARGET_NAMES[0],
            "tree_count": len(forest.estimators_),
        },
    )
    _validate_payload(payload)
    return payload


def _forest_predict(
    payload: _FittedPayload,
    predictors: _npt.NDArray[_np.float64],
) -> _npt.NDArray[_np.float64]:
    _validate_payload(payload)
    if predictors.ndim != 2 or predictors.shape[1] != len(_INPUT_NAMES):
        raise _ContractViolationError("ULS entry inference matrix shape differs")
    if not _np.isfinite(predictors).all():
        raise _ContractViolationError("ULS entry inference matrix is non-finite")
    prediction = _np.zeros(predictors.shape[0], dtype=_np.float64)
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


def _validate_payload(payload: _FittedPayload) -> None:
    expected_metadata = {
        "input_count": len(_INPUT_NAMES),
        "input_schema": ",".join(_INPUT_NAMES),
        "state_schema": _STATE_SCHEMA,
        "target_schema": _TARGET_NAMES[0],
        "tree_count": payload.metadata.get("tree_count"),
    }
    if type(payload) is not _FittedPayload or payload.metadata != expected_metadata:
        raise _ContractViolationError("fitted payload is not ULS entry forest state")
    if payload.array_names != (
        "children_left",
        "children_right",
        "split_features",
        "thresholds",
        "tree_offsets",
        "values",
    ):
        raise _ContractViolationError("ULS entry forest arrays are missing or reordered")
    tree_count = payload.metadata.get("tree_count")
    if type(tree_count) is not int or tree_count <= 0:
        raise _ContractViolationError("ULS entry forest tree count is malformed")
    offsets = payload.array("tree_offsets")
    left = payload.array("children_left")
    right = payload.array("children_right")
    features = payload.array("split_features")
    thresholds = payload.array("thresholds")
    values = payload.array("values")
    total = int(offsets[-1]) if offsets.size else -1
    if (
        offsets.dtype != _np.dtype(_np.int64)
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
        or values.shape != (total,)
        or not _np.isfinite(thresholds).all()
        or not _np.isfinite(values).all()
    ):
        raise _ContractViolationError("ULS entry forest fitted arrays are malformed")
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
            or _np.any(tree_features[~leaf] >= len(_INPUT_NAMES))
        ):
            raise _ContractViolationError("ULS entry forest tree topology is malformed")


def _validate_state(state: _FittedState) -> None:
    if type(state) is not _FittedState:
        raise _ContractViolationError("ULS entry inference requires exact FittedState")
    _validate_payload(state.payload)


_CORPUS_MODELS.add("uls_entry_forest_v1", UlsEntryForestV1)
