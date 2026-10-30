"""Execute the prototype's time formatters and assert timezone invariance.

There is no browser or Node runtime on this machine, so the JavaScript is run
through QuickJS instead. That is enough to verify the one property E1 cannot
afford to get wrong: broker wall-clock keys must decode to the same civil time
regardless of the viewer's timezone (ADR-0002).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

quickjs = pytest.importorskip("quickjs")

APP_JS = Path(__file__).resolve().parent.parent / "prototype" / "app.js"

# The pure time helpers live above the first DOM-touching line.
_SPLIT_AT = "const css ="

GOLDEN = [
    (1614848700, "2021-03-04 Thu 09:05"),
    (1614960360, "2021-03-05 Fri 16:06"),
    (1614960540, "2021-03-05 Fri 16:09"),
    (1615208820, "2021-03-08 Mon 13:07"),
]

TIMEZONES = [
    "UTC",
    "America/Los_Angeles",   # UTC-8
    "Asia/Tokyo",            # UTC+9
    "Australia/Sydney",
    "Europe/London",
    "Pacific/Kiritimati",    # UTC+14
]


def _pure_source() -> str:
    source = APP_JS.read_text()
    assert _SPLIT_AT in source, "app.js layout changed; update _SPLIT_AT"
    return source.split(_SPLIT_AT)[0]


def test_app_js_parses() -> None:
    """The whole file must parse. Wrapping it means nothing executes."""
    context = quickjs.Context()
    context.eval("(function(){" + APP_JS.read_text() + "})")


def test_vertical_price_scale_touch_target_is_wide_enough() -> None:
    """Native drag-to-scale on the price axis needs a grabbable strip.

    LWC auto-fits the axis to its label text (as narrow as ~45px) unless
    `minimumWidth` is set. 68px keeps it comfortably above a 44px touch target
    while still leaving most of the screen to the chart itself.
    """
    source = APP_JS.read_text()
    assert "minimumWidth: 68" in source
    assert "rightPriceScale" in source


def test_horizontal_time_axis_touch_target_is_wide_enough() -> None:
    """Native drag-to-scale on the time axis needs a grabbable strip.

    LWC auto-fits the time axis to its label text (well under 30px) unless
    `minimumHeight` is set, and the timeframe buttons sit directly beneath it.
    44px matches the touch-target convention used elsewhere in this UI.
    """
    source = APP_JS.read_text()
    assert "minimumHeight: 44" in source
    assert "timeScale" in source


def test_pinch_remains_enabled_for_horizontal_scaling() -> None:
    """Pinch drives the time scale in LWC, so it is the other horizontal gesture."""
    assert "pinch: true" in APP_JS.read_text()


def test_native_axis_drag_and_reset_remain_enabled() -> None:
    """Native axis scaling must receive both mouse and touch movement."""
    source = APP_JS.read_text()
    assert "axisPressedMouseMove: true" in source
    assert "axisDoubleClickReset: true" in source
    # LWC's price-axis widget treats a vertical touch as page scrolling when
    # this is false, suppressing the touchMove callback that performs scaling.
    assert "vertTouchDrag: true" in source


@pytest.mark.parametrize(("key", "expected"), GOLDEN)
def test_broker_datetime_matches_golden(key: int, expected: str) -> None:
    context = quickjs.Context()
    context.eval(_pure_source())
    assert context.get("brokerDateTime")(key) == expected


def test_axis_formatters() -> None:
    context = quickjs.Context()
    context.eval(_pure_source())
    assert context.get("brokerClock")(1614960360) == "16:06"
    assert context.get("brokerDay")(1614960360) == "5 Mar"


@pytest.mark.parametrize("tz", TIMEZONES)
def test_decode_is_timezone_invariant(tz: str) -> None:
    """Changing the host timezone must not move a displayed broker timestamp.

    Run in a subprocess because TZ is read once per process by the C library.
    """
    script = textwrap.dedent(f"""
        import json, quickjs
        source = open({str(APP_JS)!r}).read().split({_SPLIT_AT!r})[0]
        ctx = quickjs.Context(); ctx.eval(source)
        fn = ctx.get("brokerDateTime")
        print(json.dumps([fn(k) for k, _ in {GOLDEN!r}]))
    """)
    environment = {**os.environ, "TZ": tz}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True, env=environment,
    )
    assert result.stdout.strip().endswith("]")
    decoded = result.stdout.strip().splitlines()[-1]
    assert decoded == str([expected for _, expected in GOLDEN]).replace("'", '"')


# ------------------------------------------------------------- other pages

PROTOTYPE = Path(__file__).resolve().parent.parent / "prototype"


@pytest.mark.parametrize("name", ["plugins.js", "family.js"])
def test_page_script_parses(name: str) -> None:
    """The whole file must parse. Wrapping it means nothing executes."""
    source = (PROTOTYPE / name).read_text()
    context = quickjs.Context()
    context.eval("(function(){" + source + "})")


def test_family_date_formatter_uses_utc_getters_only() -> None:
    """The date column on the Experiments table must not shift with the phone's timezone."""
    source = (PROTOTYPE / "family.js").read_text()
    for getter in ("getUTCFullYear", "getUTCMonth", "getUTCDate"):
        assert getter in source
    for forbidden in ("toLocaleDateString", "getFullYear(", "getMonth(", "getDate("):
        assert forbidden not in source


