# Phase 4 — Cost Audit, Simulator & Trade Evaluation — Implementation Plan

Date: 2026-08-14 · Revision 1: 2026-08-14 (pre-merge review remediation, §1.1) ·
Revision 2: 2026-08-15 (implementation closeout and RM-5) ·
Revision 3: 2026-08-15 (CP-16 review closeout) ·
Status: **PHASE 4 COMPLETE — CP-13/CP-14/CP-15/CP-16 GREEN; OWNER APPROVED**
Architecture: [docs/architecture/V1.md](../architecture/V1.md) ·
Contract: [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) (ACCEPTED
2026-08-14) · Gate: [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md)
Amendment 1 · Evidence record:
[docs/research/COST_AUDIT.md](../research/COST_AUDIT.md)

> **This document is normative for Phase-4 implementation**, in the role
> [PHASE3_RESEARCH_RUNS.md](PHASE3_RESEARCH_RUNS.md) plays for Phase 3. Where it
> and an ADR differ, the ADR governs. Where it and chat history differ, this
> document governs: **the Git repository is the source of truth.**

---

## 0. Contents and coverage map

| § | Content |
|---|---|
| 1 | Owner-approved decisions **OD-4A…OD-4K** |
| 1.1 | Review remediation **RM-1…RM-5** — correctness corrections, **not** owner decisions |
| 2 | Phase objective; methodology boundary; non-goals |
| 3 | Public vocabulary — closed type and protocol list |
| 4 | Architecture dependency rules |
| 5 | Simulator semantics — state, intents, timing, brackets, ambiguity, gaps, re-entry, reversal, fold boundaries |
| 6 | Cost model, units, and the audit gate |
| 7 | `TradeLog` contract |
| 8 | `EquityCurve` contract |
| 9 | Plane-B evaluator scope — closed metric semantics |
| 10 | Evaluation-inventory implications |
| 11 | Determinism requirements |
| 12 | Artifact persistence requirements |
| 13 | Run fingerprint and identity implications |
| 14 | Failure behaviour — the complete refusal list |
| 15 | Slices, in order |
| 16 | Formal checkpoints CP-13…CP-16 |
| 17 | Hand-computed fixture test plan |
| 18 | Leakage and timing rigs |
| 19 | Take-profit / stop-loss ambiguity tests |
| 20 | Cost arithmetic tests |
| 21 | Gap and boundary tests |
| 22 | Determinism and re-run tests |
| 23 | Harness-inversion canaries |
| 24 | Performance qualification criteria |
| 25 | Testing matrix (consolidated) |
| 26 | Architecture-guard changes — extended, never weakened |
| 27 | Documentation obligations per checkpoint |
| 28 | Decision status: resolved · remaining blockers · engineering recommendations · non-owner detail |
| 29 | Exact first implementation action |
| 30 | Phase-4 completion checklist |
| 31 | Explicit Phase-5+ deferrals |
| 32 | Status and gating |

---

## 1. Owner-approved decisions — **OD-4A…OD-4K APPROVED 2026-08-14**

These are owner decisions, recorded exactly as approved. They are not
reopened by implementation.

### OD-4A — Minimal simulator scope — **OWNER APPROVED**

The first simulator supports **only**: one symbol · one open trade at a time ·
a fixed configurable lot size · LONG / SHORT / FLAT execution · a configurable
take-profit · a configurable stop-loss · deterministic costs · a `TradeLog` ·
an `EquityCurve` · Plane-B evaluation.

Explicitly excluded, and not built as a seam, hook or placeholder: portfolio
logic · multi-symbol · multiple simultaneous trades · partial fills · trailing
stops · a general order engine · limit or order-book simulation · a
margin/leverage engine · advanced position sizing · pyramiding · netting or
hedging modes · any speculative infrastructure.

### OD-4B — Fixed-lot discrete intent semantics — **OWNER APPROVED**

`+1.0` = target LONG at the configured fixed lot · `0.0` = target FLAT ·
`-1.0` = target SHORT at the configured fixed lot · `NaN` and absent rows keep
their existing documented meanings ([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)) ·
**any other finite value, such as `0.7`, is refused explicitly.**

Fractional exposure is never mapped to account equity, a percentage of capital,
a percentage of lot size, or sign-only semantics. **Refusal is preferred to
silent reinterpretation.**

### OD-4C — Entry timing — **OWNER APPROVED**

