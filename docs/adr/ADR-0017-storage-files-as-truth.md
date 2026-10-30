# ADR-0017 — Storage: files as truth, SQLite as rebuildable index, no PostgreSQL

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
Single-user, single-machine platform. Research value lives in run
directories and artifacts; a database that owns the truth becomes a single
point of loss and an always-on operational dependency.

## Decision
**Source of truth:** Parquet (or equivalent columnar) files for bulk
artifacts; JSON manifests and resolved configs; run directories. **SQLite
is a derived, rebuildable query index only** — rebuilding it by scanning
run directories is a tested code path; its corruption is an inconvenience,
never a loss. **No PostgreSQL in V1.** Always retained regardless of
MaterializationPolicy: RunManifest, resolved config, metrics, identity
information, audit events, failure information. **No automatic deletion in
V1**; the platform warns when disk usage reaches 70 %. **Pickle is banned
as a durable research artifact format** (unversionable, unsafe, rot-prone)
— versioned array formats + JSON metadata instead.

## Implementation guidance (recorded 2026-08-06, finding N-5 — no decision change)

Concurrent detached experiment processes ([ADR-0018](ADR-0018-execution-model.md))
share the runs root, so V1 writes must be crash- and race-safe by
construction rather than by luck. Recorded now, implemented in the phase
that first needs it:

- **Artifact and cache writes are atomic**: write to a temporary file in the
  same filesystem, fsync, then `rename()` into place. A `kill -9` mid-write
  must never leave a half-written artifact readable under its final name.
  Content-addressed entries are immutable, so a last-writer-wins rename of
  byte-identical content is safe.
- **The audit log is append-only**: one JSON object per line, written with a
  single append (`O_APPEND`) write and fsync'd before the corresponding
  access is permitted ([ADR-0013](ADR-0013-lockbox-enforcement.md)). Never
  rewrite, compact or reorder it.
- **SQLite (Phase 5 — see the schedule note below) has one explicit writer**
  with a busy timeout and bounded write transactions. The planning default was
  WAL; the implemented atomic-generation design supersedes that journal-mode
  detail in Amendment 2 below. The index is derived, so on unresolvable
  contention the correct fallback is rebuild-from-run-directories, never a
  partial write into truth.

### Schedule note — SQLite moved to Phase 5 (2026-08-13, owner decision OD-3D)

**Implementation-guidance reconciliation only. The decision above is
unchanged.** Files and run directories remain the source of truth, SQLite
remains a derived and rebuildable query index. The busy-timeout, bounded-write
and single-writer guidance applies in full; Amendment 2 records why WAL itself
does not apply to the implemented atomic-generation publisher.

The guidance originally named Phase 3 as the phase that would first need an
index. Owner decision **OD-3D (2026-08-13)** moved persistent SQLite run
indexing to **Phase 5**
([V1_ROADMAP.md](../roadmap/V1_ROADMAP.md),
[PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) §1). Phase 3 ships
only the files-first mechanism `scan_runs()`, which reconstructs structured run
records from run directories; with tens of runs a direct scan is immediate, and
the concurrency-safe `attempt_number` derivation that might otherwise have
required a database is satisfied by
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) Amendment 3's
query-time route.

Because `scan_runs()` is itself the rebuild path, the Phase-5 index is populated
from it and *"rebuilding it by scanning run directories is a tested code path"*
becomes true by construction rather than through a second implementation that
could drift.

> **Corrected by Amendment 1 (2026-08-15) — do not read this paragraph alone.**
> Its conclusion stands: the index is still populated from the files-first
> run-directory scan, and the rebuild path is still not a second implementation.
> Its unstated assumption does not: `scan_runs()` **mutates** — it locks each
> run directory exclusively and can terminalize a stale-heartbeat `RUNNING` run.
> The index must therefore be built through the scanner's **read-only mode**;
> see Amendment 1 clause 3.

## Amendment 1 — the concrete Phase-5 SQLite realization (2026-08-15, Phase-5 planning)

**The decision above is unchanged.** Files remain the source of truth and
SQLite remains a derived, rebuildable query index. This amendment records the
exact realization Phase-5 planning approved, plus one correction to the
schedule note's assumption. Detail lives in
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §6 and §12.

**1. Derived and disposable; files remain authoritative.** The index carries no
research truth. Its loss is an inconvenience and never a loss of results —
which the phase must *prove*, not assert: a test deletes the database, rebuilds
it, and asserts both that the rebuilt contents are identical and that the runs
root is **byte-identical before and after**.

**2. Location — outside the run collection.** The index lives at
**`<runs_root>/index/`**, a sibling of `<runs_root>/runs/`, never inside it.
This is not a preference: the Phase-3 scanner refuses any entry under the run
collection that is not a valid run identifier, so a database or its `-wal` /
`-shm` sidecar placed there would make **every scan raise**. The index also
never lives inside the Git working tree, because an untracked repository file
makes every subsequent run noncanonical.

**3. Index rebuild must use a non-mutating read path — correction to the
schedule note.** The schedule note below states that the Phase-5 index is
populated by `scan_runs()`. Phase-5 planning found that `scan_runs()` is a
**mutating** scanner: it takes an exclusive lock on every run directory and,
for `RUNNING` runs, reconciles state or terminalizes a stale-heartbeat run to
`INTERRUPTED`, publishing a manifest and sealing the directory. Populating an
index through that path would make "rebuild the index" a state-changing
operation and, during concurrent execution, could mark **live runs
`INTERRUPTED`** and write false failure records into the permanent research
record.

