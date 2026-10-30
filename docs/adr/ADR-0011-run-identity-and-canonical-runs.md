# ADR-0011 — Run identity triple, canonical runs, statistical trial counting

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
A single deterministic hash cannot serve as execution identity: running the
same experiment twice to verify determinism would collide, and retries
would be unrepresentable. Reproducibility also needs more than semantic
versions, and multiple-testing statistics need an honest trial count.

## Decision
Three separated identities: **experiment_fingerprint** (deterministic hash
of all reproducibility inputs: resolved config, dataset manifest hashes,
plugin ids + semvers + params + source hashes, Git commit, dependency lock
hash, outcome/execution spec ids, seed, fingerprint-algorithm version;
machine identity captured but excluded), **run_id** (ULID per execution;
run-directory name), and **attempt_number** (count of executions sharing a
fingerprint). SemVer is a human label; the source hash is the proof.

**Canonical runs require:** clean Git tree at a recorded commit, lockfile
in sync, `full` integrity verification, no lockbox access (unless a
declared, audited lockbox-evaluation run). Dirty-tree runs are permitted
only as **noncanonical** with the diff captured/hashed and the flag visible
in the manifest and index.

**Statistical trial counting (owner-ratified):** every run that reaches a
valid forecast or trade evaluation counts as one statistical research
trial — including bad and disappointing results. Pure infrastructure
failures (syntax error, package/environment failure, disk I/O failure,
process crash before evaluation, contract failure caused by a platform bug)
do **not** increase the multiple-testing denominator, because no evaluation
was produced — but they are permanently recorded as failed executions and
never deleted or hidden.

## Amendment 1 — a run is not automatically one statistical trial (2026-08-06, finding N-2)

Counting *runs* understates the multiple-testing denominator by exactly the
factor the platform's best feature encourages: one run can fan out N
DecisionPolicies × M OutcomeSpecs × H horizons over a single persisted
inference ([ADR-0009](ADR-0009-generic-output-and-intentframe-family.md)).
Dividing by runs would make Phase 5's deflated statistics cosmetic.

**Every run therefore persists an evaluation inventory** enumerating the
combinations actually evaluated, at minimum:

- model / FittedState identity,
- DecisionPolicy (where a decision plane was evaluated),
- OutcomeSpec,
- horizon,
- metric family, where the distinction is material.

Clarifications, unchanged in spirit:
- every **valid evaluated hypothesis or comparison** is recorded — poor,
  disappointing and abandoned results included;
- **infrastructure failures before a valid evaluation** remain permanently
  recorded failed executions and are **not** statistical evaluations;
- a **descriptive run that ends before any evaluation** (e.g. a regime study
  that only stores model output) is a valid recorded execution and is **not**
  a statistical evaluation;
- nothing is ever deleted or hidden.

**The exact multiple-testing denominator — how inventory entries map to
independent trials, and how correlation between fanned-out variants is
treated — is a blocking decision before any deflated statistic ships in
Phase 5.** The inventory is the raw material; the statistics are not
implemented now, and no run-count-based correction may be published in the
meantime.

> **Resolved 2026-08-15 — see Amendment 4 below and
> [ADR-0024](ADR-0024-multiple-testing-trial-ledger.md).** This paragraph is
> preserved as the original decision text. Do not read it alone: the
> denominator question is closed, and the resolution is stronger than the
> interim rule above — **no** multiple-testing-adjusted statistic ships in V1,
> not merely no run-count-based one.

## Amendment 2 — fingerprint commit sensitivity (2026-08-06, finding N-3)

Because the fingerprint includes the Git commit, two materially identical
experiments separated only by a documentation-only commit receive **different
fingerprints**. This is the safe direction — over-segmentation, never
collision — but it fragments attempt counting and determinism grouping.

Recorded, not fixed: a future **hypothesis/comparison key** (resolved config
+ plugin source hashes + dataset identities, *without* the commit) may group
such runs for comparison and trial accounting. It would be an additional
grouping key and **must not weaken the reproducibility fingerprint**, which
keeps the commit.

## Amendment 3 — concurrency-safe attempt assignment (2026-08-06, finding N-5)

`attempt_number` is derived from the count of executions sharing a
fingerprint, which is a read-modify-write across concurrent detached
processes. Implementation guidance (Phase 3, no decision change): assign the
attempt under a lock at run-registration time, or derive it at query time
from the run directories rather than storing a racing counter. Two
concurrent launches of one fingerprint must never receive the same attempt
number, and the run_id (ULID) remains the only collision-free execution
identity.

