"""One frozen ULS forest choosing abstention or one of four TP actions."""

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

__all__ = ["UlsJointActionForestV1"]

_TP_PIPS = (200, 400, 600, 800)
_TARGET_NAMES = tuple(f"value_target_{index}" for index in range(len(_TP_PIPS)))
_OUTPUT_NAMES = (
    "score",
    "baseline_score",
    *(f"value_{index}" for index in range(len(_TP_PIPS))),
)
_STATE_SCHEMA = "uls_joint_action_forest_v1"


class UlsJointActionForestV1:
    """Predict four action values and abstain when none clears one hurdle."""

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
        if (
            type(selection_threshold) is not float
            or not _math.isfinite(selection_threshold)
            or selection_threshold < 0.0
        ):
            raise _ContractViolationError(
                "selection_threshold must be a finite non-negative Python float"
            )
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
        return "uls_joint_action_forest_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return every identity-bearing forest and decision parameter."""
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
        """Use the exact causal ULS event and market-context schema."""
        return _FEATURE_NAMES

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that no representation stage is consumed."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the joint decision, raw baseline and four action values."""
        return _OUTPUT_NAMES

    @property
    def cost_hint(self) -> _CostHint:
        """Declare deterministic corpus fitting as expensive."""
        return _CostHint.EXPENSIVE

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact repeatability in the clamped runtime."""
        return None

    def fit_corpus(self, inputs: _CorpusModelInputs) -> _FittedPayload:
        """Fit one weighted multi-output forest for all four TP actions."""
        if type(inputs) is not _CorpusModelInputs:
            raise _ContractViolationError("ULS joint fit requires exact CorpusModelInputs")
        if inputs.input_names != _INPUT_NAMES or inputs.target_names != _TARGET_NAMES:
            raise _ContractViolationError("ULS joint corpus schema differs from frozen v1")
        predictors = _np.column_stack([inputs.input(name) for name in _INPUT_NAMES])
        targets = _np.column_stack([inputs.target(name) for name in _TARGET_NAMES])
        if not _np.isfinite(predictors).all() or not _np.isfinite(targets).all():
            raise _ContractViolationError("ULS joint corpus contains non-finite values")
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
        """Choose entry versus abstention and expose all four TP values."""
        _validate_state(fitted_state)
        selected, predictors = _predictors_from_feature_frame(inputs.features)
        predictions = _forest_predict(fitted_state.payload, predictors)
        event = inputs.features.column("up_event_signed")[selected]
        score = _np.full(event.shape, _np.nan, dtype=_np.float64)
        accepted = _np.max(predictions, axis=1) > self._selection_threshold
        score[accepted] = event[accepted]
        stage = _StageOrigin(self.model_id, self.version, self.params)
        columns = {
            "score": score,
            "baseline_score": event.astype(_np.float64, copy=True),
            **{
                f"value_{index}": predictions[:, index].astype(_np.float64, copy=False)
                for index in range(len(_TP_PIPS))
            },
        }
        return _make_model_output_frame(
            view=view,
            keys=inputs.features.keys[selected],
            columns=columns,
            origins=dict.fromkeys(_OUTPUT_NAMES, stage),
        )


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
        if raw.shape != (count, len(_TP_PIPS), 1):
            raise _ContractViolationError("ULS joint forest exported an unexpected value shape")
        values.append(raw[:, :, 0])
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
            "input_count": len(_INPUT_NAMES),
            "input_schema": ",".join(_INPUT_NAMES),
            "state_schema": _STATE_SCHEMA,
            "target_schema": ",".join(_TARGET_NAMES),
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
    if (
        predictors.ndim != 2
        or predictors.shape[1] != len(_INPUT_NAMES)
        or not _np.isfinite(predictors).all()
    ):
        raise _ContractViolationError("ULS joint inference matrix is malformed")
    prediction = _np.zeros((predictors.shape[0], len(_TP_PIPS)), dtype=_np.float64)
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
    expected = {
        "input_count": len(_INPUT_NAMES),
        "input_schema": ",".join(_INPUT_NAMES),
        "state_schema": _STATE_SCHEMA,
        "target_schema": ",".join(_TARGET_NAMES),
    }
    if type(payload) is not _FittedPayload or any(
        payload.metadata.get(name) != value for name, value in expected.items()
    ):
        raise _ContractViolationError("fitted payload is not ULS joint forest state")
    if payload.array_names != (
        "children_left",
        "children_right",
        "split_features",
        "thresholds",
        "tree_offsets",
        "values",
    ):
        raise _ContractViolationError("ULS joint fitted arrays are missing or reordered")
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
        or values.shape != (total, len(_TP_PIPS))
        or not _np.isfinite(thresholds).all()
        or not _np.isfinite(values).all()
    ):
        raise _ContractViolationError("ULS joint fitted arrays are malformed")
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
            raise _ContractViolationError("ULS joint forest tree topology is malformed")


def _validate_state(state: _FittedState) -> None:
    if type(state) is not _FittedState:
        raise _ContractViolationError("ULS joint inference requires exact FittedState")
    _validate_payload(state.payload)


_CORPUS_MODELS.add("uls_joint_action_forest_v1", UlsJointActionForestV1)
