# ADR-0016 — Determinism tiers and double-run verification

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
Claiming bitwise reproducibility for everything is unprovable across
hardware and would constrain library choices; claiming nothing makes
regression detection impossible.

## Decision
**Exact (bitwise) reproducibility is required for:** dataset identity,
ordering, native time keys, schemas, configs, integer operations, split
membership, and decision alignment. **For floating-point algorithms:**
dependencies pinned (uv.lock), seeds pinned through config, BLAS threading
clamped where required (workers run OMP_NUM_THREADS=1 etc.), tolerances
documented per artifact type, and **no cross-hardware bitwise equality is
claimed unless proven**. The platform supports running the same
experiment_fingerprint under two different run_ids and comparing artifact
content hashes (exact tier) or toleranced comparisons (float tier); the
verification report is a stored artifact.

**Verification never reads the cache** (cross-reference, added 2026-08-06,
finding N-1): a verification run recomputes every stage independently.
Comparing a cache-served artifact with itself would certify staleness as
reproducibility — the normative rule and the platform-code identity that
backs it live in [ADR-0007](ADR-0007-materialization-policy.md).

## Amendment 1 — the Phase-5 thread-clamp obligation (2026-08-15)

**Status of this amendment: IMPLEMENTED AND QUALIFIED ON
`phase5-experiment-ops`.** No decision above changes; this records the
compliance gap found during planning and the implementation that closes it.

**The discovery.** Phase-5 planning inspected the repository for the clamp this
ADR requires — *"BLAS threading clamped where required (workers run
`OMP_NUM_THREADS=1` etc.)"* — and found **no clamp anywhere** in `src/`,
`tests/` or `scripts/`. The only match for the clamp variable names in the
whole repository is the requirement sentence in this ADR.

Two consequences, both real:

1. Determinism results to date hold because the numeric libraries happened to
   behave, **not because the platform constrains them**. Under Phase-5's
   process pool that stops being adequate: several unclamped workers
   oversubscribe the machine and can make float reductions order-dependent.
2. The two detached launchers do not produce the same environment.
   `_launch_detached()` passes no `--setenv`, and `systemd-run --user` does not
   inherit the invoking shell's environment while the tmux fallback does — so
   the same run can execute under two different environments depending on
   which launcher accepted it.

Recorded honestly alongside them: the missing `TMPDIR` in the `systemd-run`
path is **currently harmless**, because the only `tempfile` user writes with an
explicit same-directory `dir=` and never consults `TMPDIR`. The environment gap
is nevertheless real and must be closed explicitly.

**The approved Phase-5 implementation requirement** (Slice 5D,
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §10.3):

- every worker — pooled, detached or foreground — runs with the explicit
  single-thread clamp environment `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`,
  `MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS`, each
  set to `1`;
- the environment is **constructed by the parent**, because these variables are
  read when the numeric library loads and cannot be fixed from inside the
  worker;
- **all launch paths must produce equivalent controlled environments** — one
  construction function, delivered as an explicit `env=` mapping for direct
  child processes and as `--setenv` arguments for `systemd-run`;
- the worker **fails closed**: if any required clamp is absent or not `1`, it
  refuses to start and terminates as a permanently recorded failure rather than
  producing a result under uncontrolled threading.

Slice 5D now constructs one explicit child environment, adds all five
`--setenv` values to `systemd-run`, uses `env=` on direct/tmux launch paths,
and asserts the values fail-closed at worker startup. Unit and integration
tests cover malformed environments and every launcher. The named clean-commit
qualification at `b9f74ea748c399be657335aaf38ac9492da57b7d` verified all
five values as `1` in all 100 children while sustaining 10 concurrent workers;
CP-19 and CP-21 are GREEN. CP-20's separate methodology review and bounded
remediation follow-ups passed at `22c08a27ef9f052e3c00cf605cc10c2962d1469a`;
the owner approved CP-20 and Phase-5 closeout on 2026-08-16.

## Alternatives considered
- Bitwise-everywhere — unprovable, forces single-threaded numeric paths
  everywhere, punishes legitimate library upgrades.
- Tolerances-everywhere — lets structural regressions (ordering, split
  membership) hide inside "noise".

## Consequences
Determinism is testable on this machine today and honest about its limits.
Golden tests (small synthetic dataset, full pipeline, twice) run in CI-fast
time. Tolerance defaults per artifact type are an open decision blocking
Phase 3.

## When to reconsider
If runs move across heterogeneous hardware, or if a dependency upgrade
shifts float results beyond documented tolerances (forces re-baselining).

## Related
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) ·
[ADR-0007](ADR-0007-materialization-policy.md) ·
[ADR-0018](ADR-0018-execution-model.md) (Amendment 1 — the process pool the
clamps must cover) ·
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §10.3 (Slice 5D,
the clamp implementation obligation) · V1.md §7.
