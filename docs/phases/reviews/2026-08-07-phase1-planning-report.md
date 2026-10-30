# Phase 1 Implementation Planning Report — provenance record

> **Provenance.** This was a **read-only planning and design session**
> (2026-08-07): no source, test, dependency, or dataset file was created,
> modified, or installed; no Git operations were performed; no lockbox
> market rows were inspected (one benchmark hashed TRAIN-period partitions
> only). The session's job was to convert the accepted Architecture V1 into
> a concrete Phase 1 construction plan and surface the decisions the owner
> needed to make before implementation could start.
>
> The owner reviewed this report and ruled on all open questions (§24 below,
> and item 5). Those decisions, and the plan they authorize, are recorded
> normatively in **[PHASE1_DATA_LAYER.md](../PHASE1_DATA_LAYER.md)** — that
> document is what an implementation session should read. **This report is
> retained verbatim as design provenance**, the same way the Phase 0 freeze
> proposal and independent review are preserved: it is not rewritten,
> shortened, or reconciled with the decisions that followed it. Where this
> report's recommendations and the owner's final decisions differ in
> wording, PHASE1_DATA_LAYER.md governs.

---

# Phase 1 Implementation Planning Report

**Session type:** planning only. No files created or modified, no packages installed, no Git operations, no dataset writes. No lockbox market rows inspected — the one benchmark I ran hashed TRAIN-period partitions (2020–2023) only, deliberately avoiding 2026 bytes.

---

## 1. Executive recommendation

Phase 1 is well-specified and buildable, but **it cannot begin until one dependency decision is made: there is no Parquet reader and no columnar library anywhere on this machine** — not in the venv, not in system Python. The platform currently cannot read a single row of its own dataset. That is the single blocking item; everything else in Phase 1 is design detail that I can resolve with recommendations below.

I recommend **PyArrow (which brings NumPy) as the internal columnar backend**, a **single integrity mode (`full`, always)** — measured at 31 ms for the entire admitted dataset, which makes the two-tier policy pointless at this scale — and a **reordering of the provisional slice plan so that gating and audit are built *before* the first real row is ever read**. The provisional 1A–1G ordering would have the implementer reading the gated CURATED parent "just to test the loader" in slice 1C, three slices before the gate exists. That is precisely the habit the architecture exists to prevent, and it is cheap to avoid by ordering.

Two details the accepted ADRs do not specify need owner rulings before implementation: **where generated view manifests live** (they cannot go under the immutable dataset root), and **what concrete value defines "the OOS upper boundary"** that drives range-derived gating.

I found **no architecture exceptions**. Nothing in the accepted architecture is contradictory or unimplementable.

---

## 2. Verified current-state facts

All OBSERVED FACT unless marked otherwise.

| Fact | Evidence |
|---|---|
| Working tree clean at `1bc71ec`, 3 commits on `main` | `git status --short` empty; `git log` |
| Quality gate fully green | `./scripts/gate.sh`: 50 tests, ruff clean, mypy strict clean over 30 files |
| **No columnar library installed** | `numpy`, `pyarrow`, `polars`, `pandas`, `fastparquet` all absent from both venv and system Python |
| Runtime dependency set is `pydantic` only | `uv.lock`; dev group = pytest/ruff/mypy |
| Python 3.13.14, uv 0.12.1 | `uv run python -VV` |
| Admitted data = 58.9 MB Parquet across 7 files | `du -cb` on curated-admitted partitions |
| SHA256 of 33.7 MB (TRAIN partitions) = 18–19 ms, ~1.85 GB/s | 3 trials; extrapolates to **~31 ms for all 58.9 MB** |
| Curated manifest does **not** pin the parent manifest hash | Only `parent_raw_dataset_id` + relative path; individual files are SHA256-pinned |
| Curated manifest has **no** `role`, `discovery_default`, `admitted_native_range`, or explicit `clock_domain` | Top-level key inspection; `curation_format_version: "1.0.0"` |
| Epoch convention confirmed mechanically | `first_raw` 1577926800 decodes (UTC-mechanical) to `2020-01-02T01:00:00`, exactly matching `first_encoded` |
| Curated columns include `timestamp_mt5_encoded` — a naive decoded datetime | `columns` list (9 columns) |
| Runs root exists with `cache/` and `tmp/`; **no `audit.jsonl` yet** | `find` on runs root |
| Dataset root is owner-writable (stated residual risk) | `drwxrwxr-x masterpiece` |
| 59 GB free disk, 22 GiB RAM, 12 vCPU | `df`, `free`, matches CLAUDE.local.md |
| Passwordless sudo **not** available | `sudo -n true` fails → cold-cache benchmarking cannot use `drop_caches` |

### The most useful thing I computed

**TRAIN, VALIDATION and OOS are exactly partition-aligned; only LOCKBOX and SNAPSHOT need row-bounding.** Derived from manifest metadata plus arithmetic on the policy dates — no rows read:

| View | Policy range (native keys) | Partitions | Row-bounded? |
|---|---|---|---|
| TRAIN | `1577836800 … 1704067140` | 2020, 2021, 2022, 2023 (all keys inside) | **no** |
| VALIDATION | `1704067200 … 1735689540` | 2024 (keys `1704157200…1735689360`) | **no** |
| OOS | `1735689600 … 1767225540` | 2025 (keys `1735779600…1767225360`) | **no** |
| LOCKBOX | `1767225600 …` bound | 2026 only | **yes** |
| SNAPSHOT | first usable … bound | 2020–2026 | **yes** (top only) |

The snapshot/lockbox upper cap candidate is `2026-07-31T23:59:00` → **`1785542340`**; the *bound* value is the largest observed key ≤ that, which requires the keys-only audited read. The 2026 partition spans `1767315600…1786023780`, i.e. it contains both lockbox rows and post-cap rows (Aug 1–6) — confirming ADR-0014's premise.

This materially simplifies Phase 1: `admitted_native_range` is exercised by exactly two views, and partition-level admission covers the three views researchers actually use daily.

---

## 3. Phase 1 scope confirmation

