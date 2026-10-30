# trading-research — Claude session rules

Strategy-agnostic quantitative research platform (modular monolith, Python
3.13 via uv). Architecture: docs/architecture/V1.md · Decisions: docs/adr/ ·
State & next step: PROJECT_STATE.md. Read them before proposing structural
changes; do not restate them here. **The Git repository is the source of
truth — not chat memory.**

## Non-negotiable rules

### Data
- Datasets under the dataset root (see CLAUDE.local.md) are IMMUTABLE.
  Never write, rename, or delete anything there. New data = new dataset ID.
  Generated view manifests live in a separate platform-managed root (see
  CLAUDE.local.md), never under the dataset root.
- Load market data ONLY through the platform data layer. Never glob Parquet
  directories or open partition files directly.
- Integrity: hash-verification failures are hard stops. Never bypass, never
  downgrade to a warning.
- Canonical dataset/view identity is `(dataset_id, manifest_sha256)`.
  `DatasetRef.role` is a validated field, not a third identity component —
  it must agree with the role the referenced manifest declares; a mismatch
  is a contract error. A filesystem path is resolver configuration, never
  identity — don't substitute "the path I loaded it from" for a
  `DatasetRef` check (ADR-0004).
- Public core contracts expose read-only NumPy arrays for columnar data.
  PyArrow (or pandas/Polars/DuckDB) types never appear in a public
  signature — PyArrow is an internal implementation detail only, and a
  read-only array needs no wrapper to hide it (ADR-0003).

### Time
- The platform time axis is NativeTimeKey (int64 + unit + clock_domain).
- The dataset clock domain is broker wall-clock, NOT UTC. Never attach UTC
  tzinfo, never label derived time as UTC, never "correct" offsets.
- Cross-clock-domain comparison/joins are forbidden (no resolver exists).
- Calendar/time-of-day logic must declare CalendarTimePolicy
  `native-wallclock-v1`. External time-based data feeds are blocked in V1.

### Splits, OOS, lockbox ([ADR-0012](docs/adr/ADR-0012-two-tier-splits.md), [ADR-0013](docs/adr/ADR-0013-lockbox-enforcement.md), [ADR-0014](docs/adr/ADR-0014-snapshot-and-view-roles.md))
- Split dates are policy; ADR-0012 is their only normative source.
- Default research data is TRAIN + VALIDATION only.
- OOS access must be explicit and is audited (write-ahead event) from
  Phase 1 onward. Do not evaluate on OOS casually.
- Off-limits by default — LOCKBOX, SNAPSHOT, **and the RAW and CURATED
  parent datasets** (they contain lockbox rows; anything whose range passes
  the OOS boundary is gated, read from a versioned split-policy record —
  never hardcoded; every admitted identity must resolve a concrete observed
  range, and a missing or unresolved range is malformed and refused rather
  than default-gated). Do not read,
  summarize, or evaluate their market rows, values, outcomes, charts, or
  statistics — including via ad-hoc scripts — unless the user explicitly
  runs the unlock ceremony in this session. Administrative manifest metadata
  (identity, role, native range, row count, symbol/timeframe, partitions,
  lineage identities, split-policy reference, and diagnostic classification)
  may be inspected without ceremony, but that inspection must never open
  Parquet or decode or derive market values. Never quote lockbox-period data
  in analysis or fixtures.
- Procedural folds (walk-forward etc.) must lie inside ONE integrity view.
  Never hand-slice date ranges across view boundaries. A fit spanning views
  uses the recorded adjacent-view union, never hand-written dates.
- Fold fit/eval boundaries carry a platform-computed purge derived from the
  declared information span. Never disable or shrink it without an explicit
  recorded reason.

### Research integrity
- ResearchModel outputs carry no trading vocabulary (no buy/sell/direction
  keys). Decisions happen only in DecisionPolicy.
- Model output and intent frames may be **sparse** (a strict subset of
  evaluation keys). Absence of a row ≠ a dense `NaN` row; preserve the
  distinction ([ADR-0009](docs/adr/ADR-0009-generic-output-and-intentframe-family.md)).
- Nothing except OutcomeSpec may read forward of decision time. Treat any
  suspected lookahead as a release blocker, not a cleanup item.
- **Fit-time targets are legal only through the platform channel**: config
  names the OutcomeSpec, the orchestrator builds the TrainingTargetFrame,
  `fit` receives it. Never compute forward values inside a model, feature,
  representation or policy — including "just for training"
  ([ADR-0010](docs/adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)).
- Evaluations, not runs, are the statistical unit: one run records an
  inventory of (model, policy, OutcomeSpec, horizon) it evaluated. Bad
  results count. Infrastructure failures before evaluation, and descriptive
  runs that never evaluate, are recorded but are not statistical
  evaluations. Never delete or hide runs
  ([ADR-0011](docs/adr/ADR-0011-run-identity-and-canonical-runs.md)).
- **No deflated statistic exists in V1** — no adjusted metric, p-value,
  significance claim or effective-independent-trial estimate, by owner
  decision OD-5A ([ADR-0024](docs/adr/ADR-0024-multiple-testing-trial-ledger.md)).
  The platform reports the *denominator*: one trial is one distinct
  `(experiment_fingerprint, question)` pair that produced an evaluation, with
  Plane A and Plane B separate, folds and attempts collapsed. Never invent a
  null distribution to manufacture one. The architecture guard forbidding the
  term `deflated` in production code is load-bearing — do not weaken it.
- **A sweep over LOCKBOX is prohibited**, not merely gated: a sweep searches
  over parameters that a lockbox run requires to be frozen. OOS sweeps need the
  same audited ceremony as a single OOS run, per child.