def test_family_columns_expose_only_recorded_or_summed_metrics() -> None:
    """The table must not introduce a metric name the platform never records."""
    source = (PROTOTYPE / "family.js").read_text()
    for expected in ("trades_sum", "net_pnl_sum", "win_rate_min", "win_rate_max",
                      "max_drawdown_worst", "fold_count"):
        assert expected in source
    # No collapsed run-level win rate or drawdown may be introduced client-side.
    assert "win_rate_sum" not in source
    assert "max_drawdown_sum" not in source


# ------------------------------------------------- custom x-scaling gesture

_X_SCALE_CASES = [
    # (start_span, dx, width, expect)
    (100.0, 0.0, 360.0, "unchanged"),
    (100.0, 180.0, 360.0, "narrower_span"),   # drag right -> fewer bars, wider candles
    (100.0, -180.0, 360.0, "wider_span"),     # drag left  -> more bars, narrower candles
]


def _x_scale(context, start_span, dx, width, min_bar=0.5, max_bar=60.0):
    return context.get("scaledLogicalSpan")(start_span, dx, width, min_bar, max_bar)


@pytest.fixture(scope="module")
def js():
    context = quickjs.Context()
    context.eval(_pure_source())
    return context


def test_drag_right_makes_candles_wider(js) -> None:
    """Fewer bars across the same width means each bar is wider."""
    start = 100.0
    after = _x_scale(js, start, 180.0, 360.0)
    assert after < start
    assert 360.0 / after > 360.0 / start


def test_drag_left_makes_candles_narrower(js) -> None:
    start = 100.0
    after = _x_scale(js, start, -180.0, 360.0)
    assert after > start


def test_zero_travel_is_a_no_op(js) -> None:
    """A tap without movement must not shift the scale at all."""
    assert _x_scale(js, 100.0, 0.0, 360.0) == pytest.approx(100.0)


def test_gesture_is_continuous_and_free_of_jumps(js) -> None:
    """Span must move monotonically with travel, with no discontinuity.

    Continuity is checked as a bounded *ratio* per step, not a bounded absolute
    difference: the response is exponential, so a fixed pixel step legitimately
    moves the span more when zoomed out. A ratio bound is what actually catches
    a jump.
    """
    step = 10
    # exp(step / X_SCALE_TRAVEL) is the expected per-step ratio.
    limit = math.exp(step / 180.0) * 1.02
    previous = None
    for dx in range(-300, 301, step):
        span = _x_scale(js, 100.0, float(dx), 360.0)
        assert span > 0
        if previous is not None:
            assert span <= previous + 1e-9, f"not monotonic at dx={dx}"
            assert previous / span <= limit, f"jump at dx={dx}"
        previous = span


def test_scaling_is_absolute_not_accumulated(js) -> None:
    """Recomputing from the gesture start must be path-independent.

    This is what stops the scale drifting when a finger wobbles back and forth.
    """
    direct = _x_scale(js, 100.0, 120.0, 360.0)
    for wobble in (5.0, -40.0, 300.0, -200.0):
        _x_scale(js, 100.0, wobble, 360.0)
    assert _x_scale(js, 100.0, 120.0, 360.0) == pytest.approx(direct)


def test_candle_width_is_clamped_at_both_ends(js) -> None:
    width = 360.0
    widest = _x_scale(js, 100.0, 100000.0, width)
    narrowest = _x_scale(js, 100.0, -100000.0, width)
    assert width / widest == pytest.approx(60.0)     # MAX_BAR_PX
    assert width / narrowest == pytest.approx(0.5)   # MIN_BAR_PX
    assert widest > 0 and narrowest > widest


def test_span_stays_positive_so_the_range_is_always_valid(js) -> None:
    """setVisibleLogicalRange asserts from <= to, so span must never go <= 0."""
    for dx in (-5000.0, -1.0, 0.0, 1.0, 5000.0):
        assert _x_scale(js, 100.0, dx, 360.0) > 0


def test_degenerate_inputs_are_returned_unchanged(js) -> None:
    assert _x_scale(js, 0.0, 50.0, 360.0) == 0.0
    assert _x_scale(js, 100.0, 50.0, 0.0) == 100.0


def test_strip_is_installed_and_scoped_to_the_axis_only() -> None:
    """Only the bottom strip may take the gesture; pane and price axis stay free."""
    source = APP_JS.read_text()
    assert "installTimeAxisScaling" in source
    assert "setVisibleLogicalRange" in source
    css = (PROTOTYPE / "styles.css").read_text()
    assert "#xscale" in css
    assert "right: 68px" in css      # clear of the price axis
    assert "height: 44px" in css     # only as tall as the time axis
    assert "touch-action: none" in css


