# ADR-0021 — Columnar feature contract: FeatureProvider, WindowSpec, FeatureFrame

**Status:** ACCEPTED · **Date:** 2026-08-12

**Provenance.** This ADR records decisions the owner approved as Phase-2
planning decisions **OD-1…OD-5 on 2026-08-11**
([docs/phases/PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md)) together with
the Phase-2 implementation directions and the Slice-2B namespace refinement
approved **2026-08-12**. It creates no new decision of its own; it is the ADR
that OD-1 requires so that the contract change is recorded where architecture
decisions live, not only in a phase plan.

## Context

The skeleton's feature contract is per-point:

```
FeatureProvider.compute(MarketContext) -> FeatureSet
```

`MarketContext` carries a tuple of per-row `Bar` dataclasses with timezone-aware
`datetime` fields, and `FeatureSet` carries one instant's `Mapping[str, float]`.
At the admitted 2.33 M-row scale this is the allocation and O(N²) failure mode
[ADR-0003](ADR-0003-columnar-hot-path.md) exists to prevent, and its
timezone-aware timestamps contradict the native-time model of
[ADR-0002](ADR-0002-native-time-model.md).

[ADR-0019](ADR-0019-keep-and-refactor-skeleton.md) already authorizes
*replacing* the per-object bulk carrier, and [ADR-0003](ADR-0003-columnar-hot-path.md)
already mandates a columnar hot path. What no accepted ADR records is the
**concrete replacement contract** — and replacing a public plugin contract is
exactly the class of change the repository's own change-control rule says must
be recorded in an ADR before implementation. This ADR closes that gap.

It also settles two questions that only arise once features exist: how far back
a feature may read (which [ADR-0012](ADR-0012-two-tier-splits.md) Amendment 1's
mandatory purge is derived from), and what a feature window does at an
arithmetic gap (which [ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) requires
every window spec to choose explicitly).

## Decision

### 1. The per-point feature contract is replaced, not adapted

`FeatureProvider.compute(MarketContext) -> FeatureSet` is **removed**. There is
no shim, adapter or compatibility layer. A provider declares its identity,
version, ordered feature names, required input columns, canonical parameters, a
`WindowSpec`, and a cost hint, and computes over a columnar request.

`FeatureSet`, `MarketContext`, `Bar`, `ResearchInput` and `PipelineRecord` are
**not** retired by this ADR: their replacements (`ModelOutputFrame`, the
fit/infer lifecycle, RunManifest) belong to Phase 3, and deleting them now would
force redesigning `ResearchModel.infer` and `Evaluator.evaluate` prematurely
([ADR-0019](ADR-0019-keep-and-refactor-skeleton.md)'s "deleted in one commit once
nothing references them" applies then, not now).

### 2. Backward reach is finite, row-based, and declared

A provider declares a **finite maximum number of observed rows strictly before
the evaluation row** that it may read. Reach is counted in observed rows on the
native-key axis, never as a native-time distance.

Row-based reach is required rather than preferred: a time-distance window over a
broker-wall-clock axis containing ~2,200 discontinuities would have to attribute
meaning to a multi-day key delta, which is precisely the session/holiday/closure
semantics [ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) forbids asserting.

The declared value is an **upper bound**. Over-declaration is safe — it yields a
purge at least as conservative as the minimum ADR-0012 Amendment 1 requires.
**Under-declaration is a release blocker**, and is enforced by an adversarial
backward-reach test rather than trusted.

Unbounded or expanding history is **not representable**. A method needing
expanding statistics belongs in a fitted `RepresentationModel`
([ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md)), fitted on TRAIN and
applied to the evaluation range — not in a feature.

### 3. Exactly two gap policies, explicitly declared

| Policy | Semantics |
|---|---|
| `CROSS` | A window may use observed rows from both sides of an arithmetic gap. |
| `BREAK` | An arithmetic gap resets the window; output is unavailable until a complete declared window has accumulated again after the gap. |

There is **no default and no inferred policy** — the explicit choice
[ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) requires. An arithmetic gap is
an adjacent-key delta differing from a **caller-declared nominal interval**,
which is never parsed from a timeframe label, derived from `TimeUnit`,
hardcoded, or inferred from the data — the same discipline `GapIndex` already
enforces. Gap semantics remain purely arithmetic; no session, holiday, outage or
closure meaning may ever be inferred.

**`TRUNCATE` / partial-window output is deliberately not supported in V1.** This
is a scope decision for want of a concrete research requirement, not a judgement
that truncation is invalid, and it is recorded so it is not re-litigated. The
reasoning: the platform cannot compute a value from fewer observations — only a
provider can — and the *meaning* of a partial-length value is estimator-specific
(benign for a rolling mean; a different random variable for a fixed-horizon
return; a different support for a z-score or rank). A platform vocabulary whose
third member resolves to "ask the provider" is not platform vocabulary. If a
research family later requires it, it may be added through the normal reviewed
architecture-change process.

### 4. Warm-up and gap invalidity are enforced by the platform

The platform computes the unavailability mask from the declared reach, the
declared gap policy and the observed key axis, and applies it **after** the
provider returns. A provider therefore cannot emit a value it could not have
computed, so the declaration that Phase-3 purge depends on is **enforced, not
trusted**.

### 5. Computed features use their own dense carrier

Computed features are carried by a type distinct from `MarketFrame`, which keeps
its meaning as loader-issued, verified, admitted market data and remains
mintable only by the data layer.

The carrier is columnar and **dense**, indexed **exactly by the observed
evaluation keys**. Context rows may participate in computation but never appear
as output. Columns are `float64` in V1; finite values are valid; `NaN` means
"unavailable at this evaluation key"; ±infinity is invalid and refused. Feature
name collisions across composed providers fail fast. Each output column retains
provenance binding it to its provider and its window declaration.

Density does not contradict
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md): that amendment
legalizes a strict key subset for `ModelOutputFrame` and `IntentFrame` only, and
its load-bearing "absence of a row is not a dense `NaN` row" distinction is
inapplicable to a dense carrier, which has no absence. Sparse feature rows and
non-numeric columns are deferred until a concrete research requirement justifies
them.