### Reproducibility
- Official (canonical) runs require a clean Git tree at a recorded commit.
  Dirty-tree runs are noncanonical and must be labeled as such.
- Never edit a COMPLETE run directory or manifest. Supersede, don't mutate.
- A sweep child is an **ordinary run**: same fingerprint rule, same run
  directory, same state machine. No sweep id, ordinal or parent reference may
  enter `experiment_fingerprint` or a child's run directory.
- **SQLite is disposable and never authoritative.** Files are the truth; the
  index lives at `<runs_root>/index/` (never inside `<runs_root>/runs/`, which
  refuses non-run entries, and never in the repo tree). It must be rebuildable
  from run directories through a **non-mutating** read path, and it must never
  influence identity, canonicality, gating, audit or any evaluation result
  ([ADR-0017](docs/adr/ADR-0017-storage-files-as-truth.md) Amendment 1).
- No pickle as a durable artifact format.
- Random behavior must be seeded through the config, never ad hoc.

### Machine
- Never place large intermediates in /tmp (RAM-backed tmpfs on this VM).
  Use the TMPDIR from CLAUDE.local.md.
- Long-running experiments run detached (systemd-run --user; linger is
  enabled — or tmux), never tethered to an SSH session.
- Scale-out is a bounded local process pool over **whole runs only**; folds
  stay sequential inside a run and there is no nested parallelism, queue
  daemon or remote worker ([ADR-0018](docs/adr/ADR-0018-execution-model.md)
  Amendment 1).
- In parallel workers, BLAS threading must stay clamped (OMP_NUM_THREADS=1
  etc.) — do not undo it. Phase-5 Slice 5D implemented the accepted ADR-0016
  Amendment-1 obligation with five fail-closed thread clamps applied to every
  worker.

## Required verification workflow

Before claiming any change works:

    ./scripts/gate.sh

(equivalently: `uv run pytest -q` && `uv run ruff check .` &&
`uv run mypy src tests`, from the repo root). A change that breaks
tests/test_architecture.py is an architecture change: stop and raise an ADR
instead of adjusting the test.

## Change control
- Architectural changes (new stage, new layer dependency, contract change,
  split-policy change) require a new or amended ADR in docs/adr/ BEFORE
  implementation.
- Architecture tests guard against architectural drift, not against their
  own malicious author. Intentionally widening or weakening an allowlist or
  architecture test is an architecture change subject to the same review
  discipline as changing an accepted ADR boundary.
- Phase order comes from docs/roadmap/V1_ROADMAP.md. Do not start a phase
  whose blockers are unresolved.

## Repo map (pointers)
- src/trading_research/core/ — contracts, time, frames, identity
- src/trading_research/data/ — dataset registry, views, integrity
- feature/representation/model/decision/outcome plugins — one idea =
  plugin(s) + config + tests; infra changes need an ADR
- src/trading_research/experiments/ — orchestrator, CLI, run registry
- tests/ — architecture tests are load-bearing; extend leakage rigs when
  adding any provider/model

## Context (explanations, not rules)
- First planned research plugin is similarity-based; the platform is
  deliberately not shaped around it.
- Cost/execution semantics are still unaudited. Since ADR-0020 Amendment 1
  (2026-08-14) the gate is on **claims, not construction**: the simulator may
  be built and fixture-verified, every closure-required cost fact is tracked as
  VERIFIED-with-evidence or UNKNOWN-with-evidence-required in
  `docs/research/COST_AUDIT.md`, and a run declaring
  `cost_semantics: unverified` is permanently marked — its money figures must
  never be quoted as real-money estimates. Never invent a cost semantic, and
  never accept a claim that one was verified: `verified` resolves only against
  the repository's `VERIFIED_COST_BINDING` constant, which is `None` until the
  audit closes, so it is a hard refusal, not a documentation error.
- **Which side of the market the recorded OHLC represents is UNKNOWN**
  (COST_AUDIT A6), so the *executable* price path is deliberately unbound:
  V1 declares a named provisional price model and no document may state that
  the recorded path is the executable path (ADR-0023 §8). A simulated result
  under an open audit is unverified in execution as well as in money.
- The simulator is second, never first: Plane A establishes edge, Plane B only
  asks whether it survives execution and costs. TP/SL or cost tuning is not a
  substitute for predictive edge (ADR-0023, RESEARCH_POLICY.md §7).
- Architecture V1 and ADR-0001…0020 are **ACCEPTED** (ratified 2026-08-06
  after independent review, record in
  `docs/architecture/reviews/2026-08-06-independent-review.md`); ADR-0021
  (2026-08-12), ADR-0022 (2026-08-13), ADR-0023 (2026-08-14) and ADR-0024
  (2026-08-15) are also ACCEPTED. Changing a ratified decision needs a new or
  amended ADR.
- Phase 5 is COMPLETE (2026-08-16): Slices 5A–5I and CP-17…CP-21 are green;
  CP-20, closeout and merge were owner-approved after the bounded independent
  review chain passed. Read `docs/phases/PHASE5_EXPERIMENT_OPS.md` before
  touching experiment-ops behavior or `experiments/runs.py`'s scan path.
- Stage cache identity includes platform-code identity, so a platform commit
  invalidates caches; determinism verification recomputes and never reads
  cache (ADR-0007).
- Phase 1's detailed, owner-approved implementation plan (contracts, slice
  order, tests, review checkpoints) lives in
  `docs/phases/PHASE1_DATA_LAYER.md` — read it before touching `core/time`,
  `core/identity`, `core/frames`, or `data/`.
- Machine-specific paths live in CLAUDE.local.md (git-ignored).
