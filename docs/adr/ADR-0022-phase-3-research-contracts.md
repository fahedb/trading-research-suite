# ADR-0022 — Phase-3 research contracts: fit/infer, row-local inference, evaluator requirements, declared tolerance

**Status:** ACCEPTED · **Date:** 2026-08-13

**Provenance.** This ADR records the owner-approved Phase-3 planning decisions
**OD-3A…OD-3F (2026-08-13)**
([docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md)). It
exists because Phase 3 replaces public plugin contracts — `ResearchModel`,
`RepresentationModel`, `DecisionPolicy` and `Evaluator` — and
[CLAUDE.md](../../CLAUDE.md) requires a new or amended ADR before a contract
change is implemented. This is the same instrument, for the same reason, that
[ADR-0021](ADR-0021-columnar-feature-contract.md) provided for the feature
contract.

Amendment 3's mid-implementation authority correction was separately
**OWNER APPROVED as OD-3G on 2026-08-14**. The approval confirms the amendment
as written and changes none of its substance.

**It amends no earlier ADR.** Most of what follows *operationalizes* decisions
already accepted in ADR-0006…ADR-0012 and ADR-0016…ADR-0018; §§5–10 are the
genuinely new normative content. Narrow scope choices — how many OutcomeSpecs,
evaluators, models or CLI verbs V1 ships — deliberately live in the phase plan
and roadmap, not here.

## Context

The skeleton's research contracts are per-point and pre-architecture:

```
RepresentationModel.encode(FeatureSet) -> Representation
ResearchModel.infer(ResearchInput)     -> ModelOutput
DecisionPolicy.decide(ModelOutput)     -> Signal | None
Evaluator.evaluate(Sequence[PipelineRecord]) -> Mapping[str, float]
```

They carry no fit lifecycle, no fitted-state identity, no target channel, no
sparsity, and a `Signal` type whose `Direction` enum
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) already retired in
principle. [ADR-0019](ADR-0019-keep-and-refactor-skeleton.md) authorizes
replacing them; ADR-0008, ADR-0009 and ADR-0010 fix what must replace them. What
no accepted ADR records is the **concrete replacement contract** — and four
further questions that only become answerable once models actually exist.

## Decision

### 1. The per-point research contracts are replaced, not adapted

`ResearchModel`, `RepresentationModel`, `DecisionPolicy` and `Evaluator` are
**removed and replaced**. There is no shim, adapter or compatibility layer, and
no `Direction`/`Signal` path survives into the new chain. The retired skeleton
carriers (`Bar`, `MarketContext`, `FeatureSet`, `ResearchInput`, `ModelOutput`,
`Signal`, `Direction`, `Representation`, `PipelineRecord`) are deleted in the
slice that removes their last reference, per ADR-0019.

### 2. Fit lifecycle, fitted state, and the target channel — as already accepted

Operationalizing [ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) and
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) without change:

```
fit(train_view, inputs, training_targets=None) -> FittedState
infer(eval_view, fitted_state, inputs)         -> ModelOutputFrame
```

- `training_targets` is **optional and absent** for stateless and unsupervised
  models; a no-op `fit` is a first-class implementation, not a workaround.
- `RepresentationModel` participates in the **same** lifecycle. Fitted
  preprocessing (a scaler, a PCA) is fitted state and may not be hidden inside
  another stage.
- Targets reach `fit` **only** as a `TrainingTargetFrame` computed by the
  orchestrator from a config-declared OutcomeSpec. A model performing its own
  forward read inside `fit` is a leakage bug.
- **`infer` never receives targets.** This is enforced by the signature, not by
  convention.
- `FittedState` identity pins model id and params, training view identities and
  ranges, feature/representation identities, the training OutcomeSpec id,
  params, target range and alignment, the applied purge, code identity and seed.
  Two states trained on the same bars under different target definitions must
  never share an identity.
- Serialization uses versioned array formats plus JSON metadata; **never
  pickle** ([ADR-0017](ADR-0017-storage-files-as-truth.md)).
