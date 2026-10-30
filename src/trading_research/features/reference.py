"""Small deterministic reference feature providers for the V1 MVP."""

from __future__ import annotations

import bisect as _bisect
import dataclasses as _dataclasses
import math as _math
import typing as _typing

import numpy as _np

from trading_research.core.errors import FeatureContractError as _FeatureContractError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import FeatureRequest as _FeatureRequest
from trading_research.core.features import GapPolicy as _GapPolicy
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import WindowSpec as _WindowSpec
from trading_research.core.time import TimeUnit as _TimeUnit
from trading_research.features.registry import FEATURE_PROVIDERS as _FEATURE_PROVIDERS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

__all__ = [
    "BarRange",
    "GapSensitiveLevelsH1V1",
    "LogReturn1",
    "MultiConfluenceM15V1",
    "RollingMeanClose",
    "UlsMarketContextV1",
    "UlsPushV7",
    "ZoneContextV1",
    "ZoneTraderV13",
]


class _AggregatedStrategyBars(_typing.NamedTuple):
    """Complete arithmetic-grid bars and their first usable M1 positions."""

    bucket_numbers: _npt.NDArray[_np.int64]
    usable_positions: _npt.NDArray[_np.int64]
    opened: _npt.NDArray[_np.float64]
    high: _npt.NDArray[_np.float64]
    low: _npt.NDArray[_np.float64]
    close: _npt.NDArray[_np.float64]
    tick_volume: _npt.NDArray[_np.float64]


def _validate_zone_trader_grids(
    *,
    bar_grid_seconds: int,
    bar_phase_offset_seconds: int,
    anchor_grid_seconds: int,
    anchor_phase_offset_seconds: int,
) -> None:
    """Validate explicit arithmetic M5 and anchor grids without session claims."""
    for name, value in (
        ("bar_grid_seconds", bar_grid_seconds),
        ("anchor_grid_seconds", anchor_grid_seconds),
    ):
        if type(value) is not int or value <= 0:
            raise _FeatureContractError(f"{name} must be a positive Python int")
    for name, value, grid in (
        ("bar_phase_offset_seconds", bar_phase_offset_seconds, bar_grid_seconds),
        (
            "anchor_phase_offset_seconds",
            anchor_phase_offset_seconds,
            anchor_grid_seconds,
        ),
    ):
        if type(value) is not int or not 0 <= value < grid:
            raise _FeatureContractError(
                f"{name} must be a Python int in the half-open range [0, {grid})"
            )
    if anchor_grid_seconds % bar_grid_seconds != 0:
        raise _FeatureContractError(
            "anchor_grid_seconds must be an exact multiple of bar_grid_seconds"
        )
    if (anchor_phase_offset_seconds - bar_phase_offset_seconds) % bar_grid_seconds != 0:
        raise _FeatureContractError("anchor and strategy grid phases must align on an M5 boundary")


def _arithmetic_bucket_numbers(
    keys: _npt.NDArray[_np.int64],
    *,
    grid_seconds: int,
    phase_offset_seconds: int,
) -> _npt.NDArray[_np.int64]:
    """Return floor((key - phase) / grid) without signed-int64 subtraction."""
    quotient = _np.floor_divide(keys, grid_seconds)
    remainder = _np.remainder(keys, grid_seconds)
    return (quotient - (remainder < phase_offset_seconds)).astype(_np.int64)


def _aggregate_complete_strategy_bars(
    keys: _npt.NDArray[_np.int64],
    opened: _npt.NDArray[_np.float64],
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
    tick_volume: _npt.NDArray[_np.float64],
    *,
    bar_grid_seconds: int,
    bar_phase_offset_seconds: int,
) -> _AggregatedStrategyBars:
    """Aggregate M1 rows into bars completed by an observed later-bucket row.

    This is ID-1 predicate A: a bucket becomes usable only at the first
    already-observed row belonging to a later arithmetic bucket. The final
    bucket is therefore never returned, even when its final grid minute exists.
    """
    if type(bar_grid_seconds) is not int or bar_grid_seconds <= 0:
        raise _FeatureContractError("bar_grid_seconds must be a positive Python int")
    if (
        type(bar_phase_offset_seconds) is not int
        or not 0 <= bar_phase_offset_seconds < bar_grid_seconds
    ):
        raise _FeatureContractError(
            "bar_phase_offset_seconds must be a Python int in the strategy grid"
        )
    arrays = (opened, high, low, close, tick_volume)
    if keys.ndim != 1 or any(values.ndim != 1 for values in arrays):
        raise _FeatureContractError("strategy aggregation inputs must be one-dimensional")
    if any(values.shape != keys.shape for values in arrays):
        raise _FeatureContractError("strategy aggregation inputs must share one shape")

    empty_int = _np.empty(0, dtype=_np.int64)
    empty_float = _np.empty(0, dtype=_np.float64)
    if keys.size < 2:
        return _AggregatedStrategyBars(
            empty_int,
            empty_int.copy(),
            empty_float,
            empty_float.copy(),
            empty_float.copy(),
            empty_float.copy(),
            empty_float.copy(),
        )

    bucket_numbers = _arithmetic_bucket_numbers(
        keys,
        grid_seconds=bar_grid_seconds,
        phase_offset_seconds=bar_phase_offset_seconds,
    )
    starts = _np.concatenate(
        (
            _np.array([0], dtype=_np.int64),
            (_np.flatnonzero(bucket_numbers[1:] != bucket_numbers[:-1]) + 1).astype(_np.int64),
        )
    )
    if starts.size < 2:
        return _AggregatedStrategyBars(
            empty_int,
            empty_int.copy(),
            empty_float,
            empty_float.copy(),
            empty_float.copy(),
            empty_float.copy(),
            empty_float.copy(),
        )

    complete_starts = starts[:-1]
    usable_positions = starts[1:].astype(_np.int64, copy=True)
    complete_stops = usable_positions
    opened_float = _np.asarray(opened, dtype=_np.float64)
    high_float = _np.asarray(high, dtype=_np.float64)
    low_float = _np.asarray(low, dtype=_np.float64)
    close_float = _np.asarray(close, dtype=_np.float64)
    volume_float = _np.asarray(tick_volume, dtype=_np.float64)
    return _AggregatedStrategyBars(
        bucket_numbers=bucket_numbers[complete_starts].astype(_np.int64, copy=True),
        usable_positions=usable_positions,
        opened=opened_float[complete_starts].astype(_np.float64, copy=True),
        high=_np.maximum.reduceat(high_float, starts)[:-1].astype(_np.float64, copy=False),
        low=_np.minimum.reduceat(low_float, starts)[:-1].astype(_np.float64, copy=False),
        close=close_float[complete_stops - 1].astype(_np.float64, copy=True),
        tick_volume=_np.add.reduceat(volume_float, starts)[:-1].astype(_np.float64, copy=False),
    )


def _truncated_wilder_atr(
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
    w_atr: int,
) -> _npt.NDArray[_np.float64]:
    """Return the approved finite approximation to Wilder/RMA ATR(14)."""
    if type(w_atr) is not int or w_atr <= 0:
        raise _FeatureContractError("w_atr must be a positive Python int")
    if high.ndim != 1 or low.ndim != 1 or close.ndim != 1:
        raise _FeatureContractError("ATR inputs must be one-dimensional")
    if high.shape != low.shape or high.shape != close.shape:
        raise _FeatureContractError("ATR inputs must share one shape")

    high_float = _np.asarray(high, dtype=_np.float64)
    low_float = _np.asarray(low, dtype=_np.float64)
    close_float = _np.asarray(close, dtype=_np.float64)
    result = _np.full(high_float.size, _np.nan, dtype=_np.float64)
    if high_float.size <= w_atr:
        return result

    previous_close = close_float[:-1]
    true_range = _np.maximum.reduce(
        (
            high_float[1:] - low_float[1:],
            _np.abs(high_float[1:] - previous_close),
            _np.abs(low_float[1:] - previous_close),
        )
    )
    alpha = _np.float64(1.0 / 14.0)
    decay = _np.float64(13.0 / 14.0)
    weights = _np.empty(w_atr, dtype=_np.float64)
    if w_atr == 1:
        weights[0] = 1.0
    else:
        powers = _np.arange(w_atr - 2, -1, -1, dtype=_np.float64)
        weights[1:] = alpha * _np.power(decay, powers)
        weights[0] = 1.0 - weights[1:].sum(dtype=_np.float64)
    result[w_atr:] = _np.correlate(true_range, weights, mode="valid")
    result[~_np.isfinite(result)] = _np.nan
    return result


class _ZoneTraderPrimitives(_typing.NamedTuple):
    """Stateless M5-axis quantities consumed by the strategy state machine."""

    bar_range: _npt.NDArray[_np.float64]
    upper_wick: _npt.NDArray[_np.float64]
    lower_wick: _npt.NDArray[_np.float64]
    anchor_signal: _npt.NDArray[_np.bool_]
    volume_top: _npt.NDArray[_np.float64]
    volume_bottom: _npt.NDArray[_np.float64]
    bull_fvg: _npt.NDArray[_np.bool_]
    bear_fvg: _npt.NDArray[_np.bool_]
    fvg_size_ok: _npt.NDArray[_np.bool_]
    fvg_top: _npt.NDArray[_np.float64]
    fvg_bottom: _npt.NDArray[_np.float64]
    is_base: _npt.NDArray[_np.bool_]
    impulse_up: _npt.NDArray[_np.bool_]
    impulse_down: _npt.NDArray[_np.bool_]
    demand_supply_top: _npt.NDArray[_np.float64]
    demand_supply_bottom: _npt.NDArray[_np.float64]


class _ZoneTraderStrategyOutput(_typing.NamedTuple):
    """M5-axis Zone Trader output before projection to the M1 axis."""

    event_signed: _npt.NDArray[_np.float64]
    entry_price: _npt.NDArray[_np.float64]
    stop_price: _npt.NDArray[_np.float64]
    target_price: _npt.NDArray[_np.float64]
    zone_top: _npt.NDArray[_np.float64]
    zone_bottom: _npt.NDArray[_np.float64]
    zone_kind: _npt.NDArray[_np.float64]
    candidate_open: _npt.NDArray[_np.float64]
    zone_break_count: _npt.NDArray[_np.int64]
    confirmation_observed: _npt.NDArray[_np.bool_]
    retained_zone_count: _npt.NDArray[_np.int64]
    event_zone_created_at: _npt.NDArray[_np.int64]
    event_zone_touch_count: _npt.NDArray[_np.int64]
    event_pending_bars: _npt.NDArray[_np.int64]
    event_nearest_zone_room: _npt.NDArray[_np.float64]
    event_nearest_zone_present: _npt.NDArray[_np.bool_]


def _zone_trader_primitives(
    bars: _AggregatedStrategyBars,
    atr: _npt.NDArray[_np.float64],
    *,
    bar_grid_seconds: int,
    bar_phase_offset_seconds: int,
    anchor_grid_seconds: int,
    anchor_phase_offset_seconds: int,
    vol_len: int,
    vol_mult: float,
    min_fvg_atr: float,
    max_base_body: float,
    max_base_atr: float,
    min_move_atr: float,
    use_volume_zones: bool,
    use_imbalance: bool,
    use_demand_supply: bool,
) -> _ZoneTraderPrimitives:
    """Compute the enabled stateless Zone Trader primitives on complete M5 bars."""
    size = bars.close.size
    bar_range = (bars.high - bars.low).astype(_np.float64)
    upper_wick = _np.zeros(size, dtype=_np.float64)
    lower_wick = _np.zeros(size, dtype=_np.float64)
    nonzero_range = bar_range > 0.0
    upper_wick[nonzero_range] = (
        bars.high[nonzero_range]
        - _np.maximum(bars.opened[nonzero_range], bars.close[nonzero_range])
    ) / bar_range[nonzero_range]
    lower_wick[nonzero_range] = (
        _np.minimum(bars.opened[nonzero_range], bars.close[nonzero_range]) - bars.low[nonzero_range]
    ) / bar_range[nonzero_range]

    anchor_signal = _np.zeros(size, dtype=_np.bool_)
    volume_top = _np.full(size, _np.nan, dtype=_np.float64)
    volume_bottom = _np.full(size, _np.nan, dtype=_np.float64)
    if size:
        bucket_start_keys = bars.bucket_numbers * _np.int64(bar_grid_seconds) + _np.int64(
            bar_phase_offset_seconds
        )
        anchor_numbers = _arithmetic_bucket_numbers(
            bucket_start_keys,
            grid_seconds=anchor_grid_seconds,
            phase_offset_seconds=anchor_phase_offset_seconds,
        )
        current_anchor = int(anchor_numbers[0])
        bars_in_anchor = 1
        anchor_volume = float(bars.tick_volume[0])
        anchor_history: list[float] = []
        for index in range(1, size):
            observed_anchor = int(anchor_numbers[index])
            if observed_anchor == current_anchor:
                bars_in_anchor += 1
                anchor_volume += float(bars.tick_volume[index])
                continue

            prior_average = sum(anchor_history) / len(anchor_history) if anchor_history else _np.nan
            if (
                use_volume_zones
                and _np.isfinite(prior_average)
                and anchor_volume > prior_average * vol_mult
            ):
                anchor_signal[index] = True
                scan_count = min(bars_in_anchor, 500)
                best_volume = -1.0
                best_index: int | None = None
                for distance in range(1, scan_count + 1):
                    candidate_index = index - distance
                    candidate_volume = float(bars.tick_volume[candidate_index])
                    if candidate_volume > best_volume:
                        best_volume = candidate_volume
                        best_index = candidate_index
                if best_index is not None:
                    volume_top[index] = bars.high[best_index]
                    volume_bottom[index] = bars.low[best_index]

            anchor_history.append(anchor_volume)
            if len(anchor_history) > vol_len:
                del anchor_history[0]
            current_anchor = observed_anchor
            bars_in_anchor = 1
            anchor_volume = float(bars.tick_volume[index])

    bull_fvg = _np.zeros(size, dtype=_np.bool_)
    bear_fvg = _np.zeros(size, dtype=_np.bool_)
    fvg_size_ok = _np.zeros(size, dtype=_np.bool_)
    fvg_top = _np.full(size, _np.nan, dtype=_np.float64)
    fvg_bottom = _np.full(size, _np.nan, dtype=_np.float64)
    if size > 2:
        bull_fvg[2:] = bars.low[2:] > bars.high[:-2]
        bear_fvg[2:] = bars.high[2:] < bars.low[:-2]
        bull_size = bars.low[2:] - bars.high[:-2]
        bear_size = bars.low[:-2] - bars.high[2:]
        fvg_size_ok[2:] = _np.where(
            bull_fvg[2:],
            bull_size >= atr[2:] * min_fvg_atr,
            _np.where(
                bear_fvg[2:],
                bear_size >= atr[2:] * min_fvg_atr,
                False,
            ),
        )
        if use_imbalance:
            bull_created = bull_fvg & fvg_size_ok
            bear_created = (~bull_created) & bear_fvg & fvg_size_ok
            fvg_top[bull_created] = bars.low[bull_created]
            fvg_bottom[bull_created] = _np.roll(bars.high, 2)[bull_created]
            fvg_top[bear_created] = _np.roll(bars.low, 2)[bear_created]
            fvg_bottom[bear_created] = bars.high[bear_created]

    is_base = _np.zeros(size, dtype=_np.bool_)
    impulse_up = _np.zeros(size, dtype=_np.bool_)
    impulse_down = _np.zeros(size, dtype=_np.bool_)
    demand_supply_top = _np.full(size, _np.nan, dtype=_np.float64)
    demand_supply_bottom = _np.full(size, _np.nan, dtype=_np.float64)
    if size > 1:
        base_range = bars.high[:-1] - bars.low[:-1]
        base_body = _np.abs(bars.close[:-1] - bars.opened[:-1])
        is_base[1:] = (
            (base_range > 0.0)
            & (base_body <= base_range * max_base_body)
            & (base_range <= atr[1:] * max_base_atr)
        )
        move_range = bars.high[1:] - bars.low[1:]
        impulse_up[1:] = (
            (bars.close[1:] > bars.opened[1:])
            & (move_range >= atr[1:] * min_move_atr)
            & (bars.close[1:] > bars.high[:-1])
        )
        impulse_down[1:] = (
            (bars.close[1:] < bars.opened[1:])
            & (move_range >= atr[1:] * min_move_atr)
            & (bars.close[1:] < bars.low[:-1])
        )
        if use_demand_supply:
            created = is_base & (impulse_up | impulse_down)
            demand_supply_top[created] = _np.roll(bars.high, 1)[created]
            demand_supply_bottom[created] = _np.roll(bars.low, 1)[created]

    return _ZoneTraderPrimitives(
        bar_range,
        upper_wick,
        lower_wick,
        anchor_signal,
        volume_top,
        volume_bottom,
        bull_fvg,
        bear_fvg,
        fvg_size_ok,
        fvg_top,
        fvg_bottom,
        is_base,
        impulse_up,
        impulse_down,
        demand_supply_top,
        demand_supply_bottom,
    )


