# ADR-0006 — Fixed-stage pipeline; windowing is a plugin spec, not a stage

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The platform must make very different research approaches cheap to run
through one code path, support multi-policy fan-out and resumption, and
avoid both a fused single call (the skeleton's `run_pipeline`) and a
general DAG engine.

## Decision
A **fixed-stage modular pipeline**: dataset/view resolution → features →
optional representation → model fit → model inference → ModelOutputFrame →
one or more DecisionPolicies → IntentFrames → forecast evaluation →
optional simulation → trade evaluation → run result. The stage list is
closed in V1; adding a stage requires an ADR. **Windowing is not a stage**:
a window is a data-access requirement expressed through a plugin-declared
WindowSpec; models that do not use windows neither pay for nor depend on
that abstraction.

## Alternatives considered
- General DAG engine — flexibility we do not need and an engine to
  maintain; the failure mode is building Airflow.
- Fused single function — blocks multi-policy reuse, caching, resumption
  (this is what the skeleton has and why it is replaced).
- Microservices — operational tax with zero benefit on one machine.

## Consequences
Stage boundaries become artifact boundaries (ADR-0007), enabling caching,
fan-out, and recovery. New research approaches slot into existing stages;
pressure to add stages is an architecture signal, not a patch.

## When to reconsider
If a genuinely new stage class appears (e.g., portfolio construction when
multi-symbol arrives), extend by ADR.

## Related
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) · V1.md §5.
