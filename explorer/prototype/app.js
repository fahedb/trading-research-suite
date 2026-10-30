/* Trade Explorer — navigation shell over the real Research Machine record.
 *
 * Read-only. Runs come from the disposable Phase-5 index (a listing accelerator
 * only); run detail, trades and TP/SL levels always come from files. Nothing
 * here recomputes a decision: entry, exit, direction, PnL and the bracket levels
 * all arrive already decided, and the server cross-checks the levels against the
 * simulator's own recorded exit prices.
 *
 * TIME SEMANTICS (mandatory, see docs/ARCHITECTURE.md §7)
 * ------------------------------------------------------
 * Bar keys are native broker wall-clock seconds in the domain
 * the exact MT5 wall-clock domain recorded by each dataset. They are NOT UTC.
 * We render civil time by mechanical decode through UTC-based getters ONLY,
 * which reproduces the literal encoded wall-clock fields with no offset. Using any
 * local-time getter (getHours, toLocaleString, ...) would silently shift every
 * timestamp by the viewer's own UTC offset and invite false conclusions about
 * time of day. tests/test_time_semantics.py fails the build if one appears.
 */
'use strict';

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const pad = (n) => String(n).padStart(2, '0');

/** Decode a native key into its literal broker wall-clock fields. UTC getters only. */
function brokerFields(sec) {
  const d = new Date(sec * 1000);
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth(),
    day: d.getUTCDate(),
    weekday: d.getUTCDay(),
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
  };
}

/** "2021-03-05 Fri 16:06" — the broker's own wall-clock reading. */
function brokerDateTime(sec) {
  const f = brokerFields(sec);
  return `${f.year}-${pad(f.month + 1)}-${pad(f.day)} ${WEEKDAYS[f.weekday]} `
       + `${pad(f.hour)}:${pad(f.minute)}`;
}

/** "16:06" — used on the time axis where the date is implied. */
function brokerClock(sec) {
  const f = brokerFields(sec);
  return `${pad(f.hour)}:${pad(f.minute)}`;
}

/** "5 Mar" — axis tick where a new day starts. */
function brokerDay(sec) {
  const f = brokerFields(sec);
  return `${f.day} ${MONTHS[f.month]}`;
}

// ---------------------------------------------------------------- x-scaling
//
// Horizontal candle scaling, driven explicitly rather than relying on the
// library's own time-axis drag. Native axis-drag is wired for touch in the
// bundle, but did not work in practice on a real phone across two attempts, so
// this replaces the assumption with a gesture we control.
//
// The maths is kept pure and above the DOM boundary so it can be executed
// directly in tests: given the span at gesture start and the total pointer
// travel, it returns the new span. Computing from the START each move (rather
// than accumulating per-move deltas) is what keeps the gesture free of drift
// and free of jumps when it starts or ends.

// How far a finger must travel for an e-fold change. Larger = gentler.
const X_SCALE_TRAVEL = 180;
// Candle width limits, in CSS pixels per bar. The lower bound matches LWC's own
// minBarSpacing default so this clamp never fights the library's; the upper one
// stops a single candle filling the screen.
const MIN_BAR_PX = 0.5;
const MAX_BAR_PX = 60;

/**
 * Return the visible logical span after dragging `dx` pixels on the time axis.
 * Dragging right stretches the axis (fewer bars visible, wider candles);
 * dragging left compresses it. Clamped so candles cannot collapse or explode.
 */
function scaledLogicalSpan(startSpan, dx, chartWidth, minBarPx, maxBarPx) {
  if (!(startSpan > 0) || !(chartWidth > 0)) return startSpan;
  const minSpan = chartWidth / maxBarPx;
  const maxSpan = chartWidth / minBarPx;
  const scaled = startSpan * Math.exp(-dx / X_SCALE_TRAVEL);
  return Math.min(maxSpan, Math.max(minSpan, scaled));
}

const css = (name) => getComputedStyle(document.documentElement)
  .getPropertyValue(name).trim();

// Lightweight Charts LineStyle: 0 Solid, 1 Dotted, 2 Dashed, 3 LargeDashed.
const DASHED = 2;
const DOTTED = 1;

const BARS_BEFORE_ENTRY = 90;   // context ahead of the decision, on screen
const BARS_AFTER_EXIT = 60;     // outcome after the trade closed, on screen
const FETCH_BEFORE = 600;       // loaded window, so panning has somewhere to go
const FETCH_AFTER = 400;

// Display timeframes. The server builds M5/M15/H1 from M1 on the broker's own
// native key grid; the browser never aggregates and never interprets buckets.
const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1'];

let chart = null;
let series = null;
let markers = null;
let priceLines = [];
// Strategy-intended context is drawn as SHORT LINE SEGMENTS that begin at the
// signal, never as full-width price lines. That is deliberate on two counts: it
// keeps the executed levels (full-width price lines) visually dominant, and it
// refuses to imply the zone existed before the signal — the provider never
// recorded when the zone was created, so the chart must not draw it earlier.
let contextSeries = null;

