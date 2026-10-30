# ADR-0008 — Explicit fit lifecycle and FittedState identity

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The first planned research plugin (similarity vs a historical reference
library) — like any ML, statistical, or regime model — has trained state.
Where fitted state comes from and what data it saw is the platform's
largest leakage surface. The skeleton had inference only.

## Decision
ResearchModel has an explicit lifecycle: `fit(train_view, inputs) →
FittedState` and `infer(eval_view, fitted_state, inputs) →
ModelOutputFrame`. Stateless models implement trivial/no-op fitting.
**RepresentationModel participates in the same lifecycle** (a scaler or PCA
is fitted state; hiding it would recreate the problem). **FittedState
identity pins:** model identity + parameters, training DatasetRef/view
range, feature/representation identities, code identity, dependency
identity, and seed where relevant. No fitted model may hide which data it
saw — "which runs used state trained on X" is a registry query.
Serialization uses versioned array formats + JSON metadata; never pickle.

## Amendment — training targets enter through the platform channel (2026-08-06, finding B-1)

Fitting may consume forward-looking targets **only** as a
`TrainingTargetFrame` computed by the orchestrator from a config-declared
OutcomeSpec — see the amendment in
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md), which is
normative for the channel. Consequences for this ADR:

- The lifecycle is `fit(train_view, inputs, training_targets=None) →
  FittedState`; the target argument is **optional** and absent for stateless
  and unsupervised models.
- A ResearchModel performing its own forward read inside `fit` is a leakage
  bug, not a shortcut.
- **FittedState identity additionally pins** the training OutcomeSpec id +
  version + parameters, the resulting target range and alignment rules, and
  any purge/embargo applied at fold boundaries
  ([ADR-0012](ADR-0012-two-tier-splits.md)). Two states trained on the same
  bars under different target definitions must never share an identity.
- Inference is unchanged and never receives targets.

## Alternatives considered
- Fit hidden inside infer — leakage becomes archaeology.
- Fitted state as opaque pickle — unversionable, unsafe, rot-prone.
- Defer the lifecycle until an ML model arrives — the first planned plugin
  already needs it; retrofitting a contract change mid-phase is the
  expensive path.

## Consequences
Fit-time/inference-time separation is structural. Refit-per-run is the V1
default (state is `cache_if_expensive`); persisted fitted artifacts are a
cache, not a redesign, when reuse pays.

## When to reconsider
If a model family appears whose "training" cannot be expressed as
fit-on-a-view (e.g., online learners), extend the lifecycle by ADR.

## Related
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) · V1.md §6.
