# Trade Explorer — architecture decisions

Status: **approved** · Date: 2026-08-21 · Milestone: E0 + E1

This records the decisions that govern this project. It deliberately does not
restate the Research Machine's architecture — that lives in
`~/trading-research-suite/docs/architecture/V1.md` and its ADRs, which remain
normative for everything on the research side.

---

## 1. One public suite with separate component environments

The engine lives at the repository root and the Explorer in `explorer/`.
Each has its own virtual environment and dependency lockfile. The Explorer
depends on the engine through an editable `..` path dependency.

The combined distribution accepts that all commits, including UI changes,
change the engine's future experiment fingerprints and cache identities.
See [ADR-0029](../../docs/adr/ADR-0029-public-suite-distribution.md), which
supersedes the source project's separate-repository placement requirement.

## 2. The Research Machine remains the source of truth

Run directories, artifacts and market data under `~/trading-research-suite/trading-research-runs/`,
`~/trading-research-suite/trading-research-data/` and `~/trading-research-suite/trading-research-views/` are authoritative.
The Explorer displays them; it never becomes an alternative record of them.

## 3. The Explorer is read-only with respect to research artifacts

It does not write, rename, move or delete anything under any research root. The
fixture generator's only output is inside this project.

## 4. The Explorer preserves recorded research artifacts

At runtime, it must never mutate recorded experiment fingerprints, run
canonicality, simulation, evaluation or run identity. Code changes in this
combined repository affect future identities as described in ADR-0029. Concretely, it never creates a run, never invokes
the orchestrator, and never calls a reconciling run scan — `scan_runs(...)` may
only ever be called with `reconcile=False`, because the reconciling path takes
exclusive run-directory locks and can terminalise a live `RUNNING` run to
`INTERRUPTED`, writing a false failure into the permanent research record
(ADR-0017 Amendment 1 clause 3).

## 5. It may later consume documented public Research Machine interfaces

Available today and sufficient for the planned backend:

| Need | Interface |
|---|---|
| Market bars, gating, bounded views | `trading_research.data`: `DatasetRegistry`, `authorize_access`, `classify_access`, `MarketFrameLoader`, `open_bounded_view`, `read_split_policy` |
| Bar columns | `trading_research.core.frames.MarketFrame` |
| Native time types | `trading_research.core.time` |
| Terminal run record | `trading_research.experiments.runs.RunManifest` |
| Run discovery, index freshness | the `trading-research` CLI (`index status --json`, `runs query --json`) |

Three gaps exist and are accepted for now: there is no public run-index Python
API, no TradeLog reader, and no calendar decoder. TradeLogs are therefore read
as the versioned, self-describing `.npz` container they are — the same way the
platform's own test suite reads them (`tests/test_experiments.py`) — with
`format` asserted and `format_version` checked against the supported set
(`1` and `2`) on every read. **No platform change is
required for E1, and none should be proposed until a second consumer makes one
justifiable by breadth rather than convenience.**

## 6. Files remain truth; the Phase-5 index stays derived and disposable

When the Explorer later reads `<runs_root>/index/runs.sqlite3`, it opens it
read-only, treats it purely as a listing accelerator, and never presents a value
from it as a research fact. Run detail, trades and bracket levels always come
from files (ADR-0017, INV-IDX-1).

## 7. Native broker wall-clock semantics are preserved

Bar keys are int64 seconds in the clock domain
`mt5.justmarkets-demo3.wallclock`. **They are not UTC** and nothing here may
label, "correct" or offset them (ADR-0002).

The rule the UI follows: keep the raw int64, and render civil time by mechanical
decode through **UTC-based getters only** (`getUTCHours()` and friends), which
reproduces the literal encoded wall-clock fields with no offset applied. Any
local-time API (`getHours`, `toLocaleString`, `Intl.DateTimeFormat`, …) would
shift every timestamp by the viewer's own offset and invite a false conclusion
about time of day. `tests/test_time_semantics.py` pins the expected decode and
fails the build if such an API appears in `prototype/app.js`. The clock-domain
identifier is displayed on screen so the assumption is visible rather than
assumed.

## 8. The Explorer is for visual hypothesis generation, not evaluation

It computes no statistic that the run does not already record, makes no
significance claim, and applies no adjusted or deflated metric (ADR-0024). It
may show candles after a trade closed — the researcher is inspecting completed
history, and the Explorer feeds nothing back into a model, policy or fit, so
this is not lookahead in the ADR-0010 sense.

