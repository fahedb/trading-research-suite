# ADR-0005 — GapIndex (arithmetic facts), not a semantic SessionModel

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The admitted data contains ~2,170 gaps (weekend closures, daily breaks,
possible outages). Naming these "sessions" or "holidays" asserts market
semantics the dataset cannot prove, especially given the broker-wall-clock
time domain with unknown DST history.

## Decision
V1 provides a **GapIndex / ContinuityMap** of observed arithmetic facts
only: previous/next native key, raw delta, deviation from the nominal
interval (60 s for this dataset), and objectively known boundary classes
(partition boundary, view boundary). It never claims a market session,
holiday, outage, or broker closure. An optional *annotator* under
CalendarTimePolicy `native-wallclock-v1` may label gaps (e.g., "spans
Sat–Mon in broker wall-clock"); its output is clearly marked policy-derived
and stored separately from GapIndex facts.

## Implementation guidance (recorded 2026-08-06, finding N-6 — no decision change)

The shipped dataset manifests already carry semantic gap vocabulary that
predates this ADR — `total_weekend_gap_count`, `total_intraweek_gap_count`,
`classification: "normal_boundary"`. Those fields are **provenance quotes
from the export tooling and must never become GapIndex semantics**: the MT5
adapter may record them as dataset metadata, but no platform component may
promote them to session, weekend, holiday or closure facts. GapIndex is
computed from observed native keys, never copied from manifest labels.

## Implementation guidance (recorded 2026-08-10, OD-G2 — no decision change)

**OD-G2 — `boundary_class` deferred from the V1 GapIndex. OWNER APPROVED
2026-08-10.**

The Decision above records objectively known boundary classes (partition
boundary, view boundary) among the facts GapIndex captures. Slice 1G planning
established that **no component in the current architecture can honestly
supply either class** to an arithmetic index computed from one frame's key
array:

- `MarketFrame` carries no partition provenance — it exposes `DatasetRef`,
  clock domain, unit, schema and the key/value arrays, and nothing about which
  physical file a row came from;
- a key gap does not prove a partition boundary, and inferring one from
  arithmetic would be invention;
- a cross-view seam exists only *between* `BoundedView` segments, and
  `BoundedView` composition is deliberately outside what GapIndex knows —
  teaching it that would couple an arithmetic component to authorization and
  composition machinery;
- so the only ways to ship the field are invented inference, leaking
  manifest/composition knowledge into the arithmetic component, or a column
  that is structurally always empty. **None of those is justified.**

A field that can only ever read `NONE` is worse than no field: a consumer
would reasonably infer "the platform checked and found no boundary here", when
in truth the platform never held the information.

**The V1 GapIndex therefore records no boundary-class column.** This is
deliberate minimalism, not an oversight, and it is a clarification of Slice
1G's implementation scope — **the architectural principle above is unchanged**.
GapIndex still records observed, objective facts only, and still never asserts
a market session, holiday, outage or closure.

Boundary facts may be added later **only** when an existing component can
objectively and cleanly supply them. Such an addition must be **additive** — a
further parallel column alongside the existing arithmetic ones — and **must
not mutate the arithmetic meaning** of the fields GapIndex already records.

## Alternatives considered
- A semantic SessionModel in V1 — asserts unverifiable claims; wrong labels
  would silently bias session-shaped features.
- No gap structure at all — window-based features would silently span
  closures.

## Consequences
A market-closure claim anywhere in V1 code is a bug by definition.
Window specs must explicitly choose their gap policy. Session-aware
research features become possible only via the annotator, with provenance.

## When to reconsider
When authoritative broker calendar documentation exists, a real
SessionModel can be layered on top — as a new, versioned, policy-derived
component, never by mutating GapIndex.

## Related
[ADR-0002](ADR-0002-native-time-model.md) · V1.md §4.
