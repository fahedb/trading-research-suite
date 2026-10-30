# Research Policy — Data Discipline and Statistical Honesty

Date: 2026-08-06 · Status: adopted with V1 (dates are policy, revisable by ADR)

This document explains, in plain language, the rules that keep our research
results believable. It is written for a technically capable reader who may
not know formal quant-research methodology.

> **Normative source.** The split dates below are summarized here for
> explanation. [ADR-0012](../adr/ADR-0012-two-tier-splits.md) is the single
> normative source; where this document and ADR-0012 differ, ADR-0012 wins.

---

## 1. Why we split the data at all

Historical market data is the only exam we can take before risking money.
The problem: if you look at the answer sheet while studying, your exam score
stops meaning anything. Every time you evaluate an idea on a piece of data
and then react — tweak a parameter, discard the idea, try a variant — that
piece of data has leaked into your decision-making. It is no longer an
honest test; it has become part of your training process.

So we cut the history into zones with different rules:

| Zone | Years (V1 policy) | What it is for |
|---|---|---|
| **TRAIN** | 2020–2023 | Everyday development. Fit models, compute features, iterate freely. |
| **VALIDATION** | 2024 | Frequent checking of ideas developed on TRAIN. Expect to burn through it; that is its job. |
| **OOS** (out-of-sample) | 2025 | Rare, deliberate checks of ideas that already look good on VALIDATION. Access is explicit and recorded. |
| **LOCKBOX** | 2026-01-01 → final observed bar on 2026-07-31 (native-wallclock-v1) | Untouched final exam. Opened only through a formal ceremony, ideally once per finished strategy. |

The platform materializes each zone as a separate, hash-pinned dataset view.
Loading "the data" cannot accidentally include a zone you did not ask for.

The same protection covers the **parent datasets**. The raw and curated
datasets physically contain the lockbox period, so anything whose range
extends past the end of OOS — raw, curated, snapshot, lockbox — is hidden
from ordinary discovery and requires the same unlock ceremony. You cannot
sidestep the lockbox by asking for "the whole dataset"
([ADR-0014](../adr/ADR-0014-snapshot-and-view-roles.md)).

## 2. How a test set quietly dies

Nothing dramatic happens when you evaluate on OOS once. The rot is
cumulative:

1. You check idea A on OOS. It fails. You abandon A.
2. You check idea B. It fails. You tweak B into B'.
3. B' passes. You feel confident.

But B' passed *a test whose questions you had already seen twice*. Across
enough cycles, OOS becomes just another validation set — your ideas have
evolved to fit it, without any single step feeling like cheating. This is
why OOS access is **explicit, non-default, and permanently recorded**: the
record lets us honestly say how many times we have peeked, and discount our
confidence accordingly.

## 3. Why the lockbox is different

The lockbox is the one zone we protect from ourselves *structurally*, not
just by good intentions:

- It never appears in default dataset discovery.
- Opening it requires an explicit CLI unlock flag AND a structured reason in
  the experiment config — both, so neither a copied config nor a habitual
  flag suffices.
- A write-ahead audit event is recorded *before* any data is returned. No
  audit, no data.
- Any run that touched it is permanently marked `lockbox_touched`.

**When may the lockbox be opened?** When a strategy is finished: developed
on TRAIN, selected on VALIDATION, survived its rare OOS checks, parameters
frozen, and we would deploy it if the lockbox result is acceptable. The
lockbox answers one question — "does the finished thing work on data nobody
ever saw?" — and it can only answer it the first time.

**What happens after access?** The result stands, good or bad. The runs are
flagged forever. That lockbox period is treated as spent for that line of
research: you cannot iterate against it and call later results "lockbox
results". Fresh lockbox data accumulates only with time (a future dataset
snapshot under a new identity).

Be honest about the part no mechanism can undo: **opening the lockbox
contaminates the researcher, not just the data.** Once you know how the
strategy did on 2026H1, every later decision you make is informed by it —
including decisions about "a different" strategy. The flags and audit records
track the data; nothing tracks what you now know. This is why the ceremony
exists, and why a genuinely fresh final test requires newly accumulated
lockbox data under a new snapshot identity.

