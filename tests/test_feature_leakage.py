"""Slice 2C release-blocking feature leakage and declared-reach rigs."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from trading_research.core import (
    ClockDomain,
    CostHint,
    DatasetRef,
    FeatureContractError,
    FeatureFrame,
    FeatureProvider,
    FeatureRequest,
    GapPolicy,
    NativeTimeKey,
    ParamValue,
    TimeUnit,
    ViewRole,
    WindowSpec,
)
from trading_research.core.frames import MarketFrame, _make_market_frame
from trading_research.features import FEATURE_PROVIDERS
from trading_research.features.execution import _execute_features

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import numpy.typing as npt

_MIN_NUM_ROWS = 128
_MIN_SEAM_POSITION = 64
_GAP_MARGIN = 16
_AXIS_MARGIN = 128
_NOMINAL_INTERVAL = 60
_GAP_KEY_SHIFT = 60_000
_DOMAIN = ClockDomain("feature-rig.wallclock")
_DIGEST_SINGLE = "a" * 64
_DIGEST_CONTEXT = "b" * 64
_DIGEST_EVALUATION = "c" * 64

LIVE_PROVIDER_NAMES = tuple(sorted(FEATURE_PROVIDERS.names()))
COVERED_PROVIDER_NAMES = frozenset(
    {
        "bar_range",
        "gap_sensitive_levels_h1_v1",
        "log_return_1",
        "multi_confluence_m15_v1",
        "rolling_mean_close",
        "uls_market_context_v1",
        "uls_push_v7",
        "zone_context_v1",
        "zone_trader_v13",
    }
)
_ZONE_BASE_EVENT_DETAIL_COLUMNS = frozenset(
    {
        "zt_entry_price",
        "zt_stop_price",
        "zt_target_price",
        "zt_zone_top",
        "zt_zone_bottom",
        "zt_zone_kind",
    }
)
_ZONE_CONTEXT_COLUMNS = frozenset(
    {
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
    }
)
_ULS_EVENT_DETAIL_COLUMNS = frozenset(
    {
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
    }
)
_ULS_MARKET_CONTEXT_COLUMNS = frozenset(
    {
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
    }
)
_EVENT_DETAIL_COLUMNS = (
    _ZONE_BASE_EVENT_DETAIL_COLUMNS
    | _ZONE_CONTEXT_COLUMNS
    | _ULS_EVENT_DETAIL_COLUMNS
    | _ULS_MARKET_CONTEXT_COLUMNS
)


class _BitMismatchError(AssertionError):
    """Raised only when an exact feature-value comparison differs."""


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, _DOMAIN)


def _rig_axis(reach: int) -> tuple[int, int, int]:
    seam_position = max(_MIN_SEAM_POSITION, reach + 1)
    gap_position = max(80, seam_position + _GAP_MARGIN)
    num_rows = max(
        _MIN_NUM_ROWS,
        2 * reach + _AXIS_MARGIN,
        gap_position + reach + 2,
    )
    return num_rows, seam_position, gap_position


def _synthetic_values(num_rows: int) -> dict[str, npt.NDArray[np.float64]]:
    positions = np.arange(num_rows, dtype=np.float64)
    close = 1_000.0 + positions * positions + 3.0 * positions
    opened = close - 5.0 - np.remainder(positions, 11.0)
    high = close + 20.0 + 2.0 * np.remainder(positions, 7.0)
    low = close - 10.0 - 3.0 * np.remainder(positions, 5.0)
    tick_volume = 100.0 + 7.0 * np.remainder(positions, 17.0) + positions
    return {
        "close": close.astype(np.float64),
        "high": high.astype(np.float64),
        "low": low.astype(np.float64),
        "open": opened.astype(np.float64),
        "tick_volume": tick_volume.astype(np.float64),
    }


def _synthetic_keys(
    *,
    num_rows: int,
    gap_position: int | None,
) -> npt.NDArray[np.int64]:
    keys = np.arange(num_rows, dtype=np.int64) * _NOMINAL_INTERVAL
    if gap_position is not None:
        keys[gap_position:] += _GAP_KEY_SHIFT
    return keys


def _adversarial_values(
    values: Mapping[str, npt.NDArray[np.float64]],
    *,
    names: Sequence[str],
    start: int,
    stop: int,
) -> dict[str, npt.NDArray[np.float64]]:
    mutated = {name: column.copy() for name, column in values.items()}
    positions = np.arange(start, stop, dtype=np.float64)
    for name in names:
        selected = mutated[name][start:stop]
        if name == "close":
            mutated[name][start:stop] = 2.0 * selected + 4_096.0 + 3.0 * positions
        elif name == "high":
            mutated[name][start:stop] = 3.0 * selected + 2_048.0 + 5.0 * positions
        elif name == "low":
            mutated[name][start:stop] = 0.5 * selected - 1_024.0 - 7.0 * positions
        elif name == "open":
            mutated[name][start:stop] = 1.5 * selected + 8_192.0 + 11.0 * positions
        elif name == "tick_volume":
            mutated[name][start:stop] = 4.0 * selected + 16_384.0 + 13.0 * positions
        else:
            raise AssertionError(f"the synthetic rig has no adversary for column {name!r}")
    return mutated


def _frame(
    *,
    keys: npt.NDArray[np.int64],
    values: Mapping[str, npt.NDArray[np.float64]],
    start: int,
    stop: int,
    dataset_id: str,
    digest: str,
    role: ViewRole,
) -> MarketFrame:
    columns = {name: column[start:stop] for name, column in values.items()}
    return _make_market_frame(
        dataset_ref=DatasetRef(dataset_id, digest, role),
        clock_domain=_DOMAIN,
        unit=TimeUnit.SECONDS,
        schema=dict.fromkeys(columns, "float64"),
        keys=keys[start:stop],
        columns=columns,
        admitted_lo=int(keys[start]),
        admitted_hi=int(keys[stop - 1]),
    )


def _frames(
    *,
    keys: npt.NDArray[np.int64],
    values: Mapping[str, npt.NDArray[np.float64]],
    segmented: bool,
    seam_position: int,
    num_rows: int,
) -> tuple[MarketFrame, ...]:
    if not segmented:
        return (
            _frame(
                keys=keys,
                values=values,
                start=0,
                stop=num_rows,
                dataset_id="FEATURE_RIG_SINGLE",
                digest=_DIGEST_SINGLE,
                role=ViewRole.VALIDATION,
            ),
        )
    return (
        _frame(
            keys=keys,
            values=values,
            start=0,
            stop=seam_position,
            dataset_id="FEATURE_RIG_CONTEXT",
            digest=_DIGEST_CONTEXT,
            role=ViewRole.TRAIN,
        ),
        _frame(
            keys=keys,
            values=values,
            start=seam_position,
            stop=num_rows,
            dataset_id="FEATURE_RIG_EVALUATION",
            digest=_DIGEST_EVALUATION,
            role=ViewRole.VALIDATION,
        ),
    )


def _run(
    provider: FeatureProvider,
    *,
    keys: npt.NDArray[np.int64],
    values: Mapping[str, npt.NDArray[np.float64]],
    segmented: bool,
    seam_position: int,
    num_rows: int,
    eval_start_position: int | None = None,
) -> FeatureFrame:
    start = (
        seam_position
        if segmented and eval_start_position is None
        else (0 if eval_start_position is None else eval_start_position)
    )
    return _execute_features(
        frames=_frames(
            keys=keys,
            values=values,
            segmented=segmented,
            seam_position=seam_position,
            num_rows=num_rows,
        ),
        eval_start=_key(int(keys[start])),
        eval_end=_key(int(keys[-1])),
        expected_eval_keys=keys[start:],
        providers=(provider,),
        short_context_accepted=True,
    )


def _live_factory(name: str) -> Callable[[], FeatureProvider]:
    return lambda: FEATURE_PROVIDERS.create(name)


def _break_rolling_factory() -> FeatureProvider:
    return FEATURE_PROVIDERS.create(
        "rolling_mean_close",
        window=20,
        gap_policy=GapPolicy.BREAK,
        nominal_interval=_NOMINAL_INTERVAL,
    )


class _BreakDeclaration:
    """Test-only BREAK declaration around one unchanged live computation."""

    def __init__(self, provider: FeatureProvider) -> None:
        self._provider = provider
        self._window = WindowSpec(
            provider.window.max_prior_rows,
            GapPolicy.BREAK,
            nominal_interval=_NOMINAL_INTERVAL,
        )

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    @property
    def version(self) -> int:
        return self._provider.version

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._provider.feature_names

    @property
    def required_columns(self) -> tuple[str, ...]:
        return self._provider.required_columns

    @property
    def window(self) -> WindowSpec:
        return self._window

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return self._provider.params

    @property
    def cost_hint(self) -> CostHint:
        return self._provider.cost_hint

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        return self._provider.compute(request)


def _break_live_factory(name: str) -> Callable[[], FeatureProvider]:
    return lambda: _BreakDeclaration(FEATURE_PROVIDERS.create(name))


def _assert_array_bits_equal(
    left: npt.NDArray[np.float64],
    right: npt.NDArray[np.float64],
) -> None:
    if left.dtype != np.dtype(np.float64) or right.dtype != np.dtype(np.float64):
        raise _BitMismatchError("feature dtypes differ from exact float64")
    if left.shape != right.shape:
        raise _BitMismatchError("feature shapes differ")
    if np.isnan(left).tobytes() != np.isnan(right).tobytes():
        raise _BitMismatchError("feature NaN masks differ")
    if left.view(np.uint64).tobytes() != right.view(np.uint64).tobytes():
        raise _BitMismatchError("feature value bits differ")


def _indices_for_keys(
    frame: FeatureFrame,
    expected_keys: npt.NDArray[np.int64],
) -> npt.NDArray[np.int64]:
    positions = np.searchsorted(frame.keys, expected_keys)
    assert positions.shape == expected_keys.shape
    assert np.array_equal(frame.keys[positions], expected_keys)
    return positions.astype(np.int64)


def _assert_frame_values_exact(
    left: FeatureFrame,
    right: FeatureFrame,
    *,
    expected_keys: npt.NDArray[np.int64],
) -> None:
    assert left.column_names == right.column_names
    assert np.array_equal(left.keys, right.keys)
    left_positions = _indices_for_keys(left, expected_keys)
    right_positions = _indices_for_keys(right, expected_keys)
    for name in left.column_names:
        assert left.origin(name) == right.origin(name)
        _assert_array_bits_equal(
            left.column(name)[left_positions],
            right.column(name)[right_positions],
        )


def _assert_positions_are_finite(
    frame: FeatureFrame,
    *,
    expected_key: int,
) -> None:
    position = _indices_for_keys(frame, np.array([expected_key], dtype=np.int64))[0]
    for name in frame.column_names:
        if name in _EVENT_DETAIL_COLUMNS:
            continue
        assert np.isfinite(frame.column(name)[position])


def _assert_each_feature_has_finite_witness(
    frame: FeatureFrame,
    *,
    expected_keys: npt.NDArray[np.int64],
) -> None:
    positions = _indices_for_keys(frame, expected_keys)
    for name in frame.column_names:
        if name in _EVENT_DETAIL_COLUMNS:
            continue
        assert np.isfinite(frame.column(name)[positions]).any()


def _assert_future_safe(
    factory: Callable[[], FeatureProvider],
    *,
    segmented: bool,
) -> None:
    declaration = factory()
    reach = declaration.window.max_prior_rows
    num_rows, seam_position, gap_position = _rig_axis(reach)
    assert reach < seam_position
    keys = _synthetic_keys(num_rows=num_rows, gap_position=gap_position)
    values = _synthetic_values(num_rows)
    cutoffs = (
        {
            seam_position,
            gap_position,
            (seam_position + num_rows - 1) // 2,
            num_rows - 2,
        }
        if segmented
        else {reach, num_rows // 2, num_rows - 2, gap_position}
    )
    for cutoff in sorted(cutoffs):
        assert cutoff <= num_rows - 2
        baseline = _run(
            factory(),
            keys=keys,
            values=values,
            segmented=segmented,
            seam_position=seam_position,
            num_rows=num_rows,
        )
        perturbed = _run(
            factory(),
            keys=keys,
            values=_adversarial_values(
                values,
                names=declaration.required_columns,
                start=cutoff + 1,
                stop=num_rows,
            ),
            segmented=segmented,
            seam_position=seam_position,
            num_rows=num_rows,
        )
        comparable = baseline.keys[baseline.keys <= keys[cutoff]]
        assert comparable.size
        _assert_each_feature_has_finite_witness(
            baseline,
            expected_keys=comparable,
        )
        _assert_frame_values_exact(
            baseline,
            perturbed,
            expected_keys=comparable,
        )


def _assert_backward_reach_safe(
    factory: Callable[[], FeatureProvider],
    *,
    segmented: bool,
) -> None:
    declaration = factory()
    reach = declaration.window.max_prior_rows
    num_rows, seam_position, _ = _rig_axis(reach)
    assert reach < seam_position
    keys = _synthetic_keys(num_rows=num_rows, gap_position=None)
    values = _synthetic_values(num_rows)
    targets = (
        {seam_position, seam_position + reach}
        if segmented
        else {max(reach, 64), num_rows // 2, num_rows - 2}
    )
    for target in sorted(targets):
        assert target < num_rows
        mutation_stop = target - reach
        assert mutation_stop >= 0
        baseline = _run(
            factory(),
            keys=keys,
            values=values,
            segmented=segmented,
            seam_position=seam_position,
            num_rows=num_rows,
        )
        perturbed = _run(
            factory(),
            keys=keys,
            values=_adversarial_values(
                values,
                names=declaration.required_columns,
                start=0,
                stop=mutation_stop,
            ),
            segmented=segmented,
            seam_position=seam_position,
            num_rows=num_rows,
        )
        _assert_positions_are_finite(baseline, expected_key=int(keys[target]))
        _assert_frame_values_exact(
            baseline,
            perturbed,
            expected_keys=np.array([keys[target]], dtype=np.int64),
        )


def _assert_break_isolated(
    factory: Callable[[], FeatureProvider],
    *,
    segmented: bool,
) -> None:
    declaration = factory()
    assert declaration.window.gap_policy is GapPolicy.BREAK
    reach = declaration.window.max_prior_rows
    num_rows, seam_position, single_gap_position = _rig_axis(reach)
    gap_position = seam_position if segmented else single_gap_position
    assert gap_position + reach < num_rows
    keys = _synthetic_keys(num_rows=num_rows, gap_position=gap_position)
    values = _synthetic_values(num_rows)
    baseline = _run(
        factory(),
        keys=keys,
        values=values,
        segmented=segmented,
        seam_position=seam_position,
        num_rows=num_rows,
    )
    perturbed = _run(
        factory(),
        keys=keys,
        values=_adversarial_values(
            values,
            names=declaration.required_columns,
            start=0,
            stop=gap_position,
        ),
        segmented=segmented,
        seam_position=seam_position,
        num_rows=num_rows,
    )
    comparable = baseline.keys[baseline.keys >= keys[gap_position]]
    assert comparable.size
    _assert_frame_values_exact(
        baseline,
        perturbed,
        expected_keys=comparable,
    )
    _assert_positions_are_finite(
        baseline,
        expected_key=int(keys[gap_position + reach]),
    )


def _assert_seam_equivalent(factory: Callable[[], FeatureProvider]) -> None:
    reach = factory().window.max_prior_rows
    num_rows, seam_position, gap_position = _rig_axis(reach)
    keys = _synthetic_keys(num_rows=num_rows, gap_position=gap_position)
    values = _synthetic_values(num_rows)
    single = _run(
        factory(),
        keys=keys,
        values=values,
        segmented=False,
        seam_position=seam_position,
        num_rows=num_rows,
        eval_start_position=seam_position,
    )
    segmented = _run(
        factory(),
        keys=keys,
        values=values,
        segmented=True,
        seam_position=seam_position,
        num_rows=num_rows,
    )
    _assert_frame_values_exact(single, segmented, expected_keys=keys[seam_position:])


class _ControlProvider:
    _provider_id = "control"
    _window = WindowSpec(0, GapPolicy.CROSS)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def version(self) -> int:
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        return (self._provider_id,)

    @property
    def required_columns(self) -> tuple[str, ...]:
        return ("close",)

    @property
    def window(self) -> WindowSpec:
        return self._window

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return {}

    @property
    def cost_hint(self) -> CostHint:
        return CostHint.CHEAP

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        return {self._provider_id: self._compute_values(request)}

    def _compute_values(
        self,
        request: FeatureRequest,
    ) -> npt.NDArray[np.float64]:
        raise NotImplementedError


class FutureShiftProvider(_ControlProvider):
    _provider_id = "control.future_shift"

    def _compute_values(self, request: FeatureRequest) -> npt.NDArray[np.float64]:
        close = cast("npt.NDArray[np.float64]", request.column("close"))
        positions = request.eval_offset + np.arange(request.eval_length, dtype=np.int64)
        return close[np.minimum(positions + 1, close.size - 1)].copy()


class CenteredWindowProvider(_ControlProvider):
    _provider_id = "control.centered_window"
    _window = WindowSpec(1, GapPolicy.CROSS)

    def _compute_values(self, request: FeatureRequest) -> npt.NDArray[np.float64]:
        close = cast("npt.NDArray[np.float64]", request.column("close"))
        positions = request.eval_offset + np.arange(request.eval_length, dtype=np.int64)
        prior = close[np.maximum(positions - 1, 0)]
        current = close[positions]
        future = close[np.minimum(positions + 1, close.size - 1)]
        return ((prior + current + future) / 3.0).astype(np.float64)


class WholeDatasetNormalizationProvider(_ControlProvider):
    _provider_id = "control.whole_dataset_normalization"
    _window = WindowSpec(2, GapPolicy.CROSS)

    def _compute_values(self, request: FeatureRequest) -> npt.NDArray[np.float64]:
        close = cast("npt.NDArray[np.float64]", request.column("close"))
        positions = request.eval_offset + np.arange(request.eval_length, dtype=np.int64)
        return ((close[positions] - close.mean()) / close.std()).astype(np.float64)


class EvalEndPeekProvider(_ControlProvider):
    _provider_id = "control.eval_end_peek"

    def _compute_values(self, request: FeatureRequest) -> npt.NDArray[np.float64]:
        close = cast("npt.NDArray[np.float64]", request.column("close"))
        positions = request.eval_offset + np.arange(request.eval_length, dtype=np.int64)
        result = close[positions].copy()
        result += cast("np.float64", close[-1])
        return result


class UnderDeclaredReachProvider(_ControlProvider):
    _provider_id = "control.under_declared_reach"
    _window = WindowSpec(5, GapPolicy.CROSS)

    def _compute_values(self, request: FeatureRequest) -> npt.NDArray[np.float64]:
        close = cast("npt.NDArray[np.float64]", request.column("close"))
        positions = request.eval_offset + np.arange(request.eval_length, dtype=np.int64)
        source = np.where(positions >= 50, positions - 50, positions)
        return close[source].copy()


class GapCrossingUnderBreakProvider(_ControlProvider):
    _provider_id = "control.gap_crossing_under_break"
    _window = WindowSpec(5, GapPolicy.BREAK, nominal_interval=_NOMINAL_INTERVAL)

    def _compute_values(self, request: FeatureRequest) -> npt.NDArray[np.float64]:
        close = cast("npt.NDArray[np.float64]", request.column("close"))
        positions = request.eval_offset + np.arange(request.eval_length, dtype=np.int64)
        result = close[positions].copy()
        keys = request.keys
        deltas = keys.view(np.uint64)[1:] - keys.view(np.uint64)[:-1]
        gaps = np.flatnonzero(deltas != np.uint64(_NOMINAL_INTERVAL))
        if gaps.size:
            gap_position = int(gaps[0]) + 1
            defect = positions >= gap_position + self.window.max_prior_rows
            result[defect] += close[gap_position - 1]
        return result


def test_rig_coverage_is_an_independent_exact_match_for_the_live_registry() -> None:
    assert FEATURE_PROVIDERS.names() == COVERED_PROVIDER_NAMES
    assert set(LIVE_PROVIDER_NAMES) == COVERED_PROVIDER_NAMES
    required = {
        column
        for name in LIVE_PROVIDER_NAMES
        for column in FEATURE_PROVIDERS.create(name).required_columns
    }
    assert required <= set(_synthetic_values(_MIN_NUM_ROWS))


def test_zone_event_detail_columns_have_an_exact_finite_event_witness() -> None:
    keys = np.arange(6, dtype=np.int64) * 300
    values = {
        "open": np.array([9.5, 9.6, 11.5, 11.3, 10.5, 10.5], dtype=np.float64),
        "high": np.array([10.0, 10.0, 12.0, 11.5, 10.8, 10.6], dtype=np.float64),
        "low": np.array([9.0, 9.0, 11.0, 11.2, 10.2, 10.4], dtype=np.float64),
        "close": np.array([9.5, 9.5, 11.5, 11.3, 10.5, 10.5], dtype=np.float64),
        "tick_volume": np.ones(6, dtype=np.float64),
    }
    provider = FEATURE_PROVIDERS.create(
        "zone_trader_v13",
        use_volume_zones=False,
        use_demand_supply=False,
        confirmation_mode="immediate_touch",
        break_buffer_atr=2.0,
        w_atr=1,
        max_zone_age_rows=10,
    )
    frame = _frame(
        keys=keys,
        values=values,
        start=0,
        stop=keys.size,
        dataset_id="ZONE_EVENT_WITNESS",
        digest=_DIGEST_SINGLE,
        role=ViewRole.TRAIN,
    )
    request = FeatureRequest.from_segments(
        (frame,),
        eval_start=_key(int(keys[0])),
        eval_end=_key(int(keys[-1])),
        required_columns=provider.required_columns,
    )

    first = provider.compute(request)
    second = provider.compute(request)

    assert first["zt_event_signed"][-1] == 1.0
    for name in _ZONE_BASE_EVENT_DETAIL_COLUMNS:
        assert np.isfinite(first[name][-1])
        _assert_array_bits_equal(first[name][-1:], second[name][-1:])


def test_zone_context_columns_have_an_exact_finite_event_witness() -> None:
    prefix = 50
    tail = {
        "open": np.array([9.5, 9.6, 11.5, 11.3, 10.5, 10.5], dtype=np.float64),
        "high": np.array([10.0, 10.0, 12.0, 11.5, 10.8, 10.6], dtype=np.float64),
        "low": np.array([9.0, 9.0, 11.0, 11.2, 10.2, 10.4], dtype=np.float64),
        "close": np.array([9.5, 9.5, 11.5, 11.3, 10.5, 10.5], dtype=np.float64),
        "tick_volume": np.ones(6, dtype=np.float64),
    }
    values = {
        name: np.concatenate((np.full(prefix, values[0], dtype=np.float64), values))
        for name, values in tail.items()
    }
    keys = np.arange(prefix + 6, dtype=np.int64) * 300
    provider = FEATURE_PROVIDERS.create(
        "zone_context_v1",
        use_volume_zones=False,
        use_demand_supply=False,
        confirmation_mode="immediate_touch",
        break_buffer_atr=2.0,
        w_atr=1,
        max_zone_age_rows=10,
    )
    frame = _frame(
        keys=keys,
        values=values,
        start=0,
        stop=keys.size,
        dataset_id="ZONE_CONTEXT_WITNESS",
        digest=_DIGEST_SINGLE,
        role=ViewRole.TRAIN,
    )
    request = FeatureRequest.from_segments(
        (frame,),
        eval_start=_key(int(keys[0])),
        eval_end=_key(int(keys[-1])),
        required_columns=provider.required_columns,
    )

    first = provider.compute(request)
    second = provider.compute(request)

    for name in _ZONE_CONTEXT_COLUMNS:
        assert np.isfinite(first[name][-1])
        _assert_array_bits_equal(first[name][-1:], second[name][-1:])


def test_zone_context_ignores_invalid_events_outside_the_evaluation_span() -> None:
    event_pattern = {
        "open": np.array([9.5, 9.6, 11.5, 11.3, 10.5, 10.5], dtype=np.float64),
        "high": np.array([10.0, 10.0, 12.0, 11.5, 10.8, 10.6], dtype=np.float64),
        "low": np.array([9.0, 9.0, 11.0, 11.2, 10.2, 10.4], dtype=np.float64),
        "close": np.array([9.5, 9.5, 11.5, 11.3, 10.5, 10.5], dtype=np.float64),
        "tick_volume": np.ones(6, dtype=np.float64),
    }
    prior_rows = 350
    values = {
        name: np.concatenate(
            (
                pattern,
                np.full(prior_rows, pattern[-1], dtype=np.float64),
                pattern,
            )
        )
        for name, pattern in event_pattern.items()
    }
    keys = np.arange(2 * 6 + prior_rows, dtype=np.int64) * 300
    provider = FEATURE_PROVIDERS.create(
        "zone_context_v1",
        use_volume_zones=False,
        use_demand_supply=False,
        confirmation_mode="immediate_touch",
        break_buffer_atr=2.0,
        w_atr=300,
        max_zone_age_rows=10,
    )
    frame = _frame(
        keys=keys,
        values=values,
        start=0,
        stop=keys.size,
        dataset_id="ZONE_CONTEXT_PRIOR_ONLY_EVENT",
        digest=_DIGEST_SINGLE,
        role=ViewRole.TRAIN,
    )
    request = FeatureRequest.from_segments(
        (frame,),
        eval_start=_key(int(keys[-6])),
        eval_end=_key(int(keys[-1])),
        required_columns=provider.required_columns,
    )

    result = provider.compute(request)

    for name in _ZONE_CONTEXT_COLUMNS:
        assert np.isfinite(result[name][-1])


@pytest.mark.parametrize(
    "provider_name",
    [
        "gap_sensitive_levels_h1_v1",
        "multi_confluence_m15_v1",
        "uls_market_context_v1",
        "uls_push_v7",
        "zone_trader_v13",
    ],
)
@pytest.mark.parametrize("segmented", [False, True], ids=["single", "seam"])
def test_aggregated_providers_refuse_a_denser_than_m1_seconds_axis(
    provider_name: str,
    segmented: bool,
) -> None:
    num_rows = _MIN_NUM_ROWS
    seam_position = _MIN_SEAM_POSITION
    keys = np.arange(num_rows, dtype=np.int64) * 30
    values = _synthetic_values(num_rows)

    with pytest.raises(FeatureContractError, match="at least 60 seconds apart"):
        _run(
            FEATURE_PROVIDERS.create(provider_name),
            keys=keys,
            values=values,
            segmented=segmented,
            seam_position=seam_position,
            num_rows=num_rows,
        )


@pytest.mark.parametrize("provider_name", LIVE_PROVIDER_NAMES)
@pytest.mark.parametrize("segmented", [False, True], ids=["single", "seam"])
def test_every_registered_provider_is_future_safe(
    provider_name: str,
    segmented: bool,
) -> None:
    _assert_future_safe(_live_factory(provider_name), segmented=segmented)


@pytest.mark.parametrize("provider_name", LIVE_PROVIDER_NAMES)
@pytest.mark.parametrize("segmented", [False, True], ids=["single", "seam"])
def test_every_registered_provider_honours_its_declared_backward_reach(
    provider_name: str,
    segmented: bool,
) -> None:
    _assert_backward_reach_safe(_live_factory(provider_name), segmented=segmented)


@pytest.mark.parametrize("provider_name", LIVE_PROVIDER_NAMES)
def test_every_registered_provider_matches_across_the_segment_seam(
    provider_name: str,
) -> None:
    _assert_seam_equivalent(_live_factory(provider_name))


@pytest.mark.parametrize("segmented", [False, True], ids=["single", "seam"])
@pytest.mark.parametrize("provider_name", LIVE_PROVIDER_NAMES)
def test_every_registered_provider_is_break_isolated_after_an_arithmetic_gap(
    provider_name: str,
    segmented: bool,
) -> None:
    factory = _break_live_factory(provider_name)
    provider = factory()
    assert provider.provider_id == provider_name
    assert provider.window.gap_policy is GapPolicy.BREAK
    assert provider.window.nominal_interval == _NOMINAL_INTERVAL
    _assert_break_isolated(factory, segmented=segmented)


@pytest.mark.parametrize("segmented", [False, True], ids=["single", "seam"])
def test_configured_live_rolling_provider_is_break_isolated(
    segmented: bool,
) -> None:
    provider = _break_rolling_factory()
    assert provider.provider_id == "rolling_mean_close"
    assert provider.window == WindowSpec(
        19,
        GapPolicy.BREAK,
        nominal_interval=_NOMINAL_INTERVAL,
    )
    _assert_break_isolated(_break_rolling_factory, segmented=segmented)


@pytest.mark.parametrize(
    "control",
    [
        FutureShiftProvider,
        CenteredWindowProvider,
        WholeDatasetNormalizationProvider,
        EvalEndPeekProvider,
    ],
)
def test_future_rig_catches_forward_reading_controls(
    control: type[_ControlProvider],
) -> None:
    with pytest.raises(_BitMismatchError, match="feature value bits differ"):
        _assert_future_safe(control, segmented=False)


def test_under_declared_control_is_caught_only_by_the_reach_rig() -> None:
    _assert_future_safe(UnderDeclaredReachProvider, segmented=False)
    with pytest.raises(_BitMismatchError, match="feature value bits differ"):
        _assert_backward_reach_safe(UnderDeclaredReachProvider, segmented=False)


def test_gap_crossing_control_is_caught_by_break_isolation() -> None:
    _assert_future_safe(GapCrossingUnderBreakProvider, segmented=False)
    _assert_backward_reach_safe(GapCrossingUnderBreakProvider, segmented=False)
    with pytest.raises(_BitMismatchError, match="feature value bits differ"):
        _assert_break_isolated(GapCrossingUnderBreakProvider, segmented=False)
    with pytest.raises(_BitMismatchError, match="feature value bits differ"):
        _assert_break_isolated(GapCrossingUnderBreakProvider, segmented=True)