const state = {
  runId: null,
  run: null,
  trades: [],
  index: 0,
  bars: null,
  timeframe: 'M1',
  signals: null,
  bracketMode: 'uniform',
  tradeLogVersion: null,
};

const barCache = new Map();               // `${runId}:${entryKey}` -> bars payload
const CACHE_LIMIT = 24;

const $ = (id) => document.getElementById(id);
const fmtPrice = (v) => (v === null || v === undefined ? '—' : v.toFixed(2));

async function getJSON(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch { /* keep status */ }
    throw new Error(detail);
  }
  return response.json();
}

// ----------------------------------------------------------------- rendering

function renderRunHeader() {
  const run = state.run;
  $('h-rid').textContent = `…${run.run_id.slice(-8)}`;
  const execution = run.execution;
  // Once trades can carry their own brackets, a single global TP/SL in the
  // header would be a claim about every trade that is no longer true. Say
  // "per-trade" instead and let the trade bar show the actual levels.
  const perTrade = state.bracketMode === 'per_trade';
  const brackets = perTrade
    ? 'brackets per-trade'
    : (execution.take_profit_pips !== null && execution.stop_loss_pips !== null)
      ? `TP ${execution.take_profit_pips}/SL ${execution.stop_loss_pips}` : '';
  $('h-meta').textContent =
    [run.plugins.features.join(', '), brackets, run.dataset.role].filter(Boolean).join('  ·  ');
}

// A level plus how far it sat from the fill, so a per-trade bracket is legible
// without the reader doing the subtraction. An absent side shows an em dash
// rather than a zero, which would read as a bracket at the entry price.
function bracketLabel(level, distance) {
  if (level === null || level === undefined) return '—';
  if (distance === null || distance === undefined) return fmtPrice(level);
  return `${fmtPrice(level)} (${fmtPrice(distance)})`;
}

function renderTradeBar() {
  const t = state.trades[state.index];
  const isLong = t.direction > 0;
  const dir = $('t-dir');
  dir.textContent = isLong ? 'LONG' : 'SHORT';
  dir.className = 'dir ' + (isLong ? 'long' : 'short');

  $('t-entry').textContent = `${fmtPrice(t.entry_price)}  ${brokerDateTime(t.entry_key)}`;
  $('t-exit').textContent = `${fmtPrice(t.exit_price)}  ${brokerDateTime(t.exit_key)}`;
  $('t-reason').textContent = t.exit_reason;
  $('t-tp').textContent = bracketLabel(t.take_profit, t.take_profit_distance);
  $('t-sl').textContent = bracketLabel(t.stop_loss, t.stop_loss_distance);

  const pnl = $('t-pnl');
  pnl.textContent = (t.net_pnl >= 0 ? '+' : '') + t.net_pnl.toFixed(2);
  pnl.className = 'v ' + (t.net_pnl >= 0 ? 'pos' : 'neg');

  // Money is only as trustworthy as the cost audit. Say so, permanently.
  const semantics = (state.run && state.run.execution.cost_semantics) || 'unverified';
  const badge = $('t-cost');
  badge.textContent = semantics;
  badge.title = `cost_semantics=${semantics}; `
              + `execution_price_model=${(state.run && state.run.execution.execution_price_model) || '—'}. `
              + 'Not a real-money estimate.';

  $('counter').textContent = `Trade ${state.index + 1} / ${state.trades.length}`;
  renderDetails(t);
  renderContext(t);
}

function renderContext(trade) {
  const head = $('ctx-head');
  const body = $('ctx-body');
  const signals = state.signals;
  const context = signals && signals.contexts
    ? signals.contexts[String(trade.index)] : null;

  if (!context) {
    head.textContent = 'Trade context';
    const reason = signals && !signals.available
      ? (signals.reason || 'no recorded decision context for this run')
      : 'no recorded signal matched this trade';
    body.innerHTML = `<div class="missing">${reason}.</div>`;
    return;
  }

  if (context.kind === 'uls_push') {
    renderUlsContext(context, head, body);
    return;
  }

  const zone = context.zone || {};
  const intended = context.intended || {};
  const executed = context.executed || {};
  const price = (v) => (v === null || v === undefined ? '—' : v.toFixed(2));

  head.textContent = zone.kind
    ? `Trade context · ${zone.kind} zone`
    : 'Trade context';

  const rows = [];
  const group = (label) => rows.push(`<div class="grp">${label}</div>`);
  const row = (k, v, cls = '') =>
    rows.push(`<div class="row ${cls}"><span class="k">${k}</span><span class="v">${v}</span></div>`);

  group('Signal');
  row('time', brokerDateTime(context.signal_key));
  row('direction', context.event_signed > 0 ? 'LONG (+1)' : 'SHORT (−1)');
  if (context.confirmation_mode) row('confirmation', context.confirmation_mode);
  if (context.max_bars_wait !== undefined && context.max_bars_wait !== null) {
    row('max bars wait', context.max_bars_wait);
  }

  if (zone.top !== null && zone.top !== undefined) {
    group('Zone');
    row('type', zone.kind || `code ${zone.kind_code}`);
    row('bounds', `${price(zone.bottom)} – ${price(zone.top)}`);
    row('shown from', brokerDateTime(zone.known_from));
  }

  group('Strategy intended');
  row('entry', price(intended.entry), 'intended');
  row('stop', price(intended.stop), 'intended');
  row('target', price(intended.target), 'intended');

  group('Executed by Plane B');
  row('entry', price(executed.entry), 'executed');
  row('stop (SL)', price(executed.stop), 'executed');
  row('target (TP)', price(executed.target), 'executed');

  body.innerHTML = rows.join('');

  const missing = [];
  if (!zone.created_key && zone.top !== null && zone.top !== undefined) {
    missing.push('<b>Zone creation time was not recorded</b>, so the zone is drawn '
               + 'from the signal onward only — not from where it actually formed.');
  }
  if (signals.unrecorded && signals.unrecorded.length) {
    missing.push(`Also not recorded: ${signals.unrecorded
      .filter((item) => item !== 'zone creation time').join(' · ')}.`);
  }
  if (missing.length) {
    body.insertAdjacentHTML('beforeend', `<div class="missing">${missing.join(' ')}</div>`);
  }
}

