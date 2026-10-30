# Phase 1 — Data Layer & Time Model — Implementation Plan

**Status:** **PHASE 1 COMPLETE** (2026-08-10, CP-5 owner-approved) · **§13
post-Phase-1 operational checklist COMPLETE (2026-08-12) — the Phase-2
implementation gate is CLEARED** · Phase 2 implementation NOT_STARTED but
**now permitted**, subject to [PHASE2_FEATURES.md](PHASE2_FEATURES.md) and
ADR-0021 ·
*historical progression below* — **Slice 1D
COMPLETE and merged into `main`; CP-2 APPROVED; CP-3 independent bounded
review COMPLETE and owner APPROVED; Slice 1E implementation and focused
independent review COMPLETE; verdict APPROVE; owner-approved and merged into
`main`; Slice 1F planning/reconciliation COMPLETE with owner decisions
OD-F1…OD-F6 approved (2026-08-09); Slice 1F Stage A COMPLETE, independently
reviewed, owner-approved and merged; Stage-B production preparation COMPLETE
with D-1…D-6 and R-1…R-3 owner-approved; Stage-B operational script COMPLETE,
independently reviewed, owner-approved and merged; Stage-B production generation
COMPLETE (2026-08-09); **CP-4 independent review COMPLETE (APPROVE WITH
NON-BLOCKING NOTES) and OWNER APPROVED 2026-08-10; Slice 1F COMPLETE; Slice 1G
planning COMPLETE with OD-G2 OWNER APPROVED 2026-08-10; Slice 1G implementation
COMPLETE, independently reviewed APPROVE (findings N-1/N-2 remediated and
confirmed RESOLVED), OWNER APPROVED 2026-08-10 and merged into `main` at
`fa69733367ee4c340894ff9a0d5d8b6b99f92a6e`; Slice 1G COMPLETE; Slice 1H
planning COMPLETE for Stage 1H-A scope with **OD-H1 OWNER APPROVED 2026-08-10**;
Slice 1H-A implementation COMPLETE, independently reviewed APPROVE WITH
NON-BLOCKING NOTES, **OWNER APPROVED 2026-08-10** and merged at
`64be3756bc7d4fdff2c5465774c3b5fc26998c71`; **OD-H2 OWNER APPROVED 2026-08-10
and Stage 1H-B qualification COMPLETE — executed once 2026-08-10 at
`ad1e507a3577b377317d4c68d029454479d43d7a`, all measured budgets passed, cold
honestly unmeasured (sudo unavailable), audit 2→3 exactly; §8 numbers and §11
verified; **CP-5 COMPLETE — APPROVE WITH NON-BLOCKING NOTES, OWNER APPROVED
2026-08-10; Slice 1H COMPLETE; PHASE 1 COMPLETE**; **§13 COMPLETE 2026-08-12
(item 3 Hyper-V export owner-attested; item 4 owner review COMPLETE) — the
Phase-2 implementation gate is CLEARED and Phase-2 implementation is
permitted, though not yet started**** ·
**Date approved:** 2026-08-07 (Slice 1E contracts approved
2026-08-08; Slice 1F contracts approved 2026-08-09)
Normative for Phase 1 execution. Architecture reference:
[docs/architecture/V1.md](../architecture/V1.md) · Roadmap entry:
[docs/roadmap/V1_ROADMAP.md](../roadmap/V1_ROADMAP.md) (status only — this
document owns the detail). Provenance: planning session of 2026-08-07,
preserved verbatim at
[reviews/2026-08-07-phase1-planning-report.md](reviews/2026-08-07-phase1-planning-report.md).

This document is the single place an implementation session should read to
know what Phase 1 builds, in what order, through which contracts, and when
to stop and ask for review. It does not restate architecture that ADRs
already own — it operationalizes it.

---

## 1. Objective

Honest, fast, immutable data access on the native time model: a researcher
(or a future FeatureProvider) can request a bounded, point-in-time view of
market data and receive exactly the rows it is entitled to see, with
integrity, gating and audit as structural properties of the loader — not as
conventions a caller must remember.

## 2. Scope

- `NativeTimeKey`, `ClockDomain`, `TimeUnit`, `CalendarTimePolicy` and the
  cross-domain refusal rule (ADR-0002).
- `DatasetRef` and view-identity primitives (ADR-0004).
- Manifest v1.1 schema, reader adapters for the existing raw/curated
  manifests, dataset/view registry, role-based and exact-ref resolution
  (ADR-0004, ADR-0014).
- Integrity verification, mode `full` only (ADR-0004).
- MT5 dataset adapter.
- Minimal `MarketFrame`: native-key index, typed columns as read-only NumPy
  arrays, schema metadata, `ClockDomain`, `DatasetRef`, bounded/PIT slicing
  (ADR-0003).
- Column- and partition-selective loading.
- `BoundedView` with `context_start` / `eval_start` / `eval_end` semantics
  and backward-context authorization (ADR-0012 Amendment 2).
- Representable, hash-pinned, ordered adjacent-view unions (ADR-0012
  Amendment 2) — representable only; nothing consumes them in Phase 1.
- TRAIN / VALIDATION / OOS / LOCKBOX view generation; SNAPSHOT parent
  anchor; keys-only audited snapshot-boundary binding (ADR-0014).
- Range-derived gating of RAW, CURATED, SNAPSHOT, LOCKBOX; OOS explicit
  access with write-ahead audit from day one (ADR-0013, ADR-0014).
- `GapIndex` / `ContinuityMap`, arithmetic facts only (ADR-0005).
- Add-then-migrate retirement of the object-heavy skeleton carriers
  (ADR-0019).
- Phase 1 performance qualification and completion documentation.

## 3. Non-goals (explicitly out of Phase 1)

