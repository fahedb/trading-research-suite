"""Private rolling walk-forward geometry and derived fit isolation."""

from __future__ import annotations

import typing as _typing
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.identity import ViewRole as _ViewRole
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.time import NativeTimeKey as _NativeTimeKey
from trading_research.data.views import BoundedView as _BoundedView
from trading_research.outcomes.execution import _forward_rows, _validated_spec

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
    from trading_research.core.interfaces import OutcomeSpec as _OutcomeSpec

__all__: list[str] = []

_TIER_ONE_ROLES = frozenset(
    {
        _ViewRole.TRAIN,
        _ViewRole.VALIDATION,
        _ViewRole.OOS,
        _ViewRole.LOCKBOX,
    }
)


@_dataclass(frozen=True, slots=True)
class _PurgeOverride:
    """Explicit, recorded permission to retain a bounded amount of overlap."""

    _purge_rows: int
    _reason: str

    def __post_init__(self) -> None:
        if type(self._purge_rows) is not int or self._purge_rows < 0:
            raise _ContractViolationError(
                "purge override rows must be a non-negative Python int"
            )
        if type(self._reason) is not str or not self._reason.strip():
            raise _ContractViolationError("purge override reason must be non-empty")
        object.__setattr__(self, "_reason", self._reason.strip())


@_dataclass(frozen=True, slots=True)
class _FeatureReach:
    _column_name: str
    _provider_id: str
    _version: int
    _params_json: str
    _max_prior_rows: int
    _gap_policy: str
    _nominal_interval: int | None


@_dataclass(frozen=True, slots=True)
class _OutcomeReach:
    _outcome_id: str
    _version: int
    _params_json: str
    _decision_delay_rows: int
    _horizon_rows: int
    _forward_rows: int


@_dataclass(frozen=True, slots=True)
class _KeyBounds:
    _first: _NativeTimeKey
    _last: _NativeTimeKey


@_dataclass(frozen=True, slots=True)
class _PurgeRecord:
    _derived_rows: int
    _applied_rows: int
    _derived_position_range: tuple[int, int]
    _applied_position_range: tuple[int, int]
    _permitted_overlap_range: tuple[int, int] | None
    _outcome_reaches: tuple[_OutcomeReach, ...]
    _override: _PurgeOverride | None


@_dataclass(frozen=True, slots=True)
class _EmbargoRecord:
    _configured_rows: int
    _declared_position_range: tuple[int, int]
    _applied_position_ranges: tuple[tuple[int, int], ...]
    _excluded_rows_after_purge: int


@_dataclass(frozen=True, slots=True)
class _FitIsolation:
    """Composition-root proof binding exact fit rows to identity metadata."""

    _params: _Mapping[str, _ParamValue]
    _expected_view: _ViewSpan | None
    _expected_keys: tuple[int, ...] | None

    def __post_init__(self) -> None:
        _canonical_params_json(self._params)
        object.__setattr__(
            self,
            "_params",
            _MappingProxyType({name: self._params[name] for name in sorted(self._params)}),
        )

    def _identity_params(self) -> _Mapping[str, _ParamValue]:
        return self._params

    def _validate_fit(self, view: _ViewSpan, features: _FeatureFrame) -> None:
        if type(view) is not _ViewSpan or type(features) is not _FeatureFrame:
            raise _ContractViolationError(
                "fit isolation requires exact ViewSpan and FeatureFrame values"
            )
        if self._expected_view is None:
            if self._expected_keys is not None:
                raise AssertionError("unbounded fit isolation retained expected keys")
            return
        if view != self._expected_view:
            raise _ContractViolationError(
                "fit ViewSpan must exactly match its generated fold isolation"
            )
        expected = _np.asarray(self._expected_keys, dtype=_np.int64)
        if not _np.array_equal(features.keys, expected):
            raise _ContractViolationError(
                "fit FeatureFrame keys must exactly equal the generated effective fit set"
            )


@_dataclass(frozen=True, slots=True)
class _FoldPlan:
    _index: int
    _fit_position_range: tuple[int, int]
    _eval_position_range: tuple[int, int]
    _fit_key_bounds: _KeyBounds
    _eval_key_bounds: _KeyBounds
    _effective_fit_key_bounds: _KeyBounds
    _training_view: _ViewSpan
    _evaluation_view: _ViewSpan
    _effective_fit_positions: tuple[int, ...]
    _effective_fit_size: int
    _feature_reaches: tuple[_FeatureReach, ...]
    _max_feature_prior_rows: int
    _purge: _PurgeRecord
    _embargo: _EmbargoRecord
    _fit_isolation: _FitIsolation


