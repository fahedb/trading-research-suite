# ADR-0014 — Snapshot policy, view roles, default discovery (AO-1)

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The raw 2026 partition is live-edged (it ends at the export instant, and
includes August rows). The V1 research universe must be frozen. But the
frozen snapshot (first usable bar → 2026-07-31) necessarily CONTAINS the
lockbox period 2026-01-01→07-31 — Architectural Objection AO-1: if the
snapshot were the default dataset, lockbox protection would be void.

## Decision
- A new immutable **snapshot view** ends at the final observed bar on
  2026-07-31 under `native-wallclock-v1`. The raw dataset is not modified;
  the boundary NativeTimeKey value is bound and recorded at view creation
  (Phase 1). Rows after the boundary are unreachable through any V1 view.
  Any later data update creates a **new dataset/view identity**.
- **AO-1 resolution (ratified):** the snapshot is an identity anchor and
  parent for the split views — never a working dataset. It is gated
  exactly like the lockbox.
- View manifests (format v1.1) add `role` (`raw | snapshot | train |
  validation | oos | lockbox`), `discovery_default`, and a required
  view-level `admitted_native_range`; row-bounded partitions express their
  admitted spans through `first_key` / `last_key` / `rows`.
- **Default discovery (owner amendment, ratified): TRAIN and VALIDATION
  only.** OOS is non-default: access must be explicit and is permanently
  recorded (`oos_touched`) — easier than the lockbox ceremony, but never
  ambient. SNAPSHOT and LOCKBOX require the full ceremony (ADR-0013).

## Amendment 1 — the gating rule binds parent dataset identities too (2026-08-06, finding B-3)

AO-1 was raised because the snapshot *contains* lockbox rows. The raw and
curated **parent datasets** have the identical property and were missed:
`XAUUSD_S_M1_CURATED_V1` admits the 2026 partition (210,573 rows through
2026-08-06 — the whole lockbox period **and** post-snapshot rows). As
originally written, a config naming the curated parent identity directly
would load legally, verify hashes, fire no ceremony, and produce no
`lockbox_touched` marking.

**General rule (identity-based, not name-based).** Any DatasetRef or view
whose admitted native range extends **beyond the OOS upper boundary** is:

1. `discovery_default: false`;
2. **unavailable through normal role resolution** — it cannot be reached by
   asking for a role, only by naming it under ceremony;
3. protected by the **full lockbox/snapshot access ceremony** of
   [ADR-0013](ADR-0013-lockbox-enforcement.md) — CLI unlock flag **and**
   structured config reason **and** write-ahead audit before any row is
   returned **and** permanent taint.

For the current dataset family this covers **the RAW parent, the CURATED
parent, the SNAPSHOT view, and the LOCKBOX view**. The rule is stated by
range so that future datasets inherit it automatically rather than relying
on someone remembering to add a name to a list.

**Phase 1 acceptance tests must prove:**
- default discovery exposes **TRAIN and VALIDATION only**;
- OOS resolution is explicit and writes an audit event
  (from Phase 1 — not deferred to the Phase 3 index);
- RAW / CURATED / SNAPSHOT / LOCKBOX access without the ceremony **raises**;
- **no gated row is returned before the write-ahead audit write succeeds.**

## Amendment 2 — snapshot-boundary binding is keys-only, audited access (2026-08-06, finding N-7)

Binding "the final observed bar on 2026-07-31" requires scanning native keys
inside the lockbox period. That is legitimate and unavoidable, and it is the
first sanctioned contact with those rows, so it is constrained:

- view-materialization tooling reads **native time keys only** — never OHLCV
  or spread values;
- the binding writes an **audit event** recording the procedure, the
  resulting boundary NativeTimeKey and the manifest it was written into;
- the bound value is recorded in the view manifest and never recomputed
  silently afterwards.

## Implementation guidance (recorded 2026-08-07, Phase 1 planning — no decision change)

Two operational clarifications for Amendment 1's gating rule:

1. **The gating boundary is data, not code.** "The OOS upper boundary" is
   read from a versioned split-policy record — never hardcoded in loader
   code — so a future split-policy change (which already requires an ADR
   and new view identities per [ADR-0012](ADR-0012-two-tier-splits.md))
   does not also require an unreviewed edit to gating logic. The resolved
   record and every generated view reference are pinned by exact-byte
   SHA-256 as specified by ADR-0012 Amendment 3. Every normalized V1
   manifest must carry a concrete observed admitted range; absence is a
   malformed-manifest refusal, not an alternate gating classification.
2. **Generated view manifests live under a separate platform-managed
   root, never under the immutable dataset root.** CLAUDE.md's
   dataset-immutability rule already forbids writing there; this states the
   corollary for the manifests Amendment 1/2 produce explicitly, so Phase 1
   tooling has one unambiguous answer rather than improvising a location.
3. **Row-bounded representation (clarified 2026-08-07, CP-1).** Row-bounded
   v1.1 views are represented by each admitted partition's
   `first_key`/`last_key`/`rows`, which describe the **admitted** span — not
   the physical file span — so the separate per-partition
   `admitted_native_range` this decision originally sketched is redundant
   and is not part of format 1.1. The view-level `admitted_native_range`
   remains and records the **actual observed** admitted row keys; the
   intended calendar boundary lives in the split-policy reference and, where
   a boundary was bound by scanning, the snapshot-binding provenance. This
   clarifies the accepted row-bounding intent; it changes no decision.
4. **RAW is source-only for public frames in V1 (clarified 2026-08-08,
   Slice 1D).** A RAW identity remains registrable, inspectable,
   classifiable, and usable as lineage or a partition source. It is not a
   legal target of the public `MarketFrame` loader, even after a valid RAW
   grant has been issued. The loader refuses RAW before partition-file
   access. Research frames come from CURATED or generated v1.1 views; the
   range-derived ceremony still applies to metadata access and to every
   loadable gated target.
5. **Concrete admitted range required (CP-3 N-1 Option B, 2026-08-08).**
   Every admitted dataset/view manifest must normalize to the concrete
   observed native range formed by its admitted partition endpoints. Current
   legacy and v1.1 readers all provide it; `None` is invalid. If a future
   adapter genuinely cannot provide a range, relaxing this contract requires
   an explicit reviewed change rather than speculative optionality today.

## Alternatives considered
- Snapshot as the canonical default dataset — voids the lockbox (AO-1);
  rejected.
- In-place truncation of the raw 2026 partition — destroys identity;
  rejected.
- OOS in default discovery with recording only (original proposal) —
  owner-amended to non-default; the friction is the point.
- Gating a hardcoded list of dataset names (Amendment 1) — brittle; a new
  dataset would silently arrive ungated. Rejected in favour of the
  range-derived rule.

## Consequences
TRAIN/VALIDATION are partition-aligned; LOCKBOX and SNAPSHOT are
row-bounded (first use of `admitted_native_range`). Ordinary experiments
cannot name 2025+ data without leaving a permanent record. Ordinary work
never touches the raw or curated parents directly: research reads Tier-1
views, and the parents exist as identity anchors and view sources.

## When to reconsider
At the next dataset snapshot (new raw export): new identities for
everything, and the then-current lockbox policy decides the new boundary.

## Related
[ADR-0004](ADR-0004-dataset-admission-and-integrity.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0013](ADR-0013-lockbox-enforcement.md) · V1.md §4.