def _run_zone_trader_state(
    opened: _npt.NDArray[_np.float64],
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
    atr: _npt.NDArray[_np.float64],
    primitives: _ZoneTraderPrimitives,
    *,
    max_zone_age_rows: int,
    max_zones: int,
    break_buffer_atr: float,
    confirmation_mode: str,
    minimum_wick: float,
    max_bars_wait: int,
    stop_buffer_atr: float,
    reward_risk_target: float,
) -> _ZoneTraderStrategyOutput:
    """Run the finite Zone Trader zone and pending-candidate lifecycle."""
    size = close.size
    event_signed = _np.zeros(size, dtype=_np.float64)
    entry_price = _np.full(size, _np.nan, dtype=_np.float64)
    stop_price = _np.full(size, _np.nan, dtype=_np.float64)
    target_price = _np.full(size, _np.nan, dtype=_np.float64)
    output_zone_top = _np.full(size, _np.nan, dtype=_np.float64)
    output_zone_bottom = _np.full(size, _np.nan, dtype=_np.float64)
    output_zone_kind = _np.full(size, _np.nan, dtype=_np.float64)
    candidate_open = _np.zeros(size, dtype=_np.float64)
    zone_break_count = _np.zeros(size, dtype=_np.int64)
    confirmation_observed = _np.zeros(size, dtype=_np.bool_)
    retained_zone_count = _np.zeros(size, dtype=_np.int64)
    event_zone_created_at = _np.full(size, -1, dtype=_np.int64)
    event_zone_touch_count = _np.zeros(size, dtype=_np.int64)
    event_pending_bars = _np.zeros(size, dtype=_np.int64)
    event_nearest_zone_room = _np.full(size, _np.nan, dtype=_np.float64)
    event_nearest_zone_present = _np.zeros(size, dtype=_np.bool_)

    zone_top: list[float] = []
    zone_bottom: list[float] = []
    zone_kind: list[int] = []
    zone_broken: list[bool] = []
    zone_inside: list[bool] = []
    zone_created: list[int] = []
    zone_touches: list[int] = []

    pending = False
    pending_top = _np.nan
    pending_bottom = _np.nan
    pending_short = False
    pending_kind = 0
    pending_created_at = -1
    pending_touch_count = 0
    pending_bars = 0

    def add_zone(top: float, bottom: float, kind: int, index: int) -> None:
        zone_top.append(top)
        zone_bottom.append(bottom)
        zone_kind.append(kind)
        zone_broken.append(False)
        zone_inside.append(True)
        zone_created.append(index)
        zone_touches.append(0)

    for index in range(size):
        if _np.isfinite(primitives.volume_top[index]):
            add_zone(
                float(primitives.volume_top[index]),
                float(primitives.volume_bottom[index]),
                0,
                index,
            )
        if _np.isfinite(primitives.fvg_top[index]):
            add_zone(
                float(primitives.fvg_top[index]),
                float(primitives.fvg_bottom[index]),
                1,
                index,
            )
        if _np.isfinite(primitives.demand_supply_top[index]):
            add_zone(
                float(primitives.demand_supply_top[index]),
                float(primitives.demand_supply_bottom[index]),
                2,
                index,
            )

        keep = [
            position
            for position, created_at in enumerate(zone_created)
            if index - created_at <= max_zone_age_rows
        ]
        if len(keep) != len(zone_created):
            zone_top = [zone_top[position] for position in keep]
            zone_bottom = [zone_bottom[position] for position in keep]
            zone_kind = [zone_kind[position] for position in keep]
            zone_broken = [zone_broken[position] for position in keep]
            zone_inside = [zone_inside[position] for position in keep]
            zone_created = [zone_created[position] for position in keep]
            zone_touches = [zone_touches[position] for position in keep]

        excess = len(zone_top) - max_zones
        if excess > 0:
            del zone_top[:excess]
            del zone_bottom[:excess]
            del zone_kind[:excess]
            del zone_broken[:excess]
            del zone_inside[:excess]
            del zone_created[:excess]
            del zone_touches[:excess]

        new_candidate = False
        candidate_top = _np.nan
        candidate_bottom = _np.nan
        candidate_short = False
        candidate_kind = 0
        candidate_created_at = -1
        candidate_touch_count = 0
        candidate_score = -1.0
        previous_close = float(close[index - 1]) if index > 0 else _np.nan
        buffer = float(atr[index]) * break_buffer_atr
        for position in range(len(zone_top) - 1, -1, -1):
            top = zone_top[position]
            bottom = zone_bottom[position]
            broken = zone_broken[position]
            was_inside = zone_inside[position]
            now_inside = float(low[index]) <= top and float(high[index]) >= bottom
            if not broken and (
                float(close[index]) > top + buffer or float(close[index]) < bottom - buffer
            ):
                zone_broken[position] = True
                zone_break_count[index] += 1
            elif not broken and now_inside and not was_inside:
                zone_touches[position] += 1
                is_short = bottom > previous_close
                score = 0.0
                if score >= candidate_score:
                    new_candidate = True
                    candidate_score = score
                    candidate_top = top
                    candidate_bottom = bottom
                    candidate_short = is_short
                    candidate_kind = zone_kind[position]
                    candidate_created_at = zone_created[position]
                    candidate_touch_count = zone_touches[position]
            zone_inside[position] = now_inside

        if new_candidate:
            pending = True
            pending_top = candidate_top
            pending_bottom = candidate_bottom
            pending_short = candidate_short
            pending_kind = candidate_kind
            pending_created_at = candidate_created_at
            pending_touch_count = candidate_touch_count
            pending_bars = 0

        if pending:
            pending_bars += 1
            if confirmation_mode == "immediate_touch":
                confirmed = True
            elif confirmation_mode == "rejection_wick":
                confirmed = bool(
                    primitives.upper_wick[index] >= minimum_wick
                    if pending_short
                    else primitives.lower_wick[index] >= minimum_wick
                )
            else:
                confirmed = bool(
                    close[index] < pending_bottom if pending_short else close[index] > pending_top
                )

            if confirmed:
                confirmation_observed[index] = True
                entry = float(close[index])
                stop = (
                    pending_top + float(atr[index]) * stop_buffer_atr
                    if pending_short
                    else pending_bottom - float(atr[index]) * stop_buffer_atr
                )
                risk = abs(entry - stop)
                target = (
                    entry - risk * reward_risk_target
                    if pending_short
                    else entry + risk * reward_risk_target
                )
                event_signed[index] = -1.0 if pending_short else 1.0
                entry_price[index] = entry
                stop_price[index] = stop
                target_price[index] = target
                output_zone_top[index] = pending_top
                output_zone_bottom[index] = pending_bottom
                output_zone_kind[index] = float(pending_kind)
                event_zone_created_at[index] = pending_created_at
                event_zone_touch_count[index] = pending_touch_count
                event_pending_bars[index] = pending_bars
                rooms: list[float] = []
                for position, (top, bottom) in enumerate(
                    zip(zone_top, zone_bottom, strict=True)
                ):
                    same_zone = (
                        zone_created[position] == pending_created_at
                        and zone_kind[position] == pending_kind
                        and top == pending_top
                        and bottom == pending_bottom
                    )
                    if same_zone or zone_broken[position]:
                        continue
                    if pending_short and top < entry:
                        rooms.append(entry - top)
                    elif not pending_short and bottom > entry:
                        rooms.append(bottom - entry)
                if rooms:
                    event_nearest_zone_room[index] = min(rooms)
                    event_nearest_zone_present[index] = True
                pending = False
            elif pending_bars >= max_bars_wait:
                pending = False

        candidate_open[index] = 1.0 if pending else 0.0
        retained_zone_count[index] = len(zone_top)

    return _ZoneTraderStrategyOutput(
        event_signed,
        entry_price,
        stop_price,
        target_price,
        output_zone_top,
        output_zone_bottom,
        output_zone_kind,
        candidate_open,
        zone_break_count,
        confirmation_observed,
        retained_zone_count,
        event_zone_created_at,
        event_zone_touch_count,
        event_pending_bars,
        event_nearest_zone_room,
        event_nearest_zone_present,
    )


class LogReturn1:
    """One-observation natural logarithmic close return."""

    @property
    def provider_id(self) -> str:
        """Return the stable implementation identity."""
        return "log_return_1"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the sole declared output name."""
        return ("log_return_1",)

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact readable market columns."""
        return ("close",)

    @property
    def window(self) -> _WindowSpec:
        """Return the one-row CROSS reach."""
        return _WindowSpec(max_prior_rows=1, gap_policy=_GapPolicy.CROSS)

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return identity-bearing parameters."""
        return {}

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap deterministic computation."""
        return _CostHint.CHEAP

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute natural-log close returns for evaluation rows."""
        close = _np.asarray(request.column("close"), dtype=_np.float64)
        result = _np.full(request.eval_length, _np.nan, dtype=_np.float64)
        positions = _np.arange(
            request.eval_offset,
            request.eval_offset + request.eval_length,
            dtype=_np.int64,
        )
        available = positions >= 1
        current = positions[available]
        with _np.errstate(divide="ignore", invalid="ignore"):
            result[available] = _np.log(close[current] / close[current - 1])
        return {"log_return_1": result}


class RollingMeanClose:
    """Trailing arithmetic mean of close over an observed-row window."""

    def __init__(
        self,
        window: int = 20,
        *,
        gap_policy: _GapPolicy = _GapPolicy.CROSS,
        nominal_interval: int | None = None,
    ) -> None:
        if type(window) is not int or window <= 0:
            raise _FeatureContractError("rolling window must be a positive Python int")
        self._window_size = window
        self._window = _WindowSpec(
            max_prior_rows=window - 1,
            gap_policy=gap_policy,
            nominal_interval=nominal_interval,
        )

    @property
    def provider_id(self) -> str:
        """Return the stable implementation identity."""
        return "rolling_mean_close"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the sole declared output name."""
        return ("rolling_mean_close",)

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact readable market columns."""
        return ("close",)

    @property
    def window(self) -> _WindowSpec:
        """Return the configured finite reach and gap policy."""
        return self._window

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the identity-bearing window length."""
        return {"window": self._window_size}

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap deterministic computation."""
        return _CostHint.CHEAP

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute trailing observed-row means for evaluation rows."""
        close = _np.asarray(request.column("close"), dtype=_np.float64)
        result = _np.full(request.eval_length, _np.nan, dtype=_np.float64)
        if close.size < self._window_size:
            return {"rolling_mean_close": result}

        windows = _np.lib.stride_tricks.sliding_window_view(close, self._window_size)
        means = windows.mean(axis=1, dtype=_np.float64)
        positions = _np.arange(
            request.eval_offset,
            request.eval_offset + request.eval_length,
            dtype=_np.int64,
        )
        available = positions >= self._window_size - 1
        result[available] = means[positions[available] - (self._window_size - 1)]
        return {"rolling_mean_close": result}


