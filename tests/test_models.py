"""Slice-3C reference model, representation, and fit-orchestration goldens."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import numpy as np
import pytest

from trading_research.artifacts import ArtifactStore, MaterializationPolicy, PlatformIdentity
from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    CostHint,
    DatasetRef,
    FeatureOrigin,
    FittedState,
    GapPolicy,
    ModelInputs,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewRole,
    ViewSpan,
    WindowSpec,
)
from trading_research.core.feature_frames import FeatureFrame, _restore_feature_frame
from trading_research.core.frames import _make_market_frame
from trading_research.core.research_frames import (
    TrainingTargetFrame,
    _make_training_target_frame,
)
from trading_research.data.gating import SplitPolicyIdentity
from trading_research.data.views import (
    BoundedSegment,
    BoundedView,
    ContextResolution,
    ViewUnion,
    ViewUnionMember,
)
from trading_research.experiments.fitting import (
    encode_representation,
    fit_model,
    fit_representation,
    infer_model,
    view_span_from_bounded_view,
)
from trading_research.experiments.folds import _no_fold_fit_isolation
from trading_research.models import RESEARCH_MODELS
from trading_research.models.reference import (
    _GSL_FEATURES,
    _MC_FEATURES,
    _ULS_VALUE_FEATURES,
    GapSensitiveGateV1,
    MultiConfluenceGateV1,
    ReferenceLibraryModel,
    StatelessRuleModel,
    SupervisedMeanModel,
    UlsPushGateV1,
    ZoneEventGateV1,
)
from trading_research.representations import REPRESENTATION_MODELS
from trading_research.representations.reference import TrailingStandardizer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

DOMAIN = ClockDomain("test.native")
REF = DatasetRef("TRAIN_TEST", "1" * 64, ViewRole.TRAIN)
ORIGIN = FeatureOrigin("log_return_1", 1, {}, WindowSpec(1, GapPolicy.CROSS))
ZONE_EVENT_ORIGIN = FeatureOrigin(
    "zone_trader_v13",
    1,
    {},
    WindowSpec(26_534, GapPolicy.CROSS),
)
SPLIT = SplitPolicyIdentity("ADR-0012", "v1", "2" * 64)
PLATFORM = PlatformIdentity("3" * 40, False, None)
DEPENDENCY = "4" * 64
ISOLATION = _no_fold_fit_isolation()


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _view(keys: Sequence[int], *, source: DatasetRef = REF) -> ViewSpan:
    return ViewSpan(
        (source.canonical_identity,),
        TimeUnit.SECONDS,
        DOMAIN,
        _key(keys[0]),
        _key(keys[0]),
        _key(keys[-1]),
    )


def _features(
    values: Sequence[float],
    *,
    keys: Sequence[int] | None = None,
    extra: bool = False,
    feature_name: str = "log_return_1",
) -> FeatureFrame:
    actual_keys = tuple(range(10, 10 + len(values))) if keys is None else tuple(keys)
    columns = {feature_name: np.asarray(values, dtype=np.float64)}
    origins = {
        feature_name: ZONE_EVENT_ORIGIN if feature_name == "zt_event_signed" else ORIGIN
    }
    if extra:
        columns["undeclared"] = np.zeros(len(values), dtype=np.float64)
        origins["undeclared"] = FeatureOrigin(
            "extra", 1, {}, WindowSpec(0, GapPolicy.CROSS)
        )
    return _restore_feature_frame(
        keys=np.asarray(actual_keys, dtype=np.int64),
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(REF.canonical_identity,),
        columns=columns,
        origins=origins,
    )


def _targets(
    values: Sequence[float],
    *,
    keys: Sequence[int] | None = None,
    horizon: int = 1,
) -> TrainingTargetFrame:
    actual_keys = tuple(range(10, 10 + len(values))) if keys is None else tuple(keys)
    view = _view(actual_keys)
    origin = StageOrigin(
        "forward_log_return_v1",
        1,
        {
            "boundary_policy": "nan",
            "decision_delay_rows": 1,
            "horizon_rows": horizon,
            "reference_price_column": "close",
        },
    )
    return _make_training_target_frame(
        view=view,
        keys=np.asarray(actual_keys, dtype=np.int64),
        columns={"target.forward_log_return": np.asarray(values, dtype=np.float64)},
        origins={"target.forward_log_return": origin},
    )


def _uls_features(events: Sequence[float]) -> FeatureFrame:
    values = np.asarray(events, dtype=np.float64)
    keys = np.arange(10, 10 + values.size, dtype=np.int64)
    origin = FeatureOrigin("uls_push_v7", 1, {}, WindowSpec(5_734, GapPolicy.CROSS))
    columns = {"up_event_signed": values}
    for index, name in enumerate(_ULS_VALUE_FEATURES):
        columns[name] = values + float(index + 1)
    return _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(REF.canonical_identity,),
        columns=columns,
        origins=dict.fromkeys(columns, origin),
    )


def _mc_features(events: Sequence[float]) -> FeatureFrame:
    values = np.asarray(events, dtype=np.float64)
    keys = np.arange(10, 10 + values.size, dtype=np.int64)
    origin = FeatureOrigin(
        "multi_confluence_m15_v1",
        1,
        {},
        WindowSpec(645_120, GapPolicy.CROSS),
    )
    columns = {
        _MC_FEATURES[0]: values,
        _MC_FEATURES[1]: np.where(values > 0.0, 4.0, 1.0),
        _MC_FEATURES[2]: np.where(values < 0.0, 4.0, 1.0),
    }
    return _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(REF.canonical_identity,),
        columns=columns,
        origins=dict.fromkeys(columns, origin),
    )


def _gsl_features(events: Sequence[float]) -> FeatureFrame:
    values = np.asarray(events, dtype=np.float64)
    keys = np.arange(10, 10 + values.size, dtype=np.int64)
    origin = FeatureOrigin(
        "gap_sensitive_levels_h1_v1",
        1,
        {},
        WindowSpec(120_780, GapPolicy.CROSS),
    )
    columns = {
        _GSL_FEATURES[0]: values,
        _GSL_FEATURES[1]: np.where(values > 0.0, 2.0, 1.0),
        _GSL_FEATURES[2]: np.where(values < 0.0, 2.0, 1.0),
        _GSL_FEATURES[3]: np.zeros(values.size, dtype=np.float64),
    }
    return _restore_feature_frame(
        keys=keys,
        unit=TimeUnit.SECONDS,
        clock_domain=DOMAIN,
        source_identities=(REF.canonical_identity,),
        columns=columns,
        origins=dict.fromkeys(columns, origin),
    )


def _fit_rep(
    representation: object,
    features: FeatureFrame,
    store: ArtifactStore,
) -> FittedState:
    return fit_representation(
        representation,
        view=_view(features.keys.tolist()),
        features=features,
        split_policy_identity=SPLIT,
        fit_isolation=ISOLATION,
        platform_identity=PLATFORM,
        dependency_lock_sha256=DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )


def _fit_model(
    model: object,
    *,
    inputs: ModelInputs,
    targets: TrainingTargetFrame | None,
    representation_state: FittedState | None,
    store: ArtifactStore,
    view_union: ViewUnion | None = None,
) -> FittedState:
    return fit_model(
        model,
        view=_view(inputs.features.keys.tolist()),
        inputs=inputs,
        training_targets=targets,
        representation_state=representation_state,
        split_policy_identity=SPLIT,
        fit_isolation=ISOLATION,
        platform_identity=PLATFORM,
        dependency_lock_sha256=DEPENDENCY,
        store=store,
        view_union=view_union,
        policy=MaterializationPolicy.EPHEMERAL,
    )


def test_registries_contain_reference_models_and_one_representation() -> None:
    assert RESEARCH_MODELS.names() == {
        "close_position_score_v1",
        "gap_sensitive_gate_v1",
        "multi_confluence_gate_v1",
        "reference_library_v1",
        "stateless_rule_v1",
        "supervised_mean_v1",
        "uls_push_gate_v1",
        "zone_event_gate_v1",
    }
    assert REPRESENTATION_MODELS.names() == {"trailing_standardizer_v1"}
    for name in RESEARCH_MODELS.names():
        model = RESEARCH_MODELS.create(name)
        assert not inspect.signature(type(model)).parameters
    representation = REPRESENTATION_MODELS.create("trailing_standardizer_v1")
    assert not inspect.signature(type(representation)).parameters


def test_noop_fit_and_training_targets_none_are_first_class(tmp_path: Path) -> None:
    features = _features([-1.0, 0.0, 1.0])
    model = StatelessRuleModel()
    state = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=None,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=state,
        inputs=ModelInputs(features),
    )

    assert state.payload.array_names == ()
    np.testing.assert_array_equal(
        output.column("score"), np.tanh(np.array([-1.0, 0.0, 1.0]))
    )
    with pytest.raises(ContractViolationError, match="does not accept training targets"):
        _fit_model(
            model,
            inputs=ModelInputs(features),
            targets=_targets([0.1, 0.2, 0.3]),
            representation_state=None,
            store=ArtifactStore(tmp_path),
        )


def test_zone_event_gate_has_exact_declaration_mapping_and_target_refusal(
    tmp_path: Path,
) -> None:
    features = _features(
        [-1.0, 0.0, 1.0, np.nan, 2.0],
        feature_name="zt_event_signed",
    )
    model = ZoneEventGateV1()
    assert (
        model.model_id,
        model.version,
        model.params,
        model.required_feature_names,
        model.required_representation_names,
        model.output_column_names,
        model.cost_hint,
        model.comparison_tolerance,
    ) == (
        "zone_event_gate_v1",
        1,
        {},
        ("zt_event_signed",),
        (),
        ("score",),
        CostHint.CHEAP,
        None,
    )
    state = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=None,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=state,
        inputs=ModelInputs(features),
    )

    assert state.payload.array_names == ()
    np.testing.assert_array_equal(
        output.column("score"),
        np.array([-1.0, np.nan, 1.0, np.nan, np.nan], dtype=np.float64),
    )
    with pytest.raises(ContractViolationError, match="does not accept training targets"):
        _fit_model(
            model,
            inputs=ModelInputs(features),
            targets=_targets([0.1, 0.2, 0.3, 0.4, 0.5]),
            representation_state=None,
            store=ArtifactStore(tmp_path),
        )


def test_uls_push_gate_is_sparse_exact_and_uses_only_generic_outputs(
    tmp_path: Path,
) -> None:
    features = _uls_features([-1.0, 0.0, 1.0, np.nan])
    model = UlsPushGateV1()
    assert model.required_feature_names == ("up_event_signed", *_ULS_VALUE_FEATURES)
    assert model.output_column_names == (
        "score",
        *(f"value_{index}" for index in range(len(_ULS_VALUE_FEATURES))),
    )
    state = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=None,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=state,
        inputs=ModelInputs(features),
    )
    np.testing.assert_array_equal(output.keys, np.array([10, 12], dtype=np.int64))
    np.testing.assert_array_equal(output.column("score"), np.array([-1.0, 1.0]))
    for index, feature_name in enumerate(_ULS_VALUE_FEATURES):
        np.testing.assert_array_equal(
            output.column(f"value_{index}"),
            features.column(feature_name)[[0, 2]],
        )
    assert all(
        word not in name
        for name in output.column_names
        for word in ("buy", "sell", "direction", "stop", "target", "signal")
    )


def test_multi_confluence_gate_is_sparse_exact_and_refuses_targets(
    tmp_path: Path,
) -> None:
    features = _mc_features([-1.0, 0.0, 1.0, np.nan])
    model = MultiConfluenceGateV1()
    assert (
        model.model_id,
        model.version,
        model.params,
        model.required_feature_names,
        model.required_representation_names,
        model.output_column_names,
        model.cost_hint,
        model.comparison_tolerance,
    ) == (
        "multi_confluence_gate_v1",
        1,
        {},
        _MC_FEATURES,
        (),
        ("score",),
        CostHint.CHEAP,
        None,
    )
    state = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=None,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=state,
        inputs=ModelInputs(features),
    )

    np.testing.assert_array_equal(output.keys, np.array([10, 12], dtype=np.int64))
    np.testing.assert_array_equal(output.column("score"), np.array([-1.0, 1.0]))
    with pytest.raises(ContractViolationError, match="does not accept training targets"):
        _fit_model(
            model,
            inputs=ModelInputs(features),
            targets=_targets([0.1, 0.2, 0.3, 0.4]),
            representation_state=None,
            store=ArtifactStore(tmp_path),
        )


def test_gap_sensitive_gate_is_sparse_exact_and_refuses_targets(
    tmp_path: Path,
) -> None:
    features = _gsl_features([-1.0, 0.0, 1.0, np.nan])
    model = GapSensitiveGateV1()
    assert (
        model.model_id,
        model.version,
        model.params,
        model.required_feature_names,
        model.required_representation_names,
        model.output_column_names,
        model.cost_hint,
        model.comparison_tolerance,
    ) == (
        "gap_sensitive_gate_v1",
        1,
        {},
        _GSL_FEATURES,
        (),
        ("score",),
        CostHint.CHEAP,
        None,
    )
    state = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=None,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=state,
        inputs=ModelInputs(features),
    )

    np.testing.assert_array_equal(output.keys, np.array([10, 12], dtype=np.int64))
    np.testing.assert_array_equal(output.column("score"), np.array([-1.0, 1.0]))
    with pytest.raises(ContractViolationError, match="does not accept training targets"):
        _fit_model(
            model,
            inputs=ModelInputs(features),
            targets=_targets([0.1, 0.2, 0.3, 0.4]),
            representation_state=None,
            store=ArtifactStore(tmp_path),
        )


def test_supervised_target_channel_and_target_definition_enter_identity(tmp_path: Path) -> None:
    features = _features([1.0, 2.0, np.nan, 4.0])
    model = SupervisedMeanModel()
    first_targets = _targets([0.1, 0.3, 99.0, np.nan], horizon=1)
    second_targets = _targets([0.1, 0.3, 99.0, np.nan], horizon=2)
    first = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=first_targets,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )
    second = _fit_model(
        model,
        inputs=ModelInputs(features),
        targets=second_targets,
        representation_state=None,
        store=ArtifactStore(tmp_path),
    )

    assert first.payload.array("target_mean").tolist() == [0.2]
    assert first.identity_key() != second.identity_key()
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=first,
        inputs=ModelInputs(features),
    )
    assert np.array_equal(
        output.column("score"),
        np.array([0.2, 0.2, np.nan, 0.2]),
        equal_nan=True,
    )


def test_representation_is_fitted_only_on_fit_view_and_encodes_row_locally(
    tmp_path: Path,
) -> None:
    train = _features([1.0, 2.0, 3.0, 4.0])
    evaluation = _features([2.5, 1000.0], keys=(20, 21))
    representation = TrailingStandardizer()
    state = _fit_rep(representation, train, ArtifactStore(tmp_path))
    encoded = encode_representation(
        representation,
        view=_view((20, 21)),
        fitted_state=state,
        features=evaluation,
    )

    assert state.payload.array("location").tolist() == [2.5]
    assert state.payload.array("scale").tolist() == [float(np.std([1.0, 2.0, 3.0, 4.0]))]
    assert encoded.column("standardized_log_return_1")[0] == 0.0
    perturbed = _features([2.5, -1000.0], keys=(20, 21))
    repeated = encode_representation(
        representation,
        view=_view((20, 21)),
        fitted_state=state,
        features=perturbed,
    )
    assert repeated.column("standardized_log_return_1")[0] == 0.0


def test_reference_library_carries_training_state_and_queries_fixed_rows(
    tmp_path: Path,
) -> None:
    features = _features([-1.0, 0.0, 1.0])
    store = ArtifactStore(tmp_path)
    representation = TrailingStandardizer()
    representation_state = _fit_rep(representation, features, store)
    encoded = encode_representation(
        representation,
        view=_view(features.keys.tolist()),
        fitted_state=representation_state,
        features=features,
    )
    inputs = ModelInputs(features, encoded)
    model = ReferenceLibraryModel()
    state = _fit_model(
        model,
        inputs=inputs,
        targets=_targets([-0.5, 0.0, 0.75]),
        representation_state=representation_state,
        store=store,
    )
    output = infer_model(
        model,
        view=_view(features.keys.tolist()),
        fitted_state=state,
        inputs=inputs,
    )

    assert state.payload.array_names == ("library_inputs", "library_targets")
    np.testing.assert_array_equal(output.column("score"), [-0.5, 0.0, 0.75])


def test_declarations_refuse_extra_columns_and_missing_representation_before_compute(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ContractViolationError, match="exactly equal"):
        _fit_model(
            StatelessRuleModel(),
            inputs=ModelInputs(_features([1.0, 2.0], extra=True)),
            targets=None,
            representation_state=None,
            store=store,
        )
    with pytest.raises(ContractViolationError, match="requires a RepresentationFrame"):
        _fit_model(
            ReferenceLibraryModel(),
            inputs=ModelInputs(_features([1.0, 2.0])),
            targets=_targets([0.1, 0.2]),
            representation_state=None,
            store=store,
        )


def test_view_union_is_consumed_only_as_promotion_identity_evidence(tmp_path: Path) -> None:
    member = object.__new__(ViewUnionMember)
    object.__setattr__(member, "_dataset_ref", REF)
    object.__setattr__(member, "_split_policy_identity", SPLIT)
    union = ViewUnion(members=(member,))
    features = _features([1.0, 2.0])

    state = _fit_model(
        StatelessRuleModel(),
        inputs=ModelInputs(features),
        targets=None,
        representation_state=None,
        store=ArtifactStore(tmp_path),
        view_union=union,
    )
    assert len(state.identity_key()) == 64

    other_ref = DatasetRef("TRAIN_OTHER", "5" * 64, ViewRole.TRAIN)
    other_member = object.__new__(ViewUnionMember)
    object.__setattr__(other_member, "_dataset_ref", other_ref)
    object.__setattr__(other_member, "_split_policy_identity", SPLIT)
    with pytest.raises(ContractViolationError, match="member identities"):
        _fit_model(
            StatelessRuleModel(),
            inputs=ModelInputs(features),
            targets=None,
            representation_state=None,
            store=ArtifactStore(tmp_path),
            view_union=ViewUnion(members=(other_member,)),
        )

    wrong_policy_member = object.__new__(ViewUnionMember)
    object.__setattr__(wrong_policy_member, "_dataset_ref", REF)
    object.__setattr__(
        wrong_policy_member,
        "_split_policy_identity",
        SplitPolicyIdentity("ADR-0012", "v2", "6" * 64),
    )
    with pytest.raises(ContractViolationError, match="split-policy identity"):
        _fit_model(
            StatelessRuleModel(),
            inputs=ModelInputs(features),
            targets=None,
            representation_state=None,
            store=ArtifactStore(tmp_path),
            view_union=ViewUnion(members=(wrong_policy_member,)),
        )


def test_bounded_view_adapter_emits_only_a_rowless_view_span() -> None:
    frame = _make_market_frame(
        dataset_ref=REF,
        clock_domain=DOMAIN,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=np.array([10, 11], dtype=np.int64),
        columns={"close": np.array([100.0, 101.0])},
        admitted_lo=10,
        admitted_hi=11,
    )
    context = ContextResolution._resolved(
        requested_context_start=_key(10),
        resolved_context_start=_key(10),
        shortfall=None,
    )
    bounded = BoundedView._from_bounded(
        segments=(BoundedSegment._from_bounded(frame),),
        eval_frame=frame,
        context=context,
        context_start=_key(10),
        eval_start=_key(10),
        eval_end=_key(11),
    )

    span = view_span_from_bounded_view(bounded)
    assert span.source_identities == (REF.canonical_identity,)
    assert span.context_start == _key(10)
    assert not hasattr(span, "rows")
    assert not hasattr(span, "frame")
