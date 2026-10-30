"""Slice-3D release-blocking model and representation leakage rigs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pytest

from trading_research.artifacts import ArtifactStore, MaterializationPolicy, PlatformIdentity
from trading_research.core import (
    ClockDomain,
    CostHint,
    DatasetRef,
    FeatureOrigin,
    FittedPayload,
    FittedState,
    GapPolicy,
    ModelInputs,
    ModelOutputFrame,
    NativeTimeKey,
    RepresentationFrame,
    StageOrigin,
    TimeUnit,
    TrainingTargetFrame,
    ViewRole,
    ViewSpan,
    WindowSpec,
)
from trading_research.core.feature_frames import FeatureFrame, _restore_feature_frame
from trading_research.core.research import _make_corpus_model_inputs
from trading_research.core.research_frames import (
    _make_fitted_state,
    _make_model_output_frame,
    _make_representation_frame,
    _make_training_target_frame,
)
from trading_research.data.gating import SplitPolicyIdentity
from trading_research.experiments.fitting import (
    _infer_corpus_model,
    encode_representation,
    fit_model,
    fit_representation,
    infer_model,
)
from trading_research.experiments.folds import _no_fold_fit_isolation
from trading_research.models import CORPUS_RESEARCH_MODELS, RESEARCH_MODELS
from trading_research.models.reference import _GSL_FEATURES, _MC_FEATURES, _ULS_VALUE_FEATURES
from trading_research.models.uls_entry_selector import (
    _CONTEXT_FEATURE_NAMES as _ULS_CONTEXT_FEATURE_NAMES,
)
from trading_research.models.uls_entry_selector import (
    _FEATURE_NAMES as _ULS_ENTRY_FEATURE_NAMES,
)
from trading_research.models.uls_entry_selector import (
    _INPUT_NAMES as _ULS_ENTRY_INPUT_NAMES,
)
from trading_research.models.uls_entry_selector import (
    _TARGET_NAMES as _ULS_ENTRY_TARGET_NAMES,
)
from trading_research.models.uls_joint_selector import (
    _TARGET_NAMES as _ULS_JOINT_TARGET_NAMES,
)
from trading_research.models.uls_tp_selector import (
    _ENTRY_TARGET as _ULS_TP_ENTRY_TARGET,
)
from trading_research.models.uls_tp_selector import (
    _ROLE_TARGET as _ULS_TP_ROLE_TARGET,
)
from trading_research.models.uls_tp_selector import (
    _TP_TARGET_NAMES as _ULS_TP_TARGET_NAMES,
)
from trading_research.models.zone_selector import (
    _CONTEXT_FEATURE_NAMES,
    _FEATURE_NAMES,
    _INPUT_NAMES,
    _TARGET_NAMES,
    _V2_FEATURE_NAMES,
    _V2_INPUT_NAMES,
)
from trading_research.representations import REPRESENTATION_MODELS

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import numpy.typing as npt

    from trading_research.core import ParamValue

_TRAIN_ROWS = 48
_EVAL_ROWS = 64
_DOMAIN = ClockDomain("model-rig.native")
_REF = DatasetRef("MODEL_RIG_TRAIN", "a" * 64, ViewRole.TRAIN)
_FEATURE_ORIGIN = FeatureOrigin(
    "log_return_1",
    1,
    {},
    WindowSpec(1, GapPolicy.CROSS),
)
_ZONE_EVENT_ORIGIN = FeatureOrigin(
    "zone_trader_v13",
    1,
    {},
    WindowSpec(26_534, GapPolicy.CROSS),
)
_GSL_EVENT_ORIGIN = FeatureOrigin(
    "gap_sensitive_levels_h1_v1",
    1,
    {},
    WindowSpec(120_780, GapPolicy.CROSS),
)
_MC_EVENT_ORIGIN = FeatureOrigin(
    "multi_confluence_m15_v1",
    1,
    {},
    WindowSpec(645_120, GapPolicy.CROSS),
)
_ULS_EVENT_ORIGIN = FeatureOrigin(
    "uls_push_v7",
    1,
    {},
    WindowSpec(5_734, GapPolicy.CROSS),
)
_SPLIT = SplitPolicyIdentity("ADR-0012", "v1", "b" * 64)
_PLATFORM = PlatformIdentity("c" * 40, False, None)
_DEPENDENCY = "d" * 64
_ISOLATION = _no_fold_fit_isolation()

LIVE_MODEL_NAMES = tuple(sorted(RESEARCH_MODELS.names()))
LIVE_CORPUS_MODEL_NAMES = tuple(sorted(CORPUS_RESEARCH_MODELS.names()))
COVERED_MODEL_NAMES = frozenset(
    {
        "gap_sensitive_gate_v1",
        "multi_confluence_gate_v1",
        "reference_library_v1",
        "stateless_rule_v1",
        "supervised_mean_v1",
        "uls_push_gate_v1",
        "zone_event_gate_v1",
    }
)
LIVE_REPRESENTATION_NAMES = tuple(sorted(REPRESENTATION_MODELS.names()))
COVERED_REPRESENTATION_NAMES = frozenset({"trailing_standardizer_v1"})
TARGETED_MODEL_NAMES = frozenset({"reference_library_v1", "supervised_mean_v1"})
REPRESENTED_MODEL_NAMES = frozenset({"reference_library_v1"})
_ZONE_PARAMS: Mapping[str, ParamValue] = {
    "anchor_grid_seconds": 3600,
    "anchor_phase_offset_seconds": 0,
    "bar_grid_seconds": 300,
    "bar_phase_offset_seconds": 0,
    "break_buffer_atr": 0.5,
    "confirmation_mode": "rejection_wick",
    "max_bars_wait": 6,
    "max_base_atr": 0.8,
    "max_base_body": 0.5,
    "max_zone_age_rows": 5000,
    "max_zones": 15,
    "min_fvg_atr": 0.25,
    "min_move_atr": 1.2,
    "minimum_wick": 0.35,
    "reward_risk_target": 2.0,
    "stop_buffer_atr": 0.5,
    "use_demand_supply": False,
    "use_imbalance": False,
    "use_volume_zones": True,
    "vol_len": 20,
    "vol_mult": 2.0,
    "w_atr": 300,
}


class _LeakageMismatchError(AssertionError):
    """An exact row-locality comparison changed under an adversary."""


class _ForbiddenForwardReadError(AssertionError):
    """A fit plugin reached a test-only source beyond its bounded inputs."""


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, _DOMAIN)


def _view(keys: npt.NDArray[np.int64]) -> ViewSpan:
    return ViewSpan(
        (_REF.canonical_identity,),
        TimeUnit.SECONDS,
        _DOMAIN,
        _key(int(keys[0])),
        _key(int(keys[0])),
        _key(int(keys[-1])),
    )


def _training_values() -> npt.NDArray[np.float64]:
    positions = np.arange(_TRAIN_ROWS, dtype=np.float64)
    return (0.03 * positions + np.sin(positions / 3.0)).astype(np.float64)


def _evaluation_values() -> npt.NDArray[np.float64]:
    positions = np.arange(_EVAL_ROWS, dtype=np.float64)
    return (0.02 * positions * positions - 0.4 * positions + 3.0).astype(np.float64)


def _features(
    values: npt.NDArray[np.float64],
    *,
    start_key: int,
    feature_name: str = "log_return_1",
) -> FeatureFrame:
    keys = np.arange(start_key, start_key + values.size, dtype=np.int64)
    columns = {feature_name: values}
    origins = {
        feature_name: (
            _ZONE_EVENT_ORIGIN
            if feature_name == "zt_event_signed"
            else _GSL_EVENT_ORIGIN
            if feature_name == "gsl_event_signed"
            else _MC_EVENT_ORIGIN
            if feature_name == "mc_event_signed"
            else _ULS_EVENT_ORIGIN
            if feature_name == "up_event_signed"
            else _FEATURE_ORIGIN
        )
    }
    if feature_name == "up_event_signed":
        for index, name in enumerate(_ULS_VALUE_FEATURES):
            columns[name] = values + float(index + 1)
            origins[name] = _ULS_EVENT_ORIGIN
    if feature_name == "mc_event_signed":
        columns[_MC_FEATURES[1]] = np.where(values > 0.0, 4.0, 1.0)
        columns[_MC_FEATURES[2]] = np.where(values < 0.0, 4.0, 1.0)
        origins[_MC_FEATURES[1]] = _MC_EVENT_ORIGIN
        origins[_MC_FEATURES[2]] = _MC_EVENT_ORIGIN
    if feature_name == "gsl_event_signed":
        columns[_GSL_FEATURES[1]] = np.where(values > 0.0, 2.0, 1.0)
        columns[_GSL_FEATURES[2]] = np.where(values < 0.0, 2.0, 1.0)
        columns[_GSL_FEATURES[3]] = np.zeros(values.size, dtype=np.float64)
        origins[_GSL_FEATURES[1]] = _GSL_EVENT_ORIGIN
        origins[_GSL_FEATURES[2]] = _GSL_EVENT_ORIGIN
        origins[_GSL_FEATURES[3]] = _GSL_EVENT_ORIGIN
    return _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=_DOMAIN,
        source_identities=(_REF.canonical_identity,),
        columns=columns,
        origins=origins,
    )


def _model_feature_values(
    name: str,
    values: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    if name in {
        "gap_sensitive_gate_v1",
        "multi_confluence_gate_v1",
        "uls_push_gate_v1",
        "zone_event_gate_v1",
    }:
        return np.sign(values).astype(np.float64)
    return values


def _targets(features: FeatureFrame) -> TrainingTargetFrame:
    values = features.column("log_return_1")
    targets = (0.25 * values + np.cos(np.arange(values.size) / 5.0)).astype(np.float64)
    origin = StageOrigin(
        "forward_log_return_v1",
        1,
        {
            "boundary_policy": "nan",
            "decision_delay_rows": 1,
            "horizon_rows": 1,
            "reference_price_column": "close",
        },
    )
    return _make_training_target_frame(
        view=_view(features.keys),
        keys=features.keys,
        columns={"target.forward_log_return": targets},
        origins={"target.forward_log_return": origin},
    )


def _fit_representation_state(
    representation: object,
    *,
    features: FeatureFrame,
    store: ArtifactStore,
) -> FittedState:
    return fit_representation(
        representation,
        view=_view(features.keys),
        features=features,
        split_policy_identity=_SPLIT,
        fit_isolation=_ISOLATION,
        platform_identity=_PLATFORM,
        dependency_lock_sha256=_DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )


@dataclass(frozen=True)
class _ModelHarness:
    model: object
    state: FittedState
    representation: object | None
    representation_state: FittedState | None
    feature_name: str


def _prepare_model(name: str, *, store: ArtifactStore) -> _ModelHarness:
    model = RESEARCH_MODELS.create(name)
    feature_name = (
        "zt_event_signed"
        if name == "zone_event_gate_v1"
        else "gsl_event_signed"
        if name == "gap_sensitive_gate_v1"
        else "mc_event_signed"
        if name == "multi_confluence_gate_v1"
        else "up_event_signed"
        if name == "uls_push_gate_v1"
        else "log_return_1"
    )
    train = _features(
        _model_feature_values(name, _training_values()),
        start_key=100,
        feature_name=feature_name,
    )
    representation: object | None = None
    representation_state: FittedState | None = None
    train_representation: RepresentationFrame | None = None
    if name in REPRESENTED_MODEL_NAMES:
        representation = REPRESENTATION_MODELS.create("trailing_standardizer_v1")
        representation_state = _fit_representation_state(
            representation,
            features=train,
            store=store,
        )
        train_representation = encode_representation(
            representation,
            view=_view(train.keys),
            fitted_state=representation_state,
            features=train,
        )
    inputs = ModelInputs(train, train_representation)
    state = fit_model(
        model,
        view=_view(train.keys),
        inputs=inputs,
        training_targets=_targets(train) if name in TARGETED_MODEL_NAMES else None,
        representation_state=representation_state,
        split_policy_identity=_SPLIT,
        fit_isolation=_ISOLATION,
        platform_identity=_PLATFORM,
        dependency_lock_sha256=_DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )
    return _ModelHarness(model, state, representation, representation_state, feature_name)


def _run_model(
    harness: _ModelHarness,
    values: npt.NDArray[np.float64],
) -> ModelOutputFrame:
    model_id = (
        "zone_event_gate_v1"
        if harness.feature_name == "zt_event_signed"
        else "gap_sensitive_gate_v1"
        if harness.feature_name == "gsl_event_signed"
        else "multi_confluence_gate_v1"
        if harness.feature_name == "mc_event_signed"
        else "uls_push_gate_v1"
        if harness.feature_name == "up_event_signed"
        else "reference_model"
    )
    features = _features(
        _model_feature_values(model_id, values),
        start_key=1_000,
        feature_name=harness.feature_name,
    )
    representation_frame: RepresentationFrame | None = None
    if harness.representation is not None:
        assert harness.representation_state is not None
        representation_frame = encode_representation(
            harness.representation,
            view=_view(features.keys),
            fitted_state=harness.representation_state,
            features=features,
        )
    return infer_model(
        harness.model,
        view=_view(features.keys),
        fitted_state=harness.state,
        inputs=ModelInputs(features, representation_frame),
    )


def _run_zone_corpus_model(
    model_name: str,
    values: npt.NDArray[np.float64],
) -> ModelOutputFrame:
    if model_name in {
        "uls_entry_forest_v1",
        "uls_entry_tp_forest_v1",
        "uls_joint_action_forest_v1",
    }:
        return _run_uls_entry_corpus_model(values, model_name=model_name)
    keys = np.arange(1_000, 1_000 + values.size, dtype=np.int64)
    entry = 1_800.0 + np.abs(values)
    event = np.where(values >= 0.0, 1.0, -1.0).astype(np.float64)
    origin = FeatureOrigin(
        "zone_trader_v13",
        1,
        _ZONE_PARAMS,
        WindowSpec(26_534, GapPolicy.CROSS),
    )
    columns = {
        "zt_event_signed": event,
        "zt_entry_price": entry,
        "zt_stop_price": entry - 4.0,
        "zt_target_price": entry + 8.0,
        "zt_zone_top": entry + 1.0 + values / 100.0,
        "zt_zone_bottom": entry - 1.0,
        "zt_zone_kind": (np.arange(values.size) % 3).astype(np.float64),
        "zt_candidate_open": entry,
    }
    origins = dict.fromkeys(_FEATURE_NAMES, origin)
    feature_names: tuple[str, ...]
    if model_name == "zone_ridge_selector_v1":
        coefficients = np.zeros((len(_INPUT_NAMES) + 1, 3), dtype=np.float64)
        coefficients[0] = (0.0, 1.0, 2.0)
        payload = FittedPayload(
            arrays={
                "coefficients": coefficients,
                "input_means": np.zeros(len(_INPUT_NAMES), dtype=np.float64),
                "input_scales": np.ones(len(_INPUT_NAMES), dtype=np.float64),
            },
            metadata={
                "input_schema": ",".join(_INPUT_NAMES),
                "state_schema": "zone_ridge_selector_v1",
                "target_schema": "value_0,value_1,value_2",
            },
        )
        model = CORPUS_RESEARCH_MODELS.create("zone_ridge_selector_v1", ridge_lambda=1.0)
        feature_names = _FEATURE_NAMES
    else:
        context_origin = FeatureOrigin(
            "zone_context_v1",
            1,
            _ZONE_PARAMS,
            WindowSpec(26_534, GapPolicy.CROSS),
        )
        for index, name in enumerate(_CONTEXT_FEATURE_NAMES, start=1):
            columns[name] = 1.0 + values * (float(index) / 100.0)
            origins[name] = context_origin
        model = CORPUS_RESEARCH_MODELS.create(
            "zone_forest_selector_v2",
            n_estimators=4,
            max_depth=3,
            min_samples_leaf=2,
            max_features=0.75,
            bootstrap=False,
            random_seed=7,
        )
        train = np.arange(32, dtype=np.float64)
        corpus = _make_corpus_model_inputs(
            inputs={
                name: np.sin(train / float(index + 2)) for index, name in enumerate(_V2_INPUT_NAMES)
            },
            targets={
                name: np.cos(train / float(index + 3)) for index, name in enumerate(_TARGET_NAMES)
            },
            weights=np.ones(train.size, dtype=np.float64),
        )
        payload = model.fit_corpus(corpus)
        feature_names = _V2_FEATURE_NAMES
    features = _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=_DOMAIN,
        source_identities=(_REF.canonical_identity,),
        columns=columns,
        origins={name: origins[name] for name in feature_names},
    )
    state = _make_fitted_state(identity_key="e" * 64, payload=payload)
    return _infer_corpus_model(
        model,
        view=_view(keys),
        fitted_state=state,
        inputs=ModelInputs(features),
    )


def _run_uls_entry_corpus_model(
    values: npt.NDArray[np.float64],
    *,
    model_name: str = "uls_entry_forest_v1",
) -> ModelOutputFrame:
    from trading_research.features.reference import UlsPushV7

    keys = np.arange(1_000, 1_000 + values.size, dtype=np.int64)
    event = np.where(values >= 0.0, 1.0, -1.0).astype(np.float64)
    uls_origin = FeatureOrigin(
        "uls_push_v7",
        1,
        UlsPushV7().params,
        WindowSpec(28_000, GapPolicy.CROSS),
    )
    context_origin = FeatureOrigin(
        "uls_market_context_v1",
        1,
        {},
        WindowSpec(2_569, GapPolicy.CROSS),
    )
    entry = 1_800.0 + np.abs(values)
    columns: dict[str, npt.NDArray[np.float64]] = {
        "up_event_signed": event,
        "up_source": np.ones(values.size, dtype=np.float64),
        "up_score": np.full(values.size, 65.0, dtype=np.float64),
        "up_entry_ref": entry,
        "up_sweep_extreme": entry - event,
        "up_poi_top": entry + 0.5,
        "up_poi_bot": entry - 0.5,
        "up_struct_level": entry + event,
        "up_opp_liquidity": entry + 2.0 * event,
        "up_atr_exec": np.full(values.size, 1.0, dtype=np.float64),
        "up_atr_sig": np.full(values.size, 1.2, dtype=np.float64),
        "up_liq_price": entry - event,
        "up_liq_touches": np.full(values.size, 2.0, dtype=np.float64),
        "up_sweep_dist_atr": np.full(values.size, 0.25, dtype=np.float64),
        "up_wick_ratio": np.full(values.size, 0.5, dtype=np.float64),
        "up_disp_atr": np.full(values.size, 0.75, dtype=np.float64),
        "up_vol_rel": np.full(values.size, 1.1, dtype=np.float64),
        "up_vol_rank": np.full(values.size, 55.0, dtype=np.float64),
        "up_bos": np.ones(values.size, dtype=np.float64),
        "up_tl": np.zeros(values.size, dtype=np.float64),
        "up_cisd": np.zeros(values.size, dtype=np.float64),
        "up_key_ok": np.ones(values.size, dtype=np.float64),
    }
    origins = dict.fromkeys(("up_event_signed", *_ULS_VALUE_FEATURES), uls_origin)
    for index, name in enumerate(_ULS_CONTEXT_FEATURE_NAMES, start=1):
        columns[name] = 1.0 + np.abs(values) * (index / 100.0)
        origins[name] = context_origin
    features = _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=_DOMAIN,
        source_identities=(_REF.canonical_identity,),
        columns=columns,
        origins={name: origins[name] for name in _ULS_ENTRY_FEATURE_NAMES},
    )
    if model_name in {"uls_entry_forest_v1", "uls_joint_action_forest_v1"}:
        train = np.arange(32, dtype=np.float64)
        model = CORPUS_RESEARCH_MODELS.create(
            model_name,
            n_estimators=4,
            max_depth=3,
            min_samples_leaf=2,
            max_features=0.75,
            bootstrap=False,
            random_seed=7,
            selection_threshold=0.0,
        )
        targets = (
            {_ULS_ENTRY_TARGET_NAMES[0]: np.cos(train / 3.0)}
            if model_name == "uls_entry_forest_v1"
            else {
                name: np.cos(train / float(index + 3))
                for index, name in enumerate(_ULS_JOINT_TARGET_NAMES)
            }
        )
    else:
        train = np.arange(160, dtype=np.float64)
        model = CORPUS_RESEARCH_MODELS.create(
            model_name,
            entry_n_estimators=4,
            entry_max_depth=3,
            entry_min_samples_leaf=2,
            entry_max_features=0.75,
            selector_n_estimators=4,
            selector_max_depth=3,
            selector_min_samples_leaf=2,
            selector_max_features=0.75,
            bootstrap=False,
            entry_random_seed=7,
            selector_random_seed=8,
            selection_threshold=0.0,
        )
        role = np.ones(train.size, dtype=np.float64)
        role[:120] = 0.0
        targets = {
            _ULS_TP_ROLE_TARGET: role,
            _ULS_TP_ENTRY_TARGET: np.cos(train / 3.0),
            **{
                name: np.sin(train / float(index + 2))
                for index, name in enumerate(_ULS_TP_TARGET_NAMES)
            },
        }
    corpus = _make_corpus_model_inputs(
        inputs={
            name: np.sin(train / float(index + 2))
            for index, name in enumerate(_ULS_ENTRY_INPUT_NAMES)
        },
        targets=targets,
        weights=np.ones(train.size, dtype=np.float64),
    )
    payload = model.fit_corpus(corpus)
    state = _make_fitted_state(identity_key="f" * 64, payload=payload)
    return _infer_corpus_model(
        model,
        view=_view(keys),
        fitted_state=state,
        inputs=ModelInputs(features),
    )


def _prepare_representation(name: str, *, store: ArtifactStore) -> tuple[object, FittedState]:
    representation = REPRESENTATION_MODELS.create(name)
    train = _features(_training_values(), start_key=100)
    state = _fit_representation_state(representation, features=train, store=store)
    return representation, state


def _run_representation(
    representation: object,
    state: FittedState,
    values: npt.NDArray[np.float64],
) -> RepresentationFrame:
    features = _features(values, start_key=1_000)
    return encode_representation(
        representation,
        view=_view(features.keys),
        fitted_state=state,
        features=features,
    )


def _perturb_range(
    values: npt.NDArray[np.float64],
    *,
    start: int,
    stop: int,
) -> npt.NDArray[np.float64]:
    changed = values.copy()
    positions = np.arange(start, stop, dtype=np.float64)
    changed[start:stop] = -17.0 * changed[start:stop] + 31.0 + positions
    return changed


def _selected_positions(
    frame: ModelOutputFrame | RepresentationFrame,
    expected_keys: npt.NDArray[np.int64],
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    mask = np.isin(frame.keys, expected_keys, assume_unique=True)
    return frame.keys[mask], np.flatnonzero(mask).astype(np.int64)


def _assert_array_bits_equal(
    left: npt.NDArray[np.float64],
    right: npt.NDArray[np.float64],
) -> None:
    if left.dtype != np.dtype(np.float64) or right.dtype != np.dtype(np.float64):
        raise _LeakageMismatchError("output dtypes differ from exact float64")
    if left.shape != right.shape:
        raise _LeakageMismatchError("output shapes differ")
    if np.isnan(left).tobytes() != np.isnan(right).tobytes():
        raise _LeakageMismatchError("output NaN masks differ")
    if left.view(np.uint64).tobytes() != right.view(np.uint64).tobytes():
        raise _LeakageMismatchError("output value bits differ")


def _assert_frames_exact(
    left: ModelOutputFrame | RepresentationFrame,
    right: ModelOutputFrame | RepresentationFrame,
    *,
    expected_keys: npt.NDArray[np.int64],
) -> None:
    if left.column_names != right.column_names:
        raise _LeakageMismatchError("output columns differ")
    left_keys, left_positions = _selected_positions(left, expected_keys)
    right_keys, right_positions = _selected_positions(right, expected_keys)
    if not np.array_equal(left_keys, right_keys):
        raise _LeakageMismatchError("output row presence differs")
    if left_keys.size == 0:
        raise _LeakageMismatchError("comparison has no output-row witness")
    for name in left.column_names:
        if left.origin(name) != right.origin(name):
            raise _LeakageMismatchError("output origins differ")
        left_values = left.column(name)[left_positions]
        right_values = right.column(name)[right_positions]
        if not np.isfinite(left_values).any():
            raise _LeakageMismatchError(f"output {name!r} has no finite witness")
        _assert_array_bits_equal(left_values, right_values)


def _assert_forward_safe(
    run: object,
) -> None:
    execute = run
    assert callable(execute)
    values = _evaluation_values()
    keys = np.arange(1_000, 1_000 + values.size, dtype=np.int64)
    for cutoff in (8, 31, 61):
        baseline = execute(values)
        perturbed = execute(_perturb_range(values, start=cutoff + 1, stop=values.size))
        _assert_frames_exact(
            baseline,
            perturbed,
            expected_keys=keys[: cutoff + 1],
        )


def _assert_row_local(
    run: object,
) -> None:
    execute = run
    assert callable(execute)
    values = _evaluation_values()
    keys = np.arange(1_000, 1_000 + values.size, dtype=np.int64)
    for target in (7, 29, 57):
        baseline = execute(values)
        perturbed_values = _perturb_range(values, start=0, stop=values.size)
        perturbed_values[target] = values[target]
        perturbed = execute(perturbed_values)
        _assert_frames_exact(
            baseline,
            perturbed,
            expected_keys=keys[target : target + 1],
        )


class _ControlModel:
    _model_id = "control.model"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def version(self) -> int:
        return 1

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        return ("log_return_1",)

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        return ("score",)

    @property
    def cost_hint(self) -> CostHint:
        return CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> None:
        return None

    def fit(
        self,
        view: ViewSpan,
        inputs: ModelInputs,
        training_targets: TrainingTargetFrame | None = None,
    ) -> FittedPayload:
        return FittedPayload(arrays={}, metadata={"state_schema": self._model_id})

    def infer(
        self,
        view: ViewSpan,
        fitted_state: FittedState,
        inputs: ModelInputs,
    ) -> ModelOutputFrame:
        return self._output(
            view,
            inputs,
            self._compute(inputs.features.column("log_return_1")),
        )

    def _compute(
        self,
        values: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        raise NotImplementedError

    def _output(
        self,
        view: ViewSpan,
        inputs: ModelInputs,
        values: npt.NDArray[np.float64],
    ) -> ModelOutputFrame:
        return _make_model_output_frame(
            view=view,
            keys=inputs.features.keys,
            columns={"score": values.astype(np.float64, copy=False)},
            origins={"score": StageOrigin(self.model_id, self.version, self.params)},
        )


class ForwardShiftModelControl(_ControlModel):
    _model_id = "control.forward_shift"

    def _compute(
        self,
        values: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        positions = np.minimum(np.arange(values.size) + 1, values.size - 1)
        return values[positions].copy()


class WholeEvaluationRangeNormalizerControl(_ControlModel):
    _model_id = "control.whole_evaluation_range_normalizer"

    def _compute(
        self,
        values: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        return ((values - values.mean()) / values.std()).astype(np.float64)


class CarriedStateAccumulatorControl(_ControlModel):
    _model_id = "control.carried_state_accumulator"

    def _compute(
        self,
        values: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        return np.cumsum(values, dtype=np.float64)


class _ForwardSourceProbe:
    calls: int

    def __init__(self) -> None:
        self.calls = 0

    def read_after_fit_end(self) -> None:
        self.calls += 1
        raise _ForbiddenForwardReadError("fit attempted its own forward read")


class SelfForwardReadFitModelControl(_ControlModel):
    _model_id = "control.self_forward_read_fit"

    def __init__(self, probe: _ForwardSourceProbe) -> None:
        self._probe = probe

    def fit(
        self,
        view: ViewSpan,
        inputs: ModelInputs,
        training_targets: TrainingTargetFrame | None = None,
    ) -> FittedPayload:
        self._probe.read_after_fit_end()
        raise AssertionError("the forward-read sentinel must stop fit")

    def _compute(
        self,
        values: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        return values.copy()


class EvaluationRangeFitRepresentationControl:
    @property
    def representation_id(self) -> str:
        return "control.evaluation_range_fit"

    @property
    def version(self) -> int:
        return 1

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        return ("log_return_1",)

    @property
    def output_column_names(self) -> tuple[str, ...]:
        return ("standardized_log_return_1",)

    @property
    def cost_hint(self) -> CostHint:
        return CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> None:
        return None

    def fit(self, view: ViewSpan, features: FeatureFrame) -> FittedPayload:
        values = features.column("log_return_1")
        return FittedPayload(
            arrays={
                "location": np.array([values.mean()], dtype=np.float64),
                "scale": np.array([values.std()], dtype=np.float64),
            },
            metadata={"state_schema": "control.evaluation_range_fit"},
        )

    def encode(
        self,
        view: ViewSpan,
        fitted_state: FittedState,
        features: FeatureFrame,
    ) -> RepresentationFrame:
        values = features.column("log_return_1")
        encoded = ((values - values.mean()) / values.std()).astype(np.float64)
        return _make_representation_frame(
            view=view,
            features=features,
            columns={"standardized_log_return_1": encoded},
            origins={
                "standardized_log_return_1": StageOrigin(
                    self.representation_id,
                    self.version,
                    self.params,
                )
            },
        )


def _prepare_control_model(
    control: _ControlModel,
    *,
    store: ArtifactStore,
) -> _ModelHarness:
    train = _features(_training_values(), start_key=100)
    state = fit_model(
        control,
        view=_view(train.keys),
        inputs=ModelInputs(train),
        training_targets=None,
        representation_state=None,
        split_policy_identity=_SPLIT,
        fit_isolation=_ISOLATION,
        platform_identity=_PLATFORM,
        dependency_lock_sha256=_DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )
    return _ModelHarness(control, state, None, None, "log_return_1")


def test_rig_coverage_is_an_exact_independent_match_for_both_live_registries() -> None:
    assert RESEARCH_MODELS.names() == COVERED_MODEL_NAMES
    assert set(LIVE_MODEL_NAMES) == COVERED_MODEL_NAMES
    assert REPRESENTATION_MODELS.names() == COVERED_REPRESENTATION_NAMES
    assert set(LIVE_REPRESENTATION_NAMES) == COVERED_REPRESENTATION_NAMES
    assert TARGETED_MODEL_NAMES <= COVERED_MODEL_NAMES
    assert REPRESENTED_MODEL_NAMES <= TARGETED_MODEL_NAMES
    assert LIVE_CORPUS_MODEL_NAMES == (
        "uls_entry_forest_v1",
        "uls_entry_tp_forest_v1",
        "uls_joint_action_forest_v1",
        "zone_forest_selector_v2",
        "zone_ridge_selector_v1",
    )


@pytest.mark.parametrize("model_name", LIVE_CORPUS_MODEL_NAMES)
def test_every_registered_corpus_model_is_forward_blind(model_name: str) -> None:
    _assert_forward_safe(lambda values: _run_zone_corpus_model(model_name, values))


@pytest.mark.parametrize("model_name", LIVE_CORPUS_MODEL_NAMES)
def test_every_registered_corpus_model_is_row_local(model_name: str) -> None:
    _assert_row_local(lambda values: _run_zone_corpus_model(model_name, values))


@pytest.mark.parametrize("model_name", LIVE_MODEL_NAMES)
def test_every_registered_model_is_forward_blind(
    model_name: str,
    tmp_path: Path,
) -> None:
    harness = _prepare_model(model_name, store=ArtifactStore(tmp_path))
    _assert_forward_safe(lambda values: _run_model(harness, values))


@pytest.mark.parametrize("model_name", LIVE_MODEL_NAMES)
def test_every_registered_model_is_row_local(
    model_name: str,
    tmp_path: Path,
) -> None:
    harness = _prepare_model(model_name, store=ArtifactStore(tmp_path))
    _assert_row_local(lambda values: _run_model(harness, values))


@pytest.mark.parametrize("representation_name", LIVE_REPRESENTATION_NAMES)
def test_every_registered_representation_is_forward_blind(
    representation_name: str,
    tmp_path: Path,
) -> None:
    representation, state = _prepare_representation(
        representation_name,
        store=ArtifactStore(tmp_path),
    )
    _assert_forward_safe(lambda values: _run_representation(representation, state, values))


@pytest.mark.parametrize("representation_name", LIVE_REPRESENTATION_NAMES)
def test_every_registered_representation_is_row_local(
    representation_name: str,
    tmp_path: Path,
) -> None:
    representation, state = _prepare_representation(
        representation_name,
        store=ArtifactStore(tmp_path),
    )
    _assert_row_local(lambda values: _run_representation(representation, state, values))


@pytest.mark.parametrize(
    "control",
    [ForwardShiftModelControl, WholeEvaluationRangeNormalizerControl],
)
def test_forward_rig_catches_forward_and_range_controls(
    control: type[_ControlModel],
    tmp_path: Path,
) -> None:
    harness = _prepare_control_model(control(), store=ArtifactStore(tmp_path))
    with pytest.raises(_LeakageMismatchError):
        _assert_forward_safe(lambda values: _run_model(harness, values))


def test_row_locality_rig_catches_carried_state(
    tmp_path: Path,
) -> None:
    harness = _prepare_control_model(
        CarriedStateAccumulatorControl(),
        store=ArtifactStore(tmp_path),
    )
    _assert_forward_safe(lambda values: _run_model(harness, values))
    with pytest.raises(_LeakageMismatchError):
        _assert_row_local(lambda values: _run_model(harness, values))


def test_representation_rigs_catch_evaluation_range_fitting(
    tmp_path: Path,
) -> None:
    control = EvaluationRangeFitRepresentationControl()
    train = _features(_training_values(), start_key=100)
    state = _fit_representation_state(
        control,
        features=train,
        store=ArtifactStore(tmp_path),
    )
    with pytest.raises(_LeakageMismatchError):
        _assert_forward_safe(lambda values: _run_representation(control, state, values))
    with pytest.raises(_LeakageMismatchError):
        _assert_row_local(lambda values: _run_representation(control, state, values))


def test_fit_boundary_catches_a_model_attempting_its_own_forward_read(
    tmp_path: Path,
) -> None:
    probe = _ForwardSourceProbe()
    control = SelfForwardReadFitModelControl(probe)
    with pytest.raises(_ForbiddenForwardReadError, match="own forward read"):
        _prepare_control_model(control, store=ArtifactStore(tmp_path))
    assert probe.calls == 1