- Refit-per-run remains the V1 default and `FittedState` remains
  `cache_if_expensive` — already settled by ADR-0008 and
  [ADR-0007](ADR-0007-materialization-policy.md), restated here only because the
  roadmap had carried it as an open item.

### 3. Output and intent frames — as already accepted

Operationalizing [ADR-0009](ADR-0009-generic-output-and-intentframe-family.md)
without change: `ModelOutputFrame` carries generic named numeric columns,
optional labels and optional confidence, with **no required trading
vocabulary**. `DecisionPolicy` converts one `ModelOutputFrame` into an
`IntentFrame`; multiple policies must consume one persisted output with zero
re-inference.

Every `IntentFrame` carries an `intent_schema_id`. V1 implements
`TargetExposureIntentV1` — one `float64` column `target_exposure ∈ [−1,+1]` —
and preserves the trichotomy exactly:

| Value | Meaning |
|---|---|
| absent row | this component produced **no statement** at this key |
| `NaN` | explicit **abstain / no new instruction**; the holder keeps prior state |
| `0.0` | explicit **target flat** |

`ModelOutputFrame` and `IntentFrame` may be indexed by a **strict subset** of the
evaluation keys. Sparsity relaxes coverage, never ordering or bounds, and never
the trichotomy above.

### 4. Evaluators consume artifacts only — as already accepted

Operationalizing ADR-0010: a Plane-A evaluator consumes a `ModelOutputFrame` and
OutcomeSpec targets and **never recomputes upstream research logic**. This is
guaranteed by prohibited dependencies, not by promise. Plane-B (trade)
evaluation remains gated behind the cost audit
([ADR-0020](ADR-0020-cost-semantics-quarantine.md)) and is absent from Phase 3.

---

The remaining sections are **new normative decisions**.

### 5. Row-local inference is a V1 invariant

> **A `ResearchModel`'s or `RepresentationModel`'s output at evaluation row *i*
> depends only on the declared input values for row *i* and on the fixed
> `FittedState`. It must not depend on any other row of the evaluation range.**

All legitimate history reaches a model through **declared features**, whose
backward reach is already finite, declared and adversarially enforced by
[ADR-0021](ADR-0021-columnar-feature-contract.md)'s `max_prior_rows`. A
regime-adaptive model is fully expressible under this invariant: its market-state
inputs are ordinary declared features, and its regime-dependent behaviour is
learned into `FittedState`.

This is the contract that makes
[ADR-0012](ADR-0012-two-tier-splits.md) Amendment 1's information span
computable. That amendment says `context_start` comes from *"declared feature
lookback / WindowSpec **and any carried model or representation state**"*. Under
this invariant there is no carried state, so the second term contributes nothing
and the span is fully determined by declared feature reach and the declared
OutcomeSpec.

**The invariant is enforced by adversarial rigs, not by a declaration field.**
Two exact perturbation rigs, modelled on the Phase-2 leakage rigs, run over
every registered model and representation:

| Rig | Method | Catches |
|---|---|---|
| **forward** | perturb declared input rows strictly **after** evaluation index *i*; output at *i* must be bitwise unchanged | forward reads; whole-evaluation-range normalization; peeking at the range end |
| **row-locality** | perturb declared input rows strictly **before** *i*; output at *i* must be bitwise unchanged | carried state in either direction; undeclared history; train/eval inconsistency from state that accumulates across a range |

Comparison is exact. A tolerance, skip or expected-failure marker in these rigs
is prohibited.

### 6. Stateful and online inference are out of V1 scope

Explicitly **not supported in V1**:

- mutable state carried from row to row during inference;
- online or incremental learners;
- any inference whose result at row *i* depends on other evaluation rows.

**No carried-state or model-level information-reach API is built.** Adding one
now would pre-authorize, through an API, precisely the capability ADR-0008's
*"when to reconsider"* clause gates behind an ADR — and it would protect nothing
that exists.