/** Render only the ULS facts retained by the provider and the simulator. */
function renderUlsContext(context, head, body) {
  const source = context.source || {};
  const poi = context.poi || {};
  const setup = context.setup || {};
  const confirmations = context.confirmations || {};
  const executed = context.executed || {};
  const price = (v) => (v === null || v === undefined ? '—' : v.toFixed(2));
  const number = (v, digits = 2) =>
    (v === null || v === undefined ? '—' : v.toFixed(digits));
  const yes = (v) => (v === null || v === undefined ? '—' : (v > 0 ? 'yes' : 'no'));

  head.textContent = `Trade context · ${source.label || 'ULS + Push'}`;
  const rows = [];
  const group = (label) => rows.push(`<div class="grp">${label}</div>`);
  const row = (k, v, cls = '') =>
    rows.push(`<div class="row ${cls}"><span class="k">${k}</span><span class="v">${v}</span></div>`);

  group('Signal');
  row('time', brokerDateTime(context.signal_key));
  row('direction', context.event_signed > 0 ? 'LONG (+1)' : 'SHORT (−1)');
  row('source', source.label || `code ${source.code ?? '—'}`);
  row('score', number(context.score, 1));

  group('Recorded setup');
  row('entry reference', price(context.entry_ref));
  if (poi.top !== null && poi.top !== undefined) {
    row('POI', `${price(poi.bottom)} – ${price(poi.top)}`);
  }
  row('liquidity', `${price(setup.liquidity_price)} · ${number(setup.liquidity_touches, 0)} touch(es)`);
  row('sweep extreme', price(setup.sweep_extreme));
  row('sweep / ATR', number(setup.sweep_distance_atr));
  row('structure', price(setup.structure));
  row('opposite liquidity', price(setup.opposite_liquidity));
  row('volume / baseline', `${number(setup.volume_relative)}x`);
  row('volatility rank', number(setup.volatility_rank, 1));
  row('confirmations', `BOS ${yes(confirmations.bos)} · TL ${yes(confirmations.trendline)} · `
                     + `CISD ${yes(confirmations.cisd)} · key ${yes(confirmations.key_level)}`);

  group('Executed by Plane B');
  row('entry', price(executed.entry), 'executed');
  row('stop (SL)', price(executed.stop), 'executed');
  row('target (TP)', price(executed.target), 'executed');
  body.innerHTML = rows.join('');
}

function renderDetails(t) {
  const run = state.run;
  const rows = [
    ['run', run.run_id],
    ['fingerprint', run.experiment_fingerprint.slice(0, 16) + '…'],
    ['dataset', `${run.dataset.dataset_id} (${run.dataset.role})`],
    ['features', run.plugins.features.join(', ') || '—'],
    ['model', run.plugins.model || '—'],
    ['policy', run.plugins.policies.join(', ') || '—'],
    ['fold', String(t.fold_index)],
    ['gross pnl', t.gross_pnl.toFixed(2)],
    ['spread', t.spread_cost.toFixed(2)],
    ['commission', t.commission_cost.toFixed(2)],
    ['ambiguous exit', String(t.ambiguous_exit)],
    ['gapped exit', String(t.gapped_exit)],
    // Whether the TP/SL above were read from the trade or derived from the run's
    // global configuration. A reader should never have to guess which.
    ['bracket source', t.bracket_source === 'synthetic_demo'
      ? 'synthetic interface example'
      : t.bracket_source === 'recorded'
      ? 'recorded by simulator'
      : 'reconstructed (legacy v1 artifact)'],
    ['execution', run.execution.execution_id || '—'],
    ['price model', run.execution.execution_price_model || '—'],
  ];
  const grid = $('detail-grid');
  grid.innerHTML = '';
  for (const [key, value] of rows) {
    const k = document.createElement('div'); k.className = 'dk'; k.textContent = key;
    const v = document.createElement('div'); v.className = 'dv'; v.textContent = value;
    grid.append(k, v);
  }
}