The correction is narrow and preserves the intent: the scanner gains an
explicit **read-only mode**, and the indexer uses **only** that mode. The
rebuild path stays the same files-first mechanism, so *"rebuilding it by
scanning run directories is a tested code path"* remains true by construction.
Reconciliation stays an operator-driven action and a single resume preflight,
never a side effect of querying.

**4. Rebuilding or deleting the index must not modify research truth.** No
index operation may write to a run directory, the audit log, the artifact
store or any dataset.

**5. Corruption recovery is rebuild-from-files.** A corrupt, truncated or
schema-mismatched database is dropped and rebuilt. Database state is never
treated as authoritative and never repaired in place. On unresolvable write
contention the fallback is a full rebuild, **never a partial write into
truth** — as the guidance below already required.

**6. SQLite participates in nothing that matters.** No value read from the
index may influence `experiment_fingerprint`, `run_id`, `attempt_number`,
canonicality, run state, gating, audit, or any evaluation result. This is
enforced structurally rather than by convention: the index layer imports from
the experiment layer and **never the reverse**, so the orchestrator cannot
consult it, and the existing per-layer SQLite import bans are narrowed to
permit `sqlite3` only inside the index layer rather than relaxed.

**7. Single writer, busy timeout and bounded transactions — concrete values.**
A busy timeout of 10 s; writes bounded to batches of 200 projected runs; a
single writer, with index writes funnelled through one explicit CLI path and
workers never writing to the index. Amendment 2 supersedes this amendment's
planned WAL journal mode with the atomic-generation publication actually
implemented.

**8. The 70 % disk warning becomes a concrete implementation obligation.** The
platform reads free space on the runs root and **warns at ≥ 70 % used**, as
this ADR requires. Phase 5 additionally **refuses to start a sweep at ≥ 90 %
used** unless an explicit override is supplied and recorded — a preflight
refusal, not a retention mechanism. **No automatic deletion, garbage
collection or retention machinery is introduced**, and that absence is
guarded by test.

## Amendment 2 — publish one complete SQLite generation atomically (2026-08-15, Phase-5 implementation)

**The files-as-truth decision is unchanged.** Implementation review found that
WAL and atomic replacement of the main database file are incompatible
publication models: a committed generation may span `runs.sqlite3`, `-wal`
and `-shm`, while `rename()` publishes only one filesystem object. Replacing
the main file while treating sidecars as part of another generation can expose
a split or incomplete generation to a new reader.

The implemented index therefore uses this narrower crash-consistent design:

1. one advisory-locked writer builds or copies into a private
   `.runs.<uuid>.sqlite3.tmp` database;
2. the private generation uses `journal_mode=DELETE` and `synchronous=FULL`,
   so every committed row required by that generation is contained in the one
   database file;
3. the builder validates foreign keys and `quick_check`, closes the database,
   fsyncs the file, then atomically renames it to `runs.sqlite3` and fsyncs the
   directory;
4. incremental rebuild seeds the private generation with SQLite's backup API,
   skips byte-identical terminal projections, refreshes mutable projections,
   and still publishes a whole new generation;
5. readers open read-only and therefore retain either the complete old inode
   or the complete new inode. A failed or killed builder never edits the live
   generation in place.

`busy_timeout=10000`, the single explicit CLI writer, the 200-run batch bound,
files-first recovery and the one-way dependency boundary remain as planned.
Invocation receipts and dispatcher lock files are operational telemetry, not
index inputs; only immutable `sweep.json` participates in index freshness.

**Qualification evidence (2026-08-15).** At clean commit
`b9f74ea748c399be657335aaf38ac9492da57b7d`, a 1,000-run synthetic root (100
evaluated runs) produced a 974,848-byte index in 0.720 s. Deletion plus full
rebuild reproduced the canonical table content exactly in 0.719 s; incremental
rebuild completed in 0.570 s; files-only and index-backed Trial Ledgers were
identical. Real temporary-root disk usage was 25.22% before and after cleanup,
and the injected 70% warning plus 90% refusal/explicit-override paths passed.
No research-truth or external audit/view byte changed.

## Amendment 3 — bounded pre-production run purge (2026-08-27)

ADR-0011 Amendment 5 authorizes one exact system-test purge. For that operation,
deleting the resolved run directories deletes the authoritative artifacts by
design; the derived SQLite index must then be rebuilt from the remaining files.
The sweep specification and a compact purge receipt remain, but neither is a
substitute for the deleted research truth.

This amendment adds no delete command, retention scheduler, TTL or garbage
collector. It does not weaken the dataset, audit-log, freeze, report or cache
boundaries. Production retention remains append-only unless a later accepted
ADR explicitly changes it.

## Alternatives considered
- PostgreSQL now — multi-writer robustness we do not need, an always-on
  service to run; reversal of an earlier bootstrap-phase assumption, made
  explicitly.
- Files + JSON only, no index — comparison queries become painful scans.
- DB-as-truth — corruption or schema migration risks the research record.

## Consequences
Backup = rsync of the runs root and dataset root. The index schema can
evolve freely (drop and rebuild). Disk telemetry matters (59 GB free at
freeze time); GC is deliberately deferred.

## When to reconsider
Concurrent multi-user writers, remote workers, or relational queries
SQLite demonstrably cannot serve.

## Related
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) ·
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md) (the Trial Ledger must be
reproducible from files without the index) ·
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §6, §12 ·
V1.md §8.
