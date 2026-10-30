# ADR-0019 — Keep and incrementally refactor the existing skeleton

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
`~/trading-research-suite` contains a working skeleton: Protocol-based contracts,
per-layer registries, AST-enforced layer boundaries, 50 passing tests,
strict tooling. Its conceptual separations are assets; its bulk carriers
(per-row objects), fused pipeline, tz-aware-only time rule, missing fit
lifecycle, and dense per-row Signal/PipelineRecord usage are not viable at
M1 scale.

## Decision
**Keep and refactor in place. Do not start a replacement repository.**
Sequence: (1) initialize Git and capture a clean baseline commit of the
working skeleton and its passing tests; (2) record architecture decisions;
(3) refactor incrementally, add-then-migrate, keeping tests green at every
step. Preserve: layer boundaries, registries, architecture tests, the
generic ResearchModel → ModelOutput → DecisionPolicy separation, and test
vocabulary where still correct. Replace: the per-object bulk carrier, fused
pipeline internals, the timezone-aware-only invariant, the missing fit
lifecycle, and dense per-row Signal/PipelineRecord usage.

## Alternatives considered
- Fresh repository — loses enforced architecture tests, history, and
  working test vocabulary; invites a rewrite spiral.
- Keep everything as-is — the hot path provably cannot carry 2.33 M rows.

## Consequences
The baseline commit (`01dfdb9…`) preserves the pre-architecture state
forever. Roughly ~70 % of test intent survives migration (~40 % of test
text). Retired types are deleted in one commit once nothing references
them.

## When to reconsider
Only if migration reveals the skeleton's layering itself (not its
carriers) to be wrong — no evidence of that today.

## Related
[ADR-0003](ADR-0003-columnar-hot-path.md) ·
[ADR-0002](ADR-0002-native-time-model.md) · freeze proposal §11.
