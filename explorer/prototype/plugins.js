/* Research Plugins — the Trade Explorer's first screen.
 *
 * Lists each dataset separately, then the (feature provider, model, decision
 * policy) tuple each run's own resolved config declares.
 *
 * Family totals shown here (runs, trades, net PnL) are a further sum of each
 * run's own already-summed fold totals — still plain addition of recorded
 * values, not a new statistic. No ratio (win rate, drawdown) is aggregated at
 * this level, to keep that distinction unambiguous.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const num = (v) => (v === null || v === undefined ? '—' : v.toFixed(2));

function familyTotals(group) {
  let trades = 0;
  let netPnl = 0;
  let lastFinished = 0;
  for (const run of group.runs) {
    trades += run.metrics.trades_sum ?? 0;
    netPnl += run.metrics.net_pnl_sum ?? 0;
    lastFinished = Math.max(lastFinished, Number(run.finished_unix_ns ?? 0));
  }
  return { trades, netPnl, lastFinished };
}

/** Broker wall-clock date. UTC getters only — never a local-timezone shift. */
function runDate(unixNs) {
  if (!unixNs) return '—';
  const d = new Date(Number(unixNs) / 1e6);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function familyRow(group) {
  const totals = familyTotals(group);
  const row = document.createElement('button');
  row.type = 'button';
  row.className = 'family-row';
  row.setAttribute('aria-label', `Open ${group.dataset_id} ${group.feature_name} experiments`);

  const line1 = document.createElement('div');
  line1.className = 'r1';
  line1.innerHTML =
    `<span class="rid">${group.dataset_id} · ${group.feature_name}</span>`
    + `<span>${group.run_count} run${group.run_count === 1 ? '' : 's'}</span>`;

  const line2 = document.createElement('div');
  line2.className = 'r2';
  line2.innerHTML =
    `<span class="m"><span class="lbl">model</span>${group.model_name}</span>`
    + `<span class="m"><span class="lbl">policy</span>${group.policy_name}</span>`
    + `<span class="m"><span class="lbl">all runs</span>${totals.trades} trades</span>`
    + `<span class="pnl ${totals.netPnl >= 0 ? 'pos' : 'neg'}">`
    + `${totals.netPnl >= 0 ? '+' : ''}${num(totals.netPnl)}</span>`
    + `<span class="m"><span class="lbl">latest</span>${runDate(totals.lastFinished)}</span>`;

  row.append(line1, line2);
  row.addEventListener('click', () => {
    window.location.href = `/family?key=${encodeURIComponent(group.key)}`;
  });
  return row;
}

async function main() {
  try {
    const response = await fetch('/api/experiments?limit=2000', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    $('p-sub').textContent =
      `${data.group_count} plugin famil${data.group_count === 1 ? 'y' : 'ies'} · ${data.run_count} runs total`;

    const list = $('p-list');
    list.innerHTML = '';
    if (!data.group_count) {
      list.innerHTML = '<div class="note">No completed runs with simulated trades were found.</div>';
      return;
    }
    for (const group of data.groups) list.appendChild(familyRow(group));

    const note = document.createElement('div');
    note.className = 'note';
    note.textContent = window.SYNTHETIC_DEMO
      ? 'Synthetic examples for exploring the interface. All values are made up.'
      : 'Grouped first by dataset, then by the feature provider, model and policy each run’s '
      + 'configuration declares. “All runs” totals are summed across every run in '
      + 'the family; they carry the same unverified-cost caveat as every trade in it.';
    list.appendChild(note);
  } catch (error) {
    $('p-sub').textContent = `cannot load plugins — ${error.message}`;
  }
}

main();