**Reconsideration trigger.** A concrete model family that genuinely cannot be
expressed as row-local inference over declared inputs plus a fixed
`FittedState`. At that point the lifecycle is extended by a new ADR, which must
also define the carried-state information-reach contract honestly and wire it
into ADR-0012's `context_start`. The row-locality rig of §5 is what detects the
trigger: the first model that legitimately fails it is the first model that needs
that ADR.

### 7. Evaluators declare their requirements

An `Evaluator` declares, before execution:

- the `ModelOutputFrame` columns it consumes;
- the OutcomeSpec target columns it consumes;
- the metric names it emits.

Compatibility is checked **before expensive execution**, extending the pattern
`core/pipeline.py::check_compatibility` already applies to the model/policy
pairing so that a mismatch fails in milliseconds rather than after an hour.

This exists because ADR-0009 deliberately leaves `ModelOutputFrame` generic:
outputs may be probabilities, expected returns, similarity statistics,
distances, regime probabilities or labels. No metric may be an assumption of the
universal `Evaluator` contract — a metric that needs an ordinally meaningful
scalar must say so. Semantic suitability beyond column names and dtypes is a
documented precondition of each evaluator and the researcher's responsibility;
it is deliberately not faked as a machine-checkable type.

Every metric reports its **effective sample count** alongside its value, so a
metric computed on forty rows can never be mistaken for one computed on four
hundred thousand.

### 8. Determinism tolerance is identity-bearing contract state, never a verifier knob

[ADR-0016](ADR-0016-determinism-tiers.md) requires *"tolerances documented per
artifact type"* but does not say where a tolerance lives or who may change it.
This ADR settles that:

1. **Exact comparison is the default**, for structural *and* floating-point
   artifacts alike. Exactness requires no declaration.
2. **Structural and discrete information is always exact** and can never be
   toleranced: identities, fingerprints, schemas, column order, key sets,
   dtypes, sparse presence/absence, the abstain/flat/absent trichotomy, run
   states and evaluation-inventory rows.
3. A specific artifact type or plugin that **genuinely cannot** reproduce
   bit-for-bit may declare a tolerance with a stated reason.
4. A declared tolerance is **identity-bearing**: it is recorded in artifact
   metadata and flows into the identity key and the RunManifest. Loosening one
   therefore changes the identity and is visible in review.
5. **A tolerance is never a CLI or verifier flag.** A verification-time knob
   would let a failing comparison be loosened after the fact, which is exactly
   how tolerances come to hide regressions.
6. When a tolerance is used, the comparison form is combined —
   `|a − b| <= atol + rtol · |b|` — because pure relative tolerance fails near
   zero and pure absolute tolerance fails across scales.
7. The determinism verifier reports the **observed deviation**, not merely
   pass/fail, so drift becomes visible before it crosses a threshold.
8. Tightening a tolerance is free; **loosening one is an architecture-boundary
   change** and receives the same review discipline as changing an accepted ADR.

**No claim is made that Phase 2's evidence generalizes.** Phase 2 demonstrated
that deterministic vectorized array arithmetic over pinned inputs reproduces
exactly *on this machine and environment*. It demonstrates nothing about
iterative solvers, threaded BLAS reduction order, GPU kernels or randomized
algorithms. **No cross-hardware bitwise reproducibility is claimed anywhere**,
consistent with ADR-0016 and V1.md §10.

ADR-0016's requirement that *"the verification report is a stored artifact"* —
which [PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md) §4.4 explicitly
deferred because Phase 2 had no run directories — is discharged in Phase 3.

### 9. OutcomeSpec boundary and discontinuity semantics

ADR-0010 requires an OutcomeSpec to define a *"gap/boundary policy
(`NaN | truncate | skip`)"*. That phrase occurs exactly once in the accepted ADR
corpus and fixes the **vocabulary** and the **obligation to declare a policy**;
it specifies no trigger conditions and does not require all three values to
exist. This ADR records the V1 trigger semantics, which the earlier ADR
delegates:

| Trigger | V1 behaviour |
|---|---|
| **End-of-data boundary** — the required terminal position does not exist on the resolved axis | target is **`NaN` / unavailable** |
| **Observed-key discontinuity** inside the forward span | **does not fire.** The horizon is counted in observed rows, so a discontinuity never makes it incomplete; the horizon continues over the observed rows |