## Amendment 4 — the Phase-5 denominator decision is resolved (2026-08-15, owner decision OD-5A)

**Amendment 1's blocking decision is now closed.** It is resolved by
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md), accepted 2026-08-15
during Phase-5 planning. Nothing in Amendments 1–3 is withdrawn; this
amendment records the resolution and nothing else.

- **Raw run count remains an invalid denominator**, exactly as Amendment 1
  states. Nothing here relaxes that.
- **The evaluation inventory remains the source** for trial counting.
- **ADR-0024 defines the approved counting rule:** one trial is one distinct
  `(hypothesis, question)` pair that produced at least one evaluation, with
  `hypothesis = experiment_fingerprint`, the *question* composed from
  evaluation-inventory dimensions, Plane A and Plane B counted separately,
  folds collapsed, attempts sharing a fingerprint collapsed, descriptive runs
  and pre-evaluation failures contributing zero, executed-but-unevaluated
  policies contributing zero, and policy fan-out affecting Plane B only where
  a Plane-B evaluation actually occurred.
- **On correlation** — the second half of Amendment 1's open question —
  ADR-0024 records that the platform does not currently record enough
  information to estimate a defensible effective number of independent
  trials, counts related variants individually as a conservative mechanical
  denominator, and does **not** claim those trials are independent.
- **No adjusted or deflated statistic ships in V1 under this resolution.**
  Amendment 1's *"no run-count-based correction may be published in the
  meantime"* is superseded by a stronger and permanent V1 rule: no
  multiple-testing-adjusted statistic of any kind ships, and the production
  guard forbidding the term `deflated` stays in force. What ships is the
  denominator itself, with a mandatory caveat.

Amendment 2's fingerprint over-segmentation is deliberately left uncorrected:
it inflates the denominator, which is the conservative direction, and the
hypothesis/comparison key it contemplates is explicitly **not** built in
Phase 5.

## Amendment 5 — owner-authorized pre-production system-test purge (2026-08-27)

The owner declared the current installation to be in system testing and
explicitly authorized deletion of the superseded 1,200-child TRAIN sweep
`uls-push-v7-train-joint-model-300x4-tp-v1`. This is a bounded pre-production
exception to the permanent-retention rule above, not a general deletion
facility and not a production research policy.

Before deletion, the operation must prove that the sweep contains 1,200 unique
fingerprints, that exactly 1,200 COMPLETE run directories match them, and that
none overlaps the replacement sweep. A compact purge receipt and the immutable
sweep specification remain outside the run collection. The receipt records the
target count, source commit, reason and target-set digest without retaining the
large run artifacts.

The consequence is explicit: the deleted evaluations disappear from the live
file-derived Trial Ledger and cannot be reconstructed without rerunning them.
Historical documents may report that they existed only when they also state
that the underlying runs were purged during system testing. No result may claim
that the replacement sweep was the first search over these strategy settings.

This amendment authorizes only the named sweep. Any later purge requires a new
owner-authorized record with an exact target and the same pre-deletion checks.

## Alternatives considered
- run_id = content hash — cannot represent retries or determinism checks.
- Counting every execution as a trial — inflates the denominator with
  infrastructure noise and punishes honest failure recording.
- Counting only "interesting" results — the classic self-deception this
  ADR exists to prevent.

## Consequences
Determinism verification is a first-class operation (same fingerprint, two
run_ids, compare artifact hashes — recomputed, never cache-served, per
[ADR-0007](ADR-0007-materialization-policy.md)). The registry's evaluation
inventory — not the run count — is the raw material for the Phase-5 Trial
Ledger denominator defined by Amendment 4 and ADR-0024; no adjusted or
multiple-testing statistic ships in V1. Run states: PENDING →
RUNNING → COMPLETE | FAILED; RUNNING → INTERRUPTED (graceful self-mark or
observer-marked on stale heartbeat); attempts append-only; COMPLETE is
immutable.

## When to reconsider
If runs ever execute on multiple machines, revisit whether machine identity
should enter a separate comparability key (not the fingerprint).

## Related
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0017](ADR-0017-storage-files-as-truth.md) ·
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md) (**resolves Amendment
1's blocking decision**; the approved counting rule and the V1 rule that no
adjusted statistic ships) ·
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) · V1.md §7.
