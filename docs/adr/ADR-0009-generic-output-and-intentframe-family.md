# ADR-0009 — Generic model output and the IntentFrame family

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
Different research approaches produce incommensurable outputs
(probabilities, expected returns, similarity statistics, distances, regime
probabilities, labels). Hardcoding BUY/SELL into model output couples every
model to one decision format and forecloses testing several decision rules
against one inference pass.

## Decision
ResearchModel outputs remain **generic**: named numeric columns, optional
label columns, optional confidence — **no required trading vocabulary**
(enforced by the banned-keys test). DecisionPolicy converts a
ModelOutputFrame into an **IntentFrame**; multiple policies must be able to
consume the same persisted/cached ModelOutputFrame without rerunning
inference. IntentFrames carry an `intent_schema_id` from an extensible
family. V1's first schema is **TargetExposureIntentV1**: one float column
`target_exposure ∈ [−1,+1]`; `NaN` means abstain / no new instruction (the
downstream holder keeps prior state); `0.0` means explicitly target flat.
Target exposure is NOT the permanent universal decision definition: future
schemas (orders, limits, stops, cancellations, staged entries/exits)
register new ids without any ResearchModel change. Simulators declare which
intent schemas they support; mismatches fail at resolve time.

## Amendment — sparse output and intent frames are legal (2026-08-06, finding N-8)

Event-driven and sparse research decides on a small subset of bars. Nothing
in this ADR required dense frames, but nothing permitted sparse ones either,
and the `NaN`-means-abstain convention implied density. Stated explicitly:

- a **ModelOutputFrame** may be indexed by a **strict subset** of the
  evaluation view's native keys;
- an **IntentFrame** may likewise be indexed by a strict subset;
- sparse and event-driven research is **first-class**, not a workaround. A
  model that acts on 400 events must not be forced to emit 2.3 M abstention
  rows;
- **absence of a row is distinct from a dense row containing `NaN`.**
  Absence means "this component produced no statement at this key"; a `NaN`
  in `TargetExposureIntentV1` means "explicit abstain / no new instruction —
  the holder keeps prior state". Consumers (evaluators, simulators) must
  preserve that distinction, and alignment of sparse frames against a dense
  view is the consumer's declared, tested behaviour;
- keys must still be strictly increasing and lie within the evaluation
  range; sparsity relaxes coverage, never ordering or bounds.

**TargetExposureIntentV1 remains only the first concrete intent schema**,
not the universal decision definition; order, limit, stop, cancellation and
staged-entry schemas register new ids without touching any ResearchModel.

## Alternatives considered
- Direction enum + conviction (the skeleton's Signal) — less expressive;
  the FLAT-with-conviction contradiction class; retired.
- Order-level output now — drags unaudited execution semantics into
  research contracts prematurely.
- Universal target-exposure forever — blocks order-based research later.

## Consequences
Policy sweeps are cheap (fan-out over one persisted output). The
abstain-vs-flat distinction is load-bearing and must be preserved by every
consumer. Per-model policies must be tuned per output scale — output scales
are deliberately unconstrained.

## When to reconsider
When the first order-type schema is designed (post cost audit), review
whether the family abstraction held.

## Related
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) · V1.md §6.
