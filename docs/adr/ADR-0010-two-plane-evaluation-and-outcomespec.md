# ADR-0010 — Two-plane evaluation and versioned OutcomeSpec

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
If evaluation exists only after simulation, every hypothesis pays simulator
complexity and unaudited cost assumptions before it can be judged. Most
hypotheses can be killed far more cheaply.

## Decision
Two independent evaluation planes. **Plane A — forecast evaluation:**
consumes ModelOutputFrame + a versioned **OutcomeSpec**; no simulator
required; a config may legitimately end here. **Plane B — trade
evaluation:** consumes TradeLog/EquityCurve produced by the simulator.
OutcomeSpec is a versioned plugin defining horizon (observed bars or
another explicit unit), reference price, decision delay, gap/boundary
policy (`NaN | truncate | skip`), and alignment rules. It is the **only
component permitted to read forward of decision time**; its outputs live in
a reserved namespace whose consumption is restricted as set out in the
amendment below (declared-input check + test). Evaluators consume artifacts
and never recompute upstream research logic — guaranteed structurally by
prohibited dependencies, not by promise.

## Amendment — fit-time training-target channel (2026-08-06, finding B-1)

The original text said the reserved outcome namespace could not be consumed
by "features, models, and policies". Read literally that makes supervised
and statistical fitting — including the first planned similarity plugin,
which summarizes future outcome statistics — impossible without an ad-hoc
forward read inside `fit`, i.e. exactly the distributed leakage this ADR
exists to prevent. The ban is therefore **scoped, not relaxed**:

**Prohibited, unchanged.** The reserved outcome namespace may never reach:
- FeatureProvider inputs,
- inference inputs (`infer` sees no targets, ever),
- DecisionPolicy inputs.

**Permitted, through one platform-controlled channel only.** Supervised or
statistical fitting may consume targets via an explicit
**TrainingTargetFrame**:

```
training view → declared OutcomeSpec → orchestrator computes
TrainingTargetFrame → ResearchModel.fit(inputs, training_targets)
→ FittedState
```

Requirements:
1. The experiment config **explicitly names the training OutcomeSpec**;
   there is no implicit or default target.
2. The **orchestrator** computes the targets. A ResearchModel performing its
   own forward read remains a leakage bug, sentinel-tested (Phase 2/3).
3. Targets are bounded by the **admitted training information range**: no
   target may depend on a bar outside the fit input's authorized range, and
   samples whose target span would cross a fold boundary are purged
   ([ADR-0012](ADR-0012-two-tier-splits.md)).
4. OutcomeSpec identity, parameters, resulting target range and alignment
   rules are **pinned into FittedState identity**
   ([ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md)).
5. Stateless and unsupervised models **omit the channel entirely** — it is
   optional, not a required argument shape.
6. The channel is deliberately generic: `TrainingTargetFrame` carries named
   target columns and their alignment, with no classification/regression,
   horizon-count, or column-name assumptions baked in.

The naming stays neutral for the same reason ModelOutputFrame does: a target
is whatever the declared OutcomeSpec computed, not a "label" of a presumed
learning task.

## Alternatives considered
- Single terminal evaluation after simulation — every idea pays maximal
  cost; cheap kills impossible.
- Ad-hoc forward returns computed inside models/features — the classic
  lookahead leak; quarantining forward-reading into one audited component
  is the countermeasure.

## Consequences
The experiment lifecycle has a legitimate early exit. Outcome definitions
become versioned, comparable research assets. Forecast metrics and trade
metrics can disagree — that disagreement is information about execution
assumptions, not an error.

A descriptive run may legitimately end **before** any evaluation (a regime
study that only stores model output, for example). Such a run is a valid,
recorded execution but is **not** a statistical evaluation
([ADR-0011](ADR-0011-run-identity-and-canonical-runs.md)).

## When to reconsider
If a third plane genuinely emerges (e.g., portfolio-level evaluation under
multi-symbol scope), extend by ADR. If a model family needs targets that
cannot be expressed as an OutcomeSpec over the training range, that is a new
ADR — never an ad-hoc forward read.

## Related
[ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) · V1.md §6.
