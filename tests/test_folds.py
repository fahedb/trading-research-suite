"""Slice-3E rolling-fold, derived-purge, embargo, and containment tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np
import pytest

from trading_research.artifacts import ArtifactStore, MaterializationPolicy, PlatformIdentity
from trading_research.core import (
    BoundaryPolicy,
    ClockDomain,
    ContractViolationError,
    DatasetRef,
    FeatureOrigin,
    GapPolicy,
    ModelInputs,
    NativeTimeKey,
    TimeUnit,
    ViewRole,
    WindowSpec,
)
from trading_research.core.feature_frames import FeatureFrame, _restore_feature_frame
from trading_research.core.frames import _make_market_frame
from trading_research.data.gating import SplitPolicyIdentity
from trading_research.data.views import (
    BoundedSegment,
    BoundedView,
    ContextResolution,
)
from trading_research.experiments.fitting import fit_model, fit_representation
from trading_research.experiments.folds import (
    _FoldPlan,
    _generate_walk_forward_folds,
    _PurgeOverride,
    _PurgeRecord,
)
from trading_research.models.reference import StatelessRuleModel
from trading_research.outcomes.reference import ForwardLogReturn
from trading_research.representations.reference import TrailingStandardizer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import numpy.typing as npt

    from trading_research.core import OutcomeRequest, ParamValue, ToleranceSpec

_DOMAIN = ClockDomain("fold-rig.native")
_REF = DatasetRef("FOLD_RIG_TRAIN", "1" * 64, ViewRole.TRAIN)
_SPLIT = SplitPolicyIdentity("ADR-0012", "v1", "2" * 64)
_PLATFORM = PlatformIdentity("3" * 40, False, None)
_DEPENDENCY = "4" * 64


class _FoldLeakageError(AssertionError):
    """A training position carries undeclared information into evaluation."""


class _FoldLike(Protocol):
    @property
    def _eval_position_range(self) -> tuple[int, int]: ...

    @property
    def _purge(self) -> _PurgeRecord: ...

    @property
    def _effective_fit_positions(self) -> tuple[int, ...]: ...


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, _DOMAIN)


def _axis(rows: int) -> npt.NDArray[np.int64]:
    positions = np.arange(rows, dtype=np.int64)
    keys = 100 + positions * 10
    keys[rows // 2 :] += 1_000
    return keys


def _bounded_view(rows: int, *, prior_rows: int = 0) -> BoundedView:
    keys = _axis(rows)
    prior_keys = np.arange(
        int(keys[0]) - prior_rows * 10,
        int(keys[0]),
        10,
        dtype=np.int64,
    )
    segment_keys = np.concatenate((prior_keys, keys))
    segment = _make_market_frame(
        dataset_ref=_REF,
        clock_domain=_DOMAIN,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=segment_keys,
        columns={
            "close": np.linspace(90.0, 200.0, segment_keys.size, dtype=np.float64)
        },
        admitted_lo=int(segment_keys[0]),
        admitted_hi=int(keys[-1]),
    )
    frame = _make_market_frame(
        dataset_ref=_REF,
        clock_domain=_DOMAIN,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=keys,
        columns={"close": np.linspace(100.0, 200.0, rows, dtype=np.float64)},
        admitted_lo=int(segment_keys[0]),
        admitted_hi=int(keys[-1]),
    )
    context_start = _key(int(segment_keys[0]) if prior_rows else 50)
    context = ContextResolution._resolved(
        requested_context_start=context_start,
        resolved_context_start=context_start,
        shortfall=None,
    )
    return BoundedView._from_bounded(
        segments=(BoundedSegment._from_bounded(segment),),
        eval_frame=frame,
        context=context,
        context_start=context_start,
        eval_start=_key(int(keys[0])),
        eval_end=_key(int(keys[-1])),
    )


def _features(
    view: BoundedView,
    *,
    max_prior_rows: int,
    keys: npt.NDArray[np.int64] | None = None,
) -> FeatureFrame:
    actual_keys = view.eval_frame.keys if keys is None else keys
    positions = np.arange(actual_keys.size, dtype=np.float64)
    return _restore_feature_frame(
        keys=actual_keys,
        unit=TimeUnit.SECONDS,
        clock_domain=_DOMAIN,
        source_identities=(_REF.canonical_identity,),
        columns={"log_return_1": (positions / 10.0).astype(np.float64)},
        origins={
            "log_return_1": FeatureOrigin(
                "log_return_1",
                1,
                {},
                WindowSpec(max_prior_rows, GapPolicy.CROSS),
            )
        },
    )


def _spec(horizon: int) -> ForwardLogReturn:
    return ForwardLogReturn(
        horizon_rows=horizon,
        decision_delay_rows=1,
        reference_price_column="close",
        boundary_policy=BoundaryPolicy.NAN,
    )


def _folds(
    *,
    rows: int = 30,
    fit_rows: int = 10,
    eval_rows: int = 5,
    step_rows: int = 5,
    embargo_rows: int = 0,
    horizon: int = 1,
    max_prior_rows: int = 3,
    override: _PurgeOverride | None = None,
) -> tuple[_FoldPlan, ...]:
    view = _bounded_view(rows)
    return _generate_walk_forward_folds(
        view=view,
        features=_features(view, max_prior_rows=max_prior_rows),
        outcome_specs=(_spec(horizon),),
        fit_rows=fit_rows,
        eval_rows=eval_rows,
        step_rows=step_rows,
        embargo_rows=embargo_rows,
        purge_override=override,
    )


def _subset_features(
    frame: FeatureFrame,
    positions: Sequence[int],
) -> FeatureFrame:
    selected = np.asarray(tuple(positions), dtype=np.int64)
    return _restore_feature_frame(
        keys=frame.keys[selected],
        unit=frame.unit,
        clock_domain=frame.clock_domain,
        source_identities=frame.source_identities,
        columns={name: frame.column(name)[selected] for name in frame.column_names},
        origins={name: frame.origin(name) for name in frame.column_names},
    )


def _maximum_forward_rows(fold: _FoldLike) -> int:
    reaches = fold._purge._outcome_reaches
    return max((reach._forward_rows for reach in reaches), default=0)


def _assert_no_undeclared_overlap(fold: _FoldLike) -> None:
    eval_start = fold._eval_position_range[0]
    forward_rows = _maximum_forward_rows(fold)
    permitted = fold._purge._permitted_overlap_range
    for position in fold._effective_fit_positions:
        if position + forward_rows < eval_start:
            continue
        if permitted is None or not permitted[0] <= position < permitted[1]:
            raise _FoldLeakageError(
                f"fit position {position} overlaps evaluation at {eval_start}"
            )


class PurgeDisabledFoldControl:
    """Test-only fold that silently restores every purged fit position."""

    def __init__(self, source: _FoldPlan) -> None:
        self._eval_position_range = source._eval_position_range
        self._purge = source._purge
        self._effective_fit_positions = tuple(range(*source._fit_position_range))


class OverbroadOverrideFoldControl:
    """Test-only fold retaining one overlap position beyond its declaration."""

    def __init__(self, source: _FoldPlan) -> None:
        self._eval_position_range = source._eval_position_range
        self._purge = source._purge
        assert source._purge._permitted_overlap_range is not None
        forbidden = source._purge._permitted_overlap_range[1]
        self._effective_fit_positions = (
            *source._effective_fit_positions,
            forbidden,
        )


def test_fixed_rolling_geometry_is_half_open_chronological_and_never_truncated() -> None:
    folds = _folds(rows=25, fit_rows=8, eval_rows=4, step_rows=5)

    assert tuple(fold._index for fold in folds) == (0, 1, 2)
    assert tuple(fold._fit_position_range for fold in folds) == (
        (0, 8),
        (5, 13),
        (10, 18),
    )
    assert tuple(fold._eval_position_range for fold in folds) == (
        (8, 12),
        (13, 17),
        (18, 22),
    )
    keys = _axis(25)
    assert folds[1]._fit_key_bounds._first.value == int(keys[5])
    assert folds[1]._fit_key_bounds._last.value == int(keys[12])
    assert folds[1]._eval_key_bounds._first.value == int(keys[13])
    assert folds[1]._eval_key_bounds._last.value == int(keys[16])
    assert all(fold._eval_position_range[1] <= 25 for fold in folds)


@pytest.mark.parametrize("horizon", [1, 3, 7])
@pytest.mark.parametrize("max_prior_rows", [0, 2, 9])
def test_purge_is_derived_from_outcome_reach_not_feature_reach(
    horizon: int,
    max_prior_rows: int,
) -> None:
    fold = _folds(
        rows=35,
        fit_rows=15,
        eval_rows=5,
        step_rows=20,
        horizon=horizon,
        max_prior_rows=max_prior_rows,
    )[0]

    expected = min(15, 1 + horizon)
    assert fold._purge._derived_rows == expected
    assert fold._purge._applied_rows == expected
    assert fold._purge._outcome_reaches[0]._decision_delay_rows == 1
    assert fold._purge._outcome_reaches[0]._horizon_rows == horizon
    assert fold._max_feature_prior_rows == max_prior_rows
    assert fold._effective_fit_size == 15 - expected
    _assert_no_undeclared_overlap(fold)


def test_horizon_change_legitimately_changes_the_usable_fit_set() -> None:
    short = _folds(horizon=1)[0]
    long = _folds(horizon=5)[0]

    assert short._purge._derived_rows == 2
    assert long._purge._derived_rows == 6
    assert short._effective_fit_positions != long._effective_fit_positions
    assert short._effective_fit_size == 8
    assert long._effective_fit_size == 4


def test_maximum_reach_is_taken_over_every_training_outcome_spec() -> None:
    view = _bounded_view(30)
    folds = _generate_walk_forward_folds(
        view=view,
        features=_features(view, max_prior_rows=4),
        outcome_specs=(_spec(1), _spec(5), _spec(2)),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )

    first = folds[0]
    assert tuple(
        reach._forward_rows for reach in first._purge._outcome_reaches
    ) == (2, 6, 3)
    assert first._purge._derived_rows == 6
    assert first._effective_fit_positions == (0, 1, 2, 3)


def test_target_free_fit_records_zero_derived_purge_explicitly() -> None:
    view = _bounded_view(30)
    folds = _generate_walk_forward_folds(
        view=view,
        features=_features(view, max_prior_rows=2),
        outcome_specs=(),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )

    first = folds[0]
    assert first._purge._outcome_reaches == ()
    assert first._purge._derived_rows == 0
    assert first._purge._applied_rows == 0
    assert first._effective_fit_positions == tuple(range(10))
    params = first._fit_isolation._identity_params()
    assert params["derived_forward_rows"] == 0
    assert params["applied_purge_rows"] == 0


def test_embargo_is_applied_after_purge_and_recorded_on_every_fold() -> None:
    folds = _folds(embargo_rows=3)

    assert all(fold._embargo._configured_rows == 3 for fold in folds)
    assert folds[0]._embargo._declared_position_range == (15, 18)
    assert folds[1]._embargo._declared_position_range == (20, 23)
    assert folds[2]._purge._applied_position_range == (18, 20)
    assert folds[2]._embargo._applied_position_ranges == ((15, 18),)
    assert folds[2]._effective_fit_positions == (10, 11, 12, 13, 14)
    assert folds[2]._embargo._excluded_rows_after_purge == 3


def test_zero_embargo_is_still_explicitly_recorded_on_every_fold() -> None:
    folds = _folds(embargo_rows=0)
    assert all(fold._embargo._configured_rows == 0 for fold in folds)
    assert all(
        fold._embargo._declared_position_range
        == (fold._eval_position_range[1], fold._eval_position_range[1])
        for fold in folds
    )
    assert all(fold._embargo._applied_position_ranges == () for fold in folds)


def test_structured_override_permits_only_its_exact_recorded_overlap() -> None:
    fold = _folds(
        horizon=3,
        override=_PurgeOverride(_purge_rows=2, _reason="bounded comparison"),
    )[0]

    assert fold._purge._derived_rows == 4
    assert fold._purge._applied_rows == 2
    assert fold._purge._derived_position_range == (6, 10)
    assert fold._purge._applied_position_range == (8, 10)
    assert fold._purge._permitted_overlap_range == (6, 8)
    assert fold._purge._override is not None
    assert fold._purge._override._reason == "bounded comparison"
    assert fold._effective_fit_positions[-2:] == (6, 7)
    _assert_no_undeclared_overlap(fold)

    with pytest.raises(_FoldLeakageError):
        _assert_no_undeclared_overlap(OverbroadOverrideFoldControl(fold))


@pytest.mark.parametrize("purge_rows", [4, 5])
def test_override_must_strictly_reduce_the_derived_purge(purge_rows: int) -> None:
    with pytest.raises(ContractViolationError, match="strictly reduce"):
        _folds(
            horizon=3,
            override=_PurgeOverride(_purge_rows=purge_rows, _reason="invalid"),
        )


def test_override_requires_a_nonempty_reason_and_nonnegative_exact_rows() -> None:
    with pytest.raises(ContractViolationError, match="reason"):
        _PurgeOverride(_purge_rows=0, _reason="  ")
    for value in (-1, True, 1.0):
        with pytest.raises(ContractViolationError, match="non-negative"):
            _PurgeOverride(_purge_rows=value, _reason="invalid")  # type: ignore[arg-type]


def test_no_complete_fold_and_an_empty_effective_fit_set_both_refuse() -> None:
    with pytest.raises(ContractViolationError, match="produces no complete"):
        _folds(rows=10, fit_rows=8, eval_rows=3)
    with pytest.raises(ContractViolationError, match="empty effective fit set"):
        _folds(rows=10, fit_rows=2, eval_rows=4, horizon=2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fit_rows", 0),
        ("eval_rows", -1),
        ("step_rows", True),
        ("embargo_rows", -1),
    ],
)
def test_geometry_parameters_are_explicit_exact_integers(
    field: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "fit_rows": 10,
        "eval_rows": 5,
        "step_rows": 5,
        "embargo_rows": 0,
    }
    arguments[field] = value
    view = _bounded_view(30)
    with pytest.raises(ContractViolationError):
        _generate_walk_forward_folds(
            view=view,
            features=_features(view, max_prior_rows=1),
            outcome_specs=(_spec(1),),
            fit_rows=arguments["fit_rows"],  # type: ignore[arg-type]
            eval_rows=arguments["eval_rows"],  # type: ignore[arg-type]
            step_rows=arguments["step_rows"],  # type: ignore[arg-type]
            embargo_rows=arguments["embargo_rows"],  # type: ignore[arg-type]
            purge_override=None,
        )


def test_fold_axis_must_equal_one_bounded_tier_one_view() -> None:
    view = _bounded_view(30)
    shortened = _features(
        view,
        max_prior_rows=1,
        keys=view.eval_frame.keys[:-1],
    )
    with pytest.raises(ContractViolationError, match="exactly equal"):
        _generate_walk_forward_folds(
            view=view,
            features=shortened,
            outcome_specs=(_spec(1),),
            fit_rows=10,
            eval_rows=5,
            step_rows=5,
            embargo_rows=0,
            purge_override=None,
        )

    wrong_source = _restore_feature_frame(
        keys=view.eval_frame.keys,
        unit=TimeUnit.SECONDS,
        clock_domain=_DOMAIN,
        source_identities=(
            DatasetRef("OTHER_TRAIN", "5" * 64, ViewRole.TRAIN).canonical_identity,
        ),
        columns={"log_return_1": np.zeros(30, dtype=np.float64)},
        origins={
            "log_return_1": FeatureOrigin(
                "log_return_1",
                1,
                {},
                WindowSpec(1, GapPolicy.CROSS),
            )
        },
    )
    with pytest.raises(ContractViolationError, match="Tier-1 evaluation axis"):
        _generate_walk_forward_folds(
            view=view,
            features=wrong_source,
            outcome_specs=(_spec(1),),
            fit_rows=10,
            eval_rows=5,
            step_rows=5,
            embargo_rows=0,
            purge_override=None,
        )


def test_fold_axis_accepts_legal_bounds_between_observed_keys() -> None:
    original = _bounded_view(30)
    enclosed = BoundedView._from_bounded(
        segments=original.segments,
        eval_frame=original.eval_frame,
        context=original.context,
        context_start=original.context_start,
        eval_start=_key(int(original.eval_frame.keys[0]) - 1),
        eval_end=_key(int(original.eval_frame.keys[-1]) + 1),
    )

    folds = _generate_walk_forward_folds(
        view=enclosed,
        features=_features(enclosed, max_prior_rows=1),
        outcome_specs=(_spec(1),),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )
    assert folds[0]._fit_position_range == (0, 10)


def test_feature_reach_composes_context_without_carried_state() -> None:
    fold = _folds(max_prior_rows=3)[1]
    keys = _axis(30)

    assert fold._training_view.context_start.value == int(keys[2])
    assert fold._training_view.eval_start.value == int(keys[5])
    assert fold._training_view.eval_end.value == int(keys[12])
    assert fold._evaluation_view.context_start.value == int(keys[12])
    assert fold._evaluation_view.eval_start.value == int(keys[15])
    assert fold._evaluation_view.eval_end.value == int(keys[19])

    first = _folds(max_prior_rows=3)[0]
    assert first._training_view.context_start == _key(50)

    contextual_view = _bounded_view(30, prior_rows=5)
    contextual = _generate_walk_forward_folds(
        view=contextual_view,
        features=_features(contextual_view, max_prior_rows=3),
        outcome_specs=(_spec(1),),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )[0]
    assert contextual._training_view.context_start == _key(70)


def test_fit_isolation_refuses_unpurged_rows_and_enters_state_identity(
    tmp_path: Path,
) -> None:
    view = _bounded_view(30)
    features = _features(view, max_prior_rows=3)
    short = _generate_walk_forward_folds(
        view=view,
        features=features,
        outcome_specs=(_spec(1),),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )[0]
    long = _generate_walk_forward_folds(
        view=view,
        features=features,
        outcome_specs=(_spec(3),),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )[0]
    store = ArtifactStore(tmp_path)

    with pytest.raises(ContractViolationError, match="inside ViewSpan"):
        fit_model(
            StatelessRuleModel(),
            view=short._training_view,
            inputs=ModelInputs(features),
            training_targets=None,
            representation_state=None,
            split_policy_identity=_SPLIT,
            fit_isolation=short._fit_isolation,
            platform_identity=_PLATFORM,
            dependency_lock_sha256=_DEPENDENCY,
            store=store,
            policy=MaterializationPolicy.EPHEMERAL,
        )

    wrong_effective_features = _subset_features(features, (0, *range(2, 8)))
    with pytest.raises(ContractViolationError, match="effective fit set"):
        fit_model(
            StatelessRuleModel(),
            view=short._training_view,
            inputs=ModelInputs(wrong_effective_features),
            training_targets=None,
            representation_state=None,
            split_policy_identity=_SPLIT,
            fit_isolation=short._fit_isolation,
            platform_identity=_PLATFORM,
            dependency_lock_sha256=_DEPENDENCY,
            store=store,
            policy=MaterializationPolicy.EPHEMERAL,
        )

    short_features = _subset_features(features, short._effective_fit_positions)
    long_features = _subset_features(features, long._effective_fit_positions)
    short_state = fit_model(
        StatelessRuleModel(),
        view=short._training_view,
        inputs=ModelInputs(short_features),
        training_targets=None,
        representation_state=None,
        split_policy_identity=_SPLIT,
        fit_isolation=short._fit_isolation,
        platform_identity=_PLATFORM,
        dependency_lock_sha256=_DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )
    long_state = fit_model(
        StatelessRuleModel(),
        view=long._training_view,
        inputs=ModelInputs(long_features),
        training_targets=None,
        representation_state=None,
        split_policy_identity=_SPLIT,
        fit_isolation=long._fit_isolation,
        platform_identity=_PLATFORM,
        dependency_lock_sha256=_DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )

    assert short._fit_isolation._identity_params()["applied_purge_rows"] == 2
    assert long._fit_isolation._identity_params()["applied_purge_rows"] == 4
    assert short_state.identity_key() != long_state.identity_key()


def test_fit_isolation_also_guards_representation_fit(tmp_path: Path) -> None:
    view = _bounded_view(30)
    features = _features(view, max_prior_rows=3)
    fold = _generate_walk_forward_folds(
        view=view,
        features=features,
        outcome_specs=(_spec(1),),
        fit_rows=10,
        eval_rows=5,
        step_rows=5,
        embargo_rows=0,
        purge_override=None,
    )[0]
    store = ArtifactStore(tmp_path)

    with pytest.raises(ContractViolationError, match="inside ViewSpan"):
        fit_representation(
            TrailingStandardizer(),
            view=fold._training_view,
            features=features,
            split_policy_identity=_SPLIT,
            fit_isolation=fold._fit_isolation,
            platform_identity=_PLATFORM,
            dependency_lock_sha256=_DEPENDENCY,
            store=store,
            policy=MaterializationPolicy.EPHEMERAL,
        )

    fitted = fit_representation(
        TrailingStandardizer(),
        view=fold._training_view,
        features=_subset_features(features, fold._effective_fit_positions),
        split_policy_identity=_SPLIT,
        fit_isolation=fold._fit_isolation,
        platform_identity=_PLATFORM,
        dependency_lock_sha256=_DEPENDENCY,
        store=store,
        policy=MaterializationPolicy.EPHEMERAL,
    )
    assert fitted.payload.array_names == ("location", "scale")


def test_default_fold_boundary_rig_catches_purge_disabled_control() -> None:
    folds = _folds(horizon=3)
    for fold in folds:
        _assert_no_undeclared_overlap(fold)
    with pytest.raises(_FoldLeakageError):
        _assert_no_undeclared_overlap(PurgeDisabledFoldControl(folds[0]))


class _InvalidDelayOutcome:
    outcome_id = "invalid_delay"
    version = 1
    horizon_rows = 1
    decision_delay_rows = 2
    reference_price_column = "close"
    boundary_policy = BoundaryPolicy.NAN
    required_columns = ("close",)
    target_column_names = ("target.invalid",)
    comparison_tolerance: ToleranceSpec | None = None

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return {
            "boundary_policy": "nan",
            "decision_delay_rows": 2,
            "horizon_rows": 1,
            "reference_price_column": "close",
        }

    def compute(self, request: OutcomeRequest) -> dict[str, npt.NDArray[np.float64]]:
        raise AssertionError("invalid delay must refuse before computation")


def test_non_v1_decision_delay_refuses_before_fold_generation() -> None:
    view = _bounded_view(30)
    with pytest.raises(ContractViolationError, match="decision_delay_rows"):
        _generate_walk_forward_folds(
            view=view,
            features=_features(view, max_prior_rows=1),
            outcome_specs=(_InvalidDelayOutcome(),),
            fit_rows=10,
            eval_rows=5,
            step_rows=5,
            embargo_rows=0,
            purge_override=None,
        )