Intent at observed row `T` → first eligible fill at the `open` of the next
observed row `T+1`. This is **execution semantics only**. It is not derived
from, and must not be justified by, Plane-A alignment
([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §10). Row semantics
are **observed-row**, never elapsed-clock, session or UTC inference.

### OD-4D — Take-profit and stop-loss are execution configuration — **OWNER APPROVED**

TP/SL belong to the execution configuration. They are **not** policy output and
**not** a new intent schema. They are fixed, configurable execution parameters
of the experiment. If a future research family needs per-decision TP/SL emitted
by the policy, that is a **future new intent schema** requiring separate
architecture review.

### OD-4E — Intrabar ambiguity: stop-loss wins — **OWNER APPROVED**

If both TP and SL are touched inside the same OHLC bar and the ordering cannot
be proven, the **stop loss wins**. The rule is explicit, deterministic and
identity-bearing: ambiguous exits are recorded per trade, and an
**ambiguity-rate metric** is exposed so results dominated by OHLC ambiguity are
visible.

### OD-4F — Gap opens use the observed open — **OWNER APPROVED**

If a bar opens beyond a take-profit or stop-loss level, the exit uses the
**actual observed open**, never the bracket price, which was not available.
Gapped exits are recorded and rate-reported.

### OD-4G — Re-entry: state-following target semantics — **OWNER APPROVED**

Intent is a target state. If a trade is stopped out while the policy continues
to emit `+1`, the simulator **may re-enter LONG** at the next eligible
execution point. A one-shot signal is expressed with a **sparse IntentFrame**.
No hidden edge-triggered semantics are invented.

### OD-4H — Fold boundaries — **OWNER APPROVED**

Each fold-policy simulation **starts flat**. Trades never carry state across
folds. Any position still open at the evaluation boundary is **closed
deterministically** according to this contract.

### OD-4I — Costs: configurable, explicitly united, evidence-gated — **OWNER APPROVED**

Costs are configurable and represented in explicit units. Current owner-provided
XAUUSD starting inputs — **spread 20 pips**, **commission $0.06 per 0.01 lot** —
are **audit inputs, not verified canonical semantics**. The platform does not
guess XAUUSD pip size, whether OHLC is bid/ask/mid, contract size,
spread-column units or sampling, whether commission is per side or round turn,
account-currency conversion, slippage, swap/financing, or broker
stop-distance/lot-step constraints.

Planning defines a **cost audit** recording each required fact as **VERIFIED,
with exact evidence** or **UNKNOWN, with the exact evidence still required**
([COST_AUDIT.md](../research/COST_AUDIT.md)). Synthetic-fixture verification may
proceed before broker facts are complete. **Real-data cost-bearing results must
not be represented as authoritative real-money estimates until the audit is
complete.**

### OD-4J — Smallest correct design — **OWNER APPROVED**

Prefer the smallest correct design. Do not create abstractions because the
roadmap once mentioned them. Specifically **removed from Phase-4 scope for lack
of current justification**: an intent-schema registry module while only one
schema is supported · a `PortfolioPolicy` object · a `GapIndex` dependency in
simulation · a generalized order engine · a slippage framework without evidence
· a position-limit framework · portfolio infrastructure · speculative plugin
surfaces.

Every new component, field, abstraction or API must justify itself through
**(1) research correctness/integrity** or **(2) broad usefulness across
materially different strategy families.** *"We may need it later"* is not
sufficient.

### OD-4K — Methodology boundary — **OWNER APPROVED**

Phase 4 adds `IntentFrame → Simulator → TradeLog / EquityCurve → Plane-B
evaluation`. **It does not change the research methodology.** The workflow
remains:

```
research idea -> Plane-A forecast evaluation -> evidence of edge
             -> DecisionPolicy -> simulator -> Plane-B
```

**The simulator must not become the primary edge-discovery mechanism.** TP/SL
tuning, execution settings and cost assumptions must not substitute for
establishing research/predictive edge in Plane A first. Recorded here, in
[ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §1, and in
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7.

---

## 1.1 Review remediation — **RM-1…RM-5, correctness corrections, not owner decisions**

An independent pre-merge review of this branch (2026-08-14) raised four
findings against the first version of this plan. They are resolved below and
throughout this document. **Nothing here is a new owner decision.** OD-4A…OD-4K
stand exactly as approved: the simulator remains small and single-position,
Plane A remains first, and every accepted methodology boundary is preserved.
Where a remediation narrows a *derived engineering claim* — ER-1 in particular
— it narrows engineering's own recommendation, not the owner's decision.

Read the three statuses in this document as follows:

| Marking | Authority |
|---|---|
| **OD-4x — OWNER APPROVED** | Product/methodology decisions the owner made. Not reopened by implementation or by review. |
| **RM-x — REVIEW REMEDIATION** | Architecture/correctness corrections review required. Recorded here and in the ADRs; owner is informed, not asked to re-decide. |
| **ER-x — ENGINEERING RECOMMENDATION** | Judgements engineering takes without escalation (§28.3). |

### RM-1 — the executable price path was over-bound (BLOCKER)

**Finding.** The plan fixed spread as a pure money charge that never affects
fill prices or bracket triggering, and stated that the unresolved bid/ask/mid
question therefore "does not silently distort exit triggering". That is not
safe as a permanent contract. If the recorded series is one-sided — say Bid —
an executable long entry occurs on the Ask side: recorded open `3000.00`,
spread `0.20`, executable entry ≈ `3000.20`, and a `5.00` take-profit distance
puts the executable TP at ≈ `3005.20`, not `3005.00`. Subtracting spread later
as money does not repair that trigger timing. The plan's own limitation L2
admitted one direction was optimistic by up to the spread, which is proof the
issue is not merely accounting.

**Resolution.** The simulator's **price-path/execution-side semantics** are
separated from its **monetary cost arithmetic** (§6.0). V1 declares a *named
provisional* price model, `recorded_path_unverified`, legal only under
unverified cost semantics; the side-dependent interpretation of fills and
bracket levels is **explicitly UNBOUND** until COST_AUDIT A6 and B3 are
VERIFIED, at which point the verified binding — written by ADR amendment —
determines how executable entry/exit prices and bracket levels are interpreted.
Synthetic fixtures may declare their own price-side semantics
(`synthetic_declared`), because a fixture defines its instrument. Real-data
simulations remain UNVERIFIED in execution as well as in money. No general
quote engine, per-side series or spread model is introduced: the whole contract
is one closed enum with two values plus one closure obligation
([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §8).

`spread_pips` accordingly does **not** sit as a free-standing field in the
frozen public `ExecutionSpec`; it lives with the other contingent fields inside
an identified `ExecutionCostBinding` whose verified semantics attach later as a
unit (§3.1, §6.0).

### RM-2 — the audit closure rule was internally impossible (BLOCKER)

**Finding.** COST_AUDIT.md said the audit closes only when every fact in §3 and
§4 is VERIFIED, while §4 deliberately contains items classified UNKNOWN / NOT
MODELLED or NOT APPLICABLE (slippage, swap, latency, corporate actions). The
rule could never be satisfied.

**Resolution.** Facts are split into **closure-required (CR)** — the facts V1's
implemented arithmetic and execution semantics consume — and **non-blocking
declared limitations (NB)** — effects V1 does not model, constraints it does
not enforce, and the `spread` column it does not read. The audit closes when
every CR fact is VERIFIED with named evidence, every NB item is explicitly
classified, and the verified binding exists. Closure never claims the NB
effects are zero ([COST_AUDIT.md](../research/COST_AUDIT.md) §1.1).

### RM-3 — `VERIFIED` was not fail-closed (BLOCKER)

**Finding.** A config could declare `cost_semantics = VERIFIED` while the audit
was OPEN, and the plan treated that as a documentation error rather than a
refusal. A platform must not let user input self-certify an evidence state the
repository has not reached.

**Resolution.** One frozen constant in `core` —
`VERIFIED_COST_BINDING: ExecutionCostBinding | None`, `None` while the audit is
OPEN. `VERIFIED` requires that constant to exist and the declared binding to
equal it field-for-field, including a platform-recomputed digest; otherwise the
run is refused at config resolution (§6.4, §14 R10–R13). No service, database
or registry is added, and no Markdown is parsed at runtime.

### RM-4 — Slice 4A / CP-13 versus the exact next action

**Finding.** The plan ordered `4A → CP-13 → 4B` while PROJECT_STATE named 4B as
the exact next implementation action, leaving it unclear whether CP-13 requires
external audit closure.

**Resolution.** CP-13 is the **evidence-classification gate**, and it is
**GREEN** (§16): the register exists, every fact is classified CR or NB, every
binding field maps to a register row, and no plausible default exists anywhere.
**Slice 4A's baseline record is COMPLETE**; COST_AUDIT remains **OPEN** for
external evidence, which is collected in parallel and blocks no code. Audit
closure is a separate, later gate, **CP-13C**, which is *not* a Phase-4
completion condition. The exact next implementation action is therefore
**Slice 4B**, consistently in §15, §16, §29, §32 and PROJECT_STATE.

### RM-5 — the equity-state label double-counted costs on paper

**Finding.** During implementation, §5.2 labelled `realized` as cumulative
**net** money from closed trades while §8.1 subtracted `charged` again. Read
literally, that wording would count costs twice, contrary to the normative
per-trade identity `net_pnl = gross_pnl - spread_cost - commission_cost`.

**Resolution.** The state variable is named `realized_gross`, and the dense
curve is `realized_gross - charged + unrealized`. Costs are recognized once,
at entry (§6.3), and every completed trade still records the exact gross, cost
and net columns. This is a correctness repair to engineering notation, not a
new owner decision; the implementation and hand-computed fixtures pin it.

---

## 2. Objective, methodology boundary, non-goals

### 2.1 Objective

Make **measured execution realism** available to hypotheses that already
survived Plane A: convert an `IntentFrame` into an honest trade record under
explicitly declared execution and cost assumptions, and evaluate it — with the
evidence status of every cost assumption permanently attached to the result.

### 2.2 Methodology boundary (normative)

Plane A is where hypotheses live and die. Plane B answers exactly one further
question: *does the edge Plane A found survive the execution and cost
assumptions we have written down?*

Three concrete consequences that implementation must preserve:

1. **A Plane-B run with no Plane-A evidence behind it is a methodology error**,
   not a shortcut. The platform does not mechanically prevent it — mechanisms
   cannot judge research intent — but the policy is normative and the
   evaluation inventory makes the attempt visible.
2. **TP/SL are execution configuration (OD-4D), so tuning them is a sweep of
   runs, not a sweep inside a run** (§10). Every variant is counted against the
   multiple-testing denominator. This is deliberate friction.
3. **A better backtest is not evidence.** Where Plane A and Plane B disagree,
   [ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) already
   says the disagreement is information about execution assumptions — and while
   the cost audit is OPEN those assumptions are unverified in **money** (the
   cost facts) and in **execution** (the price side that decides where fills
   and bracket levels sit, §6.0), so Plane B is the *weaker* evidence of the
   two, not the stronger.

### 2.3 Non-goals (explicit)

Not built in Phase 4, in any form, including as an inert seam:

portfolio construction · multi-symbol or cross-asset execution · more than one
open position · partial fills · trailing stops · break-even stops · time-based
exits · pyramiding · netting or hedging account modes · limit, stop, or
stop-limit order placement · order-book or queue-position simulation · margin,
leverage, margin calls or liquidation · equity-dependent or volatility-scaled
position sizing · a slippage model or framework · a position-limit framework ·
per-decision policy-emitted brackets · an intent-schema registry · a
`PortfolioPolicy` object · a `GapIndex` dependency in `simulation` · a Sharpe
or any return-ratio statistic (§9.3) · percentage returns or percentage
drawdown (§8.2) · live or paper trading · broker connectivity · sweeps
(Phase 5) · the SQLite run index (Phase 5) · any deflated or
multiple-testing-corrected statistic (Phase 5, blocked by
[ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md)).

---

## 2.4 Amendment — per-trade brackets (2026-08-22)

**Normative source: [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md)
Amendment 1.** OD-4A…OD-4K below are the historical Phase-4 record and are not
rewritten. What follows states, once, where this document is now narrower than
the implementation.

- **OD-4D** said TP/SL are execution configuration and *"if a future research
  family needs per-decision TP/SL emitted by the policy, that is a future new
  intent schema requiring separate architecture review."* That family arrived
  (Zone Trader's per-event ATR/zone-edge stop and R-multiple target), and the
  new schema is `bracketed_exposure_intent_v1`. TP/SL are now **either**
  execution configuration **or** a per-instruction decision, resolved per side
  by the rule in ADR-0023 Amendment 1 clause 2.
- **§3.3** — `SUPPORTED_INTENT_SCHEMA_IDS` is now
  `("target_exposure_intent_v1", "bracketed_exposure_intent_v1")`. The
  simulator's name, signature and purity are unchanged.
- **§3.1** — `ExecutionSpec` is unchanged, field for field. `TradeLog` gains
  `take_profit_price` and `stop_loss_price` (float64, `NaN` = that side had no
  bracket), so §7.1's table is fourteen columns.
- **§3.4** — "Intent-schema registry module" remains **not created**: the
  frozen literal tuple above is the whole resolution mechanism.
- **§5.4** — step 1's "set brackets" is now "resolve each side, then set", and
  the resolved levels are immutable for the life of the trade. Every other rule
  in §5.4, §5.5, §5.6 and §5.7 is unchanged, including the ordering rule.
- **§7.2** — the counters are unchanged. A bracket instruction on a row that
  repeats the current target is ignored, because such a row is already a no-op.

Everything in §2.3's non-goals list still holds: no trailing stops, no partial
exits, no order engine, no mutable brackets, no pyramiding, no portfolio, no
position sizing.

---

## 3. Public vocabulary — **closed**

**This section is closed. Implementation adds exactly these public types and no
others.** A type that appears to be missing is a planning defect to raise, not
a coding decision — the same rule Phase 3 §2 applied. Public columns are
read-only NumPy arrays; no PyArrow/pandas/Polars/DuckDB type appears in any
public signature ([ADR-0003](../adr/ADR-0003-columnar-hot-path.md)).

### 3.1 Core-owned public types

```
CommissionBasis (StrEnum, core)
    PER_SIDE | PER_ROUND_TURN            # explicit; no default

CostSemanticsStatus (StrEnum, core)
    UNVERIFIED | VERIFIED                # explicit; no default
    # VERIFIED is fail-closed: constructible only against VERIFIED_COST_BINDING
    # (§6.4). While the audit is OPEN, UNVERIFIED is the only reachable value.

ExecutionPriceModel (StrEnum, core)                   # RM-1
    RECORDED_PATH_UNVERIFIED             # V1 provisional: fills and bracket
                                         # levels read from the recorded OHLC
                                         # series as stored; its price side is
                                         # UNKNOWN (COST_AUDIT A6).
                                         # Legal ONLY with UNVERIFIED semantics.
    SYNTHETIC_DECLARED                   # the fixture defines its instrument and
                                         # declares its rows ARE the executable
                                         # path for both directions. TEST-ONLY,
                                         # unreachable from config resolution.
    # No third value in V1. The verified, side-dependent model is written by the
    # ADR amendment that closes the cost audit (COST_AUDIT §8 step 3).

TradeExitReason (StrEnum, core)
    TAKE_PROFIT | STOP_LOSS | TARGET_CHANGE | BOUNDARY_FORCED_CLOSE
    # stored in TradeLog as int8 codes with a documented, pinned mapping

ExecutionCostBinding (frozen, core, public construction)   # RM-1 / RM-3
    binding_id: str, non-empty                        # identity of this binding
    binding_digest: str | None                        # sha256 over the binding's
                                                      # canonical document,
                                                      # excluding this field;
                                                      # REQUIRED when VERIFIED,
                                                      # None when UNVERIFIED
    cost_semantics: CostSemanticsStatus
    execution_price_model: ExecutionPriceModel
    contract_size: float > 0, finite                  # base units per 1.00 lot
    price_units_per_pip: float > 0, finite            # price units per pip
    account_currency_per_quote_currency: float > 0, finite
    spread_pips: float >= 0, finite                   # money charge, once per round trip
    commission_per_lot: float >= 0, finite            # account currency, per 1.00 lot
    commission_basis: CommissionBasis
    # One object for every field whose MEANING is contingent on an unestablished
    # fact, so verified semantics attach later as a unit and no component claims
    # them early (COST_AUDIT §3.0). No defaults.

VERIFIED_COST_BINDING: ExecutionCostBinding | None = None   # core, frozen constant
    # The whole fail-closed mechanism (§6.4). None while COST_AUDIT is OPEN;
    # created by the commit that closes it. Not a registry: one constant, no
    # lookup, no registration, no plugin mechanism. Changing it is an
    # architecture change under CLAUDE.md change control.

ExecutionSpec (frozen, core, public construction)
    execution_id: str                    # validated literal:
                                         # "bracket_single_position_v1"
    version: int > 0
    lot_size: float > 0, finite                       # broker lots
    take_profit_pips: float > 0 finite | None         # None = no take profit
    stop_loss_pips: float > 0 finite | None           # None = no stop loss
    cost_binding: ExecutionCostBinding                # RM-1: all contingent
                                                      # semantics live here
    # every field explicit; NO defaults anywhere; canonical params document
    # for identity built exactly like a plugin params mapping, with the binding
    # nested as its own canonical block.
    # Deliberately absent: slippage, position limits, decision delay, spread
    # model selector, per-side spread asymmetry, initial equity. The FILL
    # REFERENCE is deliberately not a knob either: it is named by
    # execution_price_model and is UNBOUND for real data until the audit
    # verifies the price side (RM-1).

SimulationRequest (frozen, core, private construction)
    keys -> read-only int64, strictly increasing      # the execution axis
    unit: TimeUnit · clock_domain: ClockDomain
    num_rows: int >= 2
    source_identities: tuple[DatasetIdentity, ...]
    open / high / low / close -> read-only float64
    # built ONLY in experiments from an already-issued MarketFrame, exactly as
    # FeatureRequest (Phase 2) and OutcomeRequest (Phase 3) are.
    # Validated: all four columns finite; low <= min(open, close);
    # high >= max(open, close); low <= high. The simulator never selects rows.

TradeLog (frozen, core, private construction)          # §7
EquityCurve (frozen, core, private construction)       # §8
```

### 3.2 Core-owned public protocols

```
TradeEvaluator (Protocol)
    evaluator_id: str · version: int · params: Mapping[str, ParamValue]
    metric_names: tuple[str, ...]
    comparison_tolerance: ToleranceSpec | None
    evaluate(trade_log: TradeLog,
             equity_curve: EquityCurve) -> Mapping[str, MetricValue]
    # No declared-column requirements: unlike ModelOutputFrame, both inputs
    # have CLOSED schemas, so there is nothing for a declaration to protect
    # against (ADR-0023 §10). Every metric still reports effective_samples.
```

### 3.3 The simulator itself

One module-level function in `simulation/`, not a protocol and not a registry
entry:

```
SUPPORTED_INTENT_SCHEMA_IDS: frozen tuple = ("target_exposure_intent_v1",)

simulate_bracket_single_position_v1(
        request: SimulationRequest,
        intents: IntentFrame,
        spec: ExecutionSpec,
        *, fold_index: int, policy_origin: StageOrigin,
) -> tuple[TradeLog, EquityCurve]
```

Pure: no I/O, no clock read, no randomness, no cache, no store, no grant, no
logging side effect. Same inputs ⇒ bitwise-identical outputs.

### 3.4 Types deliberately NOT created

| Not created | Why |
|---|---|
| `Simulator` protocol / simulator registry | one simulator exists; a protocol with one implementation is a guess about the second |
| Intent-schema registry module | one schema, one simulator — nothing to resolve ([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §9) |
| `PortfolioPolicy` | nothing to pass through under one symbol and one open trade ([ADR-0015](../adr/ADR-0015-v1-single-symbol-scope.md) Amendment 1) |
| `OrderRequest` / `Fill` / `OrderBook` / `Position` object | no order-level intent exists to carry |
| `SlippageModel`, `PositionLimits`, `SpreadModel` | no evidence to bind (COST_AUDIT §4 D1); a framework without evidence is decoration |
| A quote engine — per-side price series, synthetic ask construction, half-spread decomposition | RM-1 is prevented by *naming* the price model, not by building one. The decomposition depends on COST_AUDIT A6/B3, which are UNKNOWN; building it now would be the invented semantic ADR-0020 forbids |
| A verified `ExecutionPriceModel` value | cannot be written before A6 and B3 are VERIFIED; created by the closing ADR amendment (COST_AUDIT §8) |
| A cost-binding registry, service or lookup | one symbol, one account, one binding: a single `None`-by-default constant is the smallest thing that fails closed (§6.4) |
| `AccountState` / initial-equity concept | no verified account base; §8.2 |
| `TradeEvaluator` declared-column requirements | closed artifact schemas; §3.2 |
| A `SimulationContext` god-object | nothing needs it |
| A second span/axis descriptor | `SimulationRequest` carries the execution axis; `ViewSpan` still describes the view |
| Plane-B metric library | two closed evaluators; §9 |

---

## 4. Architecture dependency rules — **exact**

The layer budget in `tests/test_architecture.py` **does not change**. Phase 4
introduces no new layer and no new allowed edge.

```
core        <- imports nothing internal (unchanged)
simulation  <- imports ONLY core                       (already declared)
evaluation  <- imports ONLY core                       (unchanged)
artifacts   <- imports ONLY core                       (unchanged)
experiments <- composition root; may import all        (unchanged)
```

Consequences that implementation must respect exactly:

1. **`simulation` may not import `data`.** `GapIndex` lives in `data/gaps.py`,
   so it is architecturally unreachable from the simulator — and unnecessary:
   execution happens only at observed rows, so a bar that does not exist is not
   a fill opportunity, and no gap oracle can add information to that. This
   supersedes the roadmap's former Phase-4 acceptance wording (§26, and the
   corrected roadmap entry).
2. **`simulation` may not import `models`, `decisions`, `features`,
   `representations`, `outcomes`, `evaluation`, `artifacts` or `experiments`.**
   It consumes frames and specs only, which is
   [V1.md](../architecture/V1.md) §2's prohibited-dependency rule verbatim.
3. **Plane-B evaluators live in `evaluation/`**, alongside the Plane-A ones,
   and likewise import only `core`.
4. **`SimulationRequest` is constructed only in `experiments`**, from an
   already-issued, already-authorized `MarketFrame`. The simulator holds no
   `AccessGrant`, resolves no dataset, and cannot widen its own row range.
5. **Persistence happens only in `experiments`**, through `artifacts`, exactly
   as for features, model output and fitted state today.
6. No civil-time, timezone, UTC or calendar machinery enters `simulation`;
   existing guards extend to the new modules.

---

## 5. Simulator semantics — **closed**

### 5.1 The execution axis

The execution axis is exactly the observed rows of **one fold's evaluation
range**, in ascending key order, positions `0 … N-1`. `SimulationRequest`
carries them; nothing else does. `N >= 2` is required (§14 R6): with one row
there is no next observed row and therefore no eligible fill.

The `IntentFrame`'s keys must be a (possibly strict, possibly empty) **subset**
of the execution axis. A key outside the axis is a refusal.

### 5.2 Simulator state

```
position    : FLAT | LONG | SHORT                       # starts FLAT
entry_key   : int64 | none
entry_price : float64 | none
tp_level    : float64 | none                            # from entry, if configured
sl_level    : float64 | none                            # from entry, if configured
realized_gross : float64 = 0.0  # cumulative gross money from closed trades
charged        : float64 = 0.0  # cumulative costs recognized (§6.3)
```

Bracket levels are derived once, at entry, from the **executable entry fill
price under the declared `execution_price_model`** (§6.0). Under V1's
provisional `RECORDED_PATH_UNVERIFIED` model that price is the recorded
`open[i]` itself, so the arithmetic below reads exactly as written; under a
future verified side-bound model the executable entry price — and therefore
both levels — may differ, which is precisely why the model is named rather than
assumed (RM-1):

```
LONG :  tp_level = entry_price + take_profit_pips * price_units_per_pip
        sl_level = entry_price - stop_loss_pips   * price_units_per_pip
SHORT:  tp_level = entry_price - take_profit_pips * price_units_per_pip
        sl_level = entry_price + stop_loss_pips   * price_units_per_pip
```

A `None` distance means that side has no bracket and is never triggered.

### 5.3 Supported intent values (OD-4B)

| Value at an observed row | Target |
|---|---|
| `+1.0` | LONG at `lot_size` |
| `0.0` | FLAT |
| `-1.0` | SHORT at `lot_size` |
| `NaN` | abstain — hold the current state |
| row absent | no statement — hold the current state |
| any other finite value | **refusal before any simulation begins** (§14 R1) |

`NaN` and absence are behaviourally identical *for state*, and remain
**distinct in the record**: the `TradeLog` counts abstain rows and absent rows
separately (§7.2), preserving
[ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)'s
trichotomy in observable output rather than by assertion.

The comparison against `±1.0` and `0.0` is **exact IEEE equality**; `-0.0`
equals `0.0` and is therefore FLAT, stated explicitly so it is not
rediscovered as a bug.

### 5.4 Per-row algorithm — exact and ordered

Throughout this section `open[i]`, `high[i]`, `low[i]` and `close[i]` mean **the
executable prices under the declared `execution_price_model`** (§6.0). Under
V1's provisional model they are the recorded columns as stored, with the price
side UNKNOWN; the ordering, ambiguity and gap rules below are about *which
observed condition* prices a fill, and they settle nothing about which side of
the market that observation represents (RM-1).

For each position `i` in `0 … N-1`:

**Step 1 — intent execution, at `open[i]`.** Let `v` be the intent value at
position `i-1` (skipped entirely when `i == 0`; `NaN` and absence hold state).
Let `target` be the state `v` names.

- `target == position` → nothing happens.
- `target != position`:
  - if a position is open, close it at `open[i]` with reason `TARGET_CHANGE`;
  - if `target != FLAT`, open the new position at `open[i]`, set brackets.
  - A **reversal** (`LONG → SHORT` or `SHORT → LONG`) performs both at the same
    row and the same price, producing **two** `TradeLog` records with costs
    charged for each. The one-open-trade invariant is never violated.

**Step 2 — bracket evaluation on row `i`** (only if a position is open after
step 1, and only if at least one bracket is configured):

1. **Gap check on `open[i]` first.** For LONG: `open[i] <= sl_level` → exit at
   `open[i]`, reason `STOP_LOSS`, `gapped_exit = True`; else
   `open[i] >= tp_level` → exit at `open[i]`, reason `TAKE_PROFIT`,
   `gapped_exit = True`. SHORT is the mirror image. A position opened at this
   row cannot gap out of its own entry price, so the check is a no-op for
   fresh entries.
2. Otherwise evaluate touches: LONG `sl_touched = low[i] <= sl_level`,
   `tp_touched = high[i] >= tp_level`; SHORT `sl_touched = high[i] >= sl_level`,
   `tp_touched = low[i] <= tp_level`.
   - both touched → **exit at `sl_level`, reason `STOP_LOSS`,
     `ambiguous_exit = True`** (OD-4E);
   - only SL → exit at `sl_level`;
   - only TP → exit at `tp_level`;
   - neither → the position stays open.

A position may open in step 1 and close in step 2 on the same row.

**Step 3 — boundary close.** At `i == N-1`, after steps 1 and 2, any still-open
position is closed at `close[N-1]` with reason `BOUNDARY_FORCED_CLOSE`
(OD-4H, §5.7).

**Step 4 — mark to market.** `equity[i]` is written for every row (§8).

**Ordering rule, stated once because it is load-bearing:** step 1 precedes step
2 within a row. When a carried position both receives a target change and gaps
through a bracket at the same `open`, the exit price is identical either way
(`open[i]`); only the recorded reason differs, and `TARGET_CHANGE` is the
honest label because the instruction predates the bar.

### 5.5 Entry timing and the final row (OD-4C)

An explicit statement at position `i` is considered for execution at
`open[i+1]`. Therefore:

- **An explicit statement at the final position `N-1` is never executed.**
  There is no next observed row inside the evaluation range, and reading one
  row beyond `eval_end` would breach the point-in-time boundary
  ([ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 2). Any explicit
  final-row statement — LONG, FLAT, SHORT or an explicit `NaN` abstention — is
  counted as `unexecuted_final_row_intents` (§7.2), never silently dropped.
  This is a diagnostic boundary counter: counting `NaN` records only that an
  explicit statement was present without an eligible next row; it does **not**
  mean the abstention would have opened, closed or otherwise changed a trade.
- No fill price is ever taken from row `i` for an intent observed at row `i`,
  and no fill price is ever taken from a row later than the fill row.

### 5.6 Re-entry and one-shot signals (OD-4G)

Intent is a **target state**, never an edge trigger. After a stop-out at row
`i`, if the intent at row `i` (or any later row) is still `+1.0`, a new LONG
opens at `open[i+1]`. There is no cooldown, no "wait for a flat first" rule and
no de-duplication.

A researcher who wants a **one-shot** instruction emits a **sparse
IntentFrame** containing only the rows where an instruction exists — which is
exactly what
[ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)'s sparsity
amendment is for. Note the consequence: with a sparse frame, the state is held
between statements, so a single `+1.0` followed by silence means "stay long
until something says otherwise", and a stop-out under that frame *will*
re-enter at the next row only if a later `+1.0` row exists. Held state does not
re-trigger an entry by itself.

### 5.7 Fold boundaries (OD-4H)

Simulation runs **per `(fold, policy)` pair** and each such simulation starts
FLAT with `realized_gross = charged = 0.0`. No position, cost or equity state crosses
a fold boundary or a policy boundary, even when folds overlap
(`step_rows < eval_rows` is legal per
[PHASE3_RESEARCH_RUNS.md](PHASE3_RESEARCH_RUNS.md) §2.7).

A position open at the last observed row is closed at that row's `close`,
flagged `BOUNDARY_FORCED_CLOSE`, and rate-reported (§9.1). The rate matters:
a fold whose results are dominated by boundary closes is measuring the fold
geometry, not the strategy.

---

## 6. Execution-price semantics, cost model, units, and the audit gate

### 6.0 The executable price path — declared, not assumed (RM-1)

Two questions are kept strictly apart, because merging them is what made the
first version of this plan unsafe:

| Question | V1 answer |
|---|---|
| **Which prices does a trade execute at, per direction — and therefore which series do the bracket levels sit on?** | Bound **provisionally, under a name**; the side-dependent part is **UNBOUND** |
| **How much money is charged for spread and commission?** | Bound **fully** (§6.1–§6.2) |

`ExecutionCostBinding.execution_price_model` names the answer to the first
question and takes exactly two values in V1:

| Value | Meaning | Where legal |
|---|---|---|
| `RECORDED_PATH_UNVERIFIED` | Entry fills, exit fills and both bracket levels are read from the recorded OHLC series exactly as stored. **The price side of that series is UNKNOWN** (COST_AUDIT A6). | Only with `cost_semantics = UNVERIFIED`. This is the V1 real-data model. |
| `SYNTHETIC_DECLARED` | The fixture defines its own instrument and declares that its rows *are* the executable path for both directions. | Test-only construction; **unreachable from config resolution** (§14 R12), architecture-guarded (§26). |

**The known defect of the provisional model, stated plainly.** If the recorded
series is one-sided — say Bid — a long entry executes on the Ask side. With a
recorded open of `3000.00`, a spread of `0.20` and a take-profit distance of
`5.00`, the executable entry is ≈ `3000.20` and the executable take-profit is
≈ `3005.20`, not `3005.00`. The error is in **trigger timing**, and charging
the spread as money afterwards does not repair it. That is why the model is
provisional, why it is illegal under verified semantics, and why real-data
Plane-B results are unverified in *execution* as well as in *money*.

**What is explicitly UNBOUND, and when it binds.** The mapping from the
recorded series to the executable series per direction — and therefore whether
bracket levels derive from the recorded `open` or from a side-adjusted
executable entry — is left open until COST_AUDIT **A6** (price side) and **B3**
(spread symmetry) are VERIFIED. At that point the closing ADR amendment writes
the verified model's exact executable-price rules and **re-derives the spread
money charge in the same act**, so that a spread expressed in the price path is
not also charged in full as money (COST_AUDIT §7 L2a). Until then **no document
in this repository may state that the recorded price path is the executable
price path as a settled fact.**

**What is deliberately not built:** per-side price series, synthetic ask
construction, a spread model, a quote engine, or any framework that would have
to be designed before the facts defining it exist (§3.4). The entire contract
is one enum, one closure obligation and one honest limitation.

### 6.1 Money conversion — one derived quantity, stated once

All cost fields below are read from `spec.cost_binding` (§3.1); `lot_size` is
read from `spec`.

```
money_per_price_unit = contract_size
                     * lot_size
                     * account_currency_per_quote_currency
```

### 6.2 Per-trade arithmetic — exact and ordered

```
direction        = +1 for LONG, -1 for SHORT
gross_pnl        = direction * (exit_price - entry_price) * money_per_price_unit
spread_cost      = spread_pips * price_units_per_pip * money_per_price_unit
commission_cost  = commission_per_lot * lot_size
                   * (1.0 if commission_basis is PER_ROUND_TURN else 2.0)
net_pnl          = gross_pnl - spread_cost - commission_cost
```

- `spread_cost` is the **full configured spread, charged once per round trip**.
- `commission_per_lot` is already in **account currency** and is therefore not
  multiplied by the currency factor.
- Every multiplication order above is normative, because float addition and
  multiplication are order-sensitive and determinism is exact (§11).

### 6.3 Under the provisional model, costs are money charges only

While `execution_price_model = RECORDED_PATH_UNVERIFIED`, costs are **never**
folded into fill prices: they are money amounts in their own `TradeLog`
columns, and bracket triggering is evaluated on the recorded prices.

Rationale, corrected (RM-1): adjusting fill prices by a half-spread today would
**pick** an answer to COST_AUDIT A6, and picking wrong moves exit triggering,
not just accounting. Charging the spread as money and leaving the price path
untouched is the smaller commitment of the two — but it is **not neutral, and
not permanent**. Under a one-sided series it is optimistic for one direction by
up to the spread in trigger timing (COST_AUDIT §7 L2), which is a declared
limitation of a **named provisional model** (§6.0), not a proof that the
question has been avoided. When the verified model arrives, the executable
price offsets and this money charge are re-derived together (COST_AUDIT §7
L2a).

Both cost components of a trade are **recognized at the trade's entry row** —
they are fully determined at entry, and recognizing them immediately is the
conservative direction for drawdown.

### 6.4 The audit gate — fail-closed (OD-4I, ADR-0020 Amendment 1, RM-3)

Every `ExecutionCostBinding` declares `cost_semantics` explicitly, and the
platform **enforces** it rather than trusting it.

**The mechanism, in full.** `core` holds one frozen module constant:

```
VERIFIED_COST_BINDING: ExecutionCostBinding | None = None    # None while the audit is OPEN
```

`cost_semantics = VERIFIED` resolves only when **all** of the following hold;
otherwise the run is **refused at config resolution** (§14 R10–R11, with R13 pinning digest presence):

1. `VERIFIED_COST_BINDING is not None`;
2. the spec's `cost_binding` **equals it field-for-field**, including
   `binding_id`, `execution_price_model` and every unit and cost value;
3. the declared `binding_digest` equals the digest the platform **recomputes**
   from the binding's own canonical document (declared-then-recomputed, so a
   stale pin refuses instead of silently applying); and
4. the binding's `execution_price_model` is a **verified** model —
   `RECORDED_PATH_UNVERIFIED` can never back verified semantics (RM-1).

Because the constant is `None` in V1, **`UNVERIFIED` is the only reachable
value today**: no config file, CLI argument, environment variable or edited
artifact can produce a verified run. That is the invariant — evidence state is
enforced by the repository, never accepted from user input.

**Why this shape.** It adds no service, database or registry; it parses no
Markdown at runtime (prose must not be load-bearing, and a heading is too easy
to edit); it is one constant plus an equality check, which is the smallest
thing that fails closed. Creating or changing the constant is an
architecture-level change under `CLAUDE.md` change control, performed by the
commit that closes [COST_AUDIT.md](../research/COST_AUDIT.md) (§8 there),
together with the ADR-0023 amendment that binds the executable price path.

**Nothing is retroactively upgraded.** `binding_id` and `binding_digest` enter
the resolved config and therefore `experiment_fingerprint`, so closure yields
**new** run identities. Runs recorded while the audit was OPEN keep
`cost_semantics: unverified` permanently — re-run under the verified binding,
never re-labelled (`COMPLETE` run directories are immutable anyway, §12).

**The rest of the gate is unchanged:**

- `UNVERIFIED` runs execute normally and are **permanently marked** in the
  `RunManifest` (`cost_semantics_unverified: true`), in the stored simulation
  summary, and in every Plane-B inventory row.
- **Money-denominated output of an `UNVERIFIED` run may not be quoted as a
  real-money estimate, and may not be used as the deciding evidence for a
  promotion, OOS or lockbox decision**
  ([RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7). Under RM-1 the same
  applies to its **execution** semantics: its trigger timing is unverified too.
- The gate constrains **representation and promotion**, not implementation:
  synthetic-fixture verification (under `SYNTHETIC_DECLARED`) and end-to-end
  qualification proceed while the audit is OPEN.

---

## 7. `TradeLog` contract

### 7.1 Columns — one row per completed trade, chronological

| Column | dtype | Meaning |
|---|---|---|
| `entry_key` | int64 | observed key of the entry fill row |
| `exit_key` | int64 | observed key of the exit row |
| `direction` | int8 | `+1` LONG, `-1` SHORT |
| `entry_price` | float64 | observed fill price (`open` of the entry row) |
| `exit_price` | float64 | observed or bracket exit price per §5.4 |
| `exit_reason` | int8 | `TradeExitReason` code, mapping pinned by test |
| `ambiguous_exit` | bool | both brackets touched in one bar; SL taken (OD-4E) |
| `gapped_exit` | bool | exit priced from a gapped `open` (OD-4F) |
| `gross_pnl` | float64 | §6.2 |
| `spread_cost` | float64 | §6.2 |
| `commission_cost` | float64 | §6.2 |
| `net_pnl` | float64 | §6.2 |

All arrays are read-only, equal-length, and ordered by `entry_key` ascending.
`entry_key < exit_key` is not required (a trade may open and close on the same
row); `entry_key <= exit_key` is.

### 7.2 Frame-level fields

`unit` · `clock_domain` · `source_identities` · `fold_index` ·
`policy_origin: StageOrigin` · `intent_schema_id` · `execution_spec:
ExecutionSpec` · `num_trades` · `execution_rows`, plus the counters that are
**not derivable from the trade rows**:

| Counter | Why it cannot be derived |
|---|---|
| `intent_rows_long` / `intent_rows_flat` / `intent_rows_short` | intents that changed nothing leave no trade |
| `intent_rows_abstain` (explicit `NaN`) | ADR-0009 trichotomy; leaves no trade |
| `intent_rows_absent` | ADR-0009 trichotomy; leaves no trade |
| `unexecuted_final_row_intents` | explicit statements present at the final boundary, including explicit `NaN`; diagnostic only, with no claim that the statement would change a trade (§5.5) |

Counters that **are** derivable from the columns (ambiguous, gapped and
boundary-close counts) are deliberately **not** stored as fields — the
evaluators derive them, so there is exactly one source of truth per fact.

---

## 8. `EquityCurve` contract

### 8.1 Shape

Dense over the execution axis: `keys` (int64, identical to the
`SimulationRequest` axis) and one float64 column `equity`, plus `unit`,
`clock_domain`, `source_identities`, `fold_index`, `policy_origin`, `num_rows`.

```
equity[i] = realized_gross_after_row_i
          - costs_recognized_through_row_i
          + direction * (close[i] - entry_price) * money_per_price_unit   # if open
```

The curve is **mark-to-market on each row's `close`**, and the pre-first-row
baseline is exactly `0.0`.

### 8.2 What the curve deliberately is not

`equity` is **cumulative net PnL in account currency**, not an account balance.
There is no initial equity, no margin and no leverage, because there is no
verified account base to invent one from, and because fixed-lot sizing (OD-4A)
means nothing in the simulator depends on account size.

Therefore Phase 4 reports **absolute** drawdown in account currency and
computes **no percentage return, no percentage drawdown and no Sharpe-like
ratio** (§9.3). A ratio statistic computed off an invented base would be read
as a Sharpe ratio, which is precisely the falsely-precise number
[ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) exists to prevent.

Intrabar equity excursions are not observable from OHLC, so drawdown measured
here is a **row-close** drawdown and understates intrabar pain. Stated as a
limitation, not modelled.

---

## 9. Plane-B evaluator scope — **closed**

Exactly two Plane-B evaluators, both reference plugins, neither an assumption
of the `TradeEvaluator` contract. **This is not a metric library and must not
grow into one.** Both receive both artifacts (§3.2).

### 9.1 `trade_summary_v1`

| Aspect | Rule |
|---|---|
| Params (explicit, no defaults) | `min_effective_trades: int >= 0` |
| `effective_samples` | `num_trades`, for every emitted metric |
| Below minimum | every metric except `trade_count` → `NaN` |
| Zero trades | rates → `NaN` (0/0 is undefined); sums → `NaN` under the minimum rule |
| Summation order | chronological over `TradeLog` rows, exactly (§11) |

Emits: `trade_count` · `gross_pnl` (sum) · `total_cost` (sum of
`spread_cost + commission_cost`) · `net_pnl` (sum) · `win_rate`
(`count(net_pnl > 0) / num_trades`; a trade with exactly `0.0` net is counted
in the denominator and not as a win, stated explicitly) ·
`ambiguous_exit_rate` · `gapped_exit_rate` · `boundary_forced_close_rate`
(`count(exit_reason == BOUNDARY_FORCED_CLOSE) / num_trades`).

The three rates are the **honesty metrics** OD-4E, OD-4F and OD-4H require: a
result dominated by unresolvable bars, gapped exits or boundary closes is
dominated by an assumption, and the number says so on the same screen as the
PnL.

### 9.2 `equity_drawdown_v1`

| Aspect | Rule |
|---|---|
| Params (explicit, no defaults) | `min_effective_rows: int >= 1` |
| `max_drawdown` | `max over i of (running_peak(i) - equity[i])`, where `running_peak(i) = max(0.0, equity[0..i])` — the `0.0` baseline is included, so a curve that only falls still reports a drawdown |
| Units | account currency, absolute, `>= 0` |
| Below minimum | `NaN` |
| `effective_samples` | `num_rows` |

Emits exactly `max_drawdown`. It earns a separate evaluator because it is the
only metric that needs the curve rather than the trade rows, and it proves the
two-artifact Plane-B path end to end.

### 9.3 Deliberately excluded metrics

Sharpe or any PnL-ratio statistic · percentage return · percentage drawdown ·
profit factor · expectancy · turnover · exposure time · per-trade
holding-period statistics · any deflated or multiple-testing-adjusted number
(Phase 5, blocked by ADR-0011). Each is either unavailable without an invented
account base, or a growth of the metric library with no current consumer.

---

## 10. Evaluation-inventory implications

Plane A is **unchanged**: `F` fitted states × `S` OutcomeSpec instances × `E`
Plane-A evaluators = `F·S·E` rows, **with no policy dimension**
([PHASE3_RESEARCH_RUNS.md](PHASE3_RESEARCH_RUNS.md) §3G).

Plane B **adds** rows and **does** carry the policy dimension — this is the
case [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) Amendment 1
anticipated with "where a decision plane was evaluated":

```
Plane-B evaluation identity =
      model / FittedState identity
    + ModelOutputFrame artifact identity
    + DecisionPolicy identity                    <-- present, unlike Plane A
    + ExecutionSpec identity (canonical params, incl. the nested cost binding:
      cost_semantics, binding_id, binding_digest, execution_price_model)
    + TradeLog / EquityCurve artifact identity
    + Plane-B evaluator identity
```

With `F` fitted states, `P` policies, `B` Plane-B evaluators and **exactly one
`ExecutionSpec` per run** (§13), a run records `F·P·B` Plane-B rows in addition
to its `F·S·E` Plane-A rows. Both sets live in the same
`evaluation_inventory.json`, distinguished by an explicit `plane` field
(`"A"` / `"B"`).

Policy executions continue to be recorded in the `RunManifest` as executed
work. A run that simulates but never evaluates Plane B is descriptive for
Plane B, exactly as ADR-0010 defines for Plane A.

**Denominator consequence, stated because it is the point:** a TP/SL sweep is a
sweep of runs, each contributing `F·P·B` rows. Tuning execution parameters
therefore inflates the multiple-testing denominator visibly instead of hiding
inside one run's fan-out. Phase 5 still owns how the inventory becomes a
denominator, and **no deflated statistic ships in Phase 4**.

---

## 11. Determinism requirements

- `TradeLog` and `EquityCurve` are compared **exactly (bitwise)** in
  determinism verification. Neither declares a `ToleranceSpec`; the simulator
  is a fixed-order scalar loop with no BLAS, no parallelism and no
  reduction-order freedom ([ADR-0016](../adr/ADR-0016-determinism-tiers.md),
  OD-3B).
- Structural fields — keys, direction, exit reason, ambiguity and gap flags,
  trade count — are **never** toleranced under any circumstances.
- All arithmetic orders in §6.2 and §8.1 are normative, because changing an
  operand order changes the last bits and would surface as a determinism
  failure rather than a silent drift. That is the intended behaviour.
- Verification **recomputes** and never reads cache
  ([ADR-0007](../adr/ADR-0007-materialization-policy.md) Amendment 1); the new
  artifacts join the existing comparable bundle so a poisoned-cache test covers
  them by construction.
- No tolerance for Plane-B artifacts is reachable from the CLI, the config or
  the verifier (ADR-0022 §8, unchanged).

---

## 12. Artifact persistence requirements

| Artifact | Policy | Path in the run directory |
|---|---|---|
| `IntentFrame` | `ephemeral` **upgraded to `persist`** when simulation is configured — exactly [ADR-0007](../adr/ADR-0007-materialization-policy.md)'s stated V1 default and the existing `_upgrade_ephemeral_for_downstream` rule | `artifacts/fold-XXXX-intent-YYYY.npz` |
| `TradeLog` | `persist` (ADR-0007 V1 default) | `artifacts/fold-XXXX-policy-YYYY-trades.npz` |
| `EquityCurve` | `persist` (ADR-0007 V1 default) | `artifacts/fold-XXXX-policy-YYYY-equity.npz` |
| Simulation summary (counters, `ExecutionSpec` canonical document including the nested binding, `cost_semantics`, `binding_id`, `binding_digest`, `execution_price_model`) | always persisted | `metrics/simulation-summary.json` |
| Plane-B metrics | always persisted, inside the existing inventory | `evaluation_inventory.json`, `metrics/evaluation-summary.json` |

Notes:

- The `IntentFrame` is stored as `keys` (int64) + `target_exposure` (float64).
  Sparsity is carried by the key set and abstention by the value bits, so the
  absent/`NaN`/`0.0` trichotomy survives persistence without a side-car mask.
  This is the first durable `IntentFrame` write path in the repository and it
  **replaces** a Phase-3 architecture guard asserting that no such path exists
  (§26) — an intended, reviewed extension, not a weakening.
- Writes reuse the existing canonical-NPZ container, immutable store and atomic
  publication ([ADR-0017](../adr/ADR-0017-storage-files-as-truth.md)). No new
  storage mechanism is introduced.
- `COMPLETE` run directories remain immutable; supersede, never mutate.

---

## 13. Run fingerprint and identity implications

- **No fingerprint-algorithm change.** `experiment_fingerprint` already hashes
  the resolved config plus component identities
  ([ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md)); the
  `ExecutionSpec` enters as an ordinary resolved-config block. The algorithm
  string stays `experiment-fingerprint-v1`.
- **`ExecutionSpec` is configuration, not a code plugin** (§3.1), so it has no
  source hash and no registry entry. Its canonical params document is what
  identity uses.
- **Exactly one `ExecutionSpec` per run** in V1. Multiple execution
  configurations are multiple runs — see §10 for why that is deliberate.
- **`FittedState` identity is unchanged.** Execution configuration is strictly
  downstream of fitting and must never enter fitted-state identity; a change of
  TP distance must not invalidate a fitted model. An architecture guard pins
  this (§26).
- **Stage identity is unchanged.** Feature and model stage identities do not
  gain execution components.
- `cost_semantics_unverified` is a **permanent run marking**, in the spirit of
  `oos_touched` / `lockbox_touched`: recorded, never cleared, never
  retro-edited. `binding_id`, `binding_digest` and `execution_price_model`
  travel with it, so *which* evidence state produced a number is answerable
  from the run record without diffing floats — and closing the audit changes
  the fingerprint rather than the meaning of an existing run (§6.4, RM-3).
- The run-record document schema gains the Plane-B fields, so its version is
  bumped and `scan_runs()` accepts both the Phase-3 and Phase-4 shapes. A
  golden test proves an existing Phase-3 run directory still scans (§25 T11).

---

## 14. Failure behaviour — the complete refusal list

Refusals are **fail-fast and total**: no partial `TradeLog`, no partially
written artifact, no "best effort" trade. Anything detectable before execution
is refused before execution.

| # | Condition | When |
|---|---|---|
| R1 | `target_exposure` is finite and not exactly `+1.0`, `0.0` or `-1.0` (OD-4B) | whole-column pre-validation, **before** the first row is simulated |
| R2 | `intent_schema_id` not in the simulator's frozen supported set | resolve time, before execution |
| R3 | Intent keys are not a subset of the execution axis, or are not strictly increasing | pre-validation |
| R4 | `ExecutionSpec` or `ExecutionCostBinding` field missing, non-finite, or outside its declared domain (`lot_size <= 0`, negative `spread_pips`, `take_profit_pips <= 0`, unknown `commission_basis`, unknown `cost_semantics`, unknown `execution_price_model`, empty `binding_id`, `execution_id` not the supported literal, …) | config resolution |
| R5 | Simulation configured but no `DecisionPolicy` is configured | config resolution |
| R6 | A fold's evaluation range has fewer than 2 observed rows | config resolution / fold generation |
| R7 | `SimulationRequest` contains a non-finite OHLC value, or an incoherent bar (`low > high`, `open`/`close` outside `[low, high]`) | request construction |
| R8 | Axis tag mismatch (`unit` / `clock_domain`) between request, intents and view | pre-validation |
| R9 | A Plane-B evaluator emits a metric name it did not declare, or omits one | post-call revalidation, as Plane A does today |
| R10 | `cost_semantics = VERIFIED` declared while `VERIFIED_COST_BINDING is None`, or while the declared binding differs from it in any field, or when the declared `binding_digest` does not equal the platform-recomputed digest (RM-3) | **config resolution — hard refusal**, never a recorded documentation error (§6.4) |
| R11 | `cost_semantics = VERIFIED` combined with `execution_price_model = RECORDED_PATH_UNVERIFIED` — an unverified price model cannot back verified semantics (RM-1) | config resolution |
| R12 | `execution_price_model = SYNTHETIC_DECLARED` reached through config resolution — it is a test-only, in-process construction | config resolution, and architecture-guarded (§26) |
| R13 | `binding_digest` present while `cost_semantics = UNVERIFIED`, or absent while `VERIFIED` | config resolution |

Classification: a refusal before any evaluation is an **infrastructure failure**
— permanently recorded, `FAILED`, zero inventory rows, and **not** a
statistical trial (ADR-0011, unchanged). The existing
pre-evaluation-failure path handles it; nothing new is invented.

---

## 15. Slices, in order

Order: **4A → CP-13 → 4B → 4C → 4D → CP-14 → 4E → 4F → 4G → CP-15 → 4H →
CP-16**, with **CP-13C** (audit closure) firing whenever the external evidence
completes — before, during or after Phase-4 completion.

Rationale: the audit record comes first because it defines the field list
everything else binds; contracts before engine; engine before orchestration;
canaries after the loop exists, because a canary that cannot run end to end
proves nothing.

**Implementation outcome (2026-08-15).** Slices 4B–4H are implemented and
qualified at clean implementation commit
`848cdf2d0f996c170deecd524cd61772ed6c971c`; CP-14 and CP-15 are GREEN. Slice
4A's baseline record remains COMPLETE and CP-13 remains GREEN; COST_AUDIT is
still OPEN. CP-16 is GREEN at the follow-up-reviewed candidate `4351b9a`; the
owner approved Phase-4 closeout and merge on 2026-08-15.

**Every slice assumes the Phase-1/2/3 rules hold unchanged:** synthetic data
for development, no gated access, architecture tests extended and never
weakened, `./scripts/gate.sh` green unfiltered before any completion claim.

### 4A — Cost-semantics audit record — **documentation only · baseline record COMPLETE (2026-08-14)**

- **Scope.** [COST_AUDIT.md](../research/COST_AUDIT.md) — the fact register,
  the CR/NB classification, the evidence required for each UNKNOWN, the
  configuration in use, the declared limitations, and the closure procedure.
- **Status (RM-4).** The **baseline record is COMPLETE**: it exists, every fact
  is classified closure-required or non-blocking, every binding field maps to a
  register row, and no plausible default appears anywhere. That is CP-13's
  planning-level condition and it is **GREEN**.
- **What continues in parallel.** COST_AUDIT stays **OPEN** while owner-supplied
  external evidence is entered fact by fact as it arrives. This is evidence
  work, not code work, and **it blocks no slice.**
- **Exclusions.** No code. No probe of OOS/LOCKBOX/SNAPSHOT/RAW/CURATED. No
  fact promoted to VERIFIED without named evidence. No `VERIFIED_COST_BINDING`
  constant until closure.
- **Acceptance.** Met — see CP-13 in §16.
- **Checkpoint.** CP-13 (**GREEN**). Audit *closure* is the separate later gate
  **CP-13C**, which is not a Phase-4 completion condition.

### 4B — Phase-4 core vocabulary — **COMPLETE (2026-08-15)**

- **Scope.** Exactly §3.1 and §3.2: the **three** enums (`CommissionBasis`,
  `CostSemanticsStatus`, `ExecutionPriceModel`), `TradeExitReason`,
  `ExecutionCostBinding`, the `VERIFIED_COST_BINDING` constant **as `None`**,
  `ExecutionSpec`, `SimulationRequest`, `TradeLog`, `EquityCurve`,
  `TradeEvaluator`. Validation, refusals, immutability, read-only arrays,
  canonical params documents (the binding nested as its own canonical block),
  and the binding-digest computation.
- **Exclusions.** No simulator, no evaluator implementation, no orchestration,
  no persistence, no config field. **No verified binding value** — the constant
  ships as `None` and only the audit-closing commit may set it (§6.4).
- **Tests.** Every validation and refusal path, **including the fail-closed
  R10–R13 and §20 C11–C13**; frozen/read-only enforcement; exact public
  surface; no table-library type in any signature; exit-reason code mapping
  pinned; the guard pinning `VERIFIED_COST_BINDING` to `None`.
- **Acceptance.** Public surface equals §3 exactly; `VERIFIED` unreachable;
  gate green.
- **Evidence.** `tests/test_simulation_contracts.py` plus the architecture
  surface guards pass at `848cdf2`; the repository binding is pinned to
  `None`, immutable arrays round-trip bitwise and all R10–R13 paths refuse.

### 4C — Simulator engine — **COMPLETE (2026-08-15)**

- **Scope.** `simulation/bracket.py`: `simulate_bracket_single_position_v1`
  implementing §5 and §6 exactly, plus `SUPPORTED_INTENT_SCHEMA_IDS`.
- **Exclusions.** No I/O, no store, no registry, no config, no orchestration,
  no `data` import, no `GapIndex`.
- **Tests.** Unit coverage of the state machine; the §14 refusals reachable at
  this layer; purity (same inputs ⇒ bitwise-identical outputs across repeated
  calls and fresh subprocesses).
- **Acceptance.** `simulation` imports only `core`; gate green.
- **Evidence.** Repeated calls and fresh subprocesses with different hash
  seeds produce identical bytes; the source boundary proves no data, I/O,
  clock, table-library or `GapIndex` authority reaches the engine.

### 4D — Hand-computed fixture truth + timing, ambiguity, gap, boundary and cost rigs — **COMPLETE; CP-14 GREEN (2026-08-15)**

- **Scope.** §17–§21 in full, including the eight adversarial controls of §18.
- **Exclusions.** No production registration of any control; no real data.
- **Acceptance.** Every fixture matches hand-computed truth exactly (no
  tolerance, no `skip`, no `xfail`); every adversarial control is caught.
- **Checkpoint.** **CP-14 GREEN.** F1–F12, ambiguity, gap, boundary and cost
  goldens are exact; both timing rigs pass; all eight bounded source/control
  mutations are caught and architecture-guarded as test-only.

### 4E — Plane-B evaluators — **COMPLETE (2026-08-15)**

- **Scope.** `trade_summary_v1` and `equity_drawdown_v1` exactly as §9;
  registered in the existing `evaluation` registry; hand-computed metric
  goldens including every edge case.
- **Exclusions.** Any third metric; any ratio statistic; any deflated number.
- **Acceptance.** Registry contains exactly the two Plane-B evaluators; gate
  green.
- **Evidence.** Twelve trade-evaluation goldens cover chronological sums,
  rates, drawdown, zero/empty/minimum edges and post-call revalidation; the
  registry and public surfaces are exact.

### 4F — Orchestrator integration, persistence, run record — **COMPLETE (2026-08-15)**

- **Scope.** The `execution` config block, including the nested cost binding and
  the fail-closed resolution of `cost_semantics` (§6.4, R10–R13);
  `SimulationRequest` construction in `experiments`; per-`(fold, policy)`
  simulation; §12 persistence including the `IntentFrame` upgrade; run-record
  schema bump with backward-compatible `scan_runs()`;
  `cost_semantics_unverified` marking alongside `binding_id`, `binding_digest`
  and `execution_price_model`; Plane-B comparables added to determinism
  verification.
- **Exclusions.** No sweep, no SQLite, no CLI verb beyond the existing four.
  The config may express **only** `cost_semantics: unverified` and
  `execution_price_model: recorded_path_unverified` while the audit is OPEN;
  `synthetic_declared` stays test-only (R12).
- **Acceptance.** A config-only run produces Plane-A and Plane-B results;
  a config declaring `verified` is refused; determinism verification covers the
  new artifacts; gate green.
- **Evidence.** The end-to-end synthetic run persists exact intent/trade/equity
  NPZ members and permanent markings; `research-run-manifest-v2` is written;
  the Phase-3 V1 terminal-directory golden still scans; the active SIGKILL rig
  finds no partial Plane-B final artifact and reuses only verified caches.

### 4G — Plane-B inventory + harness-inversion canaries — **COMPLETE; CP-15 GREEN (2026-08-15)**

- **Scope.** §10's inventory extension with the explicit `plane` field and the
  exact `F·S·E` + `F·P·B` formula; §23's four canaries end to end.
- **Exclusions.** No denominator arithmetic, no deflated statistic (Phase 5).
- **Acceptance.** Inventory counts exact; all four canaries behave as
  specified.
- **Checkpoint.** **CP-15 GREEN.** The two-policy/two-evaluator integration
  records exactly 3 Plane-A + 12 Plane-B rows; verification compares all
  intent, trade and equity artifacts with no tolerance; K1–K4 pass and cannot
  reach production registration.

### 4H — Performance qualification and implementation closeout — **COMPLETE; CP-16 GREEN (2026-08-15)**

- **Scope.** §24's protocol on published TRAIN; this document updated with
  measured evidence; PROJECT_STATE, roadmap and README updated; completion
  checklist (§30) walked item by item.
- **Acceptance.** Median under budget or a recorded STOP-and-report; external
  state (audit log, D-6 fingerprint, five view digests) unchanged.
- **Evidence.** §24 records the clean-commit full-TRAIN qualification. The
  unfiltered gate is green (1,578 default-gate cases, Ruff, strict mypy over
  138 source files). Documentation is updated without changing COST_AUDIT.
- **Checkpoint.** **CP-16 GREEN / OWNER APPROVED.** The review and remediation
  evidence is recorded in §16 and §32.

---

## 16. Formal checkpoints

| CP | Name | Green when |
|---|---|---|
| **CP-13** | *"no cost fact is invented"* — the **evidence-classification** gate · **GREEN 2026-08-14** | The register exists; **every fact is classified closure-required (CR) or non-blocking (NB)** (COST_AUDIT §1.1); every CR fact is either VERIFIED with named evidence or UNKNOWN **with the exact evidence required**; every `ExecutionSpec` / `ExecutionCostBinding` field maps to exactly one register row; **no plausible default exists anywhere** in the plan or code; the executable-price semantics are named and their unbound part is declared (RM-1); declared limitations are written down |
| **CP-13C** | *"verified means verified"* — the **audit-closure** gate · **not scheduled; external** | Every CR fact VERIFIED with named evidence; every NB item explicitly classified; the ADR-0023 §8 amendment binds the executable price path and re-derives the spread charge; `VERIFIED_COST_BINDING` created with its recomputed digest; COST_AUDIT header CLOSED with a date. **Not a Phase-4 completion condition** — Phase 4 may complete with the audit OPEN, since the gate is on claims, not construction (RM-4) |
| **CP-14** | *"the simulator cannot invent a fill"* — **GREEN 2026-08-15 at `848cdf2`** | Hand-computed fixtures exact; no fill earlier than the next observed row; no fill priced from a future row; SL wins every ambiguous bar; gapped exits use the observed open; no state crosses a fold boundary; all eight adversarial controls caught; fractional exposure refused |
| **CP-15** | *"runs cannot hide execution assumptions"* — **GREEN 2026-08-15 at `848cdf2`** | Every Plane-B result carries its `ExecutionSpec`, its `cost_semantics` status and its ambiguity / gap / boundary-close rates; inventory is exactly `F·S·E` + `F·P·B` with the `plane` field present; determinism verification covers `TradeLog` and `EquityCurve` and never reads cache; canaries behave |
| **CP-16** | Independent whole-phase review — **GREEN / OWNER APPROVED 2026-08-15 at `4351b9a`** | Review of `f27ce0b` returned **APPROVE WITH NON-BLOCKING NOTES**; F-1…F-3 were remediated at clean pushed `4351b9a`, follow-up independent review passed, N-1…N-9 remained untouched, and the owner approved closeout and merge; §30 fully green |

---

## 17. Hand-computed fixture test plan

Every fixture is a small synthetic OHLC table (5–20 rows) with hand-computed
expected trades, costs and equity written into the test as literals. **No
fixture derives its expectation from the implementation.** Comparison is exact.

Every fixture declares `execution_price_model = SYNTHETIC_DECLARED` (§6.0): the
fixture defines its instrument, so its rows *are* the executable path for both
directions by construction. That declaration is what makes hand-computed truth
meaningful here and asserts nothing about the real dataset (RM-1).

| # | Fixture | Proves |
|---|---|---|
| F1 | single `+1` then silence, no brackets, boundary close | entry at next open; boundary close at final `close`; one trade; exact PnL |
| F2 | `+1` at row 0, TP hit on row 3 | TP exit at the level; PnL from the level, not the bar high |
| F3 | `+1`, SL hit on row 2 | SL exit at the level |
| F4 | `+1` then `-1` (reversal) | two trades at one price and one row; costs charged twice; no overlapping exposure |
| F5 | `+1`, stop-out, later `+1` again | re-entry (OD-4G); two trades |
| F6 | sparse frame: one `+1` row only | state held; one trade; boundary close |
| F7 | `NaN` row and absent row interleaved with `0.0` | state held for both; counters distinguish all three (ADR-0009) |
| F8 | `0.0` while flat | no trade, no cost |
| F9 | one explicit statement at the final row, including the `NaN` variant | zero trades; `unexecuted_final_row_intents == 1`; `NaN` remains an abstention and the count is diagnostic only |
| F10 | SHORT mirror of F2/F3 | sign symmetry of levels, PnL and costs |
| F11 | entry and TP on the same row | same-row open/close is legal and priced correctly |
| F12 | `None` take-profit with a stop loss (and vice versa) | one-sided brackets never trigger the absent side |

---

## 18. Leakage and timing rigs — **release-blocking**

Two exact perturbation rigs, modelled on the Phase-2 feature rigs and the
Phase-3 row-locality rigs. Comparison is bitwise; a tolerance, `skip` or
`xfail` marker in these rigs is prohibited.

| Rig | Method | Catches |
|---|---|---|
| **future-price rig** | perturb `open/high/low/close` strictly **after** row `j`; every trade whose exit row `<= j` must be bitwise unchanged | any read of a future bar into a past fill or exit |
| **future-intent rig** | perturb intent values strictly **after** row `j`; every trade whose entry row `<= j` must be bitwise unchanged | any use of a later instruction to time an earlier fill |

Eight adversarial controls, **test-only and never registered in production**
(the Phase-3 discipline), each of which the rigs or fixtures must catch:

| # | Control | Must be caught by |
|---|---|---|
| AC-1 | fills at the decision row's `open`/`close` instead of the next row | F1/F2 + future-intent rig |
| AC-2 | take-profit wins an ambiguous bar | §19 |
| AC-3 | fills the bracket price through a gapped open | §21 |
| AC-4 | carries a position across a fold boundary | §21 |
| AC-5 | charges costs once instead of per trade, or omits them | §20 |
| AC-6 | rounds a fractional exposure to its sign instead of refusing | §14 R1 test |
| AC-7 | decides row `R`'s exit using row `R+1` | future-price rig |
| AC-8 | fills a final-row intent from beyond `eval_end` | F9 + a bounded-request test |

A coverage meta-test executes the named catching witnesses for exactly these
eight controls and derives the observed catch labels from those executions; it
does not trust a hand-maintained coverage declaration. Every control must have
at least one observed catch, and AC-7's observed catch is pinned specifically
to the future-price rig. The existing per-control tests remain independently
release-blocking.

---

## 19. Take-profit / stop-loss ambiguity tests

| # | Case | Expected |
|---|---|---|
| A1 | bar touches only TP | TP exit, `ambiguous_exit = False` |
| A2 | bar touches only SL | SL exit, `ambiguous_exit = False` |
| A3 | bar touches both (`low <= sl_level` and `high >= tp_level`) | **SL exit**, `ambiguous_exit = True`, exit price `sl_level` |
| A4 | bar touches both, SHORT | mirror of A3 |
| A5 | price exactly equals a level (`low == sl_level`) | touched — comparisons are inclusive `<=` / `>=`, stated so it is not rediscovered |
| A6 | both levels touched on the trade's **entry row** | SL wins there too; ambiguity is not special-cased for fresh entries |
| A7 | AC-2 control (TP wins) | caught: A3's expectation fails |
| A8 | many ambiguous bars | `ambiguous_exit_rate` equals the hand-counted share exactly |

---

## 20. Cost arithmetic tests

| # | Case | Expected |
|---|---|---|
| C1 | one LONG round trip, known prices and spec | `gross_pnl`, `spread_cost`, `commission_cost`, `net_pnl` each equal a hand-computed literal |
| C2 | `PER_SIDE` vs `PER_ROUND_TURN` on identical input | commission differs by **exactly** a factor of two |
| C3 | `lot_size` doubled | every money column doubles exactly |
| C4 | `contract_size` doubled | `gross_pnl` and `spread_cost` double; `commission_cost` unchanged (it is per lot, not per contract) |
| C5 | `account_currency_per_quote_currency != 1.0` | `gross_pnl` and `spread_cost` scale; `commission_cost` unchanged (already account currency) |
| C6 | `spread_pips = 0`, `commission_per_lot = 0` | `net_pnl == gross_pnl`; the zeros are declared, recorded in identity, and visible in the run record |
| C7 | SHORT trade | costs identical to the LONG mirror; only `gross_pnl` flips sign |
| C8 | AC-5 control | caught by C1/C2 |
| C9 | cost recognition timing | equity drops by the trade's total cost at the **entry** row (§6.3) |
| C10 | operand order | the implementation's multiplication order matches §6.2 exactly (source-level assertion) |
| C11 | **fail-closed VERIFIED** (RM-3) | with `VERIFIED_COST_BINDING is None`, every attempt to resolve `cost_semantics = VERIFIED` — from config, from a constructed spec, from a hand-edited resolved config — **refuses** (R10); the refusal is a pre-evaluation infrastructure failure, not a warning |
| C12 | verified path, simulated | with a **test-injected** verified binding, an exactly-equal declaration resolves; any single field altered refuses; a stale or mistyped `binding_digest` refuses (R10); `RECORDED_PATH_UNVERIFIED` with `VERIFIED` refuses (R11) |
| C13 | digest reproducibility | the recomputed digest is stable across processes and hash seeds for the same binding, and changes when any bound field changes |
| C14 | price-model reachability | `SYNTHETIC_DECLARED` is unreachable from config resolution (R12); `RECORDED_PATH_UNVERIFIED` is the only config-legal value in V1 |

---

## 21. Gap and boundary tests

| # | Case | Expected |
|---|---|---|
| G1 | LONG, next bar opens below `sl_level` | exit at that `open`, `STOP_LOSS`, `gapped_exit = True`, loss **larger** than the nominal SL distance |
| G2 | LONG, next bar opens above `tp_level` | exit at that `open`, `TAKE_PROFIT`, `gapped_exit = True`, gain **larger** than nominal |
| G3 | SHORT mirrors of G1/G2 | symmetric |
| G4 | AC-3 control (bracket price through a gap) | caught by G1/G2 |
| G5 | position open at the final row | `BOUNDARY_FORCED_CLOSE` at `close[N-1]`; counted in the rate |
| G6 | two consecutive folds, position open at the end of fold *k* | fold *k+1* starts FLAT; no trade spans folds; AC-4 caught |
| G7 | overlapping folds (`step_rows < eval_rows`) | each fold's `TradeLog` is independent; identical overlapping rows may legitimately produce identical trades in both folds |
| G8 | a large observed-key discontinuity inside the evaluation range | simulated as an ordinary consecutive observed row; **no** session, weekend, holiday, outage or closure inference anywhere; no `GapIndex` import exists in `simulation` |
| G9 | fold with `N == 1` | refused (R6) |

---

## 22. Determinism and re-run tests

| # | Case | Expected |
|---|---|---|
| D1 | one fingerprint, two `run_id`s | `TradeLog` and `EquityCurve` bitwise identical; verification report stored |
| D2 | verification never reads cache | the existing poisoned-cache/raising-store doubles extended to the Plane-B path |
| D3 | fresh subprocesses, different hash seeds | identical bytes |
| D4 | reordered but equivalent input construction | identical bytes (no dict-ordering or set-iteration dependence) |
| D5 | structural fields never toleranced | an architecture assertion that no `ToleranceSpec` reaches keys, direction, exit reason, flags or counts |
| D6 | `kill -9` mid-run then re-run | INTERRUPTED detected; cache reuse unchanged; no partial Plane-B artifact readable under a final name |

---

## 23. Harness-inversion canaries — **release-blocking**

All four run end to end on **seeded synthetic fixtures**, are test-only, and
are architecture-guarded against production registration.

| # | Canary | Expected |
|---|---|---|
| K1 | **oracle** — a test-only cheating policy that sees the next row's move | net PnL **absurdly positive**, far beyond any plausible honest result; if it is not, the harness is not measuring what it claims |
| K2 | **random** — a seeded random policy on a driftless synthetic random walk with symmetric brackets | net PnL **negative**, and `net_pnl ≈ -total_cost` within a declared relative band tied to `total_cost`; `total_cost > 0` |
| K3 | **always flat** — every row `0.0` | zero trades, zero costs, flat equity, all rates `NaN` |
| K4 | **always abstain** — every row `NaN`, and a variant with an empty (fully absent) frame | zero trades in both; counters distinguish abstain from absent |

K1 and K2 are the inversion pair the roadmap requires: *a cheating model must
win absurdly; a random model must lose approximately the costs.* Because the
fixtures are seeded and the simulator is deterministic, K1's anchors are exact
and K2's approximation is a fixed cost-relative bound — never a statistical
claim dressed as a test.

---

## 24. Performance qualification criteria

**Budget:** full-period dense-intent simulation **< 60 s** (roadmap Phase-4
acceptance criterion, unchanged).

- **View:** published TRAIN `XAUUSD_S_M1_TRAIN_V1`, 1,414,538 rows. TRAIN is
  `NORMAL`-classified: no ceremony, no unlock, **no audit event**. **No
  SNAPSHOT, OOS, LOCKBOX, RAW or CURATED access is required or permitted; the
  expected audit delta for all of Phase 4 is zero.**
- **Load:** a dense `IntentFrame` over the full axis, with brackets configured
  so that trades actually open and close (an all-flat frame would measure
  nothing).
- **Method:** 1 discarded warm-up + 5 timed repetitions; report median and min;
  a budget is missed only if the **median** exceeds it; all verification and
  integrity checks inside the timed region and unchanged; page cache warm; no
  cold measurement (sudo unavailable and no budget requires it).
- **Recorded:** per-repetition wall time, median, min, peak RSS, artifact
  sizes, content hashes, trade count, ambiguity/gap/boundary rates, Git
  identity.
- **Evidence-only, no new SLA:** peak RSS and artifact sizes carry no budget,
  exactly as in Phases 1–3.
- **Performance-miss protocol:** record the result, collect only the approved
  diagnostics, **STOP**, and report to the owner. Do not optimize
  automatically. Serving an unverified artifact to hit a number remains
  forbidden.

### Measured qualification — PASS at clean commit `571fa6a` (2026-08-15)

The committed harness ran one discarded warm-up and five measured repetitions
over all **1,414,538** published TRAIN rows. Every timed region included
registry discovery, NORMAL authorization, manifest/Parquet integrity checks,
OHLC load, construction and validation of the dense intent and simulation
request, bracket simulation, both Plane-B evaluators, artifact validation and
content hashing. The workload emitted 211,961 completed trades per repetition.

| Repetition | Wall time (s) |
|---:|---:|
| 1 | 1.329790961 |
| 2 | 1.326881142 |
| 3 | 1.320760401 |
| 4 | 1.325094530 |
| 5 | 1.319865595 |
| **Median** | **1.325094530 — PASS** |
| **Minimum** | **1.319865595** |

The median is below the 60 s budget; the miss protocol did not trigger and no
optimization was attempted. Peak RSS was **523,923,456 bytes** (evidence only;
no budget). Retained array bytes / SHA-256 were: intent **22,632,608** /
`d34637f893333c4209082d00cbdb8d2d56636b4fdcd73b5c32e5d33c31480ffd`;
trades **14,413,348** /
`b1448a65682ac9223329884f5b56bd2ecc624441d9b62c9844e6c50ecb6c2534`;
equity **22,632,608** /
`dfe6f2f5f5fa9e77e7c690bbcbdb396e6b21d658ede4a51f18cc075bd9541518`.
All signatures were identical across warm-up and measured repetitions.

The execution-honesty rates were ambiguity `0.013497766098480381`, gap
`0.007303230311236501`, and boundary close `0.000004717849038266473`.
The binding was permanently marked `UNVERIFIED` /
`RECORDED_PATH_UNVERIFIED`, id `unverified-audit-input-v1`, digest `None`.
The harness also observed unverified cost/drawdown operands solely to prove the
workload exercised arithmetic; they are **not research results or real-money
estimates** and are deliberately not promoted here.

External state remained exact after qualification: audit **3 records** /
SHA-256 `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
D-6 `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
and all five published view-manifest digests unchanged. No OOS, LOCKBOX,
SNAPSHOT, RAW or CURATED rows were read.

---

## 25. Testing matrix (consolidated)

| # | Area | What it must prove |
|---|---|---|
| T1 | Contract validation | Every §3 field validated; every §14 refusal exact; frames immutable and read-only |
| T2 | Intent value domain | `±1.0` / `0.0` / `NaN` / absent handled per §5.3; **any other finite value refused before simulation**; `-0.0` is FLAT |
| T3 | Trichotomy preservation | Absent ≠ `NaN` ≠ `0.0` through simulation, counters and persistence |
| T4 | Entry timing | §17 F1–F12 exact; no fill at the decision row; every explicit final-row statement, including `NaN`, is unexecuted and diagnostically counted |
| T5 | Ambiguity | §19 A1–A8; SL always wins; rate exact |
| T6 | Gaps | §21 G1–G4; observed open used, never the level |
| T7 | Fold boundaries | §21 G5–G9; flat start, deterministic close, no cross-fold state |
| T8 | Re-entry / reversal | F4, F5; one open trade at all times; two trades on reversal |
| T9 | Cost arithmetic | §20 C1–C10 |
| T10 | Leakage rigs | §18 both rigs green; all eight controls caught; coverage meta-test |
| T11 | Persistence | §12 paths and policies; `IntentFrame` upgrade proven; `scan_runs()` reads Phase-3 **and** Phase-4 run directories |
| T12 | Inventory | Exactly `F·S·E` Plane-A + `F·P·B` Plane-B rows; `plane` field present; policy dimension present in B and absent in A |
| T13 | Determinism | §22 D1–D6 |
| T14 | Canaries | §23 K1–K4 |
| T15 | Identity | `ExecutionSpec` changes the fingerprint; **`FittedState` identity is unaffected by any execution field** |
| T16 | Cost-status marking | `cost_semantics_unverified` present in the manifest, the simulation summary and every Plane-B inventory row, together with `binding_id`, `binding_digest` and `execution_price_model`; never clearable |
| T16a | **Fail-closed VERIFIED** (RM-3) | §20 C11–C13: with no verified binding in the repository, `VERIFIED` is unreachable from any input; with a test-injected binding, only an exact field-and-digest match resolves; an unverified price model can never back verified semantics |
| T16b | **Price-model semantics** (RM-1) | §20 C14: `SYNTHETIC_DECLARED` is test-only and unreachable from config; fixtures declare it; no source or document asserts the recorded path *is* the executable path; bracket levels are documented as derived from the executable entry price under the declared model |
| T17 | Architecture boundaries | `simulation` imports only `core`; no `GapIndex`, no `data` import, no table library, no civil time/UTC; exact public surfaces |
| T18 | No deferred vocabulary | No portfolio, order, slippage, position-limit, margin, trailing-stop, partial-fill or registry symbol anywhere |
| T19 | No Phase-5 vocabulary | No deflated statistic, no sweep, no SQLite |
| T20 | Real-data smoke (TRAIN only) | One end-to-end simulated run over published TRAIN; audit log byte-identical |

---

## 26. Architecture-guard changes — extended, never weakened

Phase-3 guards written to assert the **absence** of Phase-4 concepts have done
their job and are now scope-corrected, not deleted. Each change below is an
intended, reviewed extension; widening any other allowlist remains an
architecture change subject to full review (`CLAUDE.md`).

| Existing guard | Change |
|---|---|
| `test_slice_3i_launchers_and_scope_boundaries_are_exact` — forbids the terms `executionspec`, `tradelog`, `equitycurve`, `commissionspec`, `slippagespec` in the Slice-3I public surfaces | The first three become **permitted in the Phase-4 orchestration surfaces only**; `slippagespec` and `commissionspec` remain forbidden **everywhere** (no such type exists in this design) |
| Phase-3 guard asserting **no durable `IntentFrame` write path exists** | Replaced by a guard asserting the write path exists **only** on the simulation-configured route and writes exactly the two declared arrays (§12) |
| Phase-3 evaluation guards asserting **no Plane-B, no decision-plane evaluator, no trade metric** | Narrowed to: exactly the two §9 evaluators exist; the §9.3 exclusion list is absent everywhere; **no deflated statistic** anywhere (unchanged) |
| Layer budget `ALLOWED` | **Unchanged.** `simulation: {"core"}` already exists |
| New guards added | `simulation` imports only `core` and never `data`/`GapIndex`; the eight adversarial controls are test-only; the exit-reason code mapping is pinned; `FittedState` identity contains no execution component; exactly one `ExecutionSpec` per run |
| New guards added (RM-1 / RM-3) | `VERIFIED_COST_BINDING` is `None` in the source tree, pinned by a test whose failure message points at COST_AUDIT §8 — so creating it is a deliberate, reviewed act; `CostSemanticsStatus.VERIFIED` is unreachable from config resolution while that constant is `None`; `ExecutionPriceModel.SYNTHETIC_DECLARED` never appears on a config-resolution path; `ExecutionPriceModel` has exactly the two V1 values, so adding the verified one is an ADR-governed change |

---

## 27. Documentation obligations per checkpoint

| Checkpoint | Obligations |
|---|---|
| **CP-13** | [COST_AUDIT.md](../research/COST_AUDIT.md) updated with any newly VERIFIED fact and its evidence, and every fact classified CR or NB; PROJECT_STATE records the audit status |
| **CP-13C** | COST_AUDIT closed per its §8 — CR facts VERIFIED, NB items re-confirmed, ADR-0023 §8 amended with the verified executable-price rules, `VERIFIED_COST_BINDING` created and its digest recorded, ADR-0020, PROJECT_STATE and the roadmap dated |
| **CP-14** | This document updated with per-slice implementation evidence (the role Phase 2/3 plans play); PROJECT_STATE slice records |
| **CP-15** | Inventory semantics reflected here and in PROJECT_STATE; [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) cross-referenced (no amendment expected — §10) |
| **CP-16** | This document closed with measured evidence; [V1_ROADMAP.md](../roadmap/V1_ROADMAP.md) Phase-4 status; [PROJECT_STATE.md](../../PROJECT_STATE.md) exact next action; [README.md](../../README.md) simulation row; [docs/adr/README.md](../adr/README.md) index current; [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7 verified against what shipped |

A Phase-4 authoring guide (`docs/guides/ADDING_EXECUTION.md` or similar) is
**not** planned: with one simulator, one execution spec and no plugin surface,
there is nothing for a plugin author to author. Any future guide is a later
planning decision.

---

## 28. Decision status

### 28.1 RESOLVED owner decisions

**OD-4A** minimal simulator scope · **OD-4B** discrete fixed-lot intent
semantics and refusal of fractional values · **OD-4C** next-observed-row entry
timing as execution semantics only · **OD-4D** TP/SL as execution
configuration, not policy output and not a new intent schema · **OD-4E**
stop-loss wins intrabar ambiguity, recorded and rate-reported · **OD-4F**
gapped opens use the observed open · **OD-4G** state-following target semantics
with sparse frames for one-shot signals · **OD-4H** per-fold flat start and
deterministic boundary close · **OD-4I** configurable, explicitly united costs
behind a VERIFIED/UNKNOWN audit register · **OD-4J** smallest correct design,
with the named abstractions removed · **OD-4K** the methodology boundary.

The former roadmap blocking decision *"first supported intent-schema set"* is
resolved by OD-4A/OD-4B: exactly `target_exposure_intent_v1`, restricted to
`{+1.0, 0.0, -1.0, NaN, absent}`.

### 28.1a Correctness remediation (RM-1…RM-5) — **not owner decisions**

§1.1 records five correctness corrections: the executable price path is
named and its side-dependent part left unbound (RM-1); the audit closure rule
is split into closure-required and non-blocking facts (RM-2); `VERIFIED` is
fail-closed against a repository-controlled binding (RM-3); and Slice
4A/CP-13's status is reconciled with "next action = Slice 4B" (RM-4). RM-5
corrects the equity-state notation so costs are recognized exactly once. These
are architecture/correctness corrections and engineering mechanics. **No owner
decision changed**, and no remediation detail is presented as owner-approved.

One derived engineering claim was narrowed by RM-1: **ER-1** below.

### 28.2 Genuine remaining blockers

**Exactly one, and it is external:** the **closure-required** UNKNOWN facts in
[COST_AUDIT.md](../research/COST_AUDIT.md) §3–§4 (A1, A2, A3, A6, B3, B4, C1,
C2, C3, C4) require broker documentation that does not exist in this repository
(MT5 symbol specification for the account behind
`mt5.justmarkets-demo3.wallclock`, plus the published commission schedule).

It blocks **only** (a) the promotion of money-denominated results to
authoritative real-money estimates and (b) any claim that the executable price
path is verified (RM-1). It does **not** block implementation, fixture
verification, canaries, end-to-end qualification, or the TRAIN performance
smoke.

**No planning decision remains open.**

### 28.3 Recommendations engineering takes without escalation

These are engineering judgements recorded for review, not owner decisions. Each
follows from an approved decision plus the smallest-correct-design rule.

| # | Recommendation |
|---|---|
| ER-1 | **(narrowed by RM-1)** Under the provisional `RECORDED_PATH_UNVERIFIED` model, costs are money charges in their own columns and adjust no price (§6.3). This is the smaller of two commitments while COST_AUDIT A6 is UNKNOWN — **not** a claim that the bid/ask/mid question has been kept out of exit triggering. The side-dependent interpretation is unbound until the audit verifies it (§6.0) |
| ER-2 | `EquityCurve` is cumulative net PnL from a `0.0` baseline, mark-to-market on `close`; no invented account base (§8.2) |
| ER-3 | No Sharpe-like ratio, no percentage return or drawdown (§9.3) |
| ER-4 | Boundary close at the final row's `close`, flagged and rate-reported (§5.7) |
| ER-5 | Final-row intents are unexecuted and counted, never filled from beyond `eval_end` (§5.5) |
| ER-6 | Costs recognized at the trade's entry row (§6.3) — conservative for drawdown |
| ER-7 | Intent execution precedes bracket evaluation within a row; identical price, honest label (§5.4) |
| ER-8 | `take_profit_pips` / `stop_loss_pips` may be `None` — a bracket-free baseline is a materially different strategy family and the control the bracket is judged against |
| ER-9 | `gapped_exit` is recorded per trade alongside `ambiguous_exit` — gap-dominated results mislead exactly as ambiguity-dominated ones do |
| ER-10 | Exactly one `ExecutionSpec` per run; TP/SL sweeps are sweeps of runs (§10, §13) |
| ER-11 | Plane-B evaluators declare no column requirements, because both artifacts have closed schemas (§3.2) |
| ER-12 | `IntentFrame` persisted as `keys` + `target_exposure`; the trichotomy needs no side-car mask (§12) |
| ER-13 | Run-record schema version bump with a backward-compatible `scan_runs()` and a Phase-3-directory golden (§13) |
| ER-14 | Minimum two observed rows per simulated fold (R6) |
| ER-15 | The contingent cost/unit/price-model fields are grouped in one identified `ExecutionCostBinding` rather than spread across `ExecutionSpec` (§3.1, RM-1) — one subject for the audit register, and verified semantics attach as a unit |
| ER-16 | The fail-closed mechanism is one `None`-by-default constant plus an equality-and-digest check (§6.4, RM-3) — no service, no database, no registry, no runtime Markdown parsing |
| ER-17 | `binding_digest` is **declared by config and recomputed by the platform**, so a stale pin refuses instead of silently applying (§6.4) |

### 28.4 Implementation detail that does not belong at owner level

Module and file layout inside `simulation/` and `core/` · array dtypes and
buffer strategy · loop vs. partially vectorized bracket scanning (subject to
§24 and to §11's fixed arithmetic order) · exit-reason integer code values ·
test file organization · fixture row counts · the exact NPZ key names ·
error-message wording · which existing private helper is reused for canonical
JSON.

---

## 29. First implementation action — **historical, completed**

> The prescribed first action was completed: the implementation branch started
> with Slice 4B contracts before the simulator and orchestration layers.

Slice 4A is owner/evidence-bound and proceeds in parallel as broker
documentation arrives; it blocks no code. The first code action is therefore:

1. Branch from `main` at the merged planning commit, e.g. `codex/phase4b-contracts`.
2. Implement **exactly** §3.1 and §3.2 in `core` — `CommissionBasis`,
   `CostSemanticsStatus`, `ExecutionPriceModel`, `TradeExitReason`,
   `ExecutionCostBinding`, `VERIFIED_COST_BINDING` (as `None`), `ExecutionSpec`,
   `SimulationRequest`, `TradeLog`, `EquityCurve`, `TradeEvaluator` — with
   validation, refusals (including the fail-closed §14 R10–R13), read-only
   arrays and canonical params documents. **No simulator, no evaluator, no
   orchestration, no config field, no persistence.**
3. Extend `tests/test_architecture.py` for the exact new public surface and the
   §26 guard changes that apply to contracts only.
4. Run `./scripts/gate.sh` unfiltered; it must be green with Ruff and strict
   mypy clean.
5. Push the branch and request the focused independent review, exactly as
   Slices 2A and 3A were handled.

Nothing in Phase 4 may touch OOS, LOCKBOX, SNAPSHOT, RAW or CURATED
identities; the expected audit-log delta for the entire phase is **zero**.

---

## 30. Phase-4 completion checklist

Phase 4 is COMPLETE only when every item is green and independently verified at
a named commit with named evidence.

**Contracts and scope**
- [x] §3 public surface implemented exactly; nothing added, nothing missing.
- [x] ADR-0023 ACCEPTED and indexed; ADR-0020 Amendment 1 and ADR-0015
      Amendment 1 recorded.
- [x] No excluded concept exists anywhere (§2.3, §3.4), including as a seam.

**Execution semantics (release-blocking)**
- [x] All §17 fixtures exact against hand-computed truth.
- [x] No fill before the next observed row; no fill priced from a later row.
- [x] Final-row intents unexecuted and counted.
- [x] SL wins every ambiguous bar; ambiguity rate exact.
- [x] Gapped exits use the observed open; gap rate exact.
- [x] Re-entry and reversal exact; never more than one open position.
- [x] Every fold-policy simulation starts flat; boundary closes deterministic
      and rate-reported.
- [x] Fractional exposure refused before simulation.
- [x] Both leakage rigs green; all eight adversarial controls caught; coverage
      meta-test green; no tolerance, `skip` or `xfail` in any rig.

**Costs and audit**
- [x] Every `ExecutionSpec` and `ExecutionCostBinding` field explicit,
      validated, with no default.
- [x] Cost arithmetic matches §20 exactly, including the per-side vs
      round-turn factor of two.
- [x] `cost_semantics`, `binding_id`, `binding_digest` and
      `execution_price_model` recorded in identity, the manifest, the
      simulation summary and every Plane-B inventory row.
- [x] **`VERIFIED` is unreachable while `VERIFIED_COST_BINDING` is `None`**
      (§20 C11–C13); the guard pinning the constant is green (RM-3).
- [x] **The executable price path is declared, not assumed:** every fixture
      declares `SYNTHETIC_DECLARED`, real-data runs declare
      `RECORDED_PATH_UNVERIFIED`, and no document or source asserts the
      recorded path *is* the executable path (RM-1).
- [x] COST_AUDIT status accurate; every fact classified CR or NB; no CR fact
      promoted without named evidence; no plausible default anywhere in code or
      docs. **Audit closure (CP-13C) is not required for Phase-4 completion.**

**Evaluation and records**
- [x] Exactly the two §9 Plane-B evaluators; §9.3 exclusions absent.
- [x] Inventory exactly `F·S·E` (Plane A, no policy dimension) + `F·P·B`
      (Plane B, with policy dimension), `plane` field present.
- [x] `TradeLog`, `EquityCurve` and the upgraded `IntentFrame` persisted per
      §12; `COMPLETE` run directories immutable.
- [x] `scan_runs()` reads both Phase-3 and Phase-4 run directories.
- [x] Canaries K1–K4 behave exactly as §23 specifies.

**Determinism, boundaries, integrity**
- [x] Exact bitwise determinism for `TradeLog` and `EquityCurve`; no declared
      tolerance; verification never reads cache.
- [x] `FittedState` identity unaffected by execution configuration.
- [x] Architecture tests extended, never weakened (§26).
- [x] No table-library type, civil-time or UTC machinery on the new path.
- [x] **Zero gated access:** audit log byte-identical; D-6 fingerprint and all
      five view-manifest digests unchanged.
- [x] `./scripts/gate.sh` green unfiltered; Ruff and strict mypy clean.
- [x] Performance median under 60 s, or a recorded STOP-and-report.
- [x] Documentation duties (§27) complete.
- [x] Working tree clean; commits pushed.
- [x] **CP-16 independent review APPROVE and owner-approved.**

---

## 31. Explicit Phase-5+ deferrals

**Phase 5:** sweep generation (including TP/SL sweeps) · process-pool execution
· resumable walk-forward across runs · the SQLite run index · **multiple-testing
denominator semantics and any deflated metric** · comparison CLI · disk
telemetry · retention/GC.

> **Forward reference (2026-08-15, historical record above unchanged).** These
> items are now planned in
> [PHASE5_EXPERIMENT_OPS.md](PHASE5_EXPERIMENT_OPS.md) (Phase-5 planning
> COMPLETE, implementation NOT_STARTED). Two resolved differently from the
> wording above, by owner decision: **process-pool execution is whole-run only,
> folds stay sequential** (OD-5C,
> [ADR-0018](../adr/ADR-0018-execution-model.md) Amendment 1), and **no
> deflated metric ships in V1** — the denominator ships without any adjusted
> statistic (OD-5A,
> [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md)). §10's and
> §13's point stands and is strengthened: a TP/SL sweep is a sweep of runs, and
> under ADR-0024 each distinct execution configuration is part of the Plane-B
> *question*, so execution tuning enlarges the denominator visibly.
> **Retention/GC remains deferred and is not built in Phase 5 either** —
> Phase 5 ships disk telemetry only.

**By ADR, when a concrete need exists:** order/limit/stop/cancellation/staged-entry
intent schemas and the intent-schema registry that would then have a job ·
per-decision policy-emitted brackets · trailing and break-even stops · multiple
simultaneous positions · partial fills · slippage modelling · financing/swap ·
broker constraint enforcement (minimum stop distance, lot step) · margin and
leverage · portfolio construction and multi-symbol scope · additional Plane-B
metrics.

---

## 32. Status and gating

- **Phase-4 planning: COMPLETE (2026-08-14). Phase-4 implementation and TRAIN
  qualification: COMPLETE (2026-08-15).** RM-1…RM-4 record the planning review
  remediation and RM-5 records the implementation-time equity-notation repair;
  none changes an owner decision.
- **Slice 4A baseline record: COMPLETE. CP-13: GREEN** (evidence-classification
  gate). **COST_AUDIT: OPEN** for external evidence, collected in parallel.
  **CP-13C** (audit closure) is external and is not a Phase-4 completion
  condition (RM-4).
- OD-4A…OD-4K are recorded above;
  [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) is ACCEPTED;
  [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) Amendment 1 and
  [ADR-0015](../adr/ADR-0015-v1-single-symbol-scope.md) Amendment 1 are
  recorded. Slices 4B–4H are implemented at the named commits in §15/§24;
  CP-14 and CP-15 are GREEN.
- **Phase 3 is COMPLETE** (2026-08-14, CP-12 GREEN and owner-approved) and is
  the dependency of this phase.
- **Implementation blockers remaining: none.** The external
  broker evidence for [COST_AUDIT.md](../research/COST_AUDIT.md), which gates
  authoritative money claims **and any claim of verified execution-side
  semantics** — not implementation (§28.2).
- **No hypothesis, OOS or LOCKBOX research use occurred.** The sole real-data
  action was the sanctioned NORMAL/TRAIN infrastructure qualification in §24;
  it made no research claim and produced zero audit delta.
- **CP-16 is GREEN / OWNER APPROVED (2026-08-15).** Review of `f27ce0b`
  returned **APPROVE WITH NON-BLOCKING NOTES**. F-1…F-3 were remediated at
  clean pushed `4351b9a`; follow-up independent review passed, and N-1…N-9
  remained intentionally untouched. The remediation changed no production
  simulation semantics, accepted architecture or COST_AUDIT semantics.
- **Phase 4 is COMPLETE (2026-08-15).** Phase-5 **planning** completed later the
  same day ([PHASE5_EXPERIMENT_OPS.md](PHASE5_EXPERIMENT_OPS.md),
  OD-5A…OD-5E owner-approved, ADR-0024 ACCEPTED); Phase-5 **implementation**
  remains NOT_STARTED and no Phase-5 production code exists.

---

*This document is the normative Phase-4 plan and implementation record.
OD-4A…OD-4K are owner-approved (2026-08-14), RM-1…RM-5 are correctness
remediations and not owner decisions (§1.1), ADR-0023 is ACCEPTED at Revision
1, implementation/qualification are complete, and CP-16 is GREEN and
owner-approved.*