// -------------------------------------------------------------------- chart

function buildChart() {
  const el = $('chart');

  chart = LightweightCharts.createChart(el, {
    autoSize: true,                       // survives rotation / resize
    layout: {
      background: { color: css('--ground') },
      textColor: css('--ink-dim'),
      fontFamily: css('--mono'),
      attributionLogo: true,              // Apache-2.0 attribution, kept
    },
    grid: {
      vertLines: { color: css('--hairline'), style: DOTTED },
      horzLines: { color: css('--hairline'), style: DOTTED },
    },
    rightPriceScale: {
      borderColor: css('--hairline'),
      scaleMargins: { top: 0.12, bottom: 0.12 },
      // Native drag-to-scale (handleScale.axisPressedMouseMove.price below) grabs
      // this strip; LWC auto-fits its width to label text (as narrow as ~45px),
      // which is too thin to reliably grab with a thumb. Widen the touch target
      // only — the drag/double-tap-reset gesture itself is unchanged and native.
      minimumWidth: 68,
    },
    timeScale: {
      borderColor: css('--hairline'),
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 4,
      // Horizontal drag-to-scale on the time axis is native and already wired for
      // touch (the axis widget gates it on handleScroll.horzTouchDrag, which is on
      // below). What was missing is somewhere to put a thumb: LWC auto-fits the
      // axis to its label text, well under 30px, and the timeframe buttons sit
      // directly beneath it. Same fix as rightPriceScale.minimumWidth — widen the
      // target only; the gesture itself is unchanged.
      minimumHeight: 44,
      // Axis labels are broker wall-clock, decoded mechanically.
      tickMarkFormatter: (time, tickMarkType) =>
        // 0 Year, 1 Month, 2 DayOfMonth, 3 Time, 4 TimeWithSeconds
        (tickMarkType <= 2 ? brokerDay(time) : brokerClock(time)),
    },
    localization: {
      // Crosshair / price-line labels, same rule.
      timeFormatter: (time) => brokerDateTime(time),
      priceFormatter: (p) => p.toFixed(2),
    },
    crosshair: {
      mode: 0,                            // Normal: free crosshair, not magnet
      vertLine: { color: css('--ink-dim'), width: 1, style: DASHED, labelBackgroundColor: css('--panel') },
      horzLine: { color: css('--ink-dim'), width: 1, style: DASHED, labelBackgroundColor: css('--panel') },
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,                // one-finger horizontal pan
      // The price-axis widget uses this flag too: false classifies a vertical
      // touch there as page scroll and suppresses its native scale callback.
      vertTouchDrag: true,
    },
    handleScale: {
      mouseWheel: true,
      pinch: true,                        // two-finger zoom
      axisPressedMouseMove: true,         // vertical drag on the price axis compresses
                                          // / stretches it (MT5-style); touch-native
      axisDoubleClickReset: true,         // double-tap the axis to snap back to autoscale
    },
  });

  // The zone is drawn as a shaded rectangle BEFORE the candlestick series, so it
  // renders behind the candles: the fill tints the background rather than the
  // price action, and candles stay crisp and fully readable through it.
  //
  // Lightweight Charts has no rectangle primitive, so the band is a BaselineSeries
  // whose baseValue is the zone's lower bound and whose line is its upper bound —
  // the fill between them is exactly the zone. Its horizontal extent comes from
  // the data range (signal → exit), which is what keeps the rectangle from
  // implying the zone existed before the signal.
  const zoneEdge = 'rgba(123,140,165,0.55)';
  const zoneBand = chart.addSeries(LightweightCharts.BaselineSeries, {
    baseValue: { type: 'price', price: 0 },
    topLineColor: zoneEdge,
    topFillColor1: 'rgba(123,140,165,0.16)',
    topFillColor2: 'rgba(123,140,165,0.16)',   // flat, not a gradient: this is a
    bottomLineColor: 'rgba(0,0,0,0)',          // region, not a magnitude
    bottomFillColor1: 'rgba(0,0,0,0)',
    bottomFillColor2: 'rgba(0,0,0,0)',
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    pointMarkersVisible: false,
  });
  const zoneFloor = chart.addSeries(LightweightCharts.LineSeries, {
    color: zoneEdge, lineWidth: 1, lineStyle: 0,
    priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false, pointMarkersVisible: false,
  });

  series = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: css('--long'),
    downColor: css('--short'),
    borderUpColor: css('--long'),
    borderDownColor: css('--short'),
    wickUpColor: css('--long'),
    wickDownColor: css('--short'),
    priceLineVisible: false,
    lastValueVisible: false,
  });

  // Five subordinate context lines: the zone band, and the strategy's own
  // intended entry / stop / target. All muted and thin so they read as context
  // behind the executed overlays, never as competing levels.
  const dim = css('--ink-dim');
  const makeContextLine = (color, width, style) =>
    chart.addSeries(LightweightCharts.LineSeries, {
      color, lineWidth: width, lineStyle: style,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
      pointMarkersVisible: false,
    });
  contextSeries = {
    zoneBand,
    zoneFloor,
    intendedEntry: makeContextLine(dim, 1, DOTTED),
    intendedStop: makeContextLine(dim, 1, DOTTED),
    intendedTarget: makeContextLine(dim, 1, DOTTED),
  };

  installTimeAxisScaling(el);

  const readout = $('c-readout');
  chart.subscribeCrosshairMove((param) => {
    const bar = param.seriesData && param.seriesData.get(series);
    if (!param.time || !bar) { readout.textContent = idleReadout(); return; }
    readout.textContent =
      `${brokerDateTime(param.time)}  O ${bar.open.toFixed(2)}  H ${bar.high.toFixed(2)}`
      + `  L ${bar.low.toFixed(2)}  C ${bar.close.toFixed(2)}`;
  });
}