Money is displayed only with its recorded `cost_semantics`. While the cost audit
is open every figure is `unverified` and is labelled as such inline, permanently
(ADR-0020 Amendment 1).

## 9. No interactive TP/SL optimisation, no alternative simulation

TP/SL levels are **presentation of a recorded decision**. Since the Research
Machine gained per-trade brackets (ADR-0023 Amendment 1) there are two ways to
obtain them, and which one was used travels with every trade as
`bracket_source`:

| TradeLog `format_version` | Source | Why |
|---|---|---|
| **2** (current) | `take_profit_price` / `stop_loss_price` read **straight from the trade** | These are the exact levels the simulator compared the bars against. A per-trade bracket cannot be reconstructed from run configuration at all, because there is no single global pair to reconstruct from |
| **1** (legacy) | Reconstructed from the run's one global `ExecutionSpec` | Predates recorded levels. Reconstruction *is* the correct semantics for those runs, and every such trade is labelled `reconstructed_v1` |

The legacy reconstruction mirrors `simulation/bracket.py` exactly:

```
take_profit = entry_price + direction * take_profit_pips * price_units_per_pip
stop_loss   = entry_price - direction * stop_loss_pips  * price_units_per_pip
```

**The viewer never recomputes a level that the artifact records.** A recorded
`NaN` means that side had no bracket and becomes JSON `null`, never a `NaN`
token — strict JSON only, because `JSON.parse` rejects it.

The fixture generator cross-checks the levels it is about to show against the
simulator's own recorded exit prices and refuses to emit if any non-gapped
bracket exit disagrees. For a version-2 log that check is the stronger one: it
validates the simulator's recording rather than the viewer's arithmetic.

An unknown future `format_version` is still refused loudly. There are no
controls to change TP or SL, and there never will be — that would make the
viewer a simulator.

## 9a. Display timeframes are aggregated, never materialized

M1 is the recorded data. M5, M15 and H1 are built in-process from M1 on the same
arithmetic bucket the platform's own provider uses
(`features/reference.py::_arithmetic_bucket_numbers`, phase 0):

```
bucket = floor(key / grid_seconds)
candle_time = bucket * grid_seconds
```

Two rules make this safe:

- **No new view is materialized.** Nothing is written to the generated-views root;
  the aggregation is a cached in-memory projection of an already-authorized frame.
- **Display keeps the final bucket; research does not.** The platform implements
  ID-1 predicate A — a bucket becomes usable only once a later-bucket row has been
  observed — which is a no-lookahead rule for *research*. A researcher inspecting
  completed history wants the last candle, and this viewer feeds nothing back into
  a model, policy or fit. Boundaries are identical; only that rule differs, and
  `tests/test_aggregation.py` pins the two implementations together on real keys.

The grid is plain arithmetic on native broker wall-clock seconds. It is not a UTC
or calendar grid, it has no DST rule, and no session, holiday or closure is ever
claimed (ADR-0002, ADR-0005). A bucket with no observed rows produces no candle —
bars are never invented to bridge a market closure.

## 10. OOS / LOCKBOX are out of scope for V1

The Explorer uses discovery-default (`NORMAL`) access only. It classifies before
authorising and refuses anything else; it never requests a `GatedUnlock` and has
no code path to OOS, LOCKBOX, SNAPSHOT or the raw/curated parents. Widening this
requires separate owner approval.

## 11. E1 is a chart-UX go/no-go gate

E1 is a static, real-data chart prototype with no backend. Its only purpose is to
answer whether TradingView Lightweight Charts feels professional enough on a real
phone for sustained trade investigation. **Backend work (E2+) does not begin
until that test passes.** Rejection criteria and the checklist are in
[PHONE_TESTING.md](PHONE_TESTING.md).

---

## Chart library

**TradingView Lightweight Charts v5.2.1**, Apache-2.0, vendored at
`prototype/vendor/`. The library's attribution logo is left enabled
(`layout.attributionLogo: true`).

TradingView *Advanced Charts* is not used: it is not open source, requires an
application and a separate licence grant, and ships a full trading terminal
whose drawing tools, indicators and saved layouts are dead weight for a
read-only inspector.

If E1 fails on touch quality, the one fallback to try is `klinecharts`
(Apache-2.0) before considering anything custom. **No custom candlestick engine.**
