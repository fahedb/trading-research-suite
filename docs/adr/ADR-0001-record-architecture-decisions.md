# ADR-0001 — Record architecture decisions as ADRs

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The platform's design was produced across discovery and freeze sessions.
Chat memory is not durable, reviewable, or diffable; the repository must be
the source of truth for why things are the way they are.

## Decision
Every architecturally significant decision is recorded as a numbered ADR in
`docs/adr/`, one decision per file, with context, alternatives,
consequences, and reconsideration triggers. Architectural changes (new
stage, new layer dependency, contract change, split-policy change) require
a new or amended ADR before implementation.

## Alternatives considered
- A wiki or external document store — not versioned with the code it
  governs, drifts silently.
- No decision records — rationale evaporates; future sessions re-litigate
  settled questions.

## Consequences
Decisions are reviewable in Git history; an independent reviewer can attack
each one in isolation; superseding a decision leaves a visible trail.

## When to reconsider
If ADR volume grows past usefulness (dozens per phase), tighten the
significance bar rather than abandoning the practice.

## Related
[docs/architecture/V1.md](../architecture/V1.md) · all subsequent ADRs.