function idleReadout() {
  return state.bars ? `${state.bars.count} M1 bars · tap chart to inspect` : 'loading…';
}

/** Replace the trade overlays. Old lines must be removed or they accumulate. */
function drawOverlays(t, bars) {
  for (const line of priceLines) series.removePriceLine(line);
  priceLines = [];

  const signal = css('--signal');
  // The server returns bucket-snapped anchors so a marker lands on the candle
  // that contains the fill at any timeframe. At H1 a short trade's entry and
  // exit can share one candle; that is the honest rendering, not a bug.
  const entryAt = (bars && bars.entry_bar_key) ?? t.entry_key;
  const exitAt = (bars && bars.exit_bar_key) ?? t.exit_key;
  const points = [
    {
      time: entryAt,
      position: t.direction > 0 ? 'belowBar' : 'aboveBar',
      shape: t.direction > 0 ? 'arrowUp' : 'arrowDown',
      color: signal,
      text: 'ENTRY',
    },
    {
      time: exitAt,
      position: t.direction > 0 ? 'aboveBar' : 'belowBar',
      shape: t.direction > 0 ? 'arrowDown' : 'arrowUp',
      color: signal,
      text: 'EXIT',
    },
  ];
  // The signal candle is one execution row before the fill. Shown as a small
  // hollow tick so it reads as context, not as a third execution event.
  const context = state.signals && state.signals.contexts
    ? state.signals.contexts[String(t.index)] : null;
  if (context && bars && bars.bars.length) {
    const data = bars.bars;
    const at = data.findIndex((b) => b.t >= context.signal_key);
    if (at >= 0) {
      const kind = (context.zone && context.zone.kind)
        || (context.source && context.source.label);
      points.unshift({
        time: data[at].t,
        position: t.direction > 0 ? 'belowBar' : 'aboveBar',
        shape: 'circle',
        color: css('--ink-dim'),
        // The zone type rides on the signal marker rather than adding a second
        // label, so the chart gains information without gaining clutter.
        text: kind ? `${kind.toUpperCase()} signal` : 'signal',
      });
    }
  }

  if (markers) markers.setMarkers(points);
  else markers = LightweightCharts.createSeriesMarkers(series, points);

  priceLines.push(series.createPriceLine({
    price: t.entry_price, color: signal, lineWidth: 1, lineStyle: DOTTED,
    axisLabelVisible: true, title: 'ENTRY',
  }));
  if (t.take_profit !== null) {
    priceLines.push(series.createPriceLine({
      price: t.take_profit, color: css('--long'), lineWidth: 1, lineStyle: DASHED,
      axisLabelVisible: true, title: 'TP',
    }));
  }
  if (t.stop_loss !== null) {
    priceLines.push(series.createPriceLine({
      price: t.stop_loss, color: css('--short'), lineWidth: 1, lineStyle: DASHED,
      axisLabelVisible: true, title: 'SL',
    }));
  }
}

/**
 * Install the custom horizontal-scaling gesture on the bottom axis strip.
 *
 * Only this strip reacts. The main pane keeps its own pan and pinch untouched,
 * and the right price axis keeps its vertical drag, because the strip is inset
 * away from both. Scaling is applied through setVisibleLogicalRange — a
 * supported time-scale API — so candle data is never touched.
 */
