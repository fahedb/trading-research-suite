"""Combined frozen ULS entry gate and seven-arm TP value selector."""

from __future__ import annotations

import math as _math
import typing as _typing

import numpy as _np
from sklearn.ensemble import (  # type: ignore[import-untyped]
    RandomForestRegressor as _RandomForestRegressor,
)

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
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
from trading_research.models.uls_entry_selector import (
    _FEATURE_NAMES,
    _INPUT_NAMES,
    _predictors_from_feature_frame,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

__all__ = ["UlsEntryTpForestV1"]

_TP_PIPS = (200, 300, 400, 500, 600, 700, 800)
_ROLE_TARGET = "role_target"
_ENTRY_TARGET = "entry_value_target"
_TP_TARGET_NAMES = tuple(f"value_target_{index}" for index in range(len(_TP_PIPS)))
_TARGET_NAMES = (_ROLE_TARGET, _ENTRY_TARGET, *_TP_TARGET_NAMES)
_OUTPUT_NAMES = ("score", *(f"value_{index}" for index in range(len(_TP_PIPS))))
_STATE_SCHEMA = "uls_entry_tp_forest_v1"
_ENTRY_PREFIX = "entry_"
_SELECTOR_PREFIX = "selector_"
_TREE_ARRAY_NAMES = (
    "children_left",
    "children_right",
    "split_features",
    "thresholds",
    "tree_offsets",
    "values",
)


class UlsEntryTpForestV1:
    """Reproduce one entry forest and value seven fixed TP alternatives."""

    __slots__ = (
        "_bootstrap",
        "_entry_max_depth",
        "_entry_max_features",
        "_entry_min_samples_leaf",
        "_entry_n_estimators",
        "_entry_random_seed",
        "_selection_threshold",
        "_selector_max_depth",
        "_selector_max_features",
        "_selector_min_samples_leaf",
        "_selector_n_estimators",
        "_selector_random_seed",
    )

    def __init__(
        self,
        *,
        entry_n_estimators: int,
        entry_max_depth: int,
        entry_min_samples_leaf: int,
        entry_max_features: float,
        selector_n_estimators: int,
        selector_max_depth: int,
        selector_min_samples_leaf: int,
        selector_max_features: float,
        bootstrap: bool,
        entry_random_seed: int,
        selector_random_seed: int,
        selection_threshold: float,
    ) -> None:
        for integer_name, integer_value in (
            ("entry_n_estimators", entry_n_estimators),
            ("entry_max_depth", entry_max_depth),
            ("entry_min_samples_leaf", entry_min_samples_leaf),
            ("selector_n_estimators", selector_n_estimators),
            ("selector_max_depth", selector_max_depth),
            ("selector_min_samples_leaf", selector_min_samples_leaf),
        ):
            if type(integer_value) is not int or integer_value <= 0:
                raise _ContractViolationError(
                    f"{integer_name} must be a positive Python int"
                )
        for fraction_name, fraction_value in (
            ("entry_max_features", entry_max_features),
            ("selector_max_features", selector_max_features),
        ):
            if type(fraction_value) is not float or not 0.0 < fraction_value <= 1.0:
                raise _ContractViolationError(
                    f"{fraction_name} must be a float in (0, 1]"
                )
        if type(bootstrap) is not bool:
            raise _ContractViolationError("bootstrap must be a Python bool")
        for seed_name, seed_value in (
            ("entry_random_seed", entry_random_seed),
            ("selector_random_seed", selector_random_seed),
        ):
            if type(seed_value) is not int or seed_value < 0:
                raise _ContractViolationError(
                    f"{seed_name} must be a non-negative Python int"
                )
        if type(selection_threshold) is not float or not _math.isfinite(selection_threshold):
            raise _ContractViolationError("selection_threshold must be a finite Python float")
        self._entry_n_estimators = entry_n_estimators
        self._entry_max_depth = entry_max_depth
        self._entry_min_samples_leaf = entry_min_samples_leaf
        self._entry_max_features = entry_max_features
        self._selector_n_estimators = selector_n_estimators
        self._selector_max_depth = selector_max_depth
        self._selector_min_samples_leaf = selector_min_samples_leaf
        self._selector_max_features = selector_max_features
        self._bootstrap = bootstrap
        self._entry_random_seed = entry_random_seed
        self._selector_random_seed = selector_random_seed
        self._selection_threshold = selection_threshold

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "uls_entry_tp_forest_v1"

    @property
    def version(self) -> int:
        """Return the stable contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return every identity-bearing solver and gate parameter."""
        return {
            "bootstrap": self._bootstrap,
            "entry_max_depth": self._entry_max_depth,
            "entry_max_features": self._entry_max_features,
            "entry_min_samples_leaf": self._entry_min_samples_leaf,
            "entry_n_estimators": self._entry_n_estimators,
            "entry_random_seed": self._entry_random_seed,
            "selection_threshold": self._selection_threshold,
            "selector_max_depth": self._selector_max_depth,
            "selector_max_features": self._selector_max_features,
            "selector_min_samples_leaf": self._selector_min_samples_leaf,
            "selector_n_estimators": self._selector_n_estimators,
            "selector_random_seed": self._selector_random_seed,
        }

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Use the exact already-qualified ULS event and market context."""
        return _FEATURE_NAMES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation stage is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return one gated event and seven generic alternative values."""
        return _OUTPUT_NAMES

    @property
    def cost_hint(self) -> _CostHint:
        """Declare two deterministic forest fits as expensive."""
        return _CostHint.EXPENSIVE

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact repeatability under the clamped runtime."""
        return None

    def fit_corpus(self, inputs: _CorpusModelInputs) -> _FittedPayload:
        """Fit the entry and selector forests on their explicitly tagged rows."""
        if type(inputs) is not _CorpusModelInputs:
            raise _ContractViolationError("ULS TP fit requires exact CorpusModelInputs")
        if inputs.input_names != _INPUT_NAMES or inputs.target_names != _TARGET_NAMES:
            raise _ContractViolationError("ULS TP corpus schema differs from frozen v1")
        predictors = _np.column_stack([inputs.input(name) for name in _INPUT_NAMES])
        role = inputs.target(_ROLE_TARGET)
        if not _np.isfinite(predictors).all() or not _np.isin(role, (0.0, 1.0)).all():
            raise _ContractViolationError("ULS TP corpus contains malformed predictors or roles")
        entry_rows = role == 0.0
        selector_rows = role == 1.0
        if _np.count_nonzero(entry_rows) < 100 or _np.count_nonzero(selector_rows) < 30:
            raise _ContractViolationError("ULS TP corpus has too few rows for one fit role")
        entry_target = inputs.target(_ENTRY_TARGET)[entry_rows]
        selector_targets = _np.column_stack(
            [inputs.target(name)[selector_rows] for name in _TP_TARGET_NAMES]
        )
        if not _np.isfinite(entry_target).all() or not _np.isfinite(selector_targets).all():
            raise _ContractViolationError("ULS TP corpus contains non-finite targets")
        entry = _RandomForestRegressor(
            n_estimators=self._entry_n_estimators,
            criterion="squared_error",
            max_depth=self._entry_max_depth,
            min_samples_leaf=self._entry_min_samples_leaf,
            max_features=self._entry_max_features,
            bootstrap=self._bootstrap,
            random_state=self._entry_random_seed,
            n_jobs=1,
        )
        selector = _RandomForestRegressor(
            n_estimators=self._selector_n_estimators,
            criterion="squared_error",
            max_depth=self._selector_max_depth,
            min_samples_leaf=self._selector_min_samples_leaf,
            max_features=self._selector_max_features,
            bootstrap=self._bootstrap,
            random_state=self._selector_random_seed,
            n_jobs=1,
        )
        entry.fit(
            predictors[entry_rows],
            entry_target,
            sample_weight=inputs.weights[entry_rows],
        )
        selector.fit(
            predictors[selector_rows],
            selector_targets,
            sample_weight=inputs.weights[selector_rows],
        )
        payload = _combined_payload(entry, selector)
        _validate_payload(payload)
        return payload

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Gate ULS events and value all seven alternatives row-locally."""
        _validate_state(fitted_state)
        selected, predictors = _predictors_from_feature_frame(inputs.features)
        entry_prediction = _predict_prefixed(
            fitted_state.payload,
            predictors,
            prefix=_ENTRY_PREFIX,
            output_count=1,
        )[:, 0]
        alternatives = _predict_prefixed(
            fitted_state.payload,
            predictors,
            prefix=_SELECTOR_PREFIX,
            output_count=len(_TP_PIPS),
        )
        event = inputs.features.column("up_event_signed")[selected]
        score = _np.full(event.shape, _np.nan, dtype=_np.float64)
        score[entry_prediction > self._selection_threshold] = event[
            entry_prediction > self._selection_threshold
        ]
        stage = _StageOrigin(self.model_id, self.version, self.params)
        columns = {"score": score}
        columns.update(
            {
                f"value_{index}": alternatives[:, index].astype(_np.float64, copy=False)
                for index in range(len(_TP_PIPS))
            }
        )
        return _make_model_output_frame(
            view=view,
            keys=inputs.features.keys[selected],
            columns=columns,
            origins=dict.fromkeys(_OUTPUT_NAMES, stage),
        )


def _combined_payload(
    entry: _RandomForestRegressor,
    selector: _RandomForestRegressor,
) -> _FittedPayload:
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {}
    entry_arrays = _export_forest(entry, output_count=1)
    selector_arrays = _export_forest(selector, output_count=len(_TP_PIPS))
    arrays.update({f"{_ENTRY_PREFIX}{name}": value for name, value in entry_arrays.items()})
    arrays.update(
        {f"{_SELECTOR_PREFIX}{name}": value for name, value in selector_arrays.items()}
    )
    return _FittedPayload(
        arrays=arrays,
        metadata={
            "entry_tree_count": len(entry.estimators_),
            "input_count": len(_INPUT_NAMES),
            "input_schema": ",".join(_INPUT_NAMES),
            "selector_output_count": len(_TP_PIPS),
            "selector_tree_count": len(selector.estimators_),
            "state_schema": _STATE_SCHEMA,
            "target_schema": ",".join(_TARGET_NAMES),
        },
    )


def _export_forest(
    forest: _RandomForestRegressor,
    *,
    output_count: int,
) -> dict[str, _npt.NDArray[_typing.Any]]:
    offsets = [0]
    left: list[_npt.NDArray[_np.int64]] = []
    right: list[_npt.NDArray[_np.int64]] = []
    features: list[_npt.NDArray[_np.int64]] = []
    thresholds: list[_npt.NDArray[_np.float64]] = []
    values: list[_npt.NDArray[_np.float64]] = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        count = int(tree.node_count)
        offsets.append(offsets[-1] + count)
        left.append(_np.asarray(tree.children_left, dtype=_np.int64))
        right.append(_np.asarray(tree.children_right, dtype=_np.int64))
        features.append(_np.asarray(tree.feature, dtype=_np.int64))
        thresholds.append(_np.asarray(tree.threshold, dtype=_np.float64))
        raw = _np.asarray(tree.value, dtype=_np.float64)
        if raw.shape != (count, output_count, 1):
            raise _ContractViolationError("ULS TP forest exported an unexpected value shape")
        values.append(raw[:, :, 0])
    return {
        "children_left": _np.concatenate(left),
        "children_right": _np.concatenate(right),
        "split_features": _np.concatenate(features),
        "thresholds": _np.concatenate(thresholds),
        "tree_offsets": _np.asarray(offsets, dtype=_np.int64),
        "values": _np.concatenate(values, axis=0),
    }


def _predict_prefixed(
    payload: _FittedPayload,
    predictors: _npt.NDArray[_np.float64],
    *,
    prefix: str,
    output_count: int,
) -> _npt.NDArray[_np.float64]:
    if predictors.ndim != 2 or predictors.shape[1] != len(_INPUT_NAMES):
        raise _ContractViolationError("ULS TP inference matrix shape differs")
    prediction = _np.zeros((predictors.shape[0], output_count), dtype=_np.float64)
    offsets = payload.array(f"{prefix}tree_offsets")
    left = payload.array(f"{prefix}children_left")
    right = payload.array(f"{prefix}children_right")
    features = payload.array(f"{prefix}split_features")
    thresholds = payload.array(f"{prefix}thresholds")
    values = payload.array(f"{prefix}values")
    for tree_index in range(offsets.size - 1):
        start = int(offsets[tree_index])
        nodes = _np.zeros(predictors.shape[0], dtype=_np.int64)
        while nodes.size:
            absolute = start + nodes
            active = left[absolute] != -1
            if not _np.any(active):
                break
            rows = _np.flatnonzero(active)
            absolute_active = absolute[rows]
            columns = features[absolute_active]
            go_left = predictors[rows, columns] <= thresholds[absolute_active]
            nodes[rows] = _np.where(
                go_left,
                left[absolute_active],
                right[absolute_active],
            )
        prediction += values[start + nodes]
    return prediction / float(offsets.size - 1)


def _entry_payload(payload: _FittedPayload) -> _FittedPayload:
    """Extract the entry sub-state for exact comparison with the frozen gate."""
    _validate_payload(payload)
    arrays = {
        name: payload.array(f"{_ENTRY_PREFIX}{name}") for name in _TREE_ARRAY_NAMES
    }
    values = arrays["values"]
    if values.ndim != 2 or values.shape[1] != 1:
        raise _ContractViolationError("ULS TP entry values cannot be flattened")
    arrays["values"] = values[:, 0]
    return _FittedPayload(
        arrays=arrays,
        metadata={
            "input_count": len(_INPUT_NAMES),
            "input_schema": ",".join(_INPUT_NAMES),
            "state_schema": "uls_entry_forest_v1",
            "target_schema": "value_target",
            "tree_count": payload.metadata["entry_tree_count"],
        },
    )


def _validate_payload(payload: _FittedPayload) -> None:
    expected_arrays = tuple(
        sorted(
            (*(f"{_ENTRY_PREFIX}{name}" for name in _TREE_ARRAY_NAMES),
             *(f"{_SELECTOR_PREFIX}{name}" for name in _TREE_ARRAY_NAMES))
        )
    )
    if type(payload) is not _FittedPayload or payload.array_names != expected_arrays:
        raise _ContractViolationError("ULS TP fitted arrays are missing or reordered")
    expected_metadata = {
        "input_count": len(_INPUT_NAMES),
        "input_schema": ",".join(_INPUT_NAMES),
        "selector_output_count": len(_TP_PIPS),
        "state_schema": _STATE_SCHEMA,
        "target_schema": ",".join(_TARGET_NAMES),
    }
    if any(payload.metadata.get(name) != value for name, value in expected_metadata.items()):
        raise _ContractViolationError("fitted payload is not ULS entry TP forest state")
    for prefix, count_name, output_count in (
        (_ENTRY_PREFIX, "entry_tree_count", 1),
        (_SELECTOR_PREFIX, "selector_tree_count", len(_TP_PIPS)),
    ):
        tree_count = payload.metadata.get(count_name)
        offsets = payload.array(f"{prefix}tree_offsets")
        left = payload.array(f"{prefix}children_left")
        right = payload.array(f"{prefix}children_right")
        features = payload.array(f"{prefix}split_features")
        thresholds = payload.array(f"{prefix}thresholds")
        values = payload.array(f"{prefix}values")
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
            or values.shape != (total, output_count)
            or not _np.isfinite(thresholds).all()
            or not _np.isfinite(values).all()
        ):
            raise _ContractViolationError("ULS TP forest fitted arrays are malformed")
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
                raise _ContractViolationError("ULS TP forest tree topology is malformed")


def _validate_state(state: _FittedState) -> None:
    if type(state) is not _FittedState:
        raise _ContractViolationError("ULS TP inference requires exact FittedState")
    _validate_payload(state.payload)


_CORPUS_MODELS.add("uls_entry_tp_forest_v1", UlsEntryTpForestV1)
