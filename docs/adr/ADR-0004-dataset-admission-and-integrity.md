# ADR-0004 — Curated-manifest admission authority and hash-verified integrity

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The raw dataset contains partitions excluded for research (2011–2018 daily
resolution, 2019 mixed). The curated manifest lists admitted partitions and
pins every referenced file by SHA256. Filesystem immutability is convention
only — the owner can overwrite any file.

## Decision
The curated/view manifest is the **sole admission authority**: loaders read
exactly the files it lists — directory globbing is prohibited and tested.
**DatasetRef** carries `dataset_id`, `manifest_sha256`, and `role`; runs pin
all views by id and hash — `role` is a validated field, not a third
component of identity (see the implementation guidance below). Integrity
policy `verify: full` (SHA256 of every referenced
file before first read in a process) is required for canonical runs and any
gated-view access; `cached` is a dev-loop convenience only. Any mismatch is
a hard refusal — never a warning. Datasets are never modified: new data or
corrections produce a **new dataset identity**.

## Implementation guidance (recorded 2026-08-07, Phase 1 planning — no decision change; corrected 2026-08-07)

`DatasetRef`'s **canonical identity is exactly `(dataset_id,
manifest_sha256)`** — never a filesystem path. `role` is **not** a third,
independent component of identity: it is metadata `DatasetRef` carries and
a constraint the loader validates, not something that could vary the same
`(dataset_id, manifest_sha256)` pair into a different admitted view. The
role is already represented inside the manifest — changing a view's role
changes the manifest's contents and therefore its `manifest_sha256`, which
already produces a different canonical identity on its own. `DatasetRef`'s
`role` field must agree with the role the referenced manifest declares;
**a mismatch is a contract error**, not an alternate identity.

A registry root is separately resolver configuration (where to look), not
part of what a dataset or view *is*: the same `DatasetRef` must resolve
correctly if the underlying files are relocated, and nothing may substitute
"the path I loaded it from" for a `DatasetRef` identity check.

## Implementation guidance amendment — Slice 1D loader boundary (2026-08-08; no decision change)

For V1 row loading, `verify: full` means SHA-256 verification of **every file
referenced by the target manifest before any Parquet row decoding begins**.
Partition pruning is a decode/materialization optimization only: a target
partition outside the requested key range is still read for integrity
hashing, but is never opened through PyArrow or materialized into arrays.

The target manifest remains the sole admission authority. It supplies the
admitted partition set and order, bare filenames, hashes, admitted key spans
and row counts, target columns and adapter metadata, and byte sizes when it
declares them. Partition-source resolution supplies only the local directory
containing those exact bytes; source-manifest metadata must not fill in or
override any target admission field. If a target partition omits byte size,
the loader skips that precheck and relies on the required SHA-256 check. A
row-bounded target may legitimately admit fewer rows and a narrower key span
than its physical source file.

The public V1 `MarketFrame` loader does not accept RAW as a target, even when
the caller holds a valid RAW `AccessGrant`. RAW remains a registered identity,
provenance/lineage anchor, metadata and classification target, and physical
partition source. Public research frames are loaded only from CURATED or
generated v1.1 views. This restriction is enforced before any partition-file
access and is additional to, not a replacement for, Slice 1C authorization.

## Alternatives considered
- Trust-the-directory loading — one accidental overwrite or stray file
  silently corrupts research.
- Filesystem enforcement (chattr +i) — needs root and is bypassable by the
  owner anyway; verification-on-load is the enforceable guarantee.

## Consequences
Excluded data is structurally unreachable through the platform. Hash
verification (~sub-second at current size) is the real immutability
mechanism; the residual risk (out-of-band file edits between runs) is
detected at next load.

## When to reconsider
If dataset size grows to where full hashing is costly (tens of GB),
introduce a sampled/cached policy tier for noncanonical work — canonical
runs keep `full`.

## Related
[ADR-0014](ADR-0014-snapshot-and-view-roles.md) ·
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) · V1.md §4.
