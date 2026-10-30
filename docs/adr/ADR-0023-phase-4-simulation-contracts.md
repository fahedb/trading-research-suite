# ADR-0023 — Phase-4 simulation contracts: bracket execution, TradeLog/EquityCurve, Plane-B evaluation

**Status:** ACCEPTED · **Date:** 2026-08-14 · Added during Phase-4 planning ·
**Revision 1: 2026-08-14** (pre-merge review remediation) ·
**Amendment 1: 2026-08-22** (per-trade brackets — see below) ·
**Amendment 2: 2026-08-27** (non-interruptible brackets — see below)

> **Amendment 1 — per-trade brackets, one simulator identity (2026-08-22).**
> This ADR's "When to reconsider" named two triggers: a research family that
> cannot be expressed with one fixed bracket pair — *"including per-decision
> brackets"* — and the arrival of a second intent schema. Both fired. The
> `zone_trader_v13` provider already computes and stores a per-event
> ATR/zone-edge stop and an R-multiple target, and
> [ZONE_TRADER_PLANE_B_FIXED_BRACKETS_V1.md](../research/ZONE_TRADER_PLANE_B_FIXED_BRACKETS_V1.md)
> records the consequence plainly: *"The source strategy's per-event ATR/zone-edge
> stop and 2R target are not representable by V1 Plane B."* Every fixed-bracket
> Plane-B result therefore measured a different strategy from the one under study.
>
> **What changed, exactly:**
>
> 1. **A second intent schema, `bracketed_exposure_intent_v1`**, carrying two
>    additional float64 channels beside `target_exposure`:
>    `take_profit_distance` and `stop_loss_distance`, in **price units**. A
>    finite value is a distance; `NaN` states that this instruction supplies
>    none for that side; zero, negative and infinite values are **refused** at
>    the intent boundary rather than reinterpreted, exactly as §3 refuses a
>    fractional exposure. Distances are in price units and not pips precisely so
>    that `price_units_per_pip` — a contingent field of the identified cost
>    binding under §8.2 — never has to be duplicated into a DecisionPolicy's
>    identity. `target_exposure_intent_v1` is **unchanged**, so every artifact
>    that declares it keeps its original meaning.
>
> 2. **The simulator identity does not split.** `bracket_single_position_v1`
>    keeps its name and every semantic in §4–§7: next-observed-row entry at the
>    `open`, at most one open position, stop-loss wins an unprovable intrabar
>    ordering, gapped opens priced at the observed open, per-fold flat start,
>    forced boundary close, unexecuted final-row intents. It now declares both
>    schema ids and resolves each side of each trade's bracket by one rule:
>
>    > **an opening instruction that supplies a finite distance for a side wins;
>    > otherwise that side takes the run's fixed `ExecutionSpec` value; if
>    > neither exists, that side has no bracket.**
>
>    The rule is per side, per opening instruction, and has no third case.
>    A resolved bracket is **immutable for the life of the trade**: a later row
>    can neither mutate an open trade's levels nor carry a previous trade's
>    levels into a later one, and both failure modes are release-blocking
>    adversarial controls (`BracketMutationControl`, `BracketCarryForwardControl`).
>    A reversal binds the bracket of the instruction that caused it. Re-entry
>    after a bracket exit requires a fresh instruction, which supplies a fresh
>    bracket or none.
>
> 3. **`ExecutionSpec` is untouched** — same six fields, same canonical
>    parameter document. Every pre-existing config therefore canonicalizes to
>    byte-identical JSON and **every historical `experiment_fingerprint` is
>    unchanged**.
>
> 4. **`TradeLog` records what it used.** Two float64 columns,
>    `take_profit_price` and `stop_loss_price`, hold the exact levels the
>    simulator compared each trade's bars against, with `NaN` for a side that had
>    no bracket. The container's `format_version` becomes **2**; no other
>    artifact format changes and no historical artifact is rewritten. This is
>    additive evidence: for a fixed-bracket run the recorded levels are the ones
>    §5 always computed and discarded, and the twelve original columns are
>    bit-identical. It is what lets a reader present a trade's bracket without
>    reconstructing it from configuration — which per-trade brackets make
>    impossible anyway.
>
> 5. **No registry was created.** §9's reconsideration point is discharged by
>    the mechanism §9 already specified: each simulator declares its supported
>    schema ids as a frozen literal tuple, and a mismatch is refused. Two
>    schemas and one simulator still give a resolution mechanism nothing to
>    resolve.
>
> 6. **A new release-blocking causality rig** covers what per-trade brackets
>    make dangerous: `tests/test_decision_leakage.py` re-runs every registered
>    policy on every strict prefix of its own `ModelOutputFrame` and requires the
>    decision at the cut row to be bit-identical. Two never-registered
>    adversaries prove it is not vacuous.
>
> **What did not change:** the methodology boundary in §1, the closed
> non-goals in §2 (no trailing stops, no partial exits, no order engine, no
> pyramiding, no portfolio, no position sizing), the cost model and its
> fail-closed evidence gate in §8, and the Plane-B identity in §10. §10's
> observation about TP/SL sweeps is refined in
> [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7 rather than weakened.

> **Amendment 2 — non-interruptible bracket execution (2026-08-27).**
> The owner authorized a replacement ULS+PUSH TRAIN corpus in which an open
> trade remains open until its immutable take-profit or stop-loss is reached.
> The original `bracket_single_position_v1` semantics and implementation stay
> unchanged so every historical run remains reproducible. A second explicit
> execution identity, `bracket_non_interruptible_v1`, shares the same entry
> timing, bracket resolution, gap, ambiguity, cost, evidence and fold-boundary
> rules, but differs in exactly one respect:
>
> - while a position is open, every later LONG, SHORT or FLAT intent is ignored;
> - an ignored intent is never queued and cannot open or reverse a position
>   after the current trade closes;
> - after a TP or SL close, only a fresh later non-FLAT intent may open the next
>   trade; and
> - `BOUNDARY_FORCED_CLOSE` remains mandatory, because carrying a trade beyond
>   an evaluation fold would read outside that fold and contaminate evidence.
>
> This is a second simulator identity rather than a flag: target-change handling
> changes trade paths, costs, opportunity availability and results, so it must
> be visible in `ExecutionSpec`, experiment fingerprints and persisted
> artifacts. It adds no registry or generalized order engine; orchestration
> resolves the closed two-identity set explicitly. `TARGET_CHANGE` remains a
> valid historical exit reason but is unreachable for the new identity.

> **Revision 1 — what changed.** A pre-merge review of the planning branch found
> that §8 bound the *executable price path* permanently ("costs are money
> charges, so exit triggering is unaffected") on top of an UNKNOWN price side.
> If the recorded series is one-sided, the executable entry price and every
> bracket level derived from it are displaced by about the spread, which changes
> **trigger timing**, not just accounting — so the original wording froze a
> known-possibly-wrong execution model into the contract. §8 is therefore
> rewritten to separate the price-path semantics from the monetary arithmetic
> and to leave the side-dependent interpretation **explicitly unbound** until
> the audit verifies it; §2 and §5 pick up the resulting vocabulary. This is a
> correctness remediation required by review, **not** a new owner decision:
> OD-4A…OD-4K are unchanged, and every methodology boundary below stands
> exactly as accepted.

## Context

Phase 3 ends at `IntentFrame`. Phase 4 adds the second evaluation plane:

```
IntentFrame -> Simulator -> TradeLog / EquityCurve -> Plane-B evaluation
```

That introduces new **public contracts** — an execution configuration, a
simulator, two artifact carriers and a trade-evaluator protocol — which is
exactly the change [ADR-0001](ADR-0001-record-architecture-decisions.md) and
`CLAUDE.md`'s change-control rule require an ADR for, and the same reason
[ADR-0021](ADR-0021-columnar-feature-contract.md) and
[ADR-0022](ADR-0022-phase-3-research-contracts.md) exist.

It also introduces the first component in the platform that can produce a
number denominated in money. Money is persuasive, and an execution engine has
an unbounded appetite for generality: order types, partial fills, trailing
stops, position sizing, portfolio construction, slippage models. None of that
has a current research justification, and every piece of it would have to be
maintained and leak-audited. This ADR therefore records the boundary as
tightly as it records the mechanism.

The owner approved the Phase-4 direction on 2026-08-14 as **OD-4A…OD-4K**,
recorded in [PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §1.

## Decision

### 1. Methodology boundary — the simulator is second, never first

The research workflow is, and remains:

```
research idea -> Plane-A forecast evaluation -> evidence of edge
             -> DecisionPolicy -> simulator -> Plane-B trade evaluation
```

**The simulator is a cost-and-execution filter applied to an edge that Plane A
already found. It is not an edge-discovery mechanism.** Take-profit distance,
stop-loss distance, execution timing and cost assumptions may not substitute
for establishing predictive edge in Plane A first. This is a normative research
rule, restated in [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7, and
it is the reason Phase 4 deliberately keeps the execution surface small enough
that there is very little to tune.

[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md)'s "most
hypotheses should die in Plane A, cheaply" is unchanged and is the operative
rule.

### 2. One simulator, closed scope

The original V1 shipped one simulator, `bracket_single_position_v1`. Amendment
2 adds the closed `bracket_non_interruptible_v1` alternative. Both are pure
deterministic functions of (execution rows, `IntentFrame`, `ExecutionSpec`) and
support:

- one symbol, one instrument, one market frame (unchanged from
  [ADR-0015](ADR-0015-v1-single-symbol-scope.md));
- **at most one open trade at a time**;
- a fixed configured lot size, identical for every trade;
- LONG / SHORT / FLAT target states;
- one take-profit distance and one stop-loss distance per trade, both from the
  entry fill price **as interpreted by the declared execution-price model**
  (§8), each supplied either by the opening instruction or by the fixed
  execution configuration under Amendment 1's resolution rule, and each
  immutable once the trade is open;
- deterministic, explicitly-united costs;
- a `TradeLog` and an `EquityCurve`.

It ships **without**, and V1 does not build: portfolio logic · multi-symbol ·
simultaneous trades · partial fills · trailing stops · a general order engine ·
limit/stop order placement or order-book simulation · margin, leverage or
liquidation · advanced or equity-dependent position sizing · pyramiding ·
netting/hedging modes · a slippage framework · a position-limit framework · an
intent-schema registry · a `PortfolioPolicy` object · plugin seams for any of
the above.

Each of those is addable later by ADR when a concrete research need exists.
"We may need it later" is explicitly insufficient.

### 3. Discrete fixed-lot intent semantics — refusal over reinterpretation

The simulator declares support for the two frozen intent schema ids recorded
by Amendment 1: unchanged `target_exposure_intent_v1` and
`bracketed_exposure_intent_v1`. The value restriction below remains exact for
the shared `target_exposure` channel in both schemas:

| `target_exposure` at an observed row | Meaning to this simulator |
|---|---|
| `+1.0` | target LONG at the configured fixed lot size |
| `0.0` | target FLAT |
| `-1.0` | target SHORT at the configured fixed lot size |
| `NaN` | explicit abstain — no new instruction; prior state is held |
| row absent | no statement was produced — prior state is held |
| any other finite value (e.g. `0.7`) | **refused, explicitly, before any simulation begins** |

A fractional exposure is **never** reinterpreted as a fraction of equity, a
percentage of capital, a fraction of the lot size, or a sign-only instruction.
Each of those is a different, unverified position-sizing decision, and silently
picking one would make an unaudited assumption look like a platform semantic.

**This does not change [ADR-0009](ADR-0009-generic-output-and-intentframe-family.md).**
`TargetExposureIntentV1` keeps its `[−1,+1]` domain and its
absent/`NaN`/`0.0` trichotomy exactly as accepted. What is recorded here is a
**simulator capability restriction**, which ADR-0009 already provides for
("Simulators declare which intent schemas they support; mismatches fail at
resolve time"), extended in the only honest direction available: a simulator
may also refuse values inside a schema it cannot execute. No intent schema
changes, and no `ResearchModel` or `DecisionPolicy` contract changes.

### 4. Execution timing — next observed row, execution semantics only

An intent observed at row `T` becomes eligible at the **next observed row
`T+1`** and is filled at that row's `open`.

- The axis is **observed rows**, exactly as
  [ADR-0022](ADR-0022-phase-3-research-contracts.md) §9 and
  [ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) require. No elapsed-clock,
  session, weekend, holiday, outage or closure inference exists anywhere in the
  simulator, and no UTC or civil-time reasoning is permitted.
- This is an **execution assumption** and nothing else. It is *not* derived
  from, and may not be justified by, ADR-0022 §10's Plane-A alignment policy
  (`decision_delay_rows = 1`); the two are independent choices that happen to
  share an arithmetic shape. No repository document may state otherwise.
- An intent at the **last observed row of the execution range** has no next
  observed row inside that range and is therefore **not executed**. It is
  counted and recorded, never filled from data beyond the point-in-time
  boundary. Reading one row past `eval_end` to fill it would violate
  [ADR-0012](ADR-0012-two-tier-splits.md) Amendment 2, under which `eval_end`
  *is* the point-in-time boundary.

### 5. Bracket exits, intrabar ambiguity, and gap opens

For an open position, each observed row from the fill row onward is checked
against the take-profit and stop-loss levels derived from the entry fill price.
Throughout this section, "the observed `open`/`high`/`low`" means **the
executable price under the declared execution-price model** (§8), which under
V1's provisional `recorded_path_unverified` model is the recorded column value
itself. The rules below are about *which* observed condition prices an exit;
they do not settle which side of the market that observation represents.

1. **Stop-loss wins any unprovable intrabar ordering.** If both levels lie
   within `[low, high]` of the same row, OHLC data cannot establish which was
   touched first. The exit is the **stop loss**, the trade is flagged
   `ambiguous_exit`, and the run reports an **ambiguity rate** so a result
   dominated by unresolvable bars is visible rather than flattering.
2. **A gapped open uses the observed open.** If a row opens at or beyond a
   bracket level, the exit price is that row's **actual observed `open`** — not
   the bracket price, which was not available. This applies symmetrically to
   stop-loss (worse than the level) and take-profit (better than the level).
   Such exits are flagged `gapped_exit` and reported as a rate for the same
   reason.
3. Within one row the order is fixed and deterministic: any intent execution
   happens first at the `open`, then the resulting position's bracket is
   evaluated against that row's `high`/`low`. A position may therefore open and
   close on the same row.

Both flags are identity-bearing artifact content, not diagnostics printed and
discarded.

### 6. State-following targets — re-entry and reversal

Intent is a **target state**, not an edge trigger.

- If a trade is stopped out and the policy keeps emitting `+1.0`, the simulator
  **re-enters LONG** at the next eligible execution row. There is no hidden
  cooldown and no edge-triggered interpretation.
- A one-shot instruction is expressed by the researcher as a **sparse
  `IntentFrame`** (ADR-0009's sparsity amendment), which is exactly what
  sparsity is for.
- A reversal (`+1.0` → `-1.0`) closes the open trade and opens the opposite
  trade **at the same execution row and the same `open` price**, as two
  separate `TradeLog` records, with costs charged for both. The
  one-open-trade-at-a-time invariant is never violated, not even momentarily.

### 7. Fold boundaries — no state crosses a boundary

Simulation is per `(fold, policy)`. Each such simulation **starts flat**, and
no position, cash, cost or equity state carries across folds or across
policies. Any position still open at the last observed row of the fold's
evaluation range is **closed deterministically** at that row's `close`, flagged
`boundary_forced_close`, and reported as a rate.

Folds may overlap when `step_rows < eval_rows`
([PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) §2.7); per-fold
independence is what keeps overlapping folds from silently sharing execution
state.

### 8. Two separate bindings: the executable price path, and the money charged

Every cost parameter is explicit, united and **without a default**. The platform
never infers a pip size, a contract size, a price side, a commission basis or a
currency conversion. Revision 1 makes the *shape* of that promise precise, by
separating two things the first draft merged:

| | Bound in V1? |
|---|---|
| **Executable price path** — which prices an entry and an exit transact at per direction, and therefore which series the take-profit and stop-loss levels are compared against | **Provisionally, under a name.** The side-dependent interpretation is **UNBOUND** until COST_AUDIT A6 (price side) and B3 (spread symmetry) are VERIFIED |
| **Monetary cost arithmetic** — how much money a trade is charged for spread and commission | **Fully.** Explicit money amounts in their own columns ([PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §6.2) |

**8.1 The provisional price model, named rather than assumed.**
`ExecutionPriceModel` is a closed enum with exactly two V1 values:

- `recorded_path_unverified` — fills and both bracket levels are read from the
  recorded OHLC series exactly as stored, with the price side of that series
  UNKNOWN. **Legal only while `cost_semantics = unverified`.** Its known defect
  is stated, not hidden: if the series is one-sided, one direction's executable
  entry differs from the recorded `open` by about the spread, and the levels
  derived from that entry are displaced with it, so trigger *timing* — not only
  accounting — is wrong by that amount for that direction.
- `synthetic_declared` — the fixture defines its own instrument and declares
  that its rows *are* the executable path for both directions. Test-only, and
  unreachable from config resolution. It is what makes hand-computed fixture
  verification of the arithmetic valid while the real-data question is open;
  it asserts nothing about the dataset.

**No third value exists in V1, deliberately.** The verified, side-dependent
model — and the exact rules by which it derives executable entry/exit prices
and bracket levels — is written by the amendment that closes the audit, from
verified A6 and B3, and it re-derives the spread money charge at the same time
so the spread is not counted twice. Writing it now, in either direction, would
be exactly the invented cost semantic
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) exists to prevent.
Consequently **no repository document may state that the recorded price path is
the executable price path as a settled fact**, and results produced under the
provisional model are not verified execution outcomes.

**8.2 The contingent fields live in one identified binding.**
`spread_pips`, `commission_per_lot`, `commission_basis`,
`price_units_per_pip`, `contract_size`,
`account_currency_per_quote_currency`, `execution_price_model` and
`cost_semantics` are fields of a frozen `ExecutionCostBinding` value object
nested inside `ExecutionSpec`, carrying a `binding_id` and a `binding_digest`.
They are grouped because they share one property: their *meaning* is contingent
on facts that are not yet established. `lot_size`, `take_profit_pips` and
`stop_loss_pips` stay directly on `ExecutionSpec` — they are researcher choices,
true by construction.

This answers the question the review raised — whether `spread_pips` belongs in
a frozen public spec before the price-side audit closes. It does not belong
there *as a free-standing field*, because under a verified one-sided model the
same magnitude also displaces executable prices; carried inside an identified
binding, the verified semantics attach to it later as a unit, and no component
ever claimed them early.

**8.3 The evidence gate is fail-closed.** The evidence state of every cost fact
is recorded in [COST_AUDIT.md](../research/COST_AUDIT.md); closure-required
facts must be **VERIFIED with exact evidence**, and effects V1 does not model
are declared limitations that never block closure (COST_AUDIT §1.1).
`cost_semantics = verified` is constructible **only** against `core`'s
`VERIFIED_COST_BINDING` constant, which is `None` until the closing commit
creates it; absent that binding, or on any field or digest mismatch, the run is
**refused at config resolution** rather than recorded as a documentation error
([ADR-0020](ADR-0020-cost-semantics-quarantine.md) Amendment 1 clauses 5–7). A
run marked `unverified` is marked permanently, is never retroactively upgraded,
and its money-denominated Plane-B results may not be represented as
authoritative real-money estimates.

### 9. Contracts, placement and dependency rules

New `core` types (frozen, read-only NumPy columns, no table-library type in any
public signature): `ExecutionSpec`, `ExecutionCostBinding`,
`ExecutionPriceModel`, `SimulationRequest`, `TradeLog`, `EquityCurve`,
`TradeExitReason`, and the `TradeEvaluator` protocol, plus the single frozen
constant `VERIFIED_COST_BINDING` (§8.3). Exact field lists are closed in
[PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §3.

- `simulation` imports **only `core`** — the layer budget in
  `tests/test_architecture.py` already says so and does not change. In
  particular `GapIndex` lives in `data` and is therefore not merely unnecessary
  to the simulator but architecturally unreachable from it; observed-row
  execution needs no gap oracle, because a bar that does not exist is not a
  fill opportunity.
- Plane-B evaluators live in the existing `evaluation` layer. No new layer, no
  change to the dependency map.
- `SimulationRequest` is built **only** in `experiments`, from an
  already-issued, already-authorized `MarketFrame`, exactly as Phase 2 built
  `FeatureRequest` and Phase 3 built `OutcomeRequest`. The simulator never
  chooses which rows it can see, never loads data, and holds no grant.
- `ExecutionSpec` (with its nested `ExecutionCostBinding`) is a **configuration
  value object, not a code plugin**. There is no execution-spec registry and no
  execution-spec source hash; it enters `experiment_fingerprint` through the
  resolved config, which
  [ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) already covers, so
  the fingerprint algorithm is unchanged. `VERIFIED_COST_BINDING` is likewise
  not a registry: it is one frozen constant, `None` in V1, resolving evidence
  state rather than code, with no lookup, registration or plugin mechanism.
- The simulator declares its supported intent schema ids as a **frozen literal
  tuple**. **No intent-schema registry is created**: Amendment 1 adds a second
  schema, but both schemas are consumed by the same single simulator, so a
  registry still has no resolution job. A registry is reconsidered only when
  another simulator or another consumer creates an actual resolution choice.

### 10. Plane-B evaluation and the trial denominator

Plane-B evaluators consume a `TradeLog` and an `EquityCurve` and **never**
recompute upstream research logic — the same structural guarantee ADR-0022 §4
gives Plane A.

Unlike a `ModelOutputFrame`, both Plane-B artifacts have **closed schemas**, so
a Plane-B evaluator declares only the metric names it emits: there are no
free-form columns for a declaration to protect against. Every metric still
reports its effective sample count.

Plane B **does** carry the policy dimension that Plane A deliberately does not.
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) Amendment 1 admits the
`DecisionPolicy` dimension "where a decision plane was evaluated", and Plane B
is that place. A Plane-B evaluation-inventory entry is identified by

```
fitted state + model-output artifact + DecisionPolicy + ExecutionSpec
            + TradeLog/EquityCurve artifact identity + Plane-B evaluator
```

so a run with `F` fitted states, `P` policies and `B` Plane-B evaluators
records `F·P·B` Plane-B rows **in addition to** its `F·S·E` Plane-A rows. No
ADR-0011 amendment is required; this is the case that amendment anticipated.

A consequence worth stating plainly: fixed fallback TP/SL values live in the
run's single `ExecutionSpec`, while any per-trade bracket-generation rule and
its parameters are identity-bearing parts of the `DecisionPolicy`; the emitted
distances are preserved in its intent artifact. A sweep that varies either the
fixed values or the per-trade generation parameters is therefore a sweep of
**runs**, each contributing its own inventory rows. Tuning execution parameters
is visibly counted against the multiple-testing denominator instead of hiding
inside a single run — which is the intended friction, given §1.

## Alternatives considered

- **A general order engine now** — limit/stop/cancel orders, partial fills and
  an order book. Nothing in the research programme produces such intents; it
  would be a large, permanently leak-audited surface built for a hypothetical
  user.
- **Mapping fractional exposure onto lot size or equity** — convenient, and
  exactly the kind of invented semantics ADR-0020 exists to prevent. Refusal is
  louder and cheaper to correct.
- **Take-profit / stop-loss emitted per decision by the policy** — historical
  alternative whose reconsideration trigger fired after Phase 4. Amendment 1
  accepted the separate `bracketed_exposure_intent_v1` schema after its own
  architecture review; `TargetExposureIntentV1` was not modified.
- **Take-profit wins an ambiguous bar** — flatters every result by exactly the
  amount the data cannot resolve. Stop-loss-wins is the conservative direction
  and is the standard defensible choice; the ambiguity rate is what keeps it
  honest.
- **Filling at bracket prices through a gap** — assumes liquidity at a price
  nobody traded. Rejected.
- **Declaring the recorded OHLC path to be the executable path** (Revision 1's
  subject) — the shortest route to a working simulator, and a permanent,
  invisible error if the series is one-sided. Rejected: the model is named,
  provisional, and legal only under `unverified` semantics.
- **Inventing bid/ask/mid semantics now** — half-spread per side "because that
  is usual" is a guess wearing a formula. Rejected; the decomposition depends on
  A6 and B3, which are UNKNOWN.
- **A general quote engine** (per-side price series, spread models, synthetic
  ask construction) — far more than the problem requires, and it would have to
  be built before the facts that define it exist. Rejected in favour of the
  minimum: one named enum with two values and one closure obligation.
- **An inert `PortfolioPolicy` seam** — see
  [ADR-0015](ADR-0015-v1-single-symbol-scope.md) Amendment 1: with one symbol
  and one open trade there is nothing for it to pass through, and the 2026-08-06
  independent review already called it borderline.
- **A frictionless / zero-cost simulation mode** — a mode flag that makes costs
  disappear invites reporting the flattering variant. Costs are always
  configured explicitly; a researcher who wants a zero-cost sensitivity check
  declares zeros and those zeros are recorded in the run's identity.
- **Percentage returns, Sharpe ratios and percentage drawdown** — all require an
  account base the platform does not have and would have to invent (§8 of
  PHASE4_SIMULATION.md). Absolute money PnL and absolute drawdown are honest;
  a ratio statistic that *looks* like a Sharpe would be read as one.

## Consequences

Plane B becomes available for hypotheses that already survived Plane A, with an
execution surface small enough to specify exhaustively, hand-compute in
fixtures and audit for leakage. The cost is deliberate: the first strategy that
genuinely needs trailing stops, multiple positions or fractional sizing stops
and writes an ADR. That is the intended friction, and it is the same friction
ADR-0022 §6 applied to carried model state.

Money-denominated results carry a permanent, machine-readable evidence status
until the cost audit closes, so an unverified number can never quietly become a
quoted one — and, since Revision 1, that status is **enforced against a
repository-controlled binding rather than trusted from configuration**, so the
worst case of a mis-declared config is a refused run.

Revision 1 also leaves the platform able to *correct* its execution semantics
when the evidence arrives: because the side-dependent price interpretation was
never bound, closing the audit adds a verified model instead of having to
defend a guess. The price is one obligation carried forward — the closing
commit must write the executable-price rules and create the binding constant
together (COST_AUDIT §8) — and one honest limitation in the meantime: real-data
Plane-B results are unverified in *execution* as well as in *money*.

## When to reconsider

Amendment 1 discharged the per-decision-bracket and second-schema triggers.
Reconsider again when a concrete research family cannot be expressed as one
fixed-lot position with immutable per-trade brackets over observed rows — for
example multiple simultaneous positions or genuinely continuous exposure
sizing · when another simulator or consumer creates an actual schema-resolution
choice · **when broker documentation verifies COST_AUDIT A6
and B3, at which point §8's verified execution-price model must be written, the
spread charge re-derived, and `VERIFIED_COST_BINDING` created — this is an
obligation, not an option** · when multi-symbol
scope arrives and portfolio construction becomes a real stage
([ADR-0006](ADR-0006-fixed-stage-pipeline.md)).

## Related

[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0015](ADR-0015-v1-single-symbol-scope.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) ·
[ADR-0022](ADR-0022-phase-3-research-contracts.md) ·
[COST_AUDIT.md](../research/COST_AUDIT.md) ·
implementation detail: [docs/phases/PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md)
