/* Run Overview — understand a run before inspecting its individual trades.
 *
 * Read-only, and descriptive only. Two kinds of number appear here and they are
 * kept visibly distinct:
 *
 *   RECORDED   the per-fold metrics the Research Machine measured itself —
 *              trade_count, net_pnl, win_rate, max_drawdown. Transcribed, never
 *              recalculated.
 *   TRANSCRIBED  counts of recorded TradeLog columns (direction, exit reason,
 *              flags) and running totals of recorded per-trade net_pnl.
 *
 * No ratio is derived that the platform did not record, and there is no
 * significance, adjusted or deflated statistic anywhere (ADR-0024).
 *
 * TIME SEMANTICS: trade keys are native broker wall-clock seconds, not UTC.
 * Civil time is decoded through UTC-based getters ONLY, so no local timezone
 * offset is ever applied (ADR-0002).
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
    year: d.getUTCFullYear(), month: d.getUTCMonth(), day: d.getUTCDate(),
    weekday: d.getUTCDay(), hour: d.getUTCHours(), minute: d.getUTCMinutes(),
  };
}
function brokerDateTime(sec) {
  const f = brokerFields(sec);
  return `${f.year}-${pad(f.month + 1)}-${pad(f.day)} ${WEEKDAYS[f.weekday]} `
       + `${pad(f.hour)}:${pad(f.minute)}`;
}
function brokerDate(sec) {
  const f = brokerFields(sec);
  return `${f.year}-${pad(f.month + 1)}-${pad(f.day)}`;
}
function brokerShort(sec) {
  const f = brokerFields(sec);
  return `${f.day} ${MONTHS[f.month]} ${String(f.year).slice(2)}`;
}

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement)
  .getPropertyValue(name).trim();
const money = (v) => (v === null || v === undefined ? '—'
  : `${v >= 0 ? '+' : ''}${v.toFixed(2)}`);
const pct = (v) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`);
const sign = (v) => (v >= 0 ? 'pos' : 'neg');

const params = new URLSearchParams(window.location.search);
const runId = params.get('id') || '';
const familyKey = params.get('family') || '';

let data = null;

function chartHref(tradeIndex) {
  const parts = [`run=${encodeURIComponent(runId)}`];
  if (familyKey) parts.push(`family=${encodeURIComponent(familyKey)}`);
  if (tradeIndex !== undefined && tradeIndex !== null) parts.push(`trade=${tradeIndex}`);
  return `/chart?${parts.join('&')}`;
}

function renderHero() {
  const run = data.run;
  const execution = run.execution;

  const pnl = $('h-pnl');
  pnl.textContent = money(data.net_pnl_sum);
  pnl.className = sign(data.net_pnl_sum);

  const badge = $('h-cost');
  badge.textContent = execution.cost_semantics || 'unverified';
  badge.title = `cost_semantics=${execution.cost_semantics}; `
              + `execution_price_model=${execution.execution_price_model}. `
              + 'Not a real-money estimate.';

  const brackets = (execution.take_profit_pips !== null && execution.stop_loss_pips !== null)
    ? `${execution.take_profit_pips} / ${execution.stop_loss_pips}` : '—';
  const facts = [
    ['trades', data.trade_count],
    ['folds', data.folds.length || '—'],
    ['tp / sl', brackets],
    ['dataset', run.dataset.role],
  ];
  if (data.first_entry_key && data.last_exit_key) {
    facts.push(['period', `${brokerShort(data.first_entry_key)} → ${brokerShort(data.last_exit_key)}`]);
  }
  $('h-facts').innerHTML = facts
    .map(([k, v]) => `<span><span class="lbl">${k}</span>${v}</span>`).join('');
}

function renderBreakdown() {
  const rows = [];
  rows.push(['grp', 'Direction']);
  rows.push(['row', 'LONG', data.direction.long.count, data.direction.long.net_pnl]);
  rows.push(['row', 'SHORT', data.direction.short.count, data.direction.short.net_pnl]);
  rows.push(['grp', 'Exit reason']);
  for (const [reason, value] of Object.entries(data.exits)) {
    rows.push(['row', reason.replace(/_/g, ' ').toLowerCase(), value.count, value.net_pnl]);
  }
  rows.push(['grp', 'Flags']);
  rows.push(['row', 'gapped exit', data.flags.gapped_exit, null]);
  rows.push(['row', 'ambiguous exit', data.flags.ambiguous_exit, null]);

  const grid = $('breakdown');
  grid.innerHTML = '';
  for (const row of rows) {
    if (row[0] === 'grp') {
      const head = document.createElement('div');
      head.className = 'grp';
      head.textContent = row[1];
      grid.appendChild(head);
      continue;
    }
    const [, label, count, netPnl] = row;
    const k = document.createElement('div'); k.className = 'k'; k.textContent = label;
    const n = document.createElement('div'); n.className = 'n'; n.textContent = count;
    const m = document.createElement('div');
    m.className = netPnl === null ? 'm' : `m ${sign(netPnl)}`;
    m.textContent = netPnl === null ? '' : money(netPnl);
    grid.append(k, n, m);
  }
}

function renderFolds() {
  const body = $('fold-body');
  body.innerHTML = '';
  if (!data.folds.length) {
    body.innerHTML = '<tr><td colspan="5">No recorded fold metrics — the run index '
                   + 'may not cover this run yet.</td></tr>';
    return;
  }
  for (const fold of data.folds) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td class="align-left">${fold.fold_index}</td>`
      + `<td class="align-right">${fold.trade_count ?? '—'}</td>`
      + `<td class="align-right ${fold.net_pnl === undefined ? '' : sign(fold.net_pnl)}">`
      + `${fold.net_pnl === undefined ? '—' : money(fold.net_pnl)}</td>`
      + `<td class="align-right">${pct(fold.win_rate)}</td>`
      + `<td class="align-right">${fold.max_drawdown === undefined ? '—'
         : fold.max_drawdown.toFixed(2)}</td>`;
    body.appendChild(tr);
  }
}

function renderCurve() {
  const el = $('pnl-chart');
  if (!data.curve.length) {
    $('curve-note').textContent = 'This run recorded no trades.';
    return;
  }

  const chart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: {
      background: { color: css('--ground') },
      textColor: css('--ink-dim'),
      fontFamily: css('--mono'),
      attributionLogo: true,
    },
    grid: {
      vertLines: { color: css('--hairline'), style: 1 },
      horzLines: { color: css('--hairline'), style: 1 },
    },
    rightPriceScale: { borderColor: css('--hairline'), minimumWidth: 62 },
    timeScale: {
      borderColor: css('--hairline'),
      timeVisible: false,
      // Broker wall-clock, decoded mechanically. Never a local timezone shift.
      tickMarkFormatter: (time) => brokerShort(time),
    },
    localization: {
      timeFormatter: (time) => brokerDateTime(time),
      priceFormatter: (v) => v.toFixed(2),
    },
    crosshair: { mode: 0 },
    handleScroll: { horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { pinch: true, axisPressedMouseMove: true },
  });

  // Baseline at zero: the curve is green while the running total is in profit and
  // red while it is under water, so concentrated win/loss periods are visible.
  const series = chart.addSeries(LightweightCharts.BaselineSeries, {
    baseValue: { type: 'price', price: 0 },
    topLineColor: css('--long'),
    topFillColor1: 'rgba(53,192,138,0.28)',
    topFillColor2: 'rgba(53,192,138,0.02)',
    bottomLineColor: css('--short'),
    bottomFillColor1: 'rgba(229,84,75,0.02)',
    bottomFillColor2: 'rgba(229,84,75,0.28)',
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
  });

  // Trades can share an entry key; the series needs strictly increasing times.
  const points = [];
  let previous = null;
  for (const point of data.curve) {
    if (previous !== null && point.t <= previous) continue;
    points.push({ time: point.t, value: point.v });
    previous = point.t;
  }
  series.setData(points);

  if (data.fold_boundaries.length > 1) {
    LightweightCharts.createSeriesMarkers(series, data.fold_boundaries.map((b) => ({
      time: b.t,
      position: 'inBar',
      shape: 'circle',
      color: css('--signal'),
      text: `f${b.fold_index}`,
    })));
  }

  // Tapping the curve opens the Trade Explorer at the nearest trade in time.
  chart.subscribeClick((param) => {
    if (!param.time) return;
    let best = null;
    let bestGap = Infinity;
    for (const point of data.curve) {
      const gap = Math.abs(point.t - param.time);
      if (gap < bestGap) { bestGap = gap; best = point; }
    }
    if (best) window.location.href = chartHref(best.i);
  });

  const dropped = data.curve.length - points.length;
  $('curve-note').textContent = window.SYNTHETIC_DEMO
    ? 'Illustrative running total of synthetic trades. Tap a point to inspect a trade.'
    : `Running total of the recorded net PnL of ${data.curve.length} trades, in order. `
    + (data.fold_boundaries.length > 1 ? 'Amber markers are fold starts. ' : '')
    + 'Folds are independent evaluation windows with a refit between them, so this '
    + 'is a cumulative record of results, not a tradeable equity path.'
    + (dropped > 0 ? ` ${dropped} trade(s) share an entry minute and are not drawn separately.` : '');
}

async function main() {
  $('back').addEventListener('click', () => {
    window.location.href = familyKey
      ? `/family?key=${encodeURIComponent(familyKey)}` : '/';
  });
  $('explore').addEventListener('click', () => { window.location.href = chartHref(); });

  if (!runId) { window.location.href = '/'; return; }

  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/overview`,
                                 { cache: 'no-store' });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch { /* keep status */ }
      throw new Error(detail);
    }
    data = await response.json();

    $('r-title').textContent = `…${data.run.run_id.slice(-8)}`;
    const finished = data.run.finished_unix_ns
      ? brokerDate(Math.floor(Number(data.run.finished_unix_ns) / 1e9)) : '—';
    $('r-sub').textContent =
      `${data.run.plugins.features.join(', ')} · ${data.run.plugins.model} · finished ${finished}`;

    renderHero();
    renderCurve();
    renderBreakdown();
    renderFolds();

    $('r-note').textContent = window.SYNTHETIC_DEMO
      ? 'All prices, trades, and metrics on this page are synthetic interface examples. '
        + 'They were not produced by a research run and are not performance evidence.'
      : 'Fold metrics are transcribed exactly as the Research Machine recorded them. '
      + 'Breakdown counts and totals are a direct reading of the TradeLog. Nothing on '
      + 'this page is recomputed, adjusted or tested for significance, and money is '
      + 'unverified while the cost audit is open.';
  } catch (error) {
    $('r-sub').textContent = `cannot load run — ${error.message}`;
  }
}

main();
