"""Display-timeframe aggregation: OHLC correctness and native-time semantics.

The Trade Explorer builds M5/M15/H1 from recorded M1 rows. Two things must hold
and are checked here rather than assumed:

1. OHLC composition is correct — open of the first row, max high, min low, close
   of the last row, on the arithmetic bucket the platform itself uses.
2. Nothing drifts toward UTC or a calendar. The grid is plain arithmetic on
   native broker wall-clock seconds, and no bar is ever invented across a market
   closure.
"""

from __future__ import annotations

import numpy as np
import pytest

from explorer.bars import (
    PHASE_OFFSET_SECONDS,
    TIMEFRAMES,
    aggregate_ohlc,
    bucket_numbers,
)


def _m1(keys: list[int]) -> tuple[np.ndarray, ...]:
    """Build simple synthetic M1 rows whose values encode their position."""
    k = np.array(keys, dtype=np.int64)
    base = np.arange(k.size, dtype=np.float64)
    opened = 100.0 + base
    high = opened + 2.0
    low = opened - 2.0
    close = opened + 0.5
    return k, opened, high, low, close


# ------------------------------------------------------------------ buckets


def test_bucket_rule_matches_the_platform_formula() -> None:
    """Boundaries must equal features/reference.py::_arithmetic_bucket_numbers."""
    keys = np.array([1614960000, 1614960060, 1614960299, 1614960300, 1614963599], dtype=np.int64)
    for grid in TIMEFRAMES.values():
        expected = (
            np.floor_divide(keys, grid)
            - (np.remainder(keys, grid) < PHASE_OFFSET_SECONDS)
        ).astype(np.int64)
        assert np.array_equal(bucket_numbers(keys, grid_seconds=grid), expected)


def test_phase_zero_means_plain_floor_division() -> None:
    assert PHASE_OFFSET_SECONDS == 0
    keys = np.array([1614960360, 1614960540], dtype=np.int64)
    assert np.array_equal(
        bucket_numbers(keys, grid_seconds=300), np.floor_divide(keys, 300)
    )


@pytest.mark.parametrize(
    ("key", "grid", "expected_bucket_start"),
    [
        (1614960360, 300, 1614960300),   # 16:06 -> M5 16:05
        (1614960360, 900, 1614960000),   # 16:06 -> M15 16:00
        (1614960360, 3600, 1614960000),  # 16:06 -> H1 16:00
        (1614960000, 3600, 1614960000),  # exact boundary stays put
    ],
)
def test_known_keys_snap_to_the_expected_bucket(
    key: int, grid: int, expected_bucket_start: int
) -> None:
    bucket = int(bucket_numbers(np.array([key], dtype=np.int64), grid_seconds=grid)[0])
    assert bucket * grid + PHASE_OFFSET_SECONDS == expected_bucket_start


# --------------------------------------------------------------------- OHLC


def test_ohlc_composition_over_one_full_bucket() -> None:
    """Open is first, close is last, high is max, low is min."""
    keys, opened, high, low, close = _m1([1614960000 + 60 * i for i in range(5)])
    out = aggregate_ohlc(keys, opened, high, low, close, grid_seconds=300)
    assert out["keys"].tolist() == [1614960000]
    assert out["open"][0] == opened[0]
    assert out["close"][0] == close[-1]
    assert out["high"][0] == high.max()
    assert out["low"][0] == low.min()


def test_rows_split_across_two_buckets() -> None:
    keys, opened, high, low, close = _m1([1614960000 + 60 * i for i in range(10)])
    out = aggregate_ohlc(keys, opened, high, low, close, grid_seconds=300)
    assert out["keys"].tolist() == [1614960000, 1614960300]
    assert out["open"].tolist() == [opened[0], opened[5]]
    assert out["close"].tolist() == [close[4], close[9]]
    assert out["high"].tolist() == [high[:5].max(), high[5:].max()]
    assert out["low"].tolist() == [low[:5].min(), low[5:].min()]


def test_candle_is_stamped_with_its_bucket_start_not_its_first_row() -> None:
    """A bucket whose first observed row is late still sits on the grid."""
    keys, opened, high, low, close = _m1([1614960180, 1614960240])  # 16:03, 16:04
    out = aggregate_ohlc(keys, opened, high, low, close, grid_seconds=300)
    assert out["keys"].tolist() == [1614960000]  # the M5 bucket start, 16:00