**Truncation is refused.** A horizon that runs off the end of the data is not
shortened to the last available row. The platform cannot define the meaning of a
partial-horizon value: a seven-row return is a different random variable from a
declared twenty-row return, not a shorter version of it, and silently mixing them
in one column is a research error. This is the same reasoning ADR-0021 §3 gave
for refusing `TRUNCATE` in feature windows. `truncate` and `skip` remain
reserved ADR-0010 vocabulary, unimplemented in V1, refused with an explicit
message.

**No session semantics are invented.** An observed-key discontinuity remains an
arithmetic fact and is never interpreted as a weekend, session boundary,
holiday, outage or market closure, exactly as
[ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) requires. Refusing to compute
a return across a discontinuity would itself be such an inference.

**Naming.** The outcome-side field is `boundary_policy` and is a distinct type
from ADR-0021's feature-window `GapPolicy` (`CROSS | BREAK`). They govern
different axes — a forward horizon running off the end of data versus a backward
window spanning a discontinuity — and must never share an enum.

### 10. `X → X+1` is an approved alignment policy, not a leakage claim

V1 aligns outcomes so that a decision at observed row `X` uses row `X+1` as the
measurement origin, expressed through ADR-0010's decision-delay contract as
`decision_delay_rows = 1`, explicit with no default.

**The reason is that this is the owner-approved V1 alignment policy — a
deliberate research-definition choice. It is recorded here so it is not
re-litigated or re-justified with a different rationale.**

Two justifications are explicitly **rejected** as inaccurate or out of scope:

- **It is not a leakage rule.** Defining a forecast from a price known at `X` to
  a later price is a legitimate, leakage-free construction, provided row `X` was
  genuinely known at decision time. Nothing in this ADR implies otherwise, and no
  document may state that an `X`-origin outcome inherently reads the future.
- **It is not an execution model.** No fill, spread, slippage, commission or
  order semantics is imported from Phase 4 to justify it. Those remain
  quarantined by ADR-0020 and have no place in Plane-A forecast evaluation.

If a future research family wants a different alignment, it changes
`decision_delay_rows` — an identity-bearing parameter — under the normal reviewed
process. Nothing in this decision makes any particular value more "correct" than
another on leakage grounds.

## Amendment 1 — concrete Slice-3A vocabulary (2026-08-13, pre-implementation reconciliation finding F-1)

The Decision above named the contracts but left several public signature
positions abstract — `train_view`, `eval_view`, `inputs`, an outcome carrier, a
metric value and the tolerance surface — which is not enough to implement Slice
3A without inventing types during coding. This amendment fixes them. It adds no
capability; two of its clauses **narrow** the contract surface.

1. **One bounded-span descriptor serves both lifecycle positions.**
   `train_view` and `eval_view` are the same core-owned frozen type, `ViewSpan`,
   carrying only `source_identities`, `unit`, `clock_domain`, `context_start`,
   `eval_start` and `eval_end`. It is a **descriptor, not a data handle**: it
   exposes no rows, no columns, no loader and no grant. Two structurally
   identical types for the two positions would be ceremony. `ViewSpan` is
   genuinely required rather than derivable from the input frames, because
   `context_start` is deliberately absent from every output frame (context rows
   never appear as output) and ADR-0008 requires FittedState identity to pin the
   training **view range**.

   `BoundedView` remains a `data` type and **must not** appear in any `core`
   signature; the `BoundedView` → `ViewSpan` adaptation happens in
   `experiments`, exactly as `compute_features` adapts it today.

