"""Slice 2B deterministic feature execution and reference-provider goldens."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import numpy.typing as npt
import pytest

from trading_research.core import (
    ClockDomain,
    CostHint,
    DatasetRef,
    DuplicateComponentError,
    FeatureContractError,
    FeatureFrame,
    FeatureFrameError,
    FeatureProvider,
    FeatureRequest,
    FeatureRequestError,
    GapPolicy,
    InsufficientContextError,
    NativeTimeKey,
    Registry,
    TimeUnit,
    ViewRole,
    WindowSpec,
)
from trading_research.core.frames import MarketFrame, _make_market_frame
from trading_research.data.views import (
    BoundedSegment,
    BoundedView,
    ContextResolution,
    ContextShortfall,
)
from trading_research.experiments.features import compute_features
from trading_research.features import FEATURE_PROVIDERS
from trading_research.features.execution import _execute_features
from trading_research.features.reference import (
    BarRange,
    GapSensitiveLevelsH1V1,
    LogReturn1,
    MultiConfluenceM15V1,
    RollingMeanClose,
    UlsMarketContextV1,
    UlsPushV7,
    ZoneContextV1,
    ZoneTraderV13,
    _aggregate_complete_strategy_bars,
    _AggregatedStrategyBars,
    _finite_recursive_average,
    _gsl_strategy_values,
    _mc_confirmed_level_state,
    _mc_strategy_values,
    _mc_trendline_prices,
    _rolling_sma,
    _run_uls_push_state,
    _run_zone_trader_state,
    _truncated_wilder_atr,
    _uls_ema,
    _up_add_liquidity,
    _up_advance_setup,
    _up_confirmation_required,
    _up_consume_liquidity,
    _up_indicators,
    _up_is_pivot_high,
    _up_is_pivot_low,
    _up_liquidity_eligible,
    _up_opposite_liquidity,
    _up_update_cisd,
    _UpCisdState,
    _UpFvg,
    _UpLiquidity,
    _UpSetup,
    _validate_zone_trader_grids,
    _zone_trader_primitives,
    _ZoneTraderPrimitives,
    _ZoneTraderStrategyOutput,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DOMAIN = ClockDomain("venue.wallclock")


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _float_vector(
    values: Sequence[float] | npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    return np.asarray(values, dtype=np.float64)


def _frame(
    keys: Sequence[int],
    *,
    opened: Sequence[float] | None = None,
    close: Sequence[float] | None = None,
    high: Sequence[float] | None = None,
    low: Sequence[float] | None = None,
    tick_volume: Sequence[float] | None = None,
    extra_columns: Mapping[str, Sequence[float]] | None = None,
    dataset_id: str = "TRAIN_V1",
    digest: str = DIGEST_A,
    role: ViewRole = ViewRole.TRAIN,
) -> MarketFrame:
    key_array = np.asarray(keys, dtype=np.int64)
    default_close = np.arange(1, key_array.size + 1, dtype=np.float64)
    close_array = default_close if close is None else _float_vector(close)
    open_array = close_array - 0.5 if opened is None else _float_vector(opened)
    high_array = close_array + 2.0 if high is None else _float_vector(high)
    low_array = close_array - 1.0 if low is None else _float_vector(low)
    volume_array = (
        np.arange(1, key_array.size + 1, dtype=np.float64)
        if tick_volume is None
        else _float_vector(tick_volume)
    )
    columns: dict[str, npt.NDArray[Any]] = {
        "open": open_array,
        "close": close_array,
        "high": high_array,
        "low": low_array,
        "tick_volume": volume_array,
    }
    for name, values in (extra_columns or {}).items():
        columns[name] = _float_vector(values)
    schema = {name: values.dtype.name for name, values in columns.items()}
    return _make_market_frame(
        dataset_ref=DatasetRef(dataset_id, digest, role),
        clock_domain=DOMAIN,
        unit=TimeUnit.SECONDS,
        schema=schema,
        keys=key_array,
        columns=columns,
        admitted_lo=int(key_array[0]),
        admitted_hi=int(key_array[-1]),
    )


def _request(
    frames: Sequence[MarketFrame],
    *,
    eval_start: int,
    eval_end: int,
    required_columns: Sequence[str],
) -> FeatureRequest:
    return FeatureRequest.from_segments(
        frames,
        eval_start=_key(eval_start),
        eval_end=_key(eval_end),
        required_columns=required_columns,
    )


def _strategy_bars(
    *,
    opened: Sequence[float] | npt.NDArray[np.float64],
    high: Sequence[float] | npt.NDArray[np.float64],
    low: Sequence[float] | npt.NDArray[np.float64],
    close: Sequence[float] | npt.NDArray[np.float64],
    tick_volume: Sequence[float] | npt.NDArray[np.float64] | None = None,
    bucket_numbers: Sequence[int] | None = None,
) -> _AggregatedStrategyBars:
    size = len(close)
    buckets = tuple(range(size)) if bucket_numbers is None else bucket_numbers
    volume = tuple(1.0 for _ in range(size)) if tick_volume is None else tick_volume
    return _AggregatedStrategyBars(
        np.asarray(buckets, dtype=np.int64),
        np.arange(1, size + 1, dtype=np.int64),
        _float_vector(opened),
        _float_vector(high),
        _float_vector(low),
        _float_vector(close),
        _float_vector(volume),
    )


def _state_primitives(
    size: int,
    *,
    upper_wick: Sequence[float] | None = None,
    lower_wick: Sequence[float] | None = None,
    volume_top: Mapping[int, float] | None = None,
    volume_bottom: Mapping[int, float] | None = None,
    fvg_top: Mapping[int, float] | None = None,
    fvg_bottom: Mapping[int, float] | None = None,
    demand_supply_top: Mapping[int, float] | None = None,
    demand_supply_bottom: Mapping[int, float] | None = None,
) -> _ZoneTraderPrimitives:
    def sparse(values: Mapping[int, float] | None) -> npt.NDArray[np.float64]:
        result = np.full(size, np.nan, dtype=np.float64)
        for index, value in (values or {}).items():
            result[index] = value
        return result

    zeros_bool = np.zeros(size, dtype=np.bool_)
    return _ZoneTraderPrimitives(
        np.ones(size, dtype=np.float64),
        np.zeros(size, dtype=np.float64) if upper_wick is None else _float_vector(upper_wick),
        np.zeros(size, dtype=np.float64) if lower_wick is None else _float_vector(lower_wick),
        zeros_bool.copy(),
        sparse(volume_top),
        sparse(volume_bottom),
        zeros_bool.copy(),
        zeros_bool.copy(),
        zeros_bool.copy(),
        sparse(fvg_top),
        sparse(fvg_bottom),
        zeros_bool.copy(),
        zeros_bool.copy(),
        zeros_bool.copy(),
        sparse(demand_supply_top),
        sparse(demand_supply_bottom),
    )


def _run_state_fixture(
    *,
    opened: Sequence[float] | npt.NDArray[np.float64],
    high: Sequence[float] | npt.NDArray[np.float64],
    low: Sequence[float] | npt.NDArray[np.float64],
    close: Sequence[float] | npt.NDArray[np.float64],
    atr: Sequence[float] | npt.NDArray[np.float64],
    primitives: _ZoneTraderPrimitives,
    max_zone_age_rows: int = 5000,
    max_zones: int = 15,
    break_buffer_atr: float = 0.5,
    confirmation_mode: str = "immediate_touch",
    minimum_wick: float = 0.35,
    max_bars_wait: int = 6,
    stop_buffer_atr: float = 0.5,
    reward_risk_target: float = 2.0,
) -> _ZoneTraderStrategyOutput:
    return _run_zone_trader_state(
        _float_vector(opened),
        _float_vector(high),
        _float_vector(low),
        _float_vector(close),
        _float_vector(atr),
        primitives,
        max_zone_age_rows=max_zone_age_rows,
        max_zones=max_zones,
        break_buffer_atr=break_buffer_atr,
        confirmation_mode=confirmation_mode,
        minimum_wick=minimum_wick,
        max_bars_wait=max_bars_wait,
        stop_buffer_atr=stop_buffer_atr,
        reward_risk_target=reward_risk_target,
    )


def _zone_reach_m1_fixture() -> tuple[
    npt.NDArray[np.int64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    int,
]:
    """Build the exact-age, exact-wait M1 reach fixture for Zone Trader."""
    strategy_size = 5306
    opened = np.full(strategy_size, 100.0, dtype=np.float64)
    high = np.full(strategy_size, 101.0, dtype=np.float64)
    low = np.full(strategy_size, 99.0, dtype=np.float64)
    close = np.full(strategy_size, 100.0, dtype=np.float64)
    high[0] = 200.0
    low[0] = 0.0
    high[298] = 100.0
    close[298] = opened[298] = 99.5

    gap = float.fromhex("0x1.e6a331ae1e34fp-2") + 3e-9
    top = 100.0 + gap
    low[300] = top
    high[300] = 101.0 + gap
    close[300] = opened[300] = 100.5 + gap
    low[301:5300] = top + 0.05
    high[301:5300] = top + 2.0
    close[301:5300] = opened[301:5300] = top + 0.1
    low[5300] = top - 0.1
    high[5300] = top + 2.0
    close[5300] = opened[5300] = top + 0.2
    low[5301:5305] = top + 0.05
    high[5301:5305] = top + 2.0
    close[5301:5305] = opened[5301:5305] = top + 0.1
    low[5305] = top - 1.0
    high[5305] = top + 1.0
    close[5305] = opened[5305] = top + 0.5

    event_position = strategy_size * 5
    keys = np.arange(event_position + 5, dtype=np.int64) * 60

    def expand(values: npt.NDArray[np.float64], final: float) -> npt.NDArray[np.float64]:
        return np.concatenate((np.repeat(values, 5), np.full(5, final, dtype=np.float64)))

    return (
        keys,
        expand(opened, 100.0),
        expand(high, 101.0),
        expand(low, 99.0),
        expand(close, 100.0),
        np.ones(keys.size, dtype=np.float64),
        event_position,
    )


def _uls_reach_m1_fixture(
    *,
    shift: int = 0,
) -> tuple[
    npt.NDArray[np.int64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    int,
    int,
]:
    """Build one exact-age short ULS event and expose its oldest pivot leg."""
    event_bar = 1_103 + shift
    strategy_size = event_bar + 1
    opened = np.full(strategy_size, 100.0, dtype=np.float64)
    close = np.full(strategy_size, 100.0, dtype=np.float64)
    high = np.full(strategy_size, 101.0, dtype=np.float64)
    low = np.full(strategy_size, 99.0, dtype=np.float64)
    volume = np.full(strategy_size, 100.0, dtype=np.float64)

    high[300 + shift] = 105.0
    low[1_090 + shift] = 98.0
    high[1_096 + shift] = 103.0
    low[1_096 + shift] = 102.0
    opened[1_096 + shift] = 102.2
    close[1_096 + shift] = 102.5
    for index in range(1_097 + shift, 1_100 + shift):
        high[index] = 103.0
        low[index] = 101.2
        opened[index] = close[index] = 102.0
    high[1_100 + shift] = 105.5
    low[1_100 + shift] = 101.2
    opened[1_100 + shift] = 104.5
    close[1_100 + shift] = 105.2
    high[1_101 + shift] = 105.2
    low[1_101 + shift] = 101.2
    opened[1_101 + shift] = 105.0
    close[1_101 + shift] = 104.5
    high[1_102 + shift] = 104.0
    low[1_102 + shift] = 99.5
    opened[1_102 + shift] = 103.0
    close[1_102 + shift] = 100.5
    high[event_bar] = 101.0
    low[event_bar] = 96.5
    opened[event_bar] = 100.0
    close[event_bar] = 97.0

    event_position = strategy_size * 5
    keys = np.arange(event_position + 1, dtype=np.int64) * 60

    def expand(values: npt.NDArray[np.float64], final: float) -> npt.NDArray[np.float64]:
        return np.concatenate((np.repeat(values, 5), np.array([final], dtype=np.float64)))

    return (
        keys,
        expand(opened, 100.0),
        expand(high, 101.0),
        expand(low, 99.0),
        expand(close, 100.0),
        expand(volume / 5.0, 20.0),
        event_position,
        (295 + shift) * 5,
    )


def _run(
    providers: Sequence[object],
    *,
    frames: Sequence[MarketFrame] | None = None,
    eval_start: int = 0,
    eval_end: int = 5,
    short_context_accepted: bool = False,
    expected_eval_keys: npt.NDArray[np.int64] | None = None,
) -> FeatureFrame:
    bounded_frames = (_frame(tuple(range(eval_end + 1))),) if frames is None else tuple(frames)
    expected = (
        bounded_frames[-1].slice_by_keys(_key(eval_start), _key(eval_end)).keys
        if expected_eval_keys is None
        else expected_eval_keys
    )
    return _execute_features(
        frames=bounded_frames,
        eval_start=_key(eval_start),
        eval_end=_key(eval_end),
        expected_eval_keys=expected,
        providers=cast("Sequence[FeatureProvider]", providers),
        short_context_accepted=short_context_accepted,
    )


class _ProbeProvider:
    def __init__(
        self,
        provider_id: object = "probe",
        *,
        feature_names: object = ("probe",),
        required_columns: object = (),
        window: object = WindowSpec(0, GapPolicy.CROSS),
        params: object = None,
        cost_hint: object = CostHint.CHEAP,
        value: float = 1.0,
        output: Callable[[FeatureRequest], object] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.version: object = 1
        self.feature_names = feature_names
        self.required_columns = required_columns
        self.window = window
        self.params = {} if params is None else params
        self.cost_hint = cost_hint
        self.value = value
        self._output = output
        self.calls = 0
        self.requests: list[FeatureRequest] = []
        self.last_emitted: object | None = None

    def compute(self, request: FeatureRequest) -> object:
        self.calls += 1
        self.requests.append(request)
        if self._output is not None:
            emitted = self._output(request)
        else:
            names = cast("tuple[str, ...]", self.feature_names)
            emitted = {
                name: np.full(request.eval_length, self.value + index, dtype=np.float64)
                for index, name in enumerate(names)
            }
        self.last_emitted = emitted
        return emitted


def _bounded_view(
    frames: Sequence[MarketFrame],
    *,
    eval_start: int,
    eval_end: int,
    accept_short_context: bool = False,
) -> BoundedView:
    segment_frames = tuple(frames)
    resolved_start = int(segment_frames[0].keys[0])
    if accept_short_context:
        requested = _key(resolved_start - 10)
        resolved = _key(resolved_start)
        shortfall = ContextShortfall(
            requested_context_start=requested,
            resolved_context_start=resolved,
            reason_code="synthetic-short-history",
            detail="The test explicitly accepts the synthetic history shortfall.",
            accepted=True,
        )
    else:
        requested = _key(resolved_start)
        resolved = requested
        shortfall = None
    context = ContextResolution._resolved(
        requested_context_start=requested,
        resolved_context_start=resolved,
        shortfall=shortfall,
    )
    return BoundedView._from_bounded(
        segments=tuple(BoundedSegment._from_bounded(frame) for frame in segment_frames),
        eval_frame=segment_frames[-1].slice_by_keys(_key(eval_start), _key(eval_end)),
        context=context,
        context_start=requested,
        eval_start=_key(eval_start),
        eval_end=_key(eval_end),
    )


class TestReferenceProviders:
    def test_exact_declarations_and_hand_computed_goldens(self) -> None:
        frame = _frame(
            (0, 1, 2, 3, 4),
            close=(1.0, 2.0, 4.0, 8.0, 16.0),
            high=(2.0, 5.0, 9.0, 10.0, 20.0),
            low=(0.0, 1.0, 3.0, 12.0, 14.0),
        )
        log_return = LogReturn1()
        rolling = RollingMeanClose(window=3)
        bar_range = BarRange()

        assert (
            log_return.provider_id,
            log_return.version,
            log_return.feature_names,
            log_return.required_columns,
            log_return.params,
            log_return.window,
            log_return.cost_hint,
        ) == (
            "log_return_1",
            1,
            ("log_return_1",),
            ("close",),
            {},
            WindowSpec(1, GapPolicy.CROSS),
            CostHint.CHEAP,
        )
        assert rolling.params == {"window": 3}
        assert rolling.window == WindowSpec(2, GapPolicy.CROSS)
        assert rolling.required_columns == ("close",)
        assert rolling.cost_hint is CostHint.CHEAP
        assert (
            bar_range.provider_id,
            bar_range.feature_names,
            bar_range.required_columns,
            bar_range.window,
            bar_range.cost_hint,
        ) == (
            "bar_range",
            ("bar_range",),
            ("high", "low"),
            WindowSpec(0, GapPolicy.CROSS),
            CostHint.CHEAP,
        )

        log_values = log_return.compute(
            _request(
                (frame,),
                eval_start=0,
                eval_end=4,
                required_columns=log_return.required_columns,
            )
        )["log_return_1"]
        rolling_values = rolling.compute(
            _request(
                (frame,),
                eval_start=0,
                eval_end=4,
                required_columns=rolling.required_columns,
            )
        )["rolling_mean_close"]
        range_values = bar_range.compute(
            _request(
                (frame,),
                eval_start=0,
                eval_end=4,
                required_columns=bar_range.required_columns,
            )
        )["bar_range"]

        np.testing.assert_array_equal(
            log_values,
            np.array(
                [
                    np.nan,
                    float.fromhex("0x1.62e42fefa39efp-1"),
                    float.fromhex("0x1.62e42fefa39efp-1"),
                    float.fromhex("0x1.62e42fefa39efp-1"),
                    float.fromhex("0x1.62e42fefa39efp-1"),
                ],
                dtype=np.float64,
            ),
        )
        np.testing.assert_array_equal(
            rolling_values,
            np.array(
                [
                    np.nan,
                    np.nan,
                    float.fromhex("0x1.2aaaaaaaaaaabp+1"),
                    float.fromhex("0x1.2aaaaaaaaaaabp+2"),
                    float.fromhex("0x1.2aaaaaaaaaaabp+3"),
                ],
                dtype=np.float64,
            ),
        )
        np.testing.assert_array_equal(
            range_values,
            np.array([2.0, 4.0, 6.0, -2.0, 6.0], dtype=np.float64),
        )
        assert log_values.dtype == rolling_values.dtype == range_values.dtype == np.float64

    @pytest.mark.parametrize("window", [False, 0, -1, 1.5])
    def test_rolling_window_must_be_a_positive_exact_integer(self, window: object) -> None:
        with pytest.raises(FeatureContractError, match="rolling window"):
            RollingMeanClose(window=cast("int", window))


class TestZoneTraderPrivatePrimitives:
    def test_m1_to_m5_full_bucket_has_literal_ohlcv_reductions(self) -> None:
        keys = np.array([0, 60, 120, 180, 240, 300], dtype=np.int64)
        opened = _float_vector((10.0, 11.0, 12.0, 13.0, 14.0, 20.0))
        high = _float_vector((12.0, 15.0, 14.0, 18.0, 17.0, 21.0))
        low = _float_vector((9.0, 8.0, 10.0, 7.0, 11.0, 19.0))
        close = _float_vector((11.0, 12.0, 13.0, 14.0, 16.0, 20.5))
        volume = _float_vector((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))

        bars = _aggregate_complete_strategy_bars(
            keys,
            opened,
            high,
            low,
            close,
            volume,
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
        )

        np.testing.assert_array_equal(bars.bucket_numbers, np.array([0], dtype=np.int64))
        np.testing.assert_array_equal(bars.usable_positions, np.array([5], dtype=np.int64))
        np.testing.assert_array_equal(bars.opened, _float_vector((10.0,)))
        np.testing.assert_array_equal(bars.high, _float_vector((18.0,)))
        np.testing.assert_array_equal(bars.low, _float_vector((7.0,)))
        np.testing.assert_array_equal(bars.close, _float_vector((16.0,)))
        np.testing.assert_array_equal(bars.tick_volume, _float_vector((15.0,)))

    def test_m1_to_m5_gapped_and_missing_buckets_are_observation_only(self) -> None:
        keys = np.array([0, 120, 600, 660, 900], dtype=np.int64)
        values = _float_vector((1.0, 2.0, 3.0, 4.0, 5.0))
        bars = _aggregate_complete_strategy_bars(
            keys,
            values,
            values + 10.0,
            values - 10.0,
            values + 0.5,
            values * 10.0,
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
        )

        np.testing.assert_array_equal(
            bars.bucket_numbers,
            np.array([0, 2], dtype=np.int64),
        )
        np.testing.assert_array_equal(
            bars.usable_positions,
            np.array([2, 4], dtype=np.int64),
        )
        np.testing.assert_array_equal(bars.opened, _float_vector((1.0, 3.0)))
        np.testing.assert_array_equal(bars.high, _float_vector((12.0, 14.0)))
        np.testing.assert_array_equal(bars.low, _float_vector((-9.0, -7.0)))
        np.testing.assert_array_equal(bars.close, _float_vector((2.5, 4.5)))
        np.testing.assert_array_equal(bars.tick_volume, _float_vector((30.0, 70.0)))

    def test_m1_to_m5_never_emits_the_partial_final_bucket(self) -> None:
        keys = np.array([0, 60, 120, 180, 240], dtype=np.int64)
        values = _float_vector((1.0, 2.0, 3.0, 4.0, 5.0))

        bars = _aggregate_complete_strategy_bars(
            keys,
            values,
            values,
            values,
            values,
            values,
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
        )

        assert bars.bucket_numbers.size == 0
        assert bars.usable_positions.size == 0
        assert bars.opened.size == bars.high.size == bars.low.size == 0
        assert bars.close.size == bars.tick_volume.size == 0

    def test_zone_trader_grid_validation_requires_aligned_explicit_phases(self) -> None:
        _validate_zone_trader_grids(
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=3600,
            anchor_phase_offset_seconds=0,
        )

        with pytest.raises(FeatureContractError, match="align"):
            _validate_zone_trader_grids(
                bar_grid_seconds=300,
                bar_phase_offset_seconds=0,
                anchor_grid_seconds=3600,
                anchor_phase_offset_seconds=60,
            )
        with pytest.raises(FeatureContractError, match="exact multiple"):
            _validate_zone_trader_grids(
                bar_grid_seconds=300,
                bar_phase_offset_seconds=0,
                anchor_grid_seconds=3500,
                anchor_phase_offset_seconds=0,
            )

    def test_finite_wilder_atr_has_a_hand_computed_eight_bar_golden(self) -> None:
        high = _float_vector((10.0, 12.0, 15.0, 14.0, 16.0, 18.0, 17.0, 20.0))
        low = _float_vector((8.0, 9.0, 11.0, 10.0, 13.0, 14.0, 13.0, 16.0))
        close = _float_vector((9.0, 11.0, 12.0, 13.0, 15.0, 15.0, 16.0, 18.0))

        observed = _truncated_wilder_atr(high, low, close, 3)
        expected = np.array(
            [
                np.nan,
                np.nan,
                np.nan,
                float.fromhex("0x1.91a1f58d0fac6p+1"),
                float.fromhex("0x1.f6db6db6db6dcp+1"),
                float.fromhex("0x1.f7829cbc14e5ep+1"),
                float.fromhex("0x1.91a1f58d0fac6p+1"),
                float.fromhex("0x1.0000000000000p+2"),
            ],
            dtype=np.float64,
        )

        np.testing.assert_array_equal(observed, expected)
        assert observed.dtype == np.float64
        assert observed.shape == (8,)
        assert not np.isinf(observed).any()

    def test_finite_wilder_atr_reaches_exactly_w_atr_prior_bars(self) -> None:
        close = np.arange(304, dtype=np.float64) * 10.0
        high = close + 1.0
        low = close - 1.0
        baseline = _truncated_wilder_atr(high, low, close, 300)

        at_reach = close.copy()
        at_reach[1] -= 50.0
        changed = _truncated_wilder_atr(high, low, at_reach, 300)
        before_reach = close.copy()
        before_reach[0] -= 50.0
        unchanged = _truncated_wilder_atr(high, low, before_reach, 300)

        assert baseline[301].view(np.uint64) != changed[301].view(np.uint64)
        assert baseline[301].view(np.uint64) == unchanged[301].view(np.uint64)

    def test_zone_trader_default_declaration_is_exact_and_identity_bearing(self) -> None:
        provider = ZoneTraderV13()

        assert (
            provider.provider_id,
            provider.version,
            provider.feature_names,
            provider.required_columns,
            provider.window,
            provider.cost_hint,
        ) == (
            "zone_trader_v13",
            1,
            (
                "zt_event_signed",
                "zt_entry_price",
                "zt_stop_price",
                "zt_target_price",
                "zt_zone_top",
                "zt_zone_bottom",
                "zt_zone_kind",
                "zt_candidate_open",
            ),
            ("open", "high", "low", "close", "tick_volume"),
            WindowSpec(26_534, GapPolicy.CROSS),
            CostHint.EXPENSIVE,
        )
        assert provider.params == {
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
            "use_demand_supply": True,
            "use_imbalance": True,
            "use_volume_zones": True,
            "vol_len": 20,
            "vol_mult": 2.0,
            "w_atr": 300,
        }
        changed = ZoneTraderV13(max_zone_age_rows=4999)
        assert changed.params != provider.params
        assert changed.window != provider.window

    def test_multi_confluence_default_declaration_is_exact_and_recreatable(self) -> None:
        provider = MultiConfluenceM15V1()

        assert (
            provider.provider_id,
            provider.version,
            provider.feature_names,
            provider.required_columns,
            provider.window,
            provider.cost_hint,
        ) == (
            "multi_confluence_m15_v1",
            1,
            ("mc_event_signed", "mc_long_score", "mc_short_score"),
            ("high", "low", "close"),
            WindowSpec(645_120, GapPolicy.CROSS),
            CostHint.EXPENSIVE,
        )
        assert provider.params == {
            "atr_length": 14,
            "base_grid_seconds": 900,
            "base_phase_offset_seconds": 0,
            "ema_fast_length": 50,
            "ema_slow_length": 200,
            "ema_window": 2_000,
            "level_atr_range": 0.60,
            "level_grid_seconds": (14_400, 21_600, 28_800, 43_200, 604_800),
            "level_phase_offset_seconds": (0, 0, 0, 0, 345_600),
            "liquidity_lookback": 20,
            "max_level_age_bars": 52,
            "max_trend_age_bars": 512,
            "minimum_score": 4,
            "pivot_left": 5,
            "pivot_right": 5,
            "rma_window": 300,
            "rsi_length": 14,
            "rsi_long_level": 45,
            "rsi_short_level": 55,
            "trend_atr_range": 0.50,
        }
        recreated = FEATURE_PROVIDERS.create(
            "multi_confluence_m15_v1",
            **dict(provider.params),
        )
        assert type(recreated) is MultiConfluenceM15V1
        assert recreated.params == provider.params
        assert recreated.window == provider.window
        assert MultiConfluenceM15V1(max_level_age_bars=51).window == WindowSpec(
            635_040,
            GapPolicy.CROSS,
        )

    def test_gap_sensitive_default_declaration_is_exact_and_recreatable(self) -> None:
        provider = GapSensitiveLevelsH1V1()

        assert (
            provider.provider_id,
            provider.version,
            provider.feature_names,
            provider.required_columns,
            provider.window,
            provider.cost_hint,
        ) == (
            "gap_sensitive_levels_h1_v1",
            1,
            (
                "gsl_event_signed",
                "gsl_long_score",
                "gsl_short_score",
                "gsl_conflict",
            ),
            ("high", "low", "close"),
            WindowSpec(120_780, GapPolicy.CROSS),
            CostHint.EXPENSIVE,
        )
        assert provider.params == {
            "bar_grid_seconds": 3_600,
            "bar_phase_offset_seconds": 0,
            "ema_fast_length": 20,
            "ema_slow_length": 50,
            "ema_window": 2_000,
            "max_gap_age_bars": 2_000,
            "max_level_age_bars": 2_000,
            "min_confirm": 2,
            "min_gap_pct": 0.10,
            "rma_window": 300,
            "rsi_length": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "swing_length": 5,
        }
        recreated = FEATURE_PROVIDERS.create(
            "gap_sensitive_levels_h1_v1",
            **dict(provider.params),
        )
        assert type(recreated) is GapSensitiveLevelsH1V1
        assert recreated.params == provider.params
        assert recreated.window == provider.window

    def test_gap_sensitive_emits_only_after_confirmed_support_touch(self) -> None:
        opened = np.array([9.5, 12.5, 13.5, 13.0, 12.8, 13.5, 13.8, 14.0])
        high = np.array([10.0, 13.0, 14.0, 13.8, 13.5, 14.0, 14.2, 14.2])
        low = np.array([9.0, 12.0, 13.0, 12.8, 12.2, 13.0, 13.2, 12.2])
        close = np.array([9.5, 12.5, 13.5, 13.0, 12.8, 13.5, 13.8, 14.0])
        bars = _strategy_bars(opened=opened, high=high, low=low, close=close)
        provider = GapSensitiveLevelsH1V1(
            swing_length=2,
            rsi_length=2,
            ema_fast_length=2,
            ema_slow_length=3,
            rma_window=3,
            ema_window=3,
            max_gap_age_bars=20,
            max_level_age_bars=20,
        )

        observed = _gsl_strategy_values(bars, provider._params)

        assert observed.event_signed.tolist() == [0.0] * 7 + [1.0]
        assert observed.long_score[-1] >= 2.0
        assert observed.conflict.tolist() == [0.0] * 8

    def test_gap_sensitive_clears_a_filled_gap_before_signal_evaluation(self) -> None:
        opened = np.array([9.5, 12.5, 13.5, 13.0, 12.8, 13.5, 13.8, 14.0])
        high = np.array([10.0, 13.0, 14.0, 13.8, 13.5, 14.0, 14.2, 14.2])
        low = np.array([9.0, 12.0, 13.0, 12.8, 12.2, 13.0, 13.2, 10.0])
        close = np.array([9.5, 12.5, 13.5, 13.0, 12.8, 13.5, 13.8, 14.0])
        bars = _strategy_bars(opened=opened, high=high, low=low, close=close)
        provider = GapSensitiveLevelsH1V1(
            swing_length=2,
            rsi_length=2,
            ema_fast_length=2,
            ema_slow_length=3,
            rma_window=3,
            ema_window=3,
            max_gap_age_bars=20,
            max_level_age_bars=20,
        )

        observed = _gsl_strategy_values(bars, provider._params)

        assert observed.event_signed.tolist() == [0.0] * 8

    def test_multi_confluence_pivots_confirm_then_expire_at_the_declared_age(self) -> None:
        values = np.array([1.0, 2.0, 5.0, 2.0, 1.0, 0.0, -1.0, -2.0])

        observed = _mc_confirmed_level_state(
            values,
            left=2,
            right=2,
            max_age=2,
            high=True,
        )

        np.testing.assert_array_equal(
            observed,
            np.array([np.nan, np.nan, np.nan, np.nan, 5.0, 5.0, 5.0, np.nan]),
        )

    def test_multi_confluence_trendline_uses_only_two_confirmed_pivots(self) -> None:
        high = np.array([1.0, 2.0, 5.0, 2.0, 1.0, 2.0, 4.0, 2.0])
        low = np.array([0.0, -1.0, 0.0, -2.0, 0.0, -1.0, 0.0, -2.0])

        support, resistance = _mc_trendline_prices(
            high,
            low,
            left=1,
            right=1,
            max_age=20,
        )

        assert resistance[7] == 3.75
        assert support[6] == -0.5

    def test_multi_confluence_emits_on_the_first_exact_four_of_five_bar(self) -> None:
        close = np.array([1.0, 2.0, 1.5, 2.5, 4.0, 4.5])
        high = close + 0.2
        low = close - 0.2
        low[4] = 1.0
        bars = _strategy_bars(
            opened=close - 0.1,
            high=high,
            low=low,
            close=close,
        )
        provider = MultiConfluenceM15V1(
            pivot_left=2,
            pivot_right=2,
            rsi_length=2,
            ema_fast_length=2,
            ema_slow_length=3,
            atr_length=2,
            liquidity_lookback=2,
            rma_window=3,
            ema_window=3,
            level_grid_seconds=(1_800,),
            level_phase_offset_seconds=(0,),
        )
        supports = (close.copy(),)
        resistances = (np.full(close.size, np.nan),)

        observed = _mc_strategy_values(
            bars,
            supports,
            resistances,
            provider._params,
        )

        assert observed.long_score.tolist() == [0.0, 0.0, 1.0, 2.0, 4.0, 3.0]
        assert observed.short_score.tolist() == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        assert observed.event_signed.tolist() == [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    def test_candle_geometry_preserves_zero_range_and_literal_wicks(self) -> None:
        bars = _strategy_bars(
            opened=(10.0, 5.0),
            high=(10.0, 7.0),
            low=(10.0, 4.0),
            close=(10.0, 6.0),
        )
        primitives = _zone_trader_primitives(
            bars,
            _float_vector((1.0, 1.0)),
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=3600,
            anchor_phase_offset_seconds=0,
            vol_len=20,
            vol_mult=2.0,
            min_fvg_atr=0.25,
            max_base_body=0.5,
            max_base_atr=0.8,
            min_move_atr=1.2,
            use_volume_zones=False,
            use_imbalance=False,
            use_demand_supply=False,
        )

        np.testing.assert_array_equal(primitives.bar_range, _float_vector((0.0, 3.0)))
        np.testing.assert_array_equal(
            primitives.upper_wick,
            _float_vector((0.0, 1.0 / 3.0)),
        )
        np.testing.assert_array_equal(
            primitives.lower_wick,
            _float_vector((0.0, 1.0 / 3.0)),
        )

    def test_fvg_and_demand_supply_primitives_match_literal_geometry(self) -> None:
        bars = _strategy_bars(
            opened=(9.2, 10.0, 11.2, 8.0),
            high=(10.0, 12.5, 12.0, 8.0),
            low=(9.0, 9.5, 11.0, 7.0),
            close=(9.4, 12.0, 11.5, 7.5),
        )
        atr = _float_vector((2.0, 2.0, 2.0, 2.0))
        primitives = _zone_trader_primitives(
            bars,
            atr,
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=3600,
            anchor_phase_offset_seconds=0,
            vol_len=20,
            vol_mult=2.0,
            min_fvg_atr=0.25,
            max_base_body=0.5,
            max_base_atr=0.8,
            min_move_atr=1.2,
            use_volume_zones=False,
            use_imbalance=True,
            use_demand_supply=True,
        )

        assert primitives.is_base[1]
        assert primitives.impulse_up[1]
        assert primitives.demand_supply_top[1] == 10.0
        assert primitives.demand_supply_bottom[1] == 9.0
        assert primitives.bull_fvg[2]
        assert primitives.fvg_size_ok[2]
        assert primitives.fvg_top[2] == 11.0
        assert primitives.fvg_bottom[2] == 10.0
        assert primitives.bear_fvg[3]
        assert primitives.fvg_size_ok[3]
        assert primitives.fvg_top[3] == 9.5
        assert primitives.fvg_bottom[3] == 8.0

        too_small = _zone_trader_primitives(
            bars,
            _float_vector((2.0, 2.0, 8.0, 8.0)),
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=3600,
            anchor_phase_offset_seconds=0,
            vol_len=20,
            vol_mult=2.0,
            min_fvg_atr=0.25,
            max_base_body=0.5,
            max_base_atr=0.8,
            min_move_atr=1.2,
            use_volume_zones=False,
            use_imbalance=True,
            use_demand_supply=False,
        )
        assert too_small.bull_fvg[2]
        assert not too_small.fvg_size_ok[2]
        assert np.isnan(too_small.fvg_top[2])

        smaller_current_atr = atr.copy()
        smaller_current_atr[1] = 1.0
        changed = _zone_trader_primitives(
            bars,
            smaller_current_atr,
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=3600,
            anchor_phase_offset_seconds=0,
            vol_len=20,
            vol_mult=2.0,
            min_fvg_atr=0.25,
            max_base_body=0.5,
            max_base_atr=0.8,
            min_move_atr=1.2,
            use_volume_zones=False,
            use_imbalance=True,
            use_demand_supply=True,
        )
        assert not changed.is_base[1]

    def test_base_and_impulse_rejection_cases_are_literal(self) -> None:
        def classify(
            *,
            opened: tuple[float, float],
            high: tuple[float, float],
            low: tuple[float, float],
            close: tuple[float, float],
            atr: tuple[float, float] = (1.0, 1.0),
        ) -> _ZoneTraderPrimitives:
            return _zone_trader_primitives(
                _strategy_bars(opened=opened, high=high, low=low, close=close),
                _float_vector(atr),
                bar_grid_seconds=300,
                bar_phase_offset_seconds=0,
                anchor_grid_seconds=3600,
                anchor_phase_offset_seconds=0,
                vol_len=20,
                vol_mult=2.0,
                min_fvg_atr=100.0,
                max_base_body=0.5,
                max_base_atr=0.8,
                min_move_atr=1.2,
                use_volume_zones=False,
                use_imbalance=False,
                use_demand_supply=True,
            )

        body_failure = classify(
            opened=(9.0, 10.0),
            high=(11.0, 13.0),
            low=(9.0, 10.0),
            close=(10.5, 13.0),
            atr=(1.0, 3.0),
        )
        range_failure = classify(
            opened=(9.5, 10.0),
            high=(11.0, 13.0),
            low=(9.0, 10.0),
            close=(10.0, 13.0),
        )
        zero_range = classify(
            opened=(10.0, 10.0),
            high=(10.0, 13.0),
            low=(10.0, 10.0),
            close=(10.0, 13.0),
            atr=(1.0, 3.0),
        )
        bullish = classify(
            opened=(9.5, 10.0),
            high=(10.0, 13.0),
            low=(9.0, 10.0),
            close=(9.5, 13.0),
            atr=(1.0, 2.0),
        )
        bearish = classify(
            opened=(9.5, 9.0),
            high=(10.0, 9.0),
            low=(9.0, 6.0),
            close=(9.5, 6.0),
            atr=(1.0, 2.0),
        )
        short_move = classify(
            opened=(9.5, 10.0),
            high=(10.0, 11.0),
            low=(9.0, 10.0),
            close=(9.5, 10.8),
        )
        no_close_through = classify(
            opened=(9.5, 8.0),
            high=(10.0, 11.0),
            low=(9.0, 8.0),
            close=(9.5, 9.9),
            atr=(1.0, 2.0),
        )

        assert not body_failure.is_base[1]
        assert not range_failure.is_base[1]
        assert not zero_range.is_base[1]
        assert bullish.is_base[1]
        assert bullish.impulse_up[1]
        assert bearish.is_base[1]
        assert bearish.impulse_down[1]
        assert not short_move.impulse_up[1]
        assert not no_close_through.impulse_up[1]

    def test_anchor_average_excludes_tested_anchor_and_volume_tie_uses_recent_bar(
        self,
    ) -> None:
        bars = _strategy_bars(
            opened=(1.0,) * 7,
            high=(2.0, 3.0, 4.0, 5.0, 40.0, 50.0, 6.0),
            low=(0.0,) * 7,
            close=(1.0,) * 7,
            tick_volume=(5.0, 5.0, 5.0, 5.0, 10.5, 10.5, 1.0),
        )
        primitives = _zone_trader_primitives(
            bars,
            _float_vector((1.0,) * 7),
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=600,
            anchor_phase_offset_seconds=0,
            vol_len=2,
            vol_mult=2.0,
            min_fvg_atr=100.0,
            max_base_body=0.0,
            max_base_atr=0.0,
            min_move_atr=100.0,
            use_volume_zones=True,
            use_imbalance=False,
            use_demand_supply=False,
        )

        assert primitives.anchor_signal.tolist() == [False] * 6 + [True]
        assert primitives.volume_top[6] == 50.0
        assert primitives.volume_bottom[6] == 0.0

        equal_volume = _strategy_bars(
            opened=(1.0,) * 7,
            high=(2.0,) * 7,
            low=(0.0,) * 7,
            close=(1.0,) * 7,
            tick_volume=(5.0, 5.0, 5.0, 5.0, 10.0, 10.0, 1.0),
        )
        equality = _zone_trader_primitives(
            equal_volume,
            _float_vector((1.0,) * 7),
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=600,
            anchor_phase_offset_seconds=0,
            vol_len=2,
            vol_mult=2.0,
            min_fvg_atr=100.0,
            max_base_body=0.0,
            max_base_atr=0.0,
            min_move_atr=100.0,
            use_volume_zones=True,
            use_imbalance=False,
            use_demand_supply=False,
        )
        assert not equality.anchor_signal[6]

    def test_volume_zone_scan_retains_the_500_bar_cap(self) -> None:
        size = 1005
        volume = np.ones(size, dtype=np.float64)
        volume[502] = 1000.0
        volume[503] = 900.0
        high = np.arange(size, dtype=np.float64) + 10.0
        bars = _strategy_bars(
            opened=np.ones(size, dtype=np.float64),
            high=high,
            low=np.zeros(size, dtype=np.float64),
            close=np.ones(size, dtype=np.float64),
            tick_volume=volume,
        )
        primitives = _zone_trader_primitives(
            bars,
            np.ones(size, dtype=np.float64),
            bar_grid_seconds=300,
            bar_phase_offset_seconds=0,
            anchor_grid_seconds=150_600,
            anchor_phase_offset_seconds=0,
            vol_len=2,
            vol_mult=2.0,
            min_fvg_atr=100.0,
            max_base_body=0.0,
            max_base_atr=0.0,
            min_move_atr=100.0,
            use_volume_zones=True,
            use_imbalance=False,
            use_demand_supply=False,
        )

        assert primitives.anchor_signal[1004]
        assert primitives.volume_top[1004] == high[1004 - 1]

    def test_zone_cannot_fire_on_creation_and_can_fire_after_leave_and_reentry(
        self,
    ) -> None:
        primitives = _state_primitives(
            3,
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
        )
        output = _run_state_fixture(
            opened=(9.5, 10.2, 9.5),
            high=(10.0, 10.4, 9.8),
            low=(9.0, 10.2, 9.2),
            close=(9.5, 10.2, 9.5),
            atr=(1.0, 1.0, 1.0),
            primitives=primitives,
            break_buffer_atr=2.0,
        )

        np.testing.assert_array_equal(output.event_signed, _float_vector((0.0, 0.0, 1.0)))
        assert output.zone_top[2] == 10.0
        assert output.zone_bottom[2] == 9.0
        assert output.zone_kind[2] == 0.0

    def test_surviving_zone_can_fire_twice_after_two_distinct_reentries(self) -> None:
        output = _run_state_fixture(
            opened=(9.5, 10.2, 9.5, 10.2, 9.5),
            high=(10.0, 10.4, 9.8, 10.4, 9.8),
            low=(9.0, 10.2, 9.2, 10.2, 9.2),
            close=(9.5, 10.2, 9.5, 10.2, 9.5),
            atr=(1.0,) * 5,
            primitives=_state_primitives(
                5,
                volume_top={0: 10.0},
                volume_bottom={0: 9.0},
            ),
            break_buffer_atr=2.0,
        )

        np.testing.assert_array_equal(
            output.event_signed,
            _float_vector((0.0, 0.0, 1.0, 0.0, 1.0)),
        )

    def test_demand_supply_zone_creation_preserves_kind_and_geometry(self) -> None:
        output = _run_state_fixture(
            opened=(9.5, 10.2, 9.5),
            high=(10.0, 10.4, 9.8),
            low=(9.0, 10.2, 9.2),
            close=(9.5, 10.2, 9.5),
            atr=(1.0,) * 3,
            primitives=_state_primitives(
                3,
                demand_supply_top={0: 10.0},
                demand_supply_bottom={0: 9.0},
            ),
            break_buffer_atr=2.0,
        )

        assert output.event_signed[2] == 1.0
        assert output.zone_top[2] == 10.0
        assert output.zone_bottom[2] == 9.0
        assert output.zone_kind[2] == 2.0

    def test_equal_score_candidate_tie_is_won_by_the_older_zone(self) -> None:
        primitives = _state_primitives(
            4,
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
            fvg_top={1: 12.0},
            fvg_bottom={1: 11.0},
        )
        output = _run_state_fixture(
            opened=(9.5, 11.5, 12.5, 10.5),
            high=(10.0, 12.0, 13.0, 12.0),
            low=(9.0, 11.0, 12.5, 9.0),
            close=(9.5, 11.5, 12.5, 10.5),
            atr=(1.0,) * 4,
            primitives=primitives,
            break_buffer_atr=100.0,
        )

        assert output.event_signed[3] == 1.0
        assert output.zone_top[3] == 10.0
        assert output.zone_kind[3] == 0.0

    def test_candidate_direction_uses_previous_close_and_straddle_is_long(self) -> None:
        output = _run_state_fixture(
            opened=(9.5, 20.0, 9.5),
            high=(10.0, 20.5, 9.8),
            low=(9.0, 20.0, 9.2),
            close=(9.5, 9.5, 9.5),
            atr=(1.0,) * 3,
            primitives=_state_primitives(
                3,
                fvg_top={0: 10.0},
                fvg_bottom={0: 9.0},
            ),
            break_buffer_atr=100.0,
        )

        assert output.event_signed[2] == 1.0

    def test_zone_break_is_strict_and_prevents_same_bar_candidate(self) -> None:
        primitives = _state_primitives(
            3,
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
        )
        equality = _run_state_fixture(
            opened=(9.5, 10.1, 9.5),
            high=(10.0, 10.2, 9.8),
            low=(9.0, 10.1, 9.2),
            close=(9.5, 10.1, 9.5),
            atr=(0.2, 0.2, 0.2),
            primitives=primitives,
            break_buffer_atr=0.5,
        )
        beyond = _run_state_fixture(
            opened=(9.5, 10.2, 9.5),
            high=(10.0, 10.3, 9.8),
            low=(9.0, 10.2, 9.2),
            close=(9.5, 10.2, 9.5),
            atr=(0.2, 0.2, 0.2),
            primitives=primitives,
            break_buffer_atr=0.5,
        )

        assert equality.event_signed[2] == 1.0
        assert not beyond.event_signed.any()

    def test_pending_candidate_confirms_later_without_zone_overlap(self) -> None:
        primitives = _state_primitives(
            5,
            lower_wick=(0.0, 0.0, 0.0, 0.0, 0.5),
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
        )
        output = _run_state_fixture(
            opened=(9.5, 10.2, 9.5, 10.5, 11.0),
            high=(10.0, 10.4, 9.8, 10.8, 11.2),
            low=(9.0, 10.2, 9.2, 10.3, 10.7),
            close=(9.5, 10.2, 9.5, 10.5, 11.0),
            atr=(1.0, 1.0, 10.0, 10.0, 2.0),
            primitives=primitives,
            break_buffer_atr=100.0,
            confirmation_mode="rejection_wick",
        )

        assert output.candidate_open.tolist() == [0.0, 0.0, 1.0, 1.0, 0.0]
        assert output.event_signed[4] == 1.0
        assert output.entry_price[4] == 11.0
        assert output.stop_price[4] == 8.0
        assert output.target_price[4] == 17.0

    def test_new_candidate_overwrites_pending_and_resets_its_wait(self) -> None:
        primitives = _state_primitives(
            5,
            upper_wick=(0.0, 0.0, 0.0, 0.0, 0.5),
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
            fvg_top={1: 12.0},
            fvg_bottom={1: 11.0},
        )
        output = _run_state_fixture(
            opened=(9.5, 10.3, 9.5, 11.5, 11.5),
            high=(10.0, 10.4, 9.8, 11.8, 12.0),
            low=(9.0, 10.2, 9.2, 11.2, 11.0),
            close=(9.5, 10.3, 9.5, 11.5, 11.5),
            atr=(1.0,) * 5,
            primitives=primitives,
            break_buffer_atr=100.0,
            confirmation_mode="rejection_wick",
            max_bars_wait=2,
        )

        assert output.candidate_open.tolist() == [0.0, 0.0, 1.0, 1.0, 0.0]
        assert output.event_signed[4] == -1.0
        assert output.zone_top[4] == 12.0
        assert output.zone_bottom[4] == 11.0
        assert output.zone_kind[4] == 1.0

    def test_close_back_outside_mode_confirms_after_the_touch_bar(self) -> None:
        output = _run_state_fixture(
            opened=(9.5, 10.2, 9.5, 10.2),
            high=(10.0, 10.4, 9.8, 10.4),
            low=(9.0, 10.2, 9.2, 10.1),
            close=(9.5, 10.2, 9.5, 10.2),
            atr=(1.0,) * 4,
            primitives=_state_primitives(
                4,
                volume_top={0: 10.0},
                volume_bottom={0: 9.0},
            ),
            break_buffer_atr=100.0,
            confirmation_mode="close_back_outside",
        )

        assert output.candidate_open.tolist() == [0.0, 0.0, 1.0, 0.0]
        assert output.event_signed[2] == 0.0
        assert output.event_signed[3] == 1.0

    def test_count_eviction_removes_exact_excess_and_runs_before_scan(self) -> None:
        same_bar = _run_state_fixture(
            opened=(9.5, 10.2, 9.5),
            high=(10.0, 10.4, 9.8),
            low=(9.0, 10.2, 9.2),
            close=(9.5, 10.2, 9.5),
            atr=(1.0,) * 3,
            primitives=_state_primitives(
                3,
                volume_top={0: 10.0},
                volume_bottom={0: 9.0},
                fvg_top={0: 10.0},
                fvg_bottom={0: 9.0},
                demand_supply_top={0: 10.0},
                demand_supply_bottom={0: 9.0},
            ),
            max_zones=2,
            break_buffer_atr=100.0,
        )
        evicted_before_touch = _run_state_fixture(
            opened=(9.5, 10.2, 9.5),
            high=(10.0, 10.4, 9.8),
            low=(9.0, 10.2, 9.2),
            close=(9.5, 10.2, 9.5),
            atr=(1.0,) * 3,
            primitives=_state_primitives(
                3,
                volume_top={0: 10.0},
                volume_bottom={0: 9.0},
                fvg_top={2: 21.0},
                fvg_bottom={2: 20.0},
                demand_supply_top={2: 31.0},
                demand_supply_bottom={2: 30.0},
            ),
            max_zones=2,
            break_buffer_atr=100.0,
        )

        assert same_bar.retained_zone_count.tolist() == [2, 2, 2]
        assert same_bar.event_signed[2] == 1.0
        assert same_bar.zone_kind[2] == 1.0
        assert evicted_before_touch.retained_zone_count[2] == 2
        assert evicted_before_touch.event_signed[2] == 0.0

    def test_broken_zone_keeps_its_retained_slot(self) -> None:
        output = _run_state_fixture(
            opened=(9.5, 11.0, 11.0),
            high=(10.0, 11.2, 11.2),
            low=(9.0, 10.8, 10.8),
            close=(9.5, 11.0, 11.0),
            atr=(1.0,) * 3,
            primitives=_state_primitives(
                3,
                volume_top={0: 10.0},
                volume_bottom={0: 9.0},
            ),
        )

        assert output.zone_break_count.tolist() == [0, 1, 0]
        assert output.retained_zone_count.tolist() == [1, 1, 1]

    def test_final_wait_bar_still_gets_confirmation_before_timeout(self) -> None:
        primitives = _state_primitives(
            8,
            lower_wick=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5),
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
        )
        confirmed = _run_state_fixture(
            opened=(9.5, 10.2, 9.5, 10.5, 10.5, 10.5, 10.5, 10.5),
            high=(10.0, 10.4, 9.8, 10.8, 10.8, 10.8, 10.8, 10.8),
            low=(9.0, 10.2, 9.2, 10.3, 10.3, 10.3, 10.3, 10.3),
            close=(9.5, 10.2, 9.5, 10.5, 10.5, 10.5, 10.5, 10.5),
            atr=(1.0,) * 8,
            primitives=primitives,
            break_buffer_atr=100.0,
            confirmation_mode="rejection_wick",
            max_bars_wait=6,
        )
        timed_out_primitives = _state_primitives(
            8,
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
        )
        timed_out = _run_state_fixture(
            opened=(9.5, 10.2, 9.5, 10.5, 10.5, 10.5, 10.5, 10.5),
            high=(10.0, 10.4, 9.8, 10.8, 10.8, 10.8, 10.8, 10.8),
            low=(9.0, 10.2, 9.2, 10.3, 10.3, 10.3, 10.3, 10.3),
            close=(9.5, 10.2, 9.5, 10.5, 10.5, 10.5, 10.5, 10.5),
            atr=(1.0,) * 8,
            primitives=timed_out_primitives,
            break_buffer_atr=100.0,
            confirmation_mode="rejection_wick",
            max_bars_wait=6,
        )

        assert confirmed.event_signed[7] == 1.0
        assert not timed_out.event_signed.any()
        assert timed_out.candidate_open[6] == 1.0
        assert timed_out.candidate_open[7] == 0.0

    def test_descriptive_arithmetic_preserves_degenerate_and_inverted_short_levels(
        self,
    ) -> None:
        long_primitives = _state_primitives(
            3,
            volume_top={0: 10.0},
            volume_bottom={0: 9.0},
        )
        degenerate = _run_state_fixture(
            opened=(9.5, 10.2, 8.5),
            high=(10.0, 10.4, 9.5),
            low=(9.0, 10.2, 8.5),
            close=(9.5, 10.2, 8.5),
            atr=(1.0,) * 3,
            primitives=long_primitives,
            break_buffer_atr=100.0,
        )
        short_primitives = _state_primitives(
            3,
            fvg_top={0: 11.0},
            fvg_bottom={0: 10.0},
        )
        inverted_short = _run_state_fixture(
            opened=(10.5, 9.0, 12.0),
            high=(11.0, 9.5, 12.2),
            low=(10.0, 8.5, 10.5),
            close=(10.5, 9.0, 12.0),
            atr=(1.0,) * 3,
            primitives=short_primitives,
            break_buffer_atr=100.0,
        )

        assert degenerate.entry_price[2] == 8.5
        assert degenerate.stop_price[2] == 8.5
        assert degenerate.target_price[2] == 8.5
        assert inverted_short.event_signed[2] == -1.0
        assert inverted_short.entry_price[2] == 12.0
        assert inverted_short.stop_price[2] == 11.5
        assert inverted_short.target_price[2] == 11.0

    def test_zone_age_5000_is_eligible_and_5001_is_expired(self) -> None:
        def at_age(age: int) -> _ZoneTraderStrategyOutput:
            size = age + 1
            opened = np.full(size, 10.2, dtype=np.float64)
            high = np.full(size, 10.4, dtype=np.float64)
            low = np.full(size, 10.2, dtype=np.float64)
            close = np.full(size, 10.2, dtype=np.float64)
            opened[0] = close[0] = 9.5
            high[0] = 10.0
            low[0] = 9.0
            opened[-1] = close[-1] = 9.5
            high[-1] = 9.8
            low[-1] = 9.2
            return _run_state_fixture(
                opened=opened,
                high=high,
                low=low,
                close=close,
                atr=np.ones(size, dtype=np.float64),
                primitives=_state_primitives(
                    size,
                    volume_top={0: 10.0},
                    volume_bottom={0: 9.0},
                ),
                max_zone_age_rows=5000,
                break_buffer_atr=100.0,
            )

        assert at_age(5000).event_signed[-1] == 1.0
        assert at_age(5001).event_signed[-1] == 0.0

    def test_default_window_masks_exactly_26534_leading_m1_rows(self) -> None:
        provider = ZoneTraderV13()
        keys = tuple(index * 60 for index in range(26_536))
        frame = _frame(keys)

        observed = _run(
            (provider,),
            frames=(frame,),
            eval_start=keys[0],
            eval_end=keys[-1],
            short_context_accepted=True,
        )

        event = observed.column("zt_event_signed")
        candidate = observed.column("zt_candidate_open")
        assert np.isnan(event[:26_534]).all()
        assert np.isnan(candidate[:26_534]).all()
        assert np.isfinite(event[26_534:]).all()
        assert np.isfinite(candidate[26_534:]).all()

    def test_actual_reach_boundary_and_bucket_alignment_are_separate(self) -> None:
        keys, opened, high, low, close, volume, event_position = _zone_reach_m1_fixture()
        provider = ZoneTraderV13(
            use_volume_zones=False,
            use_demand_supply=False,
        )

        def compute(
            *,
            opened_values: npt.NDArray[np.float64] = opened,
            high_values: npt.NDArray[np.float64] = high,
            low_values: npt.NDArray[np.float64] = low,
            close_values: npt.NDArray[np.float64] = close,
            volume_values: npt.NDArray[np.float64] = volume,
        ) -> Mapping[str, npt.NDArray[np.float64]]:
            frame = _frame(
                cast("Sequence[int]", keys),
                opened=cast("Sequence[float]", opened_values),
                high=cast("Sequence[float]", high_values),
                low=cast("Sequence[float]", low_values),
                close=cast("Sequence[float]", close_values),
                tick_volume=cast("Sequence[float]", volume_values),
            )
            return provider.compute(
                _request(
                    (frame,),
                    eval_start=int(keys[event_position]),
                    eval_end=int(keys[event_position + 4]),
                    required_columns=provider.required_columns,
                )
            )

        baseline = compute()
        assert baseline["zt_event_signed"].tolist() == [1.0, 0.0, 0.0, 0.0, 0.0]

        oldest_influencing_close = 4
        actual_reach = event_position - oldest_influencing_close
        assert actual_reach == 26_526
        assert provider.window.max_prior_rows == 26_534
        assert provider.window.max_prior_rows - actual_reach == 8

        changed_at_boundary = close.copy()
        changed_at_boundary[oldest_influencing_close] = 200.0
        assert compute(close_values=changed_at_boundary)["zt_event_signed"][0] == 0.0

        changed_before_boundary = close.copy()
        changed_before_boundary[:oldest_influencing_close] = 200.0
        np.testing.assert_array_equal(
            compute(close_values=changed_before_boundary)["zt_event_signed"],
            baseline["zt_event_signed"],
        )

        aligned_open = opened.copy()
        aligned_high = high.copy()
        aligned_low = low.copy()
        aligned_close = close.copy()
        aligned_volume = volume.copy()
        current_prefix = slice(event_position, event_position + 4)
        aligned_open[current_prefix] = 99.5
        aligned_high[current_prefix] = 102.0
        aligned_low[current_prefix] = 98.0
        aligned_close[current_prefix] = 100.5
        aligned_volume[current_prefix] = 100.0
        aligned = compute(
            opened_values=aligned_open,
            high_values=aligned_high,
            low_values=aligned_low,
            close_values=aligned_close,
            volume_values=aligned_volume,
        )
        for name in provider.feature_names:
            np.testing.assert_array_equal(
                aligned[name].view(np.uint64),
                baseline[name].view(np.uint64),
            )


class TestUlsPushV7:
    @staticmethod
    def _push_fixture() -> _AggregatedStrategyBars:
        size = 306
        opened = np.full(size, 100.0, dtype=np.float64)
        close = np.full(size, 100.0, dtype=np.float64)
        high = np.full(size, 100.5, dtype=np.float64)
        low = np.full(size, 99.5, dtype=np.float64)
        volume = np.full(size, 100.0, dtype=np.float64)
        opened[300], close[300], high[300], low[300] = 101.0, 100.0, 101.0, 99.0
        opened[301], close[301], high[301], low[301] = 100.0, 104.0, 105.0, 100.0
        volume[301] = 300.0
        opened[302], close[302], high[302], low[302] = 100.5, 101.5, 102.0, 100.0
        positions = np.arange(size, dtype=np.int64)
        return _AggregatedStrategyBars(
            positions,
            positions.copy(),
            opened,
            high,
            low,
            close,
            volume,
        )

    def test_finite_recursive_average_has_an_exact_sliding_window(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64)
        observed = _finite_recursive_average(values, alpha=0.5, window=3)
        np.testing.assert_array_equal(
            observed,
            np.array([np.nan, np.nan, 2.25, 3.25], dtype=np.float64),
        )

    def test_current_ema_approximation_reaches_exactly_2000_m5_bars(self) -> None:
        values = np.arange(2_003, dtype=np.float64)
        baseline = _uls_ema(values, length=200, window=2_000)
        at_boundary = values.copy()
        at_boundary[2] += 1_000_000.0
        before_boundary = values.copy()
        before_boundary[1] += 1_000_000.0

        assert baseline[2_001].view(np.uint64) != _uls_ema(
            at_boundary,
            length=200,
            window=2_000,
        )[2_001].view(np.uint64)
        assert baseline[2_001].view(np.uint64) == _uls_ema(
            before_boundary,
            length=200,
            window=2_000,
        )[2_001].view(np.uint64)

    def test_signal_sma_has_no_bitwise_dependency_before_its_window(self) -> None:
        values = np.arange(24, dtype=np.float64)
        baseline = _rolling_sma(values, 20)
        at_boundary = values.copy()
        at_boundary[2] += 1_000_000.0
        before_boundary = values.copy()
        before_boundary[1] += 1_000_000.0

        assert baseline[21].view(np.uint64) != _rolling_sma(at_boundary, 20)[21].view(
            np.uint64
        )
        assert baseline[21].view(np.uint64) == _rolling_sma(
            before_boundary,
            20,
        )[21].view(np.uint64)

    def test_pivots_require_strictly_lower_or_higher_legs(self) -> None:
        assert _up_is_pivot_high(_float_vector((1.0, 2.0, 3.0, 2.0, 1.0)), 2, 2)
        assert not _up_is_pivot_high(
            _float_vector((1.0, 3.0, 3.0, 2.0, 1.0)),
            2,
            2,
        )
        assert _up_is_pivot_low(_float_vector((3.0, 2.0, 1.0, 2.0, 3.0)), 2, 2)
        assert not _up_is_pivot_low(
            _float_vector((3.0, 1.0, 1.0, 2.0, 3.0)),
            2,
            2,
        )

    def test_opposite_liquidity_excludes_the_reference_price(self) -> None:
        levels = [
            _UpLiquidity(99.0, 0, 0),
            _UpLiquidity(100.0, 0, 0),
            _UpLiquidity(101.0, 0, 0),
        ]
        assert _up_opposite_liquidity(levels, price=100.0, above=False) == 99.0
        assert _up_opposite_liquidity(levels, price=100.0, above=True) == 101.0

    def test_cisd_run_state_expires_at_the_owner_approved_finite_boundary(self) -> None:
        state = _UpCisdState()
        _up_update_cisd(state, index=0, opened=100.0, close=101.0, max_age=800)
        _up_update_cisd(state, index=800, opened=100.0, close=100.0, max_age=800)
        assert (state.in_bull_run, state.bull_run_bar) == (True, 0)
        assert state.last_bull_open == 100.0

        _up_update_cisd(state, index=801, opened=100.0, close=100.0, max_age=800)
        assert (state.in_bull_run, state.bull_run_bar) == (False, -1)
        assert np.isnan(state.last_bull_open)

        _up_update_cisd(state, index=802, opened=99.0, close=100.0, max_age=800)
        _up_update_cisd(state, index=803, opened=100.0, close=98.0, max_age=800)
        assert state.cisd_bear_bar == 803

    def test_default_contract_and_dynamic_reach_are_exact(self) -> None:
        provider = UlsPushV7()
        assert provider.provider_id == "uls_push_v7"
        assert provider.version == 1
        assert provider.window == WindowSpec(5_734, GapPolicy.CROSS)
        assert provider.required_columns == ("open", "high", "low", "close", "tick_volume")
        assert provider.params["atr_window"] == 300
        assert provider.params["engine_mode"] == "merged"
        assert provider.params["hour_baseline"] is False
        assert provider.params["minimum_tick"] == 0.01
        assert UlsPushV7(ema_filter="current").window == WindowSpec(
            10_009,
            GapPolicy.CROSS,
        )

    def test_resolved_params_round_trip_through_the_registry(self) -> None:
        provider = UlsPushV7()
        recreated = FEATURE_PROVIDERS.create("uls_push_v7", **dict(provider.params))

        assert recreated.params == provider.params
        assert recreated.window == provider.window

    @pytest.mark.parametrize(
        ("name", "value"),
        [("atr_window", 301), ("ema_window", 2_001), ("momentum_window", 501)],
    )
    def test_derived_windows_fail_closed_when_resolution_is_inconsistent(
        self,
        name: str,
        value: int,
    ) -> None:
        params = dict(UlsPushV7().params)
        params[name] = value

        with pytest.raises(FeatureContractError, match=f"{name} is derived"):
            FEATURE_PROVIDERS.create("uls_push_v7", **params)

    @pytest.mark.parametrize(
        ("params", "message"),
        [
            ({"hour_baseline": True}, "hour_baseline"),
            ({"session_filter": True}, "session_filter"),
            ({"ema_filter": "higher"}, "higher-timeframe EMA"),
            ({"minimum_tick": 0.0}, "minimum_tick"),
        ],
    )
    def test_unrepresentable_time_modes_fail_closed(
        self,
        params: Mapping[str, object],
        message: str,
    ) -> None:
        with pytest.raises(FeatureContractError, match=message):
            UlsPushV7(**cast("Any", params))

    def test_all_seven_confirmation_modes_are_pinned(self) -> None:
        assert _up_confirmation_required("trendline", trend=True, bos=False, cisd=False)
        assert _up_confirmation_required("bos", trend=False, bos=True, cisd=False)
        assert _up_confirmation_required("cisd", trend=False, bos=False, cisd=True)
        assert _up_confirmation_required(
            "trendline_or_bos", trend=False, bos=True, cisd=False
        )
        assert _up_confirmation_required("any", trend=False, bos=False, cisd=True)
        assert _up_confirmation_required("two", trend=True, bos=False, cisd=True)
        assert _up_confirmation_required("all", trend=True, bos=True, cisd=True)
        assert not _up_confirmation_required("all", trend=True, bos=True, cisd=False)

    def test_equal_liquidity_merge_preserves_the_owner_approved_lineage_birth(self) -> None:
        levels: list[_UpLiquidity] = []
        _up_add_liquidity(
            levels,
            price=100.0,
            pivot_bar=10,
            tolerance=0.2,
            high_side=True,
            maximum=20,
        )
        _up_add_liquidity(
            levels,
            price=100.1,
            pivot_bar=20,
            tolerance=0.2,
            high_side=True,
            maximum=20,
        )
        assert levels == [_UpLiquidity(100.1, 20, 10, touches=2)]

    def test_minimum_tick_controls_pine_liquidity_consumption(self) -> None:
        levels = [
            _UpLiquidity(100.009, 1, 1),
            _UpLiquidity(100.011, 2, 2),
        ]
        _up_consume_liquidity(levels, price=100.0, minimum_tick=0.01)
        assert [level.used for level in levels] == [True, False]

    def test_structural_equal_and_combined_liquidity_switches_are_distinct(self) -> None:
        one_touch = _UpLiquidity(100.0, 10, 10)
        assert _up_liquidity_eligible(
            one_touch,
            UlsPushV7(liquidity_mode="structural")._params,
        )
        assert not _up_liquidity_eligible(
            one_touch,
            UlsPushV7(liquidity_mode="equal")._params,
        )
        assert _up_liquidity_eligible(
            one_touch,
            UlsPushV7(liquidity_mode="both")._params,
        )
        one_touch.touches = 2
        assert _up_liquidity_eligible(
            one_touch,
            UlsPushV7(liquidity_mode="equal")._params,
        )

    def test_short_state_machine_advances_return_ifvg_and_bos_in_order(self) -> None:
        size = 13
        close = np.full(size, 100.0, dtype=np.float64)
        close[11] = 99.0
        close[12] = 97.0
        opened = close + 0.25
        high = np.maximum(opened, close) + 1.0
        low = np.minimum(opened, close) - 1.0
        bars = _AggregatedStrategyBars(
            np.arange(size, dtype=np.int64),
            np.arange(size, dtype=np.int64),
            opened,
            high,
            low,
            close,
            np.full(size, 100.0, dtype=np.float64),
        )
        params = UlsPushV7(use_key_levels=False)._params
        setup = _UpSetup(
            -1,
            state=4,
            liquidity=100.0,
            liquidity_touches=2,
            sweep_bar=8,
            sweep_extreme=101.0,
            sweep_wick=0.5,
            return_bar=10,
        )
        fvg = _UpFvg(
            top=100.5,
            bottom=99.5,
            born_bar=8,
            bullish=True,
            inverted=True,
            inversion_bar=10,
        )
        common: dict[str, object] = {
            "bars": bars,
            "push_zones": [],
            "pivot_high_prices": [],
            "pivot_high_bars": [],
            "pivot_low_prices": [98.0],
            "pivot_low_bars": [5],
            "cisd_bear_bar": -1,
            "cisd_bull_bar": -1,
            "raw_push_up": False,
            "raw_push_down": False,
            "atr_exec": 2.0,
            "atr_signal": 2.0,
            "ema_value": np.nan,
            "momentum_up": True,
            "momentum_down": True,
            "params": params,
        }
        _up_advance_setup(
            setup,
            index=11,
            fvgs=[fvg],
            **cast("Any", common),
        )
        assert setup.state == 6
        assert setup.poi_source == 1
        assert (setup.poi_top, setup.poi_bottom, setup.structure) == (100.5, 99.5, 98.0)
        assert fvg.used

        _up_advance_setup(
            setup,
            index=12,
            fvgs=[fvg],
            **cast("Any", common),
        )
        assert setup.state == 8
        assert setup.bos
        assert not setup.trendline
        assert setup.score > 0.0

    def test_atr_execution_and_signal_baseline_remain_distinct(self) -> None:
        bars = self._push_fixture()
        provider = UlsPushV7(engine_mode="push")
        indicators = _up_indicators(bars, provider._params)
        assert np.isfinite(indicators.atr_exec[300])
        assert np.isfinite(indicators.atr_signal[300])
        assert indicators.atr_exec[300] != indicators.atr_signal[300]

    def test_standalone_push_toggle_emits_one_exact_event(self) -> None:
        bars = self._push_fixture()
        common: dict[str, object] = {
            "push_bars": 2,
            "push_atr": 0.1,
            "push_volume_multiple": 0.5,
            "require_rejection": False,
            "min_retracement_pct": 0.0,
            "max_retracement_pct": 100.0,
            "use_key_levels": False,
        }
        push = UlsPushV7(engine_mode="push", **cast("Any", common))
        output = _run_uls_push_state(bars, push._params).columns
        assert output["up_event_signed"][302] == 1.0
        assert output["up_source"][302] == 3.0
        assert output["up_entry_ref"][302] == 101.5
        assert output["up_poi_top"][302] == 101.0
        assert output["up_poi_bot"][302] == 99.0
        assert output["up_score"][302] == pytest.approx(58.71287878787878)

        merged = UlsPushV7(engine_mode="merged", **cast("Any", common))
        merged_output = _run_uls_push_state(bars, merged._params).columns
        assert merged_output["up_event_signed"][302] == 0.0
        assert np.isnan(merged_output["up_source"][302])

    @pytest.mark.parametrize(
        ("engine_mode", "expected_event"),
        [("uls", 0.0), ("push", 1.0), ("parallel", 1.0), ("merged", 0.0)],
    )
    def test_all_four_engine_modes_are_independently_selectable(
        self,
        engine_mode: str,
        expected_event: float,
    ) -> None:
        provider = UlsPushV7(
            engine_mode=engine_mode,
            push_bars=2,
            push_atr=0.1,
            push_volume_multiple=0.5,
            require_rejection=False,
            min_retracement_pct=0.0,
            max_retracement_pct=100.0,
            use_key_levels=False,
        )
        output = _run_uls_push_state(self._push_fixture(), provider._params).columns
        assert output["up_event_signed"][302] == expected_event

    def test_optional_push_score_floor_can_be_switched_without_changing_detection(
        self,
    ) -> None:
        common: dict[str, object] = {
            "engine_mode": "push",
            "push_bars": 2,
            "push_atr": 0.1,
            "push_volume_multiple": 0.5,
            "require_rejection": False,
            "min_retracement_pct": 0.0,
            "max_retracement_pct": 100.0,
            "use_key_levels": False,
            "use_score_filter": True,
        }
        accepted = UlsPushV7(min_score=58, **cast("Any", common))
        refused = UlsPushV7(min_score=59, **cast("Any", common))
        assert _run_uls_push_state(
            self._push_fixture(), accepted._params
        ).columns["up_event_signed"][302] == 1.0
        assert _run_uls_push_state(
            self._push_fixture(), refused._params
        ).columns["up_event_signed"][302] == 0.0

    def test_public_provider_projects_a_complete_m5_event_to_its_first_provable_m1_row(
        self,
    ) -> None:
        bars = self._push_fixture()
        provider = UlsPushV7(
            engine_mode="push",
            push_bars=2,
            push_atr=0.1,
            push_volume_multiple=0.5,
            require_rejection=False,
            min_retracement_pct=0.0,
            max_retracement_pct=100.0,
            use_key_levels=False,
        )
        strategy_size = bars.close.size
        keys = np.arange((strategy_size + 1) * 5, dtype=np.int64) * 60

        def expand(
            values: npt.NDArray[np.float64],
            final: float,
        ) -> npt.NDArray[np.float64]:
            return np.concatenate(
                (np.repeat(values, 5), np.full(5, final, dtype=np.float64))
            )

        opened = expand(bars.opened, 100.0)
        high = expand(bars.high, 100.5)
        low = expand(bars.low, 99.5)
        close = expand(bars.close, 100.0)
        volume = expand(bars.tick_volume / 5.0, 20.0)
        event_position = 303 * 5

        def compute(
            *,
            opened_values: npt.NDArray[np.float64] = opened,
            high_values: npt.NDArray[np.float64] = high,
            low_values: npt.NDArray[np.float64] = low,
            close_values: npt.NDArray[np.float64] = close,
            volume_values: npt.NDArray[np.float64] = volume,
        ) -> Mapping[str, npt.NDArray[np.float64]]:
            stop = event_position + 5
            frame = _frame(
                cast("Sequence[int]", keys[:stop]),
                opened=cast("Sequence[float]", opened_values[:stop]),
                high=cast("Sequence[float]", high_values[:stop]),
                low=cast("Sequence[float]", low_values[:stop]),
                close=cast("Sequence[float]", close_values[:stop]),
                tick_volume=cast("Sequence[float]", volume_values[:stop]),
            )
            return provider.compute(
                _request(
                    (frame,),
                    eval_start=int(keys[event_position]),
                    eval_end=int(keys[event_position + 4]),
                    required_columns=provider.required_columns,
                )
            )

        baseline = compute()
        assert baseline["up_event_signed"].tolist() == [1.0, 0.0, 0.0, 0.0, 0.0]
        assert baseline["up_source"][0] == 3.0

        changed_open = opened.copy()
        changed_high = high.copy()
        changed_low = low.copy()
        changed_close = close.copy()
        changed_volume = volume.copy()
        current_bucket = slice(event_position, event_position + 5)
        changed_open[current_bucket] = 5_000.0
        changed_high[current_bucket] = 6_000.0
        changed_low[current_bucket] = 4_000.0
        changed_close[current_bucket] = 5_500.0
        changed_volume[current_bucket] = 9_000.0
        aligned = compute(
            opened_values=changed_open,
            high_values=changed_high,
            low_values=changed_low,
            close_values=changed_close,
            volume_values=changed_volume,
        )
        for name in provider.feature_names:
            np.testing.assert_array_equal(
                baseline[name][0:1].view(np.uint64),
                aligned[name][0:1].view(np.uint64),
            )

    def test_default_declared_reach_bounds_a_measured_public_uls_event(self) -> None:
        keys, opened, high, low, close, volume, event_position, boundary = (
            _uls_reach_m1_fixture()
        )
        provider = UlsPushV7()

        def compute(
            *,
            opened_values: npt.NDArray[np.float64] = opened,
            high_values: npt.NDArray[np.float64] = high,
            low_values: npt.NDArray[np.float64] = low,
            close_values: npt.NDArray[np.float64] = close,
            volume_values: npt.NDArray[np.float64] = volume,
        ) -> Mapping[str, npt.NDArray[np.float64]]:
            frame = _frame(
                cast("Sequence[int]", keys),
                opened=cast("Sequence[float]", opened_values),
                high=cast("Sequence[float]", high_values),
                low=cast("Sequence[float]", low_values),
                close=cast("Sequence[float]", close_values),
                tick_volume=cast("Sequence[float]", volume_values),
            )
            return provider.compute(
                _request(
                    (frame,),
                    eval_start=int(keys[event_position]),
                    eval_end=int(keys[event_position]),
                    required_columns=provider.required_columns,
                )
            )

        baseline = compute()
        assert baseline["up_event_signed"].tolist() == [-1.0]
        assert event_position - boundary == 4_045
        assert provider.window.max_prior_rows == 5_734

        changed_at_boundary = high.copy()
        changed_at_boundary[boundary] = 106.0
        assert compute(high_values=changed_at_boundary)["up_event_signed"][0] == 0.0

        old_open = opened.copy()
        old_high = high.copy()
        old_low = low.copy()
        old_close = close.copy()
        old_volume = volume.copy()
        old_open[:boundary] = 500.0
        old_high[:boundary] = 501.0
        old_low[:boundary] = 499.0
        old_close[:boundary] = 500.0
        old_volume[:boundary] = 900.0
        before_boundary = compute(
            opened_values=old_open,
            high_values=old_high,
            low_values=old_low,
            close_values=old_close,
            volume_values=old_volume,
        )
        repeated = compute()
        for name in provider.feature_names:
            np.testing.assert_array_equal(
                baseline[name].view(np.uint64),
                before_boundary[name].view(np.uint64),
            )
            np.testing.assert_array_equal(
                baseline[name].view(np.uint64),
                repeated[name].view(np.uint64),
            )

    def test_enlarged_current_ema_reach_has_a_measured_public_event_boundary(
        self,
    ) -> None:
        keys, opened, high, low, close, volume, event_position, _ = (
            _uls_reach_m1_fixture(shift=1_000)
        )
        provider = UlsPushV7(ema_filter="current")
        boundary = 104 * 5

        def compute(
            *,
            opened_values: npt.NDArray[np.float64] = opened,
            high_values: npt.NDArray[np.float64] = high,
            low_values: npt.NDArray[np.float64] = low,
            close_values: npt.NDArray[np.float64] = close,
        ) -> Mapping[str, npt.NDArray[np.float64]]:
            frame = _frame(
                cast("Sequence[int]", keys),
                opened=cast("Sequence[float]", opened_values),
                high=cast("Sequence[float]", high_values),
                low=cast("Sequence[float]", low_values),
                close=cast("Sequence[float]", close_values),
                tick_volume=cast("Sequence[float]", volume),
            )
            return provider.compute(
                _request(
                    (frame,),
                    eval_start=int(keys[event_position]),
                    eval_end=int(keys[event_position]),
                    required_columns=provider.required_columns,
                )
            )

        baseline = compute()
        assert baseline["up_event_signed"].tolist() == [-1.0]
        assert event_position - boundary == 10_000
        assert provider.window.max_prior_rows == 10_009

        boundary_slice = slice(boundary, boundary + 5)
        changed_open = opened.copy()
        changed_high = high.copy()
        changed_low = low.copy()
        changed_close = close.copy()
        changed_open[boundary_slice] = -2_000_000_000.0
        changed_high[boundary_slice] = -1_999_999_999.0
        changed_low[boundary_slice] = -2_000_000_001.0
        changed_close[boundary_slice] = -2_000_000_000.0
        assert compute(
            opened_values=changed_open,
            high_values=changed_high,
            low_values=changed_low,
            close_values=changed_close,
        )["up_event_signed"][0] == 0.0

        changed_open = opened.copy()
        changed_high = high.copy()
        changed_low = low.copy()
        changed_close = close.copy()
        changed_open[:boundary] = -2_000_000_000.0
        changed_high[:boundary] = -1_999_999_999.0
        changed_low[:boundary] = -2_000_000_001.0
        changed_close[:boundary] = -2_000_000_000.0
        before_boundary = compute(
            opened_values=changed_open,
            high_values=changed_high,
            low_values=changed_low,
            close_values=changed_close,
        )
        for name in provider.feature_names:
            np.testing.assert_array_equal(
                baseline[name].view(np.uint64),
                before_boundary[name].view(np.uint64),
            )


class TestWholePlanValidation:
    @pytest.mark.parametrize("providers", [(), "probe", b"probe"])
    def test_provider_plan_must_be_a_nonempty_ordered_sequence(self, providers: object) -> None:
        with pytest.raises(FeatureContractError, match="provider"):
            _run(cast("Sequence[object]", providers))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("feature_names", ()),
            ("feature_names", ["value"]),
            ("feature_names", ("value", "value")),
            ("feature_names", ("",)),
            ("feature_names", (1,)),
            ("required_columns", ["close"]),
            ("required_columns", ("close", "close")),
            ("required_columns", ("",)),
            ("required_columns", (1,)),
        ],
    )
    def test_malformed_name_declaration_refuses_before_compute(
        self, field: str, value: object
    ) -> None:
        first = _ProbeProvider("first", feature_names=("first",))
        malformed = _ProbeProvider("malformed", feature_names=("second",))
        setattr(malformed, field, value)

        with pytest.raises(FeatureContractError, match="malformed"):
            _run((first, malformed))

        assert first.calls == malformed.calls == 0

    def test_collision_names_both_owners_and_is_whole_plan_fail_fast(self) -> None:
        first = _ProbeProvider("alpha", feature_names=("shared",))
        second = _ProbeProvider("beta", feature_names=("shared",))

        with pytest.raises(FeatureContractError) as raised:
            _run((first, second))

        message = str(raised.value)
        assert "shared" in message
        assert "alpha" in message
        assert "beta" in message
        assert first.calls == second.calls == 0

    def test_collision_with_duplicate_provider_ids_retains_plan_indices(self) -> None:
        first = _ProbeProvider("same", feature_names=("shared",))
        second = _ProbeProvider("same", feature_names=("shared",))

        with pytest.raises(FeatureContractError) as raised:
            _run((first, second))

        message = str(raised.value)
        assert "same" in message
        assert "index 0" in message
        assert "index 1" in message
        assert first.calls == second.calls == 0

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("provider_id", ""),
            ("provider_id", 1),
            ("version", True),
            ("version", 1.0),
            ("window", object()),
            ("params", []),
            ("params", {"bad": []}),
            ("cost_hint", "cheap"),
            ("compute", None),
        ],
    )
    def test_malformed_provider_declaration_refuses_before_compute(
        self, field: str, value: object
    ) -> None:
        first = _ProbeProvider("first", feature_names=("first",))
        malformed = _ProbeProvider("malformed", feature_names=("second",))
        setattr(malformed, field, value)

        with pytest.raises(FeatureContractError):
            _run((first, malformed))

        assert first.calls == malformed.calls == 0

    def test_later_missing_required_column_refuses_before_any_compute(self) -> None:
        first = _ProbeProvider("first", feature_names=("first",), required_columns=("close",))
        second = _ProbeProvider(
            "second",
            feature_names=("second",),
            required_columns=("missing",),
        )

        with pytest.raises(FeatureRequestError, match=r"second.*missing"):
            _run((first, second))

        assert first.calls == second.calls == 0

    @pytest.mark.parametrize("field", ["feature_names", "required_columns"])
    @pytest.mark.parametrize("name", ["target", "target.", "target.forward_return_1"])
    def test_reserved_namespace_refuses_before_compute(self, field: str, name: str) -> None:
        first = _ProbeProvider("first", feature_names=("first",))
        blocked = _ProbeProvider("blocked", feature_names=("blocked",))
        setattr(blocked, field, (name,))

        with pytest.raises(FeatureContractError, match=r"blocked.*reserved"):
            _run((first, blocked))

        assert first.calls == blocked.calls == 0

    @pytest.mark.parametrize("field", ["feature_names", "required_columns"])
    @pytest.mark.parametrize(
        "name",
        [
            "Target",
            "TARGET",
            "targeted",
            "target_foo",
            "target_value",
            "targetx",
            "mytarget.value",
        ],
    )
    def test_target_near_misses_are_deliberately_allowed(self, field: str, name: str) -> None:
        extras = {name: tuple(float(index) for index in range(6))}
        frame = _frame(tuple(range(6)), extra_columns=extras)
        provider = _ProbeProvider(
            "allowed",
            feature_names=(name,) if field == "feature_names" else ("allowed",),
            required_columns=(name,) if field == "required_columns" else (),
        )

        result = _run((provider,), frames=(frame,))

        assert result.column_names == cast("tuple[str, ...]", provider.feature_names)
        assert provider.calls == 1

    def test_provider_requests_are_column_isolated_across_the_plan(self) -> None:
        def read_undeclared_high(request: FeatureRequest) -> object:
            request.column("high")
            raise AssertionError("the undeclared high column unexpectedly resolved")

        close_only = _ProbeProvider(
            "close_only",
            feature_names=("close_probe",),
            required_columns=("close",),
            output=read_undeclared_high,
        )
        legitimate_high_reader = _ProbeProvider(
            "high_reader",
            feature_names=("high_probe",),
            required_columns=("high",),
        )

        with pytest.raises(FeatureRequestError, match="not declared"):
            _run((close_only, legitimate_high_reader))

        assert close_only.calls == 1
        assert legitimate_high_reader.calls == 0
        assert close_only.requests[0].column("close").shape == (6,)
        with pytest.raises(FeatureRequestError, match="not declared"):
            close_only.requests[0].column("high")


class _ArraySubclass(np.ndarray):
    pass


def _bad_output(case: str, request: FeatureRequest) -> object:
    valid = np.ones(request.eval_length, dtype=np.float64)
    if case == "not-mapping":
        return object()
    if case == "missing-name":
        return {}
    if case == "extra-name":
        return {"probe": valid, "extra": valid.copy()}
    if case == "non-string-name":
        return {1: valid}
    if case == "not-array":
        return {"probe": valid.tolist()}
    if case == "array-subclass":
        return {"probe": valid.view(_ArraySubclass)}
    if case == "not-vector":
        return {"probe": valid.reshape((-1, 1))}
    if case == "wrong-dtype":
        return {"probe": valid.astype(np.float32)}
    if case == "dtype-metadata":
        metadata = np.dtype(np.float64, metadata={"mutable": []})
        return {"probe": np.ones(request.eval_length, dtype=metadata)}
    if case == "wrong-length":
        return {"probe": np.ones(request.eval_length + 1, dtype=np.float64)}
    if case == "masked-positive-infinity":
        valid[0] = np.inf
        return {"probe": valid}
    if case == "masked-negative-infinity":
        valid[0] = -np.inf
        return {"probe": valid}
    raise AssertionError(f"unknown test case {case}")


class TestOutputValidation:
    @pytest.mark.parametrize(
        "case",
        [
            "not-mapping",
            "missing-name",
            "extra-name",
            "non-string-name",
            "not-array",
            "array-subclass",
            "not-vector",
            "wrong-dtype",
            "dtype-metadata",
            "wrong-length",
            "masked-positive-infinity",
            "masked-negative-infinity",
        ],
    )
    def test_malformed_output_refuses_and_names_provider_before_mask(self, case: str) -> None:
        provider = _ProbeProvider(
            "broken",
            window=WindowSpec(2, GapPolicy.CROSS),
            output=lambda request: _bad_output(case, request),
        )

        with pytest.raises(FeatureFrameError, match="broken"):
            _run((provider,), eval_end=2, short_context_accepted=True)

        assert provider.calls == 1

    def test_emitted_mapping_is_reordered_to_feature_declaration(self) -> None:
        def reversed_output(request: FeatureRequest) -> object:
            return OrderedDict(
                (
                    ("a", np.full(request.eval_length, 1.0, dtype=np.float64)),
                    ("b", np.full(request.eval_length, 2.0, dtype=np.float64)),
                )
            )

        provider = _ProbeProvider(
            "ordered",
            feature_names=("b", "a"),
            output=reversed_output,
        )

        frame = _run((provider,))

        assert frame.column_names == ("b", "a")
        np.testing.assert_array_equal(frame.column("b"), np.full(6, 2.0))
        np.testing.assert_array_equal(frame.column("a"), np.full(6, 1.0))

    def test_valid_noncontiguous_output_is_detached_from_provider_memory(self) -> None:
        backing = np.arange(12, dtype=np.float64)

        def noncontiguous_output(_request: FeatureRequest) -> object:
            return {"probe": backing[::2]}

        provider = _ProbeProvider("detached", output=noncontiguous_output)
        frame = _run((provider,))
        expected = backing[::2].copy()

        backing[:] = 99.0

        np.testing.assert_array_equal(frame.column("probe"), expected)
        assert not frame.column("probe").flags.writeable


class TestPlatformMasking:
    @pytest.mark.parametrize(
        ("policy", "expected_mask"),
        [
            (
                GapPolicy.CROSS,
                (True, True, False, False, False, False, False, False, False),
            ),
            (
                GapPolicy.BREAK,
                (True, True, False, True, True, False, True, True, False),
            ),
        ],
    )
    def test_exact_cross_and_break_masks(
        self, policy: GapPolicy, expected_mask: tuple[bool, ...]
    ) -> None:
        keys = (0, 1, 2, 10, 11, 12, 20, 21, 22)
        window = WindowSpec(
            2,
            policy,
            nominal_interval=1 if policy is GapPolicy.BREAK else None,
        )
        provider = _ProbeProvider("mask", window=window, value=7.0)

        result = _run(
            (provider,),
            frames=(_frame(keys),),
            eval_start=0,
            eval_end=22,
            short_context_accepted=True,
        )

        assert np.isnan(result.column("probe")).tolist() == list(expected_mask)
        emitted = cast("Mapping[str, npt.NDArray[np.float64]]", provider.last_emitted)
        assert np.isfinite(emitted["probe"]).all(), "the platform must mask after compute"

    @pytest.mark.parametrize(
        ("eval_start", "leading_nan_count"),
        [(0, 3), (1, 2), (3, 0)],
    )
    def test_warmup_counts_prior_observed_rows_on_the_resolved_axis(
        self, eval_start: int, leading_nan_count: int
    ) -> None:
        provider = _ProbeProvider("warmup", window=WindowSpec(3, GapPolicy.CROSS))

        result = _run(
            (provider,),
            frames=(_frame((0, 1, 2, 3, 4, 5)),),
            eval_start=eval_start,
            eval_end=5,
            short_context_accepted=True,
        )

        observed = result.column("probe")
        assert np.isnan(observed[:leading_nan_count]).all()
        assert np.isfinite(observed[leading_nan_count:]).all()

    def test_zero_reach_break_never_masks_even_across_gaps(self) -> None:
        provider = _ProbeProvider(
            "zero",
            window=WindowSpec(0, GapPolicy.BREAK, nominal_interval=1),
        )

        result = _run(
            (provider,),
            frames=(_frame((0, 10, 20)),),
            eval_start=0,
            eval_end=20,
        )

        assert np.isfinite(result.column("probe")).all()

    def test_provider_nan_at_an_available_row_is_preserved(self) -> None:
        def output_with_nan(request: FeatureRequest) -> object:
            values = np.ones(request.eval_length, dtype=np.float64)
            values[-1] = np.nan
            return {"probe": values}

        provider = _ProbeProvider(
            "nan_provider",
            window=WindowSpec(1, GapPolicy.CROSS),
            output=output_with_nan,
        )

        result = _run((provider,), eval_start=1)

        assert not np.isnan(result.column("probe")[0])
        assert np.isnan(result.column("probe")[-1])

    def test_break_mask_handles_a_valid_signed_int64_spanning_delta(self) -> None:
        int64_min = -(2**63)
        int64_max = 2**63 - 1
        provider = _ProbeProvider(
            "extreme_gap",
            window=WindowSpec(1, GapPolicy.BREAK, nominal_interval=1),
        )

        result = _run(
            (provider,),
            frames=(_frame((int64_min, int64_max)),),
            eval_start=int64_min,
            eval_end=int64_max,
            short_context_accepted=True,
        )

        assert np.isnan(result.column("probe")).tolist() == [True, True]

    def test_reach_larger_than_int64_returns_an_all_warmup_mask(self) -> None:
        provider = _ProbeProvider(
            "huge_reach",
            window=WindowSpec(2**63, GapPolicy.BREAK, nominal_interval=1),
        )

        result = _run(
            (provider,),
            frames=(_frame((0, 1, 2)),),
            eval_start=0,
            eval_end=2,
            short_context_accepted=True,
        )

        assert np.isnan(result.column("probe")).all()


class TestOrderingSeamsAndPurity:
    def test_provider_order_is_preserved_while_named_columns_are_shuffle_invariant(self) -> None:
        alpha = _ProbeProvider("alpha", feature_names=("alpha",), value=1.0)
        beta = _ProbeProvider("beta", feature_names=("beta",), value=2.0)

        forward = _run((alpha, beta))
        reverse = _run((beta, alpha))

        assert forward.column_names == ("alpha", "beta")
        assert reverse.column_names == ("beta", "alpha")
        for name in ("alpha", "beta"):
            assert forward.column(name).tobytes() == reverse.column(name).tobytes()
            assert forward.origin(name) == reverse.origin(name)

    def test_all_reference_providers_match_across_a_contiguous_segment_seam(self) -> None:
        keys = (0, 1, 2, 3, 4, 5)
        close = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
        high = tuple(value + 2.0 for value in close)
        low = tuple(value - 1.0 for value in close)
        context = _frame(keys[:3], close=close[:3], high=high[:3], low=low[:3])
        evaluation = _frame(
            keys[3:],
            close=close[3:],
            high=high[3:],
            low=low[3:],
            dataset_id="VALIDATION_V1",
            digest=DIGEST_B,
            role=ViewRole.VALIDATION,
        )
        contiguous = _frame(
            keys,
            close=close,
            high=high,
            low=low,
            dataset_id="CONTIGUOUS_V1",
            digest=DIGEST_C,
            role=ViewRole.VALIDATION,
        )
        providers = (LogReturn1(), RollingMeanClose(window=3), BarRange())

        across_seam = _run(
            providers,
            frames=(context, evaluation),
            eval_start=3,
            eval_end=5,
        )
        single_axis = _run(
            providers,
            frames=(contiguous,),
            eval_start=3,
            eval_end=5,
        )

        assert across_seam.column_names == single_axis.column_names
        for name in across_seam.column_names:
            np.testing.assert_array_equal(across_seam.column(name), single_axis.column(name))

    @pytest.mark.parametrize("gap_policy", [GapPolicy.CROSS, GapPolicy.BREAK])
    def test_rolling_mean_matches_single_axis_at_an_arithmetic_gap_seam(
        self, gap_policy: GapPolicy
    ) -> None:
        keys = (0, 1, 2, 10, 11, 12)
        close = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
        context = _frame(keys[:3], close=close[:3])
        evaluation = _frame(
            keys[3:],
            close=close[3:],
            dataset_id="VALIDATION_V1",
            digest=DIGEST_B,
            role=ViewRole.VALIDATION,
        )
        contiguous = _frame(
            keys,
            close=close,
            dataset_id="CONTIGUOUS_V1",
            digest=DIGEST_C,
            role=ViewRole.VALIDATION,
        )
        provider = RollingMeanClose(
            window=3,
            gap_policy=gap_policy,
            nominal_interval=1 if gap_policy is GapPolicy.BREAK else None,
        )

        across_seam = _run(
            (provider,),
            frames=(context, evaluation),
            eval_start=10,
            eval_end=12,
        )
        single_axis = _run(
            (provider,),
            frames=(contiguous,),
            eval_start=10,
            eval_end=12,
        )

        np.testing.assert_array_equal(
            across_seam.column("rolling_mean_close"),
            single_axis.column("rolling_mean_close"),
        )
        expected_nan = (
            (False, False, False) if gap_policy is GapPolicy.CROSS else (True, True, False)
        )
        assert np.isnan(across_seam.column("rolling_mean_close")).tolist() == list(expected_nan)

    def test_registry_is_exact_zero_arg_constructible_and_protocol_conformant(self) -> None:
        expected_types = {
            "gap_sensitive_levels_h1_v1": GapSensitiveLevelsH1V1,
            "log_return_1": LogReturn1,
            "multi_confluence_m15_v1": MultiConfluenceM15V1,
            "rolling_mean_close": RollingMeanClose,
            "uls_market_context_v1": UlsMarketContextV1,
            "uls_push_v7": UlsPushV7,
            "bar_range": BarRange,
            "zone_context_v1": ZoneContextV1,
            "zone_trader_v13": ZoneTraderV13,
        }
        expected_costs = {
            "bar_range": CostHint.CHEAP,
            "gap_sensitive_levels_h1_v1": CostHint.EXPENSIVE,
            "log_return_1": CostHint.CHEAP,
            "multi_confluence_m15_v1": CostHint.EXPENSIVE,
            "rolling_mean_close": CostHint.CHEAP,
            "uls_market_context_v1": CostHint.EXPENSIVE,
            "uls_push_v7": CostHint.EXPENSIVE,
            "zone_context_v1": CostHint.EXPENSIVE,
            "zone_trader_v13": CostHint.EXPENSIVE,
        }
        assert FEATURE_PROVIDERS.names() == frozenset(expected_types)
        for name, expected_type in expected_types.items():
            provider = FEATURE_PROVIDERS.create(name)
            assert type(provider) is expected_type
            assert isinstance(provider, FeatureProvider)
            assert provider.provider_id == name
            assert provider.cost_hint is expected_costs[name]

        registry: Registry[FeatureProvider] = Registry("feature provider")
        registry.add("once", LogReturn1)
        with pytest.raises(DuplicateComponentError, match="already registered"):
            registry.add("once", LogReturn1)

    def test_every_registered_provider_is_pure_under_interleaving(self) -> None:
        positions = tuple(range(30))
        keys = tuple(index * 60 for index in positions)
        close_a = tuple(float(index + 1) for index in positions)
        close_b = tuple(float((index + 1) * 3) for index in positions)
        frame_a = _frame(keys, close=close_a)
        frame_b = _frame(keys, close=close_b, high=tuple(value + 5.0 for value in close_b))

        for name in sorted(FEATURE_PROVIDERS.names()):
            provider = FEATURE_PROVIDERS.create(name)
            request_a = _request(
                (frame_a,),
                eval_start=keys[20],
                eval_end=keys[29],
                required_columns=provider.required_columns,
            )
            request_b = _request(
                (frame_b,),
                eval_start=keys[20],
                eval_end=keys[29],
                required_columns=provider.required_columns,
            )
            before_keys = request_a.keys.copy()
            before_columns = {
                column: request_a.column(column).copy() for column in provider.required_columns
            }

            first = _emission_snapshot(provider.compute(request_a))
            second = _emission_snapshot(provider.compute(request_a))
            _emission_snapshot(provider.compute(request_b))
            after_interleave = _emission_snapshot(provider.compute(request_a))

            assert first == second == after_interleave, name
            np.testing.assert_array_equal(request_a.keys, before_keys)
            for column, expected in before_columns.items():
                np.testing.assert_array_equal(request_a.column(column), expected)


def _emission_snapshot(
    emitted: Mapping[str, npt.NDArray[np.float64]],
) -> tuple[tuple[str, str, tuple[int, ...], bytes, bytes], ...]:
    return tuple(
        (
            name,
            values.dtype.str,
            values.shape,
            np.isnan(values).tobytes(),
            values.tobytes(),
        )
        for name, values in emitted.items()
    )


class TestBoundedViewAdapter:
    def test_adapter_requires_an_exact_bounded_view(self) -> None:
        class BoundedViewSubclass(BoundedView):
            pass

        fabricated = object.__new__(BoundedViewSubclass)
        with pytest.raises(FeatureContractError, match="exact BoundedView"):
            compute_features(
                cast("BoundedView", fabricated),
                cast("Sequence[FeatureProvider]", (BarRange(),)),
            )

    def test_adapter_matches_direct_execution_on_synthetic_in_memory_segments(self) -> None:
        close = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
        context = _frame((0, 1, 2), close=close[:3])
        evaluation = _frame(
            (3, 4, 5),
            close=close[3:],
            dataset_id="VALIDATION_V1",
            digest=DIGEST_B,
            role=ViewRole.VALIDATION,
        )
        view = _bounded_view((context, evaluation), eval_start=3, eval_end=5)
        providers = (LogReturn1(), RollingMeanClose(window=3), BarRange())

        adapted = compute_features(view, providers)
        direct = _run(
            providers,
            frames=(context, evaluation),
            eval_start=3,
            eval_end=5,
        )

        np.testing.assert_array_equal(adapted.keys, view.eval_frame.keys)
        assert adapted.source_identities == (
            context.dataset_ref.canonical_identity,
            evaluation.dataset_ref.canonical_identity,
        )
        assert adapted.column_names == direct.column_names
        for name in adapted.column_names:
            np.testing.assert_array_equal(adapted.column(name), direct.column(name))

    def test_adapter_refuses_short_context_unless_the_view_explicitly_accepts_it(self) -> None:
        frame = _frame((10, 11, 12))
        provider = _ProbeProvider("warmup", window=WindowSpec(2, GapPolicy.CROSS))
        refused_view = _bounded_view((frame,), eval_start=10, eval_end=12)

        with pytest.raises(InsufficientContextError, match=r"warmup.*2 prior.*0"):
            compute_features(refused_view, cast("Sequence[FeatureProvider]", (provider,)))
        assert provider.calls == 0

        accepted_view = _bounded_view(
            (frame,),
            eval_start=10,
            eval_end=12,
            accept_short_context=True,
        )
        result = compute_features(
            accepted_view,
            cast("Sequence[FeatureProvider]", (provider,)),
        )

        assert np.isnan(result.column("probe")).tolist() == [True, True, False]
        assert provider.calls == 1
