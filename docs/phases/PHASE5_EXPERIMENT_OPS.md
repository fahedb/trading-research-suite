# Phase 5 — Experiment Operations — Implementation Record

Date: 2026-08-15 · Closeout: 2026-08-16 ·
Status: **PHASE 5 COMPLETE; SLICES 5A–5I IMPLEMENTED; §19
QUALIFICATION GREEN AT `b9f74ea748c399be657335aaf38ac9492da57b7d`;
REMEDIATION REQUALIFICATION GREEN AT
`ecb9e5d7d89df6363b8b3b0d6a0c1d32471c1ee1`;
F-7 LOCK REMEDIATION REQUALIFICATION GREEN AT
`717fc039f8b70c5eecdf1efae1a2d0b00420f356`;
FINAL SUSPENDED-WORKER TEST ORDERING FIX AT
`718b799ed10f0d3e7f0e9c9644b75555fd30d888`;
CP-17…CP-21 GREEN; CP-20 INDEPENDENT REVIEW PASSED AND OWNER APPROVED
2026-08-16; PHASE-5 CLOSEOUT AND MERGE OWNER APPROVED**
Architecture: [docs/architecture/V1.md](../architecture/V1.md) ·
Contract: [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md)
(ACCEPTED 2026-08-15) ·
Amended for this phase: [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md)
Amendment 4 · [ADR-0016](../adr/ADR-0016-determinism-tiers.md) Amendment 1 ·
[ADR-0017](../adr/ADR-0017-storage-files-as-truth.md) Amendments 1–2 ·
[ADR-0018](../adr/ADR-0018-execution-model.md) Amendment 1

> **This document is normative for Phase-5 implementation**, in the role
> [PHASE3_RESEARCH_RUNS.md](PHASE3_RESEARCH_RUNS.md) and
> [PHASE4_SIMULATION.md](PHASE4_SIMULATION.md) play for their phases. Where it
> and an ADR differ, the ADR governs. Where it and chat history differ, this
> document governs: **the Git repository is the source of truth.**

> **The governing principle of this phase: Phase 5 scales experiments, not
> infrastructure.** Every component below must either protect research
> correctness or be broadly useful across materially different research
> families. "We may need it later" is not a justification. Phase 5 adds **no**
> evaluator, metric, model, policy, outcome, simulator behaviour, dataset view
> or gate relaxation, and changes **no** research methodology.

> **Implementation/closeout status note (2026-08-16).** The production surface
> now includes the non-mutating run projection, atomic-generation SQLite index,
> typed query/comparison commands, deterministic sweep planner, bounded
> whole-run dispatcher, resource controls, disk preflight and files/index
> Trial Ledger. `trading_research.ops` is the top-level composition layer so
> the one-way `index -> experiments` dependency remains intact. The unfiltered
> default gate and clean-commit §19 qualification/requalifications are green;
> exact evidence is recorded in §§19.5–19.7. The bounded review chain passed at
> reviewed head `22c08a27ef9f052e3c00cf605cc10c2962d1469a`; the owner explicitly
> approved CP-20, Phase-5 closeout and merge on 2026-08-16.

---

## 0. Contents and coverage map

| § | Content |
|---|---|
| 1 | Owner-approved decisions **OD-5A…OD-5F** |
| 2 | Confirmed repository starting point |
| 3 | Discovered contradictions **C-1…C-9** and their approved reconciliations |
| 4 | Objective and non-objectives |
| 5 | Approved architecture — components and authority |
| 6 | Files-first / SQLite authority boundary |
| 7 | Non-mutating run projection — the requirement |
| 8 | Sweep semantics |
| 9 | Parallel execution: whole runs only |
| 10 | Resource and thread-clamp contract |
| 11 | Resume and recovery contract |
| 12 | SQLite schema, rebuild and disposability contract |
| 13 | Comparison / query boundaries |
| 14 | Trial Ledger semantics |
| 15 | OOS and LOCKBOX scale-out safeguards |
| 16 | Slices 5A…5I, in order |
| 17 | Formal checkpoints CP-17…CP-21 |
| 18 | Test matrix |
| 19 | Performance qualification protocol |
| 20 | Architecture-guard changes — narrowed, never weakened |
| 21 | Documentation obligations per checkpoint |
| 22 | Explicit implementation exclusions |
| 23 | Exact first implementation action |
| 24 | Phase-5 completion checklist |
| 25 | Explicit Phase-6+ deferrals |

---

## 1. Owner-approved decisions — **OD-5A…OD-5F APPROVED 2026-08-15**

Recorded exactly as approved. They are not reopened by implementation.

### OD-5A — Multiple-testing output: the Trial Ledger only — **OWNER APPROVED 2026-08-15**

V1 ships the **Trial Ledger / honest denominator only**.

**No** deflated statistic, adjusted metric, p-value, significance claim,
effective-independent-trial estimate, Deflated Sharpe Ratio, Bonferroni,
Šidák, Benjamini–Hochberg, White's Reality Check, Hansen's SPA, or any
equivalent statistical correction ships in Phase 5.

The platform reports exactly how many distinct evaluated
`(hypothesis, question)` pairs were tried under the §14 counting rules,
together with a **mandatory caveat** stating that related variants may be
correlated and that the effective number of independent trials is **not**
estimated.

**The existing production-code guard against the term `deflated` is not
weakened.** It becomes the mechanical enforcement of this decision.

The full methodology contract is [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md).

### OD-5B — Trial-report default scope — **OWNER APPROVED 2026-08-15**

The default scope of the trial report is **all runs in the runs root**, with
explicit narrowing options (by sweep, by time range).

The default must expose the **cumulative research-search burden**, not a
flattering local subset. Narrowing is always an explicit act by the
researcher and is recorded in the report header.

### OD-5C — Parallelism scope: whole runs only — **OWNER APPROVED 2026-08-15**

Phase 5 parallelizes **whole runs only**. **Folds remain sequential inside
each run.**

No queue daemon, remote workers, distributed execution, nested process pools
or fold-level parallelism enters V1. The roadmap wording that implied
fold-level process-pool execution is corrected accordingly
([V1_ROADMAP.md](../roadmap/V1_ROADMAP.md) Phase 5).

**Reason recorded with the decision:** folds execute inside the orchestrator
that owns the fit lifecycle, the derived purge and the TrainingTargetFrame
channel. Parallelizing them buys single-run latency only — run-level
parallelism already saturates this machine for a 100-run sweep — at the cost
of touching the code whose correctness CP-9…CP-12 established.

### OD-5D — Default worker and resource policy — **OWNER APPROVED 2026-08-15**

Phase-5 **planning defaults**:

| Setting | Default |
|---|---|
| Maximum workers | **8** |
| Assumed per-run admission RSS | **1 GiB** |
| Total memory budget | **16 GiB** |
| Qualification concurrency | **10 concurrent workers**, explicitly exercised |

The runtime must still cap effective concurrency against available CPU and
the memory budget when implemented (§10.2).

**These are Phase-5 planning defaults, not a claim that every future model
consumes 1 GiB.** The 1 GiB admission figure is a declared budgeting
constant chosen with headroom over the measured Phase-4 peak of
523,923,456 B; qualification reports the *actual* peak against it (§19).

### OD-5E — Fold identity in the evaluation inventory — **OWNER APPROVED 2026-08-15**

**Do not** bump the evaluation-inventory schema merely to add `fold_index`.

V1 keeps the existing representation and relies on the current
**one-model-per-config invariant**: `ResolvedExperimentConfig` carries
exactly one `_model`, so within one run the distinct `fitted_state_identity`
values under one *question* are the folds.

An **executable invariant / canary test is planned for Phase 5** (T-18,
Slice 5H). If that invariant ever ceases to hold, **the test must fail
loudly and the schema decision must be reopened before trial counting
continues.**

### OD-5F — Plane-B question and plural audit linkage — **OWNER APPROVED 2026-08-15**

The Plane-B Trial-Ledger question is `(policy origin, execution_spec,
evaluator origin, metric_name)`. Plane-A OutcomeSpec, horizon, decision-delay
and evaluated-output-column dimensions do not exist on Plane B and are not
inferred or invented there. Model, data and config distinctions continue to
come from `experiment_fingerprint`.

Run-to-audit linkage is an ordered `access_audit_refs` collection, not a
singleton. Each authorization invocation records its UUID4 `session_id`
before calling the gate and attaches the optional `audit_event_id` returned
on success. This supports the current zero-or-one event per authorization,
denied access (where the event is written before the gate raises), and future
multiple authorizations per run without changing the frozen audit schema.
A run may legitimately reference more than one audit event. The audit log
remains authoritative; the RunManifest stores only these minimal links and
does not duplicate audit truth. The exact contract and rationale are recorded
in ADR-0024 §7.

**Approval provenance correction (independent pre-merge review F-1).** The
review correctly required explicit owner provenance rather than an
implementation self-attestation. The owner then explicitly approved OD-5F in
full on **2026-08-15**, covering both the Plane-B question above and the plural
run↔audit linkage. This note records that approval when it occurred; it does
not rewrite the historical implementation sequence and changes no behavior.

---

## 2. Confirmed repository starting point

Verified against the repository, not assumed, at planning time.

| Item | Verified value |
|---|---|
| Branch at verification | `main` |
| HEAD at verification | `64fc15f0176dad022ca7c6f7cf528bfcd5f6a222` (*docs: close phase 4 after CP-16*, 2026-08-15) |
| Working tree | CLEAN |
| Phase status at planning time | Phases 0–4 COMPLETE; CP-1…CP-16 green; Phase 5 implementation had not started |
| Open external item | `COST_AUDIT` OPEN; `cost_semantics: verified` unreachable; closure is CP-13C and gates no Phase-5 slice |

**Machine measured at planning time** (grounds every resource figure in §10
and §19):

| Resource | Measured |
|---|---|
| Logical CPUs | 12 |
| RAM | 22 GiB total |
| Filesystem `/` | 76 G size, 57 G available, **22 % used** |
| Runs root | 236 M |
| Phase-4 full-axis TRAIN run | peak RSS **523,923,456 B**, median **1.325 s** |
| Phase-1 SNAPSHOT load | peak RSS 592,191,488 B |

### 2.1 Existing capabilities Phase 5 reuses — **do not rebuild**

| Capability | Location | Phase-5 use |
|---|---|---|
| Files-first run reconstruction | `scan_runs()`, `experiments/runs.py` | Index source and resume, **with the C-1 correction** |
| Terminal run record | `RunManifest`, `experiments/runs.py` | Primary index source |
| Evaluation inventory | `evaluation_inventory.json`, schema `research-evaluation-inventory-v2` | **The Trial Ledger's only raw material** |
| Run state machine and heartbeats | `RunState`, `_start_run`, `_heartbeat_run`, `_finish_run` | Resume — **no second state machine** |
| Attempt derivation | query-time grouping by fingerprint (ADR-0011 Amendment 3) | Unchanged |
| Run registration | `_register_experiment()` — registers PENDING without reading dataset rows | Sweep child registration, unchanged |
| Detached worker | `experiments/worker.py`, `_launch_detached()` | Pool dispatch target |
| Content-addressed artifact cache | `artifacts/store.py` — `flock` + `O_EXCL` temp + `fsync` + `link()` | Cross-worker reuse; already concurrency-safe |
| Append-only audit | `data/audit.py` — locked `O_APPEND` + `fsync` before access | Unchanged; parallel-safe |
| Atomic publication | `data/atomic_write.py` — `mkstemp(dir=parent)`, never consults `TMPDIR` | Unchanged |
| Walk-forward folds and derived purge | `experiments/folds.py` | Unchanged; **sequential** (OD-5C) |
| Determinism verification | `verify_experiment()`, cache-bypassing | Unchanged |
| CLI | `run` · `status` · `list` · `verify` | Extended, never replaced |
| Config contract | `ResolvedExperimentConfig` — exactly one `_model`, one `_folds`, ≤ one `_execution` | Sweep axes are config fields; **no new config concept** |

### 2.2 Missing capabilities at planning time — the implemented Phase-5 delta

1. No SQLite anywhere, and the architecture guards actively assert its absence.
2. **No BLAS/OpenMP thread clamp exists anywhere** — an undischarged ADR-0016
   obligation, not a new feature (C-3).