2. **Each research stage consumes frames only, never market columns — and the
   two stages consume different things.**

   - A **`ResearchModel`** receives a core-owned frozen `ModelInputs` bundle
     carrying a `FeatureFrame` and an **optional** `RepresentationFrame` —
     nothing else.
   - A **`RepresentationModel`** receives a **`FeatureFrame` directly**. It does
     **not** receive `ModelInputs`, and therefore has **no parameter through
     which another `RepresentationFrame` could reach it**.
   - Neither stage receives a `MarketFrame`, a market column, a
     `FeatureRequest` or any data-layer handle. All market data reaches both
     through declared features, whose backward reach is already finite, declared
     and adversarially enforced by
     [ADR-0021](ADR-0021-columnar-feature-contract.md).
   - A `RepresentationModel` additionally never receives targets: it has no
     target channel.

   The asymmetry is deliberate and load-bearing.
   [ADR-0006](ADR-0006-fixed-stage-pipeline.md)'s stage list contains **one
   optional representation stage**. Giving `RepresentationModel` a `ModelInputs`
   argument would have quietly permitted representation chaining,
   representation-on-representation pipelines, recursive representation
   dependencies and a mini-DAG inside the fixed-stage pipeline — the engine
   ADR-0006 exists to refuse — with no concrete research requirement asking for
   it. The direct `FeatureFrame` signature makes that non-capability
   **structural rather than merely discouraged**, and it needs no
   `RepresentationInputs` abstraction to achieve it.

   This narrows §2's `inputs` rather than widening it, and it is the natural
   completion of §5: if history reaches a stage only through declared features,
   so must the current row.

3. **The training and evaluation outcome carriers are distinct types.**
   `TrainingTargetFrame` is the only type `fit` accepts; `OutcomeFrame` is the
   only type an `Evaluator` accepts. They are structurally identical and both are
   minted only by the platform's outcome execution path. The distinction is not
   decoration: it makes ADR-0010's channel rule **structural**, so evaluation-range
   targets cannot reach `fit` and training-range targets cannot reach an
   evaluator, without relying on a runtime role check. This follows the
   repository's established preference for structural impossibility over
   validation (`MarketFrame.__init__` raises; `FeatureRequest` is issued only by
   `from_segments`).

4. **`MetricValue` is exactly three fields** — `name`, `value` (may be `NaN` when
   the metric is undefined) and `effective_samples`. Reporting the effective
   sample count alongside every metric is required by §7 and is what stops a
   statistic computed on forty rows from reading like one computed on four
   hundred thousand.

5. **The §8 tolerance surface is a frozen `ToleranceSpec`** — `atol`, `rtol` and
   a non-empty `reason` — declared by a plugin as
   `comparison_tolerance: ToleranceSpec | None`, where `None` means exact and is
   the default that requires no declaration. Being a declared plugin attribute
   makes it identity-bearing and recorded, as §8 requires, and keeps it
   structurally unreachable from any CLI or verifier argument.

The complete Slice-3A vocabulary, including which types are public `core`
contracts and which are implementation-only, is recorded in
[PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) §2.

## Amendment 2 — FittedState identity pins dependency identity (2026-08-13, pre-implementation reconciliation finding F-2)

§2 restated ADR-0008's `FittedState` identity field list but **omitted
dependency identity**, which
[ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) explicitly requires and
which [ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) also requires in
the experiment fingerprint. The omission is corrected: **`FittedState` identity
pins a dependency identity**, supplied by the caller as a content digest of the
resolved dependency lockfile, in the same caller-supplied manner as
`PlatformIdentity` — no platform library reads the lockfile or shells out.

**The existing `StageIdentity` machinery does not satisfy this obligation and
must not be claimed to.** Its hashed document contains exactly
`source_identities`, `bounds`, `split_policy`, `providers` and `platform`;
Phase 2 excluded the `uv.lock` digest from it with explicit owner approval
([PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md) §4.1), which was correct
because [ADR-0007](ADR-0007-materialization-policy.md) does not require
dependency identity for a feature stage. ADR-0008 does require it for fitted
state. `FittedState` identity therefore **reuses the canonical-encoding and
hashing machinery** of `StageIdentity` but is a **distinct identity with
additional components**, not the same key. The Phase-2 feature-stage exclusion
stands unchanged.

## Amendment 3 — plugins return payload; the platform binds state identity (2026-08-14, Slice-3C implementation correction, OD-3G OWNER APPROVED)

