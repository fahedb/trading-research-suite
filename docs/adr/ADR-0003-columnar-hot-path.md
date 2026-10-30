# ADR-0003 — Columnar hot path; minimal MarketFrame; domain objects at boundaries

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The usable dataset is 2.33 M rows (~165 MB columnar). The skeleton's
per-row Python objects (tuple-of-Bar contexts, per-step dicts) imply tens of
millions of allocations and O(N²) reference churn per pass — unusable at M1
scale, while the columnar equivalent fits in memory trivially.

## Decision
The computational hot path uses columnar structures behind **MarketFrame**,
a deliberately **small** public abstraction providing exactly: native-key
index, typed named columns, schema metadata, ClockDomain, DatasetRef, and
bounded/PIT views. It must not grow into a general-purpose DataFrame
library. NumPy/Arrow or similar are internal implementation details.
Pandas/Polars/DuckDB types never appear in public contract signatures
(plugins may use them internally). Per-row domain objects survive only at
validation boundaries, in tests, and as documentation vocabulary.

## Implementation guidance (recorded 2026-08-07, Phase 1 planning — no decision change)

The public computational column contract is a **read-only NumPy array**
(e.g. `MarketFrame.column(name)` returns one, with the writeable flag
cleared) — the concrete form "NumPy/Arrow ... implementation details" takes
at the public boundary. PyArrow may back the loader and frame internally,
but a PyArrow type never crosses into a public signature, exactly like
pandas/Polars/DuckDB. No custom `ColumnView` wrapper is needed to hide
NumPy — a read-only array already is the minimal, small abstraction this
ADR asks for; wrapping it would be the accretion this ADR's "when to
reconsider" warns against, not a defense of it.

Effective read-only exposure includes ndarray descriptor isolation. The
frame retains immutable shared data backing plus private storage metadata and
returns a fresh plain ndarray descriptor for each public key/column access.
Selected and sliced frames share the backing bytes, not a public descriptor
object. Ordinary Python/NumPy changes to a returned descriptor therefore
cannot change frame or sibling state, without paying a per-access data copy.
This is not a guarantee against hostile native-memory manipulation.

## Alternatives considered
- Keep the object model — simplest tests, dies at scale.
- Adopt Polars/DuckDB as the public contract — powerful, but couples every
  contract to one library's semantics and versioning.

## Consequences
Alignment discipline replaces per-object validation, which makes the
leakage sentinel rigs (Phase 2) mandatory rather than optional. A view's
upper bound is the point-in-time boundary.

## When to reconsider
If MarketFrame accumulates DataFrame-like conveniences (joins, groupbys,
expressions), stop and re-review — that growth is the failure mode this ADR
exists to prevent.

## Related
[ADR-0002](ADR-0002-native-time-model.md) ·
[ADR-0006](ADR-0006-fixed-stage-pipeline.md) · V1.md §4.