def _no_fold_fit_isolation() -> _FitIsolation:
    """Return explicit identity evidence for a bounded fit with no Tier-2 fold."""
    return _FitIsolation(
        _params={
            "mode": "bounded_fit_without_tier2_fold",
            "applied_purge_rows": 0,
            "embargo_rows": 0,
            "derivation": ("no_procedural_fold",),
        },
        _expected_view=None,
        _expected_keys=None,
    )


def _generate_walk_forward_folds(
    *,
    view: _BoundedView,
    features: _FeatureFrame,
    outcome_specs: _Sequence[_OutcomeSpec],
    fit_rows: int,
    eval_rows: int,
    step_rows: int,
    embargo_rows: int,
    purge_override: _PurgeOverride | None,
) -> tuple[_FoldPlan, ...]:
    """Generate the sole V1 fixed-length rolling fold geometry."""
    keys, context_keys, eval_axis_offset = _validated_axis(
        view=view,
        features=features,
    )
    _positive_rows(fit_rows, label="fit_rows")
    _positive_rows(eval_rows, label="eval_rows")
    _positive_rows(step_rows, label="step_rows")
    if type(embargo_rows) is not int or embargo_rows < 0:
        raise _ContractViolationError("embargo_rows must be a non-negative Python int")
    if purge_override is not None and type(purge_override) is not _PurgeOverride:
        raise _ContractViolationError("purge_override must be an exact PurgeOverride or None")

    feature_reaches = _feature_reaches(features)
    max_feature_prior_rows = max(
        (reach._max_prior_rows for reach in feature_reaches),
        default=0,
    )
    outcome_reaches = _outcome_reaches(outcome_specs)
    maximum_forward_rows = max(
        (reach._forward_rows for reach in outcome_reaches),
        default=0,
    )
    derived_purge_rows = min(fit_rows, maximum_forward_rows)
    applied_purge_rows = _applied_purge_rows(
        derived=derived_purge_rows,
        override=purge_override,
    )

    plans: list[_FoldPlan] = []
    prior_embargo_ranges: list[tuple[int, int]] = []
    index = 0
    while True:
        fit_start = index * step_rows
        fit_stop = fit_start + fit_rows
        eval_start = fit_stop
        eval_stop = eval_start + eval_rows
        if eval_stop > keys.size:
            break

        derived_purge_start = fit_stop - derived_purge_rows
        applied_purge_start = fit_stop - applied_purge_rows
        after_purge = tuple(range(fit_start, applied_purge_start))
        applied_embargo_ranges = _intersections(
            prior_embargo_ranges,
            lower=fit_start,
            upper=applied_purge_start,
        )
        effective_positions = tuple(
            position
            for position in after_purge
            if not _inside_ranges(position, applied_embargo_ranges)
        )
        if not effective_positions:
            raise _ContractViolationError(
                f"fold {index} has an empty effective fit set after purge and embargo"
            )

        permitted_overlap = (
            (derived_purge_start, applied_purge_start)
            if applied_purge_rows < derived_purge_rows
            else None
        )
        purge = _PurgeRecord(
            _derived_rows=derived_purge_rows,
            _applied_rows=applied_purge_rows,
            _derived_position_range=(derived_purge_start, fit_stop),
            _applied_position_range=(applied_purge_start, fit_stop),
            _permitted_overlap_range=permitted_overlap,
            _outcome_reaches=outcome_reaches,
            _override=purge_override,
        )
        declared_embargo = (eval_stop, eval_stop + embargo_rows)
        embargo = _EmbargoRecord(
            _configured_rows=embargo_rows,
            _declared_position_range=declared_embargo,
            _applied_position_ranges=applied_embargo_ranges,
            _excluded_rows_after_purge=len(after_purge) - len(effective_positions),
        )
        training_view = _fold_view(
            bounded=view,
            features=features,
            keys=keys,
            context_keys=context_keys,
            eval_axis_offset=eval_axis_offset,
            eval_positions=(effective_positions[0], effective_positions[-1] + 1),
            context_position=effective_positions[0],
            max_feature_prior_rows=max_feature_prior_rows,
        )
        evaluation_view = _fold_view(
            bounded=view,
            features=features,
            keys=keys,
            context_keys=context_keys,
            eval_axis_offset=eval_axis_offset,
            eval_positions=(eval_start, eval_stop),
            context_position=eval_start,
            max_feature_prior_rows=max_feature_prior_rows,
        )
        isolation_params = _isolation_params(
            index=index,
            fit_range=(fit_start, fit_stop),
            eval_range=(eval_start, eval_stop),
            feature_reaches=feature_reaches,
            max_feature_prior_rows=max_feature_prior_rows,
            purge=purge,
            embargo=embargo,
            effective_fit_size=len(effective_positions),
        )
        fit_isolation = _FitIsolation(
            _params=isolation_params,
            _expected_view=training_view,
            _expected_keys=tuple(int(keys[position]) for position in effective_positions),
        )
        plans.append(
            _FoldPlan(
                _index=index,
                _fit_position_range=(fit_start, fit_stop),
                _eval_position_range=(eval_start, eval_stop),
                _fit_key_bounds=_key_bounds(features, keys, fit_start, fit_stop),
                _eval_key_bounds=_key_bounds(features, keys, eval_start, eval_stop),
                _effective_fit_key_bounds=_key_bounds(
                    features,
                    keys,
                    effective_positions[0],
                    effective_positions[-1] + 1,
                ),
                _training_view=training_view,
                _evaluation_view=evaluation_view,
                _effective_fit_positions=effective_positions,
                _effective_fit_size=len(effective_positions),
                _feature_reaches=feature_reaches,
                _max_feature_prior_rows=max_feature_prior_rows,
                _purge=purge,
                _embargo=embargo,
                _fit_isolation=fit_isolation,
            )
        )
        prior_embargo_ranges.append(declared_embargo)
        index += 1

    if not plans:
        raise _ContractViolationError(
            "walk-forward geometry produces no complete fit/evaluation fold"
        )
    return tuple(plans)