class BarRange:
    """Unclamped high-minus-low range for each evaluation observation."""

    @property
    def provider_id(self) -> str:
        """Return the stable implementation identity."""
        return "bar_range"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the sole declared output name."""
        return ("bar_range",)

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact readable market columns."""
        return ("high", "low")

    @property
    def window(self) -> _WindowSpec:
        """Return the zero-reach CROSS declaration."""
        return _WindowSpec(max_prior_rows=0, gap_policy=_GapPolicy.CROSS)

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return identity-bearing parameters."""
        return {}

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap deterministic computation."""
        return _CostHint.CHEAP

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute high minus low for evaluation rows."""
        start = request.eval_offset
        stop = start + request.eval_length
        high = _np.asarray(request.column("high"), dtype=_np.float64)
        low = _np.asarray(request.column("low"), dtype=_np.float64)
        return {"bar_range": high[start:stop] - low[start:stop]}


class ZoneTraderV13:
    """Finite-reach reproduction of the approved Zone Trader v13 research event."""

    def __init__(
        self,
        *,
        use_volume_zones: bool = True,
        use_imbalance: bool = True,
        use_demand_supply: bool = True,
        min_fvg_atr: float = 0.25,
        max_base_body: float = 0.5,
        max_base_atr: float = 0.8,
        min_move_atr: float = 1.2,
        bar_grid_seconds: int = 300,
        bar_phase_offset_seconds: int = 0,
        anchor_grid_seconds: int = 3600,
        anchor_phase_offset_seconds: int = 0,
        vol_len: int = 20,
        vol_mult: float = 2.0,
        confirmation_mode: str = "rejection_wick",
        minimum_wick: float = 0.35,
        max_bars_wait: int = 6,
        stop_buffer_atr: float = 0.5,
        reward_risk_target: float = 2.0,
        max_zones: int = 15,
        break_buffer_atr: float = 0.5,
        w_atr: int = 300,
        max_zone_age_rows: int = 5000,
    ) -> None:
        for boolean_name, boolean_value in (
            ("use_volume_zones", use_volume_zones),
            ("use_imbalance", use_imbalance),
            ("use_demand_supply", use_demand_supply),
        ):
            if type(boolean_value) is not bool:
                raise _FeatureContractError(f"{boolean_name} must be a Python bool")
        for integer_name, integer_value, minimum in (
            ("vol_len", vol_len, 2),
            ("max_bars_wait", max_bars_wait, 1),
            ("max_zones", max_zones, 1),
            ("w_atr", w_atr, 1),
            ("max_zone_age_rows", max_zone_age_rows, 0),
        ):
            if type(integer_value) is not int or integer_value < minimum:
                raise _FeatureContractError(
                    f"{integer_name} must be a Python int greater than or equal to {minimum}"
                )
        for float_name, float_value in (
            ("min_fvg_atr", min_fvg_atr),
            ("max_base_body", max_base_body),
            ("max_base_atr", max_base_atr),
            ("min_move_atr", min_move_atr),
            ("vol_mult", vol_mult),
            ("minimum_wick", minimum_wick),
            ("stop_buffer_atr", stop_buffer_atr),
            ("reward_risk_target", reward_risk_target),
            ("break_buffer_atr", break_buffer_atr),
        ):
            if type(float_value) is not float or not _np.isfinite(float_value) or float_value < 0.0:
                raise _FeatureContractError(
                    f"{float_name} must be a finite non-negative Python float"
                )
        if confirmation_mode not in {
            "immediate_touch",
            "rejection_wick",
            "close_back_outside",
        }:
            raise _FeatureContractError(
                "confirmation_mode must be immediate_touch, rejection_wick, or close_back_outside"
            )
        _validate_zone_trader_grids(
            bar_grid_seconds=bar_grid_seconds,
            bar_phase_offset_seconds=bar_phase_offset_seconds,
            anchor_grid_seconds=anchor_grid_seconds,
            anchor_phase_offset_seconds=anchor_phase_offset_seconds,
        )
        if bar_grid_seconds % 60 != 0:
            raise _FeatureContractError(
                "bar_grid_seconds must be an exact multiple of the M1 source interval"
            )

        source_rows_per_bar = bar_grid_seconds // 60
        anchor_bars = anchor_grid_seconds // bar_grid_seconds
        creation_reach = max(w_atr, (vol_len + 1) * anchor_bars, 2, 1)
        strategy_reach = max_zone_age_rows + (max_bars_wait - 1) + creation_reach
        max_prior_rows = source_rows_per_bar * (strategy_reach + 2) - 1

        self._use_volume_zones = use_volume_zones
        self._use_imbalance = use_imbalance
        self._use_demand_supply = use_demand_supply
        self._min_fvg_atr = min_fvg_atr
        self._max_base_body = max_base_body
        self._max_base_atr = max_base_atr
        self._min_move_atr = min_move_atr
        self._bar_grid_seconds = bar_grid_seconds
        self._bar_phase_offset_seconds = bar_phase_offset_seconds
        self._anchor_grid_seconds = anchor_grid_seconds
        self._anchor_phase_offset_seconds = anchor_phase_offset_seconds
        self._vol_len = vol_len
        self._vol_mult = vol_mult
        self._confirmation_mode = confirmation_mode
        self._minimum_wick = minimum_wick
        self._max_bars_wait = max_bars_wait
        self._stop_buffer_atr = stop_buffer_atr
        self._reward_risk_target = reward_risk_target
        self._max_zones = max_zones
        self._break_buffer_atr = break_buffer_atr
        self._w_atr = w_atr
        self._max_zone_age_rows = max_zone_age_rows
        self._window = _WindowSpec(max_prior_rows, _GapPolicy.CROSS)

    @property
    def provider_id(self) -> str:
        """Return the stable implementation identity."""
        return "zone_trader_v13"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the ordered Zone Trader research columns."""
        return (
            "zt_event_signed",
            "zt_entry_price",
            "zt_stop_price",
            "zt_target_price",
            "zt_zone_top",
            "zt_zone_bottom",
            "zt_zone_kind",
            "zt_candidate_open",
        )

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact M1 market columns consumed by the strategy."""
        return ("open", "high", "low", "close", "tick_volume")

    @property
    def window(self) -> _WindowSpec:
        """Return the derived finite M1-source reach."""
        return self._window

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return every identity-bearing Zone Trader semantic parameter."""
        return {
            "anchor_grid_seconds": self._anchor_grid_seconds,
            "anchor_phase_offset_seconds": self._anchor_phase_offset_seconds,
            "bar_grid_seconds": self._bar_grid_seconds,
            "bar_phase_offset_seconds": self._bar_phase_offset_seconds,
            "break_buffer_atr": self._break_buffer_atr,
            "confirmation_mode": self._confirmation_mode,
            "max_bars_wait": self._max_bars_wait,
            "max_base_atr": self._max_base_atr,
            "max_base_body": self._max_base_body,
            "max_zone_age_rows": self._max_zone_age_rows,
            "max_zones": self._max_zones,
            "min_fvg_atr": self._min_fvg_atr,
            "min_move_atr": self._min_move_atr,
            "minimum_wick": self._minimum_wick,
            "reward_risk_target": self._reward_risk_target,
            "stop_buffer_atr": self._stop_buffer_atr,
            "use_demand_supply": self._use_demand_supply,
            "use_imbalance": self._use_imbalance,
            "use_volume_zones": self._use_volume_zones,
            "vol_len": self._vol_len,
            "vol_mult": self._vol_mult,
            "w_atr": self._w_atr,
        }

    @property
    def cost_hint(self) -> _CostHint:
        """Declare the bounded state-machine computation as expensive."""
        return _CostHint.EXPENSIVE

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute complete-M5 Zone Trader values on the dense M1 evaluation axis."""
        if request.unit is not _TimeUnit.SECONDS:
            raise _FeatureContractError("zone_trader_v13 requires native keys expressed in seconds")
        keys = request.keys
        deltas = keys.view(_np.uint64)[1:] - keys.view(_np.uint64)[:-1]
        if _np.any(deltas < _np.uint64(60)):
            raise _FeatureContractError(
                "zone_trader_v13 requires M1-density input with adjacent observed "
                "keys at least 60 seconds apart"
            )
        bars = _aggregate_complete_strategy_bars(
            keys,
            _np.asarray(request.column("open"), dtype=_np.float64),
            _np.asarray(request.column("high"), dtype=_np.float64),
            _np.asarray(request.column("low"), dtype=_np.float64),
            _np.asarray(request.column("close"), dtype=_np.float64),
            _np.asarray(request.column("tick_volume"), dtype=_np.float64),
            bar_grid_seconds=self._bar_grid_seconds,
            bar_phase_offset_seconds=self._bar_phase_offset_seconds,
        )
        atr = _truncated_wilder_atr(bars.high, bars.low, bars.close, self._w_atr)
        primitives = _zone_trader_primitives(
            bars,
            atr,
            bar_grid_seconds=self._bar_grid_seconds,
            bar_phase_offset_seconds=self._bar_phase_offset_seconds,
            anchor_grid_seconds=self._anchor_grid_seconds,
            anchor_phase_offset_seconds=self._anchor_phase_offset_seconds,
            vol_len=self._vol_len,
            vol_mult=self._vol_mult,
            min_fvg_atr=self._min_fvg_atr,
            max_base_body=self._max_base_body,
            max_base_atr=self._max_base_atr,
            min_move_atr=self._min_move_atr,
            use_volume_zones=self._use_volume_zones,
            use_imbalance=self._use_imbalance,
            use_demand_supply=self._use_demand_supply,
        )
        strategy = _run_zone_trader_state(
            bars.opened,
            bars.high,
            bars.low,
            bars.close,
            atr,
            primitives,
            max_zone_age_rows=self._max_zone_age_rows,
            max_zones=self._max_zones,
            break_buffer_atr=self._break_buffer_atr,
            confirmation_mode=self._confirmation_mode,
            minimum_wick=self._minimum_wick,
            max_bars_wait=self._max_bars_wait,
            stop_buffer_atr=self._stop_buffer_atr,
            reward_risk_target=self._reward_risk_target,
        )

        result = {
            "zt_event_signed": _np.zeros(request.eval_length, dtype=_np.float64),
            "zt_entry_price": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "zt_stop_price": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "zt_target_price": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "zt_zone_top": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "zt_zone_bottom": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "zt_zone_kind": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "zt_candidate_open": _np.zeros(request.eval_length, dtype=_np.float64),
        }
        positions = _np.arange(
            request.eval_offset,
            request.eval_offset + request.eval_length,
            dtype=_np.int64,
        )
        latest = _np.searchsorted(bars.usable_positions, positions, side="right") - 1
        has_completed_bar = latest >= 0
        result["zt_candidate_open"][has_completed_bar] = strategy.candidate_open[
            latest[has_completed_bar]
        ]

        strategy_events = _np.flatnonzero(strategy.event_signed != 0.0)
        if strategy_events.size:
            source_positions = bars.usable_positions[strategy_events]
            in_evaluation = (source_positions >= request.eval_offset) & (
                source_positions < request.eval_offset + request.eval_length
            )
            selected_events = strategy_events[in_evaluation]
            output_positions = (source_positions[in_evaluation] - request.eval_offset).astype(
                _np.int64
            )
            for name, values in (
                ("zt_event_signed", strategy.event_signed),
                ("zt_entry_price", strategy.entry_price),
                ("zt_stop_price", strategy.stop_price),
                ("zt_target_price", strategy.target_price),
                ("zt_zone_top", strategy.zone_top),
                ("zt_zone_bottom", strategy.zone_bottom),
                ("zt_zone_kind", strategy.zone_kind),
            ):
                result[name][output_positions] = values[selected_events]

        for values in result.values():
            values[_np.isinf(values)] = _np.nan
        return result


_ZONE_CONTEXT_NAMES = (
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


def _rolling_mean(
    values: _npt.NDArray[_np.float64], window: int
) -> _npt.NDArray[_np.float64]:
    """Return a completed-row rolling mean with a fixed finite window."""
    result = _np.full(values.size, _np.nan, dtype=_np.float64)
    if values.size < window:
        return result
    prefix = _np.concatenate(
        (_np.zeros(1, dtype=_np.float64), _np.cumsum(values, dtype=_np.float64))
    )
    result[window - 1 :] = (prefix[window:] - prefix[:-window]) / float(window)
    return result


def _zone_context_values(
    bars: _AggregatedStrategyBars,
    atr: _npt.NDArray[_np.float64],
    primitives: _ZoneTraderPrimitives,
    strategy: _ZoneTraderStrategyOutput,
    *,
    bar_grid_seconds: int,
    bar_phase_offset_seconds: int,
) -> dict[str, _npt.NDArray[_np.float64]]:
    """Build event-time context from completed bars and existing Zone state."""
    size = bars.close.size
    result = {
        name: _np.full(size, _np.nan, dtype=_np.float64) for name in _ZONE_CONTEXT_NAMES
    }
    events = _np.flatnonzero(strategy.event_signed != 0.0)
    if events.size == 0:
        return result

    true_range = (bars.high - bars.low).astype(_np.float64)
    if size > 1:
        previous_close = bars.close[:-1]
        true_range[1:] = _np.maximum.reduce(
            (
                true_range[1:],
                _np.abs(bars.high[1:] - previous_close),
                _np.abs(bars.low[1:] - previous_close),
            )
        )
    tr_12 = _rolling_mean(true_range, 12)
    tr_48 = _rolling_mean(true_range, 48)
    volume_20 = _rolling_mean(bars.tick_volume, 20)
    volume_sq_20 = _rolling_mean(bars.tick_volume * bars.tick_volume, 20)
    volume_variance = _np.maximum(volume_sq_20 - volume_20 * volume_20, 0.0)
    volume_std = _np.sqrt(volume_variance, dtype=_np.float64)
    volume_zscore = _np.zeros(size, dtype=_np.float64)
    nonzero_volume_std = volume_std > 0.0
    volume_zscore[nonzero_volume_std] = (
        bars.tick_volume[nonzero_volume_std] - volume_20[nonzero_volume_std]
    ) / volume_std[nonzero_volume_std]

    bucket_starts = (
        bars.bucket_numbers * _np.int64(bar_grid_seconds)
        + _np.int64(bar_phase_offset_seconds)
    )
    day_fraction = _np.remainder(bucket_starts, _np.int64(86_400)).astype(_np.float64) / 86_400.0
    minute_angle = day_fraction * (2.0 * _np.pi)

    eligible_events: list[int] = []
    for index in events.tolist():
        event = float(strategy.event_signed[index])
        entry = float(strategy.entry_price[index])
        top = float(strategy.zone_top[index])
        bottom = float(strategy.zone_bottom[index])
        current_atr = float(atr[index])
        width = top - bottom
        if (
            event not in {-1.0, 1.0}
            or not _np.isfinite(entry)
            or entry <= 0.0
            or not _np.isfinite(current_atr)
            or current_atr <= 0.0
            or not _np.isfinite(width)
            or width <= 0.0
        ):
            continue
        if (
            index < 48
            or not _np.isfinite(tr_12[index])
            or not _np.isfinite(tr_48[index])
            or tr_48[index] <= 0.0
            or not _np.isfinite(volume_zscore[index])
        ):
            continue
        eligible_events.append(index)
        result["ztc_atr_over_entry"][index] = current_atr / entry
        result["ztc_zone_width_over_atr"][index] = width / current_atr
        result["ztc_zone_age_bars"][index] = float(
            index - int(strategy.event_zone_created_at[index])
        )
        result["ztc_zone_touch_count"][index] = float(
            strategy.event_zone_touch_count[index]
        )
        result["ztc_pending_bars"][index] = float(strategy.event_pending_bars[index])
        result["ztc_entry_zone_position"][index] = (
            event * (entry - ((top + bottom) / 2.0)) / width
        )
        result["ztc_confirmation_wick_fraction"][index] = float(
            primitives.lower_wick[index] if event > 0.0 else primitives.upper_wick[index]
        )
        result["ztc_volume_zscore_20"][index] = float(volume_zscore[index])
        result["ztc_atr_ratio_12_48"][index] = float(tr_12[index] / tr_48[index])
        for lookback in (3, 12, 48):
            prior = float(bars.close[index - lookback])
            if prior <= 0.0:
                raise _FeatureContractError("zone_context_v1 return input is non-positive")
            result[f"ztc_directional_return_{lookback}"][index] = event * _np.log(
                entry / prior
            )
        trailing_high = float(_np.max(bars.high[index - 47 : index + 1]))
        trailing_low = float(_np.min(bars.low[index - 47 : index + 1]))
        swing_room = trailing_high - entry if event > 0.0 else entry - trailing_low
        result["ztc_room_to_swing_48_atr"][index] = max(swing_room, 0.0) / current_atr
        nearest_present = bool(strategy.event_nearest_zone_present[index])
        result["ztc_nearest_zone_present"][index] = float(nearest_present)
        nearest = (
            float(strategy.event_nearest_zone_room[index]) / current_atr
            if nearest_present
            else 20.0
        )
        result["ztc_nearest_zone_room_atr"][index] = min(max(nearest, 0.0), 20.0)
        result["ztc_minute_sin"][index] = float(_np.sin(minute_angle[index]))
        result["ztc_minute_cos"][index] = float(_np.cos(minute_angle[index]))

    eligible = _np.asarray(eligible_events, dtype=_np.int64)
    if any(not _np.isfinite(values[eligible]).all() for values in result.values()):
        raise _FeatureContractError("zone_context_v1 produced a non-finite event context")
    return result


class ZoneContextV1(ZoneTraderV13):
    """Point-in-time market and Zone context for the V2 TP selector."""

    @property
    def provider_id(self) -> str:
        """Return the stable context-provider identity."""
        return "zone_context_v1"

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the exact frozen V2 context schema."""
        return _ZONE_CONTEXT_NAMES

    @property
    def window(self) -> _WindowSpec:
        """Enlarge the Zone reach for the longest 48-bar context statistic."""
        base = super().window
        source_rows_per_bar = self._bar_grid_seconds // 60
        context_prior_rows = source_rows_per_bar * (48 + 2) - 1
        return _WindowSpec(max(base.max_prior_rows, context_prior_rows), _GapPolicy.CROSS)

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute event-time context on the dense M1 evaluation axis."""
        if request.unit is not _TimeUnit.SECONDS:
            raise _FeatureContractError(
                "zone_context_v1 requires native keys expressed in seconds"
            )
        keys = request.keys
        deltas = keys.view(_np.uint64)[1:] - keys.view(_np.uint64)[:-1]
        if _np.any(deltas < _np.uint64(60)):
            raise _FeatureContractError(
                "zone_context_v1 requires M1-density input with adjacent observed "
                "keys at least 60 seconds apart"
            )
        bars = _aggregate_complete_strategy_bars(
            keys,
            _np.asarray(request.column("open"), dtype=_np.float64),
            _np.asarray(request.column("high"), dtype=_np.float64),
            _np.asarray(request.column("low"), dtype=_np.float64),
            _np.asarray(request.column("close"), dtype=_np.float64),
            _np.asarray(request.column("tick_volume"), dtype=_np.float64),
            bar_grid_seconds=self._bar_grid_seconds,
            bar_phase_offset_seconds=self._bar_phase_offset_seconds,
        )
        atr = _truncated_wilder_atr(bars.high, bars.low, bars.close, self._w_atr)
        primitives = _zone_trader_primitives(
            bars,
            atr,
            bar_grid_seconds=self._bar_grid_seconds,
            bar_phase_offset_seconds=self._bar_phase_offset_seconds,
            anchor_grid_seconds=self._anchor_grid_seconds,
            anchor_phase_offset_seconds=self._anchor_phase_offset_seconds,
            vol_len=self._vol_len,
            vol_mult=self._vol_mult,
            min_fvg_atr=self._min_fvg_atr,
            max_base_body=self._max_base_body,
            max_base_atr=self._max_base_atr,
            min_move_atr=self._min_move_atr,
            use_volume_zones=self._use_volume_zones,
            use_imbalance=self._use_imbalance,
            use_demand_supply=self._use_demand_supply,
        )
        strategy = _run_zone_trader_state(
            bars.opened,
            bars.high,
            bars.low,
            bars.close,
            atr,
            primitives,
            max_zone_age_rows=self._max_zone_age_rows,
            max_zones=self._max_zones,
            break_buffer_atr=self._break_buffer_atr,
            confirmation_mode=self._confirmation_mode,
            minimum_wick=self._minimum_wick,
            max_bars_wait=self._max_bars_wait,
            stop_buffer_atr=self._stop_buffer_atr,
            reward_risk_target=self._reward_risk_target,
        )
        context = _zone_context_values(
            bars,
            atr,
            primitives,
            strategy,
            bar_grid_seconds=self._bar_grid_seconds,
            bar_phase_offset_seconds=self._bar_phase_offset_seconds,
        )
        result = {
            name: _np.full(request.eval_length, _np.nan, dtype=_np.float64)
            for name in _ZONE_CONTEXT_NAMES
        }
        strategy_events = _np.flatnonzero(strategy.event_signed != 0.0)
        if strategy_events.size:
            source_positions = bars.usable_positions[strategy_events]
            in_evaluation = (source_positions >= request.eval_offset) & (
                source_positions < request.eval_offset + request.eval_length
            )
            selected_events = strategy_events[in_evaluation]
            output_positions = (source_positions[in_evaluation] - request.eval_offset).astype(
                _np.int64
            )
            if any(
                not _np.isfinite(context[name][selected_events]).all()
                for name in _ZONE_CONTEXT_NAMES
            ):
                raise _FeatureContractError(
                    "zone_context_v1 evaluation event lacks its declared prior context"
                )
            for name in _ZONE_CONTEXT_NAMES:
                result[name][output_positions] = context[name][selected_events]
        return result


_ULS_PUSH_FEATURE_NAMES = (
    "up_event_signed",
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
)

_ULS_MARKET_CONTEXT_NAMES = (
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

_ULS_CONTEXT_BAR_GRID_SECONDS = 300
_ULS_CONTEXT_BAR_PHASE_OFFSET_SECONDS = 0
_ULS_CONTEXT_ATR_LENGTH = 14
_ULS_CONTEXT_ATR_WINDOW = 300
_ULS_CONTEXT_EMA_LENGTH = 50
_ULS_CONTEXT_EMA_WINDOW = 500
_ULS_CONTEXT_LONGEST_LOOKBACK = 512


def _rolling_sma(
    values: _npt.NDArray[_np.float64],
    length: int,
) -> _npt.NDArray[_np.float64]:
    """Return a finite trailing SMA with unavailable warm-up rows."""
    result = _np.full(values.size, _np.nan, dtype=_np.float64)
    if values.size < length:
        return result
    weights = _np.full(length, 1.0 / length, dtype=_np.float64)
    result[length - 1 :] = _np.correlate(values, weights, mode="valid")
    return result


def _finite_recursive_average(
    values: _npt.NDArray[_np.float64],
    *,
    alpha: float,
    window: int,
) -> _npt.NDArray[_np.float64]:
    """Return a finite-seed EMA/RMA whose physical dependency is one window."""
    result = _np.full(values.size, _np.nan, dtype=_np.float64)
    if values.size < window:
        return result
    decay = 1.0 - alpha
    weights = _np.empty(window, dtype=_np.float64)
    weights[0] = decay ** (window - 1)
    if window > 1:
        exponents = _np.arange(window - 2, -1, -1, dtype=_np.float64)
        weights[1:] = alpha * _np.power(decay, exponents)
    result[window - 1 :] = _np.correlate(values, weights, mode="valid")
    result[~_np.isfinite(result)] = _np.nan
    return result


def _uls_true_range(
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
) -> _npt.NDArray[_np.float64]:
    result = (high - low).astype(_np.float64, copy=True)
    if result.size > 1:
        result[1:] = _np.maximum.reduce(
            (
                high[1:] - low[1:],
                _np.abs(high[1:] - close[:-1]),
                _np.abs(low[1:] - close[:-1]),
            )
        )
    return result


def _uls_atr(
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
    *,
    length: int,
    window: int,
) -> _npt.NDArray[_np.float64]:
    return _finite_recursive_average(
        _uls_true_range(high, low, close),
        alpha=1.0 / length,
        window=window,
    )


def _uls_ema(
    values: _npt.NDArray[_np.float64],
    *,
    length: int,
    window: int,
) -> _npt.NDArray[_np.float64]:
    return _finite_recursive_average(values, alpha=2.0 / (length + 1.0), window=window)


def _rolling_percent_rank(
    values: _npt.NDArray[_np.float64],
    length: int,
) -> _npt.NDArray[_np.float64]:
    """Return the percentage of other window members strictly below current."""
    result = _np.full(values.size, _np.nan, dtype=_np.float64)
    ordered: list[float] = []
    denominator = max(1, length - 1)
    for index, raw in enumerate(values):
        value = float(raw)
        if not _math.isfinite(value):
            ordered.clear()
            continue
        _bisect.insort(ordered, value)
        if index >= length and _math.isfinite(float(values[index - length])):
            old = float(values[index - length])
            del ordered[_bisect.bisect_left(ordered, old)]
        if len(ordered) == length:
            result[index] = 100.0 * _bisect.bisect_left(ordered, value) / denominator
    return result


@_dataclasses.dataclass
class _UpLiquidity:
    price: float
    last_bar: int
    born_bar: int
    touches: int = 1
    used: bool = False


@_dataclasses.dataclass
class _UpFvg:
    top: float
    bottom: float
    born_bar: int
    bullish: bool
    inverted: bool = False
    inversion_bar: int = -1
    used: bool = False


@_dataclasses.dataclass
class _UpPushZone:
    top: float
    bottom: float
    leg_high: float
    leg_low: float
    born_bar: int
    active: bool = True
    used: bool = False


@_dataclasses.dataclass
class _UpSetup:
    direction: int
    state: int = 0
    liquidity: float = _math.nan
    liquidity_touches: int = 0
    sweep_bar: int = -1
    sweep_extreme: float = _math.nan
    sweep_wick: float = 0.0
    sweep_volume_ok: bool = True
    key_ok: bool = False
    return_bar: int = -1
    poi_top: float = _math.nan
    poi_bottom: float = _math.nan
    poi_bar: int = -1
    poi_source: int = 0
    structure: float = _math.nan
    trend_x1: int = -1
    trend_x2: int = -1
    trend_y1: float = _math.nan
    trend_y2: float = _math.nan
    trend_ready: bool = False
    displacement: float = 0.0
    score: float = 0.0
    bos: bool = False
    trendline: bool = False
    cisd: bool = False

    def reset(self) -> None:
        fresh = _UpSetup(self.direction)
        for field in _dataclasses.fields(self):
            setattr(self, field.name, getattr(fresh, field.name))


@_dataclasses.dataclass
class _UpCisdState:
    in_bull_run: bool = False
    in_bear_run: bool = False
    last_bull_open: float = _math.nan
    last_bear_open: float = _math.nan
    bull_run_bar: int = -1
    bear_run_bar: int = -1
    bull_broken: bool = True
    bear_broken: bool = True
    cisd_bear_bar: int = -1
    cisd_bull_bar: int = -1


@_dataclasses.dataclass(frozen=True)
class _UlsPushParams:
    engine_mode: str
    atr_len: int
    atr_window: int
    pivot_len: int
    liquidity_mode: str
    max_levels: int
    equal_tolerance_atr: float
    min_equal_touches: int
    liquidity_age: int
    cisd_max_age: int
    sweep_confirmation: str
    return_bars: int
    min_sweep_atr: float
    max_sweep_atr: float
    minimum_tick: float
    ifvg_search: int
    inversion_mode: str
    max_ifvg_distance_atr: float
    use_ifvg_size: bool
    min_ifvg_atr: float
    max_ifvg_atr: float
    fvg_age: int
    push_bars: int
    push_atr: float
    push_volume_multiple: float
    zone_uses_wicks: bool
    max_push_zones: int
    min_retracement_pct: float
    max_retracement_pct: float
    require_rejection: bool
    rejection_ratio: float
    push_zone_age: int
    use_momentum: bool
    momentum_len: int
    momentum_slope: int
    momentum_window: int
    use_key_levels: bool
    key_level_mode: str
    key_level_atr_multiple: float
    key_level_manual_step: float
    key_level_tolerance_atr: float
    require_key_level: bool
    confirmation_mode: str
    confirmation_wait: int
    min_trendline_touches: int
    trendline_tolerance_atr: float
    trendline_break_mode: str
    total_setup_bars: int
    cancel_opposite: bool
    ema_filter: str
    ema_len: int
    ema_window: int
    use_volume_filter: bool
    volume_multiple: float
    use_displacement_filter: bool
    displacement_atr: float
    use_score_filter: bool
    min_score: int
    min_volatility_rank: int


class _UlsPushOutput(_typing.NamedTuple):
    columns: dict[str, _npt.NDArray[_np.float64]]


class _UlsIndicators(_typing.NamedTuple):
    atr_exec: _npt.NDArray[_np.float64]
    atr_signal: _npt.NDArray[_np.float64]
    volume_relative: _npt.NDArray[_np.float64]
    volatility_rank: _npt.NDArray[_np.float64]
    ema: _npt.NDArray[_np.float64]
    momentum: _npt.NDArray[_np.float64]


def _up_clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _up_line_value(x1: int, y1: float, x2: int, y2: float, query: int) -> float:
    slope = (y2 - y1) / max(1, x2 - x1)
    return y1 + slope * (query - x1)


def _up_key_step(atr_signal: float, params: _UlsPushParams) -> float:
    if not params.use_key_levels:
        return 0.0
    if params.key_level_mode == "manual":
        return params.key_level_manual_step
    target = atr_signal * params.key_level_atr_multiple
    magnitude = 10.0 ** _math.floor(_math.log10(max(target, 1e-10)))
    ratio = target / magnitude
    if ratio <= 1.5:
        return magnitude
    if ratio <= 3.5:
        return magnitude * 2.5
    if ratio <= 7.5:
        return magnitude * 5.0
    return magnitude * 10.0


def _up_near_key(price: float, step: float, tolerance: float) -> bool:
    if step <= 0.0:
        return False
    nearest = _math.floor(price / step + 0.5) * step
    return abs(price - nearest) <= tolerance


def _up_is_pivot_high(values: _npt.NDArray[_np.float64], center: int, length: int) -> bool:
    if center < length or center + length >= values.size:
        return False
    candidate = float(values[center])
    left = values[center - length : center]
    right = values[center + 1 : center + length + 1]
    return candidate > float(left.max()) and candidate > float(right.max())


def _up_is_pivot_low(values: _npt.NDArray[_np.float64], center: int, length: int) -> bool:
    if center < length or center + length >= values.size:
        return False
    candidate = float(values[center])
    left = values[center - length : center]
    right = values[center + 1 : center + length + 1]
    return candidate < float(left.min()) and candidate < float(right.min())


def _up_base_offset(
    opened: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
    index: int,
    start_offset: int,
    *,
    bullish: bool,
) -> int:
    result = start_offset
    for offset in range(start_offset, min(start_offset + 12, index) + 1):
        source = index - offset
        opposite = close[source] < opened[source] if bullish else close[source] > opened[source]
        if opposite:
            return offset
    return result


def _up_recent_extreme_offset(
    values: _npt.NDArray[_np.float64],
    index: int,
    length: int,
    *,
    highest: bool,
) -> int:
    start = index - length + 1
    window = values[start : index + 1]
    target = float(window.max() if highest else window.min())
    matches = _np.flatnonzero(window == target)
    return index - (start + int(matches[-1]))


def _up_push_score(
    *,
    retracement: float,
    wick_ratio: float,
    volume_relative: float,
    key_ok: bool,
    momentum_ok: bool,
    age: int,
    max_age: int,
) -> float:
    return (
        20.0 * (1.0 - _up_clamp(abs(retracement - 55.0) / 55.0, 0.0, 1.0))
        + 20.0 * _up_clamp(wick_ratio, 0.0, 1.0)
        + 20.0 * _up_clamp(volume_relative / 2.0, 0.0, 1.0)
        + 15.0 * (1.0 - _up_clamp(age / max(1, max_age), 0.0, 1.0))
        + (15.0 if key_ok else 0.0)
        + (10.0 if momentum_ok else 0.0)
    )


def _up_empty_columns(size: int) -> dict[str, _npt.NDArray[_np.float64]]:
    result = {
        name: _np.full(size, _np.nan, dtype=_np.float64) for name in _ULS_PUSH_FEATURE_NAMES
    }
    result["up_event_signed"].fill(0.0)
    return result


def _up_update_cisd(
    state: _UpCisdState,
    *,
    index: int,
    opened: float,
    close: float,
    max_age: int,
) -> None:
    if state.bull_run_bar >= 0 and index - state.bull_run_bar > max_age:
        state.in_bull_run = False
        state.last_bull_open = _math.nan
        state.bull_run_bar = -1
        state.bull_broken = True
    if state.bear_run_bar >= 0 and index - state.bear_run_bar > max_age:
        state.in_bear_run = False
        state.last_bear_open = _math.nan
        state.bear_run_bar = -1
        state.bear_broken = True

    bar_up = close > opened
    bar_down = close < opened
    if bar_up:
        if not state.in_bull_run:
            state.in_bull_run = True
            state.last_bull_open = opened
            state.bull_run_bar = index
            state.bull_broken = False
        state.in_bear_run = False
    elif bar_down:
        if not state.in_bear_run:
            state.in_bear_run = True
            state.last_bear_open = opened
            state.bear_run_bar = index
            state.bear_broken = False
        state.in_bull_run = False
    if (
        _math.isfinite(state.last_bull_open)
        and not state.bull_broken
        and bar_down
        and close < state.last_bull_open
    ):
        state.cisd_bear_bar = index
        state.bull_broken = True
    if (
        _math.isfinite(state.last_bear_open)
        and not state.bear_broken
        and bar_up
        and close > state.last_bear_open
    ):
        state.cisd_bull_bar = index
        state.bear_broken = True


def _up_indicators(bars: _AggregatedStrategyBars, params: _UlsPushParams) -> _UlsIndicators:
    true_range = _uls_true_range(bars.high, bars.low, bars.close)
    atr_exec = _uls_atr(
        bars.high,
        bars.low,
        bars.close,
        length=params.atr_len,
        window=params.atr_window,
    )
    atr_signal = _rolling_sma(true_range, 20)
    volume_reference = _rolling_sma(bars.tick_volume, 20)
    volume_relative = _np.ones(bars.close.size, dtype=_np.float64)
    ready = _np.isfinite(volume_reference) & (volume_reference > 0.0)
    volume_relative[ready] = bars.tick_volume[ready] / volume_reference[ready]
    volatility_rank = _rolling_percent_rank(atr_exec, 500)
    ema = (
        _uls_ema(
            bars.close,
            length=params.ema_len,
            window=params.ema_window,
        )
        if params.ema_filter == "current"
        else _np.full(bars.close.size, _np.nan, dtype=_np.float64)
    )
    momentum = (
        _uls_ema(
            bars.close,
            length=params.momentum_len,
            window=params.momentum_window,
        )
        if params.use_momentum
        else _np.full(bars.close.size, _np.nan, dtype=_np.float64)
    )
    return _UlsIndicators(
        atr_exec,
        atr_signal,
        volume_relative,
        volatility_rank,
        ema,
        momentum,
    )


def _up_add_liquidity(
    levels: list[_UpLiquidity],
    *,
    price: float,
    pivot_bar: int,
    tolerance: float,
    high_side: bool,
    maximum: int,
) -> None:
    for level in levels:
        if not level.used and abs(level.price - price) <= tolerance:
            level.touches += 1
            level.price = max(level.price, price) if high_side else min(level.price, price)
            level.last_bar = pivot_bar
            return
    levels.append(_UpLiquidity(price, pivot_bar, pivot_bar))
    if len(levels) > maximum:
        del levels[0]


def _up_liquidity_eligible(level: _UpLiquidity, params: _UlsPushParams) -> bool:
    if level.used:
        return False
    return params.liquidity_mode != "equal" or level.touches >= params.min_equal_touches


def _up_consume_liquidity(
    levels: list[_UpLiquidity],
    *,
    price: float,
    minimum_tick: float,
) -> None:
    for level in levels:
        if abs(level.price - price) <= minimum_tick:
            level.used = True


def _up_start_setup(
    setup: _UpSetup,
    *,
    index: int,
    bars: _AggregatedStrategyBars,
    levels: list[_UpLiquidity],
    atr_signal: float,
    volume_relative: float,
    key_step: float,
    params: _UlsPushParams,
) -> None:
    if index == 0:
        return
    best: _UpLiquidity | None = None
    best_rank = -_math.inf
    start50 = max(0, index - 49)
    low50 = float(bars.low[start50 : index + 1].min())
    high50 = float(bars.high[start50 : index + 1].max())
    for level in levels:
        if not _up_liquidity_eligible(level, params):
            continue
        crossed = (
            bars.high[index] > level.price and bars.high[index - 1] <= level.price
            if setup.direction < 0
            else bars.low[index] < level.price and bars.low[index - 1] >= level.price
        )
        if not crossed:
            continue
        recency = 1.0 - _up_clamp(
            (index - level.last_bar) / max(1, params.liquidity_age), 0.0, 1.0
        )
        proximity = 1.0 - _up_clamp(
            abs(float(bars.close[index]) - level.price) / (atr_signal * 10.0), 0.0, 1.0
        )
        prominence = (
            _up_clamp((level.price - low50) / (atr_signal * 5.0), 0.0, 1.0)
            if setup.direction < 0
            else _up_clamp((high50 - level.price) / (atr_signal * 5.0), 0.0, 1.0)
        )
        equal_bonus = (
            1.0
            if params.liquidity_mode == "both"
            and level.touches >= params.min_equal_touches
            else 0.0
        )
        key_bonus = (
            1.5
            if _up_near_key(
                level.price,
                key_step,
                params.key_level_tolerance_atr * atr_signal,
            )
            else 0.0
        )
        rank = 2.0 * level.touches + recency + proximity + prominence + equal_bonus + key_bonus
        if rank > best_rank:
            best = level
            best_rank = rank
    if best is None:
        return
    setup.liquidity = best.price
    setup.liquidity_touches = best.touches
    setup.sweep_bar = index
    setup.sweep_extreme = (
        float(bars.high[index]) if setup.direction < 0 else float(bars.low[index])
    )
    candle_range = float(bars.high[index] - bars.low[index])
    if setup.direction < 0:
        wick = float(bars.high[index] - max(bars.opened[index], bars.close[index]))
    else:
        wick = float(min(bars.opened[index], bars.close[index]) - bars.low[index])
    setup.sweep_wick = wick / candle_range if candle_range > 0.0 else 0.0
    setup.sweep_volume_ok = (
        not params.use_volume_filter or volume_relative > params.volume_multiple
    )
    setup.key_ok = _up_near_key(
        best.price,
        key_step,
        params.key_level_tolerance_atr * atr_signal,
    )
    setup.state = 2


def _up_prepare_structure(
    setup: _UpSetup,
    *,
    index: int,
    bars: _AggregatedStrategyBars,
    pivot_high_prices: list[float],
    pivot_high_bars: list[int],
    pivot_low_prices: list[float],
    pivot_low_bars: list[int],
    atr_signal: float,
    params: _UlsPushParams,
) -> None:
    prices = pivot_low_prices if setup.direction < 0 else pivot_high_prices
    pivot_bars = pivot_low_bars if setup.direction < 0 else pivot_high_bars
    setup.structure = _math.nan
    for price in reversed(prices):
        if (setup.direction < 0 and price < bars.close[index]) or (
            setup.direction > 0 and price > bars.close[index]
        ):
            setup.structure = price
            break
    setup.trend_ready = False
    if len(prices) < 2:
        return
    setup.trend_x1, setup.trend_x2 = pivot_bars[-2], pivot_bars[-1]
    setup.trend_y1, setup.trend_y2 = prices[-2], prices[-1]
    slopes_toward_break = (
        setup.trend_y2 > setup.trend_y1
        if setup.direction < 0
        else setup.trend_y2 < setup.trend_y1
    )
    if not slopes_toward_break or setup.trend_x2 <= setup.trend_x1:
        return
    touches = 0
    for pivot_bar, price in zip(pivot_bars, prices, strict=True):
        if setup.trend_x1 <= pivot_bar <= setup.trend_x2:
            line_value = _up_line_value(
                setup.trend_x1,
                setup.trend_y1,
                setup.trend_x2,
                setup.trend_y2,
                pivot_bar,
            )
            if abs(price - line_value) <= params.trendline_tolerance_atr * atr_signal:
                touches += 1
    current_line = _up_line_value(
        setup.trend_x1,
        setup.trend_y1,
        setup.trend_x2,
        setup.trend_y2,
        index,
    )
    on_unbroken_side = (
        bars.close[index] > current_line
        if setup.direction < 0
        else bars.close[index] < current_line
    )
    setup.trend_ready = touches >= params.min_trendline_touches and on_unbroken_side


def _up_confirmation_required(mode: str, *, trend: bool, bos: bool, cisd: bool) -> bool:
    count = int(trend) + int(bos) + int(cisd)
    if mode == "trendline":
        return trend
    if mode == "bos":
        return bos
    if mode == "cisd":
        return cisd
    if mode == "trendline_or_bos":
        return trend or bos
    if mode == "any":
        return count >= 1
    if mode == "two":
        return count >= 2
    return count == 3


def _up_setup_score(
    setup: _UpSetup,
    *,
    index: int,
    atr_signal: float,
    raw_push: bool,
    params: _UlsPushParams,
) -> float:
    sweep_distance = (
        setup.sweep_extreme - setup.liquidity
        if setup.direction < 0
        else setup.liquidity - setup.sweep_extreme
    )
    return (
        12.0
        * (
            1.0
            - _up_clamp(
                sweep_distance
                / max(atr_signal * params.max_sweep_atr, params.minimum_tick),
                0.0,
                1.0,
            )
        )
        + 8.0
        * (
            1.0
            - _up_clamp(
                (setup.return_bar - setup.sweep_bar) / max(1, params.return_bars),
                0.0,
                1.0,
            )
        )
        + 8.0 * _up_clamp(setup.sweep_wick, 0.0, 1.0)
        + 12.0
        * _up_clamp((setup.poi_top - setup.poi_bottom) / (atr_signal * 0.5), 0.0, 1.0)
        + 8.0
        * _up_clamp(setup.displacement / max(params.displacement_atr, 0.01), 0.0, 1.0)
        + (18.0 if setup.trendline and setup.bos else 11.0 if setup.trendline or setup.bos else 0.0)
        + (9.0 if setup.cisd else 0.0)
        + (10.0 if setup.key_ok else 0.0)
        + (15.0 if setup.poi_source == 2 else 8.0 if raw_push else 0.0)
    )


def _up_advance_setup(
    setup: _UpSetup,
    *,
    index: int,
    bars: _AggregatedStrategyBars,
    fvgs: list[_UpFvg],
    push_zones: list[_UpPushZone],
    pivot_high_prices: list[float],
    pivot_high_bars: list[int],
    pivot_low_prices: list[float],
    pivot_low_bars: list[int],
    cisd_bear_bar: int,
    cisd_bull_bar: int,
    raw_push_up: bool,
    raw_push_down: bool,
    atr_exec: float,
    atr_signal: float,
    ema_value: float,
    momentum_up: bool,
    momentum_down: bool,
    params: _UlsPushParams,
) -> None:
    if setup.state == 2:
        setup.sweep_extreme = (
            max(setup.sweep_extreme, float(bars.high[index]))
            if setup.direction < 0
            else min(setup.sweep_extreme, float(bars.low[index]))
        )
        if params.sweep_confirmation == "wick":
            returned = (
                bars.low[index] < setup.liquidity
                if setup.direction < 0
                else bars.high[index] > setup.liquidity
            )
        else:
            returned = (
                bars.close[index] < setup.liquidity
                if setup.direction < 0
                else bars.close[index] > setup.liquidity
            )
        if returned:
            distance = (
                setup.sweep_extreme - setup.liquidity
                if setup.direction < 0
                else setup.liquidity - setup.sweep_extreme
            )
            if (
                distance > params.max_sweep_atr * atr_signal
                or distance < params.min_sweep_atr * atr_signal
                or not setup.sweep_volume_ok
                or (params.require_key_level and not setup.key_ok)
            ):
                setup.reset()
            elif params.sweep_confirmation == "close_confirm":
                setup.state = 3
            else:
                setup.state = 4
                setup.return_bar = index
        elif index - setup.sweep_bar >= params.return_bars:
            setup.reset()
        return
    if setup.state == 3:
        confirmed = (
            bars.close[index] < setup.liquidity and bars.close[index] < bars.opened[index]
            if setup.direction < 0
            else bars.close[index] > setup.liquidity and bars.close[index] > bars.opened[index]
        )
        if confirmed:
            setup.state = 4
            setup.return_bar = index
        else:
            setup.reset()
        return
    if setup.state == 4:
        invalidated = (
            bars.close[index] > setup.liquidity
            if setup.direction < 0
            else bars.close[index] < setup.liquidity
        )
        if invalidated or index - setup.return_bar > params.ifvg_search:
            setup.reset()
            return
        selected = False
        for fvg in reversed(fvgs):
            needed_bullish = setup.direction < 0
            if (
                not selected
                and fvg.inverted
                and fvg.bullish == needed_bullish
                and not fvg.used
                and setup.return_bar <= fvg.inversion_bar <= index
            ):
                midpoint = (fvg.top + fvg.bottom) / 2.0
                size = fvg.top - fvg.bottom
                distance_ok = abs(midpoint - setup.liquidity) <= (
                    params.max_ifvg_distance_atr * atr_signal
                )
                size_ok = not params.use_ifvg_size or (
                    size >= params.min_ifvg_atr * atr_signal
                    and size <= params.max_ifvg_atr * atr_signal
                )
                displacement_ok = not params.use_displacement_filter or (
                    abs(float(bars.close[index] - bars.opened[index]))
                    >= params.displacement_atr * atr_signal
                )
                if distance_ok and size_ok and displacement_ok:
                    fvg.used = True
                    setup.poi_top = fvg.top
                    setup.poi_bottom = fvg.bottom
                    setup.poi_source = 1
                    selected = True
        if not selected and params.engine_mode == "merged":
            for zone in reversed(push_zones):
                if (
                    not selected
                    and zone.active
                    and not zone.used
                    and zone.born_bar >= setup.return_bar - params.push_bars
                    and abs((zone.top + zone.bottom) / 2.0 - setup.liquidity)
                    <= params.max_ifvg_distance_atr * atr_signal
                ):
                    zone.used = True
                    setup.poi_top = zone.top
                    setup.poi_bottom = zone.bottom
                    setup.poi_source = 2
                    selected = True
        if selected:
            setup.poi_bar = index
            setup.displacement = abs(float(bars.close[index] - bars.opened[index])) / atr_signal
            _up_prepare_structure(
                setup,
                index=index,
                bars=bars,
                pivot_high_prices=pivot_high_prices,
                pivot_high_bars=pivot_high_bars,
                pivot_low_prices=pivot_low_prices,
                pivot_low_bars=pivot_low_bars,
                atr_signal=atr_signal,
                params=params,
            )
            setup.state = 6
        return
    if setup.state != 6:
        return
    invalidated = (
        bars.close[index] > setup.poi_top
        if setup.direction < 0
        else bars.close[index] < setup.poi_bottom
    )
    if invalidated or index - setup.poi_bar > params.confirmation_wait:
        setup.reset()
        return
    bos = _math.isfinite(setup.structure) and (
        bars.close[index] < setup.structure
        if setup.direction < 0
        else bars.close[index] > setup.structure
    )
    trend = False
    if setup.trend_ready:
        line_value = _up_line_value(
            setup.trend_x1,
            setup.trend_y1,
            setup.trend_x2,
            setup.trend_y2,
            index,
        )
        if setup.direction < 0:
            trend = (
                bars.low[index] < line_value
                if params.trendline_break_mode == "wick"
                else bars.close[index] < line_value
            )
        else:
            trend = (
                bars.high[index] > line_value
                if params.trendline_break_mode == "wick"
                else bars.close[index] > line_value
            )
    cisd_bar = cisd_bear_bar if setup.direction < 0 else cisd_bull_bar
    cisd = setup.return_bar <= cisd_bar <= index
    if not _up_confirmation_required(params.confirmation_mode, trend=trend, bos=bos, cisd=cisd):
        return
    ema_ok = params.ema_filter == "off" or (
        _math.isfinite(ema_value)
        and (
            bars.close[index] < ema_value
            if setup.direction < 0
            else bars.close[index] > ema_value
        )
    )
    momentum_ok = momentum_down if setup.direction < 0 else momentum_up
    if not ema_ok or not momentum_ok:
        return
    setup.bos = bos
    setup.trendline = trend
    setup.cisd = cisd
    setup.score = _up_setup_score(
        setup,
        index=index,
        atr_signal=atr_signal,
        raw_push=raw_push_down if setup.direction < 0 else raw_push_up,
        params=params,
    )
    if params.use_score_filter and setup.score < params.min_score:
        setup.reset()
    else:
        setup.state = 8


def _up_opposite_liquidity(
    levels: list[_UpLiquidity],
    *,
    price: float,
    above: bool,
) -> float:
    candidates = [
        level.price
        for level in levels
        if (level.price > price if above else level.price < price)
    ]
    if not candidates:
        return _math.nan
    return min(candidates) if above else max(candidates)


def _up_record_setup_event(
    columns: dict[str, _npt.NDArray[_np.float64]],
    *,
    index: int,
    setup: _UpSetup,
    bars: _AggregatedStrategyBars,
    indicators: _UlsIndicators,
    liquidity_highs: list[_UpLiquidity],
    liquidity_lows: list[_UpLiquidity],
) -> None:
    close = float(bars.close[index])
    direction = float(setup.direction)
    columns["up_event_signed"][index] = direction
    columns["up_source"][index] = float(setup.poi_source)
    columns["up_score"][index] = setup.score
    columns["up_entry_ref"][index] = close
    columns["up_sweep_extreme"][index] = setup.sweep_extreme
    columns["up_poi_top"][index] = setup.poi_top
    columns["up_poi_bot"][index] = setup.poi_bottom
    columns["up_struct_level"][index] = setup.structure
    columns["up_opp_liquidity"][index] = _up_opposite_liquidity(
        liquidity_highs if setup.direction > 0 else liquidity_lows,
        price=close,
        above=setup.direction > 0,
    )
    columns["up_atr_exec"][index] = indicators.atr_exec[index]
    columns["up_atr_sig"][index] = indicators.atr_signal[index]
    columns["up_liq_price"][index] = setup.liquidity
    columns["up_liq_touches"][index] = float(setup.liquidity_touches)
    columns["up_sweep_dist_atr"][index] = abs(setup.sweep_extreme - setup.liquidity) / (
        indicators.atr_signal[index]
    )
    columns["up_wick_ratio"][index] = setup.sweep_wick
    columns["up_disp_atr"][index] = setup.displacement
    columns["up_vol_rel"][index] = indicators.volume_relative[index]
    columns["up_vol_rank"][index] = indicators.volatility_rank[index]
    columns["up_bos"][index] = float(setup.bos)
    columns["up_tl"][index] = float(setup.trendline)
    columns["up_cisd"][index] = float(setup.cisd)
    columns["up_key_ok"][index] = float(setup.key_ok)


def _up_record_push_event(
    columns: dict[str, _npt.NDArray[_np.float64]],
    *,
    index: int,
    direction: int,
    zone: _UpPushZone,
    score: float,
    key_ok: bool,
    bars: _AggregatedStrategyBars,
    indicators: _UlsIndicators,
) -> None:
    columns["up_event_signed"][index] = float(direction)
    columns["up_source"][index] = 3.0
    columns["up_score"][index] = score
    columns["up_entry_ref"][index] = bars.close[index]
    columns["up_poi_top"][index] = zone.top
    columns["up_poi_bot"][index] = zone.bottom
    columns["up_atr_exec"][index] = indicators.atr_exec[index]
    columns["up_atr_sig"][index] = indicators.atr_signal[index]
    columns["up_vol_rel"][index] = indicators.volume_relative[index]
    columns["up_vol_rank"][index] = indicators.volatility_rank[index]
    columns["up_key_ok"][index] = float(key_ok)


def _run_uls_push_state(
    bars: _AggregatedStrategyBars,
    params: _UlsPushParams,
) -> _UlsPushOutput:
    size = bars.close.size
    columns = _up_empty_columns(size)
    if size == 0:
        return _UlsPushOutput(columns)
    indicators = _up_indicators(bars, params)
    liquidity_highs: list[_UpLiquidity] = []
    liquidity_lows: list[_UpLiquidity] = []
    pivot_high_prices: list[float] = []
    pivot_high_bars: list[int] = []
    pivot_low_prices: list[float] = []
    pivot_low_bars: list[int] = []
    fvgs: list[_UpFvg] = []
    push_bull: list[_UpPushZone] = []
    push_bear: list[_UpPushZone] = []
    short_setup = _UpSetup(-1)
    long_setup = _UpSetup(1)

    cisd_state = _UpCisdState()
    prior_raw_up = False
    prior_raw_down = False

    uls_on = params.engine_mode in {"uls", "parallel", "merged"}
    push_on = params.engine_mode in {"push", "parallel"}

    for index in range(size):
        atr_exec = float(indicators.atr_exec[index])
        atr_signal = float(indicators.atr_signal[index])
        atr_ok = (
            _math.isfinite(atr_exec)
            and atr_exec > 0.0
            and _math.isfinite(atr_signal)
            and atr_signal > 0.0
        )
        volume_relative = float(indicators.volume_relative[index])
        rank = float(indicators.volatility_rank[index])
        regime_ok = (
            params.min_volatility_rank == 0.0
            or not _math.isfinite(rank)
            or rank >= params.min_volatility_rank
        )
        key_step = _up_key_step(atr_signal, params) if atr_ok else 0.0

        _up_update_cisd(
            cisd_state,
            index=index,
            opened=float(bars.opened[index]),
            close=float(bars.close[index]),
            max_age=params.cisd_max_age,
        )

        if atr_ok and index >= 2 * params.pivot_len:
            center = index - params.pivot_len
            tolerance = params.equal_tolerance_atr * atr_signal
            if _up_is_pivot_high(bars.high, center, params.pivot_len):
                price = float(bars.high[center])
                pivot_high_prices.append(price)
                pivot_high_bars.append(center)
                _up_add_liquidity(
                    liquidity_highs,
                    price=price,
                    pivot_bar=center,
                    tolerance=tolerance,
                    high_side=True,
                    maximum=params.max_levels,
                )
            if _up_is_pivot_low(bars.low, center, params.pivot_len):
                price = float(bars.low[center])
                pivot_low_prices.append(price)
                pivot_low_bars.append(center)
                _up_add_liquidity(
                    liquidity_lows,
                    price=price,
                    pivot_bar=center,
                    tolerance=tolerance,
                    high_side=False,
                    maximum=params.max_levels,
                )
            if len(pivot_high_prices) > 30:
                del pivot_high_prices[0]
                del pivot_high_bars[0]
            if len(pivot_low_prices) > 30:
                del pivot_low_prices[0]
                del pivot_low_bars[0]

        while pivot_high_bars and index - pivot_high_bars[0] > params.liquidity_age:
            del pivot_high_prices[0]
            del pivot_high_bars[0]
        while pivot_low_bars and index - pivot_low_bars[0] > params.liquidity_age:
            del pivot_low_prices[0]
            del pivot_low_bars[0]
        liquidity_highs[:] = [
            level
            for level in liquidity_highs
            if not level.used and index - level.born_bar <= params.liquidity_age
        ]
        liquidity_lows[:] = [
            level
            for level in liquidity_lows
            if not level.used and index - level.born_bar <= params.liquidity_age
        ]

        if atr_ok and index > 1:
            if bars.low[index] > bars.high[index - 2]:
                fvgs.append(
                    _UpFvg(
                        float(bars.low[index]),
                        float(bars.high[index - 2]),
                        index,
                        True,
                    )
                )
            if bars.high[index] < bars.low[index - 2]:
                fvgs.append(
                    _UpFvg(
                        float(bars.low[index - 2]),
                        float(bars.high[index]),
                        index,
                        False,
                    )
                )
        retained_fvgs: list[_UpFvg] = []
        for fvg in fvgs:
            if index - fvg.born_bar > params.fvg_age or fvg.used:
                continue
            if not fvg.inverted and index > fvg.born_bar:
                if fvg.bullish:
                    broken = (
                        bars.low[index] < fvg.bottom
                        if params.inversion_mode == "wick"
                        else bars.close[index] < fvg.bottom
                    )
                else:
                    broken = (
                        bars.high[index] > fvg.top
                        if params.inversion_mode == "wick"
                        else bars.close[index] > fvg.top
                    )
                if broken:
                    fvg.inverted = True
                    fvg.inversion_bar = index
            retained_fvgs.append(fvg)
        fvgs = retained_fvgs[-120:]

        raw_push_up = False
        raw_push_down = False
        if atr_ok and index >= params.push_bars:
            low_offset = _up_recent_extreme_offset(
                bars.low,
                index,
                params.push_bars,
                highest=False,
            )
            high_offset = _up_recent_extreme_offset(
                bars.high,
                index,
                params.push_bars,
                highest=True,
            )
            start = index - params.push_bars + 1
            leg_low = float(bars.low[start : index + 1].min())
            leg_high = float(bars.high[start : index + 1].max())
            size_ok = leg_high - leg_low >= params.push_atr * atr_signal
            volume_ok = (
                float(indicators.volume_relative[start : index + 1].max())
                >= params.push_volume_multiple
            )
            raw_push_up = (
                size_ok
                and volume_ok
                and low_offset > high_offset
                and bars.close[index] > bars.close[index - params.push_bars]
            )
            raw_push_down = (
                size_ok
                and volume_ok
                and high_offset > low_offset
                and bars.close[index] < bars.close[index - params.push_bars]
            )
            if raw_push_up and not prior_raw_up:
                offset = _up_base_offset(
                    bars.opened,
                    bars.close,
                    index,
                    low_offset,
                    bullish=True,
                )
                base = index - offset
                top = (
                    float(bars.high[base])
                    if params.zone_uses_wicks
                    else float(max(bars.opened[base], bars.close[base]))
                )
                bottom = (
                    float(bars.low[base])
                    if params.zone_uses_wicks
                    else float(min(bars.opened[base], bars.close[base]))
                )
                push_bull.append(_UpPushZone(top, bottom, leg_high, leg_low, index))
                push_bull = push_bull[-params.max_push_zones :]
            if raw_push_down and not prior_raw_down:
                offset = _up_base_offset(
                    bars.opened,
                    bars.close,
                    index,
                    high_offset,
                    bullish=False,
                )
                base = index - offset
                top = (
                    float(bars.high[base])
                    if params.zone_uses_wicks
                    else float(max(bars.opened[base], bars.close[base]))
                )
                bottom = (
                    float(bars.low[base])
                    if params.zone_uses_wicks
                    else float(min(bars.opened[base], bars.close[base]))
                )
                push_bear.append(_UpPushZone(top, bottom, leg_high, leg_low, index))
                push_bear = push_bear[-params.max_push_zones :]
        prior_raw_up = raw_push_up
        prior_raw_down = raw_push_down

        momentum_up = not params.use_momentum or (
            index >= params.momentum_slope
            and _math.isfinite(float(indicators.momentum[index]))
            and _math.isfinite(float(indicators.momentum[index - params.momentum_slope]))
            and bars.close[index] > indicators.momentum[index]
            and indicators.momentum[index] > indicators.momentum[index - params.momentum_slope]
        )
        momentum_down = not params.use_momentum or (
            index >= params.momentum_slope
            and _math.isfinite(float(indicators.momentum[index]))
            and _math.isfinite(float(indicators.momentum[index - params.momentum_slope]))
            and bars.close[index] < indicators.momentum[index]
            and indicators.momentum[index] < indicators.momentum[index - params.momentum_slope]
        )

        push_long: tuple[_UpPushZone, float, bool] | None = None
        push_short: tuple[_UpPushZone, float, bool] | None = None
        if atr_ok:
            for zone in reversed(push_bull):
                if not zone.active:
                    continue
                if bars.close[index] < zone.bottom or index - zone.born_bar > params.push_zone_age:
                    zone.active = False
                elif (
                    not zone.used
                    and index > zone.born_bar
                    and bars.low[index] <= zone.top
                    and bars.high[index] >= zone.bottom
                ):
                    retracement = (zone.leg_high - bars.low[index]) / max(
                        zone.leg_high - zone.leg_low, params.minimum_tick
                    ) * 100.0
                    candle_range = float(bars.high[index] - bars.low[index])
                    wick_ratio = (
                        float(bars.close[index] - bars.low[index]) / candle_range
                        if candle_range > 0.0
                        else 0.0
                    )
                    rejected = not params.require_rejection or (
                        bars.close[index] > bars.opened[index]
                        and wick_ratio >= params.rejection_ratio
                    )
                    key_ok = _up_near_key(
                        zone.bottom,
                        key_step,
                        params.key_level_tolerance_atr * atr_signal,
                    )
                    if (
                        params.min_retracement_pct <= retracement <= params.max_retracement_pct
                        and rejected
                        and momentum_up
                        and push_long is None
                    ):
                        zone.used = True
                        score = _up_push_score(
                            retracement=retracement,
                            wick_ratio=wick_ratio,
                            volume_relative=volume_relative,
                            key_ok=key_ok,
                            momentum_ok=momentum_up,
                            age=index - zone.born_bar,
                            max_age=params.push_zone_age,
                        )
                        push_long = (zone, score, key_ok)
            for zone in reversed(push_bear):
                if not zone.active:
                    continue
                if bars.close[index] > zone.top or index - zone.born_bar > params.push_zone_age:
                    zone.active = False
                elif (
                    not zone.used
                    and index > zone.born_bar
                    and bars.high[index] >= zone.bottom
                    and bars.low[index] <= zone.top
                ):
                    retracement = (bars.high[index] - zone.leg_low) / max(
                        zone.leg_high - zone.leg_low, params.minimum_tick
                    ) * 100.0
                    candle_range = float(bars.high[index] - bars.low[index])
                    wick_ratio = (
                        float(bars.high[index] - bars.close[index]) / candle_range
                        if candle_range > 0.0
                        else 0.0
                    )
                    rejected = not params.require_rejection or (
                        bars.close[index] < bars.opened[index]
                        and wick_ratio >= params.rejection_ratio
                    )
                    key_ok = _up_near_key(
                        zone.top,
                        key_step,
                        params.key_level_tolerance_atr * atr_signal,
                    )
                    if (
                        params.min_retracement_pct <= retracement <= params.max_retracement_pct
                        and rejected
                        and momentum_down
                        and push_short is None
                    ):
                        zone.used = True
                        score = _up_push_score(
                            retracement=retracement,
                            wick_ratio=wick_ratio,
                            volume_relative=volume_relative,
                            key_ok=key_ok,
                            momentum_ok=momentum_down,
                            age=index - zone.born_bar,
                            max_age=params.push_zone_age,
                        )
                        push_short = (zone, score, key_ok)

        if atr_ok and uls_on:
            if (
                0 < short_setup.state < 8
                and index - short_setup.sweep_bar > params.total_setup_bars
            ):
                short_setup.reset()
            if (
                0 < long_setup.state < 8
                and index - long_setup.sweep_bar > params.total_setup_bars
            ):
                long_setup.reset()
            if short_setup.state == 0 and regime_ok:
                _up_start_setup(
                    short_setup,
                    index=index,
                    bars=bars,
                    levels=liquidity_highs,
                    atr_signal=atr_signal,
                    volume_relative=volume_relative,
                    key_step=key_step,
                    params=params,
                )
            else:
                _up_advance_setup(
                    short_setup,
                    index=index,
                    bars=bars,
                    fvgs=fvgs,
                    push_zones=push_bear,
                    pivot_high_prices=pivot_high_prices,
                    pivot_high_bars=pivot_high_bars,
                    pivot_low_prices=pivot_low_prices,
                    pivot_low_bars=pivot_low_bars,
                    cisd_bear_bar=cisd_state.cisd_bear_bar,
                    cisd_bull_bar=cisd_state.cisd_bull_bar,
                    raw_push_up=raw_push_up,
                    raw_push_down=raw_push_down,
                    atr_exec=atr_exec,
                    atr_signal=atr_signal,
                    ema_value=float(indicators.ema[index]),
                    momentum_up=momentum_up,
                    momentum_down=momentum_down,
                    params=params,
                )
            if long_setup.state == 0 and regime_ok:
                _up_start_setup(
                    long_setup,
                    index=index,
                    bars=bars,
                    levels=liquidity_lows,
                    atr_signal=atr_signal,
                    volume_relative=volume_relative,
                    key_step=key_step,
                    params=params,
                )
            else:
                _up_advance_setup(
                    long_setup,
                    index=index,
                    bars=bars,
                    fvgs=fvgs,
                    push_zones=push_bull,
                    pivot_high_prices=pivot_high_prices,
                    pivot_high_bars=pivot_high_bars,
                    pivot_low_prices=pivot_low_prices,
                    pivot_low_bars=pivot_low_bars,
                    cisd_bear_bar=cisd_state.cisd_bear_bar,
                    cisd_bull_bar=cisd_state.cisd_bull_bar,
                    raw_push_up=raw_push_up,
                    raw_push_down=raw_push_down,
                    atr_exec=atr_exec,
                    atr_signal=atr_signal,
                    ema_value=float(indicators.ema[index]),
                    momentum_up=momentum_up,
                    momentum_down=momentum_down,
                    params=params,
                )
            if params.cancel_opposite:
                if long_setup.state >= 6 and 0 < short_setup.state < 6:
                    short_setup.reset()
                if short_setup.state >= 6 and 0 < long_setup.state < 6:
                    long_setup.reset()

        event_written = False
        if short_setup.state == 8:
            _up_record_setup_event(
                columns,
                index=index,
                setup=short_setup,
                bars=bars,
                indicators=indicators,
                liquidity_highs=liquidity_highs,
                liquidity_lows=liquidity_lows,
            )
            _up_consume_liquidity(
                liquidity_highs,
                price=short_setup.liquidity,
                minimum_tick=params.minimum_tick,
            )
            short_setup.reset()
            event_written = True
        if long_setup.state == 8:
            if not event_written:
                _up_record_setup_event(
                    columns,
                    index=index,
                    setup=long_setup,
                    bars=bars,
                    indicators=indicators,
                    liquidity_highs=liquidity_highs,
                    liquidity_lows=liquidity_lows,
                )
                _up_consume_liquidity(
                    liquidity_lows,
                    price=long_setup.liquidity,
                    minimum_tick=params.minimum_tick,
                )
                event_written = True
            long_setup.reset()
        if push_on and regime_ok and not event_written and (push_long or push_short):
            choose_long = push_long is not None and (
                push_short is None or push_long[1] >= push_short[1]
            )
            selected_push = push_long if choose_long else push_short
            if selected_push is not None:
                zone, score, key_ok = selected_push
                if not params.use_score_filter or score >= params.min_score:
                    _up_record_push_event(
                        columns,
                        index=index,
                        direction=1 if choose_long else -1,
                        zone=zone,
                        score=score,
                        key_ok=key_ok,
                        bars=bars,
                        indicators=indicators,
                    )

    return _UlsPushOutput(columns)


def _up_choice(name: str, value: object, choices: set[str]) -> str:
    if type(value) is not str or value not in choices:
        raise _FeatureContractError(f"{name} must be one of {', '.join(sorted(choices))}")
    return value


def _up_integer(
    name: str,
    value: object,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if (
        type(value) is not int
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise _FeatureContractError(
            f"{name} must be a Python int >= {minimum}{suffix}"
        )
    return value


def _up_float(
    name: str,
    value: object,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    if (
        type(value) is not float
        or not _np.isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise _FeatureContractError(
            f"{name} must be a finite Python float >= {minimum}{suffix}"
        )
    return value


def _up_bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise _FeatureContractError(f"{name} must be a Python bool")
    return value


class UlsPushV7:
    """Point-in-time ULS+PUSH v7 setup events on complete arithmetic M5 bars."""

    __slots__ = ("_bar_grid_seconds", "_bar_phase_offset_seconds", "_params", "_window")

    def __init__(
        self,
        *,
        engine_mode: str = "merged",
        atr_len: int = 14,
        atr_window: int | None = None,
        pivot_len: int = 5,
        liquidity_mode: str = "both",
        max_levels: int = 20,
        equal_tolerance_atr: float = 0.10,
        min_equal_touches: int = 2,
        liquidity_age: int = 800,
        cisd_max_age: int = 800,
        sweep_confirmation: str = "close",
        return_bars: int = 3,
        min_sweep_atr: float = 0.10,
        max_sweep_atr: float = 0.75,
        minimum_tick: float = 0.01,
        ifvg_search: int = 6,
        inversion_mode: str = "close",
        max_ifvg_distance_atr: float = 2.0,
        use_ifvg_size: bool = False,
        min_ifvg_atr: float = 0.05,
        max_ifvg_atr: float = 1.0,
        fvg_age: int = 300,
        push_bars: int = 6,
        push_atr: float = 1.5,
        push_volume_multiple: float = 1.5,
        zone_uses_wicks: bool = True,
        max_push_zones: int = 6,
        min_retracement_pct: float = 25.0,
        max_retracement_pct: float = 90.0,
        require_rejection: bool = True,
        rejection_ratio: float = 0.45,
        push_zone_age: int = 200,
        use_momentum: bool = False,
        momentum_len: int = 50,
        momentum_slope: int = 5,
        momentum_window: int | None = None,
        use_key_levels: bool = True,
        key_level_mode: str = "auto",
        key_level_atr_multiple: float = 1.5,
        key_level_manual_step: float = 10.0,
        key_level_tolerance_atr: float = 0.25,
        require_key_level: bool = False,
        confirmation_mode: str = "trendline_or_bos",
        confirmation_wait: int = 12,
        min_trendline_touches: int = 2,
        trendline_tolerance_atr: float = 0.10,
        trendline_break_mode: str = "close",
        total_setup_bars: int = 40,
        cancel_opposite: bool = True,
        ema_filter: str = "off",
        ema_len: int = 200,
        ema_window: int | None = None,
        use_volume_filter: bool = False,
        volume_multiple: float = 1.2,
        use_displacement_filter: bool = False,
        displacement_atr: float = 0.5,
        use_score_filter: bool = False,
        min_score: int = 60,
        min_volatility_rank: int = 0,
        hour_baseline: bool = False,
        session_filter: bool = False,
        bar_grid_seconds: int = 300,
        bar_phase_offset_seconds: int = 0,
    ) -> None:
        if _up_bool("hour_baseline", hour_baseline):
            raise _FeatureContractError(
                "hour_baseline requires unavailable verified UTC/native-clock mapping"
            )
        if _up_bool("session_filter", session_filter):
            raise _FeatureContractError(
                "session_filter requires unavailable verified UTC/native-clock mapping"
            )
        if type(bar_grid_seconds) is not int or bar_grid_seconds != 300:
            raise _FeatureContractError("uls_push_v7 bar_grid_seconds is fixed at 300")
        if type(bar_phase_offset_seconds) is not int or bar_phase_offset_seconds != 0:
            raise _FeatureContractError("uls_push_v7 bar_phase_offset_seconds is fixed at 0")

        atr_len_value = _up_integer("atr_len", atr_len, minimum=1)
        ema_len_value = _up_integer("ema_len", ema_len, minimum=2)
        momentum_len_value = _up_integer("momentum_len", momentum_len, minimum=5)
        derived_atr_window = max(300, 20 * atr_len_value)
        derived_ema_window = max(2_000, 10 * ema_len_value)
        derived_momentum_window = max(500, 10 * momentum_len_value)
        for name, supplied, derived in (
            ("atr_window", atr_window, derived_atr_window),
            ("ema_window", ema_window, derived_ema_window),
            ("momentum_window", momentum_window, derived_momentum_window),
        ):
            if supplied is not None and _up_integer(name, supplied, minimum=1) != derived:
                raise _FeatureContractError(
                    f"{name} is derived and must equal {derived} for the selected length"
                )
        params = _UlsPushParams(
            engine_mode=_up_choice(
                "engine_mode", engine_mode, {"uls", "push", "parallel", "merged"}
            ),
            atr_len=atr_len_value,
            atr_window=derived_atr_window,
            pivot_len=_up_integer("pivot_len", pivot_len, minimum=1),
            liquidity_mode=_up_choice(
                "liquidity_mode", liquidity_mode, {"structural", "equal", "both"}
            ),
            max_levels=_up_integer("max_levels", max_levels, minimum=2, maximum=60),
            equal_tolerance_atr=_up_float(
                "equal_tolerance_atr", equal_tolerance_atr
            ),
            min_equal_touches=_up_integer(
                "min_equal_touches", min_equal_touches, minimum=1
            ),
            liquidity_age=_up_integer("liquidity_age", liquidity_age, minimum=20),
            cisd_max_age=_up_integer("cisd_max_age", cisd_max_age, minimum=20),
            sweep_confirmation=_up_choice(
                "sweep_confirmation",
                sweep_confirmation,
                {"wick", "close", "close_confirm"},
            ),
            return_bars=_up_integer("return_bars", return_bars, minimum=1),
            min_sweep_atr=_up_float("min_sweep_atr", min_sweep_atr),
            max_sweep_atr=_up_float("max_sweep_atr", max_sweep_atr, minimum=0.05),
            minimum_tick=_up_float("minimum_tick", minimum_tick),
            ifvg_search=_up_integer("ifvg_search", ifvg_search, minimum=1),
            inversion_mode=_up_choice(
                "inversion_mode", inversion_mode, {"wick", "close"}
            ),
            max_ifvg_distance_atr=_up_float(
                "max_ifvg_distance_atr", max_ifvg_distance_atr, minimum=0.1
            ),
            use_ifvg_size=_up_bool("use_ifvg_size", use_ifvg_size),
            min_ifvg_atr=_up_float("min_ifvg_atr", min_ifvg_atr),
            max_ifvg_atr=_up_float("max_ifvg_atr", max_ifvg_atr, minimum=0.05),
            fvg_age=_up_integer("fvg_age", fvg_age, minimum=20),
            push_bars=_up_integer("push_bars", push_bars, minimum=2, maximum=50),
            push_atr=_up_float("push_atr", push_atr, minimum=0.1),
            push_volume_multiple=_up_float(
                "push_volume_multiple", push_volume_multiple, minimum=0.5
            ),
            zone_uses_wicks=_up_bool("zone_uses_wicks", zone_uses_wicks),
            max_push_zones=_up_integer(
                "max_push_zones", max_push_zones, minimum=1, maximum=20
            ),
            min_retracement_pct=_up_float(
                "min_retracement_pct", min_retracement_pct, maximum=100.0
            ),
            max_retracement_pct=_up_float(
                "max_retracement_pct", max_retracement_pct, maximum=100.0
            ),
            require_rejection=_up_bool("require_rejection", require_rejection),
            rejection_ratio=_up_float(
                "rejection_ratio", rejection_ratio, maximum=1.0
            ),
            push_zone_age=_up_integer("push_zone_age", push_zone_age, minimum=20),
            use_momentum=_up_bool("use_momentum", use_momentum),
            momentum_len=momentum_len_value,
            momentum_slope=_up_integer("momentum_slope", momentum_slope, minimum=1),
            momentum_window=derived_momentum_window,
            use_key_levels=_up_bool("use_key_levels", use_key_levels),
            key_level_mode=_up_choice(
                "key_level_mode", key_level_mode, {"auto", "manual"}
            ),
            key_level_atr_multiple=_up_float(
                "key_level_atr_multiple", key_level_atr_multiple, minimum=0.2
            ),
            key_level_manual_step=_up_float(
                "key_level_manual_step", key_level_manual_step, minimum=0.00001
            ),
            key_level_tolerance_atr=_up_float(
                "key_level_tolerance_atr", key_level_tolerance_atr, minimum=0.01
            ),
            require_key_level=_up_bool("require_key_level", require_key_level),
            confirmation_mode=_up_choice(
                "confirmation_mode",
                confirmation_mode,
                {"trendline", "bos", "cisd", "trendline_or_bos", "any", "two", "all"},
            ),
            confirmation_wait=_up_integer(
                "confirmation_wait", confirmation_wait, minimum=1
            ),
            min_trendline_touches=_up_integer(
                "min_trendline_touches",
                min_trendline_touches,
                minimum=2,
                maximum=6,
            ),
            trendline_tolerance_atr=_up_float(
                "trendline_tolerance_atr", trendline_tolerance_atr, minimum=0.01
            ),
            trendline_break_mode=_up_choice(
                "trendline_break_mode", trendline_break_mode, {"wick", "close"}
            ),
            total_setup_bars=_up_integer(
                "total_setup_bars", total_setup_bars, minimum=5
            ),
            cancel_opposite=_up_bool("cancel_opposite", cancel_opposite),
            ema_filter=_up_choice("ema_filter", ema_filter, {"off", "current", "higher"}),
            ema_len=ema_len_value,
            ema_window=derived_ema_window,
            use_volume_filter=_up_bool("use_volume_filter", use_volume_filter),
            volume_multiple=_up_float("volume_multiple", volume_multiple, minimum=0.1),
            use_displacement_filter=_up_bool(
                "use_displacement_filter", use_displacement_filter
            ),
            displacement_atr=_up_float(
                "displacement_atr", displacement_atr, minimum=0.05
            ),
            use_score_filter=_up_bool("use_score_filter", use_score_filter),
            min_score=_up_integer("min_score", min_score, minimum=0, maximum=100),
            min_volatility_rank=_up_integer(
                "min_volatility_rank",
                min_volatility_rank,
                minimum=0,
                maximum=95,
            ),
        )
        if params.ema_filter == "higher":
            raise _FeatureContractError(
                "higher-timeframe EMA is unavailable in the staged uls_push_v7 provider"
            )
        if params.minimum_tick <= 0.0:
            raise _FeatureContractError("minimum_tick must be strictly positive")
        if params.min_sweep_atr > params.max_sweep_atr:
            raise _FeatureContractError("min_sweep_atr must not exceed max_sweep_atr")
        if params.min_ifvg_atr > params.max_ifvg_atr:
            raise _FeatureContractError("min_ifvg_atr must not exceed max_ifvg_atr")
        if params.min_retracement_pct > params.max_retracement_pct:
            raise _FeatureContractError(
                "min_retracement_pct must not exceed max_retracement_pct"
            )

        liquidity_chain = (
            params.liquidity_age
            + params.total_setup_bars
            + params.pivot_len
            + params.atr_window
        )
        fvg_chain = params.fvg_age + params.total_setup_bars + params.atr_window + 2
        push_chain = (
            params.push_zone_age + params.push_bars + 12 + params.atr_window
        )
        reach_m5 = max(
            liquidity_chain,
            fvg_chain,
            push_chain,
            500 + params.atr_window,
            params.cisd_max_age,
            params.ema_window if params.ema_filter == "current" else 0,
            params.momentum_window + params.momentum_slope if params.use_momentum else 0,
        )
        max_prior_rows = 5 * (reach_m5 + 2) - 1
        self._params = params
        self._bar_grid_seconds = bar_grid_seconds
        self._bar_phase_offset_seconds = bar_phase_offset_seconds
        self._window = _WindowSpec(max_prior_rows, _GapPolicy.CROSS)

    @property
    def provider_id(self) -> str:
        """Return the stable provider identity."""
        return "uls_push_v7"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the ordered ULS+PUSH output columns."""
        return _ULS_PUSH_FEATURE_NAMES

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact M1 inputs consumed by the provider."""
        return ("open", "high", "low", "close", "tick_volume")

    @property
    def window(self) -> _WindowSpec:
        """Return the parameter-derived finite M1 reach."""
        return self._window

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return all identity-bearing strategy and approximation parameters."""
        values: dict[str, _ParamValue] = {
            field.name: _typing.cast("_ParamValue", getattr(self._params, field.name))
            for field in _dataclasses.fields(self._params)
        }
        values["bar_grid_seconds"] = self._bar_grid_seconds
        values["bar_phase_offset_seconds"] = self._bar_phase_offset_seconds
        values["hour_baseline"] = False
        values["session_filter"] = False
        return dict(sorted(values.items()))

    @property
    def cost_hint(self) -> _CostHint:
        """Declare the scalar state machine as expensive."""
        return _CostHint.EXPENSIVE

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute causal complete-M5 events and project them to the M1 axis."""
        if request.unit is not _TimeUnit.SECONDS:
            raise _FeatureContractError("uls_push_v7 requires native keys expressed in seconds")
        keys = request.keys
        deltas = keys.view(_np.uint64)[1:] - keys.view(_np.uint64)[:-1]
        if _np.any(deltas < _np.uint64(60)):
            raise _FeatureContractError(
                "uls_push_v7 requires M1-density input with adjacent observed keys "
                "at least 60 seconds apart"
            )
        bars = _aggregate_complete_strategy_bars(
            keys,
            _np.asarray(request.column("open"), dtype=_np.float64),
            _np.asarray(request.column("high"), dtype=_np.float64),
            _np.asarray(request.column("low"), dtype=_np.float64),
            _np.asarray(request.column("close"), dtype=_np.float64),
            _np.asarray(request.column("tick_volume"), dtype=_np.float64),
            bar_grid_seconds=self._bar_grid_seconds,
            bar_phase_offset_seconds=self._bar_phase_offset_seconds,
        )
        strategy = _run_uls_push_state(bars, self._params)
        result = _up_empty_columns(request.eval_length)
        events = _np.flatnonzero(strategy.columns["up_event_signed"] != 0.0)
        if events.size:
            source_positions = bars.usable_positions[events]
            in_evaluation = (source_positions >= request.eval_offset) & (
                source_positions < request.eval_offset + request.eval_length
            )
            selected = events[in_evaluation]
            output_positions = (source_positions[in_evaluation] - request.eval_offset).astype(
                _np.int64
            )
            for name in _ULS_PUSH_FEATURE_NAMES:
                result[name][output_positions] = strategy.columns[name][selected]
        for values in result.values():
            values[_np.isinf(values)] = _np.nan
        return result


def _uls_market_context_values(
    bars: _AggregatedStrategyBars,
) -> dict[str, _npt.NDArray[_np.float64]]:
    """Return fixed, direction-neutral context from completed M5 bars only."""
    size = bars.close.size
    result = {
        name: _np.full(size, _np.nan, dtype=_np.float64)
        for name in _ULS_MARKET_CONTEXT_NAMES
    }
    if size <= _ULS_CONTEXT_LONGEST_LOOKBACK:
        return result

    atr = _uls_atr(
        bars.high,
        bars.low,
        bars.close,
        length=_ULS_CONTEXT_ATR_LENGTH,
        window=_ULS_CONTEXT_ATR_WINDOW,
    )
    ema = _uls_ema(
        bars.close,
        length=_ULS_CONTEXT_EMA_LENGTH,
        window=_ULS_CONTEXT_EMA_WINDOW,
    )
    true_range = _uls_true_range(bars.high, bars.low, bars.close)
    tr_12 = _rolling_mean(true_range, 12)
    tr_48 = _rolling_mean(true_range, 48)
    volume_5 = _rolling_mean(bars.tick_volume, 5)
    volume_20 = _rolling_mean(bars.tick_volume, 20)
    volume_sq_20 = _rolling_mean(bars.tick_volume * bars.tick_volume, 20)
    volume_variance = _np.maximum(volume_sq_20 - volume_20 * volume_20, 0.0)
    volume_std = _np.sqrt(volume_variance, dtype=_np.float64)
    volume_zscore = _np.zeros(size, dtype=_np.float64)
    nonzero_volume_std = volume_std > 0.0
    volume_zscore[nonzero_volume_std] = (
        bars.tick_volume[nonzero_volume_std] - volume_20[nonzero_volume_std]
    ) / volume_std[nonzero_volume_std]

    high_48 = _np.full(size, _np.nan, dtype=_np.float64)
    low_48 = _np.full(size, _np.nan, dtype=_np.float64)
    high_48[47:] = _np.max(
        _np.lib.stride_tricks.sliding_window_view(bars.high, 48), axis=1
    )
    low_48[47:] = _np.min(
        _np.lib.stride_tricks.sliding_window_view(bars.low, 48), axis=1
    )

    eligible = _np.arange(_ULS_CONTEXT_LONGEST_LOOKBACK, size, dtype=_np.int64)
    close = bars.close[eligible]
    current_atr = atr[eligible]
    current_ema = ema[eligible]
    valid = (
        _np.isfinite(close)
        & (close > 0.0)
        & _np.isfinite(current_atr)
        & (current_atr > 0.0)
        & _np.isfinite(current_ema)
        & _np.isfinite(ema[eligible - 12])
        & _np.isfinite(tr_12[eligible])
        & _np.isfinite(tr_48[eligible])
        & (tr_48[eligible] > 0.0)
        & _np.isfinite(volume_5[eligible])
        & _np.isfinite(volume_20[eligible])
        & (volume_20[eligible] > 0.0)
        & _np.isfinite(volume_zscore[eligible])
        & _np.isfinite(high_48[eligible])
        & _np.isfinite(low_48[eligible])
    )
    selected = eligible[valid]
    if selected.size == 0:
        return result

    selected_close = bars.close[selected]
    selected_atr = atr[selected]
    selected_range = bars.high[selected] - bars.low[selected]
    safe_range = _np.where(selected_range > 0.0, selected_range, 1.0)
    body_high = _np.maximum(bars.opened[selected], selected_close)
    body_low = _np.minimum(bars.opened[selected], selected_close)
    rolling_range = high_48[selected] - low_48[selected]
    safe_rolling_range = _np.where(rolling_range > 0.0, rolling_range, 1.0)

    result["umc_atr_over_close"][selected] = selected_atr / selected_close
    result["umc_volume_zscore_20"][selected] = volume_zscore[selected]
    result["umc_volume_ratio_5_20"][selected] = volume_5[selected] / volume_20[selected]
    result["umc_true_range_ratio_12_48"][selected] = tr_12[selected] / tr_48[selected]
    for lookback in (1, 3, 12, 48):
        prior = bars.close[selected - lookback]
        if _np.any(prior <= 0.0):
            raise _FeatureContractError(
                "uls_market_context_v1 return input is non-positive"
            )
        result[f"umc_log_return_{lookback}"][selected] = _np.log(
            selected_close / prior
        )
    result["umc_ema_distance_atr"][selected] = (
        selected_close - ema[selected]
    ) / selected_atr
    result["umc_ema_slope_12_atr"][selected] = (
        ema[selected] - ema[selected - 12]
    ) / selected_atr
    result["umc_range_over_atr"][selected] = selected_range / selected_atr
    result["umc_body_fraction"][selected] = _np.abs(
        selected_close - bars.opened[selected]
    ) / safe_range
    result["umc_upper_wick_fraction"][selected] = (
        bars.high[selected] - body_high
    ) / safe_range
    result["umc_lower_wick_fraction"][selected] = (
        body_low - bars.low[selected]
    ) / safe_range
    result["umc_room_up_48_atr"][selected] = _np.maximum(
        high_48[selected] - selected_close, 0.0
    ) / selected_atr
    result["umc_room_down_48_atr"][selected] = _np.maximum(
        selected_close - low_48[selected], 0.0
    ) / selected_atr
    result["umc_close_location_48"][selected] = (
        selected_close - ((high_48[selected] + low_48[selected]) / 2.0)
    ) / safe_rolling_range

    if any(not _np.isfinite(values[selected]).all() for values in result.values()):
        raise _FeatureContractError("uls_market_context_v1 produced non-finite context")
    return result


class UlsMarketContextV1:
    """Fixed causal market context aligned to every completed M5 bar."""

    @property
    def provider_id(self) -> str:
        """Return the stable context-provider identity."""
        return "uls_market_context_v1"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the closed context schema."""
        return _ULS_MARKET_CONTEXT_NAMES

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact M1 inputs consumed by the provider."""
        return ("open", "high", "low", "close", "tick_volume")

    @property
    def window(self) -> _WindowSpec:
        """Declare the finite M1 reach of the longest completed-M5 statistic."""
        prior_rows = 5 * (_ULS_CONTEXT_LONGEST_LOOKBACK + 2) - 1
        return _WindowSpec(prior_rows, _GapPolicy.CROSS)

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return an empty mapping because every context setting is version-fixed."""
        return {}

    @property
    def cost_hint(self) -> _CostHint:
        """Declare fixed rolling context as expensive."""
        return _CostHint.EXPENSIVE

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute context from observed rows and project completed M5 bars to M1."""
        if request.unit is not _TimeUnit.SECONDS:
            raise _FeatureContractError(
                "uls_market_context_v1 requires native keys expressed in seconds"
            )
        keys = request.keys
        deltas = keys.view(_np.uint64)[1:] - keys.view(_np.uint64)[:-1]
        if _np.any(deltas < _np.uint64(60)):
            raise _FeatureContractError(
                "uls_market_context_v1 requires M1-density input with adjacent observed "
                "keys at least 60 seconds apart"
            )
        bars = _aggregate_complete_strategy_bars(
            keys,
            _np.asarray(request.column("open"), dtype=_np.float64),
            _np.asarray(request.column("high"), dtype=_np.float64),
            _np.asarray(request.column("low"), dtype=_np.float64),
            _np.asarray(request.column("close"), dtype=_np.float64),
            _np.asarray(request.column("tick_volume"), dtype=_np.float64),
            bar_grid_seconds=_ULS_CONTEXT_BAR_GRID_SECONDS,
            bar_phase_offset_seconds=_ULS_CONTEXT_BAR_PHASE_OFFSET_SECONDS,
        )
        context = _uls_market_context_values(bars)
        result = {
            name: _np.full(request.eval_length, _np.nan, dtype=_np.float64)
            for name in _ULS_MARKET_CONTEXT_NAMES
        }
        source_positions = bars.usable_positions
        in_evaluation = (source_positions >= request.eval_offset) & (
            source_positions < request.eval_offset + request.eval_length
        )
        selected_bars = _np.flatnonzero(in_evaluation).astype(_np.int64, copy=False)
        output_positions = (
            source_positions[in_evaluation] - request.eval_offset
        ).astype(_np.int64, copy=False)
        for name in _ULS_MARKET_CONTEXT_NAMES:
            result[name][output_positions] = context[name][selected_bars]
        return result



_MC_FEATURE_NAMES = (
    "mc_event_signed",
    "mc_long_score",
    "mc_short_score",
)


class _MultiConfluenceParams(_typing.NamedTuple):
    pivot_left: int
    pivot_right: int
    rsi_length: int
    rsi_long_level: int
    rsi_short_level: int
    ema_fast_length: int
    ema_slow_length: int
    atr_length: int
    level_atr_range: float
    trend_atr_range: float
    liquidity_lookback: int
    minimum_score: int
    base_grid_seconds: int
    base_phase_offset_seconds: int
    level_grid_seconds: tuple[int, ...]
    level_phase_offset_seconds: tuple[int, ...]
    rma_window: int
    ema_window: int
    max_level_age_bars: int
    max_trend_age_bars: int


class _MultiConfluenceValues(_typing.NamedTuple):
    event_signed: _npt.NDArray[_np.float64]
    long_score: _npt.NDArray[_np.float64]
    short_score: _npt.NDArray[_np.float64]


def _mc_int(name: str, value: object, *, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise _FeatureContractError(f"{name} must be a Python int >= {minimum}{suffix}")
    return value


def _mc_float(name: str, value: object) -> float:
    if type(value) is not float or not _np.isfinite(value) or value < 0.0:
        raise _FeatureContractError(f"{name} must be a finite non-negative Python float")
    return value


def _mc_validate_grids(
    *,
    base_grid_seconds: object,
    base_phase_offset_seconds: object,
    level_grid_seconds: object,
    level_phase_offset_seconds: object,
) -> tuple[int, int, tuple[int, ...], tuple[int, ...]]:
    if type(base_grid_seconds) is not int or base_grid_seconds != 900:
        raise _FeatureContractError("multi_confluence_m15_v1 base_grid_seconds is fixed at 900")
    if type(base_phase_offset_seconds) is not int or base_phase_offset_seconds != 0:
        raise _FeatureContractError(
            "multi_confluence_m15_v1 base_phase_offset_seconds is fixed at 0"
        )
    if type(level_grid_seconds) is not tuple or not level_grid_seconds:
        raise _FeatureContractError("level_grid_seconds must be a non-empty tuple")
    if type(level_phase_offset_seconds) is not tuple or len(level_phase_offset_seconds) != len(
        level_grid_seconds
    ):
        raise _FeatureContractError(
            "level_phase_offset_seconds must be a tuple matching level_grid_seconds"
        )
    grids: list[int] = []
    phases: list[int] = []
    for index, (grid, phase) in enumerate(
        zip(level_grid_seconds, level_phase_offset_seconds, strict=True)
    ):
        if type(grid) is not int or grid <= base_grid_seconds or grid % base_grid_seconds:
            raise _FeatureContractError(
                f"level_grid_seconds[{index}] must be an integer multiple above M15"
            )
        if type(phase) is not int or not 0 <= phase < grid:
            raise _FeatureContractError(
                f"level_phase_offset_seconds[{index}] must lie inside its grid"
            )
        if (phase - base_phase_offset_seconds) % base_grid_seconds:
            raise _FeatureContractError(
                f"level_phase_offset_seconds[{index}] must align to the M15 grid"
            )
        grids.append(grid)
        phases.append(phase)
    if len(set(grids)) != len(grids):
        raise _FeatureContractError("level_grid_seconds must not contain duplicates")
    return (
        base_grid_seconds,
        base_phase_offset_seconds,
        tuple(grids),
        tuple(phases),
    )


def _mc_is_pivot(
    values: _npt.NDArray[_np.float64],
    center: int,
    *,
    left: int,
    right: int,
    high: bool,
) -> bool:
    if center < left or center + right >= values.size:
        return False
    candidate = float(values[center])
    if not _math.isfinite(candidate):
        return False
    before = values[center - left : center]
    after = values[center + 1 : center + right + 1]
    if not _np.isfinite(before).all() or not _np.isfinite(after).all():
        return False
    if high:
        return candidate > float(before.max()) and candidate > float(after.max())
    return candidate < float(before.min()) and candidate < float(after.min())


def _mc_confirmed_level_state(
    values: _npt.NDArray[_np.float64],
    *,
    left: int,
    right: int,
    max_age: int,
    high: bool,
) -> _npt.NDArray[_np.float64]:
    """Return latest confirmed pivot values with an explicit post-confirmation TTL."""
    result = _np.full(values.size, _np.nan, dtype=_np.float64)
    active_value = _math.nan
    confirmed_at = -1
    for index in range(values.size):
        center = index - right
        if _mc_is_pivot(values, center, left=left, right=right, high=high):
            active_value = float(values[center])
            confirmed_at = index
        if confirmed_at >= 0 and index - confirmed_at <= max_age:
            result[index] = active_value
    return result


def _mc_project_completed_values(
    usable_positions: _npt.NDArray[_np.int64],
    values: _npt.NDArray[_np.float64],
    query_positions: _npt.NDArray[_np.int64],
) -> _npt.NDArray[_np.float64]:
    result = _np.full(query_positions.size, _np.nan, dtype=_np.float64)
    latest = _np.searchsorted(usable_positions, query_positions, side="right") - 1
    available = latest >= 0
    result[available] = values[latest[available]]
    return result


def _mc_multitimeframe_levels(
    keys: _npt.NDArray[_np.int64],
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    close: _npt.NDArray[_np.float64],
    query_positions: _npt.NDArray[_np.int64],
    params: _MultiConfluenceParams,
) -> tuple[
    tuple[_npt.NDArray[_np.float64], ...],
    tuple[_npt.NDArray[_np.float64], ...],
]:
    dummy = _np.zeros(close.size, dtype=_np.float64)
    supports: list[_npt.NDArray[_np.float64]] = []
    resistances: list[_npt.NDArray[_np.float64]] = []
    for grid, phase in zip(
        params.level_grid_seconds,
        params.level_phase_offset_seconds,
        strict=True,
    ):
        bars = _aggregate_complete_strategy_bars(
            keys,
            close,
            high,
            low,
            close,
            dummy,
            bar_grid_seconds=grid,
            bar_phase_offset_seconds=phase,
        )
        if bars.close.size <= 1:
            supports.append(_np.full(query_positions.size, _np.nan, dtype=_np.float64))
            resistances.append(_np.full(query_positions.size, _np.nan, dtype=_np.float64))
            continue

        # The leading bucket may begin before the bounded context. Dropping it
        # makes every pivot bar used here complete within the declared reach.
        usable = bars.usable_positions[1:]
        support_state = _mc_confirmed_level_state(
            bars.low[1:],
            left=params.pivot_left,
            right=params.pivot_right,
            max_age=params.max_level_age_bars,
            high=False,
        )
        resistance_state = _mc_confirmed_level_state(
            bars.high[1:],
            left=params.pivot_left,
            right=params.pivot_right,
            max_age=params.max_level_age_bars,
            high=True,
        )
        supports.append(_mc_project_completed_values(usable, support_state, query_positions))
        resistances.append(_mc_project_completed_values(usable, resistance_state, query_positions))
    return tuple(supports), tuple(resistances)


def _mc_rsi(
    close: _npt.NDArray[_np.float64],
    *,
    length: int,
    window: int,
) -> _npt.NDArray[_np.float64]:
    result = _np.full(close.size, _np.nan, dtype=_np.float64)
    if close.size < 2:
        return result
    changes = close[1:] - close[:-1]
    gains = _np.maximum(changes, 0.0)
    losses = _np.maximum(-changes, 0.0)
    average_gain = _finite_recursive_average(gains, alpha=1.0 / length, window=window)
    average_loss = _finite_recursive_average(losses, alpha=1.0 / length, window=window)
    valid = _np.isfinite(average_gain) & _np.isfinite(average_loss)
    positive_loss = valid & (average_loss > 0.0)
    values = _np.full(changes.size, _np.nan, dtype=_np.float64)
    ratio = _np.zeros(changes.size, dtype=_np.float64)
    ratio[positive_loss] = average_gain[positive_loss] / average_loss[positive_loss]
    values[positive_loss] = 100.0 - 100.0 / (1.0 + ratio[positive_loss])
    only_gain = valid & (average_loss == 0.0) & (average_gain > 0.0)
    values[only_gain] = 100.0
    only_loss = valid & (average_gain == 0.0) & (average_loss > 0.0)
    values[only_loss] = 0.0
    result[1:] = values
    return result


def _mc_previous_extreme(
    values: _npt.NDArray[_np.float64],
    lookback: int,
    *,
    highest: bool,
) -> _npt.NDArray[_np.float64]:
    result = _np.full(values.size, _np.nan, dtype=_np.float64)
    if values.size <= lookback:
        return result
    windows = _np.lib.stride_tricks.sliding_window_view(values, lookback)
    prior = windows[:-1]
    result[lookback:] = _np.max(prior, axis=1) if highest else _np.min(prior, axis=1)
    return result


def _mc_line_price(
    points: list[tuple[int, float, int]],
    index: int,
) -> float:
    if len(points) < 2:
        return _math.nan
    older_bar, older_price, _ = points[-2]
    newer_bar, newer_price, _ = points[-1]
    if newer_bar == older_bar:
        return _math.nan
    slope = (newer_price - older_price) / (newer_bar - older_bar)
    value = older_price + slope * (index - older_bar)
    return value if _math.isfinite(value) else _math.nan


def _mc_trendline_prices(
    high: _npt.NDArray[_np.float64],
    low: _npt.NDArray[_np.float64],
    *,
    left: int,
    right: int,
    max_age: int,
) -> tuple[_npt.NDArray[_np.float64], _npt.NDArray[_np.float64]]:
    resistance = _np.full(high.size, _np.nan, dtype=_np.float64)
    support = _np.full(low.size, _np.nan, dtype=_np.float64)
    high_points: list[tuple[int, float, int]] = []
    low_points: list[tuple[int, float, int]] = []
    for index in range(high.size):
        high_points[:] = [point for point in high_points if index - point[2] <= max_age]
        low_points[:] = [point for point in low_points if index - point[2] <= max_age]
        center = index - right
        if _mc_is_pivot(high, center, left=left, right=right, high=True):
            high_points.append((center, float(high[center]), index))
            del high_points[:-2]
        if _mc_is_pivot(low, center, left=left, right=right, high=False):
            low_points.append((center, float(low[center]), index))
            del low_points[:-2]
        resistance[index] = _mc_line_price(high_points, index)
        support[index] = _mc_line_price(low_points, index)
    return support, resistance


def _mc_strategy_values(
    bars: _AggregatedStrategyBars,
    supports: tuple[_npt.NDArray[_np.float64], ...],
    resistances: tuple[_npt.NDArray[_np.float64], ...],
    params: _MultiConfluenceParams,
) -> _MultiConfluenceValues:
    size = bars.close.size
    event = _np.zeros(size, dtype=_np.float64)
    if size == 0:
        empty = _np.empty(0, dtype=_np.float64)
        return _MultiConfluenceValues(event, empty, empty.copy())

    atr = _finite_recursive_average(
        _uls_true_range(bars.high, bars.low, bars.close),
        alpha=1.0 / params.atr_length,
        window=params.rma_window,
    )
    rsi = _mc_rsi(
        bars.close,
        length=params.rsi_length,
        window=params.rma_window,
    )
    ema_fast = _finite_recursive_average(
        bars.close,
        alpha=2.0 / (params.ema_fast_length + 1.0),
        window=params.ema_window,
    )
    ema_slow = _finite_recursive_average(
        bars.close,
        alpha=2.0 / (params.ema_slow_length + 1.0),
        window=params.ema_window,
    )

    near_support = _np.zeros(size, dtype=_np.bool_)
    near_resistance = _np.zeros(size, dtype=_np.bool_)
    for level in supports:
        near_support |= (
            _np.isfinite(level)
            & _np.isfinite(atr)
            & (_np.abs(bars.close - level) <= atr * params.level_atr_range)
        )
    for level in resistances:
        near_resistance |= (
            _np.isfinite(level)
            & _np.isfinite(atr)
            & (_np.abs(bars.close - level) <= atr * params.level_atr_range)
        )

    support_trend, resistance_trend = _mc_trendline_prices(
        bars.high,
        bars.low,
        left=params.pivot_left,
        right=params.pivot_right,
        max_age=params.max_trend_age_bars,
    )
    near_support_trend = (
        _np.isfinite(support_trend)
        & _np.isfinite(atr)
        & (_np.abs(bars.close - support_trend) <= atr * params.trend_atr_range)
    )
    near_resistance_trend = (
        _np.isfinite(resistance_trend)
        & _np.isfinite(atr)
        & (_np.abs(bars.close - resistance_trend) <= atr * params.trend_atr_range)
    )
    trend_long = near_support_trend | (
        _np.isfinite(resistance_trend) & (bars.close > resistance_trend)
    )
    trend_short = near_resistance_trend | (
        _np.isfinite(support_trend) & (bars.close < support_trend)
    )

    previous_low = _mc_previous_extreme(
        bars.low,
        params.liquidity_lookback,
        highest=False,
    )
    previous_high = _mc_previous_extreme(
        bars.high,
        params.liquidity_lookback,
        highest=True,
    )
    bull_sweep = (
        _np.isfinite(previous_low) & (bars.low < previous_low) & (bars.close > previous_low)
    )
    bear_sweep = (
        _np.isfinite(previous_high) & (bars.high > previous_high) & (bars.close < previous_high)
    )

    prior_rsi = _np.full(size, _np.nan, dtype=_np.float64)
    prior_rsi[1:] = rsi[:-1]
    rsi_long = (rsi <= params.rsi_long_level) | ((rsi > 50.0) & (rsi > prior_rsi))
    rsi_short = (rsi >= params.rsi_short_level) | ((rsi < 50.0) & (rsi < prior_rsi))
    ema_bullish = (bars.close > ema_fast) & (ema_fast > ema_slow)
    ema_bearish = (bars.close < ema_fast) & (ema_fast < ema_slow)

    long_score = (
        near_support.astype(_np.int8)
        + rsi_long.astype(_np.int8)
        + ema_bullish.astype(_np.int8)
        + trend_long.astype(_np.int8)
        + bull_sweep.astype(_np.int8)
    )
    short_score = (
        near_resistance.astype(_np.int8)
        + rsi_short.astype(_np.int8)
        + ema_bearish.astype(_np.int8)
        + trend_short.astype(_np.int8)
        + bear_sweep.astype(_np.int8)
    )
    long_state = (long_score >= params.minimum_score) & (long_score > short_score)
    short_state = (short_score >= params.minimum_score) & (short_score > long_score)
    previous_long = _np.zeros(size, dtype=_np.bool_)
    previous_short = _np.zeros(size, dtype=_np.bool_)
    previous_long[1:] = long_state[:-1]
    previous_short[1:] = short_state[:-1]
    event[long_state & ~previous_long] = 1.0
    event[short_state & ~previous_short] = -1.0
    return _MultiConfluenceValues(
        event,
        long_score.astype(_np.float64),
        short_score.astype(_np.float64),
    )


class MultiConfluenceM15V1:
    """Finite, closed-bar M15 port of the approved five-factor confluence rule."""

    __slots__ = ("_params", "_window")

    def __init__(
        self,
        *,
        pivot_left: int = 5,
        pivot_right: int = 5,
        rsi_length: int = 14,
        rsi_long_level: int = 45,
        rsi_short_level: int = 55,
        ema_fast_length: int = 50,
        ema_slow_length: int = 200,
        atr_length: int = 14,
        level_atr_range: float = 0.60,
        trend_atr_range: float = 0.50,
        liquidity_lookback: int = 20,
        minimum_score: int = 4,
        base_grid_seconds: int = 900,
        base_phase_offset_seconds: int = 0,
        level_grid_seconds: tuple[int, ...] = (14_400, 21_600, 28_800, 43_200, 604_800),
        level_phase_offset_seconds: tuple[int, ...] = (0, 0, 0, 0, 345_600),
        rma_window: int = 300,
        ema_window: int = 2_000,
        max_level_age_bars: int = 52,
        max_trend_age_bars: int = 512,
    ) -> None:
        base_grid, base_phase, level_grids, level_phases = _mc_validate_grids(
            base_grid_seconds=base_grid_seconds,
            base_phase_offset_seconds=base_phase_offset_seconds,
            level_grid_seconds=level_grid_seconds,
            level_phase_offset_seconds=level_phase_offset_seconds,
        )
        pivot_left_value = _mc_int("pivot_left", pivot_left, minimum=2)
        pivot_right_value = _mc_int("pivot_right", pivot_right, minimum=2)
        rsi_length_value = _mc_int("rsi_length", rsi_length, minimum=1)
        rsi_long_value = _mc_int("rsi_long_level", rsi_long_level, minimum=0, maximum=100)
        rsi_short_value = _mc_int("rsi_short_level", rsi_short_level, minimum=0, maximum=100)
        if rsi_long_value >= rsi_short_value:
            raise _FeatureContractError("rsi_long_level must be below rsi_short_level")
        ema_fast_value = _mc_int("ema_fast_length", ema_fast_length, minimum=1)
        ema_slow_value = _mc_int("ema_slow_length", ema_slow_length, minimum=2)
        if ema_fast_value >= ema_slow_value:
            raise _FeatureContractError("ema_fast_length must be below ema_slow_length")
        atr_length_value = _mc_int("atr_length", atr_length, minimum=1)
        liquidity_value = _mc_int("liquidity_lookback", liquidity_lookback, minimum=1)
        score_value = _mc_int("minimum_score", minimum_score, minimum=1, maximum=5)
        rma_window_value = _mc_int("rma_window", rma_window, minimum=1)
        ema_window_value = _mc_int("ema_window", ema_window, minimum=1)
        if rma_window_value < max(rsi_length_value, atr_length_value):
            raise _FeatureContractError("rma_window must cover RSI and ATR lengths")
        if ema_window_value < ema_slow_value:
            raise _FeatureContractError("ema_window must cover ema_slow_length")
        max_level_age_value = _mc_int("max_level_age_bars", max_level_age_bars, minimum=0)
        max_trend_age_value = _mc_int("max_trend_age_bars", max_trend_age_bars, minimum=0)
        params = _MultiConfluenceParams(
            pivot_left=pivot_left_value,
            pivot_right=pivot_right_value,
            rsi_length=rsi_length_value,
            rsi_long_level=rsi_long_value,
            rsi_short_level=rsi_short_value,
            ema_fast_length=ema_fast_value,
            ema_slow_length=ema_slow_value,
            atr_length=atr_length_value,
            level_atr_range=_mc_float("level_atr_range", level_atr_range),
            trend_atr_range=_mc_float("trend_atr_range", trend_atr_range),
            liquidity_lookback=liquidity_value,
            minimum_score=score_value,
            base_grid_seconds=base_grid,
            base_phase_offset_seconds=base_phase,
            level_grid_seconds=level_grids,
            level_phase_offset_seconds=level_phases,
            rma_window=rma_window_value,
            ema_window=ema_window_value,
            max_level_age_bars=max_level_age_value,
            max_trend_age_bars=max_trend_age_value,
        )
        source_rows_per_base = base_grid // 60
        longest_level_rows = max(grid // 60 for grid in level_grids) * (
            max_level_age_value + pivot_left_value + pivot_right_value + 2
        )
        longest_base_chain = max(
            rma_window_value + 2,
            ema_window_value + 2,
            liquidity_value + 2,
            max_trend_age_value + pivot_left_value + pivot_right_value + 2,
        )
        longest_base_rows = source_rows_per_base * longest_base_chain
        self._params = params
        self._window = _WindowSpec(
            max(longest_level_rows, longest_base_rows),
            _GapPolicy.CROSS,
        )

    @property
    def provider_id(self) -> str:
        """Return the stable provider identity."""
        return "multi_confluence_m15_v1"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return signed events and their two five-factor scores."""
        return _MC_FEATURE_NAMES

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact same-symbol OHLC inputs consumed by the rule."""
        return ("high", "low", "close")

    @property
    def window(self) -> _WindowSpec:
        """Return the finite M1 reach dominated by the weekly pivot level."""
        return self._window

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return every identity-bearing confluence and alignment parameter."""
        return self._params._asdict()

    @property
    def cost_hint(self) -> _CostHint:
        """Declare multi-grid pivot and recursive-indicator computation expensive."""
        return _CostHint.EXPENSIVE

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute closed M15 events and project them to their first usable M1 rows."""
        if request.unit is not _TimeUnit.SECONDS:
            raise _FeatureContractError(
                "multi_confluence_m15_v1 requires native keys expressed in seconds"
            )
        keys = request.keys
        deltas = keys.view(_np.uint64)[1:] - keys.view(_np.uint64)[:-1]
        if _np.any(deltas < _np.uint64(60)):
            raise _FeatureContractError(
                "multi_confluence_m15_v1 requires M1-density input with adjacent "
                "observed keys at least 60 seconds apart"
            )
        high = _np.asarray(request.column("high"), dtype=_np.float64)
        low = _np.asarray(request.column("low"), dtype=_np.float64)
        close = _np.asarray(request.column("close"), dtype=_np.float64)
        dummy = _np.zeros(close.size, dtype=_np.float64)
        bars = _aggregate_complete_strategy_bars(
            keys,
            close,
            high,
            low,
            close,
            dummy,
            bar_grid_seconds=self._params.base_grid_seconds,
            bar_phase_offset_seconds=self._params.base_phase_offset_seconds,
        )
        supports, resistances = _mc_multitimeframe_levels(
            keys,
            high,
            low,
            close,
            bars.usable_positions,
            self._params,
        )
        values = _mc_strategy_values(bars, supports, resistances, self._params)

        result = {
            "mc_event_signed": _np.zeros(request.eval_length, dtype=_np.float64),
            "mc_long_score": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "mc_short_score": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
        }
        positions = _np.arange(
            request.eval_offset,
            request.eval_offset + request.eval_length,
            dtype=_np.int64,
        )
        latest = _np.searchsorted(bars.usable_positions, positions, side="right") - 1
        available = latest >= 0
        result["mc_long_score"][available] = values.long_score[latest[available]]
        result["mc_short_score"][available] = values.short_score[latest[available]]

        event_bars = _np.flatnonzero(values.event_signed != 0.0)
        if event_bars.size:
            source_positions = bars.usable_positions[event_bars]
            in_evaluation = (source_positions >= request.eval_offset) & (
                source_positions < request.eval_offset + request.eval_length
            )
            output_positions = (source_positions[in_evaluation] - request.eval_offset).astype(
                _np.int64, copy=False
            )
            result["mc_event_signed"][output_positions] = values.event_signed[
                event_bars[in_evaluation]
            ]
        return result


_GSL_FEATURE_NAMES = (
    "gsl_event_signed",
    "gsl_long_score",
    "gsl_short_score",
    "gsl_conflict",
)


class _GapSensitiveParams(_typing.NamedTuple):
    min_gap_pct: float
    swing_length: int
    rsi_length: int
    rsi_overbought: int
    rsi_oversold: int
    ema_fast_length: int
    ema_slow_length: int
    min_confirm: int
    bar_grid_seconds: int
    bar_phase_offset_seconds: int
    rma_window: int
    ema_window: int
    max_gap_age_bars: int
    max_level_age_bars: int


class _GapSensitiveValues(_typing.NamedTuple):
    event_signed: _npt.NDArray[_np.float64]
    long_score: _npt.NDArray[_np.float64]
    short_score: _npt.NDArray[_np.float64]
    conflict: _npt.NDArray[_np.float64]


def _gsl_strategy_values(
    bars: _AggregatedStrategyBars,
    params: _GapSensitiveParams,
) -> _GapSensitiveValues:
    """Return the finite-state port of the supplied Gap Sensitive Pine rule."""
    size = bars.close.size
    event = _np.zeros(size, dtype=_np.float64)
    long_score = _np.zeros(size, dtype=_np.float64)
    short_score = _np.zeros(size, dtype=_np.float64)
    conflict = _np.zeros(size, dtype=_np.float64)
    if size == 0:
        return _GapSensitiveValues(event, long_score, short_score, conflict)

    rsi = _mc_rsi(
        bars.close,
        length=params.rsi_length,
        window=params.rma_window,
    )
    ema_fast = _finite_recursive_average(
        bars.close,
        alpha=2.0 / (params.ema_fast_length + 1.0),
        window=params.ema_window,
    )
    ema_slow = _finite_recursive_average(
        bars.close,
        alpha=2.0 / (params.ema_slow_length + 1.0),
        window=params.ema_window,
    )

    gap_below_top = _math.nan
    gap_below_bottom = _math.nan
    gap_below_created = -1
    gap_above_top = _math.nan
    gap_above_bottom = _math.nan
    gap_above_created = -1
    support = _math.nan
    support_confirmed = -1
    resistance = _math.nan
    resistance_confirmed = -1
    prior_long_signal = False
    prior_short_signal = False

    for index in range(size):
        if gap_below_created >= 0 and index - gap_below_created > params.max_gap_age_bars:
            gap_below_top = _math.nan
            gap_below_bottom = _math.nan
            gap_below_created = -1
        if gap_above_created >= 0 and index - gap_above_created > params.max_gap_age_bars:
            gap_above_top = _math.nan
            gap_above_bottom = _math.nan
            gap_above_created = -1
        if support_confirmed >= 0 and index - support_confirmed > params.max_level_age_bars:
            support = _math.nan
            support_confirmed = -1
        if resistance_confirmed >= 0 and index - resistance_confirmed > params.max_level_age_bars:
            resistance = _math.nan
            resistance_confirmed = -1

        if index > 0:
            previous_high = float(bars.high[index - 1])
            bull_gap_size = float(bars.low[index]) - previous_high
            bull_gap_pct = (
                bull_gap_size / previous_high * 100.0 if previous_high != 0.0 else 0.0
            )
            if (
                float(bars.low[index]) > previous_high
                and bull_gap_pct >= params.min_gap_pct
            ):
                gap_below_top = float(bars.low[index])
                gap_below_bottom = previous_high
                gap_below_created = index

            previous_low = float(bars.low[index - 1])
            bear_gap_size = previous_low - float(bars.high[index])
            bear_gap_pct = (
                bear_gap_size / previous_low * 100.0 if previous_low != 0.0 else 0.0
            )
            if (
                float(bars.high[index]) < previous_low
                and bear_gap_pct >= params.min_gap_pct
            ):
                gap_above_top = previous_low
                gap_above_bottom = float(bars.high[index])
                gap_above_created = index

        if _math.isfinite(gap_below_bottom) and float(bars.low[index]) <= gap_below_bottom:
            gap_below_top = _math.nan
            gap_below_bottom = _math.nan
            gap_below_created = -1
        if _math.isfinite(gap_above_top) and float(bars.high[index]) >= gap_above_top:
            gap_above_top = _math.nan
            gap_above_bottom = _math.nan
            gap_above_created = -1

        center = index - params.swing_length
        if _mc_is_pivot(
            bars.high,
            center,
            left=params.swing_length,
            right=params.swing_length,
            high=True,
        ):
            resistance = float(bars.high[center])
            resistance_confirmed = index
        if _mc_is_pivot(
            bars.low,
            center,
            left=params.swing_length,
            right=params.swing_length,
            high=False,
        ):
            support = float(bars.low[center])
            support_confirmed = index

        has_support = _math.isfinite(support)
        has_resistance = _math.isfinite(resistance)
        long_rsi = _math.isfinite(float(rsi[index])) and float(rsi[index]) <= params.rsi_oversold
        short_rsi = (
            _math.isfinite(float(rsi[index])) and float(rsi[index]) >= params.rsi_overbought
        )
        long_ema = (
            _math.isfinite(float(ema_fast[index]))
            and (
                float(bars.close[index]) > float(ema_fast[index])
                or (
                    _math.isfinite(float(ema_slow[index]))
                    and float(ema_fast[index]) > float(ema_slow[index])
                )
            )
        )
        short_ema = (
            _math.isfinite(float(ema_fast[index]))
            and (
                float(bars.close[index]) < float(ema_fast[index])
                or (
                    _math.isfinite(float(ema_slow[index]))
                    and float(ema_fast[index]) < float(ema_slow[index])
                )
            )
        )
        current_long_score = int(long_rsi) + int(long_ema) + int(has_support)
        current_short_score = int(short_rsi) + int(short_ema) + int(has_resistance)
        long_score[index] = float(current_long_score)
        short_score[index] = float(current_short_score)

        gap_below = _math.isfinite(gap_below_top) and float(bars.close[index]) > gap_below_top
        gap_above = (
            _math.isfinite(gap_above_bottom)
            and float(bars.close[index]) < gap_above_bottom
        )
        long_signal = (
            gap_below
            and has_support
            and float(bars.low[index]) <= support
            and float(bars.high[index]) >= support
            and current_long_score >= params.min_confirm
        )
        short_signal = (
            gap_above
            and has_resistance
            and float(bars.high[index]) >= resistance
            and float(bars.low[index]) <= resistance
            and current_short_score >= params.min_confirm
        )
        new_long = long_signal and not prior_long_signal
        new_short = short_signal and not prior_short_signal
        if new_long and new_short:
            conflict[index] = 1.0
        elif new_long:
            event[index] = 1.0
        elif new_short:
            event[index] = -1.0
        prior_long_signal = long_signal
        prior_short_signal = short_signal

    return _GapSensitiveValues(event, long_score, short_score, conflict)


class GapSensitiveLevelsH1V1:
    """Finite closed-H1 port of the supplied Gap Sensitive Levels indicator."""

    __slots__ = ("_params", "_window")

    def __init__(
        self,
        *,
        min_gap_pct: float = 0.10,
        swing_length: int = 5,
        rsi_length: int = 14,
        rsi_overbought: int = 70,
        rsi_oversold: int = 30,
        ema_fast_length: int = 20,
        ema_slow_length: int = 50,
        min_confirm: int = 2,
        bar_grid_seconds: int = 3_600,
        bar_phase_offset_seconds: int = 0,
        rma_window: int = 300,
        ema_window: int = 2_000,
        max_gap_age_bars: int = 2_000,
        max_level_age_bars: int = 2_000,
    ) -> None:
        if type(min_gap_pct) is not float or not _math.isfinite(min_gap_pct) or min_gap_pct < 0.0:
            raise _FeatureContractError("min_gap_pct must be a finite non-negative Python float")
        swing = _mc_int("swing_length", swing_length, minimum=2)
        rsi_len = _mc_int("rsi_length", rsi_length, minimum=1)
        overbought = _mc_int("rsi_overbought", rsi_overbought, minimum=0, maximum=100)
        oversold = _mc_int("rsi_oversold", rsi_oversold, minimum=0, maximum=100)
        if oversold >= overbought:
            raise _FeatureContractError("rsi_oversold must be below rsi_overbought")
        ema_fast = _mc_int("ema_fast_length", ema_fast_length, minimum=1)
        ema_slow = _mc_int("ema_slow_length", ema_slow_length, minimum=1)
        minimum = _mc_int("min_confirm", min_confirm, minimum=1, maximum=3)
        if type(bar_grid_seconds) is not int or bar_grid_seconds != 3_600:
            raise _FeatureContractError("gap_sensitive_levels_h1_v1 grid is fixed at 3600")
        if type(bar_phase_offset_seconds) is not int or bar_phase_offset_seconds != 0:
            raise _FeatureContractError("gap_sensitive_levels_h1_v1 phase is fixed at zero")
        rma = _mc_int("rma_window", rma_window, minimum=rsi_len)
        ema = _mc_int("ema_window", ema_window, minimum=max(ema_fast, ema_slow))
        gap_age = _mc_int("max_gap_age_bars", max_gap_age_bars, minimum=0)
        level_age = _mc_int("max_level_age_bars", max_level_age_bars, minimum=0)
        self._params = _GapSensitiveParams(
            min_gap_pct=min_gap_pct,
            swing_length=swing,
            rsi_length=rsi_len,
            rsi_overbought=overbought,
            rsi_oversold=oversold,
            ema_fast_length=ema_fast,
            ema_slow_length=ema_slow,
            min_confirm=minimum,
            bar_grid_seconds=bar_grid_seconds,
            bar_phase_offset_seconds=bar_phase_offset_seconds,
            rma_window=rma,
            ema_window=ema,
            max_gap_age_bars=gap_age,
            max_level_age_bars=level_age,
        )
        source_rows_per_bar = bar_grid_seconds // 60
        bar_reach = max(
            ema + 2,
            rma + 3,
            gap_age + 3,
            level_age + 2 * swing + 3,
        )
        self._window = _WindowSpec(source_rows_per_bar * bar_reach, _GapPolicy.CROSS)

    @property
    def provider_id(self) -> str:
        """Return the stable provider identity."""
        return "gap_sensitive_levels_h1_v1"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return signed events, scores, and simultaneous-event diagnostics."""
        return _GSL_FEATURE_NAMES

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact same-symbol OHLC inputs consumed by the rule."""
        return ("high", "low", "close")

    @property
    def window(self) -> _WindowSpec:
        """Return the conservative finite M1 reach of the closed-H1 state."""
        return self._window

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return every identity-bearing Pine input and finite-state bound."""
        return self._params._asdict()

    @property
    def cost_hint(self) -> _CostHint:
        """Declare aggregation and finite recursive indicators expensive."""
        return _CostHint.EXPENSIVE

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute completed-H1 events at the first observed later-bucket row."""
        if request.unit is not _TimeUnit.SECONDS:
            raise _FeatureContractError(
                "gap_sensitive_levels_h1_v1 requires native keys expressed in seconds"
            )
        keys = request.keys
        deltas = keys.view(_np.uint64)[1:] - keys.view(_np.uint64)[:-1]
        if _np.any(deltas < _np.uint64(60)):
            raise _FeatureContractError(
                "gap_sensitive_levels_h1_v1 requires M1-density input with adjacent "
                "observed keys at least 60 seconds apart"
            )
        high = _np.asarray(request.column("high"), dtype=_np.float64)
        low = _np.asarray(request.column("low"), dtype=_np.float64)
        close = _np.asarray(request.column("close"), dtype=_np.float64)
        dummy = _np.zeros(close.size, dtype=_np.float64)
        aggregated = _aggregate_complete_strategy_bars(
            keys,
            close,
            high,
            low,
            close,
            dummy,
            bar_grid_seconds=self._params.bar_grid_seconds,
            bar_phase_offset_seconds=self._params.bar_phase_offset_seconds,
        )
        bars = _AggregatedStrategyBars(
            bucket_numbers=aggregated.bucket_numbers[1:],
            usable_positions=aggregated.usable_positions[1:],
            opened=aggregated.opened[1:],
            high=aggregated.high[1:],
            low=aggregated.low[1:],
            close=aggregated.close[1:],
            tick_volume=aggregated.tick_volume[1:],
        )
        values = _gsl_strategy_values(bars, self._params)

        result = {
            "gsl_event_signed": _np.zeros(request.eval_length, dtype=_np.float64),
            "gsl_long_score": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "gsl_short_score": _np.full(request.eval_length, _np.nan, dtype=_np.float64),
            "gsl_conflict": _np.zeros(request.eval_length, dtype=_np.float64),
        }
        positions = _np.arange(
            request.eval_offset,
            request.eval_offset + request.eval_length,
            dtype=_np.int64,
        )
        latest = _np.searchsorted(bars.usable_positions, positions, side="right") - 1
        available = latest >= 0
        result["gsl_long_score"][available] = values.long_score[latest[available]]
        result["gsl_short_score"][available] = values.short_score[latest[available]]

        event_bars = _np.flatnonzero(
            (values.event_signed != 0.0) | (values.conflict != 0.0)
        )
        if event_bars.size:
            source_positions = bars.usable_positions[event_bars]
            in_evaluation = (source_positions >= request.eval_offset) & (
                source_positions < request.eval_offset + request.eval_length
            )
            output_positions = (
                source_positions[in_evaluation] - request.eval_offset
            ).astype(_np.int64, copy=False)
            selected = event_bars[in_evaluation]
            result["gsl_event_signed"][output_positions] = values.event_signed[selected]
            result["gsl_conflict"][output_positions] = values.conflict[selected]
        return result


_FEATURE_PROVIDERS.add("log_return_1", LogReturn1)
_FEATURE_PROVIDERS.add("rolling_mean_close", RollingMeanClose)
_FEATURE_PROVIDERS.add("bar_range", BarRange)
_FEATURE_PROVIDERS.add("gap_sensitive_levels_h1_v1", GapSensitiveLevelsH1V1)
_FEATURE_PROVIDERS.add("multi_confluence_m15_v1", MultiConfluenceM15V1)
_FEATURE_PROVIDERS.add("uls_push_v7", UlsPushV7)
_FEATURE_PROVIDERS.add("uls_market_context_v1", UlsMarketContextV1)
_FEATURE_PROVIDERS.add("zone_trader_v13", ZoneTraderV13)
_FEATURE_PROVIDERS.add("zone_context_v1", ZoneContextV1)