Slice 3C exposed an authority mismatch in §2's original shorthand. A plugin's
`fit` call receives only its bounded input frames and optional training targets,
but a complete `FittedState` identity must also pin the split policy, applied
purge and embargo, platform identity and dependency-lock digest. Those values
are deliberately unavailable to plugins. Requiring a plugin to return a fully
identified `FittedState` would therefore either make the identity incomplete or
force platform authority through the plugin boundary.

The owner-approved correction is narrow:

```
ResearchModel.fit(...)       -> FittedPayload
RepresentationModel.fit(...) -> FittedPayload

orchestrator.bind(complete identity, FittedPayload) -> FittedState
```

`FittedPayload` is a small frozen `core` value that a plugin may construct. It
contains only detached, non-object numeric NumPy arrays and immutable
JSON-safe metadata; it has **no identity field, no view, no split policy, no
purge record, no platform identity and no dependency identity**. Array access
returns fresh read-only descriptors. The artifact format, rather than the
plugin, versions the serialized arrays and metadata.

Only the `experiments` composition root may combine that payload with the
complete eleven-component identity from the caller and mint the private-
construction `FittedState`. `FittedState.payload` returns the validated
`FittedPayload`. No public factory, fit context, stage context or god-object is
added, and no plugin receives storage, platform, dependency, split or purge
state.

This changes no fit/infer semantics and grants no new research capability. It
makes the already-accepted authority boundary honest: plugins own learned
numeric content; the platform owns artifact identity, binding, serialization
and materialization.

## Alternatives considered

- **Adapt the per-point contracts with a shim** — preserves types that carry no
  fit lifecycle, no identity and no sparsity, and a shim would have to be deleted
  later anyway.
- **A declared model/representation `max_prior_rows` field** (proposed and
  withdrawn during Phase-3 planning) — would build the API for carried state,
  pre-authorizing the capability ADR-0008 gates behind reconsideration, to
  protect nothing that exists. The row-locality rig of §5 enforces the same
  property without a speculative field, and additionally *detects* the
  reconsideration trigger.
- **Baking metrics into the `Evaluator` contract** — couples every future model
  to a scalar, sign-meaningful output, which is what ADR-0009 exists to prevent.
- **Tolerances as verifier configuration** — the single easiest way to turn a red
  run green after seeing it fail.
- **An intent-schema registry now** — V1 has one schema and no simulator that
  could declare support for a second, so a resolution mechanism would have
  nothing to resolve against. The field and the family remain; the registry
  arrives when it has a job.
- **Amending ADR-0010 to add the trigger semantics** — unnecessary: the accepted
  text fixes the vocabulary and the obligation to declare a policy, and delegates
  the rest, so §9 records rather than changes it.

## Consequences

Fit-time and inference-time separation becomes structural, and no fitted state
can hide what data it saw. Row-local inference makes ADR-0012's information span
computable from declarations alone and makes model-level leakage mechanically
testable over every registered model, exactly as ADR-0021 did for features.
Evaluators become composable without constraining model output. Determinism
stays honest by default and cannot be quietly relaxed.

The cost is real and accepted: a model that needs carried state cannot be written
in V1 and must go through an ADR first. That is the intended friction.

## When to reconsider

When a concrete model family cannot be expressed as row-local inference over
declared inputs plus a fixed `FittedState` (§6) · when a second intent schema or
the first simulator gives a schema registry an actual job · when an artifact type
genuinely requires a tolerance, which is a declaration under §8 and not an ADR
change · when Plane-B evaluation enters after the cost audit. If
`ModelOutputFrame` or `IntentFrame` begin accumulating convenience methods, stop
and re-review, for the same reason ADR-0003 states for `MarketFrame`.

## Related

[ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) ·
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0017](ADR-0017-storage-files-as-truth.md) ·
[ADR-0019](ADR-0019-keep-and-refactor-skeleton.md) ·
[ADR-0021](ADR-0021-columnar-feature-contract.md) ·
implementation detail: [docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md)