def _validated_axis(
    *,
    view: object,
    features: object,
) -> tuple[_npt.NDArray[_np.int64], _npt.NDArray[_np.int64], int]:
    if type(view) is not _BoundedView:
        raise _ContractViolationError("fold generation requires an exact BoundedView")
    if type(features) is not _FeatureFrame:
        raise _ContractViolationError("fold generation requires an exact FeatureFrame")
    bounded = view
    frame = bounded.eval_frame
    if frame.dataset_ref.role not in _TIER_ONE_ROLES:
        raise _ContractViolationError("procedural folds require one legal Tier-1 view")
    keys = frame.keys
    expected_sources = tuple(
        segment.dataset_ref.canonical_identity for segment in bounded.segments
    )
    if (
        not _np.array_equal(features.keys, keys)
        or features.unit is not frame.unit
        or features.clock_domain != frame.clock_domain
        or features.source_identities != expected_sources
        or bounded.eval_start.value > int(keys[0])
        or bounded.eval_end.value < int(keys[-1])
    ):
        raise _ContractViolationError(
            "fold axis must exactly equal one bounded Tier-1 evaluation axis"
        )
    context_keys = _np.concatenate(
        tuple(segment.frame.keys for segment in bounded.segments)
    )
    if (
        not context_keys.size
        or (context_keys.size > 1 and _np.any(context_keys[1:] <= context_keys[:-1]))
    ):
        raise _ContractViolationError(
            "bounded Tier-1 context axis must be non-empty, ordered, and unique"
        )
    eval_axis_offset = int(_np.searchsorted(context_keys, int(keys[0]), side="left"))
    eval_axis_stop = eval_axis_offset + int(keys.size)
    if (
        eval_axis_stop != int(context_keys.size)
        or not _np.array_equal(context_keys[eval_axis_offset:eval_axis_stop], keys)
    ):
        raise _ContractViolationError(
            "bounded Tier-1 evaluation axis must be the exact context-axis suffix"
        )
    return keys, context_keys, eval_axis_offset