3. No sweep concept.
4. No process pool.
5. No disk telemetry and no 70 % warning.
6. No trial-denominator arithmetic.
7. No non-mutating way to read run truth (C-1).
8. No run projection richer than the private 11-field `_RunRecord` (C-2).

---

## 3. Discovered contradictions **C-1…C-9** and approved reconciliations

Recorded because implementation must not re-derive them, and because several
correct a statement in an accepted document.

### C-1 — `scan_runs()` mutates the runs it reads — **BLOCKER for Slice 5B**

The roadmap and [ADR-0017](../adr/ADR-0017-storage-files-as-truth.md)
describe the index as *populated by `scan_runs()`*. In fact `scan_runs()`
takes an **exclusive `flock` on every run directory** and, for `RUNNING`
runs, reconciles a run that already has a manifest (rewriting `state.json`,
sealing the directory) or — when the heartbeat exceeds the caller-supplied
`stale_after_ns` — **writes a failure inventory, terminalizes the run to
`INTERRUPTED`, publishes `manifest.json` and seals the directory**.
`--stale-after-ns` is a required CLI argument with no default.

**Consequence.** An index rebuild would be a state-changing operation, and a
refresh during a sweep with a too-small threshold would mark **live children
`INTERRUPTED`** and write false failure records into the permanent research
record.

**Approved reconciliation.** Add a **read-only mode** to the Phase-3 scanner
(§7). The indexer **never** reconciles. Reconciliation remains exactly where
it is today — the operator-driven `status` / `list` path — plus the single
resume preflight of §11.3.

### C-2 — the private run record is too thin for comparison

`_RunRecord` carries 11 fields and no metrics, timestamps, config or
inventory detail. The comparison target and the Trial Ledger both need the
inventory entries and their `MetricValue`s.

**Approved reconciliation.** Do **not** write a second reader. Add one
richer projection that reuses the *same already-validated* readers
(`_read_and_validate_manifest`, `_validated_inventory_document`,
`_read_registered_config`, `_read_registered_identity`). Two projections,
one parser.

### C-3 — ADR-0016 requires BLAS clamps that do not exist, and the two launchers differ

ADR-0016 states *"workers run `OMP_NUM_THREADS=1` etc."* and ADR-0018 repeats
it. No clamp exists in `src/`, `tests/` or `scripts/`. `_launch_detached()`
passes **no `--setenv`**; `systemd-run --user` does not inherit the invoking
shell's environment while the tmux fallback does, so the same run can execute
under two different environments.

**Verified non-consequence, recorded so it is not over-claimed:** the missing
`TMPDIR` is currently harmless — the only `tempfile` user,
`data/atomic_write.py`, passes an explicit `dir=parent`, so `TMPDIR` is never
consulted. The environment gap is nevertheless real.

**Approved reconciliation.** Slice 5D (§10.3): one environment-construction
function, identical for both launchers and for pool children, with a
fail-closed worker assertion. This is an **ADR-0016 repair**, recorded in
[ADR-0016](../adr/ADR-0016-determinism-tiers.md) Amendment 1 as *planned,
not yet implemented*.

### C-4 — the roadmap implied fold-level pooling — **resolved by OD-5C**

Folds execute in a sequential loop inside the orchestrator that owns the fit
lifecycle. **Runs only.** The roadmap wording is corrected.

### C-5 — the evaluation inventory records no fold identifier — **resolved by OD-5E**

Folds are *inferable* (distinct `fitted_state_identity` within one question,
valid because a config carries exactly one `_model`) but not *recorded*.
V1 keeps the representation and pins the invariant with T-18.

A second, honest limitation recorded here because §13 and §14 depend on it:
**metrics are computed per fold and no cross-fold aggregate is stored.** Any
single number the comparison layer prints across folds is a **descriptive
summary computed by the CLI**, never a pooled estimator with a known
sampling distribution, and must be labelled as such.

### C-6 — the architecture guards forbid what Phase 5 must build

`tests/test_architecture.py` forbids the term `deflated` anywhere in the
package, asserts `"sqlite" not in source.lower()` for `runs.py` and
`run_identity.py`, forbids SQLite vocabulary across the Slice-3I public
surfaces, and bans `sqlite3` imports per layer. `ALLOWED` has no index layer.

**Approved reconciliation.** SQLite is introduced in a **new top-level layer**
added to `ALLOWED` with budget `{core, experiments}` — **not** by relaxing
any existing ban. Every current SQLite ban stays verbatim. **The `deflated`
ban is not amended at all** (OD-5A). See §20.

### C-7 — the run collection refuses any non-run entry

`scan_runs()` raises `_RunCorruptionError` for any name under
`<runs_root>/runs/` that is not a valid ULID run_id.

**Consequence.** Placing the database or its `-wal` / `-shm` sidecars there
would make **every scan raise**.

**Approved reconciliation.** The index lives at **`<runs_root>/index/`** — a
sibling of the run collection, never inside it. Recorded in
[ADR-0017](../adr/ADR-0017-storage-files-as-truth.md) Amendment 1 and pinned
by T-15b.

### C-8 — untracked repository files make every run noncanonical

`_platform_evidence()` runs `git status --porcelain=v1 -z --untracked-files=all`;
any non-empty result marks the run noncanonical.

**Consequence and rule.** Sweep specifications, the index and every generated
report **must** live under the runs root, outside the repository working
tree. This is an invariant, not operator discipline.

### C-9 — `"trial denominator → deflated metrics"` presupposes a statistic the platform does not produce — **resolved by OD-5A**

Evaluators return `MetricValue(name, value, effective_samples)` — a metric
with no null distribution. There is no p-value anywhere in the repository,
and Phase 4 excluded every ratio statistic for lack of a verified account
base. Every named deflation method needs an input the platform does not
record.

**Approved reconciliation.** Ship the denominator, not the deflation
(OD-5A, ADR-0024). The roadmap clause is corrected.

---

## 4. Objective and non-objectives

### 4.1 Objective

**Operational.** Make it routine and safe to execute, survive, resume, find
and compare *many* runs on one machine — without adding a second source of
truth and without touching the research contracts Phases 1–4 proved.

**Research.** Make the cost of searching visible. Phase 5 is where the
platform becomes able to say **"this result is the best of N things we
tried"** — and it must state N honestly before it states anything adjusted.

### 4.2 In scope

1. Deterministic sweep specification and expansion into complete, ordinary
   child runs.
2. Bounded process-pool execution of **whole runs** on one machine (OD-5C),
   with enforced thread clamps and admission-controlled memory (OD-5D).
3. Files-first resume: after any interruption, execute **only missing valid
   work**.
4. A derived, disposable SQLite index rebuilt from run directories.
5. A comparison / query CLI over that index.
6. The **Trial Ledger** (§14, ADR-0024, OD-5A/OD-5B).
7. Disk telemetry with the ADR-0017 70 % warning.
8. The ADR-0016 thread-clamp repair.

### 4.3 Non-objectives — **explicit**

