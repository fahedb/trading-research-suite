# Phase 6 — Zone Trader v13 Research Plugin — Implementation Plan

Date: 2026-08-18 · Revision 1: 2026-08-18 (gate closure — G-1, G-2, G-3
OWNER APPROVED) · Revision 2: 2026-08-19 (implementation and TRAIN H1 record) ·
Status: **COMPLETE AND MERGED INTO `main` AT `aecceef`. SLICES S-1…S-9,
CP-22/CP-23/CP-24, BOUNDED INDEPENDENT REVIEW AND FOLLOW-UPS COMPLETE; FINAL
VERDICT APPROVE; UNFILTERED GATE GREEN; CLOSEOUT/MERGE OWNER APPROVED
2026-08-23.**
Architecture reference: [docs/architecture/V1.md](../architecture/V1.md) ·
Roadmap entry: [docs/roadmap/V1_ROADMAP.md](../roadmap/V1_ROADMAP.md)
(status only) · State and next action:
[PROJECT_STATE.md](../../PROJECT_STATE.md)

**Owner research reset — 2026-08-20.** All prior Zone Trader run artifacts,
sweep execution manifests, caches and disposable indexes were intentionally
deleted by owner request. The historical S-9 narrative and run identifiers
below describe the earlier five-M1-row study but no longer resolve to local run
artifacts. They are not active evidence. The active Plane-A baseline is now
`config/experiments/zone-trader-v13-h20m5-train-v1.json`: one-row decision
delay followed by `horizon_rows = 100` observed M1 rows, representing the
owner-requested look-ahead of 20 nominal M5 candles. Its target is
`log(P[X+101] / P[X+1])`. No result exists yet for this reset baseline.

**Planning record:** 2026-08-18 · **Planning baseline:** `main` @
`abc79477109605855e4228090cd9459ec2136e68` (clean worktree) ·
**Successful H1 run:** `01M0DQCN78TX82R57MJKQBGVX9`, clean implementation
commit `e30c6ffc0184855b7bca20407c2d017d48fcaf0b` ·
**Owner decisions recorded here:** OD-6A, OD-6B, OD-6C, OD-6D (2026-08-18) ·
**G-1 (M5 strategy timeframe), G-2 (finite ATR approximation, `w_atr = 300`
M5 bars) and G-3 (`max_zone_age_rows = 5000` M5 bars) are CLOSED — OWNER
APPROVED 2026-08-18** · **No blocking decision remains** (§8.5)

> **This document is normative for the first Phase-6 Zone Trader work**, in the
> role [PHASE1_DATA_LAYER.md](PHASE1_DATA_LAYER.md),
> [PHASE2_FEATURES.md](PHASE2_FEATURES.md),
> [PHASE3_RESEARCH_RUNS.md](PHASE3_RESEARCH_RUNS.md),
> [PHASE4_SIMULATION.md](PHASE4_SIMULATION.md) and
> [PHASE5_EXPERIMENT_OPS.md](PHASE5_EXPERIMENT_OPS.md) play for their phases.
> Where it and an accepted ADR differ, **the ADR governs**. Where it and chat
> history differ, **this document governs: the Git repository is the source of
> truth.**

> **Governing principle of this phase.** Phase 6 adds *strategy*, not
> *platform*. Zone Trader is one research idea expressed as plugins, config and
> tests. Every component below must either protect research correctness or be
> broadly useful across materially different research families; *"we may need it
> later"* is explicitly insufficient. If a slice cannot be built inside the
> accepted contracts, the correct outcome is **an architecture finding and a
> recommendation to raise an ADR — never a quiet generalisation**
> ([V1_ROADMAP.md](../roadmap/V1_ROADMAP.md) §"Phase 6+").

---

## 0. Contents and coverage map

| § | Content |
|---|---|
| 1 | Status and provenance (including Pine source identity) |
| 2 | Phase objective |
| 3 | Relationship to accepted architecture |
| 4 | Zone Trader semantic overview |
| 5 | Exact decomposition of the strategy |
| 6 | Mapping to FeatureProvider / ResearchModel / DecisionPolicy / Plane A / later Plane B |
| 7 | Owner-approved decisions **OD-6A…OD-6D** and **G-1, G-2, G-3 (CLOSED)** |
| 8 | Resolved gates, the narrow implementation-detail specifications that remain (**ID-1, ID-2**), and the blocker check |
| 9 | Pine semantic quirks that must be preserved |
| 10 | Minimal implementation design |
| 11 | Ordered implementation slices **S-1…S-9** |
| 12 | Test strategy |
| 13 | Leakage and information-reach requirements |
| 14 | Determinism requirements |
| 15 | Research-methodology safeguards |
| 16 | Explicit non-goals |
| 17 | Documentation obligations |
| 18 | Review checkpoints **CP-22…CP-24** |
| 19 | Exact next action |
| 20 | Phase completion criteria |

### Why this file is Zone-Trader-specific rather than `PHASE6_RESEARCH_PLUGINS.md`

Deliberate, and recorded so it is not re-litigated.

1. **Phase 6 is a family of unrelated strategies, not one deliverable.**
   [V1_ROADMAP.md](../roadmap/V1_ROADMAP.md) §"Phase 6+" anticipates *"first
   real features/representations/similarity model/regime model"*, and
   [CLAUDE.md](../../CLAUDE.md) records that the *first planned* research plugin
   is **similarity-based** — a materially different family from Zone Trader. One
   document covering both would fuse two independent research programmes.
2. **Phase plans are already at the limit of a single document.** The five
   existing plans are 78–205 KB each. A single Phase-6 document accreting every
   future strategy would become unreviewable, and unreviewable normative
   documents stop being normative.
3. **Strategy-specific decisions must not look like platform decisions.**
   OD-6A (`tick_volume`) and G-3 (zone age) are *Zone Trader research
   semantics*. Filing them under a generic "research plugins" heading would
   invite a later reader to treat them as platform policy. They are not.
4. **The platform stays strategy-agnostic** ([CLAUDE.md](../../CLAUDE.md)
   Mission). A per-strategy plan file is the documentation shape that matches
   that architecture.

**Convention established here for Phase 6:** one normative plan per strategy
family, named `PHASE6_<STRATEGY>.md`. If a genuinely cross-strategy Phase-6
concern later appears, it gets its own document then — not pre-emptively now.

---

## 1. Status and provenance

### 1.1 Status

| Item | State |
|---|---|
| Phase 6 (Zone Trader) planning | **COMPLETE for the first slice set** (Revision 1, 2026-08-18) |
| Phase 6 (Zone Trader) implementation | **COMPLETE — S-1…S-9 and CP-22…CP-24 GREEN; independent verdict APPROVE; owner closeout/merge approved 2026-08-23** |
| Blocking gates | **NONE.** G-1, G-2, G-3 are CLOSED — OWNER APPROVED (§7) |
| Remaining open items | **None for Phase 6.** ID-1 and ID-2 are implemented and tested. |
| ADR required by current scope | **None.** See §17.3 |
| Production source changed | `features/reference.py`, `models/reference.py`, `decisions/reference.py`; plus two narrow result-neutral platform corrections in `experiments/runs.py` and `experiments/orchestration.py` (§11 S-9 evidence) |
| Tests changed | **Yes — exact goldens, release-blocking rigs, acceptance evidence and preregistration pins** |
| Datasets / view manifests touched | **None** |
| Research experiments run | **Four TRAIN run records: three pre-evaluation infrastructure failures and one COMPLETE evaluated H1 run; no OOS/LOCKBOX** |

The original planning portion remains normative. This Revision 2 adds the
implementation and evidence record without rewriting the approved strategy
semantics.

### 1.2 Pine source identity — provenance, honestly stated

This plan governs the strategy **Zone Trader v13 — Zones + Sweep + VWAP**,
supplied by the owner and analysed in the read-only planning review of
2026-08-18.

**What is independently verifiable from artifacts available on this machine:**

| Property | Value |
|---|---|
| Source located in | the owner's Codex session transcript of 2026-08-17 (`rollout-2026-08-17T14-27-01-01a0101e-5fd7-7ca3-86f6-f1728f152b2c.jsonl`, first user message) |
| Pragma | `//@version=5` |
| Header (line 3) | `Zone Trader  (v13)  -  Zones + Sweep + VWAP` |
| `indicator()` (line 17) | `"Zone Trader v13 - Zones + Sweep + VWAP"`, `shorttitle="ZoneTrader v13"` |
| Body length (`//@version` → EOF) | **392 lines**, no code fences, no trailing prose |
| SHA-256 of that 392-line body | `a61254f935871220d2d5abac631cab5ee24dcf2a0dc05c06be6bdd19e3b2f21c` |

**Owner source confirmation (closed 2026-08-19).** The repository still holds no
copy of the Pine source and therefore cannot independently establish
byte-identity against a second artifact. Before implementation, however, the
owner explicitly confirmed that the analysed Zone Trader v13 is the canonical
source intended for this work and directed that the digest above be treated as
the OD/source confirmation signature. That closes the implementation
obligation without converting owner attestation into an independent claim.

**Consequently:**

- This plan **governs the previously analysed Zone Trader v13 — Zones + Sweep +
  VWAP source**, identified by the properties in the table above.
- Every line-number citation in §5 and §9 is into that 392-line body.
- The source-confirmation obligation is **CLOSED by owner attestation**. If a
  future source differs from this digest, it is a new source/version and its
  semantics must be re-derived before code changes.

**No Pine source is committed to this repository.** It is third-party strategy
content of unestablished licence; the repository records its *semantics* (§5,
§9), which is what implementation requires.

### 1.3 Authorization chain

`PROJECT_STATE.md` recorded, from Phase-5 closeout until 2026-08-18:

> **Exact next action:** await explicit owner direction before beginning any
> Phase-6+ research-plugin planning or implementation.

The owner gave planning direction on **2026-08-18** and implementation
authorization on **2026-08-19**, subsequently confirming the canonical source,
the narrow guard adaptations, the H1 preregistration, the reach burn-in fold
correction, and advance authority for result-neutral narrow fixes. Any large
change capable of affecting the research result still requires a stop and a new
owner decision.

---

## 2. Phase objective

**Reproduce the research signal of Zone Trader v13 faithfully enough to test it,
inside the accepted contracts, without changing the platform.**

Concretely, the phase succeeds when:

1. A registered `FeatureProvider` emits, per observed evaluation row, a Zone
   Trader **event indicator** plus the strategy's descriptive geometry
   (zone bounds, and Pine's displayed entry / stop / target values).
2. That provider's **declared backward reach is honest**, and the existing
   release-blocking leakage rigs enforce it **bitwise**, with **no assertion
   weakened**.
3. A minimal row-local `ResearchModel` and a minimal sparse `DecisionPolicy`
   carry the event into the accepted `ModelOutputFrame` / `IntentFrame` chain.
4. **One pre-registered Plane-A hypothesis** is evaluated on TRAIN and recorded
   in the Trial Ledger — **whatever the answer**.

The phase does **not** aim to produce a profitable backtest, and explicitly does
not aim to reproduce Pine's *execution* (§6.5, §16.4).

### 2.1 What "faithful" means here, and what it does not

Three distinct standards are used throughout this document and must never be
conflated:

| Standard | Meaning |
|---|---|
| **Exact Pine semantics** | Bit-equivalent to what Pine computes, given identical bars |
| **Practical numerical equivalence** | Differs from Pine by an amount argued to be negligible, with the argument stated and measured |
| **Feature-contract legality** | Expressible under ADR-0021's finite declared reach and enforceable by the leakage rigs |

**These three can conflict**, and where they do, the conflict is an owner
decision, not an implementer's judgement call. Two such conflicts arose and
were **resolved by owner decision**: the finite ATR approximation (G-2) and the
5000-M5-bar zone-age bound (G-3), both recorded in §7. Both are explicitly
*practical numerical / bounded-semantic* choices, **not** claims of exact Pine
semantics.

---

## 3. Relationship to accepted architecture

Phase 6 changes **no research, data, feature, model, decision or evaluation
semantic contract**. It consumes them. During the first real run it exposed two
pre-existing composition/capacity mismatches: the run-file ceiling was below a
valid eight-column feature artifact, and the orchestrator admitted a provider
superset at preflight but failed to project it onto a model's exact declared
inputs. The owner pre-authorized narrow result-neutral corrections; §11 records
them explicitly. Neither correction changes a market value, event, score,
outcome, fold or metric. The binding constraints, with their sources:

| Constraint | Source | Consequence for Zone Trader |
|---|---|---|
| Finite, declared, row-based backward reach; **"unbounded or expanding history is not representable"** | [ADR-0021](../adr/ADR-0021-columnar-feature-contract.md) §2 | The central design problem. Drives G-2 and G-3. |
| Exactly two gap policies, no default, no `TRUNCATE`, no `min_observations` | ADR-0021 §3 | Zone Trader uses `CROSS` (§10.3) |
| Warm-up and gap masking applied **by the platform, after** the provider returns | ADR-0021 §4 | Declaration is enforced, not trusted |
| Dense `float64` feature carrier, `NaN` = unavailable, `±inf` refused | ADR-0021 §5 | Fixes the output encoding (§10.4) |
| **No feature-of-feature composition** | ADR-0021 §7, [ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md) | The whole strategy is **one** provider (§10.1) |
| Reserved namespace is exactly `target` and `target.` | ADR-0021 §7 (Slice-2B refinement) | Feature names must avoid it |
| **Row-local inference invariant**, enforced by exact perturbation rigs | [ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §5 | The `ResearchModel` must be a pure per-row map (§6.2) |
| Stateful / online inference **out of V1 scope** | ADR-0022 §6 | Zone state may **not** live in the model. It lives inside one `FeatureProvider.compute()` call with a declared finite reach (§10.3), so the trigger is not met. |
| Generic model output, no trading vocabulary; sparse frames first-class | [ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md) + amendment, ADR-0022 §3 | Enables event-driven intent (§6.3) |
| Targets reach `fit` **only** through the platform channel | [ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) amendment | Zone Trader's model is unsupervised; it takes no targets |
| Fixed stage list; **windowing is not a stage** | ADR-0006 | No aggregation stage, no DAG (§16.1) |
| Native time only; **no session / holiday / closure semantics, ever** | [ADR-0002](../adr/ADR-0002-native-time-model.md), [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md) | Drives the arithmetic grid rule (ID-2, §8.4) and the VWAP deferral (§16.3) |
| `CalendarTimePolicy` exists but implements **no decoding or calendar arithmetic** | `src/trading_research/core/time.py` (`CalendarTimePolicy` docstring) | A civil-time reset cannot be built today |
| Purge derived from declared spans; **feature reach feeds `context_start`, not purge** | [ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 1; `experiments/folds.py` | A large reach costs context rows, not fit-set size (§13.4) |
| Simulator is second, never first; fixed-config or per-opening-instruction TP/SL; fill at next observed row's `open` | [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) Amendment 1 and §§1,2,4 | Plane B can carry per-trade brackets; exact Pine close-fill execution remains unrepresentable (§6.5) |
| Cost semantics **unverified and fail-closed** | [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) Amendment 1, [COST_AUDIT.md](../research/COST_AUDIT.md) | Any future money figure is unverified and may not be quoted |
| Trial denominator counted, never adjusted; **no deflated statistic exists in V1** | [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md), [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §6 | Drives §15 |

### 3.1 Authoring guides

[`docs/guides/ADDING_FEATURES.md`](../guides/ADDING_FEATURES.md) is on this
baseline and its §9 (guard inventories), §10 (required tests) and §12 (agent
procedure, including its *stop-and-raise* list) are **binding procedure** for
Slices S-2…S-4.

The companion guides for the model, representation, policy and outcome
boundaries were authored on the branch `docs/research-plugin-authoring-guides`
and are **not on this baseline**. This plan therefore cites the **architecture
tests themselves** — which are present on this baseline — as the normative
source for the model and policy guard inventories (§11 S-5, S-6). If that
branch merges, the guides become the friendlier reference; the tests remain
normative either way.

---

## 4. Zone Trader semantic overview

Six mechanisms, in the order the Pine script executes them on each bar.

1. **Baselines.** ATR(14) and EMA(200), both Pine recursive; the bar range; the
   upper and lower wick fractions. A session-anchored VWAP is computed.
2. **Sweep detection.** A bar that exceeds the prior 10-bar extreme but closes
   back inside it, with a wick of at least 0.3·ATR, marks a liquidity sweep;
   the most recent sweep in each direction stays "fresh" for 10 further bars.
3. **Anchor bars.** Chart bars accumulate into 30/60/120/240-minute buckets.
   When a bucket closes, its summed volume is compared against the mean of the
   previous up-to-20 completed buckets; exceeding 2.0× marks it abnormal.
4. **Three independent zone families.**
   * **Volume zone** — inside an abnormal bucket, the single chart bar with the
     highest volume contributes its high/low as a zone.
   * **Imbalance / FVG** — a three-bar gap wider than 0.25·ATR becomes a zone
     spanning the gap.
   * **Demand / Supply** — a small-bodied, small-ranged base bar followed by a
     large impulse closing beyond it makes the **base bar's** high/low a zone.

   Zones persist in parallel arrays carrying `top`, `bottom`, `type`, `broken`
   and `inside`. Only the newest 15 are retained; broken zones keep their slot
   until displaced. **Pine imposes no maximum zone age.**
5. **Re-entry, direction, one candidate per bar.** Every bar, zones are scanned
   newest → oldest. A zone whose *close* moves more than 0.5·ATR beyond either
   edge is permanently broken. Otherwise, if the bar's high/low range overlaps
   the zone and did not on the previous bar, a candidate forms. **Direction is
   geometric, not nominal**: a zone whose bottom sits above the *previous* bar's
   close is overhead → SHORT; otherwise → LONG. Candidates are scored (sweep +2,
   VWAP +1) and only one survives the bar.
6. **Confirmation and signal.** A candidate waits up to 6 bars for one of three
   confirmations — immediate touch (unconditional), a rejection wick ≥ 0.35 of
   range, or a close back outside the zone. An optional EMA-200 trend filter is
   checked **at confirmation time**. On success the script displays entry = the
   confirming bar's **close**, a stop 0.5·ATR beyond the far zone edge, and a
   target at 2.0× the resulting risk. Everything after that — labels, lines,
   boxes, the info table, `alert()` and `alertcondition()` — is drawing only.

### 4.1 The fact that shapes this whole plan

In the analysed source, `useSweep` (line 57), `useVwapPriority` (line 64) and
`useTrendFilter` (line 51) are all `input.bool(false, …)`.

**At Pine defaults the sweep layer, the VWAP layer and the EMA-200 trend filter
have zero computational effect.** The default strategy is exactly: three zone
families → re-entry → confirmation mode → signal. Two consequences follow, and
both are load-bearing for §11:

- The candidate score is identically `0.0` for every candidate, so the `>=`
  replacement rule (§9 quirk Q3) resolves to **"the oldest entered zone wins"**.
- ATR is the **only** recursive Pine calculation on the required path. EMA-200
  is not. This is why G-2 was scoped to ATR alone (§7) and why OD-6C is
  coherent rather than merely convenient.

---

## 5. Exact decomposition of the strategy

Line numbers are into the 392-line body identified in §1.2. `S` = holds
persistent state. `F` = reads forward of the current bar.

> **Units, after G-1.** The "chart bars" column below is the **M5 strategy
> axis**, not the M1 source axis. Every reach in this table is in **M5 bars**.
> The conversion to the M1 rows the platform actually counts is §10.3.3, and the
> two axes must never be added together (§10.3.1).

| # | Component | Lines | Inputs | Output | Parameters | Backward reach (**M5 bars**) | S | F | Required in first slice set? |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Candle geometry | 80–82 | o,h,l,c | `rng`, `upWick`, `dnWick` | — | 0 | no | no | **Yes** |
| 2 | ATR(14) | 77 | h,l,c | `atrVal` | 14, `w_atr`=300 | **300** (approved finite approximation, G-2) | no | no | **Yes** |
| 3 | EMA(200) | 78 | c | `ema200` | 200 | **∞** | yes | no | No — OD-6C |
| 4 | VWAP (session) | 88 | hlc3, volume, **session** | `vwapVal` | — | session-anchored, unbounded | yes | no | No — OD-6C, §16.3 |
| 5 | Sweep detection | 100–116 | h,l,o,c,ATR | `sweepUpFresh`, `sweepDnFresh` | 10, 0.3, 10 | `swingLen + sweepValidBars` | bounded | no | No — OD-6C |
| 6 | Anchor construction | 125–141 | native key, `tick_volume` | `prevAnchorVol`, `prevAnchorBars`, `anchorClosed` | `anchor_grid_seconds`=3600 → **12 M5 bars** | ≤ 12 | bounded | no | **Yes — phase fixed by ID-2** |
| 7 | Abnormal anchor volume | 143–151 | anchor volumes | `anchorSignal` | `volLen`=20, `volMult`=2.0 | `(volLen+1) × 12` = **252** | bounded ring | no | **Yes** |
| 8 | Volume-zone construction | 176–189 | `tick_volume`, h, l over prior anchor | zone `(top, bottom, type 0)` | — | ≤ 12 | no | no | **Yes** |
| 9 | FVG construction | 195–205 | h, l, ATR | zone `(type 1)` | `minFvgATR`=0.25 | 2 | no | no | **Yes** |
| 10 | D/S base detection | 211–213 | o,h,l,c[1], ATR | `isBase` | `maxBaseBody`=0.5, `maxBaseATR`=0.8 | 1 | no | no | **Yes** |
| 11 | D/S impulse detection | 214–216 | o,h,l,c,h[1],l[1],ATR | `isImpulseUp/Dn` | `minMoveATR`=1.2 | 1 | no | no | **Yes** |
| 12 | Zone creation | 164–224 | components 8–11 | array push | 3 family flags | max of 8/9/10/11 | writes S | no | **Yes** |
| 13 | **Zone persistence** | 157–162, 227–238 | creation events | alive set | `maxZones`=15, `max_zone_age_rows`=**5000** | **5000** (approved finite bound, G-3) | **yes** | no | **Yes** |
| 14 | Zone break | 274–279 | close, ATR, zone edges | `zBroken[i]` | `breakBuf`=0.5 | zone age | yes | no | **Yes** |
| 15 | Enter / leave / re-enter | 271, 281, 306 | h, l vs zone | `nowIn`, `wasIn` | — | zone age | yes | no | **Yes** |
| 16 | Candidate direction | 282 | `zBot` vs `close[1]` | `isShortCand` | — | 1 + zone age | no | no | **Yes** |
| 17 | Candidate scoring | 286–290 | sweep / VWAP flags | `scoreS` | — | — | no | no | **Degenerate — see §4.1** |
| 18 | Sweep gating | 293, 295 | `sweepOk` | veto | `useSweep` | — | no | no | No — OD-6C |
| 19 | VWAP priority | 290 | close vs VWAP | +1.0 score | `useVwapPriority` | — | no | no | No — OD-6C |
| 20 | Pending-candidate state | 244–249, 308–317 | candidate | `pendState` + geometry | `maxBarsWait`=6 | ≤ `maxBarsWait` | **yes, bounded** | no | **Yes** |
| 21 | Confirmation modes | 319–323 | wicks, close, zone | `confirmed` | mode, `minWick`=0.35 | ≤ 6 + zone age | no | no | **Yes** |
| 22 | Trend filter | 325 | close vs EMA-200 | `trendOk` | `useTrendFilter` | EMA reach | no | no | No — OD-6C |
| 23 | Signal generation | 327–342 | components 20–22 | `longSignal` / `shortSignal` | — | full | resets | no | **Yes** |
| 24 | Displayed entry / stop / target | 328–331 | close, zone, ATR | three floats | `slBufferATR`=0.5, `rrTarget`=2.0 | full | no | **no** | **Yes — descriptive only** |
| 25 | Plotting, labels, boxes, lines, alerts, table | 89, 118–119, 165–167, 189, 202, 205, 221, 224, 348–392 | — | drawings | display flags | — | — | — | **No — research-irrelevant** |
| **26** | **M1 → M5 aggregation** (**not in Pine — introduced by G-1**) | — | M1 `o,h,l,c,tick_volume` + native key | the M5 strategy axis | `bar_grid_seconds`=300, `bar_phase_offset_seconds` | ≤ 5 M1 rows per bar | no | **no** | **Yes** |

### 5.1 The display layer is research-irrelevant — verified, not assumed

Nothing in the drawing layer feeds back into any computed value:

- `box.new` / `box.set_bgcolor` / `box.set_border_color` / `box.delete`
  (165, 232, 278–279, 304–305): the handle is stored in `zBox` and read back
  **only** to recolour or delete. `zBox` never influences a value.
- `label.new`, `line.new`, `plot`, `plotshape`, `alert()`, `alertcondition()`,
  `table.*` are terminal sinks.
- `typeName` (351) is re-derived from the **signal direction**, not from the
  stored `zType` — pure display.
- `sweepTag` / `vwapTag` (353–354) are commented in the source as information
  only, not conditions.
- `max_boxes_count=500` may cause TradingView to drop old boxes; the code guards
  with `not na(bxx)`. No computational effect.

**Implement none of it.**

### 5.2 `barstate.isconfirmed` is a historical no-op

It appears at lines 110, 112, 151, 199, 218, 264 and 316. On historical bars it
is always true. **The reproduction treats every observed row as confirmed.**
There is no repainting concern and no partial-bar semantics to model.

---

## 6. Mapping to platform layers

```
market columns (open, high, low, close, tick_volume)
        |
        v
+---------------------------------------------------------------+
| FeatureProvider  "zone_trader_v13"                             |  <- components 1-24
|   ADR-0021: finite declared max_prior_rows, explicit GapPolicy |
|   one class, constructor carries every strategy parameter      |
|   emits dense float64 columns on the evaluation axis           |
+---------------------------------------------------------------+
        |  zt_event_signed, zt_entry_price, zt_stop_price,
        |  zt_target_price, zt_zone_top, zt_zone_bottom,
        |  zt_zone_kind, zt_candidate_open
        v
   RepresentationModel  --  NOT USED (no fitted preprocessing exists here)
        |
        v
+---------------------------------------------------------------+
| ResearchModel  "zone_event_gate_v1"                            |
|   no-op fit -> empty FittedPayload; strictly row-local infer   |
|   maps 0.0 -> NaN and +/-1.0 -> +/-1.0 into generic "score"    |
|   MUST have no __init__ and params == {}   (guard, see 6.2)    |
+---------------------------------------------------------------+
        |  ModelOutputFrame(score)   <- generic, no trading vocabulary
        +-------------------> Plane A: rank_ic_v1 / sign_accuracy_v1
        |                             vs forward_log_return_v1
        v
+---------------------------------------------------------------+
| DecisionPolicy  "event_exposure_v1"                            |
|   emits a SPARSE IntentFrame: +/-1.0 only at event rows        |
+---------------------------------------------------------------+
        |  IntentFrame(target_exposure_intent_v1)
        v
   bracket_single_position_v1  ->  Plane B   [DEFERRED - OD-6D, 6.5]
```

### 6.1 Why the entire strategy is one `FeatureProvider`

ADR-0021 §7: a provider *"may not consume another provider's output. Feature-of-
feature composition would be the dependency-graph engine
[ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md) exists to refuse."*

Zone Trader is a strict chain: ATR → FVG sizing / base-and-impulse thresholds →
zone creation → zone persistence → candidate → confirmation → signal. That chain
is **legal inside one provider** and **illegal across providers**. Splitting it
would require the DAG engine ADR-0006 forbids. **One provider is therefore not a
convenience — it is the only legal shape.**

### 6.2 `ResearchModel` — minimal, row-local, zero-parameter

`zone_event_gate_v1` maps the provider's event column to the generic output
column `score`: `0.0 → NaN`, `±1.0 → ±1.0`. It is trivially row-local, so
ADR-0022 §5's forward and row-locality rigs pass by construction, and it takes no
`training_targets`.

Two guards constrain how it is written, both verified on this baseline:

- `tests/test_architecture.py` asserts that **every class in
  `SLICE_3C_MODEL_CLASSES` has no `__init__`**. A registered model must be
  zero-argument with `params = {}`. **All Zone Trader parameters therefore
  belong to the FeatureProvider** — which is also where they belong
  semantically, since that is where they change numbers.
- `test_only_decisions_and_core_name_a_trading_direction` asserts the literal
  string `"Direction"` appears nowhere in `features/`, `models/`,
  `representations/`, `outcomes/` or `data/` — **including comments and
  docstrings**. Separately, `core/research_frames.py` refuses a model-output
  column named (case-insensitively) `action`, `buy`, `direction`, `position`,
  `sell`, `side` or `signal`.

**Do not relax either guard.** `CLAUDE.md` treats widening an architecture test
as an architecture change.

### 6.3 `DecisionPolicy` — minimal sparse event → exposure

`event_exposure_v1` emits a **sparse** `IntentFrame` carrying `±1.0` only at rows
where the score is finite and non-zero. ADR-0009's sparsity amendment makes this
first-class — *"a model that acts on 400 events must not be forced to emit 2.3 M
abstention rows"* — and `core/research_frames.py`'s intent minting already
accepts a strict key subset.

**The trichotomy is load-bearing and must be preserved exactly**: an absent row
means *no statement*; `NaN` means *explicit abstain, holder keeps prior state*;
`0.0` means *explicitly target flat*. Zone Trader emits **absent** at non-event
rows — never `0.0`, which would flatten a position immediately after entry.

Decision policies **may** have a constructor (`ThresholdPolicy` does), so
`event_exposure_v1` may be parameterised if it needs to be.

### 6.4 Plane A

`ModelOutputFrame(score)` → `sign_accuracy_v1` and `rank_ic_v1` against
`forward_log_return_v1`. Verified property of the existing evaluator:
`SignAccuracy` excludes rows where the output is non-finite **or exactly `0.0`**,
so `effective_samples` becomes the **event count** automatically. No new
evaluator is required for the first hypothesis.

### 6.5 Plane B — deferred, and never presented as Pine's execution

Three verified mismatches between Pine's displayed levels and
`bracket_single_position_v1`:

| # | Pine | Platform | Status |
|---|---|---|---|
| 1 | `entry = close` of the confirming bar (line 328) | ADR-0023 §4: intent at row `T` fills at row `T+1`'s **`open`** | **Not bridgeable — and correctly so** |
| 2 | Per-signal `stop = zoneEdge ± 0.5·ATR`; `target = entry ± 2.0·risk` (329–331) | ADR-0023 Amendment 1 accepts `bracketed_exposure_intent_v1`, whose opening instruction carries independent TP/SL distances in price units | **Representable as per-trade distances around the actual next-open fill; not as Pine's absolute close-based levels** |
| 3 | Each signal is a discrete trade | Intent is a **target state**; a second `+1.0` while already long is a no-op (ADR-0023 §6) | Recordable deviation |

On (1): `RESEARCH_POLICY.md` §5 lists *"evaluating a decision at bar t using bar
t's own close as the fill price when you could not have acted until t+1"* as a
classic leak. **Reproducing Pine's displayed entry as a fill price would be a
leakage claim the platform refuses.** Pine's entry/stop/target remain fully
legitimate as **descriptive feature columns** — they are computed from data at or
before the signal bar — and illegitimate as execution prices.

On (2): the original Phase-4 body of ADR-0023 named per-decision brackets as a
reconsideration trigger. **That trigger fired and was discharged by ADR-0023
Amendment 1.** The platform now transports independent per-trade TP/SL
distances. This does not reproduce Pine's close-based absolute levels: the
platform still fills only at the next observed row's `open`, and the resolved
distances are bound around that actual fill.

**Three things that must never be blurred, in any document, message or chart:**

| | What it is | Status |
|---|---|---|
| **(a) Research-signal reproduction** | The event, its direction, and Pine's entry/stop/target as descriptive numbers | **Available. No contract change.** This phase's objective. |
| **(b) Existing-simulator evaluation** | Sparse exposure intent with either fixed-config or per-trade TP/SL distances → next-open fill | Available. Fixed brackets remain a different strategy; per-trade transport is qualified in §18.1. Neither changes next-open execution. |
| **(c) Exact Pine execution reproduction** | Same-bar close fill plus Pine's absolute per-signal levels | **Not representable in V1.** (b) ≠ (c). Close-fill is refused by design. |

---

## 7. Owner-approved decisions

### OD-6A — Volume semantic — **OWNER APPROVED 2026-08-18**

**Pine `volume` → MT5 `tick_volume`.** For this research implementation, the
provider reads `tick_volume`.

This is an explicit **strategy/research** decision, not a platform inference.
The repository asserts nothing about the meaning of either volume column: the
only recorded fact is [COST_AUDIT.md](../research/COST_AUDIT.md) row **E2** —
*"`open`, `high`, `low`, `close` are `float64`; `tick_volume` and `real_volume`
are `int64`"* — which is a **dtype** fact. `PHASE1_DATA_LAYER.md` confirms both
columns are preserved *"on exactly the same footing"* as OHLC, i.e. the platform
deliberately claims nothing.

Consequences, binding:

- **`real_volume` is not an alternative in this plan** and must not appear as a
  configurable option, a fallback, or a "try the other one" branch.
- The provider declares `tick_volume` in `required_columns` **directly**. There
  is no `volume_column` selection parameter, because there is no selection.
- **Changing this later is a strategy-semantic change** requiring its own owner
  decision and a new provider `version` — never an automatic fallback.
- **The Slice-0 volume-selection exercise proposed in the 2026-08-18 planning
  review is cancelled.** It existed to choose between the two columns; the
  choice is made.

A **preflight sanity check** that `tick_volume` is usable — non-negative, not
identically constant over a TRAIN sample — may be included in Slice S-2's test
work **if it earns its place as a test**. It is a data-usability assertion, not
a selection exercise, and must not be written or reported as one.

### OD-6B — Minimalism — **OWNER APPROVED 2026-08-18**

**Do not build speculative infrastructure for Zone Trader.** The following are
**prohibited** in this phase:

- generic `MultiTimeframeEngine`;
- platform `ZoneManager` service;
- generic event bus;
- streaming engine;
- generic online-state framework;
- generic session / calendar framework;
- new persistent market-data format;
- new database infrastructure;
- generalized order engine;
- **Numba / Cython or any new dependency**;
- generic DAG or feature-of-feature execution layer.

**If an implementation slice proves one is genuinely necessary, STOP and raise an
architecture finding.** Do not add it quietly, and do not approximate it with a
"small helper" that grows. This mirrors `ADDING_FEATURES.md` §12 step 12 and
`V1_ROADMAP.md` §"Phase 6+".

### OD-6C — Initial optional-layer scope — **OWNER APPROVED 2026-08-18**

The first implementation and research iteration runs at the Pine defaults:

```
useSweep        = false
useVwapPriority = false
useTrendFilter  = false
```

Therefore, in the first slice set:

- **VWAP is not implemented.** (It is additionally not representable — §16.3.)
- **EMA-200 trend filtering need not be implemented.**
- **Sweep detection need not be implemented.**

These remain **explicitly deferred Zone Trader capabilities** (§16.6) — each
addable later as a small reviewed strategy-plugin extension if research requires
it. They are **not** deferred platform abstractions, and nothing may be built now
to "prepare" for them.

Two consequences are recorded because later slices depend on them:

1. The candidate score is identically `0.0`, so the `>=` tie rule resolves to
   **"oldest entered zone wins"** (§9 Q3). That behaviour must still be
   implemented and tested exactly, because it is observable.
2. **ATR is the only recursive Pine calculation on the required path.** G-2 is
   therefore scoped to ATR, and EMA resolution does **not** block this phase.

### OD-6D — Plane A before Plane B — **OWNER APPROVED 2026-08-18**

**Do not begin with TP/SL optimization or simulator-driven edge discovery.**

The initial objective is **research-signal reproduction and Plane-A
evaluation**. Plane B is deferred until a Zone-return event demonstrates
evidence of predictive value.

This restates the binding rules in ADR-0023 §1 and
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7 — *"The simulator is a
cost-and-execution filter applied to an edge that Plane A already found. It is
not an edge-discovery mechanism."* Zone Trader ships with `slBufferATR` and
`rrTarget` already in it, which makes it exactly the case those rules exist for.

**Existing simulator semantics must never be presented as an exact reproduction
of Pine's displayed entry/stop/target behaviour** (§6.5).

---

### G-1 — Strategy timeframe is **M5** — **CLOSED · OWNER APPROVED 2026-08-18**

**Decision.** Reproduce Zone Trader as originally intended, on a **5-minute
chart**. The source market data remains **M1**, so the implementation performs
deterministic **M1 → M5 aggregation** before applying any Zone Trader bar logic.

This closes the question §8.1 previously held open. It was decided on the stated
criterion — *the purpose of the study is to reproduce the author's intended
strategy* — not on the convenience of the source resolution.

**Aggregation semantics — normative.** For each **complete** M5 bar:

| Field | Value |
|---|---|
| `open` | `open` of the **first** constituent M1 bar |
| `high` | **maximum** `high` of the constituent M1 bars |
| `low` | **minimum** `low` of the constituent M1 bars |
| `close` | `close` of the **final** constituent M1 bar |
| `tick_volume` | **sum** of the constituent M1 `tick_volume` |

"Constituent" means: the observed M1 rows whose native key falls in that M5
bucket. A bucket with no observed M1 row produces **no M5 bar** — it is absent
from the strategy axis, not an empty bar. A bucket with one observed row
produces a one-row M5 bar. **No row is synthesised, and no value is
interpolated.**

**The strategy axis is M5.** Every bar-counted Zone Trader parameter is
therefore measured in **M5 strategy bars**, not M1 source rows:

| Parameter | Unit after G-1 |
|---|---|
| `ATR(14)` and its window `w_atr` | M5 bars |
| FVG `[2]` relationships | M5 bars |
| Demand/Supply previous-bar relationships (`high[1]`, `low[1]`, `close[1]`, `open[1]`) | M5 bars |
| `maxBarsWait = 6` | M5 bars |
| `max_zone_age_rows = 5000` | M5 bars |
| Sweep `swingLen`, `sweepValidBars` (deferred layer) | M5 bars |
| Candidate direction reference `close[1]` | M5 bars |

**The H1 anchor therefore spans 12 complete M5 bars** (3600 s / 300 s), instead
of the 60 M1 bars it would have spanned under native-M1 execution. With
`volLen = 20`, the anchor history is at most `(20 + 1) × 12 = 252` M5 bars.

**Leakage requirement — normative and non-negotiable.**

> An M5 bar must not expose its final `open`/`high`/`low`/`close`/`tick_volume`
> until **all** constituent M1 observations required for that bar are available.
> **No partial M5 bar may be treated as a completed strategy bar.**

Consequences that follow, and must be implemented as stated:

1. At any M1 evaluation row, the most recent **provably complete** M5 bar is the
   one immediately preceding the bucket the evaluation row sits in. The exact
   completion predicate is a narrow implementation detail — see **ID-1** (§8.3).
2. The `FeatureFrame` remains **dense on the M1 evaluation axis** (ADR-0021 §5).
   M5-derived values appear as a **step function** that changes only where a new
   M5 bar becomes complete.
3. A Zone Trader event is emitted at **exactly one** M1 row per M5 signal — the
   first M1 row at which the confirming M5 bar is provably complete —
   and `zt_event_signed` is `0.0` at every other M1 row. This keeps the
   downstream sparse `IntentFrame` one-intent-per-signal.
4. `forward_log_return_v1`'s horizon is counted in **observed M1 rows**
   (ADR-0022 §9). An M5-based strategy evaluated at horizon `N` is measured over
   `N` **M1** rows. **State the horizon in M1 rows explicitly in the config and
   beside every reported result.** Do not describe it in M5 bars.

**Time semantics.** M5 buckets are defined by **integer arithmetic on the native
broker-wall-clock key**: a boundary occurs where
`floor((key − bar_phase_offset_seconds) / bar_grid_seconds)` changes, with
`bar_grid_seconds = 300`. **No UTC, exchange-session, New York-session or
broker-session claim is made or may be invented** (ADR-0002, ADR-0005), and no
document may state that this bucketing reproduces TradingView's M5 grid, whose
phase derives from an exchange session calendar this repository cannot observe.
The exact phase value is **ID-2** (§8.4).

**No generic MultiTimeframeEngine.** The M1→M5 aggregation is a narrow Zone
Trader requirement implemented **inside the one `FeatureProvider`**, under
OD-6B and ADR-0006. There is no resampling stage, no aggregation layer and no
reusable timeframe abstraction.

### G-2 — Finite ATR approximation, `w_atr = 300` M5 bars — **CLOSED · OWNER APPROVED 2026-08-18**

**Decision.** The numerical difference from TradingView's recursive ATR is
small enough that it does not justify expanding the platform architecture. The
first Zone Trader implementation uses the **approved finite, exactly bounded
approximation** to Pine's recursive Wilder/RMA ATR, with

```
w_atr = 300 M5 bars
```

**Definition — exact and finite.** `ATR_W(b)` at M5 bar `b` is a fixed-length
weighted sum of the `w_atr` true-range values at bars `b − w_atr + 1 … b`, with
Wilder geometric weights `w_k = (1/14)(13/14)^k` and the residual tail mass
assigned to the oldest term so the weights sum to one. `TR(b)` uses
`high(b)`, `low(b)` and `close(b−1)`, so the ATR at bar `b` reaches back
**`w_atr = 300` M5 bars**. It is computed as a correlation over a fixed window,
never as a recursion carrying state across the window boundary — a recursion
would make the value depend on every earlier row and could not satisfy a finite
declared reach.

**Required wording — binding on every document, comment, docstring and report.**

- **Do not claim it is mathematically identical to `ta.atr(14)`.** It is not,
  and for any finite `w_atr` it cannot be.
- Describe it as **"the approved finite approximation to Wilder/RMA ATR(14) used
  by this research implementation"**.
- `w_atr = 300` is an **identity-bearing semantic/numerical parameter**: it
  lives in the provider `params`, enters the stage identity, and changing it
  changes the artifact identity.
- Its declared information reach must be **honest and enforced by the existing
  `FeatureProvider` leakage machinery**.
- **Do not weaken `max_prior_rows`.** **Do not weaken, skip or tolerance the
  leakage rigs.**

**Verification is still required — as an acceptance test, not as a gate.**

The implementation must compare the approved ATR against a **long-history
recursive Wilder/RMA reference** (computed for comparison only; never
registered as a provider) and measure at minimum:

1. maximum numerical ATR difference;
2. a representative distribution / summary of the numerical differences;
3. **FVG classification differences** (`fvgSizeOk` flips);
4. **Demand/Supply classification differences** (`isBase`, `isImpulseUp/Dn`
   flips);
5. **zone-break differences** (`close > t + buf` / `close < b − buf` flips);
6. **confirmation differences**;
7. **final Zone Trader event-row differences** — added, removed, moved.

> **This is an acceptance test of an already-approved implementation choice. It
> is NOT permission to reopen G-2 or to search for an alternative ATR after
> seeing the results.** If the observed divergence is unexpectedly material,
> **implementation must STOP and report the evidence** — with the measured
> numbers — rather than silently changing `w_atr`, adjusting weights, or
> substituting a different estimator. Changing `w_atr` after seeing results
> would be parameter selection on observed outcomes (§15.3).

**EMA-200 remains outside the first required implementation path** under OD-6C,
so no equivalent EMA question arises and none may be introduced.

### G-3 — Finite maximum zone age, `max_zone_age_rows = 5000` M5 bars — **CLOSED · OWNER APPROVED 2026-08-18**

**Decision.** The finite-age solution is approved:

```
max_zone_age_rows = 5000   # M5 STRATEGY bars, not M1 source rows
```

**Because G-1 made the strategy axis M5, `5000` means 5000 M5 strategy bars.**
It is **not** 5000 source M1 rows. Any document, comment or config that states
it in M1 rows is wrong.

**Boundary semantics — exact, and preserved from the earlier plan.**
Let `age = current_M5_bar_index − creation_M5_bar_index`.

| `age` | Zone eligibility |
|---|---|
| `≤ 4999` | eligible |
| **exactly `5000`** | **still eligible** |
| **`5001`** | **expired — removed from eligibility** |

A zone that has expired is no longer eligible to break, to be entered, to
produce a candidate, or to contribute to any output. Expiry is evaluated on the
strategy (M5) axis, in the same per-bar order as Pine's count eviction, and
**before** the newest→oldest candidate scan.

**`max_zone_age_rows` is identity-bearing.** It lives in the provider `params`
and enters the stage identity.

**Prohibitions, binding:**

- **Do not sweep or optimize it.** It is a strategy semantic, not a tuning knob;
  sweeping it would be parameter mining over exactly the deviation under review
  (§15.3).
- **Do not treat it as a platform-wide Zone concept.** There is no platform
  notion of a zone, and none may be introduced (OD-6B).
- It is a **Zone Trader strategy semantic**, introduced solely to give the
  implementation a finite, honest information reach under ADR-0021 §2.

**Documentation honesty — this is a deliberate, bounded deviation from Pine.**

Recorded plainly, and not to be softened:

- **Pine limits zones by count (`maxZones = 15`) and has no explicit age limit.**
  A zone created thousands of bars earlier remains eligible in Pine until it is
  broken or displaced by fifteen newer zones.
- **This research implementation additionally imposes a maximum age of 5000 M5
  bars.** A zone that Pine would still consider valid at age 5001 is expired
  here, and every signal it would have produced is absent.
- **The deviation is owner-approved**, for the stated reason: it is what allows
  the provider to declare a finite backward reach that ADR-0021 §2 requires and
  that the release-blocking rigs can enforce bitwise.
- **No byte-for-byte or semantic identity with Pine may be claimed for zones
  older than this bound**, in any document, report or commit message. Where a
  differential comparison against TradingView is run (§12.5), zone-age
  truncation is one of its enumerated, legitimate sources of divergence.

---

## 8. Resolved gates, remaining specifications, and the blocker check

### 8.1 Gate status — all three CLOSED

| Gate | Status | Recorded in |
|---|---|---|
| **G-1** — chart timeframe | **CLOSED · OWNER APPROVED 2026-08-18** — M5 strategy axis, deterministic M1→M5 aggregation | §7 |
| **G-2** — ATR semantics | **CLOSED · OWNER APPROVED 2026-08-18** — approved finite approximation, `w_atr = 300` M5 bars | §7 |
| **G-3** — zone maximum age | **CLOSED · OWNER APPROVED 2026-08-18** — `max_zone_age_rows = 5000` M5 bars | §7 |

No gate blocked implementation. Revision 2 records the implemented ID-1/ID-2
choices below and the completed G-2 acceptance obligation in S-9.

### 8.2 OPEN-4 — anchor-grid semantics — **subsumed into ID-2**

The earlier OPEN-4 obligation (document the exact arithmetic anchor bucketing
and phase; invent no session semantics) is unchanged in substance and is now
carried by **ID-2** together with the M5 grid, because G-1 introduced a second
grid that must stay consistent with it. The anchor default remains **H1 /
3600 s = 12 complete M5 bars**.

### 8.3 ID-1 — M5 completion predicate — **FIXED: predicate A; implemented**

**Not a gate and not an owner decision.** G-1's leakage requirement is settled;
what remains is the exact predicate by which the implementation decides that a
bucket is complete. Two candidates, both leak-free:

| | Predicate | Behaviour |
|---|---|---|
| **A (recommended)** | A bucket is complete once an M1 row in a **later** bucket is observed | Never silently drops a bucket. Costs at most one M1 row of latency versus B. |
| **B** | A bucket is complete at the M1 row whose key is the bucket's **final grid minute** | Fires one M1 row earlier and matches TradingView's bar-close instant more closely, but **silently drops any bucket whose final minute is missing** — and gaps are ordinary in this dataset. |

**Implemented choice: A**, because robustness to gaps matters more than one M1
row of latency, and because a silently dropped bucket is an invisible strategy
change. The aggregation fixture pins that a bucket becomes usable only after a
row in a later bucket has already been observed. Predicate B is not implemented.

The implemented predicate reads only the current row's key and earlier rows; it
never asks whether a future row exists. This was checked directly because the
value-column leakage rigs cannot enforce key-axis causality (§13.5).

### 8.4 ID-2 — exact grid phases — **FIXED: both phase offsets 0; implemented**

Four identity-bearing provider parameters, explicit in the resolved config. The
zero-argument provider uses the approved phase defaults `0`:

```
bar_grid_seconds              = 300     # M5 bucketing
bar_phase_offset_seconds      = 0
anchor_grid_seconds           = 3600    # H1 anchor
anchor_phase_offset_seconds   = 0
```

**Consistency constraint, and it is load-bearing:** an anchor boundary must
always coincide with an M5 boundary, or the anchor would contain a partial M5
bar. Therefore

```
anchor_grid_seconds % bar_grid_seconds == 0
(anchor_phase_offset_seconds − bar_phase_offset_seconds) % bar_grid_seconds == 0
```

Both must be **validated in the provider constructor and refused otherwise**,
not assumed.

**No session claim.** These are integer parameters on the native broker
wall-clock axis. The native key is broker wall-clock encoded as epoch, so
`floor(key / 300)` yields broker-local five-minute buckets — an arithmetic fact
and nothing more. **No document may state that any phase choice reproduces
TradingView's grid.** Divergence risk is not uniform: a 300 s and a 3600 s grid
both align to whole broker-local minutes and hours and are low-risk; longer
anchors (7200 s, 14400 s) would be materially higher-risk because TradingView
typically phases longer bars from a session open this repository cannot observe.
Record that wherever an anchor other than 1800 s or 3600 s is used.

### 8.5 Blocker check — explicit

Assessed against the four categories the owner asked to be distinguished:

| Category | Present? | Detail |
|---|---|---|
| **Owner decision outstanding** | **No** | OD-6A…OD-6D and G-1/G-2/G-3 are all approved. ID-1 and ID-2 are implementation specifications a reviewer checks, not decisions a researcher makes. |
| **Architecture blocker** | **No** | Every element of the approved design fits accepted contracts: one provider (ADR-0021 §7); finite declared reach, now derived concretely in §10.3 (ADR-0021 §2); `CROSS` gap policy (§10.3); dense `float64` output (ADR-0021 §5); row-local model (ADR-0022 §5); sparse intent (ADR-0009 amendment); arithmetic-only time (ADR-0002, ADR-0005). **No accepted contract is contradicted, and no ADR is required** (§17.3). |
| **Implementation detail** | **Complete** — ID-1, ID-2 | Predicate A and both phase offsets `0` are implemented, pinned by fixtures and recorded above. |
| **Acceptance-test obligation** | **Complete** — G-2's verification (§7, §12.3) | The TRAIN evidence is recorded in S-9. It did not reopen or tune G-2. |

**Statement, plainly: no real blocker remains.** The two deferred-by-design
items — VWAP session capability (§16.3) and exact Pine execution (§16.4) — are
outside the approved scope by OD-6C and OD-6D respectively, so neither blocks
anything in the required path.

One consequence of the closed gates is worth flagging as a **meaningful risk
rather than a blocker**: the derived reach is **26,534 M1 source rows** (§10.3),
which is far beyond what the current leakage-rig harness can host (its synthetic
axis is 128 rows and it asserts `reach < 64`). **Slice S-1 is therefore no
longer conditional — it is required**, and it must enlarge the harness without
weakening a single assertion (§11 S-1).


## 9. Pine semantic quirks that must be preserved

These are **observable strategy behaviours**, verified against the analysed
source. **Do not "clean up" any of them without owner approval.** Where a quirk
belongs to a deferred layer (OD-6C), it is marked and must be preserved *if and
when* that layer is implemented.

| Q | Quirk | Evidence | Applies now? |
|---|---|---|---|
| Q1 | **Traversal is newest → oldest.** Pine v5 counts **down** when `from > to`. | 265 | Yes |
| Q2 | **Replacement uses `>=`**, and `candScore` starts at `-1.0`, reset every bar. | 262, 295 | Yes |
| Q3 | **The oldest candidate wins an equal-score tie** — Q1 + Q2 together: later-evaluated (older) zones overwrite. With OD-6C every score is `0.0`, so **the oldest entered zone always wins**. | 262, 265, 295 | **Yes — and degenerate, so it must be tested explicitly** |
| Q4 | **Same-bar candidate + confirmation.** The pending block runs in the same bar as candidate creation; `pendBars` goes `0 → 1` immediately. In "immediate touch" mode `touchOk` is literally `true`, so the signal fires on the touch bar. | 308–323 | Yes |
| Q5 | **A zone cannot fire on its own creation bar**, because `f_addZone` pushes `zInside = true`, so `wasIn` is true and `nowIn and not wasIn` cannot hold. Price must leave and return. | 172, 281 | Yes |
| Q6 | **Touch uses OHLC overlap, not close**: `nowIn = low <= t and high >= b`. | 271 | Yes |
| Q7 | **Break uses close beyond an ATR buffer**: `close > t + buf or close < b - buf`, `buf = atrVal * breakBuf`, using the **current** bar's ATR. A wick-only penetration does **not** break a zone. | 272, 274 | Yes |
| Q8 | **A breaking zone cannot also become a candidate on that bar** — `if break … else if not broken …`. | 274, 280 | Yes |
| Q9 | **A surviving zone may be touched repeatedly** after leaving and re-entering; `zInside` is updated every bar. | 306 | Yes |
| Q10 | **A pending candidate is overwritten by a new winning candidate**, and `pendBars` resets to `0`. Pending state is never queued. | 308–314 | Yes |
| Q11 | **Pending state stores geometry, not a zone reference.** A candidate confirms even if its zone is subsequently broken or evicted. | 244–249 vs 316–342 | Yes |
| Q12 | **Later confirmation need not still overlap the zone.** `touchOk = true` unconditionally; a confirming bar may have left the zone entirely. | 321 | Yes |
| Q13 | **Confirmation timeout includes the touch bar, and the final permitted bar still gets a confirmation chance** — expiry (`pendBars >= maxBarsWait`) is checked **after** the confirmation test. With `maxBarsWait = 6`: the touch bar plus five more. | 316–342 | Yes |
| Q14 | **Trend-filter failure keeps the pending candidate alive** — control falls through to the expiry test, so the candidate retries next bar. | 325, 327, 341 | **Deferred layer** — preserve when implemented |
| Q15 | **Demand/Supply and FVG semantic labels do not determine LONG/SHORT.** Both demand and supply push `zType = 2` with the *same* geometry `(high[1], low[1])`; the distinction is **never stored**. Direction comes only from `isShortCand = b > close[1]`. `typeName` (351) is re-derived from the signal for display. | 220–223, 282, 351 | Yes |
| Q16 | **The anchor average excludes the anchor being tested** — `anchorAvg` is computed at line 144 *before* the just-closed anchor is pushed at 147. | 144 vs 146–149 | Yes |
| Q17 | **Volume-zone max-volume tie chooses the most recent sub-bar** — strict `>` with traversal `i = 1 → n` (most recent → oldest). | 181–186 | Yes |
| Q18 | **`rng == 0` produces wick fractions `0.0`, not `NaN`.** | 81–82 | Yes |
| Q19 | **The Demand/Supply base range is checked against the *current* bar's ATR**, not the base bar's. | 213 | Yes |
| Q20 | **The volume-zone scan retains the 500-bar cap** (`math.min(prevAnchorBars, 500)`) even though it is non-binding for any anchor ≤ 240 minutes on M1 or M5. Reproduce it. | 177 | Yes |
| Q21 | **Abnormal-volume test is strict `>`**: `prevAnchorVol > anchorAvg * volMult`. Equality does **not** fire. | 151 | Yes |
| Q22 | **`maxZones` eviction removes exactly `initialSize − maxZones` elements.** Pine v5 evaluates `for` bounds once, so the final size is exactly `maxZones`. | 228 | Yes |
| Q23 | **Eviction runs after all three creation blocks and before the scan.** A zone created this bar is scanned this bar (but see Q5); an evicted one is not. | 227 vs 264 | Yes |
| Q24 | **`zInside` is updated for broken zones too** — line 306 sits outside the `if`/`else if`. Harmless, but required for bitwise fixture agreement. | 306 | Yes |
| Q25 | **`if i < array.size(zTop)` (266) is dead defensive code** — nothing removes zones inside the loop. Do not reproduce it as meaningful logic. | 266 | Yes |
| Q26 | **Degenerate risk is possible and must not be silently "fixed".** `risk = math.abs(entry − stop)` can be ~0, and a short confirming far above its zone can produce a "stop" **below** its entry. Preserve the arithmetic; emit the values as computed; surface them in diagnostics. | 328–331 | Yes |
| Q27 | **The stop uses the confirming bar's ATR with the candidate bar's zone geometry** — `pendTop`/`pendBot` were frozen at candidate creation, `atrVal` is current. | 329 | Yes |
| Q28 | **Sweep freshness is inclusive of the sweep bar itself** and valid for `sweepValidBars` further bars — `sweepValidBars + 1` bars in total — because the assignment at 110–113 precedes the freshness test at 115–116. Sweep gates candidate creation only and is **never re-evaluated at confirmation**. | 110–116, 287–295 | **Deferred layer** — preserve when implemented |
| Q29 | **`priorHigh`/`priorLow` exclude the current bar** — `ta.highest(high, swingLen)[1]`. | 100–101 | **Deferred layer** — preserve when implemented |

---

## 10. Minimal implementation design

**One `FeatureProvider`. One trivial `ResearchModel`. One small `DecisionPolicy`.
Nothing else.**

### 10.1 Placement — append, do not create modules

| Component | Location | Why |
|---|---|---|
| `zone_trader_v13` (FeatureProvider) | **append to `src/trading_research/features/reference.py`** | `SLICE_2B_MODULE_SURFACES` and the `exact_imports` map in `tests/test_architecture.py` pin exactly three files. A new module would **not be checked** by the import/authority guard — that is a guard **gap**, not a pass. |
| `zone_event_gate_v1` (ResearchModel) | append to `src/trading_research/models/reference.py` | `SLICE_3C_LAYER_INVENTORIES` pins the `models` file set |
| `event_exposure_v1` (DecisionPolicy) | append to `src/trading_research/decisions/reference.py` | `SLICE_3F_DECISION_INVENTORY` pins the `decisions` file set |

All three layers require **underscore-aliased imports** (`import numpy as _np`),
because the module-public-name guards assert the public surface is exactly the
class names plus `annotations`.

### 10.2 Parameters — all on the provider

Registered models may not have an `__init__` (§6.2), and provider parameters are
where they belong anyway. Every parameter that can change a returned number goes
into `params` and is therefore identity-bearing and recorded.

The parameter set is now fully determined: §5's Pine parameters (at their source
defaults, in **M5 bars** per G-1) plus the six parameters the closed gates and
ID-2 introduce.

| Parameter | Value | Unit | Source |
|---|---|---|---|
| `bar_grid_seconds` | `300` | seconds | G-1 |
| `bar_phase_offset_seconds` | fix in S-2 | seconds | ID-2 |
| `anchor_grid_seconds` | `3600` | seconds | G-1 / OPEN-4 |
| `anchor_phase_offset_seconds` | fix in S-2 | seconds | ID-2 |
| `w_atr` | `300` | **M5 bars** | G-2 |
| `max_zone_age_rows` | `5000` | **M5 bars** | G-3 |

Every one is **identity-bearing**. `max_prior_rows` is **derived** from them
(§10.3.4), never declared independently. **No parameter may be introduced that
is not listed here or in §5**, and none may be added to "prepare" for a deferred
layer (OD-6B, OD-6C).

### 10.3 Window declaration

`GapPolicy.CROSS`, `nominal_interval = None`.

**Rationale, and it is substantive, not default-picking.** Pine has no gap
concept; it computes over consecutive chart bars regardless of elapsed time.
`CROSS` reproduces that exactly. `BREAK` would **change the strategy** by
resetting zones, anchors and pending candidates at every discontinuity — a
semantic the Pine source does not contain. `core/features.py` enforces that
`CROSS` carries no `nominal_interval`.

`cost_hint = EXPENSIVE`, so the frame is materialised once under
`CACHE_IF_EXPENSIVE` (ADR-0007).

### 10.3.1 Two axes, and why they must never be mixed

G-1 introduced a second axis. **They are different units and no formula may add
them together.**

| Axis | What it is | Where it appears |
|---|---|---|
| **Source axis — M1** | The observed rows the dataset actually contains, and the axis `FeatureRequest` presents. `WindowSpec.max_prior_rows`, the platform warm-up mask, the leakage rigs, `context_start` and `OutcomeSpec` horizons are **all** counted here. | Platform contract |
| **Strategy axis — M5** | The complete M5 bars the provider builds internally. `w_atr`, `max_zone_age_rows`, `maxBarsWait`, FVG `[2]`, D/S `[1]`, the H1 anchor and every other Zone Trader bar count are **all** counted here. | Strategy semantics |

> **Authoring rule.** Any parameter named in §7's G-1 unit table is in **M5
> bars**. `max_prior_rows` is in **M1 rows**. Writing `max_prior_rows = 5000`
> because `max_zone_age_rows = 5000` would be a **factor-of-five
> under-declaration** — a release blocker under ADR-0021 §2, and the exact class
> of error the backward-reach rig exists to catch.

### 10.3.2 Strategy-axis reach `R_M5` — derived

At the M5 bar `b` where a signal is emitted:

- the signal comes from a candidate created at `b − k`, `0 ≤ k ≤ maxBarsWait − 1`
  (Q13: the touch bar counts as the first waiting bar);
- that candidate came from a zone created at `(b − k) − a`, `0 ≤ a ≤
  max_zone_age_rows` (G-3: age exactly 5000 is still eligible);
- that zone's creation needed its own history back a further `C` M5 bars.

| Term | M5 bars | Source |
|---|---|---|
| Zone age bound | **5000** | `max_zone_age_rows` (G-3) |
| Candidate → confirmation | **5** | `maxBarsWait − 1` = 6 − 1 (Q13) |
| Zone-creation history `C` | **300** | `max(` ATR `w_atr` **300**, anchor history `(volLen+1) × 12` = **252**, FVG **2**, D/S **1** `)` |
| **`R_M5`** | **5305** | 5000 + 5 + 300 |

ATR (300) dominates the anchor history (252), so a single declaration covers
every zone family. The volume-zone family reads no ATR at all, so 300 is
over-declared for it — harmless and unavoidable with one declaration.

### 10.3.3 Converting `R_M5` to the M1 source axis — the safe declaration

`max_prior_rows` counts **observed M1 rows strictly before the evaluation M1
row**. Rows that lie *between* the oldest influencing row and the evaluation row
consume that distance whether or not they influence the output, so the
conversion is a **row-span** calculation, not an influence calculation.

Two facts drive it:

1. **An M5 bucket holds at most `300 / 60 = 5` observed M1 rows.** With gaps it
   holds fewer — never more. Using 5 makes the result an **upper bound**, which
   is what ADR-0021 §2 requires ("the declared value is an upper bound;
   over-declaration is safe, under-declaration is a release blocker").
2. **Two buckets sit between the evaluation row and the reach window**, and they
   are the alignment allowance the derivation must state rather than hand-wave:
   * the **current, possibly partial bucket** `B₀` containing the evaluation
     row — its earlier rows do not influence the output (G-1's leakage rule), but
     they still occupy axis distance;
   * the **emitting strategy bar** `B₁`, the most recent provably complete
     bucket, from which `R_M5` is measured.

Walking back from evaluation row `p`:

```
rows in B0 strictly before p                     ≤  4                    (5 − 1)
rows in B1 … B(1+R_M5)   =  5 × (1 + R_M5)       =  5 × 5306  =  26,530
                                                    ─────────────────────
max_prior_rows                                   =  26,534
```

Equivalently, in closed form:

```
max_prior_rows = 5 × (R_M5 + 2) − 1
               = 5 × R_M5 + 9
               = 5 × 5305 + 9
               = 26,534          # M1 SOURCE ROWS
```

The `+9` **is** the alignment allowance: `+4` for the partial current bucket and
`+5` for the emitting bar's own bucket, less the evaluation row itself.

**Implementation correction — safe over-declaration by eight rows.** The
original planning derivation treated both alignment allowances as potentially
influential at once. Predicate A fixes event projection at the **first** row of
the later bucket, so no earlier row from that bucket precedes the event; its
four remaining constituent positions are current/future and cannot influence
the emitted event. At the oldest ATR edge, the farthest contributing M1 value is the
last close of its bucket; the first four constituent rows do not extend the
influence. The exact farthest observed influence in the approved implementation
is therefore 26,526 M1 rows, eight below the declared 26,534.

The owner approved retaining 26,534 as the parameter-derived two-bucket
alignment envelope. This is conservative in the direction ADR-0021 permits and
keeps the declaration stable across the approved parameter formula. Tests do
not pretend the endpoint is influential: they prove the actual farthest
influence at 26,526 and separately prove that the oldest bucket's four leading
rows and the current-bucket four-row alignment allowance do not influence the
event. Under-declaration remains a release blocker; this documented eight-row
over-declaration is not.

**With gaps the true influence distance is smaller**, because buckets hold fewer
than five rows. The declaration stays conservative, which is the safe direction.

### 10.3.4 The declared window

```
WindowSpec(max_prior_rows=26_534, gap_policy=GapPolicy.CROSS)   # nominal_interval=None
```

Recomputation is mandatory whenever any of `max_zone_age_rows`, `w_atr`,
`maxBarsWait`, `volLen`, `anchor_grid_seconds` or `bar_grid_seconds` changes.
The provider must **derive** `max_prior_rows` from those parameters in code —
never carry it as a hardcoded literal that can drift out of step with them — and
a test must assert the derived value equals the expected number for the approved
configuration.

**Cost of this reach — checked, not assumed.** 26,534 M1 rows is ≈ 1.88 % of
TRAIN's 1,414,538 rows, and ≈ 18.4 days of observed bars. Per ADR-0012
Amendment 1 and `experiments/folds.py`, feature reach feeds **`context_start`,
not the purge** (`derived_purge_rows` comes from the OutcomeSpec), so the cost is
context rows, not fit-set size. `eval_start` must sit at least 26,534 observed
rows inside the view, or an earlier authorized view must supply the context.

### 10.4 Output columns

All `float64`, dense on the evaluation axis. **`0.0` means "known, and there is
no event"; `NaN` means "unavailable".** This distinction is deliberate: it keeps
the encoding honest *and* satisfies the rigs' finiteness assertions naturally.

| Column | Meaning |
|---|---|
| `zt_event_signed` | `−1.0` short signal · `0.0` no signal · `+1.0` long signal |
| `zt_entry_price` | Pine's displayed entry (confirming bar close); `NaN` off-event |
| `zt_stop_price` | Pine's displayed stop; `NaN` off-event |
| `zt_target_price` | Pine's displayed target; `NaN` off-event |
| `zt_zone_top` | zone top at the signal; `NaN` off-event |
| `zt_zone_bottom` | zone bottom at the signal; `NaN` off-event |
| `zt_zone_kind` | `0` volume · `1` imbalance · `2` demand/supply; `NaN` off-event |
| `zt_candidate_open` | `1.0` while a candidate is pending, else `0.0` (diagnostic) |

No name may be `target` or begin `target.`, and no name may use the banned
model-output vocabulary (§6.2) even though that guard applies one stage later.

### 10.5 Computation shape

Three stages inside one `compute()` call, one forward pass each:

1. **M1 → M5 aggregation** (G-1). Bucket the M1 keys arithmetically, reduce to
   complete M5 bars, and record for each M5 bar the **M1 row index at which it
   becomes provably complete** (ID-1). Vectorises in NumPy via
   `np.add.reduceat` / `np.maximum.reduceat` / `np.minimum.reduceat` over
   bucket boundaries. **No partial bucket may be emitted.**
2. **Strategy computation on the M5 axis.** Components 1 and 6–11 and 24
   vectorise; only the zone / candidate / pending state machine (12–16, 20–23)
   is scalar. It runs over **M5 bars**, so the sequential loop is ~⅕ the length
   it would have been under native M1.
3. **Projection back to the M1 evaluation axis.** Dense `float64` step function;
   `zt_event_signed` non-zero at **exactly one** M1 row per M5 signal (G-1
   consequence 3).

**`±inf` must be converted to `NaN` by the provider** — it is refused at output
validation, at frame minting and again at artifact encoding.

**Performance is a real risk and is measured, not assumed** (§11 S-6). The M5
axis helps: TRAIN's 1,414,538 M1 rows reduce to at most ~283,000 M5 bars, so the
scalar loop is roughly ~283,000 × up to 15 zones ≈ 4.2M inner steps. If the
provider is still too slow, the permitted responses are **NumPy restructuring
and caching only**. **Numba, Cython and any new dependency are prohibited
(OD-6B)** — a dependency change also alters the dependency digest that
`FittedState` identity pins (ADR-0022 Amendment 2).

---

## 11. Ordered implementation slices

**All three evidence gates are closed (§7), so there is no pre-implementation
investigation stage.** The required path now begins with the smallest
implementation and test preparation the approved design needs.

**This document still authorizes no code.** Slices begin when the owner approves
the first one (§19). Each slice states its prerequisite; a slice whose
prerequisite is unmet must not start.

> **Standing rule for every slice below.** If making something fit requires a
> new gap policy, partial-window output, unbounded history, another provider's
> output, a new data-access route, a non-`float64` or sparse feature column,
> **relaxing any architecture guard or rig assertion**, a feature derived from
> forward key structure, or touching `execution.py`, `core/`, `artifacts/` or the
> data layer — **STOP and raise an architecture finding**
> (`ADDING_FEATURES.md` §12 step 12, OD-6B).

### S-1 — Leakage-rig fixture enlargement — **COMPLETE 2026-08-19**

* **Objective:** make `tests/test_feature_leakage.py` able to host a provider
  whose declared reach is **26,534 M1 rows** (§10.3.3) and which reads `open` and
  `tick_volume` — **by enlargement only**.
* **Why it is no longer conditional.** The current harness uses
  `_NUM_ROWS = 128`, `_SEAM_POSITION = 64`, `_GAP_POSITION = 80`; the
  future-safety and backward-reach rigs assert `reach < _SEAM_POSITION`; the
  `BREAK`-isolation rig asserts `gap_position + reach < _NUM_ROWS`; and
  `_adversarial_values` **raises** for any column outside `{close, high, low}`.
  The closed gates put the reach four orders of magnitude above that, so **all
  four constraints fail before the provider is even exercised.**
* **Sizing implied by the harness's own assertions** (derive in code, do not
  hardcode a guess):

  ```
  _SEAM_POSITION  >  reach                      ->  ≥ 26,535
  _NUM_ROWS       >  _SEAM_POSITION + reach     ->  ≥ 53,070
  ```

  A synthetic axis of ~55,000 rows satisfies both with margin. **Prefer sizing
  the axis from the declared reach of the provider under test** (e.g.
  `max(128, 2·reach + margin)`) over a single global constant, so the three
  existing providers are not forced onto a 55,000-row axis unnecessarily and the
  relationship stays self-evidently correct.
* **Scope:** raise the synthetic-axis constants (or make them reach-derived);
  add `open` and `tick_volume` to `_synthetic_values()`; add deterministic
  adversaries for both in `_adversarial_values()`.
* **Files:** `tests/test_feature_leakage.py` only.
* **Non-goals — absolute:** **no assertion relaxed; no `==` weakened to `>=` or
  `issubset`; no pin deleted; no `skip`, `xfail`, marker or tolerance added; no
  Zone Trader code.**
* **Tests:** the three existing live providers stay green on the enlarged axis;
  **all six negative controls must still fail as designed** (`FutureShift`,
  `CenteredWindow`, `WholeDatasetNormalization`, `EvalEndPeek`,
  `UnderDeclaredReach`, `GapCrossingUnderBreak`). Record the rig-suite runtime
  before and after — a 400× longer axis must not make the release-blocking suite
  impractical.
* **Acceptance:** `./scripts/gate.sh` green unfiltered; the commit message states
  explicitly that this is a **guard-fixture enlargement** under `CLAUDE.md`'s
  change-control rule, so it is reviewed rather than absorbed.
* **Docs after approval:** note in `ADDING_FEATURES.md` §9 that the rigs now
  cover `open` and `tick_volume`.

### S-2 — Deterministic M1 → M5 aggregation, unregistered — **COMPLETE 2026-08-19**

* **Objective:** the new, non-Pine component G-1 introduced (§5 row 26), proven
  before anything is built on top of it.
* **Scope:** arithmetic M5 bucketing on the native key; the five reduction rules
  of G-1; the completion predicate (**ID-1** — record the choice here); the four
  grid parameters and their consistency validation (**ID-2**); and the mapping
  from each complete M5 bar to the M1 row at which it becomes usable.
* **Files:** private helpers in `features/reference.py`. **Not registered.**
* **Non-goals:** no ATR, no zones, no candidates, no registration. **No
  MultiTimeframeEngine, no resampling stage, no reusable timeframe abstraction**
  (OD-6B).
* **Tests:** §12.1's aggregation fixtures — a full 5-row bucket; a gapped bucket
  with 2 rows; a **missing** bucket producing no M5 bar; a bucket boundary
  landing exactly on a grid edge; **the leakage assertion that a partial bucket
  is never emitted**; and the ID-2 consistency validator refusing a misaligned
  anchor phase.
* **Acceptance:** every fixture matches literal expected numbers; ID-1 and ID-2
  are recorded in §10.3 and §8.

### S-3 — Finite ATR(14) approximation, `w_atr = 300`, unregistered — **COMPLETE 2026-08-19**

* **Objective:** the approved G-2 estimator, with its acceptance evidence.
* **Scope:** `_truncated_wilder_atr(high, low, close, w_atr)` on the **M5 axis**,
  computed as a fixed-window weighted correlation, never as a boundary-crossing
  recursion (§7 G-2).
* **Files:** private helper in `features/reference.py`. **Not registered.**
* **Non-goals:** no EMA-200 (OD-6C); **no search for an alternative estimator**;
  no change to `w_atr`.
* **Tests:** hand-computed 8-bar golden; exact-reach test (perturb at `b − 300`
  → value changes; at `b − 301` → **bitwise** unchanged); dtype/shape; no `±inf`.
* **Acceptance test (G-2 obligation, §12.3):** the seven-part comparison against
  a long-history recursive Wilder/RMA reference. **If divergence is unexpectedly
  material, STOP and report the measured evidence — do not adjust `w_atr`.**

### S-4 — Stateless Zone Trader primitives, unregistered — **COMPLETE 2026-08-19**

* **Objective:** the arithmetic that carries no state, on the M5 axis.
* **Scope:** candle geometry (Q18); FVG detection and sizing; Demand/Supply base
  and impulse (Q19); H1 anchor construction over the M5 axis (12 bars per
  anchor); abnormal anchor-volume detection (Q16, Q21); highest-`tick_volume`
  sub-bar selection (Q17, Q20).
* **Files:** private helpers in `features/reference.py`. **Not registered.**
* **Non-goals:** no zone lifecycle, no candidates, no confirmation, no
  registration. **No sweep, no VWAP, no EMA-200** (OD-6C).
* **Tests:** §12.1's FVG, Base, Impulse, anchor-volume and max-volume-sub-bar
  fixtures, plus the `tick_volume` usability assertion (OD-6A).
* **Acceptance:** every fixture matches literal expected numbers.

### S-5 — Stateful zone lifecycle, unregistered — **COMPLETE 2026-08-19**

* **Objective:** the highest-risk logic, isolated deliberately.
* **Scope, in exact Pine order, on the M5 axis:** creation (all three families)
  → **age expiry at `max_zone_age_rows = 5000` M5 bars** (G-3 boundary
  semantics: age 5000 eligible, 5001 expired) → count eviction (Q22) → scan
  newest→oldest (Q1) → break-before-candidate (Q7, Q8) → OHLC-overlap touch (Q6)
  → `wasIn` gate (Q5) → direction from `close[1]` (Q15) → scoring and the `>=`
  tie rule (Q2, Q3) → `zInside` update for every zone (Q24) → pending state
  (Q10, Q11) → confirmation and timeout (Q4, Q12, Q13) → entry/stop/target
  arithmetic (Q26, Q27).
* **New contract:** **none.** `max_zone_age_rows` is a **provider parameter**,
  not a platform concept (G-3).
* **Non-goals:** **no `ZoneManager`, no generic state engine, no event bus, no
  online-state framework** (OD-6B). No registration yet.
* **Tests:** §12.1's zone, candidate, confirmation and timeout fixtures — with
  the **exact age-boundary pair** (5000 eligible / 5001 expired) — plus the
  bespoke zone-reach test of §13.2.
* **Acceptance:** all fixtures exact; the reach test green.

### S-6 — Complete and register `zone_trader_v13` — **COMPLETE 2026-08-19**

* **Objective:** register **only once the reach declaration is honest and the
  rigs can enforce it.**
* **Scope:** assemble S-2…S-5 into the eight-member provider surface; emit the
  eight columns of §10.4; **derive `max_prior_rows` in code** from the six
  parameters (§10.3.4) and assert it equals **26,534** for the approved
  configuration; register.
* **Guard inventories — add names only, and say so in the commit message:**
  `SLICE_2B_MODULE_SURFACES["features/reference.py"]` (both sets) ·
  `SLICE_2C_LIVE_FACTORY_PROVENANCE` ·
  `test_slice_2b_reference_providers_have_the_exact_protocol_surface`'s iterated
  tuple · `exact_imports["features/reference.py"]` if a new import is needed ·
  `COVERED_PROVIDER_NAMES` in `tests/test_feature_leakage.py` · `__all__`
  (sorted).
* **Non-goals:** no model, no policy, no simulator, no experiment config, no
  documentation updates yet.
* **Tests:** the full `ADDING_FEATURES.md` §10 checklist — declaration test over
  all eight members; hand-computed goldens; **exactly `max_prior_rows` leading
  unavailable rows, no more and no fewer**; `CROSS` behaviour across a synthetic
  gap; declared safety plus the approved separate actual-influence/alignment
  proof (§10.3.3, §13.3); exact dtype and shape; `NaN`
  semantics with no `±inf`; registry inclusion; **zero-argument
  constructibility**; parameter identity.
* **Rigs (automatic, release-blocking):** future perturbation (single + seam),
  backward reach (single + seam), segment-seam equivalence, `BREAK` isolation
  under wrapping, and
  `test_rig_coverage_is_an_independent_exact_match_for_the_live_registry`.
* **Performance:** measure and record the feature-stage wall time on TRAIN.
* **Acceptance:** `./scripts/gate.sh` green unfiltered.

### S-7 — `zone_event_gate_v1` (ResearchModel) — **COMPLETE 2026-08-19**

* **Scope:** append to `models/reference.py`. **No `__init__`; `params = {}`.**
  No-op `fit` returning an empty `FittedPayload`; row-local `infer` mapping
  `0.0 → NaN`, `±1.0 → ±1.0` into `score`.
* **Guard inventories:** `SLICE_3C_MODEL_CLASSES` · `SLICE_3D_COVERED_MODELS` ·
  `COVERED_MODEL_NAMES` in `tests/test_model_leakage.py` — and **not**
  `TARGETED_MODEL_NAMES` (it takes no targets) and **not**
  `REPRESENTED_MODEL_NAMES` (it uses no representation) · `__all__`.
* **Non-goals:** no parameters (§6.2); no trading vocabulary; the literal string
  `"Direction"` must not appear anywhere in the file.
* **Tests:** golden mapping table; refusal when `training_targets` is supplied;
  the automatic forward-blindness and **row-locality** rigs.

### S-8 — `event_exposure_v1` (DecisionPolicy) — **COMPLETE 2026-08-19**

* **Scope:** append to `decisions/reference.py`. Sparse `IntentFrame` at finite
  non-zero score rows; `target_exposure = ±1.0`; schema
  `target_exposure_intent_v1`.
* **Guard inventories:** `SLICE_3F_POLICY_CLASSES` · `__all__`.
* **Tests:** golden sparse key set; **explicit assertion of the
  absent / `NaN` / `0.0` trichotomy** (§6.3); keys strictly increasing and a
  strict subset of the model output's keys; **one intent per M5 signal**, not one
  per M1 row (G-1 consequence 3).

### S-9 — First pre-registered Plane-A study, TRAIN only — **COMPLETE 2026-08-19**

* **Objective:** answer **one pre-registered question** — *does a Zone Trader
  re-entry event carry sign information about the forward return?* (H1, §15.2).
* **Scope:** one resolved config — `zone_trader_v13` → `zone_event_gate_v1` →
  `threshold_v1` (threshold `0`) → `forward_log_return_v1` →
  `sign_accuracy_v1` **and** `rank_ic_v1`; rolling walk-forward folds inside
  TRAIN. **The OutcomeSpec horizon is stated in M1 rows** (G-1 consequence 4).
* **Non-goals:** **no OOS, no LOCKBOX, no simulator, no TP/SL, no parameter
  sweep.**
* **Acceptance:** the run completes; the Trial Ledger records the evaluations;
  metrics reported **with `effective_samples`** and the mandatory correlation
  caveat.
* **Docs after approval:** record the hypothesis, the frozen parameters and the
  result — **including a disappointing one** (RESEARCH_POLICY §6).

#### S-9 implementation and TRAIN result

**Pre-registration.** The frozen resolved config is
`config/experiments/zone-trader-v13-h1-train-v1.json`; its canonical JSON
SHA-256 is
`2a3676a078afc442f20dbd840f2e5cfde14d40e938f86857d96b16913d6f0697`.
The 26,534-M1-row feature reach consumes the beginning of TRAIN as context:
`context_start = 1577926800`, `eval_start = 1580299740`, and
`eval_end = 1703894160`. The remaining evaluation axis has 1,388,004 observed
M1 rows. The owner-approved feasibility correction uses five contiguous,
non-overlapping rolling folds with `fit_rows = 388004`,
`eval_rows = 200000`, `step_rows = 200000`, `embargo_rows = 0`; the model's
fit is a no-op. The outcome is the observed-M1-row target
`log(P[X+6] / P[X+1])`: `decision_delay_rows = 1` and
`horizon_rows = 5`, both explicitly **M1 source rows**.

The provider configuration is frozen as follows. Bar-counted strategy
parameters in this table are M5 unless explicitly labelled M1.

| Group | Frozen value |
|---|---|
| Enabled zone families | volume, imbalance and demand/supply all `true`; Sweep, VWAP and EMA trend absent per OD-6C |
| Grids | M5 `bar_grid_seconds=300`, phase `0`; H1 `anchor_grid_seconds=3600`, phase `0` |
| Volume anchor | `vol_len=20`, `vol_mult=2.0` |
| Imbalance | `min_fvg_atr=0.25` |
| Demand/supply | `max_base_body=0.5`, `max_base_atr=0.8`, `min_move_atr=1.2` |
| Confirmation | `rejection_wick`, `minimum_wick=0.35`, `max_bars_wait=6` M5 bars |
| Zone lifecycle | `max_zones=15`, `break_buffer_atr=0.5`, `max_zone_age_rows=5000` M5 bars |
| Displayed bracket | `stop_buffer_atr=0.5`, `reward_risk_target=2.0`; not used by Plane A |
| Finite ATR / source reach | `w_atr=300` M5 bars; declared reach `26,534` observed M1 rows |
| Model / policy | `zone_event_gate_v1`; `threshold_v1(score, threshold=0.0)` |
| Evaluators | `sign_accuracy_v1(min=1)` and `rank_ic_v1(min=2)` against `target.forward_log_return` |

**Successful canonical TRAIN run.** Run
`01M0DQCN78TX82R57MJKQBGVX9`, experiment fingerprint
`1c7746ce718959b00cfa935fbfb1ad539e43bb783f8767830b843015f7b0e490`,
was executed from clean commit
`e30c6ffc0184855b7bca20407c2d017d48fcaf0b`. It completed in
4.910147258 seconds, persisted a 99,947,460-byte feature artifact, recorded
five policy executions and ten Plane-A inventory entries, and touched neither
OOS nor LOCKBOX.

| Fold | `sign_accuracy` | Effective samples | `rank_ic` | Effective samples |
|---:|---:|---:|---:|---:|
| 0 | 0.44687500000000002 | 320 | -0.13510301051049367 | 324 |
| 1 | 0.49469964664310956 | 283 | 0.0028375023965299894 | 286 |
| 2 | 0.48464163822525597 | 293 | -0.008936213969692521 | 298 |
| 3 | 0.47508305647840532 | 301 | -0.033256232788504564 | 306 |
| 4 | 0.51666666666666672 | 300 | 0.05263111331867238 | 304 |

Across folds there were 1,497 effective sign observations; the descriptive
sample-weighted accuracy is 0.48296593186372744. There were 1,518 effective
rank observations; the descriptive effective-sample-weighted fold value is
-0.026219624682059774 and the fold range is
[-0.13510301051049367, 0.05263111331867238]. These cross-fold summaries are
descriptions, **not additional preregistered estimators**.

**Result.** H1 does **not** show consistent positive sign information on TRAIN:
four of five fold accuracies are below 0.5 and rank IC changes sign, with three
negative folds and two positive folds. This is a valid negative research result,
not a failed phase. It authorizes no OOS/LOCKBOX claim and, under OD-6D, gives
no basis to start the conditional Plane-B study.

**Trial Ledger.** The all-runs-root report examined four canonical TRAIN runs:
one distinct evaluated hypothesis, two Plane-A trials (the two evaluator/metric
questions; folds collapse), zero Plane-B trials, three visible
`pre_evaluation_failure` runs excluded from the denominator, and one executed
but unevaluated policy family. `effective_samples`: count 10, min 283, median
300.5, max 324. There were zero OOS events, zero LOCKBOX events, zero audit
discrepancies and zero noncanonical runs. The mandatory caveat applies:
**the two reported questions are not claimed statistically independent, the
effective number of independent trials is not estimated, and no
multiple-testing correction or significance claim is attached to either
metric.** Rank IC is a correlation on TRAIN only; it is not causation, execution
evidence or an OOS estimate.

**Retained pre-evaluation failures and narrow corrections.** Nothing was
deleted or retried in place. Run `01M0D9P7BT9SSPCNPHGAZTNXCK` exposed the
missing reach burn-in; before any metric existed, the owner approved the exact
`eval_start` above and `fit_rows=388004` to preserve five non-overlapping folds.
Run `01M0DQ5C8WFKJYWZ5YNKFN12BX` exposed the 64-MiB run-file ceiling; the shared
writer/reader ceiling was raised to 128 MiB without changing NPZ encoding or
any value. Run `01M0DQ964YJDGTM9SJ5JMNPD92` exposed that preflight admitted a
provider feature superset but fold execution did not project it to the model's
exact declared feature tuple; the orchestrator now performs that projection,
with an end-to-end regression test. All three are platform failures before a
valid evaluation, contribute zero trials under ADR-0024, remain permanently
visible, and were corrected before the successful result was observed.

**Acceptance evidence.** The committed TRAIN-only G-2 comparison observed
2,146 finite and 2,146 recursive events with 2,146 unchanged, zero added,
removed or moved events; zero FVG, base/impulse, break or confirmation
classification differences; maximum ATR absolute difference
2.236626350793358e-08 and p99 6.906949323592898e-10. The full feature-stage wall
time was 1.3133074109209701 seconds. The unfiltered gate at the implementation
head was green: 1,697 active tests (1,705 collected; 8 explicit performance
tests deselected), 189 architecture tests, Ruff clean, and strict mypy clean
over 158 source files. The audit remained exactly 3 records with SHA-256
`deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`.

### Plane B — **DEFERRED AND CONDITIONAL** — prerequisite: S-9 **and** a separate owner decision

Runs only if S-9 shows evidence of predictive value (OD-6D). Adds
`event_exposure_v1` plus one `ExecutionSpec` with fixed TP/SL,
`cost_semantics: unverified`, `execution_price_model: recorded_path_unverified`.

**Mandatory labelling, wherever any number from it appears:**

> This is not Zone Trader's execution. Entry is the next observed **M1** bar's
> `open`, not the signal bar's close; the bracket is fixed per run, not per
> signal. Money figures are unverified in execution as well as in money and may
> not be quoted as real-money estimates.

### 11.1 Slices explicitly NOT in the required path

Per OD-6C, **no VWAP, Sweep or EMA-trend work appears anywhere in S-1…S-9**.
They exist only under §16.6, "deferred Zone Trader capabilities". Do not
reintroduce them, and do not add a parameter, column or code branch that exists
only to prepare for them.

Per OD-6B, **no MultiTimeframeEngine appears anywhere**, despite S-2 adding
timeframe aggregation. S-2's aggregation is private to
`zone_trader_v13`, parameterised by two integers, and is not a reusable
abstraction.


## 12. Test strategy

**Principle: a human who does not trust an AI translation must be able to check
it by hand.** Synthetic data only — never real market values in a fixture.

### 12.1 Hand-computed fixtures — literal expected values, never a re-implementation

Each of the following requires **separate verification** with a small fixture
(5–30 rows) whose expected numbers were worked out by hand:

| Algorithm | Cases |
|---|---|
| **M1 → M5 aggregation** (S-2) | full 5-row bucket · gapped 2-row bucket · **missing bucket produces no M5 bar** · boundary exactly on a grid edge · **a partial bucket is never emitted** · ID-2 consistency validator refuses a misaligned anchor phase |
| **ATR** (`w_atr = 300` M5 bars) | hand-computed 8-bar golden · exact-reach pair (`b−300` changes it, `b−301` does not) **and** the seven-part acceptance comparison of §12.3 |
| **FVG** | detected · not detected · fails the `0.25·ATR` size threshold |
| **Base** | qualifies · fails body ratio · fails ATR range · `baseRange == 0` (Q19) |
| **Impulse** | bullish · bearish · fails `1.2·ATR` · fails `close > high[1]` |
| **Anchor volume** | above `2.0×` · **exactly `2.0×` must NOT fire** (Q21) · average excludes the anchor under test (Q16) |
| **Max-volume sub-bar** | unique maximum · **tie → most recent wins** (Q17) · 500-bar cap present (Q20) |
| **Zone creation** | one fixture per family; zone bounds exactly as the source computes them |
| **Zone touch** | OHLC overlap true · false (Q6) |
| **Break / no-break** | wick-only penetration does not break (Q7) · close at **exactly** `edge + buf` does not break (strict `>`) · close beyond does break · break wins over candidate on the same bar (Q8) |
| **Leave / re-enter** | two separate events from one surviving zone (Q9) · **no event on the creation bar** (Q5) |
| **Candidate selection** | direction from `close[1]`, including the straddle case `b <= close[1] <= t` → LONG (Q15) · **two zones entered on one bar with equal score → older wins** (Q3) · pending overwritten by a new candidate (Q10) |
| **Confirmation** | same-bar confirmation in "immediate touch" mode (Q4) · wick-mode confirmation two bars later **with no zone overlap on the confirming bar** (Q12) · close-back-outside mode |
| **Timeout** | expires at exactly `maxBarsWait` bars inclusive of the touch bar, **and the final permitted bar still gets its chance** (Q13) |
| **Entry / stop / target descriptive arithmetic** | both directions · confirming-bar ATR with candidate-bar geometry (Q27) · **the degenerate `risk ≈ 0` case preserved, not repaired** (Q26) |
| **Zone age** (G-3) | **age exactly 5000 M5 bars → still eligible; age 5001 → expired.** Both halves required; the pair is the whole point of the fixture |
| **Eviction** | exactly `initialSize − maxZones` removed (Q22) · a broken zone still occupies its slot · eviction before the scan (Q23) |

### 12.2 What must never substitute for the above

**Aggregate backtest agreement is not evidence of semantic correctness.** A
strategy can agree in aggregate while differing on thousands of individual
events. Every algorithm above needs its own fixture.

### 12.3 ATR — the G-2 acceptance test

G-2 is **closed**; this is the acceptance test of an approved implementation
choice, **not a gate and not an invitation to reopen it**.

Compare the approved `w_atr = 300` estimator against a **long-history recursive
Wilder/RMA reference** (computed for comparison only, never registered) on
TRAIN, and report all seven:

| # | Measurement |
|---|---|
| 1 | **maximum** numerical ATR difference |
| 2 | representative **distribution / summary** of the numerical differences |
| 3 | **FVG classification** differences (`fvgSizeOk` flips) |
| 4 | **Demand/Supply classification** differences (`isBase`, `isImpulseUp/Dn` flips) |
| 5 | **zone-break** differences |
| 6 | **confirmation** differences |
| 7 | **final Zone Trader event-row** differences — added, removed, moved |

**Items 3–7 are the decisive ones.** ATR enters five threshold comparisons, so
an arbitrarily small numerical difference can flip a discrete decision. A pass on
1–2 with a material failure on 3–7 is a **failure**.

> **If the observed divergence is unexpectedly material, implementation STOPS and
> reports the measured evidence.** It does **not** change `w_atr`, adjust the
> weights, or substitute another estimator. Selecting `w_atr` after seeing its
> downstream results would be parameter selection on observed outcomes (§15.3),
> and it would reopen a decision the owner has closed.

### 12.4 Zone state — the G-3 boundary must be proven, not assumed

G-3 is **closed**: `max_zone_age_rows = 5000` **M5 bars** is approved as a
deliberate, owner-accepted deviation from Pine (§7 G-3).

S-5 must therefore carry a fixture proving the boundary **exactly**: a zone at
age **5000** is still eligible and can still produce a candidate; the same zone
at age **5001** is expired and produces nothing. Both halves are required — a
test that only checks expiry would pass an off-by-one that silently shortens
every zone's life by one bar.

S-5 must also assert that expiry is evaluated on the **M5 strategy axis**, and a
test must fail if the bound is ever applied to M1 source rows — the factor-of-five
error §10.3.1 warns about, in its most damaging form.

### 12.5 Differential testing against TradingView — corroboration only

Comparing exported TradingView values against the provider is **recommended as
corroboration**, and **can never be exact**. Enumerated, legitimate sources of
divergence:

1. the approved finite ATR approximation versus Pine's recursion (G-2);
2. **M5 and anchor grid phase** versus TradingView's session bucketing (ID-2) —
   two grids now, not one;
3. the approved **5000-M5-bar zone-age bound** (G-3), which removes every signal
   from a zone Pine would still consider valid;
4. the ID-1 completion predicate, which fixes signals to the earliest **provably
   complete** bar rather than to TradingView's bar-close instant;
5. a different data vendor and therefore a different bar set entirely.

**Record the observed divergence rate with its attribution. Never present
differential agreement as a substitute for §12.1.**

---

## 13. Leakage and information-reach requirements

### 13.1 Automatic, release-blocking, bitwise

Registering the provider enrols it in the existing rigs, which parametrise over
the live registry and compare **exactly** — NaN masks as bytes, values as raw
`uint64` bits:

| Rig | Property proven |
|---|---|
| Future perturbation (single axis + multi-segment seam) | Nothing at or before a cutoff changes when later market values are replaced adversarially |
| Backward reach (single + seam) | Perturbing rows strictly before `keys[i − max_prior_rows]` never changes value `i` |
| Segment seam | Multi-segment output is bit-for-bit identical to the single contiguous axis |
| `BREAK` isolation | Wrapped in a `BREAK` declaration, post-gap output is unaffected by pre-gap values |

The model rigs (forward-blindness and row-locality) attach automatically on model
registration.

**No tolerance, `skip`, `xfail` or marker may be introduced into any of them.**

### 13.2 Bespoke zone-reach test (S-5)

Perturb an M1 row strictly before the zone-persistence horizon and assert the
**alive zone set** at the evaluation row is **bitwise identical**. This is what
makes G-3's bound enforceable rather than merely argued: without it, an
off-by-one in the age expiry would silently extend the real reach beyond the
declared 26,534 rows, which is an under-declaration and therefore a release
blocker.

### 13.3 Conservative-declaration non-vacuity test

The implementation review proved that predicate A and the ATR edge cannot make
both four-row alignment allowances influential simultaneously. The declared
26,534-row envelope is therefore eight rows larger than the exact farthest
influence, 26,526 (§10.3.3). The owner approved a narrow correction to the
generic over-declaration recipe rather than fabricating a fixture that asserts a
false endpoint dependency.

The dedicated test now proves two independent facts bitwise: perturbing the
actual farthest influencing row at `p − 26,526` changes the event, while the
oldest bucket's four earlier constituent rows and the current-bucket four-row
alignment allowance do not. The ordinary backward-reach rigs still prove that
nothing earlier than the **declared** bound can influence output. Together these
show that the declaration is safe, finite and non-vacuous while recording its
exact eight-row conservatism honestly.

### 13.4 Reach cost — verified, and lower than it looks

`experiments/folds.py` derives `derived_purge_rows = min(fit_rows,
maximum_forward_rows)` from the **OutcomeSpec**, while `max_feature_prior_rows`
feeds only `context_start`. **A large `max_prior_rows` costs context rows, not
fit-set size.**

Concretely, at **26,534** M1 rows: ≈ **1.88 %** of TRAIN's 1,414,538 rows and
≈ 18.4 days of observed bars. `eval_start` must sit at least 26,534 observed rows
inside the view, or an earlier authorized view must supply the context
(ADR-0012 Amendment 2). Fold geometry in S-9 must be chosen with that in mind.

### 13.5 Authoring rule the rigs cannot enforce

`ADDING_FEATURES.md` §7 records that the rigs perturb **market value columns**,
not the key axis, so a provider deriving values from **forward key structure**
would pass every rig while using unavailable information.

**Binding on this provider:** the **M5 bucketing, the anchor grid and the ID-1
completion predicate** may read `request.keys` **only at or before the current
evaluation position**. None may use `keys[i+1]`, forward key spacing, or
`len(request.keys)`.

This is sharper after G-1 than before it, because the completion predicate is
*about* whether later rows exist. **Predicate A is expressed as "a row in a later
bucket has already been observed at or before position `i`" — a backward
statement — never as "no further rows will arrive in this bucket", which would be
a forward read.** Getting that inversion wrong would pass every rig while using
information unavailable at decision time.

**A human reviewer must verify this by eye at CP-23**, because no rig catches
it.

---

## 14. Determinism requirements

1. **Exact comparison by default.** `comparison_tolerance = None` on every
   component. A tolerance here would be an **identity-bearing contract
   declaration** under ADR-0022 §8 — never a convenience, never a verifier knob.
2. **Repeat-call bitwise equality** for every helper, for `compute()`, for
   `infer()` and for `decide()`.
3. **No hidden environment dependence**: no `os.environ`, no wall-clock read, no
   config-file read, no module-level mutable state, no unseeded randomness. The
   architecture guards scan for these identifiers.
4. **Stage identity changes on any parameter change** — verified by the parameter
   identity test in S-4.
5. **No `±inf` anywhere.** Refused at output validation, frame minting and
   artifact encode/decode. Convert to `NaN` in the provider.
6. Cache-bypassing determinism verification recomputes and never reads cache
   (ADR-0007); its report is a stored artifact (ADR-0016, ADR-0022 §8).
7. **No cross-hardware bitwise reproducibility is claimed** — ADR-0022 §8 and
   V1.md §10.

---

## 15. Research-methodology safeguards

### 15.1 Plane A first — not advice, a rule

ADR-0023 §1 and RESEARCH_POLICY §7 are binding, and OD-6D restates them for this
strategy. Zone Trader arrives with `slBufferATR` and `rrTarget` already in it;
**that is precisely the temptation these rules exist to refuse.**

### 15.2 Intermediate hypotheses — small, ordered, pre-registered

| H | Question | Chain | What a negative result kills |
|---|---|---|---|
| **H1** | Does a zone re-entry event carry **sign** information about the forward return? | `zt_event_signed` → `score` → `sign_accuracy_v1` + `rank_ic_v1` vs `forward_log_return_v1` | If H1 fails at a decent `effective_samples`, the strategy is dead **before any TP/SL exists** |
| **H2** | Do the three zone families differ? | H1 restricted by `zt_zone_kind` | May kill two of three families cheaply |
| **H3** | Does confirmation add anything over the raw touch? | H1 under one pre-registered mode vs "immediate touch" | Tests whether confirmation is signal or decoration |
| **H4** | Is the direction rule (`zBot > close[1]`) better than its inverse? | H1 with the sign flipped | A negative here is more informative than a marginal positive |

**H1 first, alone, pre-registered. Everything else waits on H1.**

### 15.3 Parameters that create multiple-testing risk if swept

ADR-0024 and RESEARCH_POLICY §6: the platform **counts, it does not adjust**, and
**no deflated statistic, adjusted metric, p-value or significance claim exists in
V1**. The denominator is the only defence, so it must be kept small.

| Parameter | Risk | Discipline |
|---|---|---|
| `minFvgATR`, `maxBaseBody`, `maxBaseATR`, `minMoveATR` | **high** — continuous | Freeze at source defaults |
| `volMult`, `volLen` | **high** | Freeze |
| `confirmMode` | **high** — three discrete options, tempting to try all | **Pre-register one** for H1 |
| `minWick` | **high** | Freeze |
| `maxBarsWait`, `maxZones`, `breakBuf` | medium | Freeze |
| sweep / VWAP / trend parameters | n/a | **Not implemented** (OD-6C) |
| `slBufferATR`, `rrTarget` | **highest** — pure Plane-B curve-fitting bait | **Never sweep** (RESEARCH_POLICY §7) |
| chart timeframe (G-1) | **high** — a strategy identity, not a knob | **FIXED: M5.** Never re-opened as a variant |
| `max_zone_age_rows` (G-3) | **high** | **FIXED: 5000 M5 bars**, approved before any evaluation. **Never swept, never optimized** |
| `w_atr` (G-2) | numerical, not research | **FIXED: 300 M5 bars.** **Never swept.** Changing it after seeing §12.3's results would be selection on observed outcomes |
| `bar_phase_offset_seconds`, `anchor_phase_offset_seconds` (ID-2) | **high** — phase is a free parameter with no ground truth | Fix once in S-2 and record. **Never swept** |

**No exhaustive parameter mining is proposed and none may be authorized.** Start
at source defaults; every deviation is a decision that is recorded and counted.

### 15.4 Data discipline

- **TRAIN only** for S-1 through S-9. VALIDATION only after H1 has a
  pre-registered result.
- **OOS**: explicit, audited, per-run ceremony. **Never for parameter selection.**
- **LOCKBOX**: untouched. A **sweep over LOCKBOX is prohibited, not merely
  gated.**
- **Descriptive runs count zero** toward the trial denominator while remaining
  permanently recorded. G-2 and G-3 are descriptive.
- **Nothing is deleted or hidden.** Disappointing results are recorded.

---

## 16. Explicit non-goals

### 16.1 Rejected platform abstractions — OD-6B

Each was evaluated against the justification test (research correctness, or broad
usefulness across materially different research families) and **rejected**:

| Considered | Why rejected |
|---|---|
| **Generic `MultiTimeframeEngine`** | One strategy needs one arithmetic grid. Two integer parameters inside one provider deliver the entire requirement. A general engine would need a resampling stage, which ADR-0006 closes. |
| **`ZoneManager` platform service** | The most important rejection. Zone state is *this strategy's* state, not the platform's. ADR-0022 §6 refused generic carried state, and its *"Alternatives considered"* refused even a declared model-level reach field as speculative. A service would pre-authorize exactly that. |
| **Generic event bus / streaming engine** | ADR-0006 exists to refuse engines. One producer, one consumer. |
| **Generic online-state framework** | ADR-0022 §6's trigger requires a model family that *cannot* be expressed row-locally. Zone Trader's model **can** be. |
| **Generic session / calendar framework** | ADR-0002 and ADR-0005. Needed only for VWAP, which is deferred. |
| **New persistent market-data format / database infrastructure** | ADR-0017: files are truth, SQLite is disposable. Nothing here needs storage. |
| **Generalized order engine** | ADR-0023 §2's exclusion list. |
| **Numba / Cython / any new dependency** | Changes the dependency digest `FittedState` identity pins (ADR-0022 Amendment 2) and requires its own review. |
| **Generic DAG / feature-of-feature layer** | ADR-0021 §7 and ADR-0006. |
| **A new `features/zone_trader.py` module** | `SLICE_2B_MODULE_SURFACES` and `exact_imports` pin exactly three files; a fourth would slip past the import/authority guard. That is a guard gap, not a pass. |
| **Relaxing any rig or guard to make the provider fit** | `ADDING_FEATURES.md` §12 step 12: **stop and raise.** Enlarging a fixture (S-1) is legitimate; weakening a comparison is not. |

### 16.2 No `TRUNCATE`, no `min_observations`, no time-distance windows

ADR-0021 §3 refuses partial-window output with stated reasoning; ADR-0021 §2
refuses time-distance windows because they require attributing meaning to key
deltas, which ADR-0005 forbids.

### 16.3 VWAP is not implemented, and exact reproduction is not currently possible

`ta.vwap` (line 88) anchors to the TradingView **session** start, which is
exchange-calendar-derived. Reproducing it honestly would require an authoritative
statement of the broker's session boundary in the
`mt5.justmarkets-demo3.wallclock` domain **plus** an implemented
`CalendarTimePolicy` decode. **Neither exists**: `core/time.py` records that the
policy defines only its identity, with no decoding and no calendar arithmetic,
and ADR-0002 records the broker clock's DST history as unknown and unrecoverable.

**No substitute may be invented** — not UTC midnight, not an exchange session,
not a New York session, not a broker session. Deriving a day boundary from
`key % 86400` inside a provider would be inventing a civil-day semantic in an
unmarked place, which ADR-0005 forbids.

OD-6C makes this moot for now: `useVwapPriority` is `false`, VWAP contributes
only `+1.0` to a tie-break score, and it is never a gate. **Enabling it later is
an architecture decision requiring the calendar work first, not a strategy
tweak.**

### 16.4 Exact Pine execution reproduction is out of scope

ADR-0023 Amendment 1 has supplied the second intent schema and simulator
support for per-opening-instruction bracket distances. It deliberately did not
create an intent-schema registry: two schemas supported by one simulator still
give a registry no resolution job.

The remaining exact-reproduction gap is the fill semantic. Pine records its
displayed entry at the confirming bar's close and derives absolute stop/target
levels from that price; the platform acts only at the next observed row's open
and binds the supplied distances around the actual fill. Entry-at-close remains
refused as lookahead-prone execution, so exact Pine execution is still out of
scope even though per-trade bracket transport is now available.

**Not proposed, and not to be built.** The shape is recorded here only so it is
not re-derived if the owner ever raises it.

### 16.5 No ADR is authorized by this plan

See §17.3.

### 16.6 Deferred Zone Trader capabilities — strategy features, not platform gaps

Each is a small reviewed **strategy-plugin extension** if research later requires
it. None may be pre-built, and nothing may be built now to "prepare" for them.

| Capability | Deferred because | Cost to add later |
|---|---|---|
| **Sweep layer** (components 5, 18; Q28, Q29) | `useSweep = false` (OD-6C) | Bounded, clean: rolling-extreme + wick test + bounded freshness, plus its own fixtures |
| **EMA-200 trend filter** (components 3, 22; Q14) | `useTrendFilter = false` (OD-6C) | Requires an owner decision equivalent to G-2 for the EMA truncation window, and **increases `max_prior_rows`** — an EMA-200 window of `w_ema` M5 bars would raise `R_M5` and therefore the M1 reach by `5 × max(0, w_ema − 300)` |
| **VWAP priority** (components 4, 19) | `useVwapPriority = false` **and** not representable (§16.3) | **Blocked** on session/calendar capability |
| **Plane-B evaluation** | OD-6D | S-8, conditional on H1 |
| **Exact Pine execution** | §16.4 | Requires changing the refused same-bar close-fill semantic; per-trade bracket transport itself is already implemented |

---

## 17. Documentation obligations

### 17.1 Updated by the commit that introduces this plan

| Document | Change |
|---|---|
| `docs/phases/PHASE6_ZONE_TRADER.md` | **New — this document** |
| `PROJECT_STATE.md` | Phase-6 planning status; the new normative plan; OD-6A…OD-6D; G-1/G-2/G-3 open; the new exact next action |
| `docs/roadmap/V1_ROADMAP.md` | Phase 6+ status `NOT_STARTED` → planning in progress, pointing here rather than duplicating |

### 17.2 Required after each future approval

| Trigger | Update |
|---|---|
| ID-1 / ID-2 fixed in S-2 | §8.3, §8.4 and §10.3 record the chosen predicate and phase values |
| S-1 approved | `ADDING_FEATURES.md` §9 rig-coverage note (`open`, `tick_volume`, enlarged axis) |
| Each slice approved | `PROJECT_STATE.md` slice record with the exact reviewed commit |
| H1 evaluated | The hypothesis, frozen parameters and result — **including a disappointing one** |
| Phase 6 (Zone Trader) complete | §20 criteria evidenced; roadmap status |

### 17.3 ADR discipline — no ADR is authorized

**Do not create an ADR merely because Phase 6 exists.** Under the currently
approved scope:

- **no ADR is authorized**;
- VWAP / session capability is **deferred**, not designed;
- exact Pine dynamic-bracket execution is **deferred**, not designed;
- generic state infrastructure is **rejected** (OD-6B);
- G-1, G-2 and G-3 are **closed by owner decision** and required **no**
  contract change: the M5 aggregation is private provider arithmetic, the finite
  ATR fits ADR-0021 §2's finite-reach rule, and `max_zone_age_rows` is a provider
  parameter rather than a platform concept. §8.5 records the explicit blocker
  check.

Implementation exposed two narrow internal mismatches, recorded in S-9: a
bounded file-size ceiling below the first valid eight-column run artifact, and
a missing model-input projection despite preflight already admitting provider
supersets. The owner pre-authorized result-neutral narrow corrections. Raising
the shared ceiling to 128 MiB did not change the deterministic uncompressed NPZ
format; projecting columns realizes the existing exact model-input declaration.
Neither changes an accepted research/data contract or requires an ADR.

**If an implementation slice proves that an accepted architecture contract must
change, STOP and recommend an ADR then** — with the concrete finding that forced
it. Do not pre-authorize speculative architecture, and do not write an ADR in
anticipation.

---

## 18. Review checkpoints

Numbering continues from Phase 5, which ended at CP-21. All three checkpoints
have green implementation evidence, completed independent review, and explicit
owner approval recorded on 2026-08-23.

| CP | Covers | Green when |
|---|---|---|
| **CP-22** | **S-1 + S-2 + S-3.** Rig enlargement, M1→M5 aggregation, finite ATR. | **GREEN AND OWNER APPROVED.** All negative controls and prior providers remain live; ID-1/ID-2 are fixed; exact fixtures and the seven-part TRAIN ATR comparison pass. |
| **CP-23** | **S-4 + S-5 + S-6.** Stateless primitives, stateful zone lifecycle, registered provider. | **GREEN AND OWNER APPROVED.** Exact lifecycle fixtures, age 5000/5001, derived reach 26,534, leakage/reach guards and TRAIN timing pass; forward-key logic uses only already-observed rows. |
| **CP-24** | **S-7 + S-8 + S-9.** Model, policy, and the first Plane-A study. | **GREEN AND OWNER APPROVED.** Row-locality/forward-blindness, trichotomy and sparse-intent tests pass; the canonical TRAIN run and Trial Ledger are recorded above with effective samples, M1 units and the correlation caveat. |

The bounded independent review initially returned REQUEST CHANGES. F-1 found
that the provider accepted seconds axes denser than the M1 assumption used by
its declared reach; F-2 found fixed-bracket-only wording left stale after the
per-trade integration. `a200898` added the fail-closed minimum-60-second-density
check, single-axis and seam adversarial regressions, and documentation
reconciliation. The first follow-up accepted F-1 and identified one remaining
F-2 sentence; `811db03` corrected it. The final follow-up returned **APPROVE**,
after a documentation diff check. The independent reviewer ran the full gate at
clean `a200898`; the implementing agent repeated the unfiltered gate at clean
`811db03` and at the later documentation-only closeout head. The owner then
approved CP-22/CP-23/CP-24, closeout and merge on 2026-08-23.

---

## 18.1 Per-trade bracket integration (2026-08-22)

`main`'s per-trade bracket infrastructure (`c5dedf2`,
[ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) Amendment 1) was
merged into this branch so the Zone Trader chain can reach the bracketed intent
path. Every Phase-6 commit is preserved and nothing on either side was
reimplemented.

**One policy was added: `zone_fixed_bracket_exposure_v1`.** Its exposure decision
is bit-for-bit `event_exposure_v1`'s — same sparse selection, same signs, same
keys, pinned by a parity test — and the only addition is that each opening
instruction also names a fixed take-profit and stop-loss distance in price units.
The distances are identity-bearing policy parameters, so they reach
`experiment_fingerprint` and cannot hide inside simulator code.

**`zone_event_gate_v1` is unchanged**, deliberately. This qualification proves
the bracket *transport* independently of the bracket-generation algorithm, so it
does not need `zt_stop_price` or `zt_target_price` in model output yet, and no
`zone_event_gate_v2` was created. `event_exposure_v1` is likewise untouched and
still registered.

**The bracket used here is a fixed constant and is not the Zone Trader stop or
target.** It exists to exercise the path. The strategy's real per-event
ATR/zone-edge stop and R-multiple target remain future work (§5, §10), and no
result from the qualification family is evidence about bracket quality.

The frozen family is
[ZONE_TRADER_PER_TRADE_BRACKET_QUALIFICATION_V1.md](../research/ZONE_TRADER_PER_TRADE_BRACKET_QUALIFICATION_V1.md):
five TRAIN-only children over the already-researched ordinals 0–4 signal
vectors, with `execution.take_profit_pips` and `execution.stop_loss_pips` both
**null** so no global fallback can be reached and every bracket must arrive
through the intent.

**Qualification outcome (2026-08-22): GREEN.** The frozen sweep completed 5/5
children with zero failures or interruptions. Inspection of every fold found
2,776 bracketed instructions at exactly 8.00/4.00 and 2,350 TradeLog-v2 trades
whose recorded levels preserve those distances around the actual entry. Two
complete determinism verifications passed all 82 artifacts with zero observed
deviation. The fresh Explorer index listed the family, and the read-only API
successfully opened one child and its recorded per-trade brackets after the
service was reloaded. Exact run ids and evidence are recorded in the linked
qualification document. This closes the infrastructure qualification only; it
is not evidence about bracket quality and does not begin the real per-event
Zone Trader bracket mechanism.

---

## 19. Exact next action

> **Completed:** the reviewed Phase-6 branch was merged into `main` at
> `aecceef`, preserving the implementation and closeout records; the post-merge
> gate is GREEN.

The current action is owner review of OD-7A…OD-7D and proposed ADR-0025 in
[PHASE7_ZONE_TP_SELECTOR.md](PHASE7_ZONE_TP_SELECTOR.md). Do not implement that
architecture or access VALIDATION before approval. Do not touch OOS or LOCKBOX.
The three pre-evaluation failures and the negative H1 result remain permanent
research history.

---

## 20. Phase completion criteria

Phase 6 (Zone Trader) is COMPLETE when **all** of the following hold:

1. **CP-22, CP-23 and CP-24 are GREEN and owner-approved**, each after a bounded
   independent review.
2. **ID-1 and ID-2 are fixed, justified and recorded** in this document, and the
   ID-2 consistency constraint is validated in code rather than assumed.
3. `zone_trader_v13`, `zone_event_gate_v1` and `event_exposure_v1` are
   **registered, and the release-blocking rigs pass with them registered** — with
   **no rig, guard, allowlist or architecture test weakened**, and every guard
   inventory change being an addition called out in its commit message.
4. Every algorithm in §12.1 has a **hand-computed fixture with literal expected
   values**, and every §9 quirk applicable to the enabled layers is covered by at
   least one test.
5. **§12.3's seven-part ATR acceptance comparison is recorded**, and **§12.4's
   age-5000/5001 boundary pair passes**. If §12.3 showed material divergence, the
   phase does **not** complete by adjusting `w_atr` — it stops and reports (§7
   G-2).
6. **One pre-registered Plane-A hypothesis (H1) has been evaluated on TRAIN and
   recorded** — *whatever the result*. A negative result completes the phase just
   as validly as a positive one.
7. `./scripts/gate.sh` is **green, unfiltered**, at the exact reviewed commit.
8. `PROJECT_STATE.md`, `V1_ROADMAP.md` and this document record the outcome, the
   frozen parameters — **with their units, M5 versus M1, stated explicitly** —
   and the exact next action.
9. Strategy production source is confined to `features/reference.py`,
   `models/reference.py` and `decisions/reference.py`. The only platform-source
   changes are the owner-pre-authorized, result-neutral 128-MiB run-file ceiling
   and exact model-input column projection recorded in S-9. No audit record,
   dataset, view manifest or existing cache artifact was modified.

**Explicitly NOT completion criteria:** a profitable backtest · a Plane-B result
· agreement with a TradingView backtest · any money-denominated figure. Under an
open cost audit no such figure could be quoted anyway
([COST_AUDIT.md](../research/COST_AUDIT.md), ADR-0020 Amendment 1).

**Also explicitly not a completion criterion: byte-for-byte agreement with
Pine.** Four owner-approved deviations are recorded — the finite ATR
approximation (G-2), the 5000-M5-bar zone-age bound (G-3), the arithmetic grid
phases (ID-2) and the earliest-provable-completion signal instant (ID-1) — and
§12.5 enumerates them as legitimate sources of divergence. **No document may
claim semantic identity with Pine.**

**Final disposition (2026-08-23):** all nine criteria are satisfied. The
bounded independent review chain ended APPROVE at clean `811db03`; the
independent reviewer ran the full gate at clean `a200898`, and the implementing
agent repeated it at clean `811db03` and the closeout head;
CP-22/CP-23/CP-24 and Phase-6 closeout/merge are explicitly owner-approved.
Phase 6 is COMPLETE.