## 4. Walk-forward folds, and the two-tier rule

Walk-forward research repeatedly fits on one span and evaluates on the next
(fit 2020 → evaluate 2021 Q1, roll forward, repeat). Those rolling windows
are **procedural folds** — part of a modeling procedure, not integrity
boundaries.

- **Tier 1 (integrity views):** TRAIN / VALIDATION / OOS / LOCKBOX —
  materialized, hash-pinned, enforced by the loader. These are the walls.
- **Tier 2 (procedural folds):** generated at runtime, must lie **entirely
  inside one Tier-1 view** (the platform checks), and every fold boundary
  is recorded in the run manifest.

Folds never cross the walls. A walk-forward study that wants to span
TRAIN + VALIDATION is really a promotion decision, not a fold — and the
platform has a proper form for it: a **recorded, ordered union of adjacent
views** (TRAIN + VALIDATION, each pinned by hash), never a hand-written date
range.

### Purge: the leak that lives *inside* a view

Fold boundaries need their own protection, because the walls do not help
here. Suppose you fit on 2020–2021 and evaluate on 2022, and your model
learns from a target measured 60 bars ahead. The training samples in the last
60 bars of 2021 had their targets computed from bars in *2022* — the
evaluation period. The model has seen a sliver of its own exam. The same
applies to a similarity library whose reference windows carry future-outcome
statistics.

So the platform **purges** those samples automatically. Each training sample
has an information span — the earliest bar its features read, its own bar,
and the last bar its target needed — and any sample whose span touches the
evaluation range is dropped from the fit. The purge is derived from what the
experiment actually declared (feature lookback, OutcomeSpec horizon and
delay), not from a fixed number of bars, and it is recorded per fold in the
run manifest. Shrinking it requires an explicit reason that is recorded too.
An **embargo** — an extra buffer after the evaluation range — can be added on
top where a method needs it. See
[ADR-0012](../adr/ADR-0012-two-tier-splits.md).

## 5. Lookahead leakage

Leakage = information from time t+1 or later influencing anything the
platform believes was computed at time t. Classic ways it happens:

- an indicator centered on its window instead of trailing;
- normalizing with statistics computed over the whole dataset ("just to
  scale it") before splitting;
- a similarity search whose reference library contains windows overlapping
  the evaluation period;
- evaluating a decision at bar t using bar t's own close as the fill price
  when you could not have acted until t+1.

> **Read the last one narrowly.** It is about **execution**: assuming a fill at
> a price you could not have transacted at. It is *not* a claim that measuring
> an outcome from a price observed at bar *t* is inherently leaky — a forecast
> defined from a price genuinely known at decision time to a later price is a
> legitimate, leakage-free construction. V1 nonetheless measures outcomes from
> bar *t+1* onward, but that is an **owner-approved research-definition choice**
> (`decision_delay_rows = 1`), recorded in
> [ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §10, **not** a
> leakage rule and **not** an execution model. Execution and cost semantics stay
> quarantined until the Phase-4 audit
> ([ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md)).

Leaky results look wonderful and are worthless. The platform's answer is
structural: point-in-time views make future rows physically absent; the
**OutcomeSpec is the only component allowed to read forward**; leakage
sentinel tests perturb future bars and fail if anything computed "in the
past" changes. Treat any suspected leak as a release blocker, never a
cleanup item.

**One legitimate exception, tightly controlled.** Supervised and statistical
models must see future-derived *targets while training* — that is what
training means. Doing that ad hoc inside a model is the leak above; doing it
through the platform is not. So targets reach `fit` only as a
**TrainingTargetFrame** that the orchestrator computes from an OutcomeSpec
the config names explicitly, bounded by the training range and pinned into
the fitted state's identity. Features never see targets, inference never sees
targets, and decision policies never see targets
([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)).

## 6. Multiple testing — why trying many things "finds" fake gold

Flip 100 coins ten times each and the best coin will look impressively
skilled. Test 100 strategy variants on the same years and the best backtest
will look impressively profitable — by luck alone. The more things you try,
the better your best result looks, *even if nothing works*.

The only defense is knowing the denominator: how many things were tried.
Therefore:

- **Every completed hypothesis evaluation counts as one statistical
  research trial — including bad, disappointing, and abandoned results.**
  A run that reached a valid forecast or trade evaluation is a trial even
  if the numbers are terrible.
- **A run is not automatically one trial.** One run can evaluate many
  decision policies, several outcome definitions and several horizons over a
  single inference pass — that is closer to many correlated trials than to
  one. Every run therefore records an **evaluation inventory** listing the
  combinations it actually evaluated (model/fitted state, policy,
  OutcomeSpec, horizon, metric family). Counting runs instead of evaluations
  would understate the denominator by exactly the factor our cheapest
  feature encourages.
- **Pure infrastructure failures are NOT statistical trials**: a syntax
  error, a package/environment failure, disk I/O failure, a crash before
  evaluation, or a contract failure caused by a platform bug produced no
  evaluation, so it does not inflate the denominator. It is still
  permanently recorded as a failed execution.
- **A descriptive run that ends before any evaluation** — storing model
  output for inspection, say — is a valid recorded run and not a statistical
  evaluation.
- **Nothing is ever deleted or hidden to "clean up" results.** Deleting
  failures is how a research program lies to itself. The registry keeps
  everything.

### How the inventory becomes a denominator — **decided 2026-08-15**

*(Normative source: [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md),
which closes the question [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md)
Amendment 1 left open. Owner-approved as OD-5A.)*

**One trial is one distinct `(hypothesis, question)` pair that produced at
least one evaluation.** The *hypothesis* is the experiment fingerprint; the
*question* is built from what the evaluation inventory already records — which
model output columns were scored, against which OutcomeSpec at which horizon
and decision delay, by which evaluator, for which metric (and on Plane B, under
which decision policy and execution configuration).

- **Plane A and Plane B are counted separately** and never added together.
- **Folds do not multiply the count.** Five walk-forward folds are five
  measurements of one hypothesis, not five hypotheses. Counting them would also
  punish the good practice of using more folds.
- **Retries and determinism re-runs do not multiply the count** — they share a
  fingerprint.
- **Descriptive runs, pre-evaluation failures, and policies that were executed
  but never evaluated all count zero** — while remaining permanently recorded.
- **Raw run count remains invalid** as a denominator, in either direction.

**On correlation, plainly: we count, we do not adjust.** Neighbouring
parameter values in a sweep produce highly correlated results, so a hundred
variants are not a hundred independent tries. Estimating *how many* independent
tries they really amount to needs information this platform does not record —
a per-trial series of the statistic, or an approved null distribution, or a
declared correlation model. So the platform counts related variants
individually, which over-counts rather than under-counts, and says so: every
report carries a mandatory caveat stating that the variants may be correlated
and that the effective number of independent trials is **not** estimated here.

**The practical principle:**

> When many variants are tried, the platform must expose the search burden.
> It must not present an adjusted statistical confidence measure unless an
> approved methodology and the inputs that methodology requires both exist.

Concretely, and permanently until a new ADR says otherwise: **V1 ships no
deflated statistic, adjusted metric, p-value, significance claim or
effective-independent-trial estimate** — no Deflated Sharpe Ratio, no
Bonferroni or Šidák, no Benjamini–Hochberg, no Reality Check, no SPA. The
repository has no p-values (evaluators produce metrics, not test statistics),
no ratio statistic (§7 — there is no verified account base), and no per-trial
return series in the reporting layer. Inventing any of those inputs to produce
an adjusted number would be exactly the self-deception this section exists to
prevent, and an adjusted number is far harder to withdraw once quoted than a
raw one. What ships is the **denominator itself**: how many distinct evaluated
hypothesis-question pairs were tried, reported beside every comparison, and by
default over *all* runs rather than a flattering subset.

### Scale-out protections (Phase 5)

Running a hundred variants in one command multiplies the rate at which §2's
quiet death of a test set can happen. The protections are policy, not
convenience:

- **OOS cannot quietly become another validation set.** A sweep pointed at OOS
  requires the same explicit, audited access ceremony as a single OOS run — per
  child, not once for the batch — plus an explicit cap on how many protected
  children may be planned, with no default. Every OOS access remains
  permanently recorded and counted, and the count appears in the trial report.
  Continuing a sweep after an OOS access has occurred requires an explicit
  acknowledgement, because the remaining variants would otherwise be chosen
  with knowledge of a protected result.
- **A sweep over LOCKBOX is prohibited.** Not gated — prohibited. §3 opens the
  lockbox once, for a finished strategy with frozen parameters; a sweep is a
  search over parameters that are by definition not frozen. Single lockbox runs
  remain available through the unchanged ceremony.
- **Protected-result ranking must not silently become a tuning mechanism.**
  Results from runs that touched OOS or the lockbox are excluded from
  comparison and query output by default; including them is an explicit act,
  and when included every such row is marked and the access counts are stated
  alongside. A ranking that quietly mixed protected results into the ordering
  would be selection on protected data without anyone deciding to do it.
- **Unverified cost semantics survive the comparison layer.** Every simulated
  result carries its `cost_semantics` status wherever its numbers appear, so
  §7's restrictions cannot be lost by copying a figure out of a table.
- **Nothing is deleted, hidden or archived** to tidy a comparison. Changing a
  default view never changes the record.

## 7. The simulator is second, never first

*(Normative sources: [ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md),
[ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md),
[ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md). Owner-approved as
OD-4K, 2026-08-14.)*

Phase 4 implements a simulator and, with it, the ability to produce numbers
denominated in money. Money is persuasive in a way a rank correlation is not,
so the discipline below remains binding now that the implementation exists.

**The intended workflow is, and remains:**

```
research idea -> Plane-A forecast evaluation -> evidence of edge
             -> DecisionPolicy -> simulator -> Plane-B trade evaluation
```

Plane A is where hypotheses live and die, cheaply. Plane B answers exactly one
further question: *does the edge Plane A found survive the execution and cost
assumptions we have written down?*

**The simulator is not an edge-discovery mechanism.** Take-profit distance,
stop-loss distance, entry timing and cost assumptions do not create predictive
information; they only reshape the distribution of an existing signal, and they
reshape it in ways that are extremely easy to overfit. Concretely:

- **Do not** search TP/SL settings until a backtest looks good and call the
  result a strategy. A profitable-looking bracket over a signal with no Plane-A
  edge is a curve fit with extra steps.
- **Do not** treat "it works with a tighter stop" as evidence. It is evidence
  about the stop, not about the forecast.
- **Do** establish the forecast first — a Plane-A metric with an honest
  effective sample count — and only then ask whether costs and execution leave
  anything of it.

**A bracket a policy computes is a strategy component; a bracket coefficient
searched until the curve looks good is still a curve fit.** Since 2026-08-22 a
DecisionPolicy may emit a per-trade take profit and stop loss
([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) Amendment 1). That
does not relax anything above. A bracket derived point-in-time from market
structure — a zone edge, a volatility estimate, an R-multiple — is part of the
strategy being tested, and it is covered by the same causality rig as every
other policy output. A bracket *coefficient* swept across runs is execution
tuning wearing a policy parameter's clothes, and it is counted exactly like one:
a policy-parameter sweep is a sweep of runs, each with its own
`experiment_fingerprint` and its own Plane-B inventory rows, so it inflates the
denominator in the Trial Ledger just as `/execution/take_profit_pips` does. Any
such sweep is preregistered in a frozen research document before it is
launched, exactly as the fixed-bracket families were.

The platform makes this visible rather than merely advisable. Take-profit and
stop-loss live in the **execution configuration** or in an explicit per-trade
instruction — never in the model — and V1 permits exactly one execution
configuration per run. A TP/SL
search is therefore a sweep of **runs**, each contributing its own rows to the
evaluation inventory — so execution tuning inflates the multiple-testing
denominator of §6 visibly, instead of hiding inside a single run's fan-out.
That friction is deliberate.

### Cost evidence, and what an unverified number may be used for

The broker facts that turn a price movement into money — pip size, contract
size, which side of the market the recorded prices are, whether commission is
charged per side or per round turn, currency conversion — are **not yet
verified**. They are tracked, one row at a time, in
[COST_AUDIT.md](COST_AUDIT.md), where every fact the platform's arithmetic
consumes is either VERIFIED with exact evidence or UNKNOWN with the exact
evidence still required, and effects the platform deliberately does not model
are classified separately as declared limitations. The platform never fills a
gap with a plausible default, and — since the 2026-08-14 review remediation —
it does not accept a *claim* that a gap has been filled either: `verified` cost
semantics are constructible only against a repository-controlled binding that
exists solely after the audit closes ([ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md)
Amendment 1 clauses 5–7). A config cannot self-certify evidence.

One of those unverified facts is not about money at all. **Which side of the
market the recorded prices represent decides where trades actually execute**,
and therefore where a take-profit or stop-loss actually triggers. Until it is
verified, the simulator runs a *named provisional* execution model and its
trigger timing is unverified too — so a simulated result under an open audit is
unverified in execution as well as in money
([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §8).

Until that audit closes:

- every simulated run declares `cost_semantics: unverified` and is permanently
  marked as such in its run manifest, simulation summary and every Plane-B
  inventory row, together with its binding and price-model identity;
- its money figures **may not be quoted as real-money estimates**, in any
  document, message or chart;
- it **may not be the deciding evidence** for promoting a strategy, for
  spending an OOS check, or for opening the lockbox;
- slippage, financing/swap and broker order constraints are **not modelled at
  all**, so results are optimistic by an unmeasured amount — an omission to be
  stated wherever the numbers appear, not a rounding error;
- the executable price path is **provisionally bound**, so exit-trigger timing
  for one direction may be optimistic by up to the spread — stated with the
  numbers, never quietly.

A simulated result under an open audit is a *sanity check on execution
mechanics*, not a P&L forecast. When Plane A and Plane B disagree, the
disagreement is information about execution assumptions
([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)) — and
while those assumptions are unverified, Plane B is the weaker of the two
pieces of evidence, not the stronger.

## 8. Current V1 policy summary

*(Normative source: [ADR-0012](../adr/ADR-0012-two-tier-splits.md).)*

- Splits: TRAIN 2020–2023 · VALIDATION 2024 · OOS 2025 · LOCKBOX
  2026-01-01 → last observed bar ≤ 2026-07-31 (native-wallclock-v1).
- Default discovery returns **TRAIN and VALIDATION only**.
- OOS: explicit access, no ceremony, **audited from Phase 1** (a write-ahead
  audit event before rows are returned), permanently recorded and counted.
- Anything whose range extends past the end of OOS — **the raw parent, the
  curated parent**, SNAPSHOT and LOCKBOX — is gated behind the full ceremony
  (AO-1 generalized, ADR-0014).
- Folds: Tier-2, runtime-generated, single-view-contained, recorded (AO-2),
  with automatic derived purge at every fit/evaluation boundary.
- Fit inputs may be a recorded ordered union of adjacent non-gated views;
  hand-written date ranges never are.
- These dates are research policy, not platform assumptions. Changing them
  requires an ADR and new view identities — never editing an existing view.

## 9. Related documents

- [Architecture V1](../architecture/V1.md) — the enforcing mechanisms
- [ADR-0012](../adr/ADR-0012-two-tier-splits.md) (**normative** for splits,
  folds, purge and view composition) · [ADR-0013](../adr/ADR-0013-lockbox-enforcement.md) · [ADR-0014](../adr/ADR-0014-snapshot-and-view-roles.md) · [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) (trial counting) ·
  [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md) (**normative**
  for the multiple-testing denominator and the V1 rule that no adjusted
  statistic ships) ·
  [PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §14–§15 (the
  Trial Ledger and the scale-out safeguards) · [ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) (training-target channel)
- [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) (simulation
  contracts and the methodology boundary) ·
  [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) (cost quarantine) ·
  [COST_AUDIT.md](COST_AUDIT.md) (the evidence record) ·
  [PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) (the Phase-4 plan)
- [Independent review, 2026-08-06](../architecture/reviews/2026-08-06-independent-review.md) — the findings these rules answer