`FeatureProvider` and any feature calculation · `WindowSpec` semantics
beyond what the view contract itself needs · artifact caching for computed
features · `RepresentationModel` · `ResearchModel` · `TrainingTargetFrame`
· `OutcomeSpec` calculation · `DecisionPolicy` · `IntentFrame`
implementation · simulation · experiment orchestration · SQLite run
registry · sweeps · vector similarity or any research strategy code · fold
generation or purge computation (ADR-0012 Amendment 1 — the *contract*
Phase 1's views must support is in scope; the *algorithm* is Phase 3).

If implementing a slice below seems to require reaching into one of these,
stop — that is a scope signal, not a shortcut to take silently.

## 4. Owner-approved decisions (2026-08-07)

These were open questions in the planning report; the owner ruled on all of
them. They are binding for Phase 1 implementation.

| # | Decision |
|---|---|
| 1 | PyArrow and NumPy are added as dependencies **immediately before Slice 1D** (the first slice that reads a real Parquet file), not earlier. |
| 2 | PyArrow is an internal implementation detail only — never in a public signature. |
| 3 | Read-only NumPy arrays **are** the V1 public computational column contract. No custom `ColumnView` wrapper to hide NumPy. |
| 4 | pandas, Polars, DuckDB and PyArrow types must never appear in public core contracts. |
| 5 | Generated view manifests live under `/path/to/research-views/` — a new root, never under the immutable dataset root. |
| 6 | Canonical dataset/view identity is exactly `(dataset_id, manifest_sha256)`. `DatasetRef` also carries `role`, but role is a validated field — it must agree with the role the referenced manifest declares, and a mismatch is a contract error — not an independent third component of identity (role is already inside the manifest; changing it changes `manifest_sha256`, which already yields a different identity). A filesystem path is resolver configuration or a local location hint — never part of research identity. |
| 7 | The gating boundary is read from `config/split-policies/adr-0012-v1.json`, never hardcoded in the loader. Exact policy identity is `(adr, policy_version, policy_sha256)` over the record's exact bytes; v1.1 manifests pin all three fields. Every V1 normalized manifest resolves a concrete observed admitted range; a missing range or a missing, malformed, unsupported or mismatched policy is refused. |
| 8 | Integrity verification implements mode `full` only in Phase 1; `cached` is deferred until dataset size makes it necessary (ADR-0004's own reconsideration trigger). |
| 9 | Current `ClockDomain` id: `mt5.justmarkets-demo3.wallclock`. |
| 10 | `timestamp_mt5_encoded` is **never loaded into MarketFrame** — it remains adapter-level display metadata only. |
| 11 | The cold-cache benchmark (`drop_caches`, requires sudo) is a **manual** Phase 1 qualification measurement. If sudo is unavailable, that is recorded honestly (§10) and does not block ordinary implementation slices. |
| 12 | Snapshot cap is inclusive: `2026-07-31T23:59:00` under `native-wallclock-v1`. The bound is the largest observed native key not exceeding the cap. |
| 13 | All gated access and persistent view-creation events are written to `/path/to/research-runs/audit.jsonl`, using append semantics, `fsync`, and a short advisory process lock. **No partition data file may be opened before the audit write succeeds.** A temporary test-only manifest assembled from already-authorized metadata is fixture setup, not persistent Slice 1F view creation; it uses a NORMAL grant and never touches the real audit log. |
| 14 | GapIndex is lazy, compact (columnar, not object-per-gap) and ephemeral (not persisted) in Phase 1. |
| 15 | Implementation order is 1A → 1B → 1C → 1D → 1E → 1F → 1G → 1H (§6) — gating and audit land in 1C, **before** the first real row is ever read in 1D. |
| 16 | Every slice updates documentation before it is considered complete (§9). |

## 5. Minimal contracts

Semantic sketches only — no implementation code. See the planning report
(§7) for the full justification table; the summary here is what an
implementer types against.

```
NativeTimeKey        value: int64 · unit: TimeUnit · clock_domain: ClockDomain
                     scalar/boundary use only — never a per-row object
ClockDomain          id: str  (equality is identity; cross-domain compare raises)
TimeUnit             SECONDS | MILLIS | MICROS | NANOS   (dataset: SECONDS)
CalendarTimePolicy   "native-wallclock-v1" — the only V1 member

DatasetRef           dataset_id: str · manifest_sha256: str · role: ViewRole
                     canonical identity = (dataset_id, manifest_sha256) only;
                     role is validated, not identity-bearing — role must
                     agree with the role the referenced manifest declares,
                     else it is a contract error (ADR-0004)
ViewRole             RAW | CURATED | SNAPSHOT | TRAIN | VALIDATION | OOS | LOCKBOX

MarketFrame   (immutable, columnar, zero-copy views)
  properties:  dataset_ref · clock_domain · unit · schema · num_rows
               keys              -> read-only int64 NumPy array, strictly increasing
               column_names
  operations:  column(name)      -> read-only typed NumPy array
               select(names)     -> MarketFrame (zero-copy, fewer columns)
               slice_by_keys(lo, hi) -> MarketFrame (zero-copy, inclusive bounds)
  forbidden:   joins, groupby, resample, expressions, arithmetic, mutation,
               per-row iteration, statistics, I/O, gap semantics

BoundedView   (refined by owner decision OD-2, 2026-08-08)
  view_id · role · context_start · eval_start · eval_end  (NativeTimeKey values)
  segments       -> ordered immutable sequence of BoundedSegment; each segment
                    retains its own exact DatasetRef
  eval_frame     -> the bounded MarketFrame covering only the evaluation view
                    and [eval_start, eval_end]
  context        -> ContextResolution (structured, may carry ContextShortfall)
  invariant: context_start <= eval_start <= eval_end; nothing readable beyond
             eval_end; no unbounded underlying frame is reachable
  NO frame():   there is no cross-view whole-range frame accessor, and no
                multi-identity or concatenated MarketFrame is ever created

BoundedSegment  dataset_ref (exact, hash-pinned) · role · bounded MarketFrame
                covering only that segment's admitted, bounded span

ContextResolution  structured outcome of backward-context resolution:
                   requested vs. resolved context boundary, status, and an
                   optional ContextShortfall
ContextShortfall   structured evidence that insufficient backward context was
                   explicitly accepted (OD-3): requested boundary, resolved
                   boundary, structured reason code, bounded human-readable
                   detail, explicit accepted-short-context status

ViewUnion    ordered Tier-1 fitting/promotion members (TRAIN/VALIDATION/OOS
             only — LOCKBOX forbidden, OD-4), each pinned by
             (dataset_id, manifest_sha256); representable in Phase 1 only.
             Contains no MarketFrame, performs no load, and exposes no union
             hash/fingerprint in Slice 1E (identity is Phase 3).
ViewUnionMember  one pinned member of a ViewUnion: exact identity + role

open_bounded_view(
    *,
    loader: MarketFrameLoader,
    grants: Sequence[AccessGrant],
    context_start: NativeTimeKey,
    eval_start: NativeTimeKey,
    eval_end: NativeTimeKey,
    ...
) -> BoundedView
             the single approved Slice 1E public composition entry point
             (OD-1). `loader` is the explicit configured infrastructure
             dependency, not authorization. `grants` supplies the exact
             authorization authorities. Not a decoder: it composes verified
             frames only through MarketFrameLoader.load.

GapIndex     compact columnar arithmetic facts (§ Slice 1G)
AccessRequest exact DatasetRef + optional structured reason + optional unlock
AccessContext UUID4 session + caller/platform identity only (§ Slice 1C)
AccessGrant   immutable process-local result of the gate; exact manifest,
              policy and session bound; sole public row-loader authority
```

**Public-contract boundary (owner decisions 2–4 plus the CP-2 integration
seam):** `MarketFrame` metadata may expose `DatasetRef`; public computational
contracts expose `NativeTimeKey`/`ClockDomain` scalars and read-only NumPy
arrays. The sole public authority for entering any market-row loader is an
`AccessGrant`, never a bare `DatasetRef` or other metadata/location object.
PyArrow (and pandas/Polars/DuckDB) never appear in a public type. Architecture
tests enforce both boundaries from the moment PyArrow and the loader land
(Slice 1D).

**Approved Slice 1E extension of that boundary (OD-1, 2026-08-08).**
`MarketFrameLoader.load(AccessGrant, ...)` remains the *sole* public row
loader/decoder. Slice 1E adds exactly one explicitly approved public
*composition* entry point, `open_bounded_view(...)`, which is not an
independent decoder: it composes verified frames only through the existing
`MarketFrameLoader` supplied explicitly by its caller. Possession of that
loader authorizes nothing; exact grants remain mandatory for every underlying
load. The Slice 1D loader guard is not weakened or removed — the Slice 1E
architecture tests **enumerate and constrain** the new composition route
alongside it. Details and the full refusal list are in the Slice 1E section
below.

## 6. Slices 1A–1H

Each row: objective · files/packages · depends on · tests added · acceptance
· rollback boundary · deliberately unbuilt. Documentation duty is §9 (not
repeated per slice here to avoid the duplication the task asks to avoid).

### 1A — Core time and identity — **COMPLETE** (2026-08-07, `89a1650`)

- **Objective:** `NativeTimeKey`, `ClockDomain`, `TimeUnit`,
  `CalendarTimePolicy`, `ViewRole`, `DatasetRef`, and the cross-domain
  refusal rule.
- **Files:** `core/time.py`, `core/identity.py`, `core/errors.py` (extend),
  `core/__init__.py` (re-exports), `tests/test_time.py`,
  `tests/test_identity.py`, `tests/test_architecture.py` (extend).
- **Depends on:** nothing (pure addition).
- **Tests:** cross-domain compare/join raises; no `utc` identifier anywhere;
  `NativeTimeKey` equality/ordering; `CalendarTimePolicy` has exactly one
  member.
- **Acceptance:** new module tree type-checks and tests green; nothing else
  in the repo imports it yet.
- **Rollback boundary:** trivial — delete the two new files.
- **Deliberately unbuilt:** frames, I/O, PyArrow/NumPy (not yet a
  dependency).

**Contracts implemented.** `TimeUnit` (4 members, labels only — no
conversion factors) · `CalendarTimePolicy` (`native-wallclock-v1`, identity
only) · `ClockDomain(identifier)` · `NativeTimeKey(value, unit,
clock_domain)` · `ViewRole` (7 members) · `DatasetIdentity(dataset_id,
manifest_sha256)` · `DatasetRef(dataset_id, manifest_sha256, role)` with
`.canonical_identity` · errors `ClockDomainError`, `TimeUnitMismatchError`
under the existing `ContractViolationError`.

**Implementation decisions worth carrying forward.**

1. **Ordering refuses; equality does not.** Ordering across units or domains
   raises (`TimeUnitMismatchError` / `ClockDomainError`); `==` returns
   `False` instead. Keys are set members and dict keys while resolving
   manifests and configs, and an `__eq__` that could raise would make those
   containers unusable. The asymmetry is semantic, not a compromise: *"are
   these the same key"* is answerable across domains, *"which came first"*
   is not.
2. **Ordering is hand-written, never `@dataclass(order=True)`.** Generated
   ordering compares the field tuple, which would silently sort by clock
   domain string when domains differ — the exact failure this slice exists
   to prevent.
3. **`DatasetRef` equality spans all three fields**, deliberately broader
   than `canonical_identity`. Two refs naming one manifest under different
   roles are unequal objects sharing one identity; at most one is correct,
   and collapsing them under `==` would hide the disagreement rather than
   surface it. **`DatasetIdentity` is a separate type** so that anything
   computing a fingerprint or cache key consumes identity alone — role
   cannot leak in by accident.
4. **SHA-256 digests are normalized to lower case, not rejected.**
   `hashlib` emits lower case but external tooling may emit upper; both
   denote one manifest, so identity comparison must not depend on typing.
5. **`ClockDomain.identifier`, not `.id`** — avoids shadowing the `id`
   builtin (ruff A003). Minor deviation from §5's sketch, no semantic
   change.
6. **`bool` is rejected at runtime although it satisfies `int` statically.**
   `NativeTimeKey(True, …)` would otherwise become key `1`. The test for
   this carries no `type: ignore`, which documents that the type checker
   cannot catch it.
7. **`dataset_id` refuses path separators**, so a filesystem path cannot be
   smuggled in where an identity is expected.

**Acceptance evidence.** Gate green — 152 tests (from 50), ruff clean, mypy
strict clean over 34 files. Six architecture guards added, **each verified
to fail when deliberately violated** (vendor name in core; `datetime` import
in `core/time.py`; a `UTC` identifier in the primitives; a third-party
import in the primitives; a legacy-carrier import; a DataFrame-library
import in `core`/`data`). The UTC guard is AST-scoped to *identifiers* so
prose may discuss UTC and future operational timestamps such as a manifest's
`generated_at_utc` stay legal — the brittle repository-wide substring ban
was explicitly avoided. Dependency files, dataset root, views root and runs
root all confirmed untouched; no legacy type retired.

**Deferred to 1B (recorded, not forgotten).** `DatasetRef.role` must agree
with the role its manifest declares; that check needs a manifest reader and
is therefore Slice 1B, tested there. Nothing in the repository imports the
new primitives yet — wiring them into a loader is 1B/1D.

### 1B — Manifest readers and registry (metadata only, zero rows) — **COMPLETE** (2026-08-07, `302721a`)

- **Objective:** parse the existing raw (1.0.0) and curated (1.0.0)
  manifests and the new v1.1 format through one internal
  `DatasetManifest` model; a registry that resolves by exact `DatasetRef`
  or by role, from explicitly configured roots.
- **Inherited from 1A:** implement the `DatasetRef.role` ↔ manifest-declared
  role agreement check here, and test that a mismatch raises. ✅
- **Files:** `data/manifests/{__init__,models,v11_schema,readers}.py`,
  `data/registry.py` (extend), `data/__init__.py`, `core/errors.py`
  (extend), `core/__init__.py` (re-exports), `tests/manifest_fixtures.py`,
  `tests/test_manifests.py`, `tests/test_dataset_registry.py`,
  `tests/test_architecture.py` (extend).
- **Depends on:** 1A.
- **Acceptance:** registry answers "what datasets/views exist" and "what
  partitions does this manifest admit" without opening a single Parquet
  file. ✅
- **Rollback boundary:** additive; no loader depends on this yet.
- **Deliberately unbuilt:** any file read beyond manifests; gating; frames.

**Normalized manifest model** (`NormalizedManifest`, frozen, no local
paths): `dataset_id` · `manifest_sha256` (computed over exact bytes) ·
`role` · `source_format` · `discovery_default` · `adapter_id` · `symbol` ·
`timeframe` · `clock_domain` · `time_unit` · `calendar_time_policy` ·
`columns` · `partitions` · `admitted_native_range` · `expected_row_count` ·
`declared_parent` · `partition_source`. Derived: `.identity`,
`.time_key_column`, `.frame_columns`. Supporting types: `ManifestColumn`
(name, purpose, optional dtype), `ColumnPurpose`
(`time_key`/`value`/`display_only`), `PartitionMetadata`, `NativeKeyRange`,
`DeclaredParent`, `ManifestFormat`. Cross-format validations: partitions
ordered and non-overlapping by admitted span; `admitted_native_range` must
**equal** the observed admitted endpoints; `expected_row_count` must equal
the sum of admitted partition rows (both rules verified to hold on the real
RAW and CURATED manifests before adoption).

**Reader adapters**, selected by explicit marker — never by filename or
shape-sniffing:

| Marker | Reader | Normalized role | Discovery |
|---|---|---|---|
| `source: "MetaTrader5"` + `dataset_builder_version: "1.0.0"` | raw | `RAW` | never default |
| `kind: "curated_view"` + `curation_format_version: "1.0.0"` | curated | `CURATED` | never default |
| `manifest_format_version: "1.1"` | v1.1 | as declared | as declared |

**Registry surface** (`DatasetRegistry`): `discover(roots)` ·
`resolve(ref)` · `default_views()` · `resolve_role(role)` ·
`all_manifests_for_diagnostics()`. `RegisteredManifest` wraps a normalized
manifest with `manifest_path`, `root` and `resolved_parent`.

**Default-discovery semantics.** `default_views()` returns only manifests
declaring `discovery_default`; the v1.1 schema refuses that flag on any role
other than TRAIN/VALIDATION, and both legacy readers hardcode it false.
`all_manifests_for_diagnostics()` is the deliberately verbose administrative
listing — visibility, never access. On the real dataset root today,
`default_views()` is correctly empty: TRAIN/VALIDATION arrive in 1F.

**Implementation decisions worth carrying forward.**

1. **Digest is over exact bytes, not re-serialized content.** Reformatting a
   manifest changes its identity — deliberately strict, and tested.
2. **Identity carries no path.** `NormalizedManifest` holds zero local
   paths; location lives on `RegisteredManifest`. After CP-1, v1.1 lineage
   is identity-only and partitions everywhere carry bare filenames; the one
   surviving manifest-declared path — the legacy curated parent location,
   provenance from an immutable file — is checked against every configured
   root, so a manifest cannot name files outside the roots it was found
   under.
3. **Legacy tolerance vs. v1.1 strictness is asymmetric on purpose.** The
   legacy files are immutable artefacts carrying exporter fields we do not
   model, so unknown keys are ignored; v1.1 is generated by this platform,
   so `extra="forbid"` and every required field must be present.
4. **The MT5 clock domain is derived** from the declared `trade_server`
   (`mt5.{server.lower()}.wallclock`), not hardcoded — a different server
   yields a different domain, which makes cross-domain comparison refuse on
   its own. The reader also refuses an unexpected `timestamp_policy` or
   clock classification rather than assuming the integer is untouched.
5. **`timestamp_mt5_encoded` is marked `DISPLAY_ONLY`** by the adapter and
   is absent from `frame_columns` (owner decision 10), keeping it out of the
   computational path by construction.
6. **Declared vs. resolved parent stay separate.** The legacy curated format
   names its parent without pinning a digest; `declared_parent.
   manifest_sha256` is `None` rather than fabricated, and the registry
   attaches `resolved_parent` after resolving the id against actually
   discovered manifests. A v1.1 pin that disagrees with the discovered
   parent is a `ParentResolutionError`.
7. **Duplicate ids are fatal even when bytes are identical** — "last one
   wins" would make which copy a run consumed depend on scan order.
8. **Resolution is not authorization.** `resolve()` succeeds for a LOCKBOX
   identity and returns metadata; permission is Slice 1C's decision, tested
   explicitly.

**Errors added:** `ManifestError` → `UnsupportedManifestFormatError`,
`MalformedManifestError`; `DatasetRegistryError` → `RegistryRootError`,
`ManifestLocationError`, `DuplicateDatasetIdError`, `DatasetNotFoundError`,
`ParentResolutionError`; `DatasetRefMismatchError` under
`ContractViolationError` (a ref that misdescribes what it names is a caller
contract violation).

**Acceptance evidence.** Gate green — 234 tests (from 152), ruff clean, mypy
strict clean over 41 files. The **entire suite was re-run under a hook that
raises on any `.parquet` read**, and passed: this slice provably opens no
partition file. Three architecture guards added and each mutation-verified
to fail when violated (parquet glob including computed patterns; row-decoder
import into metadata modules; legacy-carrier import), plus role-mismatch and
duplicate-id refusals mutation-checked. Dependency files, dataset root,
views root (still absent) and runs root all confirmed untouched; no legacy
type retired.

**Inherited work for Slice 1C.** Effective gating is range-derived from the
versioned split-policy record; after CP-1 the manifest carries **no**
authorization field at all — 1C computes access classification from
`admitted_native_range` + `split_policy_ref` + the policy record, every
time. The `ManifestLocationError` root-containment check is location safety
only, not access control. Deferred and recorded: parent/child clock-domain
agreement is not yet checked (no contract requires it before frames exist
in 1D).

### CP-1 — **APPROVED** (2026-08-07, after targeted corrections in `e831032`)

A targeted CP-1 schema review raised two owner concerns (a parent
`relative_path` inside the hashed v1.1 bytes; a stored `gated` boolean) and
surfaced one blocker (row-bounded SNAPSHOT/LOCKBOX views were not
expressible) plus one gap (an unvalidated `row_count_expected`). The owner
approved five corrections, all applied before any real v1.1 file exists:

1. **`parent.relative_path` removed** — v1.1 lineage is identity-only
   `(dataset_id, manifest_sha256)`; relocating a parent on disk no longer
   changes the child view's canonical identity. The legacy curated format's
   source-declared parent path survives unrewritten as provenance.
2. **`gated` removed** — no authorization field exists in any manifest;
   effective gating is derived in 1C from the admitted range and the
   versioned split policy. One source of truth.
3. **Admitted-span semantics** — partition `first_key`/`last_key`/`rows`
   describe what the view **admits** from that partition, never the
   physical file span; the redundant per-partition `admitted_native_range`
   is gone. Row-bounded views are now expressible and tested with
   real-shaped 2026 keys. The view-level `admitted_native_range` records
   **actual observed** admitted keys and must equal the observed endpoints
   — a calendar-policy boundary written there is refused. Policy intent =
   `split_policy_ref` (+ `snapshot_binding`); observation = the range.
   (ADR-0014 implementation-guidance item 3 records this clarification.)
4. **`partition_source` identity + bare `filename`** — partitions name
   whose files they are by `(dataset_id, manifest_sha256)` and a bare
   filename (separators/traversal refused at two layers); the registry
   resolves the source identity through its configured roots and refuses a
   missing or digest-mismatched pin. Identity says *what*; registry
   configuration says *where*.
5. **`row_count_expected` validated** — must equal the sum of admitted
   partition rows, for all three formats.

Evidence: 259 tests green (from 234); all five new validators
mutation-checked non-vacuous; whole suite re-run under the
fail-on-parquet-read hook; dataset root, dependency files, views root and
runs root untouched.

### 1C — Gating and audit (before any real row is readable) — **COMPLETE** (2026-08-07, `aaf1dba`)

- **Objective:** the range-derived gating rule (owner decision 7), the
  ADR-0013 ceremony (unlock flag AND structured reason), and the
  write-ahead audit writer — **before** slice 1D exists to read anything.
- **Files:** `data/gating.py`, `data/audit.py`.
- **Depends on:** 1B.
- **Tests:** gating boundary read from a versioned split-policy record, not
  hardcoded; every normalized manifest carries a concrete observed range;
  RAW/CURATED/
  SNAPSHOT/LOCKBOX access without ceremony raises; partial ceremony (flag
  only / reason only) raises; OOS requires explicit request + reason and
  writes an audit event; **audit-write failure ⇒ no file handle opened, no
  data returned** (fault-injected); audit append is a single `O_APPEND`
  write + `fsync`, protected by the short advisory process lock (owner
  decision 13); concurrent-append test.
- **Acceptance:** the gate and the audit writer exist and are fully tested
  against **synthetic** gated fixtures — no real dataset access happens in
  this slice.
- **Rollback boundary:** additive; nothing yet calls it from a real loader.
- **Policy identity:** strict JSON, exact-byte SHA-256, and an immutable
  `(adr, policy_version, policy_sha256)` identity per ADR-0012 Amendment 3.
  Generated v1.1 manifests carry that exact reference. Legacy RAW/CURATED
  manifests honestly carry no reference and require an explicitly supplied,
  axis-compatible policy for classification.
- **Deliberately unbuilt:** the Phase 3 run system — `AccessContext` is a
  minimal caller-identity record (`session_id` as UUID4, OS user, PID,
  hostname, platform Git commit + dirty flag), not a RunManifest. Event time
  is recorded on each audit event; context carries no captured timestamp.

**Contracts implemented.** The strict split-policy reader consumes exact
bytes from `config/split-policies/adr-0012-v1.json`; rejects unsupported,
missing, duplicate, unknown, malformed, non-int64, inverted, overlapping and
axis-invalid content; and resolves exact identity as `(adr, policy_version,
policy_sha256)`. v1.1 manifests preserve and must exactly match all three
reference fields. Legacy RAW/CURATED manifests retain `None` and require an
explicit resolved compatible policy.

Classification is range-first, then raised by the role floor: ranges ending
beyond OOS are `FULLY_GATED`; OOS overlap is
`OOS_EXPLICIT`; earlier ranges are `NORMAL`; RAW/CURATED/SNAPSHOT/LOCKBOX can
never fall below `FULLY_GATED`, and OOS can never fall below `OOS_EXPLICIT`.
`NORMAL` rejects extra ceremony and receives an unaudited grant;
`OOS_EXPLICIT` requires a bounded structured reason; `FULLY_GATED` requires
both that reason and an unlock bound to the exact dataset identity. Every
grant is frozen, process-local, bound to the resolved registered manifest,
policy identity and UUID4 session, and issued only by the gate after any
required durable audit succeeds.

**Audit event schema implemented.** Top-level fields are `schema_version`,
`event_id`, `event_type`, `event_time_utc`, `outcome`, `denial_code`,
`caller`, `platform`, `dataset_ref`, `manifest_axis`,
`admitted_native_range`, `split_policy`, `classification`,
`classification_basis`, and `authorization`. Nested fields are:
`caller.{session_id,os_user,hostname,pid}`;
`platform.{git_commit,git_dirty}`;
`dataset_ref.{dataset_id,manifest_sha256,role}`;
`manifest_axis.{clock_domain,time_unit}`;
`admitted_native_range.{start,end}` (always concrete in V1);
`split_policy.{adr,policy_version,policy_sha256}`; and
`authorization.{unlock_present,unlock_target,reason}`, with exact identity
fields in `unlock_target` and bounded `code`/`detail` fields in `reason`.
Operational event time is UTC provenance only and never reinterprets market
keys.

**Durability and refusal evidence.** The writer serializes deterministic
UTF-8 JSON before opening, enforces the 16 KiB hard limit and mode 0600,
opens with append/close-on-exec/no-follow protections, checks owner and
regular-file status, takes a bounded exclusive flock, validates the existing
tail, performs exactly one checked low-level write, fsyncs the file, and
fsyncs the parent directory before returning. Parent fsync is deliberately
performed after every append, which also closes the concurrent first-create
and retry-after-sync-failure races. Classifiable OOS/gated ceremony refusals
are durably recorded before `AccessDeniedError`; a denial-audit failure is
surfaced with the intended refusal chained and never grants access. Failures
before trustworthy classification are not decision events.

Acceptance: 384 tests, Ruff and strict mypy green; the full suite also passed
under a fail-on-Parquet-read hook. Focused controlled mutations killed the
range-boundary, role-floor, OOS-overlap, policy-reference,
ceremony, reason, unlock-target, append, lock, one-write, short-write, fsync
and torn-tail defects, with all mutations restored. Tests use only synthetic
metadata and temporary audit paths; no dependency, dataset, view artifact or
real runs-root audit log was created. At that checkpoint, Slice 1D was
deliberately unbuilt and not started.

### 1D — Integrity loader and minimal MarketFrame — **COMPLETE; MERGED INTO `main`; CP-3 APPROVED** (2026-08-08; final reviewed head `2e474532c6cea4a19ebfd94928b13cbec998fc1f`; final bounded remediation `11e2f4f`; starting head `742a756`; third remediation `8ee33f1`; second remediation `27c5567`, `bf9285d`, `1d7dc70`; prior remediation `16474b7`, `e266455`, `41db586`; original `76f5586`)

- **Objective:** the first real Parquet read. Full-mode integrity
  verification, `MarketFrame`, the MT5 adapter, and column/partition
  selective loading — entered through a mandatory Slice 1C `AccessGrant`
  for every loadable role, including NORMAL. RAW remains metadata,
  provenance and a partition source, but is not a public `MarketFrame`
  target in V1 and is refused before partition access.
- **Files:** `data/integrity.py`, `core/frames.py`, `data/adapters/mt5.py`,
  the narrow registry source-location seam, and focused integration/
  architecture tests.
  **PyArrow and NumPy are added as dependencies in this slice** (owner
  decision 1), in their own commit immediately before the rest of 1D.
- **Depends on:** 1C.
- **Tests:** manifest-only admission (no globbing, architecture-tested);
  excluded partitions (2011–2019) unreachable through every path; size
  mismatch detected before hashing; hash mismatch → hard refusal, never a
  warning; **no partial frame ever returned on failure**; column
  dtype/schema mismatch rejected; native keys strictly increasing, no
  duplicates, unsorted input rejected; partition pruning (out-of-range
  partitions are still integrity-hashed but never PyArrow-decoded or
  materialized — separate I/O counter assertions); column selection
  (unrequested columns never materialized); `timestamp_mt5_encoded` never
  present in a loaded frame; no PyArrow/pandas/Polars/DuckDB type in any
  public signature (architecture test); real-manifest end-to-end load of
  TRAIN-period data through the temporary exact-hash-pinned target described
  below.
- **Acceptance:** real 2020–2023 Parquet bytes load and verify through the
  public loader using a temporary v1.1 TRAIN test target; RSS and timing are
  informally observed here (formal budget qualification is 1H).
- **Rollback boundary:** legacy carriers (`Bar`, `MarketContext`) remain
  untouched and unreferenced by this code.
- **Deliberately unbuilt:** `BoundedView`, context/eval semantics, unions,
  GapIndex.

**Load-bearing AccessGrant boundary (required by CP-2).** The public
market-row loading entry point must require the `AccessGrant` issued by the
Slice 1C gate. Slice 1D implements that entry point as:

```
MarketFrameLoader.load(
    grant: AccessGrant, *, columns=None, lo=None, hi=None
) -> MarketFrame
```

There must be no public row-loading entry point that accepts a bare
`DatasetRef`, `RegisteredManifest`, `DatasetIdentity`, or filesystem path.
Nor may the loader accept `already_authorized=True` or any equivalent caller
assertion/bypass flag. On every load operation, after resolving the exact
`RegisteredManifest` and split-policy identity it is about to consume but
immediately before the first partition open/read, the loader must call
`grant.require_matches(...)` against that exact registered manifest/dataset
identity and exact policy identity. No partition may be opened first. A grant
for identity A never authorizes identity B, and a grant issued under policy
identity A never authorizes a load under policy identity B.

Slice 1D architecture tests make this public boundary load-bearing: every
public row-loading API consumes
`AccessGrant`, and no public row-loading API accepts a bare `DatasetRef`,
`RegisteredManifest`, `DatasetIdentity`, or path; it must also forbid any
`already_authorized` or equivalent bypass parameter. Focused Slice 1D
acceptance tests must prove:

- a valid NORMAL `AccessGrant` permits the loading path;
- a valid, audited OOS `AccessGrant` permits the loading path;
- a valid, audited FULLY_GATED `AccessGrant` for a loadable CURATED,
  SNAPSHOT, or LOCKBOX target permits the loading path;
- a valid RAW `AccessGrant` is refused before any partition-file access;
- a mismatched-dataset grant refuses before any partition open;
- a mismatched-policy grant refuses before any partition open;
- bare `DatasetRef`, `RegisteredManifest`, `DatasetIdentity`, and path inputs
  have no supported public loading route; and
- `require_matches()` executes before the first partition open.

This mandatory input seam is the structural reason Slice 1C was implemented
before Slice 1D: authorization and durable-audit success are prerequisites of
the only public row-loading boundary, not optional checks a caller may bypass.

**Target/source authority and exact loader order.** The target manifest alone
defines admission: partition set and order, bare filename, SHA-256, admitted
first/last key and row count, target columns and adapter metadata, and byte
size only when the target itself declares one. Registry partition-source
resolution returns location only. Source-manifest metadata must never fill,
replace or override a target field; missing target byte size means no size
precheck, not permission to borrow one. A row-bounded target may admit a
narrower span than the physical source file.

The loader validates request metadata and resolves the grant's exact target,
current split-policy identity and source location without rereading manifests.
Immediately before the first stat/open/hash/decode it calls
`grant.require_matches(...)`. It then hashes every target-listed file before
any PyArrow decode. Only overlapping partitions are decoded. For each decoded
partition it first restricts physical rows to the target-admitted span,
validates the complete admitted slice (endpoints, row count, strict ordering,
schema/null policy and equal lengths), and only then applies caller bounds.
The loader neither reauthorizes nor re-audits an issued grant.

**Slice 1D real-data smoke and MT5 schema establishment.** The research-VM
smoke creates an ephemeral, test-only v1.1 TRAIN manifest under a temporary
registry root. It is derived from existing manifest metadata without reading
market rows; pins the real 2020–2023 filenames, hashes, row counts, native
ranges, CURATED parent, RAW partition-source identity and exact split policy;
and references the existing bytes in place. It is explicitly resolved by
exact `DatasetRef`, is not default-discoverable, writes nothing under the
generated-views root, creates no persistent view and touches no real audit
log. This is verification of the 1D loader, not Slice 1F generation.

Physical MT5 types are established fail-closed inside that same
`AccessGrant` → exact-match → full-integrity boundary. The observed exporter
v1 Arrow/NumPy dtype table is then recorded in the private adapter and
enforced exactly with no silent casts. A semantic ambiguity (rather than a
representation fact) stops implementation for owner review.

**Implementation record (2026-08-08).** NumPy 2.5.1 and PyArrow 25.0.0 are
locked by `uv.lock` (dependency-only commit `144dbae`). The implementation
commit is `76f5586`. `MarketFrameLoader.load(grant, *, columns, lo, hi)` is
the sole public row route. It resolves the grant's exact registry snapshot
and current policy, refuses RAW, validates all target adapter/schema metadata,
then calls `require_matches()` as its final metadata-only action before file
access. It opens only target-listed regular files without following final
symlinks, size-checks every target declaration that includes bytes, hashes
every target file before any decoder is constructed, and decodes only
overlapping partitions from those verified handles. A second full digest
after each selected partition is materialized rejects an ordinary in-place
same-inode overwrite before frame construction. The complete target-admitted
slice is validated before caller bounds; failures return no partial frame.

**CP-3 remediation record (2026-08-08).** Initial validated construction now
makes one defensive copy of each key/value array onto immutable Python
`bytes` backing. Public arrays remain plain NumPy arrays; every ndarray in an
ordinary `.base` chain refuses `setflags(write=True)`, while `select()` and
basic `slice_by_keys()` continue to share storage without copying. The MT5
adapter now restricts the projected Arrow table to the target-authoritative
admitted span, validates admitted nulls and exact Arrow types, and only then
materializes exact NumPy dtypes. Consequently, nullable `tick_volume` int64
or `spread` int32 values outside target admission cannot poison a valid load,
while admitted nulls still fail closed before caller bounds.

**Second CP-3 remediation record (2026-08-08).** A frame now retains the
immutable bytes together with private dtype/offset/length metadata rather
than retaining any ndarray descriptor that can cross the public boundary.
Every `keys` or `column()` access reconstructs a fresh plain ndarray
descriptor over those same bytes. Therefore ordinary NumPy changes to a
returned descriptor's `shape`, `dtype` or `strides` affect only that returned
object: fresh calls and original/selected/sliced/composed sibling frames keep
their validated one-dimensional shape, exact dtype, contiguous stride, row
count and values. Data remain zero-copy shared after the initial validated
construction; only the small ndarray descriptor is new per access. This is
an ordinary Python/NumPy guarantee, not protection against hostile native
memory manipulation.

**Third CP-3 remediation record (2026-08-08).** Architecture tests enumerate
the exact public names present in the `core.frames`, `data.integrity` and data
package source/runtime namespaces. Unexpected bindings therefore fail without
depending on callable `__module__`, wrapper type, annotation or name heuristics;
the approved `MarketFrame` and `MarketFrameLoader` objects remain identity-
checked. The frame/integrity/adapter computational path recognizes NumPy
`datetime64`/`timedelta64` and equivalent `M8[...]`/`m8[...]` dtype-style
conversions while still allowing display-only physical schema metadata strings.
The fourth review subsequently demonstrated that replacement of an existing
allowlisted support binding still escaped these name-set checks; the final
bounded contract below closes that ordinary drift case with canonical source
and runtime identity tables. No production Slice 1D source changed. The path
also remains independent of legacy `Bar` / `MarketContext` /
`MarketDataProvider` carriers.
Pruning instrumentation separately observes integrity hashes, PyArrow reads
and per-column NumPy materialization, proving an unselected partition is
hashed but neither decoded nor materialized.

The authorized real 2020–2023 smoke observed and now enforces this exact MT5
exporter-v1 physical table (field order is also exact):

| Physical field | Arrow type | Public NumPy representation |
|---|---|---|
| `mt5_time_raw` | `int64` | `int64` native-key index |
| `timestamp_mt5_encoded` | `timestamp[ns]` | none — display-only, never projected |
| `open`, `high`, `low`, `close` | `double` | `float64` |
| `tick_volume` | `int64` | `int64` |
| `spread` | `int32` | `int32` |
| `real_volume` | `int64` | `int64` |

No silent casting or admitted null is permitted. The ephemeral exact-ref
TRAIN target pinned and loaded the four real 2020–2023 partitions (1,414,538
rows, native keys 1,577,926,800 through 1,703,894,160) with a NORMAL grant,
zero audit events, and no persistent view. The remediation smoke run of
the complete real-smoke pytest process after the third remediation,
including interpreter/test/import overhead, took 0.46 s wall time and 458,340
KiB peak RSS. This is evidence for 1D, not the five-repetition full-dataset/
cold-cache qualification owned by 1H. The third-remediation gate collected
522 tests; pytest, Ruff and strict mypy over 54 source files were green. The
previously surviving controlled mutations were reproduced, killed and
restored exactly: public `functools.partial` bindings over the private frame
factory and an integrity delegate, the integrity wrapper's data-package
export, `np.array(..., dtype="M8[s]")`,
`np.array(..., dtype="m8[s]")`, and `np.dtype("M8[s]")`.
The dataset tree digest remained
`4637ca9d3d33eab767086804c73a0b58eca46d36fd867de990b1f0b031f745c3`;
the generated-views root and real audit log remained absent; test-only temp
paths were removed; and no persistent run artifact was created.

**CP-3 N-1 resolution — Option B approved (2026-08-08).**
`NormalizedManifest.admitted_native_range` is required. Every V1 reader,
including both legacy adapters and the v1.1 reader, resolves the concrete
observed admitted partition endpoints; normalized construction with `None`
refuses, and gating has no unknown-range classification or nullable audit
serialization. If a future adapter genuinely cannot provide a range,
relaxing this contract requires an explicit reviewed change rather than
speculative optionality today.

**Final bounded CP-3 architecture-test contract (2026-08-08).** Architecture
tests are structural regression guards against accidental and ordinary drift,
not a security sandbox against their own malicious author. For every governed
Slice 1D module/package, the guard fixes the exact approved public names and
pins every approved source binding and runtime re-export to its canonical
object. An approved support name is therefore not safe merely because its name
is allowlisted. Package-owned public wrappers are refused, and
`core/frames.py`, `data/integrity.py`, `data/adapters/*.py`,
`core/__init__.py` and `data/__init__.py` may not define module-level
`__getattr__` or `__dir__`.

The closed in-scope Slice 1D CP-3 mutation set is exactly:

1. direct public alias of the private frame constructor;
2. package re-export of the private frame constructor;
3. ordinary public wrapper delegating to the private frame constructor;
4. `functools.partial` over the private frame constructor;
5. rebinding an approved public name to forbidden frame-construction
   machinery;
6. rebinding an approved public name to an alternate integrity/loader
   wrapper;
7. alternate loader wrapper exported from the data package;
8. module-level `__getattr__` or `__dir__` serving forbidden machinery;
9. civil-time conversion on the computational path through `datetime64`,
   `timedelta64`, `M8[...]` or `m8[...]`; and
10. legacy `Bar`, `MarketContext` or `MarketDataProvider` import/reference.

The following deliberate-obfuscation attacks are explicitly out of scope for
V1 architecture tests and accepted as residual owner risk:

- editing the architecture test itself;
- editing the allowlist or expected-origin table itself;
- `exec`, `eval` or compile-generated public bindings;
- deliberate `globals()[...] = ...` import-time obfuscation;
- custom import hooks or meta-path manipulation;
- `sys.modules` substitution;
- post-import external monkeypatching;
- bytecode manipulation;
- C extensions, `ctypes` or raw native-memory manipulation; and
- any mutation whose meaningful purpose is to conceal a bypass from the
  architecture test rather than make a normal implementation/refactor work.

**Finite stopping rule.** Once the defined runtime acceptance criteria are
green, the ten in-scope architecture mutations are all killed, the quality
gate passes, and external dataset/view/audit state remains unchanged, CP-3
may be approved. Newly invented deliberate-obfuscation mutations outside the
documented threat model do not reopen Slice 1D.

**Final bounded remediation evidence.** Commit `11e2f4f` adds explicit
source-origin and runtime-object tables for every approved public binding on
the governed module/package surfaces, including private adapters. Exact names,
canonical identities, package re-exports and module-owned definitions are
checked without relying on callable `__module__`, annotations, suspicious
names or wrapper type. The same guard rejects substitution of an existing
allowlisted support/export name. A separate narrow AST rule rejects
module-level `__getattr__` and `__dir__` on all governed files.

All ten in-scope mutation classes were independently reproduced and killed.
Both dynamic hooks and all four civil-time spellings were exercised; the
canonical-binding campaign included frame, integrity and data-package
allowlisted-name substitutions. The focused Slice 1D suite, the ephemeral
1,414,538-row real TRAIN smoke, and the full 527-test/Ruff/strict-mypy gate
were green. The smoke took 0.50 s wall time and 458,540 KiB peak RSS. No
production runtime source or dependency changed. The immutable dataset stayed
at 20 files with tree digest
`4637ca9d3d33eab767086804c73a0b58eca46d36fd867de990b1f0b031f745c3`;
generated views and the real audit log remained absent, and the runs root
retained only its pre-existing empty `cache/` and `tmp/` directories.

**Final bounded CP-3 review outcome.** The independent bounded review is
COMPLETE. It approved the runtime Slice 1D contract and confirmed that the
closed ten-mutation architecture-test contract is satisfied at final reviewed
head `2e474532c6cea4a19ebfd94928b13cbec998fc1f`. One
cloud-environment/interpreter-sensitive test observation was non-blocking and
does not change the accepted runtime contract. The owner APPROVED CP-3 on
2026-08-08. CP-3 N-1 remains resolved as Option B: a concrete
`admitted_native_range` is required.

### 1E — View / context / adjacent-union semantics — **implementation COMPLETE · focused independent review COMPLETE / APPROVE · owner-approved · merged into `main`**

- **Objective:** `BoundedView` with `context_start`/`eval_start`/`eval_end`,
  backward-context authorization, and the representable `ViewUnion`.
- **Files:** `data/views.py`.
- **Depends on:** 1D.
- **Tests:** `context_start ≤ eval_start ≤ eval_end` enforced; bounded-slice
  correctness at both edges (property test); backward context into a
  strictly earlier **authorized** view is legal; context into a **later**
  view refused; context into an **unauthorized gated** view refused;
  insufficient backward context refused unless explicitly accepted through
  structured evidence (OD-3); nothing readable beyond `eval_end`;
  adjacent-view unions accepted when legally ordered and hash-pinned;
  non-adjacent unions, gated unauthorized members, and hand-written date
  ranges refused.
- **Acceptance:** every rule in ADR-0012 Amendment 2 has a passing and a
  failing test, and every owner decision OD-1…OD-6 below has both.
- **Rollback boundary:** additive on top of 1D; nothing consumes
  `ViewUnion` yet (Phase 3 does).
- **Deliberately unbuilt:** fold generation, purge computation (Phase 3
  algorithm — Phase 1 only guarantees the *contract* folds and unions will
  be built on), plus the full exclusion list in "Slice 1E scope exclusions".

#### Owner-approved Slice 1E decisions (2026-08-08)

The owner reviewed the detailed Slice 1E planning report and approved the
following six decisions, with refinements. They are binding for Slice 1E
implementation. None of them changes an accepted runtime authorization
decision; OD-1 is a narrow, approved evolution of the CP-3 architecture-test
route table, and OD-2 refines an earlier *planning sketch* in this document,
not ADR-0003.

**OD-1 — composition route, and the CP-3 route-table refinement.**

`MarketFrameLoader.load(AccessGrant, ...)` remains the sole public row
loader / decoder. Slice 1E may add exactly one explicitly approved public
composition entry point:

```
open_bounded_view(
    *,
    loader: MarketFrameLoader,
    grants: Sequence[AccessGrant],
    context_start: NativeTimeKey,
    eval_start: NativeTimeKey,
    eval_end: NativeTimeKey,
    ...
) -> BoundedView
```

- Exact additional optional Slice 1E parameters may be derived during
  implementation from the already approved contract, but `loader` and
  `grants` are fixed inputs.
- `loader: MarketFrameLoader` is the explicit, already-configured
  infrastructure dependency holding the `DatasetRegistry` and resolved
  `SplitPolicy`. It is **not authorization**: possession of a loader alone
  authorizes nothing.
- `grants: Sequence[AccessGrant]` supplies the exact authorization
  authorities and is **non-optional**. Every contributing dataset/view
  requires its own exact grant.
- It may orchestrate multiple existing `loader.load(...)` calls, one for each
  contributing authorized view, but every call must receive the corresponding
  exact `AccessGrant`. Slice 1D `grant.require_matches(...)` semantics are
  unchanged.
- It is **not** an independent decoder, performs no decoding or file I/O
  itself, and does not reauthorize grants.
- It must not rediscover or build a registry, resolve configuration from the
  environment or working directory, reconstruct a split policy, access paths
  directly, or create ambient/global loader state.
- It must **not** accept a registry, policy, bare `DatasetRef`,
  `DatasetIdentity`, `RegisteredManifest`, manifest object, path or root, or
  any caller-asserted authorization — including `already_authorized`,
  `trusted`, `unsafe` or any equivalent bypass flag.
- Loaded-data accessors on `BoundedView` are **not** additional row loaders
  when they expose only data already composed and bounded during
  construction.

The Slice 1E architecture tests must distinguish and **explicitly enumerate
and constrain**:

1. the sole public decoder / row loader, `MarketFrameLoader.load`; and
2. the one approved composition route, `open_bounded_view`.

`open_bounded_view` may reference and call `MarketFrameLoader.load`, but it
must satisfy every boundary above. No other new public data-package callable
may gain row-loading or composition capability without another owner-reviewed
architecture change. This is the narrow, approved evolution of the CP-3
architecture-test contract: the route table gains one reviewed entry; the
sole-decoder and Slice 1D authorization rules are unchanged. The
corresponding ADR-0013 implementation guidance records the same boundary.

**OD-2 — no cross-view `BoundedView.frame()`.**

The normative/public `BoundedView.frame()` concept is **removed**. The
uniform public model is:

- `segments` — an ordered immutable sequence of bounded `MarketFrame`
  segments, each retaining its **own exact `DatasetRef`**;
- `eval_frame` — the bounded frame covering only the evaluation view and
  `[eval_start, eval_end]`.

Consequently: no multi-identity `MarketFrame` will be created; no
concatenated `MarketFrame` may hide multiple dataset identities; the caller
must not receive an unbounded underlying frame. The contract is **uniform**
for single-view and cross-view context — there is deliberately no
convenience API that works only when exactly one segment exists. This
refines the earlier planning sketch in §5 (which showed a
`frame()` accessor); it is not a change to ADR-0003.

**OD-3 — insufficient-context evidence.**

Insufficient backward context **refuses by default**. There is no bare
`allow_short_context: bool` bypass. Explicit acceptance of insufficient
context requires a structured `ContextShortfall`, carried by a structured
`ContextResolution`. The evidence must include at least:

1. the requested context boundary;
2. the resolved context boundary;
3. a structured reason code;
4. bounded human-readable detail;
5. an explicit status indicating short context was accepted.

The resulting `BoundedView` **permanently retains** this structured
evidence.

Slice 1E performs **no durable write** and creates **no new audit event
type**. Phase 3 will serialize the structured evidence into the RunManifest.
In Phase 1, "recorded" therefore means **retained immutably in the returned
semantic object** — nothing more. That limitation is stated here honestly
rather than implied away, and it is the reason ADR-0013 and ADR-0014 are
**not** amended to add a new audit ceremony for this path.

**OD-4 — LOCKBOX treatment: evaluation and fitting-union semantics split.**

| Surface | Legal Tier-1 roles |
|---|---|
| `BoundedView` (bounded **evaluation**) | TRAIN · VALIDATION · OOS · **LOCKBOX** |
| `ViewUnion` (V1 **fitting/promotion**) | TRAIN · VALIDATION · OOS — **LOCKBOX forbidden** |

A bounded evaluation view may use those Tier-1 evaluation roles with exact
valid authorization for **every** contributing member. LOCKBOX evaluation
remains fully ceremony-gated and audited through the existing 1C/1D
mechanisms — OD-4 grants it no new or easier route.

RAW, CURATED and SNAPSHOT are **not** legal `BoundedView` or `ViewUnion`
research members.

The distinction is intentional: LOCKBOX may eventually be *evaluated* under
full ceremony, but LOCKBOX must not become *fitting/promotion input* in V1.
ADR-0012 Amendment 2 never enumerated LOCKBOX as a fitting/promotion member
and already requires authorization for gated members such as OOS. Slice 1E
makes the V1 implementation boundary explicit: `ViewUnion` supports TRAIN,
VALIDATION and authorized OOS; LOCKBOX is excluded from fitting/promotion
while remaining available for fully ceremonial bounded evaluation.

**OD-5 — consecutive policy-role context chain; observed gaps remain legal.**

Backward-context members must form a **consecutive Tier-1 policy-role
chain ending at the evaluation view**.

| Chain | Outcome |
|---|---|
| TRAIN → VALIDATION | legal |
| VALIDATION → OOS | legal with OOS authorization |
| TRAIN → VALIDATION → OOS | legal with required authorization |
| TRAIN → OOS, omitting VALIDATION | **refused** |
| reordered or duplicated roles | **refused** |

"Consecutive" means **consecutive in the resolved split-policy role order**.
It does **not** mean any of:

- `next.start == previous.end + 1`;
- `next.start == previous.end + timeframe`;
- physical/native keys are arithmetically contiguous;
- bars exist at every expected interval.

**Observed-key gaps are legal facts and do not break authorization
adjacency.** The synthetic fixture where TRAIN ends at 202 and VALIDATION
begins at 1100 must remain a valid adjacency test. The rule must be derived
from exact split-policy role order and identity — never from hardcoded V1
dates or naive key arithmetic.

**OD-6 — empty evaluation range.**

- Boundary ordering remains `context_start <= eval_start <= eval_end`.
- Requested boundaries need **not** equal observed keys.
- A boundary inside an arithmetic data gap is legal.
- Zero-length context is legal.
- A single observed evaluation row is legal.
- If `[eval_start, eval_end]` contains **zero observed rows**, construction
  refuses with `ViewBoundsError`. Do **not** introduce a separate
  `EmptyEvaluationRangeError`.
- Context segments may be empty where semantically valid; the **evaluation
  frame may not be empty**.

#### Final minimal Slice 1E public direction

The planned public Slice 1E surface is **intentionally minimal and subject to
implementation verification**. Expected public concepts:

`BoundedView` · `BoundedSegment` · `ContextResolution` ·
`ContextShortfall` · `ViewUnion` · `ViewUnionMember` · `open_bounded_view`

Do **not** introduce or approve additional public enums/value objects unless
implementation demonstrates a concrete need **and** the owner reviews them.
In particular:

- `ViewAuthorization` does **not** become public by default;
- the explicit loader and grants are **consumed during composition** but are
  not retained or exposed;
- `ViewUnion` contains no `MarketFrame`, performs no load, and exposes no
  union hash/fingerprint in Slice 1E — union identity/fingerprinting remains
  Phase 3;
- `BoundedView` retains or exposes no `MarketFrameLoader`, `AccessGrant`,
  `DatasetRegistry`, `SplitPolicy` object, manifest object, filesystem path
  or root.

#### Slice 1E scope exclusions

Explicitly **out** of Slice 1E: persistent view-manifest generation ·
snapshot binding · real Tier-1 artifact creation · Slice 1F ceremony/tooling
· fold generation · purge computation · `OutcomeSpec` ·
`TrainingTargetFrame` · `WindowSpec` / features · models · decision policies
· simulation · `GapIndex` · performance qualification · RunManifest
implementation · durable recording · new audit event types · union
identity/fingerprint · legacy-carrier retirement · Phase 2 work.

If implementing 1E appears to require any of these, that is a scope signal —
stop and raise it, per §3.

#### Slice 1E testing and I/O expectations

- Use **synthetic manifests, temporary roots and temporary Parquet only**.
- **Reuse/refactor existing Slice 1D fixtures** rather than duplicating
  them.
- Include: multi-role TRAIN / VALIDATION / OOS / LOCKBOX fixtures;
  deliberate **policy-adjacent but key-distant** ranges (per OD-5); at least
  one **intra-view key gap**; and at least one **row-bounded target whose
  admitted range is narrower than the physical file**.
- **No real-data Slice 1E smoke.**
- **No generated production Tier-1 views.**
- The immutable dataset root stays unchanged; the generated-views root
  remains absent; the real audit log remains absent; the runs root receives
  no persistent artifact.

#### Slice 1E implementation evidence (2026-08-08)

Implementation is **COMPLETE and merged into `main`** at reviewed
implementation head `84bee8187a72aa361f3b9b986bf84b4d00a63818`, based exactly
on prior `main` at `02ad4465b565445478506f7d5854113b185ea16f`. Commits
`423b39f`, `8b09cdc`, `58d5fad`, and `28764c8` add the
narrow error hierarchy, reusable multi-role fixtures, the approved seven
public contracts, the functional matrix, and the bounded architecture guards.
No approved OD-1…OD-6 contract required refinement or exception.

The focused Slice 1E/loader/frame/architecture suite passed **225 tests**.
The full gate passed **594 tests**, Ruff, and strict mypy; `git diff --check`
also passed. A finite seven-case Slice 1E architecture mutation/regression set
proved refusal of a missing loader, optional grants, a bare path input, a
second decoder, retained loading authority, direct PyArrow use, and a fallback
role branch. This was the bounded CP-3-style stopping rule, not an open-ended
adversarial campaign.

All Slice 1E tests used synthetic manifests, temporary Parquet roots, and
test-only in-memory audit facilities. No real research data was contacted and
no real-data smoke ran. The immutable dataset root remained exactly 20 files
with unchanged tree digest
`4637ca9d3d33eab767086804c73a0b58eca46d36fd867de990b1f0b031f745c3`;
the generated-views root and real audit log remained absent; and the runs root
retained only its pre-existing `cache/` and `tmp/` directories. Slice 1F Stage
A is **COMPLETE / REVIEW PENDING**; Stage B and Phase 2 remain **NOT_STARTED**.

#### Focused independent Slice 1E review outcome (2026-08-08)

The focused bounded independent review is **COMPLETE** with verdict
**APPROVE**, and owner approval is COMPLETE. It found no blocker and required
no change. Its two observations are non-blocking notes only: private internal
coupling from `data/views.py` to `MarketFrameLoader._policy`, and one
tautological `require_matches()` call in `ViewUnionMember`. Neither schedules
immediate remediation and neither blocks Slice 1F.

The exact next action is: **Run a focused, bounded, independent read-only
Slice 1F Stage-A review before Stage B.**

### 1F — Snapshot binding and Tier-1 view generation — **planning/reconciliation COMPLETE (owner decisions OD-F1…OD-F6 approved 2026-08-09) · Stage A COMPLETE, INDEPENDENTLY REVIEWED (APPROVE WITH NON-BLOCKING NOTES), OWNER APPROVED 2026-08-09 and merged into `main` at `79bac876f84bcc915532b9bad246e74200e67041` · Stage-B production preparation COMPLETE (D-1…D-6 and refinements R-1…R-3 owner-approved 2026-08-09) · Stage-B operational script COMPLETE, INDEPENDENTLY REVIEWED (APPROVE WITH NON-BLOCKING NOTES), OWNER APPROVED and merged into `main` at `f8b6168511fa2544479c0ad96a16c50cf0ab7890` · **Stage-B production generation COMPLETE (2026-08-09, fresh mode, no OD-F6 recovery)** · **CP-4 independent review COMPLETE (APPROVE WITH NON-BLOCKING NOTES) and OWNER APPROVED 2026-08-10 · SLICE 1F COMPLETE**

- **Objective:** the audited tooling that produces the SNAPSHOT anchor and
  the TRAIN / VALIDATION / OOS / LOCKBOX v1.1 manifests, under the ceremony
  built in 1C.
- **Files:** an internal generation library `data/views_generation.py` plus a
  small atomic manifest writer; the thin Stage-B operational script
  `scripts/generate_views.py` (OD-F1). No CLI framework.
- **Depends on:** 1C, 1D, 1E.
- **Acceptance:** five hash-pinned manifests exist under the views root;
  default discovery returns TRAIN + VALIDATION only; OOS/SNAPSHOT/LOCKBOX
  require the full ceremony; every hash is recorded in PROJECT_STATE.md.
- **Rollback boundary:** generated manifests are new artifacts — deletable
  and regenerable from the (untouched) curated parent. This is the first
  slice that performs a real ceremony-gated access; run it deliberately,
  not incidentally.
- **Deliberately unbuilt:** any research use of LOCKBOX or OOS data; fold
  generation; purge; GapIndex; performance qualification (1H); any
  database/index or generalized artifact framework.

#### Owner-approved Slice 1F decisions (2026-08-09)

Binding for Slice 1F implementation. All five are implementation choices
inside already-accepted architecture — **no ADR changed**, because research
policy is already external (ADR-0012), partition metadata already describes
admitted rather than physical rows (ADR-0014 implementation guidance 3),
physical location is already not identity (ADR-0004), and multiple exact
split-policy identities are already supported (ADR-0012 Amendment 3).

**OD-F1 — tooling.** A small internal generation library, plus the thin
Stage-B operational script `scripts/generate_views.py`. No CLI framework. All inputs explicit —
registry, resolved `SplitPolicy`, `MarketFrameLoader`, `AccessGrant`, views
root, audit writer, bundle timestamp. No cwd, environment, or global
configuration. **Generation is not exposed through the package-level public
API** (`data/__init__.py` is unchanged); Stage B imports the module directly.

**OD-F2 — ceremony and the one fresh-generation read.** One fresh production
generation performs exactly one gated source-data operation: exact CURATED
authorization → one `AccessGrant` → one logical keys-only read → snapshot
bound determination. The remaining view manifests are metadata construction.
There are **no separate OOS/LOCKBOX generation ceremonies**, because no
additional row read is required.

The read is one authorized keys-only load of the **complete admitted CURATED
source** through the existing sole decoder:

```
loader.load(grant, columns=())      # projects only the native key column
```

with **zero** OHLC/volume/spread value-column materialization, **no** separate
Parquet key scanner, **no** direct Parquet I/O inside view generation, and
**no** second loader route. From the resulting native-key array the generator
derives exactly: per-role first/last admitted key, per-role admitted row
count, per-physical-partition admitted first/last/count, the snapshot bound
key, the lockbox bound, each view-level `admitted_native_range`, and each
`row_count_expected`.

**OD-F3 — one bundle timestamp and deterministic SNAPSHOT-anchor resume.**
One explicit `generated_at_utc` for the entire five-manifest bundle; never a
per-manifest timestamp. Serialization is canonical
(`sort_keys=True, indent=2, ensure_ascii=False`, trailing newline) so bytes
are stable.

SNAPSHOT is the resume anchor. Where a valid SNAPSHOT from a partial
generation already exists, the generator reuses its exact hash, timestamp,
generation provenance and snapshot-binding evidence, and reconstructs missing
children deterministically. Per file: byte-identical existing artifact is
accepted as a no-op; a differing existing artifact is refused. There is **no
force overwrite**.

> **Superseded by OD-F6 (2026-08-09).** OD-F3 originally also required resume
> to perform no gated source reread and to emit no audit records. That
> requirement is **withdrawn** — it is unsatisfiable together with
> file-layout independence under the current v1.1 schema. Resume now performs
> one new audited keys-only source scan. See OD-F6 below; everything else in
> OD-F3 (one bundle timestamp, canonical bytes, SNAPSHOT as anchor,
> deterministic child reconstruction, no-op/refuse/no-force) stands unchanged.

**OD-F4 — audit model.** A fresh successful generation produces exactly two
logical audit records: the existing `dataset_access_decision`, and one new
`snapshot_binding` event. There is **no third publication event**. A resume
adds one new `dataset_access_decision` (its source scan is an ordinary gated
access) and **no** new `snapshot_binding`, because the existing SNAPSHOT is
reused rather than rebound — see OD-F6. Both use the existing append-only
`JsonlAuditWriter`; no second audit system is introduced.

The circularity-free order — the `snapshot_binding` event must durably
precede SNAPSHOT publication *and* name the exact SNAPSHOT manifest hash — is:

1. allocate the `snapshot_binding` event id;
2. obtain bound facts from the authorized keys-only scan;
3. build the exact SNAPSHOT manifest bytes **in memory**, using that event id
   and the bundle timestamp;
4. hash those exact bytes;
5. append and fsync the `snapshot_binding` event **containing that exact
   SNAPSHOT hash**;
6. publish those exact bytes atomically;
7. derive the four children from that exact SNAPSHOT identity.

Hashing does not require publication, so there is no circular dependency.
Required invariant, asserted by test:

```
sha256(published_snapshot_bytes) == snapshot_binding.snapshot_manifest_sha256
```

**OD-F5 — generation dtype declaration.** `ColumnV11.dtype` is required, but
the legacy CURATED manifest honestly carries no dtype strings, so the
generator needs a declaration source. Use a small internal
manifest/generation-layer mapping (e.g. `data/manifests/adapter_columns.py`,
or the smallest equivalent location). It is **not** a second runtime
enforcement authority: the existing adapter remains the sole decoder and type
enforcement authority. A conformance test must prove the generation
declaration exactly matches the adapter's already-verified dtype contract, so
drift fails the gate. Do **not** add a public adapter accessor, do **not**
weaken the approved row-machinery architecture guard, and do **not** expose
the mapping as general public API.

**OD-F6 — partial-generation recovery may perform a new audited keys-only
read (2026-08-09).** A pre-implementation architecture check found a genuine
information-loss contradiction in the original zero-read resume requirement,
and it is resolved here.

*The contradiction.* A SNAPSHOT partition entry retains only three aggregate
admitted facts per physical file — `first_key`, `last_key` and `rows`. When
one physical file crosses a Tier-1 boundary and legal native-key gaps exist,
those aggregates cannot reconstruct the per-role admitted counts and
endpoints. Two different key sets produce an identical SNAPSHOT entry:

| Observed keys in one file | SNAPSHOT entry `(first, last, rows)` | TRAIN (≤100) | VALIDATION (>100) |
|---|---|---|---|
| `10, 20, 110, 190` | `(10, 190, 4)` | first 10, last 20, rows 2 | first 110, last 190, rows 2 |
| `10, 110, 120, 190` | `(10, 190, 4)` | first 10, last 10, rows 1 | first 110, last 190, rows 3 |

So "resume with zero source reread" and "file-layout independence" cannot
both hold under the current schema. Since file-layout independence is
approved and load-bearing, the zero-read requirement is **withdrawn** — it
was an optimization for a rare crash-recovery path, not a research-integrity
property.

*Binding rules.* File-layout independence remains fully approved; a physical
source file may cross any number of Tier-1 boundaries; arbitrary-straddle
support is not weakened. The v1.1 schema is **not** changed to optimize crash
recovery, and Tier-1 recovery segmentation is **not** encoded into SNAPSHOT
by repeating physical partition entries as a recovery database.

*Partial-generation resume.* Where a valid SNAPSHOT exists but one or more
children are absent:

1. validate the SNAPSHOT anchor belongs to the exact expected dataset family
   and policy (full field list under "Resume identity and provenance
   validation" below);
2. authorize the exact CURATED source again through the normal existing
   ceremony;
3. perform one new complete keys-only load;
4. reconstruct the missing children from the reread native keys, source
   metadata, the supplied exact `SplitPolicy`, and the existing SNAPSHOT
   identity/binding/provenance;
5. existing byte-identical child → no-op;
6. missing child → publish;
7. differing child → refuse;
8. no force overwrite.

The resume source read is an ordinary gated data access and therefore
produces the normal existing `dataset_access_decision` audit evidence. A
second `snapshot_binding` event must **not** be written when the existing
SNAPSHOT is reused rather than rebound or replaced. A successful resume
therefore normally adds **one** new `dataset_access_decision` and **zero**
new `snapshot_binding` events.

*Generator-provenance compatibility.* Resume must not claim old generator
provenance for output produced by materially different generator code.
Before resuming, verify the existing SNAPSHOT's generator name, generator
version and `platform_git_commit` against the current generation operation.
**V1 rule: if the current generation code identity does not exactly match the
SNAPSHOT's `platform_git_commit`, refuse resume** and require a deliberate
fresh generation with new identities rather than emitting new children under
stale provenance. No generalized code-compatibility framework is built.

*What remains deterministic.* Given the same source bytes, source identities,
exact policy, observed keys, SNAPSHOT identity/binding, bundle timestamp and
generator provenance, a reconstructed missing child is still byte-identical.
The second keys-only scan does not change identity; it recovers information
the SNAPSHOT never retained.

*Rejected V1 recovery alternatives.* Neither is an architecture defect; both
are simply rejected implementation choices for V1.

- **Alternative A — repeat one physical file as several artificial SNAPSHOT
  partition contributions** purely to retain Tier-1 recovery facts.
  Technically possible and may be revisited if a concrete need appears, but
  it adds semantic complexity to SNAPSHOT for a rare crash-recovery
  optimization. Not justified for V1.
- **Alternative B — amend v1.1 to store explicit Tier-1 binding/recovery
  facts.** Unnecessary schema expansion; the underlying keys can be safely
  reread keys-only under the existing audited gate.

#### Complete column preservation

Generated views preserve the complete ordered declared source-column
contract. Column names, order and purposes are copied verbatim from the
source manifest; dtype strings come from the OD-F5 declaration. There is **no
OHLC-specific whitelist**: `tick_volume`, `spread` and `real_volume` are
preserved on exactly the same footing as `open`/`high`/`low`/`close`, and
`timestamp_mt5_encoded` remains declared `display_only` and never projected.
A future adapter-supported column flows through without changing
view-generation logic — adding it is an adapter/schema change, not a
generator change.

#### Multi-dataset generality and file-layout independence

Slice 1F performs the first real production generation for the current
XAUUSD dataset family, but **the generation library is not an XAUUSD
generator**. Production code must not encode: `XAUUSD`/`XAUUSD.s`, any other
asset, `M1`, fixed partition years, fixed filenames, fixed dataset ids,
current manifest hashes, a fixed number of partitions, current observed
ranges, or the V1 policy boundary integers.

Derived from the supplied source manifest/registry metadata: symbol,
timeframe, adapter id, column inventory, partition inventory, filenames,
physical file digests, source and parent identities, observed keys and
counts. Derived from the supplied resolved `SplitPolicy`: all Tier-1
intended ranges and the snapshot cap (the last Tier-1 role's upper bound).
No UTC or civil-time derivation anywhere. The current V1 XAUUSD policy
integers remain permitted only in the V1 policy-conformance test and in
Stage-B configuration/provenance.

**File-layout independence.** Generation must **not** depend on the source
dataset being partition-aligned to Tier-1 boundaries. A compatible source may
contain physical files crossing TRAIN→VALIDATION, VALIDATION→OOS,
OOS→LOCKBOX, the snapshot cap, or more than one boundary at once. For
example, a future EURUSD source might hold one file covering 2022–2024 and
another covering 2025–2026; that layout must not require editing the
generator.

This works because the accepted v1.1 contract already represents admitted row
subsets of physical files: per-view `first_key`, `last_key` and `rows`
describe the **admitted** span while the whole physical file SHA-256 is
retained, and the Slice 1D loader restricts each decoded partition to its
target-admitted span before validation. **Physical partition layout is
storage detail, not research-policy structure.** A physical file may
therefore appear in more than one generated view with different admitted
spans. A straddling physical file is never refused merely for its layout;
its admitted subset is derived from observed native keys intersected with the
policy range.

The generator attributes observed keys to partitions by intersecting the
scanned key array with each source partition's admitted span and each role's
policy range; source partitions ordered and non-overlapping (already a 1B
invariant) make that attribution exact without further I/O. A source
partition contributing no keys to a role is simply omitted from that role's
`admitted_partitions`.

**Required Stage-A behavioral proof:** at least one complete synthetic
second-dataset example with a different symbol, different dataset ids,
different filenames, a different physical partition layout including at least
one physical partition crossing a **non-snapshot** Tier-1 boundary, and a
compatible but independently supplied split policy. All five views must
generate correctly with no production-code change.

#### Resume identity and provenance validation

Before reusing an existing SNAPSHOT anchor, the generator validates at least:
exact expected SNAPSHOT view id; `role == SNAPSHOT`; exact supplied
split-policy identity; `bound_key <= policy cap`; exact expected CURATED
parent identity; exact expected partition-source identity; adapter id;
symbol; timeframe; clock domain; time unit; calendar-time policy; and the
complete ordered column contract. **Generation for one dataset family must
never resume from another dataset family's SNAPSHOT.**

Resume also requires **generator-code identity agreement** (OD-F6): the
SNAPSHOT's generator name, generator version and `platform_git_commit` are
checked against the current generation operation, and a `platform_git_commit`
mismatch **refuses resume** rather than emitting children under stale
provenance.

Resume reuses the generation provenance embedded in the existing SNAPSHOT:
`generated_at_utc`, generator name, generator version,
`platform_git_commit`, the snapshot-binding evidence, and the SNAPSHOT
manifest hash. The current Git commit must **not** be substituted during
resume where doing so would change deterministic child bytes. The goal is
that a missing child is reconstructed byte-identically from the reread
observed keys, source metadata, the supplied policy, the validated SNAPSHOT
anchor, the reused binding, the reused bundle timestamp and the reused
generator provenance.

Resume obtains the per-role admitted facts from **one new audited keys-only
source scan** (OD-F6), because a SNAPSHOT partition entry retains only
aggregate `first_key`/`last_key`/`rows` per physical file and cannot yield
per-role counts and endpoints when a file straddles a Tier-1 boundary. That
scan is an ordinary gated access producing one `dataset_access_decision`; it
writes no second `snapshot_binding`.

Note that `NormalizedManifest` deliberately does not retain
`generated_at_utc` or `snapshot_binding`, so resume reads the existing
SNAPSHOT as raw v1.1 wire JSON (and additionally normalizes it through the
ordinary reader to confirm full validity) rather than obtaining those fields
from the normalized model.

#### Dataset-history subset behavior and the non-empty-view precondition

A dataset need not begin exactly at a policy range's first key: the observed
admitted range may be a strict subset of the intended policy range. This is
already accepted behavior — `admitted_native_range` records observed
endpoints, and the current XAUUSD TRAIN view already exercises it (policy
start `1577836800`, observed start `1577926800`).

**Honest V1 compatibility precondition.** The accepted v1.1 schema requires
`admitted_partitions` to be non-empty, and `NormalizedManifest` refuses a
manifest that admits no partitions and requires `admitted_native_range` to
equal the observed endpoints. A generated view with **zero observed rows for
an entire Tier-1 role is therefore not representable in V1** — there are no
observed endpoints to record, and a fabricated degenerate span would put a
falsehood in a field that documents observations. This is recorded as an
onboarding precondition, not worked around: a compatible dataset family must
have at least one observed row in every Tier-1 role it generates. The
repository resolves this behavior decisively, so no owner decision is open;
relaxing it later would be an explicit reviewed schema change.

#### Current XAUUSD production facts vs. the general generator contract

These are deliberately separated. The left column is Stage-B input for the
current dataset and belongs in the Stage-B script, PROJECT_STATE.md and the
V1 conformance test. It must never become generator runtime constants.

| Current XAUUSD production fact (Stage B) | Value |
|---|---|
| CURATED source identity | `XAUUSD_S_M1_CURATED_V1` |
| Partition-source identity | `XAUUSD_S_M1_MT5_V1` (the physical Parquet home) |
| Policy identity | ADR-0012 / `v1` / exact policy SHA-256 |
| Snapshot cap | `1785542340` (the LOCKBOX upper bound) |
| Current layout | 7 admitted partitions, 2,333,614 rows |
| Current alignment | TRAIN/VALIDATION/OOS happen to be partition-aligned; only the 2026 file crosses the cap |

**Current alignment is an observation about today's dataset, not a generator
assumption.** The generator derives every boundary from observed keys and
policy ranges and must behave identically if a future source is cut
differently.

| General generator contract (any compatible family) |
|---|
| A source manifest with a concrete admitted range, ordered non-overlapping partitions, and a declared column inventory whose value columns the adapter recognizes |
| A supported adapter |
| A resolved `SplitPolicy` on the same clock domain and time unit |
| At least one observed row per generated Tier-1 role |
| Views root supplied explicitly |

Given those, the generator produces five valid v1.1 manifests. Anything
outside them is an explicit refusal, never a silent assumption.

#### Stage A / Stage B separation

**Stage A — implementation validation.** Synthetic and temporary fixtures
only. No real ceremony, no real view manifests, no real audit log, no real
Parquet market values. Stage A ends at a **focused, bounded, independent
Stage-A review** before Stage B begins.

**Stage-A validation profile (owner-approved 2026-08-09).** A
pre-implementation check found a real operational conflict:
`scripts/gate.sh` runs `uv run pytest -q` **unfiltered**, and the
pre-existing `tests/test_integrity_real.py` is skip-guarded only on dataset
*absence* — so on the research VM, where the dataset is present, it executes
and loads real TRAIN Parquet rows with all value columns. "Stage A must not
read real Parquet market data" and "Stage A must run unfiltered
`./scripts/gate.sh`" therefore cannot both hold on this machine.

Stage A resolves this by **intentionally excluding the pre-existing real-data
smoke** `tests/test_integrity_real.py` (or its exact real-data node). Do
**not** modify `scripts/gate.sh` for this slice unless repository convention
genuinely requires a reusable no-real-data profile. Instead run the
equivalent validation explicitly:

1. the pytest suite **excluding** the pre-existing real-data smoke;
2. focused Slice 1F tests;
3. architecture tests;
4. Ruff;
5. strict mypy;
6. `git diff --check`.

The Stage-A implementation report must state plainly:

> Stage-A validation is green under the owner-approved no-real-data profile.
> The pre-existing real TRAIN integration smoke was intentionally excluded
> because executing it would violate the Stage-A no-real-data boundary.

It must **not** be described as an unfiltered full gate. The real/integration
validation is run deliberately before Stage B / CP-4 under the
production-generation ceremony plan, where real-data contact is in scope.
This exclusion is scoped to Slice 1F Stage A only; the Phase 1 completion
checklist (§11) still expects the ordinary full gate at 1H closeout.

**Stage B — deliberate owner-authorized production generation.** Requires
explicit owner confirmation immediately before the ceremony, after pre-state
is recorded and exact inputs are displayed. Ordinary `pytest` must never
execute Stage B.

**Stage-A implementation evidence (2026-08-09).** Stage A is **COMPLETE,
independently reviewed, OWNER APPROVED (2026-08-09) and merged into `main`**
at the exact reviewed head `79bac876f84bcc915532b9bad246e74200e67041`
(fast-forward only from `d3de32c88677d9280136cb56cd2aa3d24ee7fc1b`; no merge
commit, and no implementation commit exists after the reviewed head). The
internal implementation and
synthetic tests cover CP-2 N-2 policy conformance, OD-F5 dtype conformance,
fault-injected immutable publication, one complete zero-value-column
keys-only scan, arbitrary partition straddles/gaps, complete ordered columns,
multi-dataset generation, non-empty-role refusal, strict deterministic
five-manifest lineage, circularity-free binding audit publication, OD-F6
reauthorized recovery, discovery/gating, and normal-loader post-cap
inaccessibility. No Slice 1B–1E runtime contract or v1.1 schema changed.

The focused foundation/generation set passed 56 tests; the relevant
manifest/registry/gating/loader/view set passed 279; architecture passed 76;
and the complete owner-approved Stage-A suite passed 654 with
`tests/test_integrity_real.py` excluded. Ruff, strict mypy and
`git diff --check` passed. **Stage-A validation is green under the
owner-approved no-real-data profile. The pre-existing real TRAIN integration
smoke was intentionally excluded because executing it would violate the
Stage-A no-real-data boundary.** This is not an unfiltered full gate. Only
synthetic/temp Parquet and in-memory audit facilities were used; the real data
tree's metadata-only before/after fingerprint was unchanged, the real views
root and audit log remain absent, and no persistent run artifact was created.

**Stage-A independent review outcome (2026-08-09).** The focused, bounded,
independent, read-only Slice 1F Stage-A review is **COMPLETE** with verdict
**APPROVE WITH NON-BLOCKING NOTES**, at reviewed head
`79bac876f84bcc915532b9bad246e74200e67041` over baseline
`d3de32c88677d9280136cb56cd2aa3d24ee7fc1b`. It found **no correctness,
research-integrity, durability, authorization, identity, reproducibility or
architecture blocker**, and the **owner APPROVED Stage A on 2026-08-09**. The
review independently confirmed policy conformance with no civil-time
derivation and no policy integers in production; the private, complete OD-F5
dtype declaration genuinely bound to adapter enforcement; complete ordered
column preservation with no OHLC whitelist; exactly one structurally pinned
`MarketFrameLoader.load(grant, columns=())` as the sole decoder route with no
`AccessGrant` bypass; file-layout independence with stable whole-file digests
across differing admitted spans; a materially different second synthetic
dataset through the same code path; exact RAW → CURATED → SNAPSHOT → Tier-1
lineage and correct discovery defaults; `os.link` atomic no-replace
publication with fault-injected durability and no force path; the
circularity-free binding order with `sha256(published) ==
snapshot_binding.snapshot_manifest_sha256`; byte determinism; OD-F6 resume
preflight before any gated reread with one new `dataset_access_decision` and
zero new `snapshot_binding`; and end-to-end post-cap inaccessibility through
registry, gate and loader. The reviewer reproduced the approved no-real-data
profile (654 / 56 / 76 tests, Ruff, strict mypy, `git diff --check`) and read
no real Parquet.

**Four non-blocking review notes.** Recorded as review notes, **not blockers**.
No production source was changed to address them in the approval task, and none
schedules immediate remediation.

1. **`snapshot_binding.event_id` is a deterministic logical-binding identity,
   not a per-append key.** Determinism is required because `audit_event_id`
   sits inside the hashed SNAPSHOT bytes. A retry after a durable binding-audit
   append but before SNAPSHOT publication can append another record with the
   same logical `event_id`; repository contracts do not currently require
   per-append uniqueness, and each record still carries its own unique
   `source_access_event_id`. Stage-B operational notes may mention that
   duplicate logical ids are expected and benign after such a retry; a separate
   append-attempt identifier is future refinement only.
2. **OD-F6 resume writes a new `dataset_access_decision`, but children carry no
   forward reference to that recovery access event.** Existing lineage and
   binding evidence remain sufficient for V1; richer run-level provenance
   belongs to later RunManifest/run infrastructure.
3. **Dataset fingerprint methodology is not canonically documented.** The older
   recorded tree digest and the Stage-A metadata-only fingerprint are
   **different measures and must not be compared as though they were the
   same**. If a canonical fingerprint is recorded in future, record its
   producing command/method too.
4. **`data/atomic_write.py` alone has weaker parent-symlink guarantees than its
   current generation callers**, but every current production call path
   performs the stronger root/path validation before using it. **No current
   Stage-A violation.**

**Status:** Stage A is **COMPLETE / INDEPENDENTLY REVIEWED / OWNER APPROVED /
IN `main`**. The Stage-B operational script is **COMPLETE / REVIEW PENDING**.
Stage-B production generation remains **NOT_STARTED**: no Stage-B production
generation has occurred, **no real generated view manifest exists yet**, the
real views root and real audit log remain absent, and no real ceremony has been
performed. CP-4 is **NOT_READY** and still occurs after Slice 1F. Slice 1G and
Phase 2 are NOT_STARTED.

> *Historical context, accurate as of Stage-A completion on 2026-08-09.* Stage B
> has since been executed and CP-4 was owner-approved on 2026-08-10 — see the
> Stage-B production outcome and CP-4 record below.

#### Owner-approved Stage-B production decisions D-1…D-6 (2026-08-09)

Stage-B production **preparation** is COMPLETE and owner-approved. These are
operational production choices inside the already-accepted OD-F1…OD-F6
architecture — **OD-F1…OD-F6 are unchanged and no ADR changes**. Stage-B
production generation itself remains **NOT_STARTED**.

**D-1 — Production view ids (durable research identities).**

| Role | View id |
|---|---|
| SNAPSHOT | `XAUUSD_S_M1_SNAPSHOT_V1` |
| TRAIN | `XAUUSD_S_M1_TRAIN_V1` |
| VALIDATION | `XAUUSD_S_M1_VALIDATION_V1` |
| OOS | `XAUUSD_S_M1_OOS_V1` |
| LOCKBOX | `XAUUSD_S_M1_LOCKBOX_V1` |

Each is a valid `DatasetIdentity`, the five are distinct, and none collides
with `XAUUSD_S_M1_CURATED_V1` or `XAUUSD_S_M1_MT5_V1`. Final manifest paths
follow the generator's convention `{views_root}/{view_id}/manifest.json`:

```
/path/to/research-views/XAUUSD_S_M1_SNAPSHOT_V1/manifest.json
/path/to/research-views/XAUUSD_S_M1_TRAIN_V1/manifest.json
/path/to/research-views/XAUUSD_S_M1_VALIDATION_V1/manifest.json
/path/to/research-views/XAUUSD_S_M1_OOS_V1/manifest.json
/path/to/research-views/XAUUSD_S_M1_LOCKBOX_V1/manifest.json
```

**Changing a view id later means creating a new identity, never renaming an
existing generated view** (ADR-0012: new view identities, never editing an
existing view).

**D-2 — Generator identity.** `name = slice1f-view-generator`, `version = 1`.

The production `platform_git_commit` is **not** the planning commit. It must be
the **exact final independently reviewed commit containing the approved Stage-B
operational script, as of production execution time**, taken from a clean
worktree (`platform_git_dirty = false`). `_execute_prepared_bundle` requires
`AccessContext.platform_git_commit` to equal the generator provenance, and
**OD-F6 resume refuses unless the existing SNAPSHOT anchor's generator name,
version and `platform_git_commit` all match exactly** — so a later code
identity cannot emit children under stale provenance. The commit also feeds the
deterministic `snapshot_binding.event_id` seed and therefore the SNAPSHOT bytes.

**D-3 — CURATED access reason.** CURATED classifies `FULLY_GATED`, so the
ceremony requires a structured reason **and** an unlock bound to the exact
identity:

- `AccessReason.code`: `slice1f-view-generation`
- `AccessReason.detail`: `Owner-authorized Slice 1F Stage-B keys-only CURATED
  read for SNAPSHOT binding and Tier-1 view generation.`
- `GatedUnlock.target`: the exact CURATED `DatasetIdentity`
  (`XAUUSD_S_M1_CURATED_V1`,
  `6f5c7b50e5ee40ee83913f97ba9164713d1296f234e9a759315c6182dbaf0da2`).

**D-4 — Stage-B operational interface.** A thin `scripts/generate_views.py`
with exactly two modes:

- `preflight` — the default safe mode;
- `execute --confirm "GENERATE VIEWS"` — production mode.

No CLI framework, no new dependency. The script is a **composition root only**:
it delegates every generation semantic to the already-approved
`data/views_generation.py` and must not duplicate generation logic, scan
Parquet, contain view-boundary or key arithmetic, or introduce a second audit
system.

**D-5 — Real integration smoke placement (as refined by R-1).** Run
`tests/test_integrity_real.py` **once**, **after** owner confirmation and
**before** the Stage-B CURATED `authorize_access`. Do not repeat it
unnecessarily. Its purpose is to verify the accepted real loader/integrity path
against actual TRAIN bytes immediately before the irreversible CURATED
access/binding sequence begins.

**D-6 — Canonical immutable-dataset before/after verification.** One exact,
reproducible, metadata-only method, recorded **with its command** beside its
value, run identically before and after Stage B:

```bash
# Canonical Slice 1F immutable-source fingerprint (metadata only; opens no Parquet)
cd /path/to/research-data && \
  find . -mindepth 1 -printf '%y %m %s %p\n' | LC_ALL=C sort | sha256sum
```

It hashes, for every entry under the dataset root, the entry type, permission
mode, byte size and relative path, in a locale-stable sort order. Observed
value on 2026-08-09 over the current 22 entries (20 files + 2 directories):

```
5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180
```

Pin the two canonical manifest identities alongside it (manifest JSON, not
Parquet):

```bash
sha256sum /path/to/research-data/*/manifest.json
# XAUUSD_S_M1_MT5_V1      8f91f4516b9a1dbe46c82152fe78021151bc4a3188f623ea6ff9a2c659813cb0
# XAUUSD_S_M1_CURATED_V1  6f5c7b50e5ee40ee83913f97ba9164713d1296f234e9a759315c6182dbaf0da2
```

**mtimes are deliberately excluded** — they are not part of dataset identity and
would make the fingerprint fragile against a benign `touch`. **Full byte hashing
of the 16 Parquet files is deliberately avoided**: the manifest digests already
pin every per-file Parquet SHA-256 and are themselves covered, the deliberate
real TRAIN smoke verifies real bytes against those pins at the D-5 point, and
Stage B is structurally incapable of writing under the dataset root
(`_reject_source_root_overlap`). Introduce content hashing only if a later
concrete reason requires it.

**Never compare this fingerprint to older hashes produced by undocumented or
different methods** — in particular not to the historic tree digest
`4637ca9d3d33eab767086804c73a0b58eca46d36fd867de990b1f0b031f745c3`, whose
producing command is not recorded anywhere in this repository.

#### Owner-approved Stage-B operational refinements R-1…R-3 (2026-08-09)

**R-1 — The real TRAIN smoke runs *after* owner confirmation.** The earlier
preparation recommendation to run it before final confirmation is
**superseded**, because that smoke opens real Parquet **value** data and **no
real Parquet file may be opened before owner confirmation**. Binding production
order:

1. metadata-only preflight;
2. display exact production inputs and state;
3. **STOP**;
4. explicit owner confirmation via the exact `execute --confirm "GENERATE
   VIEWS"`;
5. run the deliberate real TRAIN smoke **once**;
6. if green, perform the exact CURATED `authorize_access`;
7. then perform the Stage-B keys-only generation.

If the smoke fails: **stop**; do **not** call the Stage-B CURATED
`authorize_access`; do **not** create the Stage-B CURATED
`dataset_access_decision`; do **not** generate manifests. The owner confirmation
authorizes the deliberate real-data production sequence as a whole; the smoke is
the **first real-data contact after** that confirmation.

**R-2 — Preflight may create only the empty canonical views root.** The accepted
`_prepare_view_bundle(...)` requires the supplied views root to already exist
(`_validate_views_root` resolves it strictly and requires a canonical, non-
symlink directory), and the generator creates only per-view child directories.
Stage-B **preflight is therefore allowed to create**
`/path/to/research-views/` as an empty canonical directory when
absent. Stated honestly: this is a real filesystem side effect of preflight.

Preflight remains metadata-only **in the research/data-access sense** — it must
create no manifest, no SNAPSHOT, no child view, no audit event, no
`AccessGrant`, and must perform no market-data read. If the owner does not
execute Stage B, an empty views root may remain; that is harmless and is **not a
research artifact**. **Do not modify the Stage-A generator merely to avoid
creating this empty directory.**

**R-3 — A complete bundle is a terminal no-op before authorization.** If
preflight finds that the SNAPSHOT and all four children exist, validate, and
correspond to the exact requested bundle, the script must report

```
Bundle already complete — nothing to execute.
```

and **exit successfully before** production authorization is requested, before
`authorize_access`, before any `AccessGrant` is issued, before
`MarketFrameLoader.load`, and before any new audit event is written.

OD-F6 reread/recovery applies **only** where a valid SNAPSHOT anchor exists and
one or more child manifests need reconstruction. **Do not perform a gated
keys-only reread merely to prove that an already-complete immutable bundle is
still complete.**

#### Stage-B fresh-production confirmation boundary

The maintained Stage-B procedure must make this boundary unmistakable.

**BEFORE owner confirmation** — Git/repo checks · manifest and policy metadata ·
identity resolution · collision checks · the D-6 fingerprint · views-root and
audit-log state · empty views-root creation if required (R-2) ·
`_prepare_view_bundle` · complete-bundle early exit (R-3) · preflight display.
**NO real Parquet read.**

**AFTER exact owner confirmation** — 1. deliberate real TRAIN smoke (D-5/R-1);
2. if green, construct the production `AccessContext`; 3. exact CURATED
`authorize_access`; 4. durable `dataset_access_decision`; 5. one keys-only
`MarketFrameLoader.load(grant, columns=())`; 6. Stage-B generation.

#### Stage-B operational runbook — script COMPLETE / REVIEW PENDING

This runbook documents the implemented `scripts/generate_views.py` composition
root. It is **not authorization to execute production**. Production generation
remains a later, separately owner-authorized task after the script and its
confirmation boundary pass independent review.

**Safe preflight commands.** Bare invocation and explicit `preflight` are
equivalent:

```bash
cd /path/to/trading-research-suite
uv run python scripts/generate_views.py

# Equivalent explicit spelling:
uv run python scripts/generate_views.py preflight
```

Preflight performs only Git, manifest-JSON, policy, registry, filesystem and
metadata-fingerprint work. It verifies and displays the exact `HEAD`, local
`main`, `origin/main`, clean state, committed script identity, generator code
commit, CURATED and partition-source identities, SplitPolicy identity/axis/four
ranges, five view ids and final paths, views-root and audit-log state, registry
collision state, accepted `_prepare_view_bundle` mode, bundle state, D-6
fingerprint and entry count, RAW/CURATED manifest digests, timestamp procedure,
and expected fresh/resume/no-op audit delta. It fails closed if the worktree is
dirty; local `main`, `origin/main` and `HEAD` differ; the script is not the
committed version at `HEAD`; a configured source/policy/fingerprint identity
mismatches; or accepted registry/generation preparation refuses a collision,
root overlap, orphan child, invalid anchor or incompatible provenance.

The **only permitted preflight side effect** is R-2 creation of the empty,
canonical `/path/to/research-views/` directory when it is
absent. The script states that side effect explicitly. Preflight creates no
view-id child directory, manifest, SNAPSHOT, audit record or grant and performs
no market-data or Parquet read. If the root already exists, preflight does not
create or rewrite it. An empty root left after an owner declines execution is
harmless and is not a research artifact.

**Bundle timestamp procedure.** On each invocation the script calls the
existing `operational_event_time_utc()` once during metadata-only preflight and
displays that value as the proposed fresh-bundle `generated_at_utc`. A fresh
execution uses that one value for all five manifests. A partial resume instead
reuses the exact timestamp validated from the existing SNAPSHOT anchor; it does
not substitute the newly proposed value or create per-child timestamps.

**Future production command — do not run during implementation or review.**

```bash
cd /path/to/trading-research-suite
uv run python scripts/generate_views.py execute --confirm "GENERATE VIEWS"
```

The confirmation literal is exactly `GENERATE VIEWS`. Missing or different
confirmation refuses after the allowed metadata-only preflight; a malformed
command shape refuses immediately. Every refusal occurs before the real smoke,
production `AccessContext`, `JsonlAuditWriter`, CURATED authorization, grant,
loader or bundle executor. A complete bundle is the R-3 exception: it returns
the terminal no-op before confirmation becomes relevant.

**Fresh sequence.** The future owner-authorized fresh invocation performs:

1. the complete metadata-only preflight above, with `_prepare_view_bundle`
   returning `fresh`;
2. exact confirmation validation;
3. the deliberate real TRAIN smoke node
   `tests/test_integrity_real.py::test_ephemeral_real_train_target_loads_after_full_integrity`
   once — the first real Parquet contact;
4. only if the smoke reports exactly one passing test, revalidation that the
   Git/code identity is unchanged, then production `AccessContext` construction
   and the existing `JsonlAuditWriter` at
   `/path/to/research-runs/audit.jsonl`;
5. one exact CURATED `AccessRequest` with D-3 reason and identity-bound
   `GatedUnlock`, delegated to the existing `authorize_access`;
6. the resulting one `AccessGrant`, existing `MarketFrameLoader`, and accepted
   `_execute_prepared_bundle` path, which performs the one keys-only
   `MarketFrameLoader.load(grant, columns=())` and publishes the five manifests;
7. the identical D-6 fingerprint and RAW/CURATED manifest-digest checks again,
   followed by evidence rendering.

If the real TRAIN smoke fails, execution stops before CURATED authorization: no
Stage-B `dataset_access_decision`, grant, keys-only read, binding or manifest is
created. A successful fresh generation has exactly one
`dataset_access_decision`, then one `snapshot_binding`; the durable binding
append precedes SNAPSHOT publication, and no third publication event exists.

**OD-F6 partial-resume sequence.** A valid SNAPSHOT with one or more missing
children is prepared and validated before confirmation. After exact
confirmation, the real TRAIN smoke runs once; if green, the script performs the
same exact CURATED reauthorization and delegates one keys-only reread to the
accepted executor. The existing SNAPSHOT is never deleted, rebound or
republished. Missing children are reconstructed from its exact identity,
timestamp, binding and generator provenance; byte-identical existing children
are no-ops; a conflicting child refuses; and there is no force path. Resume
adds exactly one `dataset_access_decision` and zero new `snapshot_binding`
events. Generator name, version or `platform_git_commit` mismatch refuses
during preflight before any gated reread.

**Crash-retry audit note.** A crash after durable `snapshot_binding` append but
before SNAPSHOT publication may cause a same-input retry to append another
audit record carrying the same deterministic logical
`snapshot_binding.event_id`. This is expected and benign under current V1
semantics. **Never clean up, truncate, rewrite or otherwise repair the
append-only audit history.**

**R-3 complete-bundle terminal no-op.** When preflight establishes that the
SNAPSHOT and all four children exist, validate and match the exact requested
immutable bundle, the script prints:

```
Bundle already complete — nothing to execute.
```

and exits successfully before confirmation-sensitive real actions. It performs
no real smoke, authorization, grant creation, loader call, audit append,
keys-only reread or manifest rewrite. OD-F6 is only for a valid SNAPSHOT whose
children need reconstruction.

**Canonical immutable-source fingerprint (D-6).** The script implements and
prints this exact metadata-only method, identically before and after a future
fresh or resume execution:

```bash
cd /path/to/research-data && \
  find . -mindepth 1 -printf '%y %m %s %p\n' | LC_ALL=C sort | sha256sum
```

The approved pre-production value is
`5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`
over 22 entries. The evidence displays the RAW manifest digest
`8f91f4516b9a1dbe46c82152fe78021151bc4a3188f623ea6ff9a2c659813cb0`
and CURATED manifest digest
`6f5c7b50e5ee40ee83913f97ba9164713d1296f234e9a759315c6182dbaf0da2`
alongside it. The method excludes mtimes and never opens or hashes Parquet
payload bytes.

**Expected CP-4 evidence output.** Preflight renders the complete proposed
bundle and pre-state listed above. After a future successful accepted bundle
execution, the script renders the execution mode, bound native key, and for
each of the five roles its view id, manifest SHA-256, final path and
created/reused disposition; the expected audit count/order; the immutable
source fingerprint before and after; and the post-execution RAW/CURATED
manifest digests. It labels this output a **CP-4 evidence candidate** and does
not change checkpoint status. CP-4 remained NOT_READY until production had been
separately authorized and executed and the generated manifests, binding
evidence and actual audit trail received their required review — all of which
has since happened; CP-4 was owner-approved on 2026-08-10 (record below).

**Implementation evidence.** The composition root is commit
`389822c710d91f115ec741f6be9d10edc180ba8f`; its synthetic/preflight and
bounded architecture tests are commit
`1cadc187a41de3b53cc77bea63dce2e7554c9f7d`. The no-real-data profile passed
35 focused script tests, 111 focused script plus existing architecture tests,
and all 689 repository tests with `tests/test_integrity_real.py` explicitly
excluded. Repository-wide Ruff, strict mypy over `src tests scripts`, and
`git diff --check` passed. No production preflight or execution was run.

**Status after operational-script implementation:** the owner-approved
decisions are unchanged; the thin operational script was **COMPLETE / REVIEW
PENDING** at this point. The subsequent independent review, owner approval and
production execution outcome are recorded below.

#### Stage-B production outcome — COMPLETE (2026-08-09)

This subsection is **outcome and history**. It changes no accepted contract:
OD-F1…OD-F6, D-1…D-6 and R-1…R-3 above stand exactly as approved.

**Review and integration.** The focused bounded independent review of the
Stage-B operational script is COMPLETE with verdict **APPROVE WITH NON-BLOCKING
NOTES** and found no blocker. The owner approved it, and the exact reviewed head
`f8b6168511fa2544479c0ad96a16c50cf0ab7890` was fast-forwarded into `main` with
no merge commit and **no intervening documentation commit**, so the production
run would record the reviewed generator-code identity exactly as D-2 requires.

**Preflight and authorization.** One metadata-only production preflight ran
first and reported a clean `fresh` bundle, creating only the permitted empty
canonical views root (R-2). The owner then gave explicit final authorization and
`execute --confirm "GENERATE VIEWS"` was run **once**, exiting `0` with empty
stderr. The deliberate real TRAIN smoke ran after confirmation and before the
CURATED authorization (R-1/D-5) and had to report exactly one passing test.
**Execution mode: fresh. No OD-F6 recovery was required or performed.**

**Published bundle.** Five hash-pinned manifests under
`/path/to/research-views/{view_id}/manifest.json`, sharing one
bundle timestamp `2026-08-09T20:40:46.018612Z`, generator
`slice1f-view-generator / 1 / f8b6168511fa2544479c0ad96a16c50cf0ab7890`,
partition source `XAUUSD_S_M1_MT5_V1 /
8f91f4516b9a1dbe46c82152fe78021151bc4a3188f623ea6ff9a2c659813cb0`, split policy
`ADR-0012 / v1 / b7e0f582149509b26e53bcaefc8e4c3e36cadf2e7f558984fadb79bbe73405eb`,
and the complete ordered nine-column source contract:

| Role | View id | Manifest SHA-256 | Admitted native range | Rows | Parts | `discovery_default` |
|---|---|---|---|---|---|---|
| SNAPSHOT | `XAUUSD_S_M1_SNAPSHOT_V1` | `d5b1724cb204699d4e8c3a45bebffa39d080a5260d285610f50981f6ae7eb600` | `[1577926800, 1785542220]` | 2,328,717 | 7 | false |
| TRAIN | `XAUUSD_S_M1_TRAIN_V1` | `30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e` | `[1577926800, 1703894160]` | 1,414,538 | 4 | **true** |
| VALIDATION | `XAUUSD_S_M1_VALIDATION_V1` | `5d122caa6a38c5bcf94ebc20895199d89c30f0f460fabd244865ad8829ebe6fd` | `[1704157200, 1735689360]` | 354,856 | 1 | **true** |
| OOS | `XAUUSD_S_M1_OOS_V1` | `d94a54331a1ef30b0242e685ab8d2f3f0d8d910a64b9c52412682d991bcab3af` | `[1735779600, 1767225360]` | 353,647 | 1 | false |
| LOCKBOX | `XAUUSD_S_M1_LOCKBOX_V1` | `c9080133719b79016beace397ab569aad07a2b15008bab6c5d36730b14b64dc8` | `[1767315600, 1785542220]` | 205,676 | 1 | false |

SNAPSHOT's parent is the exact CURATED identity `XAUUSD_S_M1_CURATED_V1 /
6f5c7b50e5ee40ee83913f97ba9164713d1296f234e9a759315c6182dbaf0da2`; all four
children pin the published SNAPSHOT identity as parent. All five re-read cleanly
as strict v1.1 through the ordinary reader; `default_views()` returns exactly
TRAIN + VALIDATION; classification is TRAIN/VALIDATION `NORMAL`, OOS
`OOS_EXPLICIT`, SNAPSHOT/LOCKBOX `FULLY_GATED`. The four Tier-1 row counts sum
to exactly the SNAPSHOT count of 2,328,717, so the 4,897 post-cap 2026-08-01 →
2026-08-06 rows admitted by CURATED are excluded from every generated view.

**Binding verified.** Bound native key **`1785542220`** at or below policy cap
`1785542340` (`policy_cap_encoded` `2026-07-31T23:59:00`), method
`market-frame-loader-keys-only-v1`, `snapshot_binding.event_id`
`c2bc6820-11bc-5455-945a-09b23390c47a`, `source_access_event_id`
`056443ef-825d-4836-b76b-ec915b75bdd0`. Independently recomputed from the
published bytes: `sha256(published SNAPSHOT) ==
snapshot_binding.snapshot_manifest_sha256` — **TRUE**. The binding block appears
only on SNAPSHOT and LOCKBOX.

**Audit verified.** Exactly two records, in order, in the append-only log (mode
`0600`): `dataset_access_decision`
`056443ef-825d-4836-b76b-ec915b75bdd0` at `2026-08-09T20:40:46.615427Z`
(outcome **allowed**, `fully_gated`, basis `range_exceeds_oos_boundary`, reason
`slice1f-view-generation`, unlock bound to the exact CURATED identity), then
`snapshot_binding` `c2bc6820-11bc-5455-945a-09b23390c47a` at
`2026-08-09T20:40:46.761963Z`. **No third publication event.** History untouched.

**Immutable source unchanged.** The D-6 method above produced
`5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180` both before
and after — **EQUAL**, over 22 entries, newest mtime still 2026-08-06. RAW and
CURATED manifest digests are unchanged. No Parquet payload was hashed.

**Status:** Stage-B production generation is **COMPLETE**; recovery was **not
used**; and **no research use of the generated views occurred before CP-4
approval**.

#### CP-4 — independent review and OWNER APPROVAL (2026-08-10)

The focused, bounded, independent read-only CP-4 review of the real generated
bundle is **COMPLETE** with verdict **APPROVE WITH NON-BLOCKING NOTES**, at
repository head `e2d0833f8d1198bb8734789bd8f845dee4a6f2b0` over production
execution identity `f8b6168511fa2544479c0ad96a16c50cf0ab7890`. The **owner
APPROVED CP-4 on 2026-08-10**, and **Slice 1F is COMPLETE**.

The review found **no blocker** and independently recomputed rather than
trusting the Stage-B report. It re-derived every published manifest digest from
its bytes and matched it to the reader identity; recomputed the RAW, CURATED
and policy digests; re-proved `sha256(published SNAPSHOT) ==
snapshot_binding.snapshot_manifest_sha256`; confirmed the exact RAW → CURATED →
SNAPSHOT → four-child lineage with no child pointing at CURATED or RAW; checked
all 14 partition entries for filename, digest, byte size, containment, ordering
and non-zero rows; confirmed every observed range is an honest subset of its
policy range with nothing beyond the bound `1785542220`; verified the Tier-1 row
identity summing to the SNAPSHOT count with the 4,897-row post-cap exclusion
localized to the 2026 partition; confirmed exactly two ordered audit records
with exact cross-reference, production Git identity and no third publication
event; confirmed one generator block and one bundle timestamp across all five
manifests with no documentation-commit SHA substituted; confirmed
`default_views()` returns exactly TRAIN + VALIDATION with every gated role
refused by `resolve_role`; and confirmed the unchanged immutable-source
fingerprint.

Two findings are worth carrying forward as accepted properties. **Gating is
genuinely range-derived**: every observed classification basis was `range_*`,
never `role_floor_*`, so the role floor remains a raise-only backstop rather
than the mechanism. **Loader protection was approved without any new real-data
read**: per-partition admitted-span restriction inside the adapter, out-of-
admission bound refusal, and exact endpoint/row-count validation together make
the post-cap rows structurally unreachable and prevent a boundary-straddling
file from leaking adjacent-view rows; a real LOCKBOX or SNAPSHOT read would have
required a new fully-gated ceremony and was deliberately not performed.

Validation reproduced under the no-real-data profile: 689 tests excluding
`tests/test_integrity_real.py`, 442 focused
manifest/registry/gating/loader/view/generation/script tests, 76 architecture
tests, Ruff, strict mypy over `src tests scripts`, and `git diff --check`. No
production artifact or audit record was modified and no new production
authorization occurred.

**CP-4 non-blocking notes.** Recorded as review notes, **not** blockers, and
scheduling no immediate remediation; none blocks Slice 1G.

1. **Deterministic `snapshot_binding.event_id` logical-id semantics** — a
   `uuid5` over the logical binding inputs, required because `audit_event_id`
   sits inside the hashed SNAPSHOT bytes. A retry after a durable binding append
   but before SNAPSHOT publication could repeat the logical id; this **did not
   occur** in production, and repository contracts do not require per-append
   uniqueness.
2. **OD-F6 recovery access-event forward link** — a resume writes a new
   `dataset_access_decision`, but reconstructed children carry no forward
   reference to it. No resume occurred. Richer run-level provenance belongs to
   later RunManifest/run infrastructure.
3. **Standalone `atomic_write.py` parent-symlink hardening** — the helper's own
   `parent.is_dir()` check and parent `fsync` follow symlinks, while every
   current production call path validates the root/path first and opens the
   final name `O_NOFOLLOW`. No current violation.
4. **Documentation/status cleanup** — stale current-state `CP-4 NOT_READY`
   wording in three places and a self-contradictory §9 duty row, classified as
   status-wording drift rather than a checkpoint-state problem. **Corrected when
   this approval was recorded.**

**Slice 1F is COMPLETE.** Slice 1G planning, implementation, independent review
and owner approval are all COMPLETE (2026-08-10; decision OD-G2 recorded below
and in ADR-0005; reviewed head `fa69733367ee4c340894ff9a0d5d8b6b99f92a6e`).
**Slice 1G is COMPLETE.** **Slice 1H-A is COMPLETE and OWNER APPROVED
(2026-08-10)** at reviewed head `64be3756bc7d4fdff2c5465774c3b5fc26998c71`,
under **OD-H1 OWNER APPROVED 2026-08-10** (legacy retirement scope, recorded in
the Slice 1H section below). **OD-H2 is OWNER APPROVED (2026-08-10)** and
**Stage 1H-B qualification is COMPLETE** — executed exactly once on 2026-08-10
at `ad1e507a3577b377317d4c68d029454479d43d7a`, with every measured budget
passed, cold honestly recorded as unmeasured under owner decision 11, and the
audit log moving exactly 2 → 3. §8 carries the measured numbers and §11 is
verified item by item. **CP-5 is COMPLETE (verdict APPROVE WITH NON-BLOCKING
NOTES) and OWNER APPROVED on 2026-08-10, so Slice 1H and Phase 1 are COMPLETE.**
The **§13 post-Phase-1 operational checklist is COMPLETE (2026-08-12)** — see
§13 — so it **no longer gates Phase 2**; Phase-2 implementation is permitted
under [PHASE2_FEATURES.md](PHASE2_FEATURES.md) and ADR-0021.

**Non-blocking CP-2 finding N-2 — required before/during Slice 1F.** Pin and
assert all four machine-readable policy ranges (TRAIN, VALIDATION, OOS and
LOCKBOX), including each inclusive start/end native-key pair, against
ADR-0012's normative table before generated views depend on them. Do not
UTC/civil-time re-derive the native keys; checking only the OOS boundary or
snapshot cap is insufficient.

### 1G — GapIndex

**Planning COMPLETE and OWNER APPROVED 2026-08-10. Implementation COMPLETE,
independently reviewed (final verdict APPROVE), OWNER APPROVED 2026-08-10 and
fast-forwarded into `main` at reviewed head
`fa69733367ee4c340894ff9a0d5d8b6b99f92a6e`. Slice 1G is COMPLETE.** The
contract below was normative for implementation and is preserved as the
accepted GapIndex contract; the implementation outcome is recorded at the end
of this section.

- **Objective:** compact, lazy, ephemeral, arithmetic-only gap facts.
- **Files:** `data/gaps.py` (new); `data/__init__.py` (export only).
- **Depends on:** 1D (needs a MarketFrame's key array). Nothing depends on 1G.

#### Arithmetic-only mandate

GapIndex records observed integer facts about one frame's native-key axis and
nothing else. It asserts no market session, holiday, outage, closure or
rollover, applies no gap *policy* (that is the Phase 2 FeatureProvider's
declaration), and applies no threshold, tolerance or severity judgement.
Comparison is exact integer equality.

#### OD-G2 — `boundary_class` deferred (OWNER APPROVED 2026-08-10)

The V1 GapIndex records **no boundary-class column**. `MarketFrame` carries no
partition provenance, a key gap does not prove a partition boundary, and
cross-view seams belong to `BoundedView` composition, which GapIndex
deliberately does not know. Shipping the field would require invented
inference, leaking manifest/composition knowledge into an arithmetic
component, or a column that is structurally always `NONE`. Full reasoning and
the additive-only condition for adding boundary facts later are recorded in
[ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md), "Implementation
guidance (recorded 2026-08-10, OD-G2 — no decision change)". The ADR's
architectural decision is unchanged.

#### Public API — exactly one public symbol

Re-checked against the owner's minimalism rule at recording time. The planning
report proposed two symbols (`GapIndex` plus a module-level
`compute_gap_index`); the separate function was found to carry **no
responsibility distinct from construction** — it consumes no authority and
composes nothing, unlike `open_bounded_view`, which exists as its own symbol
precisely because it orchestrates loader calls under exact grants. The smaller
surface loses no correctness, provenance safety or cross-strategy usefulness,
so **one public symbol is recorded**:

```
GapIndex                       # data/gaps.py; exported via data/__init__.py

GapIndex.from_frame(frame: MarketFrame, *, nominal_interval: int) -> GapIndex
                               # the sole public producer (classmethod)
GapIndex.__init__              # refuses: raises TypeError, exactly as MarketFrame does

# scalar properties
dataset_ref -> DatasetRef      # exact source identity; metadata, never authority
clock_domain -> ClockDomain    # axis tag of prev_key/next_key (ADR-0002)
unit -> TimeUnit               # resolution of every integer here (ADR-0002)
nominal_interval -> int        # the interval supplied by the caller
num_gaps -> int                # G

# columnar properties (read-only NumPy)
prev_key, next_key, delta, deviation
```

No `__iter__`, no `__getitem__`, no `__len__`, no summary statistics, no query
helpers, no boundary field. Direct construction is refused so nobody can
present fabricated arrays as observed gap facts.

`GapIndex` accepts a **`MarketFrame` only** — never a bare key array. A frame
can only be minted by the loader, so its keys are already proven `int64`,
one-dimensional, strictly increasing and immutably backed; accepting a bare
array would permit fabricated keys, force a redundant revalidation pass, and
discard the axis tags ADR-0002 requires. It reads **only `frame.keys`** and
needs no value column; a keys-only frame (`select(())`) is fully sufficient.

**Export:** `GapIndex` is added to `data/__init__.py` and to
`DATA_PACKAGE_PUBLIC_EXPORTS` in `tests/test_architecture.py`. This follows the
established facade convention — every public data-layer *contract* is
re-exported — and is not convenience exporting: GapIndex is a research-facing
contract Phase 2 feature code will annotate against. (Contrast the Slice 1F
generation library, deliberately internal-only under OD-F1 because it is not a
research contract.)

#### Nominal interval — explicit caller-supplied argument

`nominal_interval` is a **required, keyword-only, positive `int`**, expressed
in the frame's own `unit`. **There is no default.**

The repository has no trustworthy machine-readable bar size:
`NormalizedManifest.timeframe` is documented as a free-text label "as the
source states it", and `TimeUnit` members are documented as "labels, not
conversion factors" (`TimeUnit.SECONDS` is the integer's encoding resolution,
not a 60-second bar). Rejected alternatives and why: parsing `timeframe`
(invents a contract the manifest never made, and breaks on another vendor's
spelling); deriving from `TimeUnit` (M1 and M5 share `SECONDS`); hardcoding 60
(contradicts approved 1F multi-dataset generality); inferring the modal delta
(circular — the nominal classifies the deltas — and inference presented as
fact); carrying it on `MarketFrame` (relocates the same dishonesty, expands
`MarketFrame` against ADR-0003's explicit warning, and converts an additive
slice into a core-contract change).

Requiring the caller to state it makes the bar-size assumption explicit,
greppable and reviewable at each call site — the same discipline
`CalendarTimePolicy` applies to civil time. **No accepted public contract
changes:** `MarketFrame`, the manifest schema, the loader and the gate are
untouched. A typed duration primitive carrying unit and clock domain would
additionally make unit mismatch structurally impossible; it is **deferred**,
since it would add a new `core` public type for a platform with exactly one
unit and one clock domain, and can be introduced later without changing the
arrays or their semantics.

#### Gaps-only representation

GapIndex retains **only discontinuities** — transitions where
`delta != nominal_interval` — and never materializes normal adjacent
transitions. Measured on a synthetic array at the exact published SNAPSHOT
scale (N = 2,328,717 keys, ~2,200 discontinuities, NumPy 2.5.1 on this VM):

| Representation | Records | Retained | Median compute |
|---|---|---|---|
| Every adjacent pair | 2,328,716 | 74.5 MB | 4.05 ms |
| **Discontinuities only** | 2,200 | **70,400 bytes** | **2.74 ms** |

Nothing is lost: normal transitions are fully reconstructible from
`frame.keys` plus `nominal_interval`, both of which the caller already holds.
Materializing them would consume ~7.5% of the 1.0 GiB RSS budget to store
2.3 M records that all say "nothing happened", and would force every consumer
to filter `deviation != 0` — re-deriving this representation at each call
site. This satisfies owner decision 14's "compact" requirement.

#### The four V1 arithmetic fields

Let `G` be the number of retained discontinuities. All four arrays are exact
`np.dtype('int64')`, `ndim == 1`, shape `(G,)`, C-contiguous, index-aligned
(record *i* of each describes the same transition) and ordered by increasing
`prev_key`.

| Field | Meaning |
|---|---|
| `prev_key` | Native key of the earlier row of each discontinuous adjacent pair |
| `next_key` | Native key of the later row of the same pair |
| `delta` | `next_key - prev_key`, in the frame's `unit` |
| `deviation` | `delta - nominal_interval`, in the frame's `unit` |

#### Exact formulas

```
full_delta = numpy.diff(frame.keys)                       # int64, length N-1
if full_delta.size and int(full_delta.min()) <= 0: refuse # see below
retained   = numpy.flatnonzero(full_delta != nominal_interval)
prev_key   = frame.keys[:-1][retained]
next_key   = frame.keys[1:][retained]
delta      = full_delta[retained]
deviation  = delta - nominal_interval
```

- Both `delta` and `deviation` are **signed `int64`**. `delta` is always ≥ 1;
  `deviation` is signed by necessity.
- `delta == nominal_interval` is **never** stored.
- A **sub-nominal** gap (`delta < nominal_interval`) is retained with a
  negative `deviation` and is **never refused** — refusing it would be
  GapIndex passing judgement on the dataset.
- An **irregular but strictly increasing** dataset works correctly; if every
  transition differs from the nominal, `G == N-1`. That is honest, and is the
  caller's own consequence for declaring a nominal the data does not follow.
- **Overflow:** `MarketFrame` permits the full `int64` key range, so a
  pathological span could wrap `numpy.diff` and yield a non-positive delta. The
  vectorized `full_delta.min() > 0` check catches exactly this at a measured
  cost of 0.11 ms (2.74 ms vs 2.63 ms) and also re-proves the frame's
  strictly-increasing invariant.
- **No tolerances and no configurable gap thresholds.**

#### Empty and small-frame behaviour

Natural empty results, never exceptions:

| Case | Result |
|---|---|
| 0 rows | Empty `GapIndex`; all arrays `shape (0,)`, `num_gaps == 0` |
| 1 row | Empty (no adjacent pairs) |
| 2 rows, normal interval | Empty |
| 2 rows with a gap | `G == 1` |
| Keys-only frame (`select(())`) | Identical to the full-column frame |
| Frame sliced to a valid interval with zero observed rows | Empty — this frame can exist, per `slice_by_keys` |

Refusals — all `ContractViolationError`, an **existing exported error; no new
error type is added**: `frame` is not a `MarketFrame`; `nominal_interval` is a
`bool` (checked before `int`, since `bool` subclasses `int`); is not an `int`;
is `<= 0`; or any `full_delta <= 0`.

#### Immutability and representation

Compact columnar NumPy, following ADR-0003's implementation guidance: each
column is backed by an immutable `bytes` buffer, and every public array access
returns a **fresh** `numpy.frombuffer` descriptor with `write=False`. A merely
read-only owning `ndarray` can make itself writable again; `bytes` cannot.
Descriptor changes therefore stay local to the returned object.

The storage helper is **local to `data/gaps.py`** (roughly a dozen lines,
always `int64` and offset 0) rather than importing
`core.frames._ImmutableArrayStorage`, to avoid adding a second instance of the
private cross-module coupling already recorded as a non-blocking review note.
No per-gap Python objects, no generic table abstraction, and no
pandas/Polars/DuckDB/PyArrow type anywhere.

#### Lifecycle — lazy, ephemeral, no persistence, no caching

Computed on demand by the caller. **No caching anywhere:** `MarketFrame` and
`BoundedView` are frozen slotted dataclasses, so memoizing on either would
require mutable state plus core-contract and frozen-surface-test changes to
save 2.74 ms. **No persistence:** no gap files, cache keys, manifests, registry
entries or audit events (owner decision 14; persistence remains a §12 deferred
decision, revisited only if Phase 2 profiling shows repeated recomputation
actually matters).

> Note: the 2026-08-07 planning report §14 — a review record, not this
> normative plan — wrote "memoized per frame". Owner decision 14 mandates only
> lazy/compact/ephemeral. Measurement and the frozen-slots reality of
> `MarketFrame` both resolve this to **no cache**; no accepted contract is
> contradicted.

#### Capability boundary

`data/gaps.py` may import only: `__future__`, `dataclasses`, `typing`,
`numpy`, `trading_research.core.errors.ContractViolationError`,
`trading_research.core.frames.MarketFrame`, and — under `TYPE_CHECKING` only —
`numpy.typing`, `core.identity.DatasetRef`, `core.time.{ClockDomain, TimeUnit}`.

It has **no** registry, gating, authorization, audit, filesystem,
environment/config-discovery, view-generation, adapter, Parquet/PyArrow or
data-loading authority, and imports no other `trading_research.data.*` module.
It decodes nothing, so it correctly requires no `AccessGrant`.

#### Semantic-vocabulary and manifest-label prohibition

No semantic vocabulary (`weekend`, `holiday`, `session`, `closure`, `outage`,
`missing`, `rollover`, `intraweek`, `normal_boundary`) may appear as an
identifier or non-docstring string literal in `data/gaps.py`. The guard is
**scoped to that module and to identifiers plus non-docstring literals**, so
docstrings may — and should — explain what GapIndex refuses to claim. This
follows the precedent already set by
`test_native_primitives_declare_no_utc_identifier`, whose own docstring records
that "prose may discuss UTC — explaining why it is refused is valuable". The
ban is deliberately **not** repository-wide: `data/adapters/mt5.py` and future
provenance code may legitimately quote a manifest's own labels as dataset
metadata, exactly as ADR-0005 permits.

Raw-manifest gap labels (`total_weekend_gap_count`,
`total_intraweek_gap_count`, `classification: "normal_boundary"`) are never
promoted into GapIndex output. Verified structurally at planning time: the
legacy readers ignore unknown fields, `ViewManifestV11` uses `extra="forbid"`,
and `NormalizedManifest` has no field able to carry them.

#### Tests

- **Unit:** regular sequence; one gap; multiple gaps of differing sizes; exact
  field values; multiple nominal intervals (60/300/900/1) proving nothing is
  hardcoded; a non-seconds unit (milliseconds with `nominal_interval=60_000`);
  sub-nominal gap retained with negative deviation; all-irregular frame; 0/1/2
  rows; keys-only frame; zero-observed-row slice; strict dtype/shape/contiguity;
  arrays read-only; descriptor isolation and a fresh descriptor per access;
  direct construction refused; every refusal path; poisoned value columns
  proving keys-only computation.
- **Property:** `delta == next_key - prev_key` and
  `deviation == delta - nominal_interval` over arbitrary strictly-increasing
  key arrays; completeness/soundness — retained transitions are exactly
  `{j : k[j+1]-k[j] != nominal}`, cross-checked against a naive Python loop.
- **Raw-manifest non-promotion (through the real interface):** two synthetic
  CURATED manifest documents identical except for provenance gap labels
  (`total_weekend_gap_count`, `total_intraweek_gap_count`, a
  `classification: "normal_boundary"` block) over identical synthetic
  partitions; each run through `DatasetRegistry.discover` →
  `authorize_access` (synthetic policy, in-memory audit writer) →
  `MarketFrameLoader.load(grant, columns=())` → `GapIndex.from_frame`; assert
  all four arrays are equal across variants and that no label value or token
  appears in the result or in the public surface. Not a string-matching test.
- **Architecture guards:** vocabulary guard (scope above); import allowlist;
  exact module public surface (`__all__ == {"GapIndex"}`); exact `GapIndex`
  member surface as a frozen set; no persistence/audit/registry/gating/
  filesystem reference; no Parquet/loader/adapter reference; vectorized
  evidence (the module calls `numpy.diff`; the producer and its private helpers
  contain no `for`/`while` statement and no comprehension over key data);
  `DATA_PACKAGE_PUBLIC_EXPORTS` gains exactly `GapIndex`. A **bounded
  three-mutation** self-check proves the guards bite (a `session_label`
  identifier; a `for` loop in the producer; an added `import pyarrow`) — three
  mutations, deliberately not a campaign.
- **Performance smoke:** synthetic only, unmarked, in the ordinary suite.

#### Performance measurement

**Synthetic data, not a real frame.** The question is how fast `numpy.diff` +
`flatnonzero` run over 2.33 M `int64` values, which depends only on size and
dtype. A real TRAIN frame would require the registry, gate, grant, Parquet I/O
and integrity hashing — none of which GapIndex touches and all of which would
dominate the measurement — and would add real-data contact for no answer. It
also keeps the whole 1G suite inside the owner-approved no-real-data profile.

Acceptance is **qualitative**; the repository defines no GapIndex SLA and this
slice must not invent one:

> Computing a `GapIndex` over a production-scale frame must complete well under
> one second and retain memory proportional to the discontinuity count rather
> than the row count.

The test asserts the deterministic part strictly (retained bytes < 1 MB at
N = 2,328,717, which structurally proves the gaps-only representation) and the
timing with a deliberately generous ceiling (< 1.0 s against 2.74 ms observed,
a ~365× margin) so it cannot become a flaky gate. **No `tests/performance/`
directory and no `pytest` marker** — pytest runs under `--strict-markers` with
no markers registered, and adding one is Slice 1H work (§7, §8).

#### Acceptance

GapIndex computable on demand from any MarketFrame's key array; the four
arithmetic fields exact; not persisted and not cached; recomputation cost
measured on synthetic production-scale data and recorded in §9, confirming the
"lazy is fine" call.

#### Rollback boundary

Additive; no other slice depends on it. No existing runtime contract, schema,
dependency or lockfile changes.

#### Deliberately unbuilt (deferred or rejected — do not add)

The annotator and any session-shaped labels (deferred past V1 per ADR-0005);
`boundary_class` (OD-G2); calendar/weekend/holiday detection; gap severity
categories; configurable thresholds or tolerances; aggregate gap statistics;
per-partition summaries; persistent GapIndex artifacts; caching; per-gap Python
objects; arbitrary metadata tags; multi-frame indexes; `BoundedView`
integration; automatic feature-window invalidation; nearest-gap/binary-search
helpers; row-aligned gap masks; `between(lo, hi)` and other query-helper
families; manifest/partition metadata plumbing.

#### Implementation outcome — COMPLETE and OWNER APPROVED (2026-08-10)

Slice 1G implementation is **COMPLETE and merged into `main`** at reviewed head
`fa69733367ee4c340894ff9a0d5d8b6b99f92a6e`, fast-forwarded from
`670e7caa39bbf0d3c70e42a53cc9de1c74de1202` with no merge commit, no squash and
no rebase. Four commits, four files, `+1,236 / −0`:

| Commit | Subject | Files |
|---|---|---|
| `a39db3e9f63598e09deb6d720296ed95d3c8fcc3` | feat: add arithmetic gap index | `data/gaps.py` (new), `data/__init__.py` |
| `e1d6214447b6a286a069b8254bd91a237dde50f9` | test: verify gap index arithmetic and immutability | `tests/test_gaps.py` (new) |
| `56df0a830013e6ff5c6a50b58235355cfa654a19` | test: guard gap index arithmetic-only boundary | `tests/test_architecture.py` |
| `fa69733367ee4c340894ff9a0d5d8b6b99f92a6e` | fix: harden gap index producer bounds | `data/gaps.py`, `tests/test_gaps.py` |

No existing Slice 1B–1F runtime contract, v1.1 schema, adapter, dependency,
lockfile or documentation was changed by the implementation.

**Delivered capability — exactly the accepted contract.** One public symbol
`GapIndex`, exported through `data/__init__.py`; arithmetic-only; retains
**discontinuities only**; an explicit required keyword-only `nominal_interval`
bounded to a positive signed int64; four exact `int64` fields `prev_key`,
`next_key`, `delta`, `deviation`; immutable compact columnar storage over
`bytes` with a fresh read-only descriptor per access; **no `boundary_class`**
per OD-G2; no persistence; no caching; no semantic session/calendar claims; and
no data-loading, registry, gating, audit or filesystem authority.

**Independent review: COMPLETE — final verdict APPROVE.** The focused, bounded,
read-only review found **no blocker** and recorded two non-blocking findings,
both since **RESOLVED** by remediation commit
`fa69733367ee4c340894ff9a0d5d8b6b99f92a6e`:

- **N-1 — `nominal_interval` above signed int64 escaped the refusal contract.**
  A positive Python `int` greater than `np.iinfo(np.int64).max` passed producer
  validation and then raised a bare `OverflowError` from NumPy subtraction
  instead of the declared `ContractViolationError`. **Resolved:** the producer
  now enforces `1 <= nominal_interval <= np.iinfo(np.int64).max` and refuses
  oversized values with `ContractViolationError` *before* any NumPy arithmetic.
  Re-review confirmed every previously leaking case now refuses through the
  contract — including 0-row and 1-row frames — while `nominal_interval` equal
  to the int64 maximum is still accepted and yields exact `int64` output.
- **N-2 — `isinstance` admitted `MarketFrame` subclasses.** A subclass could
  present fabricated keys, and non-`int64` fabricated keys produced silent bit
  reinterpretation. **Resolved:** the producer now requires
  `type(frame) is MarketFrame`, matching the repository's existing exact-type
  convention for bulk array carriers (`type(keys) is not np.ndarray`,
  `type(column) is not np.ndarray` in `core/frames.py`). Re-review confirmed a
  subclass is refused **before** its fabricated keys are read, and that
  legitimate loader-issued frames — including `select(())` and
  `slice_by_keys(...)` derivations — are still accepted.

The remediation was minimal: one changed line plus one four-line guard clause in
production code, and three targeted tests. The optional `_Int64Storage` dtype
assertion considered during review was deliberately **not** added, as the
exact-type check already closes the path. The focused re-review confirmed the
remediation altered no public API, output field, representation decision,
OD-G2 outcome, immutability design, capability boundary, architecture test,
dependency or document, and introduced no new blocker.

**Validation at the reviewed head** (owner-approved no-real-data profile):
**47** Slice 1G tests, **81** architecture tests, **741** repository tests with
`tests/test_integrity_real.py` explicitly excluded, plus `ruff check .`, strict
`mypy src tests scripts` and `git diff --check` — all green. **The pre-existing
real TRAIN integration smoke was deliberately excluded; this was not an
unfiltered full-data gate.** Slice 1G required no real-data contact: all tests
use synthetic frames, synthetic manifests, temporary roots and in-memory audit
writers. The immutable dataset root, the five generated view manifests and the
two-record audit log were unchanged throughout implementation and review.

#### Recomputation evidence (§9 documentation duty — 1G)

Measured on **synthetic** production-scale data at the reviewed head; no
production dataset was read.

| Measurement | Observed |
|---|---|
| Synthetic keys | 2,328,717 |
| Discontinuities | 2,200 |
| Retained arithmetic arrays | **70,400 bytes** (2,200 × 4 × 8) |
| Recomputation samples | approximately **2.446 – 4.325 ms** |
| Median | approximately **2.569 ms** |

This is **implementation evidence supporting the qualitative "lazy recomputation
is cheap enough" decision** recorded in owner decision 14 — it is **not a product
SLA**, and no GapIndex performance budget is accepted anywhere in this
repository. Retention is proportional to the discontinuity count rather than the
row count, which is what the memory assertion structurally protects.

The `< 1 s` ceiling in the smoke test is likewise **only a generous, non-flaky
regression smoke** — roughly a 390× margin over the observed median — and is
**not an accepted performance budget**. The deterministic retained-bytes
assertion, not the timing ceiling, is the load-bearing part of that test. Formal
performance qualification against the accepted §8 budgets remains Slice 1H work.

### 1H — Migration, performance qualification, closeout

- **Objective:** retire what can be retired, measure the performance
  budgets for real, and close Phase 1 with updated documentation.
- **Files:** legacy-retirement commit (see the OD-H1 file inventory below);
  `tests/performance/`; documentation updates per §9.
- **Depends on:** 1A–1G.
- **Tests:** the full matrix in §7's cross-reference; formal performance
  tests (§8); an architecture test that the data layer never imports the
  retired carriers.
- **Acceptance:** §11 completion checklist fully checked.
- **Rollback boundary:** the retirement is its own commit, independently
  revertible from the feature work in 1A–1G.
- **Deliberately unbuilt:** anything from Phase 2.

#### OD-H1 — legacy retirement scope (OWNER APPROVED 2026-08-10)

Slice 1H planning established that the obsolete market-data path is one
connected component: `MarketDataProvider` cannot be removed alone, because the
fused pipeline and the experiment runner exist only to drive it, while several
per-row carriers survive only as parameter types of research-layer protocols
that Phase 2 will redefine. The owner ruled on the exact cut.

**RETIRE NOW — the obsolete market-data-provider execution path.**

| Symbol / file | Location |
|---|---|
| `MarketDataProvider` | `core/interfaces.py` |
| `MARKET_DATA_PROVIDERS` | `data/registry.py` (+ its `data/__init__.py` export) |
| `run_pipeline` | `core/pipeline.py` |
| `step` | `core/pipeline.py` |
| `_build_features` | `core/pipeline.py` |
| `run_experiment` | `experiments/runner.py` |
| `experiments/runner.py` | module deleted |
| Exports, tests and synthetic doubles existing **only** to support that path | `core/__init__.py`, `data/__init__.py`, `tests/doubles.py`, `tests/test_contracts.py`, `tests/test_experiments.py`, `tests/test_architecture.py` |

The implementation must remove **only** code whose existence depends on this
obsolete provider-driven path. Breadth beyond that is out of scope.

**KEEP NOW — accepted research-layer contracts that remain useful
independently of the obsolete data-provider path.** These are **not** removed:

`FeatureProvider` · `RepresentationModel` · `ResearchModel` ·
`DecisionPolicy` · `Evaluator` · `check_compatibility`

The accepted separation **`ResearchModel` → `ModelOutput` → `DecisionPolicy`**
is preserved exactly, as ADR-0019 requires.

**EXPLICITLY DEFERRED TO PHASE 2 — scheduled, not endorsed.** These are **not**
retired or redesigned in Slice 1H:

`Bar` · `MarketContext` · `PipelineRecord` · `ResearchInput` ·
`ExperimentConfig` · `ExperimentResult`

They survive solely because the KEEP-NOW protocols currently reference them in
their signatures. They may be retired or retyped **only** when Phase 2 has a
concrete replacement path for those research-layer contracts. This is an
**explicit scheduled deferral, not an endorsement of the legacy data path** —
§11's legacy item is satisfied by "retired **or explicitly scheduled**, with a
test preventing new use", and Slice 1H supplies that test.

**Minimalism rationale.** Slice 1H adds **no** compatibility wrapper for
`MarketDataProvider`, **no** temporary adapter, **no** replacement provider
abstraction, and performs **no** retyping of the research protocols against
`MarketFrame`. All of those are Phase-2 architecture work and are deliberately
out of scope. OD-H1 exists to (1) remove infrastructure that is objectively
obsolete, (2) prevent future bypass of the native-time / `MarketFrame` data
layer, and (3) avoid deleting accepted research contracts before their
replacement exists.

**Corrected Slice 1H file inventory (planning correction, not an architecture
expansion).** The original Files line named only `core/domain.py`,
`core/pipeline.py` and `core/interfaces.py`. The actual dependency graph shows
that the retirement additionally requires bounded changes to:

- `core/__init__.py` — drop retired exports;
- `data/registry.py` — drop `MARKET_DATA_PROVIDERS` and its
  `MarketDataProvider` import;
- `data/__init__.py` — drop the `MARKET_DATA_PROVIDERS` export;
- `experiments/runner.py` — module deleted;
- tests, fixtures and architecture guards directly tied to the retired path.

`core/domain.py` is **not** expected to change under OD-H1, since every symbol
it defines is either KEEP NOW or explicitly deferred.

#### Stage 1H-A — COMPLETE and OWNER APPROVED (2026-08-10)

Stage 1H-A is **COMPLETE, independently reviewed (verdict APPROVE WITH
NON-BLOCKING NOTES), OWNER APPROVED 2026-08-10 and merged into `main`** at
reviewed head `64be3756bc7d4fdff2c5465774c3b5fc26998c71`, fast-forwarded from
`8c4289a7242ff6ed55011847efc5c7cd836d2435` with no merge commit, no squash and
no rebase. Three commits, 18 files, `+409 / −504` — net deletion:

| Commit | Subject |
|---|---|
| `6a93d0b1ad1fae1c3ac09e60cfa99d5fa24e636f` | refactor: retire legacy market data provider path |
| `cf6a367bb8bf3c29f2f24b9a87d7adf61dfe1006` | test: guard retired legacy market data path |
| `64be3756bc7d4fdff2c5465774c3b5fc26998c71` | test: add phase 1 performance qualification harness |

**Minimalism outcome.** Stage 1H-A deleted the obsolete provider-driven path
and **added no replacement provider, shim, adapter or compatibility layer**. It
preserved the accepted `ResearchModel` → `ModelOutput` → `DecisionPolicy`
separation and left the Phase-2 DEFER carriers intact — `core/domain.py` is
byte-unchanged. It added a **test-only, dependency-free TRAIN-only** performance
harness, and added **no** product performance framework, CLI, persistence,
profiling framework, sudo/cache-drop automation, and **no gated performance
route**: no SNAPSHOT, OOS or LOCKBOX identity is representable anywhere in the
harness, which is structurally incapable of the gated ceremony because it
issues no `AccessReason` and no `GatedUnlock`.

**Independent review confirmations.** The reviewer verified by repo-wide search
that every RETIRE NOW symbol is gone; that the KEEP NOW protocols and
`check_compatibility` are semantically unchanged; that `core/domain.py` is
byte-identical; that the new guard is scoped to the data layer only — so it does
not obstruct Phase 2 — and detects aliased imports and `__all__` exports rather
than plain text; and that the three unexpected file changes
(`experiments/__init__.py`, `simulation/__init__.py`, deletion of
`tests/conftest.py`) are necessary mechanical consequences of OD-H1 rather than
scope drift. Pytest selection was **proven empirically by collection**: ordinary
collection is 741/744 with exactly the 3 performance tests deselected;
`-m performance --collect-only` selects exactly those 3; and
`tests/test_integrity_real.py` remains selected by the ordinary gate.

**Validation at the reviewed head:** 84 architecture tests, 37 focused
retirement/contract tests, and `./scripts/gate.sh` green — 741 passed, 3
deselected, Ruff clean, strict mypy over `src tests scripts` (69 files) clean,
`git diff --check` clean. The ordinary gate executed the pre-existing sanctioned
real TRAIN integration on this VM; **no performance test was run**, and no
cold-cache, sudo, SNAPSHOT, OOS or LOCKBOX operation occurred.

*As recorded at Stage 1H-A approval (historical; superseded by the Stage 1H-B
and CP-5 records below):* **Performance qualification remains NOT_STARTED.** The
harness exists but has never been executed against production data. **OD-H2
remains UNRESOLVED** and blocks Stage 1H-B. **CP-5 is NOT_STARTED. Slice 1H and
Phase 1 are NOT complete.**

##### Stage 1H-A review notes (non-blocking, recorded)

None schedules remediation except where stated.

1. **N-1 — test-double live exercise reduced.** After retiring the obsolete
   composition path, some test-double methods (`RuleModel.infer` positive path,
   `FeatureVectorEncoder.encode`, `PriceFeatures.compute`,
   `SignalStatistics.evaluate`) are no longer exercised end-to-end. This affects
   **test doubles, not production contracts**: surviving production helpers
   retain coverage (`FeatureSet.merged_with` in `tests/test_domain.py`,
   `ThresholdPolicy.decide` in `tests/test_decisions.py`). **Do not recreate an
   artificial orchestrator in Slice 1H to restore it.** Real composition
   coverage returns when Phase 2 introduces the accepted orchestrator.
2. **N-2 — per-layer registry exercise reduced.** `FEATURE_PROVIDERS`,
   `REPRESENTATION_MODELS`, `DECISION_POLICIES` and `EVALUATORS` lost the
   experiment-runner-driven exercise that populated them at import time. The
   generic `Registry` remains tested in `tests/test_registry.py`. **No synthetic
   consumer should be added merely to increase coverage**; revisit naturally when
   Phase 2 creates real consumers.
3. **N-3 — RSS baseline not reported.** The measurement child reports absolute
   peak RSS, which **is** the accepted §8 budget quantity, but reports no
   pre-load process baseline. Recorded as a useful **1H-B evidence refinement**:
   before qualification runs, decide the smallest way to report a baseline if it
   materially improves interpretability. **The harness is not modified by this
   closeout.**
4. **N-4 — warm measurement is conservative.** The warm timing region includes
   work beyond §8's narrow wording — split-policy read, registry discovery over
   both roots, and authorization — so it is a **conservative superset**, and a
   passing result is therefore **at least as strong** as the accepted budget
   requires. The 1H-B result **must state this measurement boundary alongside
   the number**. Do not optimize or narrow the region during closeout.
5. **N-5 — `simulation/__init__.py` prose cleanup.** The small docstring change
   merely removed a stale reference to the retired
   `trading_research.core.pipeline.step` and has **zero behavioural effect**.

#### OD-H2 — fully-gated SNAPSHOT qualification (OWNER APPROVED 2026-08-10)

**Decision.** The owner authorizes Phase-1 infrastructure qualification against
the exact published SNAPSHOT under the **existing** FULLY_GATED access
contract. The purpose is strictly **integrity verification, load timing, peak
RSS measurement, and the row-count/schema confirmation qualification requires**.

**This is not research use.** No feature, model, representation, decision
policy, evaluator, return calculation, volatility calculation, gap analysis or
any other market-data interpretation is permitted. **No value-column contents
may be printed, summarized, persisted or inspected as research evidence.** The
frame is loaded, structurally checked, measured and discarded.

**Exact target — verified from the repository on 2026-08-10.**

| Fact | Verified value |
|---|---|
| View id | `XAUUSD_S_M1_SNAPSHOT_V1` |
| Manifest SHA-256 | `d5b1724cb204699d4e8c3a45bebffa39d080a5260d285610f50981f6ae7eb600` (recomputed from the published bytes) |
| Declared role | `snapshot`, `discovery_default: false`, 7 admitted partitions |
| Rows | **2,328,717** |
| Admitted range | `[1577926800, 1785542220]` |
| Classification | **FULLY_GATED** — `1785542220 > 1767225540` (policy OOS end) ⇒ basis `range_exceeds_oos_boundary`; range-derived, not role-name trust |

**No substitution is permitted:** not RAW, not CURATED, not OOS, not LOCKBOX,
not an ephemeral full-range TRAIN target, and not a regenerated identity. If the
observed identity differs at execution time, **STOP** and report.

**Authorization contract — existing mechanism only.** Exact `DatasetRef` →
`AccessReason` → identity-bound `GatedUnlock` → `authorize_access` → issued
`AccessGrant` → `MarketFrameLoader.load`. **No new access API and no bypass.**

- Reason code: **`phase1h-performance-qualification`**
- Reason detail: *"Phase 1 infrastructure qualification of the published
  SNAPSHOT: integrity verification, load timing and peak RSS only. Not research
  or evaluation use. Load, measure, discard — no value-column content is
  inspected, derived or retained."*

This is one approved reason code for one approved operation. It must **not**
grow into a configurable reason system.

##### Audit cardinality — resolved from the code, not assumed

Verified in `data/gating.py` and `data/integrity.py`:

- `authorize_access` appends **exactly one** audit event per call for any
  non-NORMAL classification (allowed or denied); NORMAL appends none and issues
  a grant with `audit_event_id=None`.
- `MarketFrameLoader.load` **never** classifies, authorizes, audits or issues a
  grant — its docstring and the Slice 1D architecture guard both pin this.
- `AccessGrant` carries no expiry and no single-use flag; `require_matches`
  checks only exact identity and split-policy bytes. **One grant therefore
  serves unlimited loads within the process that obtained it.**
- A grant is an in-process object holding a `RegisteredManifest`. It is **not**
  serializable across processes, so **each OS process that loads gated data must
  perform its own `authorize_access` and therefore appends its own audit event.**

That last point is the whole design constraint: **audit events scale with
processes, not with loads.**

**Analysis of the three measurements.**

1. **Warm timing** — one discarded warm-up plus five measured loads. All six
   loads reuse **one** grant in **one** process. **Audit delta: +1.**
2. **SNAPSHOT RSS** — the accepted quantity is absolute peak process RSS while
   holding the full frame. A dedicated child process would give the cleanest
   number but costs a **second** authorization and audit event. The warm process
   already holds a full frame at its peak, so its own `ru_maxrss` satisfies the
   §8 definition; because that process performed six sequential loads, its peak
   is a **conservative upper bound** (allocator reuse and transient overlap can
   only raise it). A pass at the inflated figure is therefore strictly stronger
   than a pass at a clean figure. **Recommended: measure RSS in the same
   authorized process. Audit delta: +0 additional.**
3. **Cold qualification** — five genuine cold repetitions each need a manual
   cache drop immediately beforehand. Separate process invocations would cost
   **+5** audit events. **Because the grant is process-local and reusable, one
   long-lived authorized process can perform all five** while the operator drops
   caches externally, at **+0 additional** audit events. That requires a bounded
   operator handshake, which is justified here — see the reconciliation below.
   **SNAPSHOT cold is part of Stage 1H-B and is not substituted by TRAIN cold.**

> **Reconciliation with §8 (recorded 2026-08-10).** An earlier draft of this
> runbook proposed qualifying cold on TRAIN only and deferring the full-set cold
> headline, on the grounds that five SNAPSHOT process invocations would cost five
> audit events. **That contradicted §8**, whose accepted scope makes the full
> admitted set the source of the **headline warm, cold and RSS** numbers, with
> TRAIN reported separately as the common case. **Audit records are integrity
> evidence, not pollution, and extra audit complexity is not a reason to narrow
> an accepted qualification scope.** The corrected contract restores SNAPSHOT
> cold to the headline matrix, and the one-process topology below keeps the total
> delta at **+1** without weakening anything. This is an implementation
> clarification of OD-H2's runbook; the §8 contract itself is unchanged.

**Corrected qualification matrix.**

| Class | Measurements |
|---|---|
| **Headline** (full admitted set) | SNAPSHOT warm · SNAPSHOT **cold** · SNAPSHOT RSS |
| **Common case** | TRAIN warm · TRAIN cold · TRAIN RSS |
| **Selective loading** | TRAIN `("close", "spread")` RSS |

**Recommended minimum-total-audit procedure.**

| Stage | Target | Classification | Authorizations | Expected audit delta |
|---|---|---|---|---|
| Preflight (metadata/fingerprint only) | — | — | 0 | **+0** |
| TRAIN warm + cold + RSS + 2-column | `XAUUSD_S_M1_TRAIN_V1` | NORMAL | 1 per process, unaudited | **+0** |
| **SNAPSHOT RSS + warm (1+5) + cold (5) + integrity** | `XAUUSD_S_M1_SNAPSHOT_V1` | **FULLY_GATED** | **1, reused for every load** | **+1** |

> **Audit line count before qualification = 2.**
> **Expected audit line count after the complete approved qualification = 3.**
> The single new record must be `dataset_access_decision`, outcome `allowed`,
> classification `fully_gated`, basis `range_exceeds_oos_boundary`, reason code
> `phase1h-performance-qualification`.

**Execution must STOP if observed audit cardinality differs from this
expectation**, before recording any number.

If the owner later wants the full-set **cold** headline as well, it costs **+5**
further audit events (or +1 with an operator handshake). That is a separate
decision and is **deferred**, not approved here. Audit accuracy outranks
minimizing the count: no persistent grant, grant serialization, authorization
daemon, IPC service or interactive benchmark framework may be invented to
reduce it.

##### Value-inspection prohibition (Stage-B guard)

Stage 1H-B must carry an architecture guard proving the SNAPSHOT qualification
code can emit **only** operational facts:

*permitted* — elapsed time · absolute peak RSS · optional pre-load RSS baseline ·
row count · column names · identity / manifest hash / integrity status.

*forbidden* — printing ndarray values; computing `min`/`max`/`mean`/`std` or any
statistic over a value column; inspecting open/high/low/close values;
calculating returns, volatility or gaps; creating any derived market statistic;
persisting a `MarketFrame` or any value array.

##### N-3 resolution — RSS baseline

`ru_maxrss` is itself a **high-water mark**, so a sample taken before the load
is not an instantaneous reading: it is the process's peak over startup, imports
and registry/policy resolution. Because nothing frame-sized has been allocated
by then, that startup high-water is a faithful and sufficient baseline, and
using the same primitive avoids introducing a second measurement mechanism.

**Resolution:** the measurement child reports **both** `ru_maxrss` sampled
immediately before the load — labelled as the pre-load high-water baseline
(interpreter, imports and metadata resolution) — and the absolute post-load peak
while the frame is still referenced. **The accepted `< 1.0 GiB` budget continues
to apply to the absolute peak, never to the difference.** The baseline is
interpretability evidence only. No `psutil`, no `/proc` parsing, no change to
budget semantics.

##### Timed-region boundary — N-4 resolution, corrected for manifest parse

§8 defines warm and cold as **manifest parse + hash verify + Parquet decode +
`MarketFrame` construction + ordering validation**, excluding process startup.
Two facts, both verified from the code, fix the boundary exactly:

- **`DatasetRegistry.discover(roots)` is where manifest parse happens.** It
  `rglob`s each root for `manifest.json` and calls `read_manifest(path)` per
  file, recomputing `manifest_sha256` from the bytes.
- **`MarketFrameLoader.load` does not reparse.** `DatasetRegistry.resolve` is a
  dictionary lookup over already-parsed entries.

An earlier draft timed only repeated `loader.load(grant, …)` after a single
up-front discovery. **That omitted manifest parse from the measured region and
is corrected here.**

**Corrected timed region — each repetition performs:**

```
read_split_policy(POLICY_PATH)                 # exact policy bytes re-read
DatasetRegistry.discover([DATASET_ROOT, VIEWS_ROOT])   # manifest parse + digest recompute
MarketFrameLoader(registry=…, split_policy=…).load(grant, …)
```

**The authorization ceremony and its audit `fsync` remain outside the timed
region, and the already-issued grant is reused unchanged.** An audit `fsync` is
the durability cost of an authorization *decision*, not Parquet-load
performance; it happens once regardless of repetition count, so timing it would
measure disk-sync latency as though it were load cost. §8 does not ask for it.
**Authorization is never repeated merely to force manifest parsing.**

##### Grant compatibility with a re-discovered registry — proven from code

The timed region rebuilds the registry, so the grant must still validate against
it. `MarketFrameLoader.load` calls
`grant.require_matches(registered_manifest=candidate, split_policy_identity=self._policy.identity)`,
and `require_matches`:

```python
candidate_ref = _dataset_ref_for(registered_manifest.manifest)   # fresh DatasetRef
if candidate_ref != self.dataset_ref: raise AccessGrantMismatchError(...)
if split_policy_identity != self.split_policy_identity: raise AccessGrantMismatchError(...)
```

**Both comparisons are value equality, never object identity:**

- `_dataset_ref_for` constructs a fresh `DatasetRef(dataset_id,
  manifest_sha256, role)`; `DatasetRef` is a frozen slotted dataclass whose
  docstring states equality is "ordinary Python value equality over **all
  three** fields".
- `SplitPolicyIdentity` is a frozen dataclass over `(adr, policy_version,
  policy_sha256)`, so a re-read policy compares equal when the bytes are equal.

**Conclusion: a freshly discovered registry over unchanged bytes yields an equal
`DatasetRef`, and a freshly read policy yields an equal `SplitPolicyIdentity`, so
the previously issued grant validates without reauthorization.** `MarketFrameLoader.__init__`
merely stores the two references, so wrapping the fresh registry is free.

This is not a loophole — it is the pin working: if any manifest byte or policy
byte changed between repetitions, the recomputed digest would differ and
`require_matches` would **refuse**. Reparsing per repetition therefore adds
integrity re-verification rather than weakening it.

##### Full-set warm procedure (pinned)

Authorization already completed outside the timer · pre-load RSS baseline
recorded · **one** full SNAPSHOT warm-up load with RSS sampled while that first
frame is alive · warm-up frame discarded · **five** measured repetitions ·
**median and minimum** reported · headline warm budget **< 2.0 s** · **the median
is the pass/fail statistic**.

Every repetition executes the complete §8 path: policy re-read, **manifest parse
with digest recomputation**, grant validation, **full SHA-256 verification of all
seven admitted partitions**, Parquet decode, admitted-slice validation and
`MarketFrame` construction. Verified from the code: `data/integrity.py` contains
no `lru_cache`, no memoization and no cached-integrity mode, and it re-hashes
each partition after decode — so no repetition can measure less than the accepted
warm definition. **No integrity caching mode may be added** (ADR-0004's deferral
stands).

**TRAIN warm must use this identical timed-region boundary** so the headline and
common-case numbers are comparable, and the recorded result must state the
boundary alongside the number.

##### Cold procedure clarification

Cache manipulation remains **manual and outside pytest**:

```bash
sync
echo 3 | sudo tee /proc/sys/vm/drop_caches
```

**Final interpretation:** §8's **"5 repetitions after 1 discarded warm-up"** is
one common repetition rule, and it is **satisfied for cold as well as warm**. An
earlier draft claimed the discarded-trial rule applied only to warm measurement;
**that claim is withdrawn.** Cold satisfies the rule with **one discarded *cold*
trial** followed by **five measured cold repetitions** — **six cold trials in
total, each immediately preceded by its own manual page-cache drop**.

The discarded trial is itself genuinely cold and cannot warm what follows: the
operator drops caches, one full cold load executes, its result is discarded, and
**caches are dropped again before measured repetition #1**. This is recorded as
an **implementation clarification, not an architecture change**.

- The discarded cold trial is **excluded** from the statistics.
- The **five measured** trials determine the reported **median and minimum**.
- **The median remains the pass/fail statistic**; headline cold budget
  **< 5.0 s**.
- Every cold trial uses the **same corrected timed region** as warm (policy
  re-read, manifest parse, grant validation, full hash verify, decode, validate,
  construct).

The same one-discarded-plus-five rule applies to **TRAIN common-case cold**.

**Cold process/operator topology.** `drop_caches` evicts clean page cache and
does not touch a process's own anonymous memory, so the authorized SNAPSHOT
process can stay alive across all **six** drops. Because the grant is
process-local and reusable, keeping that one process alive is what holds the
audit delta at **+1**; **the extra discarded cold trial requires no new
authorization.** The bounded handshake runs at **six checkpoints** — discarded
cold trial, then measured cold #1 … #5:

1. the authorized process reaches a clearly labelled pre-cold checkpoint and
   **blocks on stdin**, printing which trial is next and whether it is the
   discarded trial or a measured repetition;
2. the operator, in a second terminal, runs `sync` then
   `echo 3 | sudo tee /proc/sys/vm/drop_caches`;
3. the operator presses **Enter** to release the process;
4. exactly one cold timed load executes;
5. repeat until the discarded trial and all five measured repetitions are done.

This is **not** a generic interactive benchmark framework. It is a single
blocking stdin read, justified solely because cold-cache control must remain
manual, the grant is process-local, and the accepted full-set cold measurement
must remain honest. **`sudo` is never automated and no cache-drop command is
hidden inside pytest.**

If `sudo` is unavailable at execution time, record exactly
**`not measured on this machine — sudo unavailable`** (owner decision 11), skip
the cold block entirely, and **do not fabricate a cold result**. That does not
block closing Phase 1.

##### RSS procedure (pinned)

Absolute **peak process RSS while holding the loaded frame**, `ru_maxrss × 1024`
bytes on Linux only, compared against **1.0 GiB**:

| Configuration | Target | Process |
|---|---|---|
| SNAPSHOT, full columns | `XAUUSD_S_M1_SNAPSHOT_V1` | the single authorized SNAPSHOT process |
| TRAIN, full columns | `XAUUSD_S_M1_TRAIN_V1` | its own child (NORMAL, unaudited) |
| TRAIN, `("close", "spread")` | `XAUUSD_S_M1_TRAIN_V1` | its own child (NORMAL, unaudited) |

**SNAPSHOT RSS is sampled on the first full load — the warm-up — while that
frame is still referenced**, before any repeated warm or cold load can inflate
the process high-water unnecessarily. Recorded together: the **pre-load
`ru_maxrss` high-water baseline** and the **absolute peak** after the first full
frame is constructed and still alive.

**No incremental-memory budget is introduced**; the `< 1.0 GiB` budget applies to
the absolute peak, never to the difference, and the baseline is interpretability
evidence only. The two-column comparison is **evidence that selective loading
materially reduces memory**, not a new product SLA.

##### Integrity evidence

A successful full SNAPSHOT load contributes directly to §11's dataset-integrity
item, because the production loader SHA-256-verifies **every** target-listed
partition against its manifest-pinned digest before decode, and re-verifies
after. Recorded evidence is therefore: the exact published manifest identity is
unchanged; the reproducible D-6 metadata fingerprint is unchanged; and the
authorized full load verified all admitted payload bytes against the
already-pinned hashes.

This is **not** a newly created Phase-0 baseline digest, and it must **never** be
compared with the historical undocumented `4637ca9d…` value (CP-4 note 3).
**OD-H4 remains a separate closeout wording decision** unless this evidence is
judged to resolve the §11 ambiguity fully under the accepted plan.

##### Performance-miss protocol

If a **median** budget is missed: record the result; collect **only** the already
approved diagnostics — `cProfile`, `tracemalloc`, and a verify / decode /
validate / construct timing breakdown; **STOP**; report to the owner. **Do not
optimize automatically.** OD-H3 is not resolved prospectively.

##### Execution order (runbook)

1. **Immutable-state preflight** — metadata only: recompute the D-6 fingerprint,
   the RAW/CURATED/SNAPSHOT manifest digests, and record
   **audit line count = 2**. No authorization, no row access.
2. **Ordinary TRAIN qualification** — warm, RSS (full and two-column), and cold
   ×5 with manual drops if `sudo` is available. NORMAL access throughout, so
   separate process invocations are free.
3. **Confirm zero audit delta from TRAIN** — still 2 lines. If not, **STOP**.
4. **Fully-gated SNAPSHOT authorization** — exactly one `authorize_access` with
   the approved reason and identity-bound unlock, **outside every timed region**.
   This process now stays alive until step 8.
5. **SNAPSHOT RSS** — record the pre-load `ru_maxrss` baseline, perform the
   warm-up load, sample the absolute peak while that first frame is alive, then
   discard the frame.
6. **SNAPSHOT warm** — five timed repetitions on the same grant, each performing
   the corrected timed region (policy re-read, manifest parse, grant validation,
   hash verify, decode, validate, construct). Report median and minimum.
7. **SNAPSHOT cold** — if `sudo` is available, **one discarded cold trial then
   five measured cold repetitions** in the **same** process, each of the six
   preceded by the operator's manual `sync` + `drop_caches` released through the
   stdin handshake. Report median and minimum over the five measured trials
   only. If `sudo` is unavailable, record the accepted phrase and skip.
8. **Discard every frame and exit** the authorized process holding nothing.
9. **Validate the exact audit delta: 2 → 3**, with the expected record fields.
   Any other cardinality ⇒ **STOP** before recording numbers.
10. **Verify the generated views root and dataset fingerprint are unchanged.**
11. **Record the numbers** only after the run completes cleanly.

Steps 4–8 are one process holding one grant, which is what keeps the whole
headline suite — RSS, one warm-up plus five warm loads, and one discarded cold
trial plus five measured cold loads — at a single audit event. Steps 3 and 9
bracket the only irreversible action of the stage. RSS is deliberately sampled at
step 5, before the twelve subsequent loads of steps 6–7 can raise the process
high-water.

**CP-2 non-blocking findings to close out in Slice 1H documentation.**

- **N-3:** `JsonlAuditWriter` validates the existing log tail before append;
  it does not claim to verify every historical audit record on every append.
  This is the accepted V1 hot-path trade-off. If full-log
  verification/reconciliation is desired, provide explicit audit tooling
  later rather than putting it on the hot append path. Slice 1H must state
  this limitation clearly.
- **N-4:** if cleanup fails after the event has been fsynced, an `allowed`
  audit record can conservatively remain even though no `AccessGrant` was
  returned. An `allowed` audit line is therefore not proof that a grant was
  returned or that any row/partition was opened. Slice 1H must document that
  audit-log interpretation explicitly.
- **N-5:** the five-second audit-lock timeout is sufficient for V1. Reconsider
  it only if measured later contention justifies a change.

#### Stage 1H-B execution outcome — COMPLETE (2026-08-10)

The one owner-approved qualification was executed exactly once at code identity
`ad1e507a3577b377317d4c68d029454479d43d7a`. Measured numbers are recorded in §8.
A memory-evidence remediation preceded it: the first attempt on 2026-08-09
showed that absolute `ru_maxrss` cannot discriminate column selections, so a
separate `retained_payload_bytes` evidence metric was added (tests only; the
§8 RSS budget and its absolute-peak semantics were **not** changed).

**Audit effect — exactly the approved 2 → 3.** The first two records are
byte-identical to their pre-execution state (`sha256` of the first two lines
equals the pre-execution whole-file digest
`2d98f815fa2ce7f1d5db58fce0ab393d9ba764cc75d42be05d32b574e190e0e0`), mode
remains `0600`, and exactly one record was appended:

| Field | Value |
|---|---|
| `event_id` | `6afe71e2-be34-4cd5-b32f-b2f60d6e9ef7` |
| `event_type` / `outcome` | `dataset_access_decision` / `allowed` |
| `classification` / basis | `fully_gated` / `range_exceeds_oos_boundary` |
| `dataset_ref` | `XAUUSD_S_M1_SNAPSHOT_V1` / `d5b1724c…eb600` / `snapshot` |
| reason | `phase1h-performance-qualification` + the approved detail |
| unlock | present, bound to the exact SNAPSHOT canonical identity |
| caller | `masterpiece` @ `masterpiecetradingresearch`, pid 350230, uuid4 session |
| `platform.git_commit` / dirty | `ad1e507a3577b377317d4c68d029454479d43d7a` / `False` |
| `event_time_utc` | `2026-08-10T17:45:56.497769Z` |
| split policy | ADR-0012 / v1 / `b7e0f582…05eb` |

**No research use occurred.** Only identity, row count, column names, timings,
RSS and payload sizes were observed. No value column was read in the gated
path, no statistic, return, volatility or gap was computed, and no frame or
value array was persisted. TRAIN measurements used ordinary NORMAL access and
appended **no** audit record.

#### OD-H4 — §11 dataset-integrity evidence (resolved 2026-08-10)

The three required facts were verified after the successful qualification:

1. **Published manifest identities unchanged** — RAW `8f91f451…13cb0`, CURATED
   `6f5c7b50…f0da2`, SNAPSHOT `d5b1724c…eb600`, TRAIN `30ca7b4d…f374e`, and the
   generated-views fingerprint `8da4bd3b…a4b1`, all identical before and after.
2. **D-6 metadata fingerprint unchanged** —
   `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`, by the
   documented command.
3. **Production loader verified every SNAPSHOT target-listed partition** — the
   loader SHA-256-hashes all admitted partitions before any decode
   (`_verify_all`) and re-hashes each after decode
   (`_require_unchanged_after_decode`); the qualification loaded all seven
   partitions and 2,328,717 rows successfully six times, so every admitted
   payload byte was verified against its already-pinned manifest hash.

**§11's dataset-integrity item is therefore satisfied by:** unchanged
manifest-pinned identities, the unchanged reproducible D-6 metadata
fingerprint, and successful full production-loader payload verification.

**This is explicitly NOT a new Phase-0 baseline digest**, and it **must not be
compared** with the historic undocumented `4637ca9d…` value (CP-4 note 3).

#### CP-2 audit notes N-3 / N-4 / N-5 — closed as accepted V1 limitations

- **N-3 — tail validation, not full-history verification.** `JsonlAuditWriter`
  validates the existing log tail immediately before appending; it does **not**
  verify every historical record on every append. This is the accepted V1
  hot-path trade-off, since full verification would make audit cost grow with
  history. Whole-log reconciliation, if ever wanted, belongs in separate
  explicit tooling that V1 deliberately does not ship.
- **N-4 — an `allowed` record proves a decision, not a read.** The append is
  write-ahead: the record is fsynced *before* any grant is issued or partition
  opened. If a later step fails, an `allowed` record can remain although no
  grant was returned and no byte was read. **Interpret the log as a record of
  authorization decisions, never as proof that data was delivered.**
- **N-5 — five-second audit-lock timeout.** Sufficient for V1's single-operator,
  low-concurrency use. Reconsider only if measured contention justifies it; no
  configurable timeout system exists or is planned for V1.

## 7. Dependencies and test expectations

Slice dependency chain is linear: 1A → 1B → 1C → 1D → 1E → {1F, 1G in
parallel, both need 1D/1E and 1C respectively} → 1H needs all prior slices.

**Test kinds**, matching the planning report's 40-item matrix (preserved in
full in the review record — not reproduced here to avoid duplication):

- **Fast unit + property tests** — default `pytest` gate, synthetic Parquet
  fixtures, no dataset dependency.
- **Architecture tests** — extend `tests/test_architecture.py`: no
  directory globbing; no PyArrow/pandas/Polars/DuckDB types in public
  signatures; no `utc` identifier in `core`/`data`; no session vocabulary in
  `data/gaps.py`; data layer never imports retired legacy carriers.
- **Integration tests** — real manifests, TRAIN-period real data only;
  marked, skip cleanly if the dataset root is absent.
- **Real-dataset tests** — end-to-end load and verify against the actual
  curated dataset (TRAIN/VALIDATION only; gated-path tests use synthetic
  fixtures so the suite never performs a lockbox ceremony as a side effect
  of running `pytest`).
- **Performance tests** — `tests/performance/`, marked
  `@pytest.mark.performance`, **deselected by default** in
  `pyproject.toml` addopts (§8).

## 8. Performance budgets

Accepted budgets (ADR-0003, roadmap): warm full-frame load < 2 s; cold load
with full hash verification < 5 s; loaded-frame RSS < 1.0 GiB.

- **Warm** includes manifest parse + hash verify + Parquet decode +
  `MarketFrame` construction + ordering validation, page cache warm.
  Excludes process startup and imports.
- **Cold** is the same with the page cache dropped — a **manual,
  documented, one-off procedure** (`sync; echo 3 | sudo tee
  /proc/sys/vm/drop_caches`) run at Phase 1 qualification (owner decision
  11). If sudo is unavailable when 1H is executed, the result is recorded
  as **"not measured on this machine — sudo unavailable"**, honestly, and
  does not block closing Phase 1.
- **RSS** is peak RSS of a process holding one full admitted frame, all
  columns except `timestamp_mt5_encoded`.
- Scope: the full admitted set (2.33 M rows, ~59 MB Parquet) for the
  headline numbers; TRAIN alone reported as the common case; a 2-column
  selection reported separately to demonstrate selective loading actually
  reduces memory.
- 5 repetitions after 1 discarded warm-up; report median and min; a budget
  is missed only if the **median** exceeds it.
- Before optimizing a miss: capture `cProfile` + `tracemalloc` and a
  per-stage timing breakdown (verify / decode / validate / construct). No
  speculative optimization.

### Measured results — Stage 1H-B, executed 2026-08-10

Executed once at code identity `ad1e507a3577b377317d4c68d029454479d43d7a`
(HEAD = main = origin/main, clean worktree). All timed repetitions used the
final §8 region: exact split-policy read, registry re-discovery with manifest
parse and digest recomputation, loader construction, grant validation, full
SHA-256 verification of every target-listed partition, Parquet decode,
admitted-slice validation and `MarketFrame` construction — with authorization
and its audit `fsync` outside the timed region.

**Headline — published SNAPSHOT `XAUUSD_S_M1_SNAPSHOT_V1`
(`d5b1724c…eb600`), 2,328,717 rows, 7 value columns.**

| Metric | Result | Budget | Verdict |
|---|---|---|---|
| Absolute peak process RSS | **592,191,488 B** (564.8 MiB) | < 1.0 GiB | **PASS** |
| Pre-load RSS high-water baseline | 96,092,160 B (91.6 MiB) | — | interpretability only |
| Warm samples (s) | 0.264042839, 0.266729258, 0.265468449, 0.317522212, 0.265562650 | — | — |
| Warm **median** | **0.265562650 s** | < 2.0 s | **PASS** |
| Warm minimum | 0.264042839 s | — | — |
| Cold | `not measured on this machine — sudo unavailable` | < 5.0 s | honestly unmeasured (owner decision 11) |

**Common case — published TRAIN `XAUUSD_S_M1_TRAIN_V1` (`30ca7b4d…f374e`),
1,414,538 rows.**

| Metric | Full columns | `("close", "spread")` |
|---|---|---|
| Pre-load RSS high-water baseline | 464,871,424 B | 464,871,424 B |
| Absolute peak process RSS | **464,871,424 B** (443.3 MiB) — **PASS** | **464,871,424 B** — **PASS** |
| **Retained frame payload** | **84,872,280 B** (80.9 MiB) | **28,290,760 B** (27.0 MiB) — **3.0× lower** |
| Load elapsed (s) | 0.181433 | 0.102381 |
| Warm median / min | **0.142175 s** / 0.141786 s — **PASS** | — |
| Cold | `not measured on this machine — sudo unavailable` | — |

**Two different metrics, deliberately.** *Absolute process RSS* is the accepted
§8 process-memory budget — it answers "does a process holding one full admitted
frame stay below 1 GiB". *Retained frame payload* is **selective-loading
evidence only**: the exact `nbytes` of the immutable native-key buffer plus each
materialized value column of the actually loaded frame. It reads no market
element value, computes no market statistic, copies no payload, and **is not
RSS**. It is **not a second product SLA** and no budget is attached to it.

The TRAIN process high-water plateau (~443 MiB, identical pre-load and peak and
identical across both column selections) is an artefact of interpreter, NumPy
and PyArrow import cost exceeding the frame cost. It is a valid absolute
process-budget reading and **not** a defect for that criterion; it simply cannot
discriminate column selections, which is exactly why retained payload exists.
The SNAPSHOT process, whose baseline was sampled before those imports were paid,
shows the expected rise from 91.6 MiB to 564.8 MiB.

**Cold measurement.** Not performed. `sudo` on this machine requires interactive
authentication (`sudo: interactive authentication is required`), the executing
account is uid 1000, and `/proc/sys/vm/drop_caches` is root-writable only — so
page-cache dropping could not be executed. The accepted owner-decision-11
fallback was used at the first cold checkpoint for both TRAIN and SNAPSHOT;
**no cold number was fabricated**, and per §8 this does not block closing
Phase 1.

## 9. Documentation duties per slice

Every slice updates documentation as part of being "done" (owner decision
16) — not as a separate cleanup pass:

| Slice | Documentation duty |
|---|---|
| 1A | This file: mark 1A complete with commit hash. ✅ (`89a1650`) |
| 1B | This file: record registry root configuration mechanism actually used. ✅ (`302721a`) — roots are passed explicitly to `DatasetRegistry.discover(roots)`; no env var, no config file, no cwd fallback, and a missing root is never created. |
| 1C | This file: record the audit event schema actually implemented (field list). ✅ (`aaf1dba`) CLAUDE.local.md already names `/path/to/research-runs/audit.jsonl`; the file is intentionally absent until the first real audited access. |
| 1D | This file: record actual PyArrow/NumPy versions pinned. `pyproject.toml`/`uv.lock` changes are their own reviewable commit (§ dependency addition). **Done:** NumPy 2.5.1 / PyArrow 25.0.0 (`144dbae`); implementation `76f5586`. |
| 1E | This file: record any refinement to the context/union rules discovered during implementation (a mismatch with the approved OD-1…OD-6 contracts is an **architecture exception**, not a silent doc edit). **Done and owner-approved (2026-08-08):** implementation and focused review evidence are recorded above; verdict APPROVE; no approved contract required refinement or exception; reviewed head `84bee8187a72aa361f3b9b986bf84b4d00a63818` is merged into `main`. |
| 1F | **PROJECT_STATE.md**: record the five view manifest hashes and the bound snapshot key in "Verified dataset identities". ✅ **Done (2026-08-09):** the five manifest hashes and bound key `1785542220` are recorded there; the views root `/path/to/research-views/` is live. **Pre-implementation duty done (2026-08-09):** owner decisions OD-F1…OD-F6 (including the OD-F6 resume contract and the Stage-A no-real-data validation profile), complete column preservation, multi-dataset generality, file-layout independence, the one keys-only fresh-generation read, the resume identity/provenance contract, the non-empty-view precondition, and the Stage A/B separation are recorded in the Slice 1F section above. **Stage-A duty done (2026-08-09):** implementation, independent-review outcome (APPROVE WITH NON-BLOCKING NOTES), the four non-blocking notes, and owner approval at reviewed head `79bac876f84bcc915532b9bad246e74200e67041` are recorded above. **Stage-B preparation duty done (2026-08-09):** owner-approved production decisions D-1…D-6 and operational refinements R-1…R-3 — production view ids, generator name/version and the `platform_git_commit` rule, the CURATED access reason, the two-mode script interface, the post-confirmation real-smoke placement, the empty-views-root preflight semantics, the complete-bundle early exit, the canonical fingerprint command, and the fresh-production confirmation boundary — are recorded in the Slice 1F section above. **Stage-B operational-script duty done / review pending (2026-08-09):** the maintained runbook now records the implemented commands, preflight side effect, timestamp procedure, fresh/OD-F6 resume/R-3 complete sequences, crash-retry audit note, exact D-6 method and expected CP-4 evidence output. **Stage-B production duty done (2026-08-09 / 2026-08-10):** Stage B executed in fresh mode at execution identity `f8b6168511fa2544479c0ad96a16c50cf0ab7890`; the five manifest hashes and bound key `1785542220` are recorded in PROJECT_STATE.md; the views root `/path/to/research-views/` is live and confirmed in CLAUDE.local.md; and CP-4 was owner-approved on 2026-08-10. **All Slice 1F documentation duties are complete.** |
| 1G | This file: record GapIndex recomputation cost observed. **Pre-implementation duty done (2026-08-10):** the owner-approved Slice 1G implementation contract — arithmetic-only mandate, OD-G2 `boundary_class` deferral, the single-public-symbol API, the explicit nominal-interval input, the gaps-only representation, the four arithmetic fields with dtypes/shapes, the exact formulas, empty/small-frame behaviour, immutable compact NumPy representation, no persistence and no caching, the capability boundary, the semantic-vocabulary and manifest-label prohibitions, and the synthetic performance-measurement rule — is recorded in the Slice 1G section above, with the OD-G2 clarification in [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md). **Implementation duty done (2026-08-10):** the observed synthetic production-scale recomputation evidence — 2,328,717 keys, 2,200 discontinuities, 70,400 retained bytes, samples ≈2.446–4.325 ms, median ≈2.569 ms — is recorded in "Recomputation evidence" in the Slice 1G section above, explicitly as implementation evidence for the qualitative "lazy recomputation is cheap enough" call and **not** as a product SLA, with the `< 1 s` test ceiling recorded as a generous non-flaky regression smoke rather than an accepted budget. The implementation outcome, the four reviewed commits, the APPROVE verdict and the resolved N-1/N-2 findings are recorded there as well. **All Slice 1G documentation duties are complete.** |
| 1H | PROJECT_STATE.md, V1_ROADMAP.md: Phase 1 → COMPLETE, Phase 2 → NEXT. This file: fill in §8's measured numbers and §11's checklist. Independent review scheduled per §10. **Done (2026-08-10):** OD-H1 retirement scope and Stage 1H-A recorded; OD-H2 and the final Stage 1H-B runbook recorded; Stage 1H-B executed once at `ad1e507a3577b377317d4c68d029454479d43d7a` with §8 measured results, the exact 2→3 audit effect, OD-H4 dataset-integrity resolution and the CP-2 N-3/N-4/N-5 closures all recorded above; §11 verified item by item with named evidence. CP-5 is the remaining item. |

## 10. Review checkpoints

| Checkpoint | After | What the owner reviews |
|---|---|---|
| **CP-1** | 1B | Manifest v1.1 field set and the reader-adapter approach, before any generated artifact depends on it. **APPROVED** (2026-08-07) after five targeted schema corrections (`e831032`) — see the CP-1 record in §6. |
| **CP-2** | 1C | Independent review **COMPLETE AND APPROVED**; the single AccessGrant-to-loader documentation blocker was resolved and Slice 1C was merged to `main`. |
| **CP-3** | 1D | Independent bounded review **COMPLETE**; runtime Slice 1D contract approved; closed ten-mutation architecture-test contract satisfied; **APPROVED by owner on 2026-08-08** at reviewed head `2e474532c6cea4a19ebfd94928b13cbec998fc1f`. N-1 is resolved as Option B: concrete `admitted_native_range` is required in V1. |
| **CP-4** | 1F | Generated view manifests + binding evidence + audit trail, **before** any research uses them. Independent review **COMPLETE — APPROVE WITH NON-BLOCKING NOTES**; **APPROVED by owner on 2026-08-10** over production execution identity `f8b6168511fa2544479c0ad96a16c50cf0ab7890`. Slice 1F is COMPLETE; the four non-blocking notes are recorded in §6 and schedule no immediate remediation. |
| **CP-5** | 1H | Full §11 completion checklist + independent review of the whole phase. **COMPLETE 2026-08-10 — verdict APPROVE WITH NON-BLOCKING NOTES; OWNER APPROVED 2026-08-10**, reviewed at `38569ffbed8efff8efe8864cd5a8cefd0f4e203f`. It confirmed range-derived gating and the exact 2→3 audit effect; TRAIN/VALIDATION/OOS/LOCKBOX boundaries (live `classify_access` and `default_views()`); no UTC invention; all five view identities re-verified reader-digest == byte-digest; loader double-hash integrity; the preserved `ResearchModel` → `ModelOutput` → `DecisionPolicy` separation; all eight 1A–1H duty rows; and §11 satisfied honestly. No blocker. **Phase 1 is COMPLETE.** |

**Slice 1E review outcome (owner-approved 2026-08-08).** There is **no new
formal checkpoint after Slice 1E**; CP-4 remains after Slice 1F. The focused,
bounded, independent, read-only Slice 1E review is **COMPLETE** with verdict
**APPROVE**. It was explicitly **not** a "CP-3.5" and was limited to four
questions:

1. Is the composition-route widening minimal, and does it preserve
   `MarketFrameLoader.load` as the sole decoder?
2. Does backward-context authorization admit only earlier, consecutive,
   authorized Tier-1 members?
3. Can a returned `BoundedView` expose any row after `eval_end`, or any
   loading authority?
4. Does the insufficient-context path fail closed and retain adequate
   structured evidence?

That review did **not** reopen Slice 1D or become an open-ended mutation
campaign. Slice 1D's finite stopping rule (§6, CP-3) stands. The owner
approved Slice 1E and its reviewed head was fast-forwarded into `main`.

## 11. Phase 1 completion checklist

Each item was independently verified on 2026-08-10 at
`ad1e507a3577b377317d4c68d029454479d43d7a`; the evidence basis is named beside
it rather than assumed.

- [x] All contracts in §5 implemented (1A–1G) — 1A `89a1650`, 1B `302721a`/`e831032`,
      1C `aaf1dba`, 1D `2e47453`, 1E `84bee81`, 1F `f8b6168`, 1G `fa69733`; each
      owner-approved at a reviewed head.
- [x] `./scripts/gate.sh` green; architecture tests **extended, never weakened** —
      750 passed / 3 performance deselected, Ruff clean, strict mypy over
      `src tests scripts` (71 files); architecture suite grew 76 → **87** across
      1F→1H with no guard removed.
- [x] Dataset root integrity (checksum-verified) — satisfied per **OD-H4**
      above: unchanged manifest-pinned identities, unchanged reproducible D-6
      fingerprint `5125b9ff…8180`, and successful full production-loader
      SHA-256 verification of all seven SNAPSHOT partitions. Not a new Phase-0
      baseline digest; never compared with `4637ca9d…`.
- [x] 5 view manifests generated, validated, hash-pinned; hashes recorded in
      PROJECT_STATE.md — CP-4 approved 2026-08-10; all five digests re-verified
      unchanged during Stage 1H-B.
- [x] Default discovery returns **TRAIN + VALIDATION only** — `default_views()`
      re-executed against the live registry: exactly
      `['XAUUSD_S_M1_TRAIN_V1', 'XAUUSD_S_M1_VALIDATION_V1']`.
- [x] OOS explicit and audited from day one — OOS manifest carries
      `discovery_default: false` and re-classifies as `OOS_EXPLICIT`; audit path
      exercised in `tests/test_gating.py`.
- [x] RAW / CURATED / SNAPSHOT / LOCKBOX retain fully gated classification; RAW
      public frame loading always refused — `classify_access` re-run live:
      RAW, CURATED, SNAPSHOT, LOCKBOX all `FULLY_GATED`; TRAIN/VALIDATION
      `NORMAL`; RAW refusal pinned by
      `test_slice_1d_loader_contains_an_explicit_raw_role_refusal`. The Stage
      1H-B SNAPSHOT ceremony demonstrated the gated path end to end.
- [x] **No data returned before audit success** (fault-injection test) —
      Slice 1C write-ahead ordering with injected audit failure in
      `tests/test_gating.py` / `tests/test_audit.py`.
- [x] No UTC invention; no `utc` identifier in the data layer —
      `test_native_primitives_declare_no_utc_identifier` and the Slice 1D
      civil-time guard.
- [x] Cross-clock-domain comparison refused — `NativeTimeKey._require_orderable_with`,
      covered in `tests/test_time.py`.
- [x] Context/eval semantics and all refusal paths tested — Slice 1E, reviewed
      APPROVE; `tests/test_views.py`.
- [x] Adjacent-view union representable and validated — `ViewUnion` +
      `tests/test_views.py`.
- [x] GapIndex arithmetic-only (`prev_key`, `next_key`, `delta`, `deviation`;
      no boundary class per OD-G2); semantic vocabulary architecture-tested
      ✅ **Done (2026-08-10)** — `fa69733367ee4c340894ff9a0d5d8b6b99f92a6e`;
      public surface re-verified live as exactly the ten approved members.
- [x] Snapshot binding keys-only, audited, recorded; post-cap rows unreachable —
      CP-4 re-proved `sha256(published SNAPSHOT) == snapshot_binding.snapshot_manifest_sha256`;
      bound key `1785542220`; 4,897 post-cap rows structurally unreachable.
- [x] Performance budgets measured and recorded — §8 "Measured results". SNAPSHOT
      warm median 0.265562650 s and peak RSS 592,191,488 B both pass; TRAIN warm
      median 0.142175 s and RSS 464,871,424 B pass; selective-loading evidence
      28,290,760 B < 84,872,280 B. **Cold honestly recorded as
      `not measured on this machine — sudo unavailable`** per owner decision 11.
- [x] Legacy carriers retired or explicitly scheduled, with a test preventing
      new use — OD-H1: provider path retired in `6a93d0b`; `Bar`,
      `MarketContext`, `PipelineRecord`, `ResearchInput`, `ExperimentConfig`,
      `ExperimentResult` explicitly scheduled to Phase 2 and guarded by
      `test_data_layer_never_references_the_retired_provider_path` and
      `test_data_layer_never_references_phase_2_deferred_research_carriers`.
- [x] Documentation duties (§9) complete for every slice — 1A–1H rows all
      recorded, including this §8/§11 fill-in.
- [x] PROJECT_STATE.md and V1_ROADMAP.md updated; Git tree clean — updated in
      this closeout; `git status --porcelain=v1 --untracked-files=all` empty.
- [x] **Phase 1 independently reviewed** (same pattern as Phase 0B) — **CP-5
      COMPLETE 2026-08-10, verdict APPROVE WITH NON-BLOCKING NOTES; OWNER
      APPROVED 2026-08-10.**

**All §11 items are satisfied. Phase 1 is COMPLETE.**

## 12. Deferred decisions and enduring review guards

The following deferred decisions do not block Phase 1 and are explicitly not
decided here:

- Broker clock DST documentation — proceed as unknown-and-derived
  (unchanged from Phase 0's open-decision list).
- Integrity mode `cached` — deferred until dataset size makes `full`
  costly (ADR-0004's own trigger).
- GapIndex persistence — revisit only if Phase 2 profiling shows repeated
  recomputation actually matters.
- Narrower platform-code cache identity (ADR-0007) — Phase 2.

**Resolved Slice 1B implementation choice.** Registry roots are passed
explicitly to `DatasetRegistry.discover`; there is no environment-variable,
config-file or working-directory fallback (§9).

**Enduring CP-2 evolution guard (N-6; already decided).** Any future
`ViewRole` must receive an explicitly defined and tested role-floor behavior;
never rely on a fallback/default branch for a new role. This guard is
non-blocking for current Phase 1 work and remains mandatory for future role
evolution.

## 13. Post-Phase-1 operational checklist

Before Phase 2 begins:

1. Create a **Production Checkpoint** named for Phase 1 completion.
2. Create a **private Git remote** and push the repository.
3. Consider a full **Hyper-V VM export**.
4. **Do not begin Phase 2** until items 1–3 are reviewed by the owner.

### Status (2026-08-12) — **§13 COMPLETE**

**§13 is COMPLETE. The Phase-2 implementation gate created by §13 is
CLEARED.** Items 1 and 2 were satisfied on 2026-08-10 and are unchanged; item
3 was completed on 2026-08-12 on the owner's explicit attestation; item 4
(owner review of items 1–3) is COMPLETE on 2026-08-12.

**1. Production Checkpoint — SATISFIED.** Owner decision: the Production
Checkpoint is **one annotated Git tag** pinned to the exact completed Phase-1
repository state. No GitHub Release is created; the tag alone is the
checkpoint. Tag **`phase1-complete-2026-08-10`**, annotated (not lightweight),
message *"Phase 1 complete — CP-5 owner-approved 2026-08-10"*, created on and
pushed to `origin`. **Target commit
`dd62d76a0c37df6ec27571f2140ab609d6b4e987`** (tag object
`107a90f3744151ce2e6b56f8bdf7a1488b9edb18`, identical on `origin`).

**2. Private Git remote — SATISFIED.** Remote repository
**`fahedb/trading-research`**, GitHub visibility **private** (owner-confirmed),
reached over SSH as `origin`; `main` is pushed and synchronized
(`HEAD == main == origin/main`). No credential, SSH configuration or secret is
stored in the repository.

**3. Hyper-V VM export — COMPLETED (OWNER ATTESTED, 2026-08-12).** The owner
attests that a full Hyper-V VM export of this virtual machine was **completed
on 2026-08-12**. **Host-side storage location, exactly as provided by the
owner: under the virtual machine's host files, in a folder named `export`.**
No more precise host path was provided by the owner and none is inferred or
invented here. **Verification provenance: OWNER ATTESTED — not independently
verified.** The export was performed on the Hyper-V host, not inside this
Linux VM; the guest cannot see or independently verify that host filesystem
path. Nothing in this repository verifies the export's existence, contents,
size, integrity or restorability: this record is the owner's attestation,
recorded as such.

**4. Owner review of items 1–3 — COMPLETE (2026-08-12).** The owner has
reviewed items 1–3 and **accepted all three**:

- **item 1 — Production Checkpoint: ACCEPTED** (annotated tag
  `phase1-complete-2026-08-10` at `dd62d76a0c37df6ec27571f2140ab609d6b4e987`);
- **item 2 — private Git remote: ACCEPTED** (`fahedb/trading-research`,
  private, pushed and synchronized);
- **item 3 — Hyper-V VM export: ACCEPTED on the basis of the owner's
  attestation** (owner-attested completion; not independently verified from
  the guest VM).

**§13 is COMPLETE.** The §13 "Do not begin Phase 2 until items 1–3 are
reviewed by the owner" gate is therefore **CLEARED**. **Phase 1 remains
COMPLETE.** **Phase-2 implementation is now permitted**, subject to the
already accepted Phase-2 plan
([PHASE2_FEATURES.md](PHASE2_FEATURES.md)) and
[ADR-0021](../adr/ADR-0021-columnar-feature-contract.md). Clearing this gate
is a state transition only: no Phase-2 source file, test, dependency, cache
entry or artifact has been created by this record.

---

*This document supersedes the Phase 1 section of
[V1_ROADMAP.md](../roadmap/V1_ROADMAP.md) for execution detail; the roadmap
keeps only high-level status and a pointer here.*