def test_x_scale_phone_diagnostic_is_explicitly_opt_in() -> None:
    """The temporary HUD must not alter normal chart interaction or layout."""
    source = APP_JS.read_text()
    css = (PROTOTYPE / "styles.css").read_text()
    assert "get('xdiag') === '1'" in source
    assert "subscribeVisibleLogicalRangeChange" in source
    assert "#xscale-debug" in css
    assert "pointer-events: none" in css


def test_pane_gestures_are_not_disturbed() -> None:
    source = APP_JS.read_text()
    assert "horzTouchDrag: true" in source      # pan
    assert "pinch: true" in source              # pinch zoom
    assert "axisPressedMouseMove: true" in source   # vertical price scaling


# ------------------------------------------------------- zone visualisation


def test_zone_is_a_filled_band_not_bare_lines() -> None:
    """The zone renders as a shaded rectangle via a BaselineSeries fill."""
    source = APP_JS.read_text()
    assert "BaselineSeries" in source
    assert "topFillColor1" in source
    assert "baseValue" in source
    # The old two-bare-lines rendering must be gone.
    assert "zoneTop:" not in source
    assert "zoneBottom:" not in source


def test_zone_band_is_created_behind_the_candles() -> None:
    """Drawing order is what keeps candles readable through the fill."""
    source = APP_JS.read_text()
    band_at = source.index("const zoneBand = chart.addSeries")
    candles_at = source.index("series = chart.addSeries(LightweightCharts.CandlestickSeries")
    assert band_at < candles_at, "zone band must be added before the candlestick series"


def test_zone_fill_is_subtle_and_flat() -> None:
    """Noticeable but not dominant, and a region rather than a magnitude."""
    source = APP_JS.read_text()
    assert "rgba(123,140,165,0.16)" in source          # low-alpha fill
    assert source.count("rgba(123,140,165,0.16)") == 2  # flat, both stops equal
    # The unused lower half of the baseline series must be fully transparent.
    assert "bottomFillColor1: 'rgba(0,0,0,0)'" in source


def test_zone_horizontal_extent_starts_at_the_signal() -> None:
    """The rectangle must never be drawn before the signal.

    Zone creation time was not recorded, so extending the rectangle leftwards
    would be inventing history.
    """
    source = APP_JS.read_text()
    assert "context.signal_key" in source
    assert "trade.exit_key" in source
    # Both bounds come from the recorded columns, not from anything derived.
    assert "zone.top" in source and "zone.bottom" in source


def test_signal_marker_survives_and_carries_the_zone_type() -> None:
    source = APP_JS.read_text()
    assert "signal'" in source or "signal`" in source
    assert "kind.toUpperCase()" in source


def test_executed_overlays_remain_dominant() -> None:
    """Executed levels stay full-width price lines; context stays segments."""
    source = APP_JS.read_text()
    assert "createPriceLine" in source          # executed entry / TP / SL
    assert "title: 'TP'" in source
    assert "title: 'SL'" in source


# --------------------------------------------------------------------------
# Bracket presentation
# --------------------------------------------------------------------------

_FMT_PRICE = "const fmtPrice ="
_BRACKET_LABEL = "function bracketLabel("


def _bracket_source() -> str:
    """Extract the two pure presentation helpers, which sit below the DOM line.

    They cannot be reached by ``_pure_source`` and are not worth moving just to
    be testable, so they are lifted by name instead. If either is renamed or
    removed this fails loudly rather than silently covering nothing.
    """
    source = APP_JS.read_text()
    assert _FMT_PRICE in source, "app.js no longer defines fmtPrice"
    assert _BRACKET_LABEL in source, "app.js no longer defines bracketLabel"
    fmt_line = next(
        line for line in source.splitlines() if line.startswith(_FMT_PRICE)
    )
    start = source.index(_BRACKET_LABEL)
    end = source.index("\n}", start) + 2
    return fmt_line + "\n" + source[start:end]


def _label(level: object, distance: object) -> str:
    context = quickjs.Context()
    context.eval(_bracket_source())
    call = context.eval("bracketLabel")
    return str(call(level, distance))


def test_a_recorded_bracket_shows_its_level_and_distance() -> None:
    assert _label(1585.0, 8.0) == "1585.00 (8.00)"


def test_an_absent_bracket_shows_a_dash_not_a_zero() -> None:
    """A zero would read as a bracket sitting exactly at the entry price."""
    assert _label(None, None) == "—"
    assert _label(None, 8.0) == "—"


def test_a_level_without_a_distance_still_renders() -> None:
    assert _label(1585.0, None) == "1585.00"


def test_short_side_levels_render_the_same_way() -> None:
    """Direction lives in the sign of the level, not in this formatter."""
    assert _label(1569.0, 8.0) == "1569.00 (8.00)"
