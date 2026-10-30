/* Experiments table — every run for one plugin family.
 *
 * Read-only. Every column is either a value the Research Machine recorded, or
 * a clearly labelled aggregation of one: counts and money are additive over a
 * run's disjoint evaluation folds, so they are summed; win rate and drawdown
 * are not additive, so win rate is the observed fold range and drawdown is the
 * worst single fold — never averaged into an invented number.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const num = (v, digits = 2) => (v === null || v === undefined ? '—' : v.toFixed(digits));
const pct = (v) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(0)}%`);

/** Broker wall-clock date. UTC getters only — never a local-timezone shift. */
function runDate(unixNs) {
  if (!unixNs) return '—';
  const d = new Date(Number(unixNs) / 1e6);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function pnlCell(v) {
  if (v === null || v === undefined) return '<span>—</span>';
  const cls = v >= 0 ? 'pos' : 'neg';
  return `<span class="pnl ${cls}">${v >= 0 ? '+' : ''}${v.toFixed(2)}</span>`;
}

// Each column is both a table header and, where `get` is present, a sort key.
// Run is intentionally not sortable — an id has no meaningful order.
const COLUMNS = [
  { id: 'run', label: 'Run', align: 'left',
    cell: (r) => `…${r.run_id.slice(-8)}` },
  { id: 'dataset', label: 'Dataset', align: 'left',
    get: (r) => r.dataset_id || '',
    cell: (r) => r.dataset_id || '—' },
  { id: 'date', label: 'Date', align: 'left', desc: true,
    get: (r) => Number(r.finished_unix_ns ?? 0),
    cell: (r) => runDate(r.finished_unix_ns) },
  { id: 'tp', label: 'TP', align: 'right', desc: true,
    get: (r) => r.take_profit_pips ?? -1,
    cell: (r) => (r.take_profit_pips ?? '—') },
  { id: 'sl', label: 'SL', align: 'right', desc: true,
    get: (r) => r.stop_loss_pips ?? -1,
    cell: (r) => (r.stop_loss_pips ?? '—') },
  { id: 'trades', label: 'Trades', align: 'right', desc: true,
    get: (r) => r.metrics.trades_sum ?? -1,
    cell: (r) => (r.metrics.trades_sum ?? '—') },
  { id: 'pnl', label: 'Net PnL', align: 'right', desc: true,
    get: (r) => r.metrics.net_pnl_sum ?? -Infinity,
    cell: (r) => pnlCell(r.metrics.net_pnl_sum) },
  { id: 'win', label: 'Win/fold', align: 'right', desc: true,
    get: (r) => r.metrics.win_rate_min ?? -1,
    cell: (r) => `${pct(r.metrics.win_rate_min)}–${pct(r.metrics.win_rate_max)}` },
  { id: 'dd', label: 'Worst DD', align: 'right', desc: false,
    get: (r) => r.metrics.max_drawdown_worst ?? Infinity,
    cell: (r) => num(r.metrics.max_drawdown_worst) },
  { id: 'folds', label: 'Folds', align: 'right', desc: true,
    get: (r) => r.metrics.fold_count ?? -1,
    cell: (r) => (r.metrics.fold_count ?? '—') },
];

const state = { group: null, sort: 'date', reversed: false };
const familyKey = new URLSearchParams(window.location.search).get('key') || '';

function sortedRuns() {
  const column = COLUMNS.find((c) => c.id === state.sort) ?? COLUMNS[1];
  const direction = (column.desc ? -1 : 1) * (state.reversed ? -1 : 1);
  return [...state.group.runs].sort((a, b) => {
    const av = column.get(a);
    const bv = column.get(b);
    if (av === bv) return a.run_id.localeCompare(b.run_id);
    return av < bv ? direction : -direction;
  });
}

function renderHead() {
  const headRow = $('f-head');
  headRow.innerHTML = '';
  for (const column of COLUMNS) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.className = `align-${column.align}`;
    if (column.get) {
      const active = state.sort === column.id;
      th.classList.add('sortable');
      if (active) th.classList.add('active');
      th.textContent = active ? `${column.label} ${state.reversed ? '▲' : '▼'}` : column.label;
      th.tabIndex = 0;
      th.setAttribute('role', 'button');
      th.setAttribute('aria-sort', active ? (state.reversed ? 'ascending' : 'descending') : 'none');
      const activate = () => {
        if (state.sort === column.id) state.reversed = !state.reversed;
        else { state.sort = column.id; state.reversed = false; }
        renderHead();
        renderBody();
      };
      th.addEventListener('click', activate);
      th.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
      });
    } else {
      th.textContent = column.label;
    }
    headRow.appendChild(th);
  }
}

function renderBody() {
  const body = $('f-body');
  body.innerHTML = '';
  for (const run of sortedRuns()) {
    const tr = document.createElement('tr');
    tr.tabIndex = 0;
    tr.setAttribute('aria-label', `Open run ${run.run_id}`);
    for (const column of COLUMNS) {
      const td = document.createElement('td');
      td.className = `align-${column.align}`;
      td.innerHTML = column.cell(run);
      tr.appendChild(td);
    }
    const open = () => {
      window.location.href =
        `/run?id=${encodeURIComponent(run.run_id)}&family=${encodeURIComponent(familyKey)}`;
    };
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    body.appendChild(tr);
  }
}

async function main() {
  $('back').addEventListener('click', () => { window.location.href = '/'; });

  try {
    const response = await fetch('/api/experiments?limit=2000', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const group = data.groups.find((g) => g.key === familyKey);
    if (!group) {
      $('f-sub').textContent = 'Plugin family not found. It may have moved — go back and reselect.';
      return;
    }
    state.group = group;
    $('f-title').textContent = group.dataset_id;
    $('f-sub').textContent =
      `${group.feature_name} · ${group.model_name} · ${group.policy_name} · `
      + `${group.run_count} runs`;
    renderHead();
    renderBody();
  } catch (error) {
    $('f-sub').textContent = `cannot load experiments — ${error.message}`;
  }
}

main();