function installTimeAxisScaling(container) {
  const strip = document.createElement('div');
  strip.id = 'xscale';
  strip.setAttribute('aria-hidden', 'true');
  container.appendChild(strip);

  let gesture = null;

  // TEMP DIAGNOSTIC: opt in with `&xdiag=1` on the chart URL. This is kept
  // observational so a real-phone report can distinguish "the finger missed the
  // strip" from "the range API was called but did not stick" without changing
  // the normal interaction path.
  const diagnosticEnabled = new URLSearchParams(window.location.search).get('xdiag') === '1';
  const eventCounts = () => ({
    pointerdown: 0, pointermove: 0, pointerup: 0, pointercancel: 0,
    touchstart: 0, touchmove: 0, touchend: 0, touchcancel: 0,
  });
  const diagnosticState = {
    chart: eventCounts(),
    strip: {
      ...eventCounts(),
      lostpointercapture: 0,
    },
    last: 'ready',
    startSpan: null,
    requestedSpan: null,
  };
  let diagnostic = null;

  const countLine = (counts) =>
    `d${counts.pointerdown}/m${counts.pointermove}/u${counts.pointerup}`
    + `/c${counts.pointercancel}`;
  const touchCountLine = (counts) =>
    `s${counts.touchstart}/m${counts.touchmove}/e${counts.touchend}`
    + `/c${counts.touchcancel}`;
  const fixed = (value) => (Number.isFinite(value) ? value.toFixed(1) : '—');
  const elementName = (element) => {
    if (!element) return 'none';
    return `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}`;
  };
  const rectLine = (rect) => rect
    ? `${fixed(rect.left)},${fixed(rect.top)} ${fixed(rect.width)}x${fixed(rect.height)}`
    : '—';

  const renderDiagnostic = (event = null) => {
    if (!diagnostic) return;
    const stripRect = strip.getBoundingClientRect();
    const rows = container.querySelectorAll('.tv-lightweight-charts tr');
    const timeRow = rows.length ? rows[rows.length - 1] : null;
    const timeCell = timeRow && timeRow.children.length > 1 ? timeRow.children[1] : null;
    const axisRect = timeCell ? timeCell.getBoundingClientRect() : null;
    const hit = document.elementFromPoint(
      stripRect.left + stripRect.width / 2,
      stripRect.top + stripRect.height / 2,
    );
    const range = chart && chart.timeScale().getVisibleLogicalRange();
    const span = range ? range.to - range.from : null;
    const sourcePoint = event && (
      (event.touches && event.touches[0])
      || (event.changedTouches && event.changedTouches[0])
      || event
    );
    const point = sourcePoint && Number.isFinite(sourcePoint.clientX)
      ? ` @${fixed(sourcePoint.clientX)},${fixed(sourcePoint.clientY)}` : '';
    diagnostic.textContent =
      `X DIAG  chart P ${countLine(diagnosticState.chart)}`
      + ` T ${touchCountLine(diagnosticState.chart)}\n`
      + `strip P ${countLine(diagnosticState.strip)}`
      + `/l${diagnosticState.strip.lostpointercapture}`
      + ` T ${touchCountLine(diagnosticState.strip)}\n`
      + `last ${diagnosticState.last}${point}`
      + `${event ? ` target=${elementName(event.target)}` : ''}\n`
      + `span actual=${fixed(span)} start=${fixed(diagnosticState.startSpan)}`
      + ` requested=${fixed(diagnosticState.requestedSpan)}\n`
      + `strip ${rectLine(stripRect)}  axis ${rectLine(axisRect)}`
      + `  hit=${elementName(hit)}`;
  };

  if (diagnosticEnabled) {
    strip.dataset.diagnostic = 'true';
    diagnostic = document.createElement('output');
    diagnostic.id = 'xscale-debug';
    diagnostic.setAttribute('aria-live', 'off');
    document.body.appendChild(diagnostic);

    const pointerTypes = ['pointerdown', 'pointermove', 'pointerup', 'pointercancel'];
    const touchTypes = ['touchstart', 'touchmove', 'touchend', 'touchcancel'];
    for (const type of [...pointerTypes, ...touchTypes]) {
      container.addEventListener(type, (event) => {
        diagnosticState.chart[type] += 1;
        diagnosticState.last = `chart ${type}`;
        renderDiagnostic(event);
      }, { capture: true, passive: true });
    }
    for (const type of touchTypes) {
      strip.addEventListener(type, (event) => {
        diagnosticState.strip[type] += 1;
        diagnosticState.last = `strip ${type}`;
        renderDiagnostic(event);
      }, { capture: true, passive: true });
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => renderDiagnostic());
    window.addEventListener('resize', () => renderDiagnostic(), { passive: true });
    requestAnimationFrame(() => renderDiagnostic());
  }

  strip.addEventListener('pointerdown', (event) => {
    if (diagnostic) {
      diagnosticState.strip.pointerdown += 1;
      diagnosticState.last = `strip pointerdown (${event.pointerType})`;
    }
    if (gesture) return;                       // ignore extra fingers on the strip
    const range = chart && chart.timeScale().getVisibleLogicalRange();
    if (!range) { renderDiagnostic(event); return; }
    gesture = {
      id: event.pointerId,
      startX: event.clientX,
      startSpan: range.to - range.from,
      // Hold the centre fixed so scaling grows/shrinks around what you are
      // looking at instead of walking the view sideways.
      centre: (range.from + range.to) / 2,
      width: container.clientWidth,
    };
    diagnosticState.startSpan = gesture.startSpan;
    diagnosticState.requestedSpan = gesture.startSpan;
    try { strip.setPointerCapture(event.pointerId); } catch { /* not fatal */ }
    event.preventDefault();
    renderDiagnostic(event);
  });

  strip.addEventListener('pointermove', (event) => {
    if (diagnostic) {
      diagnosticState.strip.pointermove += 1;
      diagnosticState.last = `strip pointermove (${event.pointerType})`;
    }
    if (!gesture || event.pointerId !== gesture.id) { renderDiagnostic(event); return; }
    const span = scaledLogicalSpan(
      gesture.startSpan, event.clientX - gesture.startX,
      gesture.width, MIN_BAR_PX, MAX_BAR_PX,
    );
    diagnosticState.requestedSpan = span;
    chart.timeScale().setVisibleLogicalRange({
      from: gesture.centre - span / 2,
      to: gesture.centre + span / 2,
    });
    event.preventDefault();
    renderDiagnostic(event);
  });

  const release = (event) => {
    if (diagnostic && event.type in diagnosticState.strip) {
      diagnosticState.strip[event.type] += 1;
      diagnosticState.last = `strip ${event.type} (${event.pointerType})`;
    }
    if (!gesture || event.pointerId !== gesture.id) { renderDiagnostic(event); return; }
    try { strip.releasePointerCapture(gesture.id); } catch { /* already gone */ }
    gesture = null;                            // next gesture re-reads the range
    renderDiagnostic(event);
  };
  strip.addEventListener('pointerup', release);
  strip.addEventListener('pointercancel', release);
  strip.addEventListener('lostpointercapture', release);
}