I confirm the scope as stated, with three clarifications about what "implement" means for contracts whose consumers do not exist yet:

- **`context_start` (ACCEPTED ARCHITECTURE, ADR-0012 Amendment 2)** — Phase 1 implements the *representation*, *resolution* and *refusal* rules (context may resolve into strictly earlier authorized views; never later; never unauthorized gated). It does **not** implement warm-up logic, because no feature exists to warm up. Tests assert the refusals and the resolved ranges.
- **Adjacent-view union** — Phase 1 implements the representable, hash-pinned, ordered-union *object* and its adjacency/authorization validation. No fitting consumes it (Phase 3).
- **GapIndex** — Phase 1 computes arithmetic facts. No WindowSpec, no gap *policy* application (that is the FeatureProvider's declaration in Phase 2).

Explicitly **out**, and I will flag any drift toward them: FeatureProvider, WindowSpec semantics beyond the view contract, artifact caching, TrainingTargetFrame, OutcomeSpec, any model/policy/intent type, SQLite, orchestration.

---

## 4. Architecture exceptions

**None.** I attempted to break the accepted architecture against the real dataset and environment and found no contradiction, no unimplementable contract, and no correctness problem that would invalidate Phase 1.

Two things are *unspecified* rather than wrong; both are owner decisions (§5, items 2 and 8), not exceptions:

1. ADR-0014 mandates v1.1 view manifests but never says where generated manifests live. CLAUDE.md forbids writing under the dataset root, so they must live elsewhere — the architecture simply does not say where.
2. ADR-0014 Amendment 1 gates anything whose range extends "beyond the OOS upper boundary" without naming the concrete value or its source of truth.

One structural consequence worth stating plainly, which is *implied* by the accepted architecture rather than missing from it: **creating the Tier-1 views requires reading the gated CURATED parent, so view creation is itself a ceremony-gated, audited operation.** This is consistent (ADR-0014 Amendment 2 already constrains the binding read), but it dictates build order (§20).

---

## 5. Owner decisions required before implementation

| # | Decision | Recommendation | Blocking? |
|---|---|---|---|
| 1 | **Columnar backend + dependency addition** | Add `pyarrow` and `numpy` as direct runtime deps | **YES — nothing reads without it** |
| 2 | **Where generated view manifests live** | New sibling root `/path/to/research-views/`, recorded in CLAUDE.local.md | **YES** — 1F cannot start |
| 3 | **Gating-boundary source of truth** | A versioned platform split-policy file declaring the boundary key; unknown range ⇒ gated (default-deny) | **YES** — 1C correctness |
| 4 | Integrity modes | Implement `full` only; defer `cached` | No (recommend now) |
| 5 | ClockDomain id string + where declared | `mt5.justmarkets-demo3.wallclock`, declared in the MT5 adapter, recorded in every generated view manifest | No |
| 6 | Load `timestamp_mt5_encoded`? | **Never** into MarketFrame; adapter may expose a decode helper for display | No |
| 7 | Cold-benchmark procedure | Manual, documented, one-off with sudo `drop_caches`; CI measures warm only | No |
| 8 | Snapshot cap semantics | Inclusive cap `2026-07-31T23:59:00` = key `1785542340`; bound = max observed key ≤ cap | No |
| 9 | Audit log location and whether view-creation events share it | Single `runs/audit.jsonl` for all gated access including view creation | No |
| 10 | GapIndex persistence | Ephemeral/lazy in Phase 1 | No |

---

## 6. Recommended technical decisions

### A. Columnar backend — recommend **PyArrow (+ NumPy)**

IMPLEMENTATION RECOMMENDATION, with the comparison the brief asked for:

| Criterion | PyArrow | NumPy + thin schema | Polars (internal only) |
|---|---|---|---|
| Parquet read | native, with column + row-group selection | **cannot** — needs a second dep anyway | native |
| Typed columns | Arrow schema, exact dtypes | manual dtype bookkeeping | native |
| Zero-copy bounded slice | `Table.slice()` — zero-copy | slicing views is native | zero-copy-ish, engine-mediated |
| Column selection at read | `read_table(columns=[...])` | n/a | yes |
| Memory | ~168 MB for 6 columns × 2.33 M rows (matches manifest's 165 MB estimate) | same | same + engine overhead |
| Hash/reproducibility | stable buffers; hashing is over source files anyway | stable | stable |
| Dependency cost | large wheel; pulls NumPy | small, but insufficient alone | largest; bundles its own Arrow |
| Semantic leak risk into contracts | low (dumb container) | lowest | **highest** — expressions/lazy frames are seductive |
| Testing ease | good | best | good |

PyArrow wins because it is the only single dependency that covers Parquet reading *and* typed columnar storage, and because it is deliberately a **container**, not a query engine — which is exactly what ADR-0003 asks for. Polars is rejected not because it is bad but because its expression API is the most likely thing to end up leaking into a "convenient" MarketFrame method and recreating the in-house DataFrame ADR-0003 forbids. NumPy alone cannot read Parquet, so choosing it means adding PyArrow anyway.

NumPy arrives as a PyArrow dependency and I recommend declaring it **explicitly** as a direct dependency, because the data layer will use it directly for `searchsorted` bound lookups (the O(log n) requirement in the roadmap) and `diff` for GapIndex.

**Packages that would be added (not installed in this session):** `pyarrow`, `numpy`. Both to `[project.dependencies]`, both pinned via `uv.lock`. An architecture test must forbid `pyarrow`/`numpy` types in public contract signatures (ADR-0003).

### B–M summary of the other recommendations

Detailed in §§8–20. Headlines: single integrity mode; one frame carrying context+eval rows with explicit eval bounds rather than two frames; manifest v1.1 as *new* files with *reader adapters* for the immutable originals; registry roots explicitly configured with manifest-discovery separated from partition-admission; audit-before-data enforced by making the audit write a precondition inside the resolver, not the caller.

---

## 7. Minimal contracts and semantic interface sketches

Sketches are semantic, not implementation. Each method is justified by the "what fails without it" test.

```
NativeTimeKey        value: int64 · unit: TimeUnit · clock_domain: ClockDomain
                     — scalar/boundary use only, NEVER per row
ClockDomain          id: str  (e.g. "mt5.justmarkets-demo3.wallclock")
                     equality is identity; cross-domain comparison RAISES
TimeUnit             SECONDS | MILLIS | MICROS | NANOS   (dataset: SECONDS)
CalendarTimePolicy   "native-wallclock-v1" — the only member in V1
```

```
DatasetRef           dataset_id: str · manifest_sha256: str · role: ViewRole
ViewRole             RAW | CURATED | SNAPSHOT | TRAIN | VALIDATION | OOS | LOCKBOX
```

```
MarketFrame   (immutable, columnar, zero-copy views)
  properties:  dataset_ref · clock_domain · unit · schema · num_rows
               keys            -> read-only int64 array, strictly increasing
               column_names
  operations:  column(name)             -> read-only typed array
               select(names)            -> MarketFrame (zero-copy, fewer columns)
               slice_by_keys(lo, hi)    -> MarketFrame (zero-copy, inclusive bounds)
  forbidden:   joins · groupby · resample · expressions · arithmetic · mutation
               per-row iteration · statistics · I/O · gap semantics
```

```
BoundedView   (what research actually receives)
  view_id · role · dataset_refs (ordered, each hash-pinned)
  context_start · eval_start · eval_end        (NativeTimeKey values)
  frame()          -> MarketFrame covering [context_start, eval_end]
  eval_frame()     -> MarketFrame covering [eval_start, eval_end]
  invariants: context_start <= eval_start <= eval_end
              nothing readable beyond eval_end
```

```
ViewUnion     ordered, adjacent, non-gated Tier-1 members; each pinned by
              (dataset_id, manifest_sha256); adjacency verified against
              declared ranges; representable in Phase 1, consumed in Phase 3
```

```
GapIndex      compact columnar arithmetic facts (see §14)
AccessContext caller identity + authorization + structured reason (see §13)
```

**Justification of every MarketFrame member** (the test the brief demanded):

| Member | What fails without it |
|---|---|
| `keys` | Bound lookup, ordering checks, GapIndex, every alignment operation |
| `column(name)` | Nothing can read prices; the frame is inert |
| `schema` | Type mismatches surface as silent wrong math instead of a refusal |
| `clock_domain` / `unit` | ADR-0002's "no untagged time" invariant is unenforceable |
| `dataset_ref` | Provenance and identity-key composition (ADR-0007) break |
| `num_rows` / `column_names` | Validation, tests, and error messages |
| `select(names)` | Phase 1 acceptance: column-selective loading; memory budget |
| `slice_by_keys(lo, hi)` | PIT views, context/eval separation, fold ranges — the core of Phase 1 |

Anything not on that list is a candidate for the in-house-DataFrame failure mode and should be refused.

**Ownership and copy semantics:** MarketFrame is immutable; slices and selections share buffers (zero-copy); exposed arrays are read-only (writeable flag cleared) so a plugin cannot mutate shared data. No defensive copying — copying 168 MB per stage is exactly the cost ADR-0003 exists to avoid.

**Validation boundaries:** validate once, at construction from the loader (ordering, dtype, domain tag, bounds); never per row, never per slice — a slice of a valid frame is valid by construction.

**Coexistence with later frames:** ModelOutputFrame, IntentFrame and TrainingTargetFrame are *separate types* in later phases, possibly sharing a tiny "keyed frame" notion. **Do not** make MarketFrame their base class and do not add sparsity to MarketFrame — market data is dense by definition; sparsity belongs to the frames that ADR-0009 made sparse.

---

## 8. Manifest v1.1 proposal

ACCEPTED ARCHITECTURE: v1.1 adds `role`, `discovery_default`, `admitted_native_range` (ADR-0014). IMPLEMENTATION RECOMMENDATION: the following field set, in **new** generated files; the immutable raw and curated manifests are never rewritten.

```
manifest_format_version   "1.1"
view_id                   e.g. XAUUSD_S_M1_TRAIN_V1
role                      train | validation | oos | lockbox | snapshot
discovery_default         true only for train, validation
gated                     true when admitted range exceeds the gating boundary
generated_at_utc          tooling timestamp (a real UTC clock — not market time)
generator                 tool name + version + platform git commit

parent                    { dataset_id, manifest_sha256, relative_path }
                            ^ closes the gap that curated 1.0.0 left open
symbol, timeframe
clock_domain              "mt5.justmarkets-demo3.wallclock"
time_unit                 "seconds"
calendar_time_policy      "native-wallclock-v1"
columns                   [name -> dtype]   (canonical, excludes timestamp_mt5_encoded)

admitted_partitions       [ { key, filename, relative_path, sha256, bytes, rows,
                              first_key, last_key,
                              admitted_native_range: {start, end} | null } ]
admitted_native_range     { start, end }        view-level, inclusive
row_count_expected        int

snapshot_binding          { policy_cap_encoded: "2026-07-31T23:59:00",
                            policy_cap_key: 1785542340,
                            bound_key: <observed>,
                            method: "keys-only scan",
                            audit_event_id: <ulid> }      (snapshot/lockbox only)

split_policy_ref          { adr: "ADR-0012", policy_version: "v1" }
notes
```

**Compatibility with the existing manifests.** OBSERVED FACT: curated 1.0.0 provides `included_partitions` with `sha256`, `first_raw`, `last_raw`, `rows`, and `excluded_periods`; it lacks role, discovery, ranges, clock domain, and a parent hash. Two **reader adapters** handle this — a `RawManifestReader` and a `CuratedV1Reader` — each producing the same internal `DatasetManifest` object that `ViewManifestV11Reader` also produces. Source files stay byte-identical; `parent.manifest_sha256` is computed by the platform over the parent file at generation time and pinned thereafter. This satisfies "prefer adapters over rewriting immutable source manifests."

**Context/evaluation semantics in manifests:** deliberately **absent**. `context_start`/`eval_start`/`eval_end` are *runtime* properties of a requested BoundedView, not properties of a materialized Tier-1 view. Baking them into manifests would fossilize a per-experiment choice into an integrity artifact. The manifest declares the admitted range; the view request declares the three keys, validated against it.

---

## 9. Registry/discovery proposal

- **Roots are explicitly configured**, never guessed: a small platform config (env var or config file, machine paths from CLAUDE.local.md) lists a dataset root and a views root. No implicit CWD scanning.
- **Manifest discovery ≠ partition admission** — the distinction the brief rightly emphasizes. Discovery *may* scan registered roots for `manifest.json` files to learn which datasets/views exist. Admission is decided **exclusively** by a manifest's admitted-partition list. Scanning for manifests is legal; globbing for `*.parquet` to decide what to load is prohibited and architecture-tested.
- **Resolution by exact DatasetRef** `(dataset_id, manifest_sha256, role)` is the canonical path: if the manifest's current hash differs from the pinned one, **refuse** (identity changed → new identity required, ADR-0004).
- **Resolution by role** returns only `discovery_default: true` views — TRAIN and VALIDATION. Gated roles are unreachable by role and must be named explicitly under ceremony.
- **Duplicate dataset IDs** across roots → hard error at registry construction, naming both paths. Never "last wins".
- **Manifest hash change** on a previously pinned identity → hard error distinguishing "manifest edited in place" (a violation) from "new identity" (legal, different id).
- **Future data sources:** the registry stores `adapter_id` per dataset; `mt5` is one adapter registered in `data/adapters/`. Core never imports it; nothing named MT5 appears in `core/`. This is already enforceable by the existing AST architecture test.

---

## 10. Integrity-loader proposal

IMPLEMENTATION RECOMMENDATION: **one mode — `full` — always.** OBSERVED FACT: hashing all 58.9 MB takes ~31 ms. A second "cached" mode would save 31 ms while introducing cross-process false trust, an extra config surface, and a class of bug where a dev-loop run silently trusts a modified file. ADR-0004's "when to reconsider" already scopes `cached` to a future where hashing is costly (tens of GB); we are three orders of magnitude away.

Behavior:
1. Resolve manifest → verify the manifest's own hash against the pinned `manifest_sha256`.
2. For every admitted partition: check **size first** (cheap mismatch detection), then SHA256.
3. Verification is **complete before any bytes are decoded into a frame**.
4. Any mismatch → `IntegrityError` naming file, expected hash, actual hash. Never a warning, never a downgrade, never a retry.
5. **No partial frame return, ever** — failure yields an exception, not a truncated frame. Load is all-or-nothing: verify all, then decode.
6. Verification results may be memoized **per process** keyed by `(path, size, mtime_ns, hash)`; a new process re-verifies. This avoids the cross-process false-trust problem entirely.

---

## 11. MarketFrame proposal

Covered by the contract in §7. Additional implementation notes:

- Backed by an Arrow `Table` held privately; **no PyArrow type crosses the public boundary** (architecture-tested).
- Column arrays exposed as read-only NumPy views via zero-copy where the Arrow buffer allows (int64/float64 without nulls qualify).
- Bound lookups via `numpy.searchsorted` on the key array → O(log n), satisfying the roadmap's stated performance shape.
- Construction validates: strictly increasing keys, no duplicates, declared dtypes match, clock domain and unit present, key column is int64.
- Expected memory: ~168 MB for 6 numeric columns at 2.33 M rows — comfortably inside the 1 GiB RSS budget with room for a working copy.
- **`timestamp_mt5_encoded` is not loaded.** It is a derived duplicate of the key and the single most likely vector for "someone treats this as UTC" (risk R12 in the freeze proposal). A separate display helper can decode on demand for humans.

---

## 12. View/context/union proposal

**Recommendation: one frame spanning `[context_start, eval_end]`, plus explicit evaluation bounds** — not two frames, not a boolean mask.

Rationale: features need contiguous history across the context/eval seam; two frames force every future plugin to concatenate (allocation + a new bug class). A boolean mask costs 2.33 MB and invites boolean indexing, which copies. Two integer bounds cost nothing and make `eval_frame()` a zero-copy slice.

Representation of each concept:

| Concept | Representation |
|---|---|
| Admitted range | manifest `admitted_native_range`, validated against observed partition keys |
| `context_start` / `eval_start` / `eval_end` | int64 key values on the BoundedView; `context_start ≤ eval_start ≤ eval_end` |
| Role, parent identity | on the view; every member pinned by id + manifest hash |
| Ordered union | ordered tuple of member views + adjacency validation |
| PIT upper bound | `eval_end`; loader never materializes rows beyond it |
| Partition boundaries | recorded as objective boundary facts in GapIndex |
| Zero-copy reads | Arrow slice; only admitted row-groups decoded |
| Empty range | legal, explicit `EmptyRangeError` on request for zero eval rows — never a silent empty frame |
| Insufficient backward context | **refuse by default** with the shortfall reported; an explicit `allow_short_context` flag records the truncation. Silent short warm-up is how train/eval inconsistency hides |
| Partial current-year partition | normal: the 2026 partition is partial by nature; bounds come from the manifest range, never from "the file ends here" |

**Context authorization rules** (ACCEPTED ARCHITECTURE, ADR-0012 Amendment 2), enforced at view construction:
- context may resolve into strictly **earlier** views only;
- never into a later view;
- never into a gated view unless the request carries that authorization;
- resolution is recorded (which views supplied context, and their hashes).

**Union rules:** members must be adjacent in policy order (TRAIN→VALIDATION→OOS), non-gated unless authorized, each hash-pinned, order preserved and recorded. Non-adjacent unions and any hand-constructed date range are refused.

---

## 13. Gating and audit proposal

**Gating decision (range-derived, default-deny):**

```
gated(identity) := admitted_range.end > gating_boundary_key
                   OR admitted_range unknown        # default-deny
```

`gating_boundary_key` comes from the versioned split-policy record (OWNER DECISION #3), *not* from a hardcoded date and *not* from a name list. Under the current policy that is the OOS end, `1767225540`. This automatically gates RAW, CURATED, SNAPSHOT and LOCKBOX, and automatically gates any future dataset that arrives with fresh data — the property ADR-0014 Amendment 1 was written to get.

**Caller identity before Phase 3 exists.** Do **not** build a run system. A minimal `AccessContext` carries: a ULID minted per CLI invocation, OS user, PID, hostname, platform Git commit + dirty flag, wall-clock timestamp, the requested identity/role, the authorization presented, and the structured reason. Phase 3's RunManifest later *references* these events; it does not replace them.

**Ceremony, per ADR-0013:** explicit CLI unlock flag **and** structured reason in the resolved config — both required — for RAW/CURATED/SNAPSHOT/LOCKBOX. OOS requires explicit naming plus a reason, no unlock flag.

**The audit-before-data invariant, enforced structurally:**

```
resolve_gated(identity, access_context):
    validate ceremony            -> raise if incomplete
    event = build_audit_event(...)
    append_audit(event)          # O_APPEND single write + fsync
    if append failed:  raise AuditWriteError   # ACCESS DENIED
    ... only now open any file / decode any row ...
```

The audit call sits **inside** the resolver, before the loader is even constructed — not in the CLI, not in a caller that could be bypassed. A test injects an audit-write failure and asserts that no file handle was opened and no frame returned.

**Audit log mechanics** (ADR-0017 guidance): append-only JSONL at `runs/audit.jsonl`; one JSON object per line; single `O_APPEND` `write()` of a pre-serialized line (atomic for a single write under PIPE_BUF-sized lines on Linux ext4); `fsync` before returning. Never rewritten, compacted or reordered. Concurrent processes append safely; readers tolerate a torn final line only by ignoring it, never by repairing the file.

---

## 14. GapIndex proposal

Minimal schema — arithmetic facts only (ACCEPTED ARCHITECTURE, ADR-0005):

```
GapIndex (compact columnar, parallel arrays over gap occurrences)
    prev_key      int64
    next_key      int64
    delta         int64          # next - prev
    deviation     int64          # delta - nominal_interval
    boundary_class enum { NONE, PARTITION_BOUNDARY, VIEW_BOUNDARY }
  + nominal_interval, clock_domain, source view identity
```

Forbidden vocabulary — not just absent but architecture-tested: `weekend`, `holiday`, `session`, `closure`, `outage`, `missing`.

**Computation:** `numpy.diff` over the int64 key array — a single vectorized pass over 2.33 M values, low single-digit milliseconds. **Lazy, computed on first request, memoized per frame, not persisted in Phase 1.** Persisting it would create an artifact whose identity must be managed (ADR-0007 machinery that Phase 1 deliberately excludes) to save milliseconds. If Phase 2 profiling shows repeated recomputation matters, persistence is a small, well-scoped addition then.

**Compact columnar, not one object per gap:** the raw manifest reports 6,122 gaps across all partitions; the admitted subset is smaller. Even at 10⁴ the object-per-gap form is the per-row-object mistake ADR-0003 exists to prevent, at small scale.

**Provenance discipline (ADR-0005 guidance):** the raw manifest's `weekend_gap_count`, `total_intraweek_gap_count` and `classification: "normal_boundary"` may be retained as *dataset metadata quotes* and must never be promoted into GapIndex facts.

---

## 15. Snapshot and split-view tooling proposal

A single audited CLI tool, run once, producing five v1.1 manifests.

**Where they live (OWNER DECISION #2):** recommend a new sibling root `/path/to/research-views/`, because (a) CLAUDE.md forbids writing under the dataset root, (b) views are data-identity artifacts that should be backed up *with* data rather than with run outputs, and (c) a separate root keeps "immutable inputs" and "generated identities" distinguishable at a glance. Recorded in CLAUDE.local.md; registered as a second registry root.

**Immutability after creation:** write to a temp file, fsync, atomic rename, then `chmod a-w` as a tripwire (not a security boundary — the owner can override, exactly as ADR-0013 states about the lockbox). Real immutability comes from hash-pinning: the manifest's own SHA256 is recorded in PROJECT_STATE and in every DatasetRef that uses it.

**Keys-only binding, structurally guaranteed:** PyArrow's `read_table(columns=["mt5_time_raw"])` reads only that column's row-groups. "Keys only" is therefore enforced by the read call itself rather than by reviewer diligence — the tool never has OHLCV or spread values in memory. This is a genuinely strong form of ADR-0014 Amendment 2 and worth calling out in the completion evidence.

**Proving post-July-31 rows are excluded:** the tool records `policy_cap_key = 1785542340`, the observed `bound_key ≤ cap`, the count of observed keys above the cap (expected > 0, since Aug 1–6 rows exist), and asserts `bound_key < first_excluded_key`. A test then attempts to read beyond `bound_key` through every V1 view path and asserts refusal.

**Naming without hardcoding dates:** view ids carry role and dataset lineage (`XAUUSD_S_M1_TRAIN_V1`), never dates. The dates live in the manifest range fields and in ADR-0012; platform code reads them from the policy record.

**Order of operations:** ceremony → audit event → keys-only scan of the curated parent → bind boundary → emit SNAPSHOT manifest → emit TRAIN/VALIDATION/OOS/LOCKBOX manifests referencing the same parent files → verify each generated manifest re-reads and re-verifies cleanly.

---

## 16. Existing-skeleton migration map

OBSERVED FACT: 1,273 lines across `src` + `tests`; 50 tests green.

| Component | Classification | Notes |
|---|---|---|
| `core/registry.py` `Registry` | **keep unchanged** | Generic, correct, no time or data assumptions |
| `core/errors.py` | **adapt** | Add `IntegrityError`, `ClockDomainError`, `GatedAccessError`, `AuditWriteError`, `EmptyRangeError`, `InsufficientContextError` under the existing root |
| `core/interfaces.py` `MarketDataProvider` | **replace** | `datetime`-based, tz-aware-required — directly contradicts ADR-0002. Phase 1's data layer supersedes it |
| `core/interfaces.py` others | **keep temporarily** | Feature/Representation/Model/Policy/Evaluator protocols retire in Phases 2–3 |
| `core/domain.py` `Bar`, `MarketContext` | **retire later** | Superseded by MarketFrame; keep until the tests that use them migrate |
| `core/domain.py` `_require_aware` | **retire with them** | Its tz-aware invariant is the skeleton rule ADR-0002 replaced |
| `FeatureSet`, `Representation`, `ModelOutput`, `ResearchInput` | **keep temporarily** | Demoted to boundary/test vocabulary in Phase 2–3 |
| `Signal`, `Direction`, `PipelineRecord` | **keep temporarily** | Replaced by IntentFrame family in Phase 3 |
| `core/pipeline.py` | **keep temporarily** | The fused `run_pipeline` is replaced by the Phase 3 orchestrator |
| `experiments/runner.py` | **keep temporarily** | Phase 3 |
| `data/registry.py` | **adapt** | Becomes the dataset/adapter registry home |
| `tests/test_architecture.py` | **adapt — extend, never weaken** | Add: no PyArrow/NumPy in public signatures; no `utc` identifier in the data layer; no `*.parquet` globbing; no session vocabulary |
| `tests/test_domain.py::TestPointInTime` | **adapt** | Same intent, new carriers |
| `tests/doubles.py` | **adapt** | Gains synthetic-Parquet fixtures; loses `SyntheticDataProvider` when `MarketDataProvider` retires |
| `test_contracts/decisions/experiments` | **keep temporarily** | Migrate in Phases 2–3 per ADR-0019 |

**Add-then-migrate sequence, with a deadline to avoid a long-lived duplicate architecture:** new `core/time`, `core/frames`, `core/identity` land *alongside* the old types; the data layer is built only on the new ones; no new code may import `Bar` or `MarketContext`. The old carriers are retired in a single commit at the end of Phase 1 **only if** nothing references them; anything still referenced (the model/decision vocabulary) retires on its own phase's schedule. An architecture test asserting "the data layer never imports the legacy carriers" is what actually prevents the duplicate architecture from becoming permanent.

---

## 17. Test matrix

| # | Test | Kind |
|---|---|---|
| 1 | Manifest schema validation; unknown/missing fields rejected | unit |
| 2 | Only manifest-listed partitions are read | unit + integration |
| 3 | Excluded partition (2011–2019) unreachable through every API | integration |
| 4 | **No directory globbing** anywhere in the data layer | architecture (AST/source) |
| 5 | Duplicate dataset IDs → hard error naming both paths | unit |
| 6 | Manifest hash mismatch vs pinned ref → refusal | unit |
| 7 | Partition hash mismatch → refusal | unit (corrupted fixture) |
| 8 | Size-mismatch detected before hashing | unit |
| 9 | **No partial frame on failure**: nothing returned, no partial state | unit |
| 10 | Clock-domain mismatch on compare/join → raises | unit |
| 11 | **No UTC invention**: no `utc` identifier, no tzinfo attached | architecture + unit |
| 12 | Native keys strictly increasing | property |
| 13 | Duplicate keys rejected | unit |
| 14 | Unsorted input rejected | unit |
| 15 | Column dtype/schema mismatch rejected | unit |
| 16 | Partition pruning: out-of-range partitions never opened | integration (I/O counter) |
| 17 | Column selection: unrequested columns never materialized | integration |
| 18 | Bounded view slicing correctness at both edges | property |
| 19 | `context_start ≤ eval_start ≤ eval_end` enforced | property |
| 20 | Backward context into an earlier authorized view is legal | unit |
| 21 | Context into a **later** view refused | unit |
| 22 | Context into an **unauthorized gated** view refused | unit |
| 23 | Insufficient context → refusal unless explicitly allowed + recorded | unit |
| 24 | Nothing readable beyond `eval_end` | property |
| 25 | Adjacent-view union: legal orders accepted, hash-pinned | unit |
| 26 | Non-adjacent union / gated member / hand-written range refused | unit |
| 27 | **Default discovery returns TRAIN + VALIDATION only** | integration |
| 28 | OOS requires explicit request and writes an audit event | integration |
| 29 | RAW/CURATED/SNAPSHOT/LOCKBOX without ceremony → raises | integration |
| 30 | Partial ceremony (flag only / reason only) → raises | unit |
| 31 | **Audit-write failure ⇒ no file opened, no rows returned** | unit (fault injection) |
| 32 | Audit append is single-line, fsync'd, concurrent-safe | integration |
| 33 | GapIndex arithmetic correctness on known fixtures | property |
| 34 | GapIndex contains no semantic vocabulary | architecture |
| 35 | Snapshot boundary: bound ≤ cap; rows above cap unreachable | integration |
| 36 | Binding read touched only the key column | integration |
| 37 | Layer/import boundaries (existing) + no PyArrow in public signatures | architecture |
| 38 | Real-manifest end-to-end load and verify | real-dataset |
| 39 | Warm load < 2 s; RSS < 1 GiB | performance |
| 40 | Cold load with full hash < 5 s | performance (manual) |

**Separation:** fast unit + property tests run in the default `pytest` gate (no dataset dependency, synthetic Parquet fixtures). Integration and real-dataset tests are marked and run in the gate on this machine (they are fast — 31 ms of hashing) but skip cleanly if the dataset root is absent. **Performance tests are marked and excluded from the default gate** (§18). Real-dataset tests use TRAIN/VALIDATION only; gated-path tests use synthetic gated fixtures so the suite never performs a lockbox ceremony.

---

## 18. Performance/benchmark plan

ACCEPTED ARCHITECTURE budgets: warm full-frame load < 2 s; cold with full hash verification < 5 s; loaded-frame RSS < 1 GiB.

**Precise definitions:**

| Benchmark | Includes | Excludes |
|---|---|---|
| Warm load | manifest parse + hash verify + Parquet decode + MarketFrame construction + ordering validation, page cache warm | process startup, imports |
| Cold load | same, page cache dropped | process startup |
| RSS | peak RSS of a process holding one full frame, all admitted columns except `timestamp_mt5_encoded` | interpreter baseline reported separately |

**Scope:** the SNAPSHOT-equivalent full admitted set (2.33 M rows) for the headline numbers; TRAIN alone reported as the common case. All columns for the memory budget; a 2-column selection reported separately to demonstrate selective loading.

**Cold vs warm:** OBSERVED FACT — passwordless sudo is unavailable, so `drop_caches` cannot run unattended. Recommendation: cold measurement is a **documented manual procedure** (owner runs `sync; echo 3 > /proc/sys/vm/drop_caches` with sudo, once, at Phase 1 qualification), recorded with its result in the completion evidence. Automated benchmarks measure warm only and record `/proc/meminfo` cached state so a warm claim is never mistaken for cold.

**Repeats and variance:** 5 repetitions after 1 discarded warm-up; report median and min; fail only if the **median** exceeds budget. Hashing is measured separately (already OBSERVED at ~31 ms) so a regression can be attributed to I/O, decode, or validation rather than guessed at.

**Avoiding flaky unit tests:** benchmarks live in `tests/performance/`, marked `@pytest.mark.performance`, **deselected by default** in `pyproject.toml` addopts. They are a qualification gate run deliberately, not a CI coin-flip. A budget breach produces a report, not a mysterious red test in an unrelated PR.

**Profiling evidence before optimizing:** if a budget is missed, capture `cProfile` + `tracemalloc` snapshots and the per-stage timing breakdown (verify / decode / validate / construct) **before** changing any code. No optimization without a profile — at 58.9 MB the likely answer is that something is accidentally copying, not that the design is slow.

---

## 19. Dependency/package recommendation

**Add (OWNER DECISION #1):** `pyarrow`, `numpy` — both direct runtime dependencies in `[project.dependencies]`, locked via `uv.lock`.

**Do not add:** pandas, Polars, DuckDB, fastparquet, hypothesis (property tests can be written with plain parametrization in Phase 1; add hypothesis only if a real need appears), pytest-benchmark (a small timing harness is enough and avoids a plugin's opinions in the default gate).

Guardrails that must ship with the dependency: an architecture test forbidding `pyarrow`/`numpy` types in public contract signatures, and a note in CLAUDE.md's repo map that these are internal implementation details of the data layer.

---

## 20. Ordered implementation slices and commit plan

I evaluated the provisional 1A–1G and recommend **one substantive change**: move gating and audit ahead of any real-data read. In the provisional ordering, 1C (integrity loader + MarketFrame) reads real data three slices before 1E builds the gate — and the only real data available at that point is the **gated CURATED parent**. That ordering teaches the implementer to read gated data ungated. Reordered below; slice count and content are otherwise close to the original, which was sound.

| Slice | Objective | Files / packages | Depends on | Tests added | Acceptance | Rollback boundary | Deliberately unbuilt |
|---|---|---|---|---|---|---|---|
| **1A** | Core time and identity | `core/time.py`, `core/identity.py`, `core/errors.py` | — | 10–14, 37 | Cross-domain compare raises; no UTC identifier | Pure addition; nothing depends on it yet | Frames, I/O |
| **1B** | Manifest model + reader adapters + registry (metadata only, **zero rows**) | `data/manifests/`, `data/registry.py` | 1A | 1, 5, 6, 27 | Real manifests parse; duplicate IDs error; role resolution returns TRAIN+VAL only | Additive; no loader exists | Any file read beyond manifests |
| **1C** | **Gating + audit** (before any row is readable) | `data/gating.py`, `data/audit.py` | 1B | 28–32 | Ceremony enforced; audit-before-data proven by fault injection | Additive; no data path yet | Run system, Phase 3 identity |
| **1D** | Integrity verification + MarketFrame + first real read | `data/integrity.py`, `core/frames.py`, `data/adapters/mt5.py` | 1C | 2–4, 7–9, 15–18, 38 | TRAIN loads and verifies; no partial return; no globbing | Legacy carriers untouched | Views, context semantics |
| **1E** | View semantics + adjacent-view union | `data/views.py` | 1D | 19–26 | Context/eval rules and refusals enforced | Additive on top of 1D | Fold generation, purge (Phase 3) |
| **1F** | Snapshot binding + Tier-1 view generation (audited tooling) | `scripts/` or `data/tools/`, views root | 1E | 35, 36 | 5 manifests generated, hash-pinned; keys-only proven; rows above cap unreachable | Generated files are new artifacts; deletable and regenerable | Any use of LOCKBOX in research |
| **1G** | GapIndex | `data/gaps.py` | 1D | 33, 34 | Arithmetic correct; no semantic vocabulary | Additive, lazy | Annotator, session labels |
| **1H** | Migration, performance qualification, closeout | legacy retirement commit, `tests/performance/`, docs | 1A–1G | 39, 40 | Budgets measured; gate green; docs updated | Retirement is its own revertible commit | Phase 2 anything |

**Commit discipline:** one commit per slice minimum, more if a slice exceeds ~400 changed lines. `./scripts/gate.sh` green at every commit. The dependency addition (`pyarrow`, `numpy`) is its **own commit** immediately before 1D, so a bisect can isolate it.

I considered a fully vertical slice (manifest → gate → load → view for TRAIN only, then widen). It is attractive for early feedback, but it forces the view contract to be designed before the frame exists and tends to produce a TRAIN-shaped abstraction that then needs reshaping for row-bounded views. The horizontal ordering above, with gating pulled forward, gets the same safety with less rework.

---

## 21. Review checkpoints

| Checkpoint | After | What the owner reviews |
|---|---|---|
| **CP-1** | 1B | Manifest v1.1 field set and the reader-adapter approach, before any generated artifact depends on it |
| **CP-2** | 1C | The gating rule, ceremony surface, and audit event schema — the research-integrity core |
| **CP-3** | 1D | MarketFrame's *public surface* specifically: has anything DataFrame-shaped crept in? |
| **CP-4** | 1F | Generated view manifests + binding evidence + audit trail, **before** any research uses them |
| **CP-5** | 1H | Full Phase 1 completion checklist + independent review |

CP-3 deserves emphasis: ADR-0003's failure mode is gradual, and the only moment it is cheap to catch is before Phase 2 starts adding "just one convenience method".

---

## 22. Phase 1 completion checklist

- [ ] All Phase 1 contracts implemented: NativeTimeKey, ClockDomain, DatasetRef, manifest v1.1, registry, integrity, MT5 adapter, MarketFrame, BoundedView, ViewUnion, GapIndex, gating, audit
- [ ] `./scripts/gate.sh` green; architecture tests green and **extended**, never weakened
- [ ] Dataset root byte-identical — verified by checksum against the Phase 0 baseline
- [ ] 5 view manifests generated, validated, hash-pinned; hashes recorded in PROJECT_STATE.md
- [ ] Default discovery returns **TRAIN + VALIDATION only** (test 27)
- [ ] OOS explicit and audited from day one (test 28)
- [ ] RAW / CURATED / SNAPSHOT / LOCKBOX gated; ceremony required (tests 29–30)
- [ ] **No data returned before audit success** (test 31, fault-injected)
- [ ] No UTC invention; no `utc` identifier in the data layer (tests 11, 37)
- [ ] Cross-clock-domain comparison refused (test 10)
- [ ] Context/eval semantics and refusals tested (tests 19–24)
- [ ] Adjacent-view union representable and validated (tests 25–26)
- [ ] GapIndex arithmetic-only; semantic vocabulary architecture-tested (tests 33–34)
- [ ] Snapshot binding keys-only, audited, recorded; post-cap rows unreachable (tests 35–36)
- [ ] Performance budgets measured and recorded: warm < 2 s, cold < 5 s, RSS < 1 GiB
- [ ] Legacy carriers retired or explicitly scheduled, with a test preventing new use
- [ ] PROJECT_STATE.md and V1_ROADMAP.md updated; Git tree clean
- [ ] **Phase 1 independently reviewed** (same pattern as Phase 0B)

**Post-Phase-1 operational reminder (before Phase 2):** create a Production Checkpoint named for Phase 1 completion · create a private Git remote and push · consider a full Hyper-V VM export · review all three before starting Phase 2.

---

## 23. Risks and failure modes

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | MarketFrame accretes DataFrame conveniences | **high** | ADR-0003 defeated; permanent maintenance tax | CP-3 review; the §7 justification test for every method |
| R2 | Implementer reads the gated curated parent "just to test" | **high** without ordering | Establishes the exact habit the lockbox forbids | Gating built in 1C, before any real read |
| R3 | Off-by-one at view boundaries (inclusive vs exclusive) | medium | Silent leakage or silent data loss at every edge | Property tests at both edges; inclusive bounds fixed in the contract and stated once |
| R4 | PyArrow types leak into public signatures | medium | Couples every contract to one library | Architecture test from the moment the dep lands |
| R5 | Audit written *after* file open by a later refactor | low | The core invariant silently voided | Fault-injection test asserting no file handle opened |
| R6 | Generated view manifests edited in place instead of superseded | low | Identity destroyed, results unreproducible | Hash pinning + chmod tripwire + refusal on hash change |
| R7 | Snapshot binding accidentally reads value columns | low | First lockbox contact exceeds its mandate | Column-selective read makes it structural; test asserts it |
| R8 | Legacy + new architectures coexist indefinitely | medium | Two ways to do everything; confusion for future sessions | Retirement commit in 1H; import-ban test |
| R9 | Performance tests become flaky and get disabled | medium | Budgets silently abandoned | Deselected by default; median-based; qualification gate not CI gate |
| R10 | `timestamp_mt5_encoded` gets loaded and treated as UTC | medium | Freeze-proposal risk R12 realized | Never loaded into MarketFrame; architecture test |

---

## 24. Questions requiring owner decision

1. **Approve adding `pyarrow` + `numpy`?** Nothing in Phase 1 can read data otherwise. *(blocking)*
2. **Views root location** — recommend `/path/to/research-views/` as a sibling of the dataset root. Alternative: under the runs root. *(blocking 1F)*
3. **Gating-boundary source** — approve a versioned split-policy record declaring the boundary key (currently `1767225540`), with default-deny for unknown ranges? *(blocking 1C)*
4. **Single integrity mode** — approve implementing `full` only and deferring `cached`? (31 ms measured for the whole dataset.)
5. **ClockDomain id** — confirm `mt5.justmarkets-demo3.wallclock`, declared in the MT5 adapter and recorded in every generated manifest.
6. **Confirm `timestamp_mt5_encoded` is never loaded** into MarketFrame.
7. **Cold-benchmark procedure** — will you run the one-off `drop_caches` measurement with sudo at qualification, or should cold performance be recorded as "not measured on this machine"?
8. **Snapshot cap** — confirm inclusive `2026-07-31T23:59:00` → key `1785542340`, bound = largest observed key ≤ cap.
9. **Audit log** — confirm `runs/audit.jsonl` for all gated access *including* view-creation events.
10. **GapIndex** — confirm lazy/ephemeral in Phase 1 (no persistence).

---

## 25. Exact recommended first implementation task after approval

**Slice 1A — core time and identity primitives.** Concretely: add `src/trading_research/core/time.py` (`TimeUnit`, `ClockDomain`, `NativeTimeKey`, `CalendarTimePolicy` with the single `native-wallclock-v1` member, and the cross-domain comparison refusal), add `src/trading_research/core/identity.py` (`ViewRole`, `DatasetRef`), and extend `core/errors.py` with `ClockDomainError` and the other new error types. Add unit tests 10–14 and extend `tests/test_architecture.py` with the "no `utc` identifier in core/data" rule. Retire nothing; import nothing new; touch no dataset.

It is the right first task because it depends on no owner decision, adds no dependency, is pure addition with a trivial rollback boundary, and every subsequent slice types against it. It can begin the moment Phase 1 is authorized — **but slices 1C, 1D and 1F cannot start until decisions 1, 2 and 3 are made.**

---

**Stopping here for owner review.** No files were created or modified, no packages installed, no Git operations performed, no dataset touched, and no lockbox rows inspected.
