# ADR-0002 — Native time model: NativeTimeKey + ClockDomain, no UTC invention

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The dataset encodes broker wall-clock through the Unix-epoch convention
(`BROKER_WALL_CLOCK_ENCODED_AS_EPOCH`, observed +10,801 s vs system UTC at
export). Its manifest deliberately refuses UTC claims, and the broker
clock's DST history is unknown and unrecoverable. The original skeleton
required timezone-aware datetimes everywhere — a direct contradiction.

## Decision
The platform time axis is **NativeTimeKey** `{value: int64, unit,
clock_domain}`. Frames carry a bare int64 index plus one frame-level
`(unit, clock_domain)` tag — never per-row key objects. The MT5-specific
field name `mt5_time_raw` exists only inside the MT5 dataset adapter.
Cross-clock-domain comparison and joining are refusals; no resolver exists
in V1. Calendar/time-of-day logic requires an explicit CalendarTimePolicy;
V1 ships exactly one, `native-wallclock-v1` (mechanical decode treated as
broker wall-clock civil time, valid only inside this domain). The invariant
is **"no untagged time"**, not "everything must be UTC". Nothing may
attach UTC tzinfo to, or claim UTC semantics for, any dataset-derived time.

## Alternatives considered
- Force-UTC conversion — falsifies data by +2–3 h and bakes in an
  unverifiable DST history. Rejected on correctness.
- Naive datetimes throughout — readable, but reintroduces exactly the
  ambiguity the dataset manifest fought to kill.
- Tz-aware-only core (the skeleton's rule) — right instinct, wrong
  mechanism for this data.

## Consequences
Ordering, PIT slicing, windows, fit/infer/decision alignment, bar-unit
evaluation, and simulation all work without civil-time resolution. UTC
mapping, external time-based feeds, and cross-domain studies are blocked
until a real resolver exists — requesting one is an automatic ADR trigger.
Humans read integers unless a decoded label is displayed.

## When to reconsider
When authoritative broker clock-policy documentation appears, or when an
external data source must be joined (forces a resolver design).

## Related
[ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) ·
[ADR-0014](ADR-0014-snapshot-and-view-roles.md) · V1.md §3.