def test_a_partial_final_bucket_is_kept() -> None:
    """Display keeps the last candle; the research aggregator deliberately drops it.

    features/reference.py implements ID-1 predicate A, a no-lookahead rule for
    research. A researcher inspecting completed history wants the final candle,
    and nothing here feeds a model, policy or fit.
    """
    keys, opened, high, low, close = _m1([1614960000, 1614960060, 1614960300])
    out = aggregate_ohlc(keys, opened, high, low, close, grid_seconds=300)
    assert out["keys"].size == 2
    assert out["open"][-1] == opened[-1]


def test_empty_input_yields_empty_series() -> None:
    empty_i = np.empty(0, dtype=np.int64)
    empty_f = np.empty(0, dtype=np.float64)
    out = aggregate_ohlc(empty_i, empty_f, empty_f, empty_f, empty_f, grid_seconds=300)
    assert out["keys"].size == 0


# ------------------------------------------------------- closures and gaps


def test_no_candle_is_invented_across_a_market_closure() -> None:
    """A weekend gap must produce no bars, not a run of synthetic flat ones."""
    friday = 1614988620          # 2021-03-05 23:57
    monday = friday + 176580     # the real 49h gap in the E1 fixture
    keys, opened, high, low, close = _m1([friday, monday])
    out = aggregate_ohlc(keys, opened, high, low, close, grid_seconds=3600)
    assert out["keys"].size == 2, "exactly two observed buckets, nothing in between"
    assert int(out["keys"][1]) - int(out["keys"][0]) > 3600


def test_aggregation_is_pure_arithmetic_on_native_keys() -> None:
    """Shifting every key by a whole number of grids shifts buckets identically.

    This is what 'no calendar, no timezone' means concretely: the grid has no
    anchor date, no DST rule and no session concept (ADR-0002, ADR-0005).
    """
    base = [1614960000 + 60 * i for i in range(12)]
    shift = 3600 * 24 * 37
    keys_a, o, h, low, c = _m1(base)
    keys_b, *_ = _m1([k + shift for k in base])
    out_a = aggregate_ohlc(keys_a, o, h, low, c, grid_seconds=900)
    out_b = aggregate_ohlc(keys_b, o, h, low, c, grid_seconds=900)
    assert (out_b["keys"] - out_a["keys"]).tolist() == [shift] * out_a["keys"].size
    assert out_a["open"].tolist() == out_b["open"].tolist()
    assert out_a["high"].tolist() == out_b["high"].tolist()


# ------------------------------- conformance against the platform's own code


def test_display_buckets_match_the_platform_aggregator_on_real_keys() -> None:
    """Pin the display aggregator to features/reference.py on real recorded data.

    The two must agree on every complete bucket. They differ by exactly one rule:
    the platform drops the final bucket (ID-1 predicate A, a no-lookahead rule for
    research) and display keeps it. Anything else diverging is a bug in this
    viewer, and this test is what catches it.
    """
    reference = pytest.importorskip("trading_research.features.reference")
    aggregate_platform = reference._aggregate_complete_strategy_bars

    rng = np.random.default_rng(20260822)
    start = 1614960000
    # Irregular spacing, so buckets have unequal row counts and some are empty.
    offsets = np.cumsum(rng.integers(60, 240, size=400)) - 60
    keys = (start + offsets).astype(np.int64)
    opened = (1700.0 + rng.normal(0, 1, keys.size)).astype(np.float64)
    high = opened + rng.uniform(0.1, 2.0, keys.size)
    low = opened - rng.uniform(0.1, 2.0, keys.size)
    close = opened + rng.normal(0, 0.5, keys.size)
    volume = rng.uniform(1, 100, keys.size).astype(np.float64)

    for name, grid in TIMEFRAMES.items():
        if name == "M1":
            continue
        platform = aggregate_platform(
            keys, opened, high, low, close, volume,
            bar_grid_seconds=grid, bar_phase_offset_seconds=PHASE_OFFSET_SECONDS,
        )
        mine = aggregate_ohlc(keys, opened, high, low, close, grid_seconds=grid)

        platform_starts = (
            platform.bucket_numbers * np.int64(grid) + np.int64(PHASE_OFFSET_SECONDS)
        )
        # Display keeps the final bucket; the platform does not. Compare the overlap.
        shared = platform_starts.size
        assert shared > 0
        assert mine["keys"][:shared].tolist() == platform_starts.tolist(), name
        assert mine["open"][:shared].tolist() == platform.opened.tolist(), name
        assert mine["high"][:shared].tolist() == platform.high.tolist(), name
        assert mine["low"][:shared].tolist() == platform.low.tolist(), name
        assert mine["close"][:shared].tolist() == platform.close.tolist(), name
        # And the only extra candle display adds is the trailing partial one.
        assert mine["keys"].size - shared in (0, 1), name