| Excluded | Why |
|---|---|
| Queue daemon | ADR-0018: *"No queue daemon in V1."* Files already hold the state a queue would. |
| Distributed / remote workers | ADR-0018's reconsideration trigger; would also reopen ADR-0011's machine-identity question. |
| Dashboards | Roadmap exclusion. A CLI answering §13.2 is the whole requirement. |
| PostgreSQL | ADR-0017: *"No PostgreSQL in V1."* No reconsideration trigger is met. |
| Generic cluster scheduler | Fails the justification test. |
| Portfolio / multi-symbol infrastructure | ADR-0015 Amendment 1. A symbol sweep would be a sweep of runs needing no new machinery. |
| Retention / GC / automatic deletion | ADR-0017 (*"No automatic deletion in V1"*, *"GC is deliberately deferred"*) and [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §6. **Telemetry only.** |
| Plugin-specific experiment abstractions | Phase 6 has not defined its needs. |
| **Fold-level parallelism** | OD-5C. |
| **Nested parallelism of any kind** | OD-5C. |
| **Any deflated / adjusted statistic, p-value or significance claim** | OD-5A, ADR-0024. |
| ADR-0011 Amendment 2 hypothesis/comparison key | It *merges* runs, which shrinks the denominator — the unsafe direction. Over-segmentation is conservative; leave it. |
| Query DSL, ORM, generic repository layer, migration framework | A fixed set of parameterised statements serves §13; the index is dropped and rebuilt rather than migrated. |
| Automatic retry of `FAILED` children | A `FAILED` run is a real contract or platform failure; silent retry converts a signal into noise. |

---

## 5. Approved architecture

Six components. Each is justified by protecting correctness or by broad
research utility, and each declares whether the state it owns is
authoritative or derived.

```
                 files = truth (unchanged Phase-3 run directories)
                              ^                 ^
        (read-only projection)|                 | (register + execute, unchanged)
                              |                 |
   +--------------+   +-------+--------+   +----+---------+   +-------------+
   | RunIndex     |<--| RunProjection  |   | SweepPlanner |-->| WorkerPool  |
   | (SQLite,     |   | (5A, read-only)|   | (5E)         |   | (5F)        |
   |  derived)    |   +----------------+   +--------------+   +-------------+
   +------+-------+                                                  |
          |                                                    ResourcePolicy (5D)
   +------+--------+        +--------------+                   thread clamps,
   | CompareCLI    |        | TrialLedger  |                   admission control,
   | (5C)          |        | (5H)         |                   disk telemetry (5G)
   +---------------+        +--------------+
```

| Component | Why it exists | Justification | Composes with | State owned | Authority | Failure / recovery |
|---|---|---|---|---|---|---|
| **RunProjection** (5A) | C-1, C-2 | Protects correctness — a query can no longer kill a live run | `runs.py` internals only | none | none (a view) | Corrupt run raises as today; reports per-run errors; never repairs, deletes or hides |
| **RunIndex** (5B) | 1,000 directory scans cannot answer a query in < 1 s | Broadly useful; **zero** correctness authority | `RunProjection` only | `<runs_root>/index/` | **derived, disposable** | Any error ⇒ drop and rebuild; never a partial write into truth |
| **SweepPlanner** (5E) | Reproducible, resumable, auditable enumeration of what was tried | Protects correctness (the denominator's raw material) and broadly useful | `ResolvedExperimentConfig` + existing identity path | `<runs_root>/sweeps/<sweep_id>/` | **orchestration metadata, never research truth** | Expansion is pure and repeatable |
| **WorkerPool** (5F) | 12 cores, runs launched one at a time | Broadly useful; dispatches the **existing** worker unchanged | `_register_experiment()` + `worker.py` | **in-memory only** | none — deliberately volatile | Child crash ⇒ its own state machine; pool crash ⇒ nothing to repair |
| **ResourcePolicy** (5D, 5G) | ADR-0016 clamp obligation; ADR-0017 70 % warning | Protects determinism and the research record | both launchers and the pool | none | none | Fail-closed: a worker without correct clamps refuses to start |
| **TrialLedger + CompareCLI** (5C, 5H) | §13, §14 | The ledger protects research integrity | `RunIndex` for speed; **every number reproducible from files alone** | none | none | stateless |

**Deliberately absent:** scheduler state file, job table, run→sweep
back-reference inside child run directories, ORM, repository abstraction,
migration framework, plugin registry.

---

## 6. Files-first / SQLite authority boundary

**INV-IDX-1 (normative).** No value read from the index may influence
`experiment_fingerprint`, `run_id`, `attempt_number`, `canonical`, run state,
gating, audit, or any evaluation result.

Enforced mechanically by two things, not by convention:

1. **Layer direction.** `index` imports from `experiments`; **`experiments`
   never imports `index`.** Enforced by the existing `ALLOWED` dependency-budget
   test, so the orchestrator *cannot* consult the index.
2. **The existing SQLite bans stay verbatim** in `runs.py`, `run_identity.py`,
   `core`, `data`, `artifacts` and every Slice-3I public surface.

| Indexed (small, queryable) | Files only (bulk / authoritative) |
|---|---|
| Run identity, state, canonicality, timestamps | Parquet/columnar artifacts, `ModelOutputFrame`, `TradeLog`, `EquityCurve` |
| `outcome_class`, evaluation and policy-execution counts | Artifact **bytes** (content-addressed store) |
| `oos_touched`, `lockbox_touched`, `declared_lockbox_evaluation`, `cost_semantics_unverified`, binding id/digest, price model | Determinism verification reports |
| One row per inventory entry | The audit log — **append-only truth, never mirrored as authority** |
| Scalar metric values with `effective_samples` | Per-fold frames and all intermediate stage output |
| The exact canonical `config.resolved.json` text | Everything else |
| Sweep specs and their child fingerprints | — |

---

## 7. Non-mutating run projection — the requirement

**REQ-PROJ-1.** `scan_runs()` gains a `reconcile: bool` parameter whose
**default preserves today's exact behaviour**. In read-only mode
(`reconcile=False`) the scanner:

- takes a **shared** lock, never an exclusive one;
- performs **no** write of any kind — no `state.json` rewrite, no manifest
  publication, no directory sealing, no failure inventory;
- reports heartbeat staleness as an **observation** carrying the exact
  heartbeat age, so the operator sees the judgement the reconciler would make
  without it being made.

**REQ-PROJ-2.** A richer per-run projection returns manifest + inventory +
resolved config + identity, built **only** from the existing validated
readers. No new parser, no new schema, no new validation.

**REQ-PROJ-3.** `stale_after_ns` is floor-checked. A value below
`10 x heartbeat_interval_ns` is **refused**, so a mis-set threshold cannot
interrupt a healthy worker.

**REQ-PROJ-4.** Every Phase-5 read except the single resume preflight of
§11.3 uses `reconcile=False`.

Proof obligation: T-10b — a recursive hash of the runs root **and** of the
audit log is byte-identical before and after a read-only scan.

---

## 8. Sweep semantics

**Definition.** A sweep is a named, deterministic, ordered expansion of one
parent specification into N complete resolved experiment configurations,
**each of which is an ordinary run in every respect**.

### 8.1 Parent spec vs child configs

`<runs_root>/sweeps/<sweep_id>/sweep.json` (schema
`research-sweep-spec-v1`) contains: a base resolved-config document; an
ordered list of **axes**, each a `(json_pointer, [values])` pair; a
combination rule (`cartesian` in V1, or `zip` for equal-length axes); the
expansion order rule; the `git_commit` at which the sweep was planned; and
the resulting ordered child list of
`(child_ordinal, config_digest, experiment_fingerprint)`.

Child configs are complete and self-contained, indistinguishable from a
hand-written config. **A child run directory contains no reference to its
sweep** (§8.6).

### 8.2 A sweep is not a research result

A sweep produces no metric, artifact or evaluation. It is a *plan of runs*.
Its manifest is orchestration metadata: deleting `<runs_root>/sweeps/` loses
the convenience of resuming and the grouping label and loses **no research
truth** — every child run stands alone with its own manifest and inventory.

This is precisely why a sweep must not become an "experiment hierarchy": a
hierarchy would tempt the platform to attach results to the parent, and the
parent would become authoritative — the second source of truth this phase
forbids.

### 8.3 Child run identity — unchanged

**INV-SWEEP-1 (normative).** No sweep identifier, child ordinal or parent
reference enters `experiment_fingerprint`. A child must be byte-for-byte
reproducible by a researcher who types its config by hand and runs
`trading-research run`. Children are registered through the existing
`_register_experiment()` → `_register_new_run()` path and receive the
ordinary identity triple. Pinned by T-04 and T-04b.

### 8.4 Duplicate child configurations

Expansion computes each child's `experiment_fingerprint` **before** dispatch.
Duplicate fingerprints within one sweep are **collapsed to the first
occurrence** and recorded in the manifest as
`duplicates_collapsed: [{ordinal, duplicate_of_ordinal}]`.

Refusing would be wrong (a duplicate is usually a harmless axis overlap);
executing both would be wrong (it inflates attempts without producing new
information). Collapse is **visible and recorded**, so the ledger reports
"planned 100, distinct 97" rather than silently claiming 100.

### 8.5 Deterministic generation order

**INV-SWEEP-2 (normative).** Expansion order is the **odometer order of the
declared axis list, last axis varying fastest**, with each axis's values kept
in exactly the order written in the spec — never sorted, never de-duplicated
by the planner. `child_ordinal` is assigned in that order and is stable
across re-expansion of the same spec.

### 8.6 Comparison groups

Membership is represented **through the sweep manifest's child fingerprint
list and nothing else**. To find a sweep's runs: read `sweep.json` →
fingerprints → select runs with those fingerprints.

This keeps children ordinary (INV-SWEEP-1), keeps membership derivable from
files, permits a run to belong to two sweeps without a conflicting stored
field, and makes the sweep manifest a pure convenience.

### 8.7 Retries, interruption, commit and tree state

- **Interrupted sweep.** Nothing to repair; re-invoking with the same
  `sweep_id` re-derives remaining work from files (§11).
- **Child `INTERRUPTED`.** Re-executed on resume as a **new `run_id`, new
  attempt, same fingerprint**, reusing the content-addressed cache. Both the
  interrupted and the successful attempt remain permanently recorded.
- **Child `FAILED`.** Not retried automatically; reported, with a non-zero
  exit listing failures. `--retry-failed` is an explicit operator act.
- **Commit change.** The manifest records `git_commit`; resuming at a
  different commit means every child fingerprint has changed (ADR-0011
  Amendment 2), so the planner **refuses** and directs the operator to plan a
  new sweep. Continuing silently would mix two hypothesis sets under one label.
- **Dirty tree.** `sweep run` refuses by default (every child would be
  noncanonical); `--allow-noncanonical` is required and is recorded in the
  invocation receipt.

Each invocation appends one fsync'd, advisory-locked JSON line under the sweep
directory containing the requested/effective worker limits, memory admission,
disk telemetry, OOS acknowledgement state and launch mode, followed by a
completion/error line. Detached launch additionally records whether the
actual launcher was `systemd-run-memorymax` or the honest
`tmux-no-memorymax` fallback. These receipts are observability only: resume
never reads them and the run index ignores them.

---

## 9. Parallel execution — whole runs only (OD-5C)

### 9.1 Model

```
plan (pure, from files) -> pending child list
loop:
  while running < effective_workers and pending:
      child  = pending.pop(0)                 # deterministic order
      run_id = _register_experiment(child)    # existing path, no dataset read
      spawn worker subprocess with the clamped environment
  wait for any child to exit
  record its terminal state by reading its run directory, not the exit code alone
```

- **Process pool, not threads** — reuses the existing per-run process
  isolation.
- The pool holds **no durable state**.
- **Direct `subprocess` children by default** for pooled execution; `--detach`
  uses the existing `_launch_detached()` path. **Both paths receive the
  identical clamped environment** (§10.3), closing C-3.

### 9.2 Determinism under parallelism

**INV-POOL-1 (normative).** A run's artifacts, metrics and manifest must be
independent of how many workers ran concurrently and in what order.

This already holds by construction — separate processes; content-addressed
cache with `flock`-guarded `link()` publication of byte-identical content;
locked-append `fsync`'d audit; atomic temp+rename run state; query-time
attempt derivation. Phase 5 adds nothing to make it true and must only
**prove** it under real concurrency (T-05).

### 9.3 No nested parallelism

Exactly one level: the pool. Folds sequential, evaluators sequential, BLAS
single-threaded. Guarded by an architecture test asserting no
`multiprocessing` / `concurrent.futures` / `ProcessPoolExecutor` symbol
appears outside the pool module (T-29).

### 9.4 Failure semantics

| Failure | Behaviour |
|---|---|
| Worker exits non-zero | Its run directory already records `FAILED` with a reason; the pool reads the directory, never trusts the exit code alone |
| Worker `kill -9` | Run stays `RUNNING` with a frozen heartbeat → the next reconciling contact marks it `INTERRUPTED` → resume re-executes it as a new attempt with cache reuse |
| Dispatcher `kill -9` | Orphan children keep running and complete normally; re-invoking sees them as `RUNNING` and **waits** rather than duplicating them |
| Machine power loss | Every durable write is atomic temp+rename or `O_EXCL`+`link` with `fsync`; `RUNNING` runs go stale and become `INTERRUPTED` |
| Disk full mid-sweep | Publication fails; the run FAILS with a permanent reason and the record stays honest. §10.4 preflight is what prevents reaching this |
| Cache reuse | Unchanged Phase-2/3 semantics; resume is fast precisely because completed stages are content-addressed |
| Audit integrity | Unchanged; parallelism adds contention, not exceptions |

---

## 10. Resource and thread-clamp contract

### 10.1 Approved defaults (OD-5D)

Maximum workers **8** · assumed per-run admission RSS **1 GiB** · memory
budget **16 GiB** · qualification exercises **10 concurrent workers**.

### 10.2 Effective concurrency

```
effective_workers = min(
    requested_workers,                                   # default 8
    max(1, floor(memory_budget_bytes / assumed_peak_rss_bytes)),
    cpu_count - 1                                        # leave one core for the dispatcher
)
```

`memory_budget_bytes` defaults to 16 GiB, is configurable, and is hard-capped
at 80 % of detected total RAM. On the measured machine this yields
`min(8, 16, 11)`; with `--max-workers 10` the qualification target of ≥ 10
concurrent workers within 16 GiB is satisfiable.

### 10.3 Thread clamps — the ADR-0016 repair

Every worker — pooled, detached or foreground-under-pool — receives exactly:

```
OMP_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1
```

Set in the **parent-constructed child environment**, because these are read
when the BLAS library loads — too late to fix from inside the process.
Delivery: explicit `env=` mapping on the `subprocess` path (never implicit
inheritance); one `--setenv` per variable on the `systemd-run` path, which
also removes the launcher divergence of C-3.

**Fail-closed.** The worker asserts all five equal `"1"` at startup and exits
`FAILED` with a permanent reason otherwise.

### 10.4 What is enforced automatically vs documented

| Control | Mechanism | Kind |
|---|---|---|
| Worker count | §10.2 | **Enforced** |
| Thread clamps | Child env + worker startup refusal | **Enforced, fail-closed** |
| Memory ceiling | Admission control (count × assumed RSS) | **Enforced as admission, not as runtime enforcement** |
| Runaway child | `systemd-run --property=MemoryMax=` on the detached path **where user-unit delegation permits**; the mode actually used is recorded | **Best-effort, honestly labelled** |
| CPU oversubscription | 1 BLAS thread per worker × ≤ 11 workers ≤ 12 cores | **Enforced by the clamp** |
| Disk headroom | Preflight refuses to *start* a sweep above the threshold (§10.5) | **Enforced** |
| Per-run peak RSS | Measured in qualification | **Informational** |

The plan does **not** claim runtime RSS enforcement via cgroups as a general
guarantee. Calling admission control "enforcement of workers × peak RSS"
would be dishonest.

### 10.5 Disk telemetry (ADR-0017)

`statvfs` on the runs root; percentage-used reported in index status and at
sweep preflight. **Warn at ≥ 70 %** (ADR-0017's stated threshold); **refuse
to start a sweep at ≥ 90 %** with an explicit, recorded override. **No
deletion, no GC, no retention logic anywhere** (T-26).

---

## 11. Resume and recovery contract

### 11.1 The property

After **any** interruption — child crash, dispatcher `kill -9`, machine
restart — re-invoking the sweep executes **only missing valid work**, and
does so by reading files, never by trusting scheduler state.

### 11.2 Definitions

- **Completed work** — a run directory whose state is `COMPLETE` and whose
  `experiment_fingerprint` equals the child's planned fingerprint. Terminal
  `COMPLETE` is immutable (ADR-0011), so this is durable.
- **Reusable work** — content-addressed cache entries from any prior attempt
  of the same fingerprint. Reuse is automatic and needs no sweep bookkeeping.
- **Missing work** — a planned child fingerprint with no `COMPLETE` run.
- **In-flight work** — a planned child fingerprint with a `RUNNING` run whose
  heartbeat is fresh.
- **Failed work** — a planned child fingerprint whose only terminal runs are
  `FAILED`; not re-executed without `--retry-failed`.

### 11.3 Algorithm

```
resume(sweep_id):
  spec = read <runs_root>/sweeps/<sweep_id>/sweep.json
  refuse unless spec.git_commit == current HEAD
  records = RunProjection.scan(runs_root, reconcile=True)   # the ONE reconciling call
  by_fp   = group records by experiment_fingerprint
  for child in spec.children (ordinal order):
      rs = by_fp.get(child.fingerprint, [])
      if   any COMPLETE                      -> SKIP     (completed)
      elif any RUNNING with fresh heartbeat  -> WAIT     (adopt in-flight)
      elif >1 PENDING                        -> REFUSE   (ambiguous ownership)
      elif exactly 1 PENDING                 -> DISPATCH that existing run_id
      elif any FAILED and not retry_failed   -> REPORT and skip
      else                                   -> DISPATCH a new attempt (missing/interrupted)
```

**Exactly one reconciling call, at resume preflight.** Stale `RUNNING`
children from a previous `kill -9` must become `INTERRUPTED` before they can
be re-dispatched, and `scan_runs()` is the only code path authorised to do
that. Doing it once, with the REQ-PROJ-3 floor applied, is what makes C-1's
hazard safe. Every other Phase-5 read uses `reconcile=False`.

### 11.4 Partially completed folds and runs

A run is atomic at the run level: partial fold work leaves no terminal record,
only cache entries. On re-execution the fresh attempt recomputes the run and
the cache makes completed stages nearly free. **Phase 5 introduces no
fold-level checkpointing** — that would require a second, finer-grained state
machine to save work the cache already saves.

### 11.5 No second source of truth

**INV-RESUME-1 (normative).** The resume decision function reads **only** run
directories and the sweep spec. Proof obligation T-09: `SIGKILL` the
dispatcher mid-sweep, discard everything volatile, restart, and assert the
newly-dispatched set is exactly the complement of the completed set and that
**no `COMPLETE` run is recomputed**.

---

## 12. SQLite schema, rebuild and disposability contract

### 12.1 Shape and location

**One published database** at **`<runs_root>/index/runs.sqlite3`**. A private
`.runs.<uuid>.sqlite3.tmp` generation may exist while its single writer is
building; no live `-wal` / `-shm` sidecars participate in publication. Never
under `<runs_root>/runs/` (C-7). Never inside the repository (C-8).

### 12.2 Schema

```sql
PRAGMA journal_mode = DELETE;          -- one self-contained staged generation
PRAGMA busy_timeout = 10000;          -- 10 s
PRAGMA foreign_keys = ON;
PRAGMA synchronous = FULL;            -- fsync before atomic generation publish

CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: schema_version, built_unix_ns, generation, runs_root, builder_git_commit

CREATE TABLE run (
  run_id                      TEXT PRIMARY KEY,   -- ULID, run-directory name
  experiment_fingerprint      TEXT NOT NULL,      -- deliberately NOT unique
  attempt_number              INTEGER NOT NULL,   -- derived at scan time (ADR-0011 Am.3)
  state                       TEXT NOT NULL,
  canonical                   INTEGER NOT NULL,
  outcome_class               TEXT,
  evaluation_count            INTEGER NOT NULL DEFAULT 0,
  policy_execution_count      INTEGER NOT NULL DEFAULT 0,
  oos_touched                 INTEGER NOT NULL,
  lockbox_touched             INTEGER NOT NULL,
  declared_lockbox_evaluation INTEGER NOT NULL,
  cost_semantics_unverified   INTEGER NOT NULL DEFAULT 0,
  binding_id                  TEXT,
  binding_digest              TEXT,
  execution_price_model       TEXT,
  dataset_id                  TEXT,
  dataset_manifest_sha256     TEXT,
  dataset_role                TEXT,
  platform_git_commit         TEXT,
  platform_git_dirty          INTEGER,
  dependency_lock_sha256      TEXT,
  created_unix_ns             INTEGER,
  started_unix_ns             INTEGER,
  finished_unix_ns            INTEGER,
  failure_reason              TEXT,
  heartbeat_age_ns            INTEGER NOT NULL,
  heartbeat_stale             INTEGER NOT NULL,
  resolved_config_json        TEXT NOT NULL,      -- exact canonical bytes, JSON1-queryable
  record_digest               TEXT NOT NULL       -- staleness key for incremental rebuild
);
CREATE INDEX run_fingerprint_idx ON run(experiment_fingerprint);
CREATE INDEX run_state_idx       ON run(state, outcome_class);
CREATE INDEX run_protected_idx   ON run(oos_touched, lockbox_touched);
CREATE INDEX run_finished_idx    ON run(finished_unix_ns);

CREATE TABLE evaluation (
  run_id                 TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  entry_ordinal          INTEGER NOT NULL,   -- exact position in inventory entries[]
  plane                  TEXT NOT NULL,      -- 'A' | 'B'
  fitted_state_identity  TEXT NOT NULL,      -- distinct values within a question == folds
  model_output_identity  TEXT NOT NULL,
  evaluated_columns_json TEXT,
  outcome_plugin_id      TEXT, outcome_version INTEGER, outcome_params_json TEXT,
  horizon_rows           INTEGER, decision_delay_rows INTEGER,
  policy_plugin_id       TEXT, policy_version INTEGER, policy_params_json TEXT,
  execution_spec_json    TEXT,
  evaluator_plugin_id    TEXT NOT NULL, evaluator_version INTEGER NOT NULL,
  evaluator_params_json  TEXT NOT NULL,
  comparison_tolerance_json TEXT,
  cost_semantics_unverified INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, entry_ordinal)
);
CREATE INDEX evaluation_question_idx
  ON evaluation(plane, outcome_plugin_id, outcome_version, horizon_rows,
                decision_delay_rows, evaluator_plugin_id, evaluator_version);

CREATE TABLE metric (
  run_id            TEXT NOT NULL,
  entry_ordinal     INTEGER NOT NULL,
  metric_name       TEXT NOT NULL,
  value_real        REAL,                 -- NULL when the stored bits are NaN
  value_bits_hex    TEXT NOT NULL,        -- exact big-endian float64, NaN-preserving
  effective_samples INTEGER NOT NULL,
  PRIMARY KEY (run_id, entry_ordinal, metric_name),
  FOREIGN KEY (run_id, entry_ordinal)
    REFERENCES evaluation(run_id, entry_ordinal) ON DELETE CASCADE
);
CREATE INDEX metric_name_value_idx ON metric(metric_name, value_real);

CREATE TABLE policy_execution (   -- executed, NOT necessarily evaluated: never a trial
  run_id           TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  ordinal          INTEGER NOT NULL,
  plugin_id        TEXT NOT NULL, version INTEGER NOT NULL, params_json TEXT NOT NULL,
  intent_schema_id TEXT NOT NULL, intent_rows INTEGER NOT NULL,
  first_key INTEGER, last_key INTEGER,
  PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE access_audit_ref (
  run_id        TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  ordinal       INTEGER NOT NULL,
  session_id    TEXT NOT NULL,
  audit_event_id TEXT,
  PRIMARY KEY (run_id, ordinal),
  UNIQUE (run_id, session_id),
  UNIQUE (audit_event_id)
);

CREATE TABLE sweep (              -- orchestration metadata mirror; deletable
  sweep_id        TEXT PRIMARY KEY,
  spec_digest     TEXT NOT NULL,
  git_commit      TEXT NOT NULL,
  child_count     INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  created_unix_ns INTEGER NOT NULL
);
CREATE TABLE sweep_member (
  sweep_id               TEXT NOT NULL REFERENCES sweep(sweep_id) ON DELETE CASCADE,
  child_ordinal          INTEGER NOT NULL,
  experiment_fingerprint TEXT NOT NULL,
  PRIMARY KEY (sweep_id, child_ordinal),
  UNIQUE (sweep_id, experiment_fingerprint)
);

CREATE TABLE scan_error (         -- honesty: unreadable runs are reported, never hidden
  run_dir_name TEXT PRIMARY KEY,
  error_kind   TEXT NOT NULL,
  detail       TEXT NOT NULL
);
```

**Keys and uniqueness.** `run_id` is the only global identity.
`experiment_fingerprint` is deliberately **non-unique** — attempts and
determinism-verification pairs share it. `(run_id, entry_ordinal)` preserves
the exact file order of `entries[]`, which is what makes every index row
traceable back to `evaluation_inventory.json`.

**No EAV table.** The resolved config is stored as exact canonical text in
one column and queried with `json_extract`, because sweep axes are arbitrary
config paths and enumerating them as columns is impossible. **No ORM and no
generic repository abstraction** — a fixed set of parameterised statements
serves §13 completely.

### 12.3 Rebuild algorithm

```
rebuild(runs_root, mode in {full, incremental}):
  1. Acquire an exclusive advisory lock on <runs_root>/index/.build.lock.
  2. Create a NEW private database at
     <runs_root>/index/.runs.<uuid>.sqlite3.tmp.
     incremental: seed the private database from the published generation
     through SQLite backup; a corrupt source falls back to a fresh schema.
  3. records = RunProjection.scan(runs_root, reconcile=False)   # NEVER reconciles
  4. For each run directory, in sorted run_id order:
       a. copied generation and record_digest + attempt_number unchanged and
          indexed state terminal -> skip
          (terminal runs are immutable per ADR-0011).
       b. Read manifest / state / identity / resolved config / inventory through
          the Phase-3 validated readers.
       c. On any read or corruption failure: insert one scan_error row and CONTINUE.
          Never abort the whole rebuild for one bad run.
       d. Upsert run, evaluation, metric, policy_execution rows.
       e. COMMIT every 200 runs (short transactions, ADR-0017).
  5. Read <runs_root>/sweeps/*/sweep.json -> sweep, sweep_member.
  6. Write index_meta (schema_version, built_unix_ns, generation+1, runs_root,
     builder_git_commit).
  7. Validate foreign keys + quick_check; commit, close and fsync the private
     file; rename() it atomically over runs.sqlite3; fsync the directory.
     A kill -9 at any earlier point leaves the previous generation intact.
  8. Release the lock.
```

### 12.4 Interrupted, incomplete and corrupt runs

- `PENDING` / `RUNNING` — indexed with their state and `NULL` outcome_class,
  `evaluation_count = 0`. **Not reconciled.** A stale heartbeat is *reported*,
  never acted upon.
- `INTERRUPTED` / `FAILED` — fully indexed including `failure_reason`, and
  **permanently visible**; [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §6:
  *"Nothing is ever deleted or hidden."*
- Corrupt — one `scan_error` row. The CLI prints a loud warning and exits
  non-zero whenever `scan_error` is non-empty.

### 12.5 Concurrency

Readers open the published generation read-only while the rebuilder works on
a different private inode. Atomic rename means an existing reader keeps the
complete old generation and a new reader gets the complete new generation.
**`busy_timeout = 10000`** · **bounded transactions** (200 projected runs per
batch) · **single writer** — an advisory lock makes concurrent rebuilds
impossible and **workers never write to the index**. This is ADR-0017
Amendment 2's explicit replacement for the planning-time WAL detail. On an
unresolvable SQLite error: abandon the private generation and instruct or
perform a full rebuild — **never a partial write into research truth**.

Only run-directory sources and immutable `sweep.json` files enter the index
source token. `invocations.jsonl` and `dispatcher.lock` are operational
receipts and deliberately do not stale research queries.

### 12.6 Disposability proof (T-13)

1. Build a fixture runs root spanning every state and both planes: COMPLETE
   evaluated, COMPLETE descriptive, FAILED pre-evaluation, INTERRUPTED,
   RUNNING, PENDING, one deliberately corrupted run, repeated fingerprints
   (attempts) and a sweep.
2. Rebuild → dump every table, ordered, to canonical JSON → `digest_1`.
   Hash every byte of the runs root → `runs_digest_1`.
3. Delete `runs.sqlite3*`.
4. Rebuild → `digest_2`; re-hash the runs root → `runs_digest_2`.
5. **Assert** `digest_1 == digest_2` (modulo `built_unix_ns` / `generation`)
   **and** `runs_digest_1 == runs_digest_2`, the second proving the rebuild is
   genuinely read-only. Assert the audit log is byte-identical too.

Two consecutive full rebuilds over an unchanged runs root must produce
byte-identical row content for every table except `index_meta`.

---

## 13. Comparison / query boundaries

### 13.1 Surface — four new query verbs plus two sweep verbs

The existing four commands (`run`, `status`, `list`, `verify`) are untouched.
The console entry point now targets `trading_research.ops.cli`, the permitted
composition layer that delegates those four verbs verbatim and composes the
one-way experiment/index operations below.

```
trading-research index rebuild  --runs-root R --repository-root REPO
                                [--incremental] [--stale-after-ns NS] [--json]
trading-research index status   --runs-root R [--json]

trading-research runs query     --runs-root R
        [--state ...] [--outcome-class ...] [--canonical|--noncanonical]
        [--fingerprint ...] [--sweep ...] [--dataset-role ...]
        [--oos-touched|--no-oos-touched] [--lockbox-touched]
        [--config-path <json_pointer>=<value>]        # repeatable
        [--metric NAME] [--plane A|B]
        [--order-by finished|metric] [--limit N] [--json]

trading-research runs compare   --runs-root R --metric NAME
        (--sweep ID | --fingerprint F ... | --query-from-file P)
        [--audit AUDIT] [--plane A|B] [--group-by fold|question]
        [--include-protected] [--json]

trading-research trials report  --runs-root R [--audit AUDIT]
                                [--sweep ID | --since NS] [--json]
                                # default: all runs (OD-5B)

trading-research sweep plan     --spec S --data-root D ... --runs-root R
                                --split-policy P --audit A
                                --repository-root REPO --dependency-lock LOCK
                                [--heartbeat-interval-seconds S]
                                [--spend-oos --max-protected-children N
                                 --protected-reason TEXT] [--json]
trading-research sweep run      --runs-root R --sweep ID
                                [--max-workers N] [--memory-budget-bytes B]
                                [--assumed-peak-rss-bytes B]
                                [--retry-failed] [--allow-noncanonical]
                                [--acknowledge-oos-access]
                                [--allow-critical-disk-usage]
                                [--stale-after-ns NS]
                                [--poll-interval-seconds S]
                                [--detach] [--json]
```

When protected run references are in scope, omitting `--audit` makes the Trial
Ledger return a discrepancy and a non-zero status; the index never guesses an
audit path. Normal TRAIN-only scopes remain valid without it.

**Not a query language.** Fixed, named, typed flags mapped to parameterised
SQL. The single general-purpose escape hatch is `--config-path` over
`json_extract(resolved_config_json, ...)`, which exists because sweep axes
are arbitrary config paths. **No raw SQL is ever accepted from the
operator** (T-27). Output is plain text and `--json` only — no new formatter,
no table framework, no colour.

### 13.2 Questions the CLI must answer

1. Every COMPLETE, canonical, evaluated run on TRAIN from the last week,
   newest first.
2. For sweep `S`, children ranked by Plane-A metric `X`, with per-fold values
   and the fold count.
3. Which runs touched OOS, when, and what they evaluated.
4. Has this exact config been run before? (by fingerprint; list attempts)
5. Which runs in this sweep failed, and with what reason.
6. How many distinct hypotheses have been evaluated against VALIDATION in
   total. (feeds §14)
7. Which Plane-B rows carry `cost_semantics_unverified = 1`.
8. Which run directories could not be read (`scan_error`).

### 13.3 Three layers, separated on purpose

| Layer | Does | Must never |
|---|---|---|
| **1. Filtering / index lookup** (`runs query`) | Returns run and inventory rows matching declared predicates; pure retrieval | Compute or derive any statistic |
| **2. Metric comparison** (`runs compare`) | Places already-computed metric values side by side, grouped by the §14 *question* tuple, with `effective_samples` **always shown**; reports per-fold values and a plain cross-fold mean **labelled a descriptive summary, not an estimator** (C-5) | Rank without showing the denominator; hide NaN; produce an adjusted, deflated or significance number |
| **3. Research claims** | Made by the human, in prose, outside the tool | The CLI must never print "significant", "beats", "confirmed" or "edge" |

Layer 3 has no code — that is the point. Every `runs compare` output carries
the §14 trial-context line in its header, so a ranking can never be read
without its denominator.

---

## 14. Trial Ledger semantics

Normative contract: [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md).
Restated here in implementation terms.

### 14.1 The trial rule

> **One trial = one distinct `(hypothesis, question)` pair that produced at
> least one evaluation.**
>
> **hypothesis** := `experiment_fingerprint`
>
> **question** :=
> - **Plane A:** `(evaluated_output_columns, outcome.plugin_id,
>   outcome.version, outcome.params, horizon_rows, decision_delay_rows,
>   evaluator.plugin_id, evaluator.version, evaluator.params, metric_name)`
> - **Plane B:** `(policy.plugin_id, policy.version, policy.params,
>   execution_spec, evaluator.plugin_id, evaluator.version,
>   evaluator.params, metric_name)`
>
> **Collapse and exclusion rules**
> - **Folds collapse** — distinct `fitted_state_identity` within one
>   `(hypothesis, question)` do **not** multiply the count.
> - **Attempts collapse** — multiple `run_id`s sharing one
>   `experiment_fingerprint` do **not** multiply the count.
> - **`descriptive` contributes 0.**
> - **`pre_evaluation_failure` contributes 0.**
> - **Executed-but-unevaluated policies contribute 0** — `policy_executions[]`
>   is reported, never counted.
> - **Policy fan-out affects Plane B only, and only where an actual Plane-B
>   evaluation occurred.**
> - **Planes A and B are counted separately and never summed** into one
>   headline number.

**Every field in that rule exists in the corresponding Plane-A or Plane-B
entry in `evaluation_inventory.json` today.** The rule is fully computable
from files as they are, with no evaluation-inventory schema change (OD-5E,
OD-5F).

### 14.2 Report contents

| Field | Source | Kind |
|---|---|---|
| `plane_a_trials`, `plane_b_trials` | §14.1 | mechanical |
| `distinct_hypotheses` | distinct fingerprints reaching `evaluated` | mechanical |
| `runs_examined`, `attempts_collapsed` | run count vs distinct fingerprints | mechanical |
| `fold_multiplicity` (min/median/max) | distinct `fitted_state_identity` per `(hypothesis, question)` | mechanical, **reported not multiplied** |
| `descriptive_runs`, `pre_evaluation_failures` | `outcome_class` | **excluded from the denominator, shown anyway** |
| `policies_executed_not_evaluated` | distinct `(experiment_fingerprint, policy origin)` in `policy_executions[]` minus Plane-B entries; fold-varying intent shape does not multiply it | mechanical |
| `oos_evaluated_runs`, `oos_access_events` | evaluated in-scope runs and their audit-linked OOS events | mechanical |
| `lockbox_evaluated_runs`, `lockbox_access_events` | evaluated in-scope runs and their audit-linked LOCKBOX events | mechanical |
| `oos_access_events_audit_total`, `lockbox_access_events_audit_total` | relevant OOS/LOCKBOX access events across the whole authoritative audit log; `null` when no audit path is supplied | mechanical, audit-wide |
| `unverified_cost_semantics_entries` | Plane-B `cost_semantics_unverified = 1` | mechanical |
| `noncanonical_runs` | `canonical = 0` | mechanical |
| `duplicates_collapsed` | sweep manifest | mechanical |
| `effective_samples` distribution | `MetricValue.effective_samples` | mechanical |
| `scope` | all runs by default (OD-5B); narrowing recorded explicitly | mechanical |
| **`adjusted_metric` / `deflated_*` / `p_value`** | — | **ABSENT BY DESIGN (OD-5A)** |

### 14.3 Mandatory caveat

Every report and every `runs compare` header carries, unremovably:

> *This is a count of evaluated (hypothesis, question) pairs in the declared
> scope. Folds and attempts are collapsed. Related variants within a sweep are
> counted individually and are NOT statistically independent, so the true
> effective number of independent trials is smaller than this figure and is
> not estimated by this platform. No multiple-testing adjustment has been
> applied to any metric shown.*

### 14.4 Reproducibility from files alone

**INV-LEDGER-1 (normative).** Every number the ledger prints must be
reproducible from run directories without the index. Slice 5H ships a
files-only implementation used solely by the equivalence test T-17, which is
what proves the index carries no statistical authority.

---

## 15. OOS and LOCKBOX scale-out safeguards

Phase 5 multiplies the rate at which
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §2's quiet death of a
test set can happen: a sweep can be pointed at OOS; ranking many variants is
itself selection; and resuming after an OOS look turns the remaining children
into a choice made with protected information.

**All safeguards are additive. No existing gate is loosened.**

- **S-1 — Protected-role sweeps require the existing ceremony per child, plus
  a rate guard.** `sweep plan` inspects **every fully expanded child config**;
  checking only the base config is forbidden because an axis may target the
  dataset identity or role. For `OOS`, expansion refuses unless an explicit
  spend flag is supplied, and the child count is capped by an explicit
  `--max-protected-children N` with **no default**. The flag, the reason and
  the child count are permanently recorded in the sweep manifest. Per-child
  gating, write-ahead audit and `oos_touched` marking are unchanged and still
  apply.
- **S-2 — LOCKBOX sweeps are refused outright.** A sweep is a search;
  RESEARCH_POLICY §3 opens the lockbox once, for a finished strategy with
  frozen parameters. A sweep over frozen parameters is a contradiction in
  terms. Single lockbox runs remain available through the existing unchanged
  ceremony.
- **S-3 — Protected results are quarantined from every default view.**
  `runs query` and `runs compare` exclude `oos_touched` / `lockbox_touched`
  runs unless `--include-protected` is passed; when included, every such row
  carries a protected marker and the header states the access counts.
- **S-4 — Resume across an OOS access is flagged, not silently continued.**
  `sweep run` compares the OOS access count at plan time with the current
  count; an increase requires `--acknowledge-oos-access`. It does not block —
  the check may have been legitimate and unrelated — but it cannot be passed
  through unnoticed.
- **S-5 — The ledger always reports protected access**, cross-checking every
  manifest `access_audit_refs` entry against the append-only audit log. Event
  ID is authoritative when present; session ID preserves the join for a
  denied gate that wrote an event before raising. A normal authorization with
  no audit event must match zero events; a protected run with no authorization
  reference is itself a discrepancy. The reverse check also walks every
  authoritative OOS/LOCKBOX access event: an event not referenced by an
  in-scope run is `audit_unreferenced`. In-scope linked totals and audit-wide
  totals are printed separately, so narrowing by sweep or time cannot hide the
  larger access history. Historical fully-gated RAW/CURATED/SNAPSHOT access is
  excluded by the experiment-role plus classification contract and is never
  mislabelled as an OOS/LOCKBOX run event. Missing, duplicate, unreferenced or
  contradictory protected matches are **reported loudly, never reconciled**.
- **S-6 — Cost quarantine survives the comparison layer.**
  `cost_semantics_unverified` is a first-class filter and is printed on every
  Plane-B row, so the unverified status cannot be lost while copying a number
  out of a table (RESEARCH_POLICY §7, ADR-0020 Amendment 1).
- **S-7 — Nothing is deleted.** No Phase-5 verb deletes, hides or archives a
  run. `--include-protected` changes the default view, never the stored truth.

**Stated plainly, because no mechanism can fix it:** RESEARCH_POLICY §3 —
*"opening the lockbox contaminates the researcher, not just the data … The
flags and audit records track the data; nothing tracks what you now know."*
The Trial Ledger counts evaluations, not knowledge.

---

## 16. Slices, in order

Nine slices derived from real correctness boundaries; each proves one
property. **5A–5I are implemented; 5I's clean-commit execution is green at
`b9f74ea748c399be657335aaf38ac9492da57b7d`.**

### 5A — Non-mutating run projection — **IMPLEMENTED**

- **Objective.** Read run truth with zero side effects and project the full
  run record. Closes C-1 and C-2.
- **Production scope.** In `experiments/runs.py`: the `reconcile` parameter
  and read-only shared-lock path (REQ-PROJ-1); the richer projection built
  from existing validated readers (REQ-PROJ-2); the `stale_after_ns` floor
  (REQ-PROJ-3).
- **Exclusions.** No SQLite. No sweep. No pool. No CLI change. No new state.
- **Dependencies.** None. **This slice can start immediately.**
- **Files.** `src/trading_research/experiments/runs.py`; `tests/test_runs.py`;
  `tests/test_architecture.py`.
- **Tests.** T-10, T-10b.
- **Acceptance.** Read-only scan provably mutates nothing; the existing gate
  stays green; `test_slice_3h_attempts_scan_and_interruption_are_files_only_guards`
  still passes (still no SQLite in `runs.py`).
- **New authority.** **None.**

### 5B — SQLite run index: build, rebuild, disposability — **IMPLEMENTED**

- **Objective.** A derived, disposable index; prove deleting it loses nothing.
- **Production scope.** New layer `src/trading_research/index/`
  (`__init__.py`, `schema.py`, `build.py`) implementing §12 and ADR-0017
  Amendment 2. Consumes
  `RunProjection` only, always with `reconcile=False`.
- **Exclusions.** No CLI verb yet. No sweep. No pool. No metric arithmetic.
- **Dependencies.** 5A; the §20 guard amendment.
- **Files.** `src/trading_research/index/*`; `tests/test_run_index.py`;
  `tests/test_architecture.py`.
- **Tests.** T-11, T-13, T-14, T-15, T-15b, T-28.
- **Acceptance.** T-13 green; rebuild over ≥ 1,000 synthetic runs completes
  with its wall time recorded; **`experiments` does not import `index`**.
- **Checkpoint.** **CP-17.**
- **New authority.** **None** — INV-IDX-1 is enforced by layer direction.

### 5C — Comparison / query CLI — **IMPLEMENTED**

- **Objective.** Answer §13.2 in under a second over 1,000 runs.
- **Production scope.** `index rebuild`, `index status`, `runs query`,
  `runs compare` dispatched from the `ops` composition layer into a new
  `index/query.py` holding the parameterised SQL. S-3 and S-6 enforcement.
- **Exclusions.** No trial counting (5H). **No deflated statistic — ever.**
  No sweep verbs. No query DSL.
- **Dependencies.** 5B.
- **Files.** `src/trading_research/index/query.py`;
  `src/trading_research/ops/cli.py`;
  `tests/test_run_index_query.py`; `tests/test_experiment_cli.py`.
- **Tests.** T-21, T-22, T-24, T-27.
- **Acceptance.** All §13.2 questions answerable; latency measured (formally
  qualified in 5I).
- **New authority.** **None.**

### 5D — Thread clamps and the launch-environment contract — **IMPLEMENTED**

- **Objective.** Discharge ADR-0016's clamp obligation and eliminate the
  launcher divergence (C-3).
- **Production scope.** One environment-construction function producing the
  five §10.3 variables; `--setenv` on the `systemd-run` path; explicit `env=`
  on the `subprocess` path; the fail-closed worker startup assertion.
- **Exclusions.** No pool. No `MemoryMax` work (that is 5F's best-effort item).
- **Dependencies.** None — **may proceed in parallel with 5A–5C.**
- **Files.** `src/trading_research/experiments/worker.py`;
  `src/trading_research/experiments/cli.py`;
  new `src/trading_research/experiments/resources.py`;
  `tests/test_experiment_cli.py`; `tests/test_architecture.py`.
- **Tests.** T-06.
- **Acceptance.** Clamps enforced and fail-closed; both launchers produce
  identical child environments; existing determinism tests green.
- **New authority.** **None** — this repairs an existing obligation.

### 5E — Sweep specification and deterministic expansion — **IMPLEMENTED**

- **Objective.** Prove expansion is deterministic and a child is an ordinary
  run. **No execution.**
- **Production scope.** New `experiments/sweeps.py`: schema
  `research-sweep-spec-v1`; odometer expansion (INV-SWEEP-2); duplicate
  collapse with recording (§8.4); child fingerprint computation **without
  registering, without reading dataset rows and without emitting an audit
  event**, reusing the existing identity code — **never a second fingerprint
  implementation**; the `sweep plan` verb; S-1 and S-2 guards.
- **Exclusions.** No execution, no pool, no index writes, no resume.
- **Dependencies.** 5A.
- **Files.** `src/trading_research/experiments/sweeps.py`;
  `src/trading_research/ops/cli.py`; `tests/test_sweeps.py`.
- **Tests.** T-01, T-02, T-03, T-04, T-04b, T-23.
- **Acceptance.** T-01…T-04b green; the sweep spec lives outside the
  repository (C-8).
- **Checkpoint.** **CP-18.**
- **New authority.** **The sweep specification and expansion manifest —
  declared explicitly as orchestration metadata, not research truth** (§8.2).

### 5F — Bounded process pool and resume — **IMPLEMENTED**

- **Objective.** Execute a sweep within a resource budget and prove that after
  any kill only missing work is recomputed.
- **Production scope.** `experiments/pool.py`: dispatcher; §10.2 concurrency
  computation; resume preflight with the REQ-PROJ-3 floor; the §11.3 decision
  function; in-flight adoption; `--retry-failed`; best-effort `MemoryMax`
  with the mode actually used recorded; the `sweep run` verb; S-4.
- **Exclusions.** **No fold parallelism** (OD-5C). No queue, no daemon, no
  distributed execution, no nested pools. No index writes from workers.
- **Dependencies.** 5D (clamps first), 5E, 5A.
- **Files.** `src/trading_research/experiments/pool.py`;
  `src/trading_research/ops/cli.py`; `tests/test_resume.py`.
- **Tests.** T-05, T-06, T-07, T-08, T-09, T-09b, T-11, T-12, T-29.
- **Acceptance.** INV-RESUME-1 and INV-POOL-1 demonstrated.
- **Checkpoint.** **CP-19.**
- **New authority.** **None** — the pool's state is explicitly volatile.

### 5G — Disk telemetry and sweep preflight — **IMPLEMENTED**

- **Objective.** Discharge ADR-0017's 70 % warning and stop a sweep starting
  into a full disk.
- **Production scope.** §10.5 exactly.
- **Exclusions.** **No GC, no retention, no automatic deletion.**
- **Dependencies.** 5C, 5F.
- **Files.** `src/trading_research/experiments/resources.py`;
  `tests/test_resources.py`.
- **Tests.** T-25, T-26.
- **Acceptance.** ADR-0017's 70 % warning satisfied and tested.
- **New authority.** **None.**

### 5H — Trial Ledger — **COMPLETE — CP-20 GREEN / OWNER APPROVED**

- **Objective.** Ship the honest denominator (§14, ADR-0024). Ship no
  deflated statistic (OD-5A).
- **Production scope.** `index/trials.py` implementing §14.1 exactly; the
  `trials report` verb defaulting to all runs (OD-5B); the §14.3 mandatory
  caveat; the OD-5E fold invariant assertion; OOS/lockbox counts cross-checked
  against the audit log; **a files-only ledger implementation** used solely by
  T-17.
- **Exclusions.** **Every deflated/adjusted statistic, every p-value, every
  significance claim, every effective-N estimate.**
- **Dependencies.** 5B, 5C. Governed by OD-5A, OD-5B, OD-5E and ADR-0024.
- **Files.** `src/trading_research/index/trials.py`;
  `src/trading_research/ops/cli.py`; `tests/test_trial_ledger.py`.
- **Tests.** T-16, T-17, T-18, T-19, T-20.
- **Acceptance.** T-16…T-20 green; the roadmap criterion *"multiple-testing
  report consumes the evaluation inventory and recorded OOS access counts,
  not raw run counts"* satisfied literally.
- **Checkpoint.** **CP-20.**
- **New authority.** **The trial-counting rule**, recorded in ADR-0024.

### 5I — Phase qualification — **IMPLEMENTED; QUALIFICATION GREEN**

- **Objective.** Execute §19 once, cleanly, and record the evidence.
- **Production scope.** A qualification harness under `tests/performance/`
  mirroring `test_phase3_run_budget.py` and
  `test_phase4_simulation_budget.py`, deselected by default.
- **Dependencies.** 5A–5H complete.
- **Files.** `tests/performance/test_phase5_sweep_budget.py`.
- **Tests.** T-30.
- **Acceptance.** §19 hard criteria met; informational measurements recorded
  honestly, including anything not measurable on this machine.
- **Checkpoint.** **CP-21 GREEN** at clean commit
  `b9f74ea748c399be657335aaf38ac9492da57b7d`.
- **New authority.** **None.**

### Dependency graph

```
5A --+-- 5B -- 5C --+--------------- 5H -- 5I
     |              |                 |
     +-- 5E --------+-- 5F -- 5G -----+
5D ------------------+
```

---

## 17. Formal checkpoints

| CP | Name | Green when | Status |
|---|---|---|---|
| **CP-17** | *"the index is not truth"* — after 5B | Building **or deleting** the index changes no byte of research truth (T-13, including the runs-root-unchanged assertion); `experiments` cannot import `index`; corruption resolves to rebuild, never a partial write | **GREEN** — default gate plus §19 index evidence at `b9f74ea` |
| **CP-18** | *"a child is an ordinary run"* — after 5E | Expansion deterministic (T-01, T-03); duplicates recorded not hidden (T-02); no sweep identifier reaches the fingerprint (T-04, T-04b); LOCKBOX sweep refused and OOS sweep ceremony-gated (T-23) | **GREEN** — default gate plus two distinct 100-child sweeps at `b9f74ea` |
| **CP-19** | *"only missing work is recomputed"* — after 5F | After `kill -9` of a child and of the dispatcher, exactly the complement executes and no `COMPLETE` run is recomputed (T-07, T-09, T-09b); clamps active in every child (T-06); determinism independent of worker count (T-05); no nested parallelism (T-29) | **GREEN** — default gate plus forced-kill exact-complement evidence at `b9f74ea` |
| **CP-20** | *"the denominator is honest"* — after 5H · **methodology checkpoint** | §14.1 implemented exactly (T-16); files-only ledger equals index-derived ledger (T-17); the OD-5E invariant canary fails loudly on violation (T-18); **no deflated statistic, p-value or adjusted metric exists anywhere** (T-19); OOS/lockbox counts reconcile with the audit log (T-20); the §14.3 caveat is unremovable. **Requires independent review and explicit owner approval** | **GREEN / OWNER APPROVED 2026-08-16** — independent review and bounded F-1…F-7 follow-ups passed at reviewed head `22c08a27ef9f052e3c00cf605cc10c2962d1469a` |
| **CP-21** | Final scale and performance qualification — after 5I | Every §19 hard criterion H-1…H-10 met, measured once on a clean tree at a named commit, with unmeasurable items recorded honestly | **GREEN** — H-1…H-10 passed at `b9f74ea748c399be657335aaf38ac9492da57b7d` and remained green in remediation requalifications at `ecb9e5d` and `717fc03` (§§19.5–19.7) |

Deliberately **not** checkpoints: 5A (covered by CP-17's evidence), 5C (a
query surface with no new authority), 5D (a contained ADR-0016 repair fully
covered by tests), 5G (threshold arithmetic).

---

## 18. Test matrix

`P` = production test (default gate) · `A` = adversarial / canary ·
`Q` = qualification (deselected by default).

| # | Property | Kind | Slice | Method |
|---|---|---|---|---|
| T-01 | Deterministic sweep expansion | P | 5E | Same spec twice, across processes ⇒ identical ordered child list |
| T-02 | Duplicate children collapsed **and recorded** | P | 5E | Overlapping axes ⇒ `duplicates_collapsed` populated; never silently dropped |
| T-03 | Odometer generation order | P | 5E | Axis permutation ⇒ exact expected ordinal sequence |
| T-04 | Child identity == ordinary run identity | P | 5E | Hand-run child config reproduces the planner's predicted fingerprint |
| T-04b | **No sweep identifier enters the fingerprint** | A | 5E | Two different sweeps yielding one config ⇒ identical fingerprint |
| T-05 | Determinism under concurrency (INV-POOL-1) | P | 5F | Same sweep at 1 and 8 workers ⇒ identical artifact hashes and metric bits |
| T-06 | Nested-thread clamps active | P/A | 5D, 5F | All five variables `"1"` in every child; **worker refuses to start when they are not** |
| T-07 | Worker `kill -9` ⇒ only that child recomputed | P | 5F | Kill one child; exactly one new run_id, zero recomputed COMPLETEs |
| T-08 | Memory admission control | P | 5F | Injected budget/RSS ⇒ worker count capped; boundary values exercised |
| T-09 | **Dispatcher `SIGKILL` ⇒ exact complement executed** | A | 5F | Kill the parent mid-sweep, discard volatile state, restart |
| T-09b | Orphaned children adopted, not duplicated | A | 5F | Live children are waited on, never re-dispatched |
| T-10 | Stale-state interpretation | P | 5A, 5F | Read-only reports staleness; reconcile mode acts; below-floor `stale_after_ns` refused |
| T-10b | **Read-only scan mutates nothing** | A | 5A | Recursive hash of runs root + audit log identical before/after |
| T-11 | Cache reuse on resume | P/Q | 5F | Existing cache identities remain byte/mtime-identical across resume; qualification records resume time and proves only the incomplete fingerprint set is launched |
| T-12 | Audit integrity under parallelism | P | 5F | 8 concurrent gated runs ⇒ valid append-only JSONL, no interleaved lines |
| T-13 | **SQLite deletion + rebuild equivalence** | A | 5B | §12.6 — table dumps equal **and** runs root byte-identical |
| T-14 | SQLite corruption handling | A | 5B | Truncated / garbage / wrong-schema DB ⇒ detected, dropped, rebuilt; no partial write to truth |
| T-15 | Concurrent SQLite access | P | 5B | Reader during rebuild; `SQLITE_BUSY` past timeout ⇒ rebuild instructed |
| T-15b | **Stray file in `<runs_root>/runs/` raises** | A | 5B | Guards C-7 and the index-location rule |
| T-16 | Trial counting matrix | P | 5H | §18.1 below |
| T-17 | **Files-only ledger == index-derived ledger** | A | 5H | Proves the index carries no statistical authority (INV-LEDGER-1) |
| T-18 | **OD-5E fold-collapse invariant canary** | A | 5H | Synthetic multi-model inventory ⇒ loud failure, never a silent miscount |
| T-19 | **No deflated statistic exists anywhere** | A | 5H | Reuses the existing `deflated` architecture guard, **unamended** |
| T-20 | OOS/lockbox counts match the audit log | P/A | 5H | Seeded discrepancy is reported, never reconciled |
| T-21 | Comparison correctness | P | 5C | Known fixture ⇒ known ranking; NaN bit-exact; `effective_samples` always shown |
| T-22 | **Protected results excluded by default** | A | 5C | OOS/lockbox runs absent without `--include-protected`; marked when included |
| T-23 | **LOCKBOX sweep refused; OOS sweep ceremony-gated** | A | 5E | S-1, S-2 |
| T-24 | Unverified cost semantics never lost | P | 5C | Every Plane-B row carries its flag in text and JSON |
| T-25 | Disk telemetry thresholds | P | 5G | Injected `statvfs` at 69.9 / 70.0 / 89.9 / 90.0 % |
| T-26 | **No GC / retention / deletion exists** | A | 5G | Architecture canary over the whole package |
| T-27 | **No SQL injection surface** | A | 5C | Injection-shaped `--config-path` value; assert parameterised binding |
| T-28 | `experiments` never imports `index` | P | 5B | Existing `ALLOWED` dependency-budget test |
| T-29 | **No nested parallelism** | A | 5F | No `multiprocessing` / `concurrent.futures` symbol outside `pool.py` |
| T-30 | Performance qualification | Q | 5I | §19 |

### 18.1 T-16 — the trial-counting matrix, exactly

| Case | Expected |
|---|---|
| 1 run · 1 fold · 1 outcome · 1 evaluator · 1 metric | **1** Plane-A trial |
| 1 run · **5 folds** · 1 outcome · 1 evaluator · 1 metric | **1** (folds collapse) |
| 1 run · 1 fold · **2 outcomes × 3 evaluators** (1 metric each) | **6** |
| **3 attempts** of one fingerprint, each evaluated | **1** (attempts collapse) |
| 1 `descriptive` run | **0** |
| 1 `pre_evaluation_failure` run | **0** |
| 1 run with **4 executed policies, no Plane-B evaluation** | **0** Plane-A policy effect; **0** Plane-B trials |
| 1 run · Plane B · **4 policies × 2 trade evaluators**, 5 folds | **8** Plane-B trials (folds collapse) |
| **100-child sweep**, each evaluated once | **100** distinct hypotheses; correlation **not** shrunk |

---

## 19. Performance qualification protocol

Executed **once**, on a clean tree, at a recorded commit, following the
Phase-1 / Phase-3 / Phase-4 precedent.

### 19.1 Protocol

1. **Preconditions.** Clean tree; recorded HEAD; default gate green; runs-root
   state, `audit.jsonl` line count and disk usage recorded before and after.
2. **Fixture.** A 100-child sweep over a published **TRAIN** view using the
   existing reference components and the Phase-3 fold configuration. **No OOS.
   No LOCKBOX. No new component.**
3. **Run A — cold sweep.** `sweep run --max-workers 10`, cold cache. Record
   wall time, per-child wall time, **peak RSS per worker** and the **sum of
   concurrent peaks**, observed concurrency over time, final states.
4. **Run B — forced kill.** Plan a **second, distinct 100-child TRAIN sweep**
   (different seeds; never replay Run A's already-complete fingerprints). At
   ~50 % completion `kill -9` the dispatcher **and** two RUNNING children;
   re-invoke. Record children recomputed (must equal the incomplete set),
   children skipped, **zero `COMPLETE` recomputed**, resume wall time and
   unchanged pre-existing content-addressed cache entries.
5. **Run C — index.** Build over ≥ 1,000 clearly marked synthetic run
   directories in a separate temporary runs root, including ≥ 100 evaluated
   runs so metric and Trial-Ledger queries are real. Record full,
   delete-and-rebuild and incremental rebuild time and database size.
6. **Run D — query.** Execute the §13.2 questions with 1 warm-up plus 5 timed
   repetitions each; record median and minimum.
7. **Run E — telemetry.** Record `statvfs` before and after; confirm the 70 %
   path with an injected value and **state plainly** that the real disk
   (22 % used at planning time) does not reach the threshold, rather than
   simulating a pass.

### 19.2 Hard acceptance criteria

| # | Criterion |
|---|---|
| H-1 | 100-child sweep completes; all children terminal; no orphan |
| H-2 | **≥ 10 workers concurrently active** for a sustained majority of the sweep |
| H-3 | **Sum of concurrent peak RSS ≤ 16 GiB** |
| H-4 | After the forced kill, **only missing work is recomputed** — zero `COMPLETE` runs recomputed |
| H-5 | **Median comparison-query latency < 1 s over ≥ 1,000 indexed runs** |
| H-6 | Deleting the index and rebuilding reproduces identical content (T-13) |
| H-7 | Audit log grew only by legitimate gated accesses; no unexpected event |
| H-8 | All five view-manifest digests and the D-6 metadata fingerprint **unchanged** |
| H-9 | All five thread-clamp variables `= 1` in every child |
| H-10 | Trial-ledger output contains **no** deflated/adjusted statistic and **no** p-value |

### 19.3 Informational measurements

Cold vs warm sweep wall time · per-child wall-time distribution · actual vs
assumed peak RSS (calibrates the OD-5D 1 GiB figure) · index full and
incremental rebuild times · database size · scheduler efficiency (idle
worker-seconds) · disk delta · whether `MemoryMax` delegation was available ·
cache hit rate on resume.

### 19.4 Honesty rule

If something cannot be measured on this VM it is recorded as **not measured**
with the exact reason, following the Phase-1 precedent (*"cold honestly
recorded as `not measured on this machine — sudo unavailable`"*). A
qualification that quietly substitutes a proxy is worse than one that reports
a gap.

### 19.5 Executed qualification evidence — **GREEN 2026-08-15**

The unfiltered default gate was green on the exact source and test contents
committed as `b9f74ea748c399be657335aaf38ac9492da57b7d`. The deselected qualification
then passed at that clean commit in **251.48 s**. It used the published TRAIN
identity `XAUUSD_S_M1_TRAIN_V1` at manifest
`30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e`.
"Cold" here means a fresh temporary runs root and empty content-addressed
run cache; the harness did not claim or perform a privileged OS page-cache
drop.

| Evidence | Observed result |
|---|---|
| Run A | 100/100 children; 100 worker PIDs and 100/100 clamp verifications; 56.590 s wall time |
| H-2 scheduling | maximum concurrency 10; concurrency 10 sustained for `0.751773` of non-zero samples |
| H-3 memory | maximum concurrent observed peak RSS 4,674,699,264 bytes; maximum worker 488,378,368 bytes; median worker peak 477,702,144 bytes — below 16 GiB |
| Run B kill point | dispatcher plus two confirmed RUNNING workers killed; 45 complete at the kill point, 52 complete after surviving orphans exited |
| Run B resume | incomplete set 48; dispatched exactly 48; 100/100 complete afterward; zero prior COMPLETE fingerprints relaunched; two pre-existing cache files byte/mtime-identical; 53.465 s |
| Synthetic index | 1,000 runs, 100 evaluated; 974,848-byte database; full rebuild 0.720 s; delete/rebuild 0.719 s; incremental rebuild 0.570 s; delete/rebuild table content identical |
| Query timings | run query median/minimum 0.129381/0.128626 s; comparison median/minimum 0.132875/0.131164 s; Trial Ledger median/minimum 0.149390/0.146333 s; five measured repetitions after one warm-up |
| Trial Ledger | files/index outputs identical; actual two-sweep scope = 200 distinct hypotheses, 200 Plane-A trials, 0 Plane-B trials, zero audit discrepancies; mandatory caveat present |
| Disk | real usage 25.22% before and after cleanup; 42.17% while temporary evidence existed; injected 70.00% warning and 90.00% refusal/explicit override paths passed |
| Temporary state | qualification root was outside the repository and removed after measurement |

H-7/H-8 were byte-level external invariants: the audit log stayed at three
records with SHA-256
`deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
the D-6 metadata fingerprint stayed
`5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`
over 22 entries; and the five view-manifest hashes stayed:

- TRAIN `30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e`;
- VALIDATION `5d122caa6a38c5bcf94ebc20895199d89c30f0f460fabd244865ad8829ebe6fd`;
- OOS `d94a54331a1ef30b0242e685ab8d2f3f0d8d910a64b9c52412682d991bcab3af`;
- LOCKBOX `c9080133719b79016beace397ab569aad07a2b15008bab6c5d36730b14b64dc8`;
- SNAPSHOT `d5b1724cb204699d4e8c3a45bebffa39d080a5260d285610f50981f6ae7eb600`.

H-10 passed both the qualification serialization checks and the unchanged
architecture guard: no adjusted metric, p-value or prohibited deflated
statistic exists in the Trial Ledger. `MemoryMax` was honestly recorded as
**not measured** because the foreground dispatcher was required for per-worker
`/proc` telemetry; no cgroup-enforcement claim is made. Thus H-1…H-10 and
CP-21 were green at this qualification point; CP-20 remained a separate
methodology review/owner gate and is closed in §24.1.

### 19.6 Post-review remediation requalification — **GREEN 2026-08-15**

F-2 changed the Trial Ledger's audit reconciliation and F-3 changed one ledger
diagnostic, so the full H-1…H-10 qualification was repeated rather than
relying on §19.5. The same deselected harness passed on exact clean remediation
commit `ecb9e5d7d89df6363b8b3b0d6a0c1d32471c1ee1`, against the same published
TRAIN identity and with no OOS or LOCKBOX access.

| Evidence | Observed result |
|---|---|
| Run A | 100/100 children; 100 worker PIDs and 100/100 clamp verifications; 56.188 s wall time |
| H-2 scheduling | maximum concurrency 10; concurrency 10 sustained for `0.759607` of non-zero samples |
| H-3 memory | maximum concurrent observed peak RSS 4,339,372,032 bytes; maximum worker 488,427,520 bytes; median worker peak 484,454,400 bytes — below 16 GiB |
| Run B | dispatcher plus two RUNNING workers killed; 50 complete before resume; resume dispatched exactly the 50-fingerprint complement; 100/100 complete; zero prior COMPLETE fingerprints relaunched; two pre-existing cache files byte/mtime-identical; 58.842 s resume |
| Synthetic index | 1,000 runs, 100 evaluated; 974,848-byte database; full rebuild 0.726 s; delete/rebuild 0.712 s; incremental rebuild 0.572 s; delete/rebuild table content identical |
| Query timings | run query median/minimum 0.130613/0.129387 s; comparison median/minimum 0.133914/0.133563 s; Trial Ledger median/minimum 0.152789/0.148891 s; five measured repetitions after one warm-up |
| Trial Ledger | files/index outputs identical; actual two-sweep scope = 200 distinct hypotheses, 200 Plane-A trials, 0 Plane-B trials, zero audit discrepancies; mandatory caveat present |
| External state | audit remained three records at SHA-256 `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`; all five view hashes and the 22-entry D-6 fingerprint were unchanged |
| Disk and temporary state | real usage 25.23% before and after cleanup; 42.20% while temporary evidence existed; the isolated qualification root was removed |

The RSS figures above are **informational observations**, expected to vary
between runs. No exact RSS value was added to the test contract; H-3 remains
the existing 16 GiB upper bound. `MemoryMax` again remained honestly not
measured because foreground `/proc` telemetry is required. H-1…H-10 remain
green, while CP-20 still requires independent review and explicit owner
approval.

### 19.7 F-7 lock remediation requalification — **GREEN 2026-08-16**

The bounded follow-up review confirmed F-1…F-4 resolved and found F-7: an
unbounded run-directory `flock` could wedge scan and resume preflight behind a
suspended live worker. Exclusive and shared run locks now use `LOCK_NB`, retry
every 10 ms and raise `_RunLockTimeoutError` after 5 seconds with the affected
run-directory path. This is a bounded failure, not recovery: run truth and the
files-first state machine are unchanged.

Because this behavior can affect H-1 completion, H-4 recovery and the scans
underlying index/ledger evidence, the full H-1…H-10 harness was repeated on
exact clean remediation commit
`717fc039f8b70c5eecdf1efae1a2d0b00420f356`. It passed against the same
published TRAIN identity with no OOS or LOCKBOX access.

| Evidence | Observed result |
|---|---|
| Run A | 100/100 children; 100 worker PIDs and 100/100 clamp verifications; 59.009 s wall time |
| H-2 scheduling | maximum concurrency 10; concurrency 10 sustained for `0.767024` of non-zero samples |
| H-3 memory | maximum concurrent observed peak RSS 4,633,763,840 bytes; maximum worker 488,431,616 bytes; median worker peak 484,784,128 bytes — below 16 GiB |
| Run B | dispatcher plus two RUNNING workers killed; 52 complete before resume; resume dispatched exactly the 48-fingerprint complement; 100/100 complete; zero prior COMPLETE fingerprints relaunched; two pre-existing cache files byte/mtime-identical; 48.842 s resume |
| Synthetic index | 1,000 runs, 100 evaluated; 974,848-byte database; full rebuild 0.730 s; delete/rebuild 0.719 s; incremental rebuild 0.571 s; delete/rebuild table content identical |
| Query timings | run query median/minimum 0.129715/0.128864 s; comparison median/minimum 0.134632/0.128977 s; Trial Ledger median/minimum 0.146081/0.144664 s; five measured repetitions after one warm-up |
| Trial Ledger | files/index outputs identical; actual two-sweep scope = 200 distinct hypotheses, 200 Plane-A trials, 0 Plane-B trials, zero audit discrepancies; mandatory caveat present |
| External state | audit remained three records at SHA-256 `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`; all five view hashes and the 22-entry D-6 fingerprint were unchanged |
| Disk and temporary state | real usage 25.23% before and after cleanup; 42.15% while temporary evidence existed; the isolated qualification root was removed |

The RSS values remain informational observations, not fixed expectations;
H-3 remains the existing 16 GiB bound. `MemoryMax` again remained honestly
not measured because foreground `/proc` telemetry is required. H-1…H-10 and
CP-21 remain green. The bounded F-7 follow-up review subsequently passed at
reviewed head `22c08a27ef9f052e3c00cf605cc10c2962d1469a`; the owner explicitly
approved CP-20 and Phase-5 closeout on 2026-08-16.

The final unfiltered gate then exposed a second ordering dependency in the
same regression test: while the worker was suspended, the test's pre-kill
wait still called the locking projection scan. Commit
`718b799ed10f0d3e7f0e9c9644b75555fd30d888` removed that scan and observes the
other workers' atomically replaced `state.json` files until two are COMPLETE;
the independent watchdog remains responsible for `SIGCONT` at resume
preflight. This is a test-only correction: `runs.py` is byte-identical to the
qualified `717fc039` production implementation, so the H-1…H-10 evidence
above remains the exact qualification of shipped lock behavior.

---

## 20. Architecture-guard changes — narrowed, never weakened

Exactly two guard families change. Everything else stays verbatim.

1. **`ALLOWED` gains one layer:** `"index": {"core", "experiments"}`.
   `experiments` does **not** gain `index` — the direction is one-way and is
   what enforces INV-IDX-1.
2. **The SQLite bans are narrowed, not removed.** `sqlite3` becomes importable
   **only** inside `trading_research/index/`. The bans in `runs.py`,
   `run_identity.py`, `core`, `data`, `artifacts` and every Slice-3I public
   surface remain exactly as they are, including
   `test_slice_3h_attempts_scan_and_interruption_are_files_only_guards`'s
   assertion that `scan_runs` references none of
   `{Registry, connect, execute, sqlite3}`.

**The `deflated` ban is not amended** (OD-5A). It is reused as T-19.

New guards added by this phase: T-26 (no GC/retention vocabulary), T-28
(layer direction), T-29 (no nested parallelism), T-27 (no raw SQL from
operator input).

---

## 21. Documentation obligations per checkpoint

| CP | Update on green |
|---|---|
| CP-17 | This document (5A/5B status and evidence); PROJECT_STATE.md; ADR-0017 Amendment 1 confirmed as realised |
| CP-18 | This document (5E status); PROJECT_STATE.md |
| CP-19 | This document (5D/5F/5G status); ADR-0016 Amendment 1 status flipped from *planned* to *implemented at `<commit>`*; ADR-0018 Amendment 1 confirmed |
| CP-20 | This document (5H status); ADR-0024 status note; RESEARCH_POLICY §6 confirmed accurate; PROJECT_STATE.md |
| CP-21 | This document (§24 checklist); V1_ROADMAP.md Phase 5 → COMPLETE; PROJECT_STATE.md; architecture V1 §7–§9 confirmed |

---

## 22. Explicit implementation exclusions

Nothing in this list may be built in Phase 5, including as a seam or stub:

queue daemon · remote or distributed workers · dashboards · PostgreSQL ·
generic cluster scheduler · portfolio or multi-symbol infrastructure ·
retention, GC or automatic deletion · plugin-specific experiment
abstractions · **fold-level parallelism** · **nested parallelism of any
kind** · **any deflated, adjusted or multiple-testing-corrected statistic** ·
**any p-value or significance claim** · **any effective-independent-trial
estimate** · ADR-0011 Amendment 2's hypothesis/comparison key · query DSL ·
ORM · generic repository abstraction · schema-migration framework ·
automatic retry of `FAILED` children · any new evaluator, metric, model,
policy, outcome or simulator behaviour · any change to gating, splits, purge,
audit or the fit lifecycle · a second fingerprint implementation · a second
run-state machine · a run→sweep back-reference inside a child run directory.

---

## 23. Exact first implementation action — completed

Slice 5A began only after verifying the documentation/ADR planning commit and
creating the clean `phase5-experiment-ops` implementation branch. The
non-mutating projection landed first; SQLite was then built on that validated
read path.

This sequencing requirement is satisfied and remains recorded here as
historical implementation evidence.

---

## 24. Phase-5 completion checklist

Phase 5 is COMPLETE only when every item is green and independently verified
at a named commit with named evidence.

**Decisions and contracts**
- [x] OD-5A…OD-5F recorded and unchanged by implementation.
- [x] ADR-0024 ACCEPTED and indexed; ADR-0011/0016/0017/0018 amendments
      recorded and accurate.
- [x] No excluded concept exists anywhere (§22), including as a seam.

**Files-first truth**
- [x] Read-only scan provably mutates nothing (T-10b).
- [x] Index lives at `<runs_root>/index/`; nothing extra in
      `<runs_root>/runs/` (T-15b).
- [x] Deleting the index and rebuilding reproduces identical content and
      leaves the runs root byte-identical (T-13).
- [x] `experiments` does not import `index` (T-28).
- [x] Corruption resolves to rebuild-from-files, never a partial write (T-14).

**Sweeps and identity**
- [x] Expansion deterministic; duplicates recorded (T-01…T-03).
- [x] No sweep identifier in any fingerprint (T-04, T-04b).
- [x] Sweep specs live outside the repository (C-8).

**Execution, resource and recovery**
- [x] Whole-run parallelism only; folds sequential; no nested parallelism
      (OD-5C, T-29).
- [x] All five thread clamps enforced fail-closed in every launch path
      (T-06); ADR-0016 Amendment 1 status updated to implemented.
- [x] Concurrency capped by CPU and memory budget (OD-5D, T-08).
- [x] Only missing work recomputed after child and dispatcher kills
      (T-07, T-09, T-09b).
- [x] Determinism independent of worker count (T-05); audit valid under
      parallelism (T-12).
- [x] 70 % disk warning implemented and tested; **no GC anywhere**
      (T-25, T-26).

**Trial Ledger and research integrity**
- [x] §14.1 implemented exactly; T-16 matrix green.
- [x] Files-only ledger equals index-derived ledger (T-17).
- [x] OD-5E invariant canary fails loudly on violation (T-18).
- [x] **No deflated statistic, adjusted metric or p-value anywhere** (T-19).
- [x] Mandatory caveat present and unremovable (§14.3).
- [x] OOS/lockbox counts reconcile with the audit log (T-20).
- [x] S-1…S-7 implemented; LOCKBOX sweeps refused (T-22, T-23, T-24).

**Qualification and records**
- [x] CP-17…CP-21 green with named evidence; CP-20 independent review passed
      and owner approval recorded on 2026-08-16.
- [x] §19 hard criteria H-1…H-10 met; informational measurements recorded
      honestly.
- [x] View-manifest digests, the D-6 fingerprint and the audit log unchanged
      except by legitimate gated access.
- [x] PROJECT_STATE.md, V1_ROADMAP.md, V1.md and RESEARCH_POLICY.md updated.

### 24.1 Closeout record — **OWNER APPROVED 2026-08-16**

The independent Phase-5 review sequence returned REQUEST CHANGES, verified
F-1…F-4 after remediation, identified F-7 in bounded follow-up, and then
passed the bounded F-7 remediation review at exact reviewed head
`22c08a27ef9f052e3c00cf605cc10c2962d1469a`. F-5 remained informational and
F-6 preserved the accepted ops composition root. The owner explicitly
approved CP-20, Phase-5 closeout and merge on 2026-08-16. CP-17…CP-21 are all
GREEN; Slices 5A–5I and Phase 5 are COMPLETE. Architecture V1 and
RESEARCH_POLICY were rechecked and remain accurate without amendment.

---

## 25. Explicit Phase-6+ deferrals

Deferred and **not** built here: per-trial statistic series persistence (the
prerequisite for any future deflation) · an approved null-distribution
methodology · effective-number-of-trials estimation · Deflated Sharpe Ratio,
FDR/Benjamini–Hochberg, Bonferroni/Šidák, White's Reality Check, Hansen's SPA
· ADR-0011 Amendment 2's hypothesis/comparison key · retention and GC ·
remote or distributed execution · dashboards · PostgreSQL · portfolio and
multi-symbol infrastructure · the intent-schema registry (arrives with the
second intent schema) · research plugins themselves, which are Phase 6 and
outside platform scope.

Any of these arriving later is an architecture finding requiring an ADR, not
a quiet patch.