/** Draw the recorded decision context, from the signal onward only. */
function drawContext(trade, bars) {
  if (!contextSeries) return;
  const blank = () => {
    for (const series of Object.values(contextSeries)) series.setData([]);
  };
  const context = state.signals && state.signals.contexts
    ? state.signals.contexts[String(trade.index)] : null;
  if (!context || !bars || !bars.bars.length) { blank(); return; }

  const data = bars.bars;
  // Snap to candles that actually exist, so a segment never extends the range.
  const startAt = data.findIndex((b) => b.t >= context.signal_key);
  if (startAt < 0) { blank(); return; }
  let endAt = data.findIndex((b) => b.t >= trade.exit_key);
  if (endAt < 0) endAt = data.length - 1;
  endAt = Math.min(data.length - 1, Math.max(endAt, startAt + 1));
  const from = data[startAt].t;
  const to = data[endAt].t;

  const segment = (value) => (value === null || value === undefined
    ? [] : [{ time: from, value }, { time: to, value }]);

  const zone = context.zone || context.poi || {};
  const intended = context.intended || {};

  // Rectangle = fill between the recorded zone/POI bounds, bounded horizontally
  // by the visible data range. Nothing is reconstructed.
  if (zone.top !== null && zone.top !== undefined
      && zone.bottom !== null && zone.bottom !== undefined) {
    contextSeries.zoneBand.applyOptions({
      baseValue: { type: 'price', price: zone.bottom },
    });
    contextSeries.zoneBand.setData(segment(zone.top));
    contextSeries.zoneFloor.setData(segment(zone.bottom));
  } else {
    contextSeries.zoneBand.setData([]);
    contextSeries.zoneFloor.setData([]);
  }

  contextSeries.intendedEntry.setData(segment(intended.entry));
  contextSeries.intendedStop.setData(segment(intended.stop));
  contextSeries.intendedTarget.setData(segment(intended.target));
}

/** Return to the trade. Never locks the view — pan and zoom stay free after. */
function recenter() {
  if (!state.bars || !state.bars.bars.length) return;
  const data = state.bars.bars;
  const t = state.trades[state.index];

  const entryKey = state.bars.entry_bar_key ?? t.entry_key;
  const exitKey = state.bars.exit_bar_key ?? t.exit_key;
  let entryAt = data.findIndex((b) => b.t >= entryKey);
  let exitAt = data.findIndex((b) => b.t >= exitKey);
  if (entryAt < 0) entryAt = 0;
  if (exitAt < 0) exitAt = data.length - 1;

  const from = data[Math.max(0, entryAt - BARS_BEFORE_ENTRY)].t;
  const to = data[Math.min(data.length - 1, exitAt + BARS_AFTER_EXIT)].t;
  // Range is set in TIME, never logical indices: logical indices shift whenever
  // the bar array changes, and restoring one after a change teleports the view.
  chart.timeScale().setVisibleRange({ from, to });
  series.priceScale().applyOptions({ autoScale: true });
}

// --------------------------------------------------------------- navigation

function barsKey(runId, trade, timeframe) {
  return `${runId}:${timeframe}:${trade.entry_key}`;
}

