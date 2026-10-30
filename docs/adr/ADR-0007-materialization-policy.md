# ADR-0007 — MaterializationPolicy; identity key vs content hash

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
Stage boundaries must be hashable and materializable for caching, fan-out,
resumption, and determinism verification — but writing a full Parquet
artifact after every trivial operation would tax every experiment with I/O.

## Decision
Every stage output carries a **MaterializationPolicy**: `ephemeral`
(in-memory; identity recorded), `cache_if_expensive` (written to the
content-addressed cache when the plugin/stage declares an expensive cost
hint — see Amendment 2), `persist` (always written), and
`persist_on_failure` (opt-in debug dumps). Manifests, resolved configs,
metrics, identity blocks, audit events, and failure records are always
persisted regardless of policy. Two hashes with distinct jobs: the
**identity key** (computed before execution from input identities + plugin
id/version/params + code hash + seed; always computed; the cache key) and
the **content hash** (SHA256 of canonical serialized output; computed on
materialization or in verification mode). A key mismatch IS invalidation:
entries are immutable, no TTL, no GC in V1. V1 defaults: ModelOutputFrame,
TradeLog, EquityCurve, OutcomeFrames → `persist`; features,
representations, FittedState → `cache_if_expensive`; IntentFrames →
`ephemeral` unless simulation is configured.

## Amendment 1 — identity keys include platform-code identity (2026-08-06, finding N-1)

The identity key originally pinned **plugin** code only. A fix inside the
platform itself — window alignment in the frame code, an outcome-alignment
correction, a view-boundary fix — changes results while every plugin hash
stays identical, so stale cache entries would be reused and, worse,
double-run "determinism verification" could compare a cached artifact with
itself and certify the staleness as reproducible.

**Stage identity keys therefore include platform-code identity**, not only
plugin identity. V1 uses a deliberately conservative form:

- the repository **Git commit** of the platform, plus
- the **dirty/diff identity** for noncanonical runs
  ([ADR-0011](ADR-0011-run-identity-and-canonical-runs.md)).

Consequence, accepted knowingly: a platform commit invalidates prior stage
caches. That cost is paid for correctness — a core alignment or frame bug
fix **must** invalidate old caches. A narrower identity (e.g. hashing only
the modules a stage transitively imports) may replace this later by ADR if
cache hit rates prove painful; V1 does not attempt it.

**Determinism verification bypasses the cache.** Verification runs
recompute every stage independently and compare content hashes per tolerance
tier ([ADR-0016](ADR-0016-determinism-tiers.md)). Reading a cached artifact
during verification — thereby comparing an artifact with itself — is
prohibited and is itself a tested behaviour.

## Amendment 2 — `cache_if_expensive` is hint-driven in V1 (2026-08-06, review §6)

The original rule ("cache when measured wall-time ≥ 5 s") makes cache
presence depend on machine load: a stage near the threshold caches on one
run and not the next, so recovery and fan-out behaviour becomes
nondeterministic for marginal I/O savings. In V1, `cache_if_expensive`
consults an **explicit plugin/stage cost hint** only; no wall-time
threshold decides cache presence. Measured-cost caching may be revisited by
ADR once Phase 2 has real numbers.

## Amendment 3 — owner-authorized cache eviction under disk pressure (2026-08-27)

The V1 prohibition on cache garbage collection is narrowed for an explicit
disk-pressure operation approved by the owner. Content-addressed stage-cache
entries may be evicted manually when capacity is required for a new research
run, provided that:

- the exact cache subtree is resolved and recorded before deletion;
- canonical run directories, manifests, metrics, audit records, trial-ledger
  evidence, freezes, reports and training bundles are never deleted or hidden;
- eviction is reported as deletion of recomputable derived data, not deletion
  or replacement of prior research evidence; and
- a later cache miss recomputes the stage through the ordinary verified
  pipeline.

This amendment does not introduce TTL, LRU, automatic GC or result-dependent
eviction. It authorizes bounded owner-directed eviction only. The first
authorized operation is removal of the feature-cache contents under
`<runs_root>/cache/features` to make capacity available for the replacement
ULS+PUSH study.

## Alternatives considered
- Persist everything — simple, but every cheap feature pays Parquet I/O.
- Persist nothing — no fan-out reuse, no resumption, no audits.
- One hash for both jobs — either hashing every ephemeral output (waste) or
  cache keys that require execution first (useless).

## Consequences
Multi-policy fan-out and interrupted-run recovery come from cache hits.
Resume correctness requires that any artifact a later stage needs is at
least cacheable — the orchestrator must upgrade `ephemeral` when a
downstream consumer is configured.

## When to reconsider
If bounded manual eviction proves insufficient and automated GC is required
(revisit together with ADR-0017), if commit-scoped platform identity proves
too coarse in Phase 2 (measure
before narrowing it), or if cost hints prove a poor proxy for real expense.

## Related
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0017](ADR-0017-storage-files-as-truth.md) · V1.md §5.