def _positive_rows(value: object, *, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise _ContractViolationError(f"{label} must be a positive Python int")


def _feature_reaches(features: _FeatureFrame) -> tuple[_FeatureReach, ...]:
    records: list[_FeatureReach] = []
    for name in features.column_names:
        origin = features.origin(name)
        records.append(
            _FeatureReach(
                _column_name=name,
                _provider_id=origin.provider_id,
                _version=origin.version,
                _params_json=_canonical_params_json(origin.params),
                _max_prior_rows=origin.window.max_prior_rows,
                _gap_policy=origin.window.gap_policy.value,
                _nominal_interval=origin.window.nominal_interval,
            )
        )
    return tuple(records)


def _outcome_reaches(value: object) -> tuple[_OutcomeReach, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, _Sequence):
        raise _ContractViolationError("outcome_specs must be an ordered sequence")
    records: list[_OutcomeReach] = []
    for spec in value:
        validated = _validated_spec(spec)
        decision_delay_rows = validated.decision_delay_rows
        horizon_rows = validated.horizon_rows
        forward_rows = _forward_rows(validated)
        if forward_rows != decision_delay_rows + horizon_rows:
            raise AssertionError("OutcomeSpec forward reach differs from its declarations")
        records.append(
            _OutcomeReach(
                _outcome_id=validated.outcome_id,
                _version=validated.version,
                _params_json=_canonical_params_json(validated.params),
                _decision_delay_rows=decision_delay_rows,
                _horizon_rows=horizon_rows,
                _forward_rows=forward_rows,
            )
        )
    return tuple(records)


def _applied_purge_rows(
    *,
    derived: int,
    override: _PurgeOverride | None,
) -> int:
    if override is None:
        return derived
    if override._purge_rows >= derived:
        raise _ContractViolationError(
            "purge override must strictly reduce the derived purge without exceeding it"
        )
    return override._purge_rows


def _intersections(
    ranges: _Sequence[tuple[int, int]],
    *,
    lower: int,
    upper: int,
) -> tuple[tuple[int, int], ...]:
    clipped = [
        (max(start, lower), min(stop, upper))
        for start, stop in ranges
        if max(start, lower) < min(stop, upper)
    ]
    if not clipped:
        return ()
    merged: list[tuple[int, int]] = []
    for start, stop in sorted(clipped):
        if not merged or start > merged[-1][1]:
            merged.append((start, stop))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
    return tuple(merged)


def _inside_ranges(position: int, ranges: _Sequence[tuple[int, int]]) -> bool:
    return any(start <= position < stop for start, stop in ranges)


def _key(
    features: _FeatureFrame,
    value: int,
) -> _NativeTimeKey:
    return _NativeTimeKey(value, features.unit, features.clock_domain)


def _key_bounds(
    features: _FeatureFrame,
    keys: _npt.NDArray[_np.int64],
    start: int,
    stop: int,
) -> _KeyBounds:
    return _KeyBounds(
        _first=_key(features, int(keys[start])),
        _last=_key(features, int(keys[stop - 1])),
    )


def _fold_view(
    *,
    bounded: _BoundedView,
    features: _FeatureFrame,
    keys: _npt.NDArray[_np.int64],
    context_keys: _npt.NDArray[_np.int64],
    eval_axis_offset: int,
    eval_positions: tuple[int, int],
    context_position: int,
    max_feature_prior_rows: int,
) -> _ViewSpan:
    eval_start, eval_stop = eval_positions
    context_index = eval_axis_offset + context_position - max_feature_prior_rows
    context_start = (
        _key(features, int(context_keys[context_index]))
        if context_index >= 0
        else bounded.context.resolved_context_start
    )
    return _ViewSpan(
        features.source_identities,
        features.unit,
        features.clock_domain,
        context_start,
        _key(features, int(keys[eval_start])),
        _key(features, int(keys[eval_stop - 1])),
    )


def _isolation_params(
    *,
    index: int,
    fit_range: tuple[int, int],
    eval_range: tuple[int, int],
    feature_reaches: tuple[_FeatureReach, ...],
    max_feature_prior_rows: int,
    purge: _PurgeRecord,
    embargo: _EmbargoRecord,
    effective_fit_size: int,
) -> _Mapping[str, _ParamValue]:
    override = purge._override
    return {
        "mode": "rolling_walk_forward_v1",
        "fold_index": index,
        "fit_position_range": fit_range,
        "eval_position_range": eval_range,
        "feature_reaches": tuple(
            (
                reach._column_name,
                reach._provider_id,
                reach._version,
                reach._params_json,
                reach._max_prior_rows,
                reach._gap_policy,
                reach._nominal_interval,
            )
            for reach in feature_reaches
        ),
        "max_feature_prior_rows": max_feature_prior_rows,
        "outcome_reaches": tuple(
            (
                reach._outcome_id,
                reach._version,
                reach._params_json,
                reach._decision_delay_rows,
                reach._horizon_rows,
                reach._forward_rows,
            )
            for reach in purge._outcome_reaches
        ),
        "derived_forward_rows": max(
            (reach._forward_rows for reach in purge._outcome_reaches),
            default=0,
        ),
        "derived_purge_rows": purge._derived_rows,
        "applied_purge_rows": purge._applied_rows,
        "derived_purge_position_range": purge._derived_position_range,
        "applied_purge_position_range": purge._applied_position_range,
        "permitted_overlap_position_range": purge._permitted_overlap_range,
        "embargo_rows": embargo._configured_rows,
        "declared_embargo_position_range": embargo._declared_position_range,
        "applied_embargo_position_ranges": embargo._applied_position_ranges,
        "embargo_excluded_rows_after_purge": embargo._excluded_rows_after_purge,
        "effective_fit_rows": effective_fit_size,
        "purge_override_rows": None if override is None else override._purge_rows,
        "purge_override_reason": None if override is None else override._reason,
    }