async function fetchBars(trade, timeframe = state.timeframe) {
  const key = barsKey(state.runId, trade, timeframe);
  const hit = barCache.get(key);
  if (hit) return hit;
  const url = `/api/runs/${state.runId}/bars?entry_key=${trade.entry_key}`
            + `&exit_key=${trade.exit_key}&timeframe=${encodeURIComponent(timeframe)}`
            + `&before=${FETCH_BEFORE}&after=${FETCH_AFTER}`;
  const payload = await getJSON(url);
  barCache.set(key, payload);
  if (barCache.size > CACHE_LIMIT) barCache.delete(barCache.keys().next().value);
  return payload;
}

/** Warm the neighbours so Prev/Next usually resolve from cache. */
function prefetchNeighbours() {
  for (const offset of [1, -1]) {
    const neighbour = state.trades[state.index + offset];
    if (neighbour && !barCache.has(barsKey(state.runId, neighbour, state.timeframe))) {
      fetchBars(neighbour).catch(() => { /* prefetch is best-effort */ });
    }
  }
}

function renderTimeframes() {
  const row = $('timeframes');
  row.innerHTML = '';
  for (const timeframe of TIMEFRAMES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = timeframe;
    button.setAttribute('aria-pressed', String(timeframe === state.timeframe));
    button.addEventListener('click', () => selectTimeframe(timeframe));
    row.appendChild(button);
  }
}

/** Switch timeframe while holding the selected run and trade fixed. */
async function selectTimeframe(timeframe) {
  if (timeframe === state.timeframe || !state.trades.length) return;
  state.timeframe = timeframe;
  renderTimeframes();
  // selectTrade re-fetches at the new timeframe and recenters on the same trade.
  await selectTrade(state.index);
}

let selectToken = 0;

async function selectTrade(index) {
  if (!state.trades.length) return;
  state.index = Math.max(0, Math.min(index, state.trades.length - 1));
  const trade = state.trades[state.index];

  // Summary updates immediately; the chart follows when its bars arrive.
  renderTradeBar();
  $('prev').disabled = state.index === 0;
  $('next').disabled = state.index === state.trades.length - 1;

  const token = ++selectToken;
  try {
    const payload = await fetchBars(trade);
    if (token !== selectToken) return;      // a newer press already won
    state.bars = payload;
    series.setData(payload.bars.map((b) => ({
      time: b.t, open: b.o, high: b.h, low: b.l, close: b.c,
    })));
    $('c-domain').textContent = payload.clock_domain;
    drawOverlays(trade, payload);
    drawContext(trade, payload);
    recenter();
    $('c-readout').textContent = idleReadout();
    prefetchNeighbours();
  } catch (error) {
    if (token === selectToken) $('c-readout').textContent = `bars unavailable — ${error.message}`;
  }
}

async function selectRun(runId, startIndex = 0) {
  state.runId = runId;
  state.bars = null;
  state.signals = null;
  barCache.clear();
  selectToken++;
  $('c-readout').textContent = 'loading run…';
  try {
    const [run, log, signals] = await Promise.all([
      getJSON(`/api/runs/${runId}`),
      getJSON(`/api/runs/${runId}/trades`),
      // Decision context is additive: if it is unavailable the chart still works.
      getJSON(`/api/runs/${runId}/signals`).catch(() => null),
    ]);
    state.run = run;
    state.trades = log.trades;
    state.bracketMode = log.bracket_mode;
    state.tradeLogVersion = log.trade_log_format_version;
    state.signals = signals;
    renderRunHeader();
    if (!state.trades.length) {
      $('c-readout').textContent = 'This run recorded no simulated trades.';
      return;
    }
    await selectTrade(startIndex);
  } catch (error) {
    $('c-readout').textContent = `run unavailable — ${error.message}`;
  }
}

// --------------------------------------------------------------------- boot

async function main() {
  buildChart();

  $('prev').addEventListener('click', () => selectTrade(state.index - 1));
  $('next').addEventListener('click', () => selectTrade(state.index + 1));
  $('recenter').addEventListener('click', recenter);
  renderTimeframes();
  $('back').addEventListener('click', () => {
    const search = new URLSearchParams(window.location.search);
    const run = search.get('run');
    const family = search.get('family');
    if (run) {
      const parts = [`id=${encodeURIComponent(run)}`];
      if (family) parts.push(`family=${encodeURIComponent(family)}`);
      window.location.href = `/run?${parts.join('&')}`;
    } else {
      window.location.href = '/';
    }
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') selectTrade(state.index - 1);
    else if (e.key === 'ArrowRight') selectTrade(state.index + 1);
    else if (e.key === 'r' || e.key === 'R') recenter();
  });

  const search = new URLSearchParams(window.location.search);
  const requested = search.get('run');
  if (!requested) {
    // The chart is always opened for a specific run from the run overview.
    window.location.href = '/';
    return;
  }
  const startAt = Number.parseInt(search.get('trade') ?? '0', 10);
  await selectRun(requested, Number.isFinite(startAt) ? startAt : 0);
}

main();
