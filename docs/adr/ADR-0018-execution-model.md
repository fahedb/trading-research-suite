# ADR-0018 — Execution model: detached CLI processes, no queue daemon

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
Long experiments must survive SSH disconnects on a single VM without
premature infrastructure. `/tmp` on this VM is a RAM-backed tmpfs; systemd
user services require linger to outlive login sessions.

## Decision
The platform remains a **modular monolith**; one experiment is one detached
process launched through the CLI. V1 uses `systemd-run --user` (linger
deliberately enabled — verified `Linger=yes` on this VM, 2026-08-06) with
tmux as fallback. **No queue daemon in V1.** Run state: PENDING · RUNNING ·
COMPLETE · FAILED · INTERRUPTED, with heartbeats; a stale-heartbeat RUNNING
run is marked INTERRUPTED by the next CLI contact. Content-addressed stage
caching makes rerunning an interrupted experiment reuse completed work
(same fingerprint, new run_id/attempt). Large intermediates never go to
`/tmp`; TMPDIR points at disk (CLAUDE.local.md). Parallelism is a process
pool within the machine's budget, with BLAS threading clamped in workers.

## Amendment 1 — the Phase-5 process pool: whole runs only (2026-08-15, owner decisions OD-5C and OD-5D)

**The decision above is unchanged.** This amendment makes its last sentence —
*"Parallelism is a process pool within the machine's budget, with BLAS
threading clamped in workers"* — concrete, and closes one ambiguity that the
V1 roadmap had inherited.

**Scope of parallelism — OD-5C, owner-approved 2026-08-15.** Local
process-level parallelism is **whole-run parallelism only**:

- **folds remain sequential inside each run**;
- **no nested parallelism** of any kind;
- **no queue daemon** — restated, unchanged;
- **no remote workers and no distributed execution** — restated, unchanged.

The roadmap previously described *"process-pool fold/sweep execution"*, which
implied fold-level pooling. It is corrected to whole-run parallelism. The
reason is recorded with the decision: folds execute inside the orchestrator
that owns the fit lifecycle, the derived purge and the training-target channel,
so parallelising them would touch the platform's most correctness-sensitive
code to buy single-run latency only — whereas run-level parallelism already
saturates this machine for the sweep sizes V1 targets.

**Default planning values — OD-5D, owner-approved 2026-08-15.** For Phase-5
planning: **8 workers by default**, an assumed **1 GiB per-run admission RSS**,
and a **16 GiB total memory budget**; the Phase-5 qualification explicitly
exercises **10 concurrent workers**.

These are **planning defaults and budgeting constants, not architecture
assumptions about every future model.** The 1 GiB figure is a declared
admission-control constant chosen with headroom over a measured reference
workload; it does not assert that any particular model consumes 1 GiB, and the
qualification reports the actual observed peak against it. The runtime must
still cap effective concurrency against available CPU and the configured memory
budget, and the memory ceiling is honestly labelled as **admission control**
rather than runtime enforcement.

**Thread clamping** for these workers is specified in
[ADR-0016](ADR-0016-determinism-tiers.md) Amendment 1 and was implemented in
Phase 5 with a parent-constructed environment and fail-closed worker
assertion.

**Implementation status (2026-08-15).** The bounded dispatcher launches only
the existing whole-run worker command. Resume performs one reconciling
preflight, adopts fresh RUNNING attempts, reuses exactly one PENDING attempt,
refuses ambiguous multiple-PENDING ownership, skips COMPLETE work, and retries
FAILED only with an explicit flag. Dispatcher state is volatile;
`invocations.jsonl` is an fsync'd operational receipt that resume never reads.
Detached `MemoryMax` is best-effort on the whole dispatcher cgroup and the
actual systemd/tmux enforcement mode is recorded rather than inferred.

**Qualification evidence (2026-08-15).** At clean commit
`b9f74ea748c399be657335aaf38ac9492da57b7d`, a fresh 100-child TRAIN sweep
reached 10 concurrent workers for 75.1773% of active telemetry samples. The
maximum observed worker peak RSS was 488,378,368 bytes and the maximum sum of
concurrent observed peaks was 4,674,699,264 bytes, below the 16 GiB admission
budget. All five clamps were verified in all 100 children. A second distinct
100-child sweep then survived dispatcher plus two-worker SIGKILL: resume
launched exactly the 48 incomplete fingerprints and no COMPLETE fingerprint.
These measurements calibrate but do not turn the 1 GiB admission assumption
into a universal runtime guarantee. `MemoryMax` itself was not measured in
this foreground `/proc`-telemetry run, so no cgroup-enforcement claim follows.

Implementation detail lives in
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §9 and §10.

## Alternatives considered
- Queue daemon now — another stateful service before any need.
- nohup/SSH-tethered runs — fragile, no restart semantics.
- Containers per experiment — isolation without benefit here; slows the
  loop.

## Consequences
SSH-independence with zero new infrastructure. Recovery is idempotent by
cache. The run-directory/manifest design stays agnostic to *who* executes,
so a queue or remote workers can arrive later without redesign.

## When to reconsider
When concurrent sweep demand outgrows ad-hoc process pools, or execution
moves off this machine.

## Related
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) (Amendment 1 — the worker thread
clamps this pool requires) ·
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §9, §10 ·
V1.md §9.