### 6. Cross-segment context uses a private computation buffer

When backward context spans more than one authorized `BoundedView` segment, the
platform may build a **private, temporary, contiguous computation buffer** from
already-authorized arrays. It is not a `MarketFrame`, invents no `DatasetRef`,
is not a dataset or view, performs no file I/O and no authorization, cannot
bypass the loader/grant boundary, and is never persisted as market data.
Contributing source identities and bounds remain visible in provenance and enter
the stage identity; output remains limited to evaluation keys.

This satisfies the Slice-1E prohibition literally — no multi-identity or
concatenated `MarketFrame` is created, and no identity is hidden. Segment
stitching is platform-owned rather than reimplemented by every provider, which
is what allows the leakage rigs [ADR-0003](ADR-0003-columnar-hot-path.md)
requires to test the seam in one place instead of N.

### 7. Providers read market columns only

A provider may read only the input columns it declared, may not read any key
beyond the evaluation upper boundary, and **may not consume another provider's
output**. Feature-of-feature composition would be the dependency-graph engine
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) exists to refuse; if it is ever
genuinely needed, it is a new ADR.

The reserved outcome/target namespace remains prohibited as feature input
([ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md)); feature names in
that namespace are refused before computation.

**Owner-approved Slice-2B namespace refinement (2026-08-12; no broader V1
rule):** the reserved namespace is exactly the case-sensitive name `target`
and names beginning `target.`. The platform refuses it in both a provider's
`feature_names` and `required_columns`, during complete-plan validation before
any provider computation begins. Names outside that exact predicate remain
legal; V1 does not infer, normalize or reserve any additional spelling.

## Alternatives considered

- **Adapt the per-point contract with a batching shim** — preserves a contract
  that cannot carry 2.33 M rows and keeps timezone-aware timestamps in the hot
  path; a shim would also have to be deleted later anyway.
- **Reuse `MarketFrame` for computed features** — would require widening its
  private minting seam and would blur verified admitted market rows with
  computed values; the loader-issued guarantee is the point of that type.
- **Native-time-distance windows** — requires attributing meaning to key deltas,
  which ADR-0005 forbids. Deferred as an additive extension with its own gap
  story.
- **A third `TRUNCATE` gap policy** — see §3; the platform cannot define the
  meaning of a partial-length value.
- **Trusting providers to mask their own warm-up** — makes the declared reach,
  and therefore the mandatory purge, unenforceable.
- **Recording this contract only in the phase plan** — would leave a public
  contract change outside the ADR record, contrary to the repository's
  change-control rule.

## Consequences

Feature output becomes a first-class, identity-bearing, cacheable artifact whose
declared reach feeds the Phase-3 sample information span directly. Leakage and
reach protection become mechanically testable over every registered provider,
because alignment, warm-up, gap invalidation and segment stitching all live in
one place. The cost is that a feature must declare a finite reach and an explicit
gap policy — deliberately, since both are what make purge derivable and leakage
testable.

Phase 3 inherits an obligation this ADR does not discharge: deriving the sample
information span and the fold purge from these declarations.

## When to reconsider

If a legitimate research family requires partial-window output, sparse feature
rows, non-numeric feature columns, time-distance windows, or feature-of-feature
composition, extend by ADR — never by widening a provider's latitude silently.
If `FeatureFrame` begins accumulating DataFrame conveniences, stop and re-review,
for the same reason [ADR-0003](ADR-0003-columnar-hot-path.md) states for
`MarketFrame`.

## Related

[ADR-0002](ADR-0002-native-time-model.md) ·
[ADR-0003](ADR-0003-columnar-hot-path.md) ·
[ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) ·
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0019](ADR-0019-keep-and-refactor-skeleton.md) ·
implementation detail: [docs/phases/PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md)
