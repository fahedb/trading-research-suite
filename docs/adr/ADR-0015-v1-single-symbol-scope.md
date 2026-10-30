# ADR-0015 — V1 scope: single symbol; PortfolioPolicy as inert seam

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
One instrument's data exists today. Multi-symbol/portfolio infrastructure
would multiply the surface of every layer (data, features, decisions,
simulation, evaluation) before any research needs it.

## Decision
V1 experiments assume **one symbol, one instrument, one base market frame
per experiment**. Core contracts avoid making future multi-symbol work
impossible (identities and frames do not hardcode single-symbol
assumptions), but V1 implements no portfolio or cross-asset
infrastructure. **PortfolioPolicy exists only as a pass-through seam**
between DecisionPolicy and the simulator — a named place for future work,
deliberately inert.

## Amendment 1 — no `PortfolioPolicy` symbol is created in V1 (2026-08-14, Phase-4 planning)

The single-symbol decision above is unchanged. What changes is the seam.

The original text says `PortfolioPolicy` "exists only as a pass-through seam
between DecisionPolicy and the simulator". Phase 4 is the moment that sentence
would take effect, and at that moment it turned out to be describing nothing:
with one symbol, one open trade and a fixed lot ([ADR-0023](ADR-0023-phase-4-simulation-contracts.md)
§2), the simulator consumes an `IntentFrame` directly and there is no
allocation, netting or aggregation for a pass-through object to pass through.
No such symbol exists in the repository today, so this amendment records a
decision rather than removing code.

**V1 therefore ships no `PortfolioPolicy` type, protocol, config field or
registry entry.** The 2026-08-06 independent review already recorded the seam
as borderline ("deleting it would also be fine"), and the owner-approved
Phase-4 direction of 2026-08-14 (OD-4J) resolves it in that direction: an
abstraction must justify itself through research correctness or broad
usefulness, and "we may need it later" does not.

The forward path is unchanged and is ADR-0006's: when a second instrument's
dataset exists and a concrete cross-asset hypothesis is on the table, portfolio
construction arrives as a genuinely new stage by ADR — which is a better
extraction point than an inert object that was never exercised.

## Alternatives considered
- Multi-asset contracts now — speculative generality, tax on every phase.
- No seam at all — extraction cost later; a named no-op is nearly free.
  **Chosen in Amendment 1**: the no-op turned out to have nothing to pass
  through, and an unexercised seam is a guess about the shape of the future,
  not insurance against it.

## Consequences
Phase 4 stays small. Cross-asset research is explicitly out of V1 scope;
proposing it triggers an ADR and a scope review.

## When to reconsider
When a second instrument's dataset exists and a concrete cross-asset
hypothesis is on the table.

## Related
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0023](ADR-0023-phase-4-simulation-contracts.md) · V1.md §10.
