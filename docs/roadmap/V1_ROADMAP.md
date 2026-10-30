# V1 Roadmap — Operational Construction Plan

Date: 2026-08-07 · Architecture reference: [docs/architecture/V1.md](../architecture/V1.md)

Statuses: NOT_STARTED · IN_PROGRESS · COMPLETE · BLOCKED

> **The repository has exactly one current next action, and it is governed by
> [PROJECT_STATE.md](../../PROJECT_STATE.md).** This roadmap records phase scope
> and status. Sections below that narrate completed phases preserve their
> contemporary "next action" wording as **historical record**; those statements
> are superseded and must not be read as current instructions.

---

## Phase 0 — Governance & baseline

### Phase 0A — Repository baseline + documentation freeze — **COMPLETE**

- **Objective:** the repository, not chat memory, becomes the source of truth.
- **Components:** Git init + baseline commit of the working skeleton;
  docs/architecture/V1.md; freeze-proposal review record; ADR-0001…0020;
  V1 roadmap; research policy; PROJECT_STATE.md; CLAUDE.md +
  CLAUDE.local.md(.example); scripts/gate.sh; runs root + TMPDIR dirs;
  linger enabled.
- **Dependencies:** ratified owner decisions of 2026-08-06 (AO-1, AO-2,
  split dates, OOS non-default, runs root, lockbox ceremony, MarketFrame
  minimality, trial counting, files-as-truth, keep-and-refactor).
- **Acceptance:** gates green before and after; source/tests/datasets
  untouched; CLAUDE.local.md ignored; ADR index complete; docs links
  resolve.
- **Excluded deliberately:** any source refactor, dataset view manifests,
  Data Layer work, agent filesystem hooks.
- **Exit criteria:** baseline + documentation commits on `main`. ✅

### Phase 0B — Independent review & ratification — **COMPLETE**

- **Objective:** an independent architecture-review session attacks the
  proposal using docs/architecture/V1.md, the freeze-proposal record, and
  the review checklist within it; owner ratifies.
- **Components:** review findings document under docs/architecture/reviews/;
  ADR status flips PROPOSED → ACCEPTED (or amendments).
- **Dependencies:** Phase 0A. **Blocking decisions:** none — this phase IS
  the decision gate.
- **Acceptance / exit:** every ADR either ACCEPTED or amended with a
  recorded reason; V1.md status updated; PROJECT_STATE.md updated. ✅
- **Outcome (2026-08-06):** independent read-only review returned **accept
  with required changes** —
  [record](../architecture/reviews/2026-08-06-independent-review.md),
  preserved verbatim. Four blocking findings (fit-time target channel;
  fold-boundary purge; raw/curated lockbox bypass; view-composition
  semantics) and eight non-blocking findings were resolved by amendment to
  ADR-0007, 0008, 0009, 0010, 0011, 0012, 0014, with implementation guidance
  recorded in ADR-0005, 0016, 0017. All 20 ADRs and Architecture V1 are
  **ACCEPTED**. No source, test or dataset change was made.
- **Excluded:** implementation of any kind.

**Phase 0 is COMPLETE.**

---

## Phase 1 — Data Layer & time model — **COMPLETE (2026-08-10, CP-5 owner-approved)** · **historical detail:** IN_PROGRESS · Slice 1E COMPLETE, reviewed APPROVE, owner-approved and merged · Slice 1F planning/reconciliation COMPLETE (OD-F1…OD-F6 approved 2026-08-09) · Slice 1F Stage A COMPLETE, reviewed APPROVE WITH NON-BLOCKING NOTES, owner-approved 2026-08-09 and merged · Stage-B production preparation COMPLETE (D-1…D-6, R-1…R-3 owner-approved 2026-08-09) · Stage-B operational script COMPLETE, reviewed APPROVE WITH NON-BLOCKING NOTES, owner-approved and merged · **Stage-B production generation COMPLETE (2026-08-09)** · **CP-4 APPROVED 2026-08-10 · SLICE 1F COMPLETE** · **Slice 1G COMPLETE and owner-approved 2026-08-10** · **Slice 1H-A COMPLETE and owner-approved 2026-08-10; OD-H2 approved; Stage 1H-B qualification COMPLETE 2026-08-10 with all measured budgets passed, cold unmeasured (sudo unavailable) and audit 2→3; CP-5 COMPLETE and OWNER APPROVED 2026-08-10**

- **Objective:** honest, fast, immutable data access on the native time
  model.
- **Detailed plan (normative):**
  [docs/phases/PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) —
  slices 1A–1H, minimal contracts, owner-approved decisions, test matrix,
  performance plan, review checkpoints, completion checklist. This roadmap
  entry is status-only; do not duplicate detail here — update the plan
  document instead.
- **Dependencies:** Phase 0B ratification ✅ (2026-08-06); Phase 1 planning
  ✅ (2026-08-07, owner-approved).
- **Current checkpoint:** **CP-3 independent bounded review COMPLETE;
  APPROVED by owner on 2026-08-08. Slice 1D is COMPLETE and merged into
  `main`. Slice 1E implementation and its focused independent review are
  COMPLETE; verdict APPROVE; owner approval is COMPLETE; reviewed head
  `84bee8187a72aa361f3b9b986bf84b4d00a63818` is merged into `main`; Slice
  1F Stage A is COMPLETE, independently reviewed APPROVE WITH NON-BLOCKING
  NOTES, owner-approved on 2026-08-09 and merged into `main` at reviewed head
  `79bac876f84bcc915532b9bad246e74200e67041`; Stage-B production preparation
  is COMPLETE with owner decisions D-1…D-6 and refinements R-1…R-3 approved on
  2026-08-09; the Stage-B operational script is COMPLETE, independently
  reviewed APPROVE WITH NON-BLOCKING NOTES, owner-approved and merged into
  `main` at `f8b6168511fa2544479c0ad96a16c50cf0ab7890`; and Stage-B production
  generation is COMPLETE (2026-08-09, fresh mode, no OD-F6 recovery); the
  independent CP-4 review is COMPLETE with verdict APPROVE WITH NON-BLOCKING
  NOTES and **CP-4 was OWNER APPROVED on 2026-08-10, closing Slice 1F**.** CP-3
  N-1 is resolved as
  Option B: every V1 normalized manifest requires a concrete observed
  `admitted_native_range`; future relaxation requires explicit review.
  **No new formal checkpoint followed Slice 1E — CP-4 remains after Slice
  1F**; the focused bounded independent read-only Slice 1E review is complete
  (detail in the plan document).
- **Slice 1G: COMPLETE and OWNER APPROVED (2026-08-10).** Planning approved
  decision OD-G2 — `boundary_class` is deferred from V1 because no current
  component can honestly supply it; see
  [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md). Implementation was
  independently reviewed with final verdict **APPROVE**; findings N-1 and N-2
  were remediated and confirmed **RESOLVED**; the reviewed head
  `fa69733367ee4c340894ff9a0d5d8b6b99f92a6e` was fast-forwarded into `main`.
- **Slice 1H-A: COMPLETE and OWNER APPROVED (2026-08-10)** at reviewed head
  `64be3756bc7d4fdff2c5465774c3b5fc26998c71`, under **OD-H1 OWNER APPROVED
  2026-08-10**. The obsolete provider-driven path is retired with no
  replacement shim, and a test-only TRAIN performance harness exists.
  Independent review verdict **APPROVE WITH NON-BLOCKING NOTES** (five notes,
  none scheduling remediation).
- **Stage 1H-B qualification: COMPLETE (2026-08-10)** under OD-H2, executed
  exactly once at `ad1e507a3577b377317d4c68d029454479d43d7a`. SNAPSHOT peak RSS
  592,191,488 B and warm median 0.265562650 s both pass; TRAIN warm median
  0.142175 s and RSS 464,871,424 B pass; selective-loading evidence 28,290,760 B
  vs 84,872,280 B retained payload. Cold recorded honestly as `not measured on
  this machine — sudo unavailable`. Audit moved exactly **2 → 3**. OD-H4
  resolved and §11 verified item by item.
- **Checkpoint CP-5: COMPLETE (2026-08-10) — verdict APPROVE WITH NON-BLOCKING
  NOTES; OWNER APPROVED 2026-08-10.** No blocker. **Slice 1H is COMPLETE and
  Phase 1 is COMPLETE.**
- **§13 post-Phase-1 operational checklist: COMPLETE (2026-08-12).** Item 1
  (Production Checkpoint) and item 2 (private Git remote) satisfied and
  accepted; **item 3 (Hyper-V VM export) completed 2026-08-12 on the owner's
  explicit attestation — host-side location as provided by the owner: under
  the virtual machine's host files, in a folder named `export`; OWNER
  ATTESTED, not independently verified from the guest VM**; item 4 (owner
  review of items 1–3) COMPLETE with all three accepted. **The §13 Phase-2
  implementation gate is CLEARED.** Detail lives in
  [PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) (§13).
- **Next action at the time (2026-08-12) — HISTORICAL, SUPERSEDED:** *"begin
  Phase-3 planning only; resolve its three listed owner decisions."* Phase-3
  planning is now COMPLETE (2026-08-13) and all three decisions are resolved
  (OD-3A, OD-3B, OD-3C). The current next action is in
  [PROJECT_STATE.md](../../PROJECT_STATE.md).
- **Acceptance criteria (headline; full list in the plan doc):** full usable
  frame loads + verifies < 2 s warm / < 5 s cold full-hash; loaded-frame
  RSS < 1.0 GiB; default discovery returns TRAIN + VALIDATION only;
  RAW is never a public frame target; CURATED/SNAPSHOT/LOCKBOX are gated with
  no data returned before audit success; OOS explicit and audited from day
  one; view context semantics
  (`context_start`/`eval_start`/`eval_end`) and adjacent-view unions
  enforced; snapshot binding keys-only and audited.
- **Excluded deliberately:** features, caching, orchestrator, CLI,
  TrainingTargetFrame, OutcomeSpec, fold/purge algorithm (contract only).
- **Expected artifacts:** view manifests (hash-pinned) under
  `/path/to/research-views/`, Phase-1 completion note in
  PROJECT_STATE.md.
- **Exit criteria:** [PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md)
  §11 completion checklist green + docs updated + commit on `main` +
  independent review.

## Phase 2 — Features & calculations — **COMPLETE (2026-08-12) · Slices 2A–2H COMPLETE · CP-6/CP-7/CP-8 GREEN · independent whole-phase verdict APPROVE**

- **Detailed plan (normative):**
  [docs/phases/PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md) — slices 2A–2H,
  minimal contracts, owner-approved decisions, test matrix, performance plan,
  checkpoints and completion checklist. This roadmap entry is status-only; do
  not duplicate detail here — update the plan document instead.
- **Contract:** [ADR-0021](../adr/ADR-0021-columnar-feature-contract.md)
  (**ACCEPTED 2026-08-12**) records the columnar feature contract replacing the
  per-point `FeatureProvider.compute(MarketContext) -> FeatureSet`, satisfying
  OD-1's precondition for Slice 2A.
- **Completed prerequisite:** the §13 post-Phase-1 operational checklist is **COMPLETE
  (2026-08-12)** — item 3 (Hyper-V VM export) completed on owner attestation
  and item 4 (owner review of items 1–3) COMPLETE with all three accepted — so
  the §13 gate is **CLEARED** and implementation is permitted, subject to this
  plan and ADR-0021.
- **Slice 2A:** COMPLETE; 833 tests collected; full gate, Ruff and strict mypy
  green; independent review verdict **APPROVE / no blockers**. Contracts only:
  no provider, cache, artifact or data access was added.
- **Slice 2B:** COMPLETE; 923 tests collected (920 active, 3 performance tests
  deselected by default); full gate, Ruff and strict mypy green; independent
  review verdict **APPROVE / no blockers**. Deterministic execution,
  platform-owned masks, one isolated request per provider, the exact
  `BoundedView` adapter and three registered reference providers are present;
  no cache, artifact or data access was added.
- **Slice 2C / CP-6:** COMPLETE / GREEN; 958 tests collected (955 active, 3
  performance tests deselected by default); full gate, Ruff and strict mypy
  green; independent contract, semantic and test reviews verdict **APPROVE /
  no blockers**. Exact future, declared-reach, `BREAK` and multi-segment seam
  rigs cover the live registry and catch all six test-only adversarial
  controls. This slice changed tests and status documentation only; production,
  dependencies, cache, artifacts, data and audit state are unchanged.
- **Slice 2D:** COMPLETE; 1004 tests collected (1001 active, 3 performance
  tests deselected by default); full gate, Ruff and strict mypy green;
  independent contract, semantic and test reviews verdict **APPROVE / no
  blockers**. Pure caller-supplied platform identity and deterministic stage
  identity cover exactly the ordered sources, resolved/eval bounds and axis,
  split-policy triple and full ordered provider declarations. `artifacts`
  imports only `core`; no Git lookup, I/O, cache entry or durable artifact was
  added.
- **Slice 2E:** COMPLETE; 1105 tests collected (1102 active, 3 performance
  tests deselected by default); full gate, Ruff and strict mypy green;
  independent architecture, test and filesystem-security reviews verdict
  **APPROVE / no blockers**. Canonical uncompressed NPZ bytes bind the complete
  stage preimage to exact feature metadata, and the immutable store verifies
  whole-file hashes and embedded identities on every read. Descriptor-relative
  no-follow traversal, per-key interprocess locking, complete-temp fsync,
  atomic no-replace publication and directory fsync are fault- and
  subprocess-race-tested. `data/atomic_write.py`, dependencies and the data,
  feature and experiment layers are unchanged; no persistent production cache
  entry was created.
- **Slice 2F / CP-7:** COMPLETE / GREEN; 1152 tests collected (1149 active, 3
  performance tests deselected by default); full gate, Ruff and strict mypy
  green; independent contract, runtime and security reviews verdict **APPROVE /
  no blockers**. The exact four-policy materialization path performs verified
  hit/miss composition without clock-based cost decisions or failure writes;
  post-lookup provider revalidation prevents identity/execution TOCTOU, and
  cache hits restore only exact live-bound frames. No production cache entry,
  data-layer caller or dependency was added.
- **Slice 2G:** COMPLETE; 1182 tests collected (1179 active, 3 performance
  tests deselected by default); full gate, Ruff and strict mypy green;
  independent contract, runtime-test and production-security reviews verdict
  **APPROVE / no blockers**. Two forced-ephemeral recomputations are compared
  by exact structure and canonical artifact bytes. A live wrong same-identity
  cache hit, an armed raising store, unchanged cache inventory, a deliberately
  alternating non-random control and fresh hash-seed subprocesses prove the verifier
  recomputes rather than certifying cached bytes. No production cache entry,
  data-layer caller or dependency was added.
- **Slice 2H:** COMPLETE on clean qualification commit `61d4446`; 1185 tests
  collected (1181 active, 4 performance tests deselected by default). The
  verified hit passed at 107.817 ms median against the owner-adjusted 150 ms
  budget, and the full TRAIN reference pass passed at 94.530 ms median against
  5 s, with mandatory verification unchanged. The 45,268,047-byte canonical
  artifact, exact timings/RSS/NaNs, 1,314 discontinuities and unchanged audit,
  D-6 and view digests are recorded in the normative plan. GapIndex remains
  ephemeral.
- **CP-8:** GREEN / APPROVE at clean pushed candidate `09ce941`. Independent
  contract, runtime and store/external-state reviews reproduced poisoned-cache
  and performance evidence, independently verified the exact-head identity and
  45,268,047-byte artifact, reconfirmed audit/D-6/all five manifests, and found
  no weakened guard, Phase-3 leakage or closeout blocker. Full default gate:
  1,181 active / 1,185 collected; architecture 128/128; Ruff and strict mypy
  green.
- **Pre-2B decision resolved:** exactly `target` and the case-sensitive prefix
  `target.` are reserved in both `feature_names` and `required_columns`, with
  fail-fast before computation and no broader V1 rule.
- **Objective:** vectorized, PIT-safe, cached feature computation.
- **Components:** FeatureProvider contract + WindowSpec (gap policy
  explicit) with **declared lookback feeding the sample information span**;
  generic future-perturbation leakage sentinel rig auto-applied to every
  registered provider; artifact store + MaterializationPolicy +
  identity/content hashing (**identity includes platform-code identity**);
  2–3 mundane reference features (returns, rolling stats) as rig proof —
  not research.
- **Dependencies:** Phase 1.
- **Slice 2H budget decision (owner-approved 2026-08-12):** the verified
  cache-hit target is **< 150 ms median**, replacing 100 ms after the first
  clean-commit qualification measured 124.4 ms over a 45,268,047-byte
  artifact. Verification/integrity checks stay unchanged; no optimization is
  authorized for the former target.
- **Blocking decisions:** none remain for Phase 2.
- **Acceptance:** sentinel rig green for all providers; verified cache hit < 150 ms;
  full-view reference feature pass < 5 s; identity-key stability test; plus,
  from the independent review:
  - **leakage rigs account for sample information spans** — a provider's
    declared lookback and the declared target span, not only the t+1 edge;
  - **cache identity includes platform code**: a platform commit (and diff
    identity for noncanonical runs) invalidates prior stage caches, proven
    by test;
  - **determinism verification bypasses cache reads** and recomputes — a
    verification run must never compare a cached artifact with itself;
  - `cache_if_expensive` is decided by explicit cost hint, not measured
    wall-time.
- **Excluded:** representations, models, orchestrator CLI.

## Phase 3 — Models, decisions, forecast evaluation & runs — **COMPLETE (2026-08-14) · SLICES 3A–3J COMPLETE · CP-9/CP-10/CP-11/CP-12 GREEN · CP-12 OWNER APPROVED**

- **Objective:** the cheapest possible hypothesis-kill loop, fully recorded.
- **Detailed plan (normative):**
  [docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) —
  slices 3A–3J, owner decisions OD-3A…OD-3G, minimal contracts, test matrix,
  performance protocol, checkpoints and completion checklist. This roadmap entry
  is status-only; do not duplicate detail here — update the plan document
  instead.
- **Contract:** [ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md)
  (**ACCEPTED 2026-08-13**) records the research-contract replacement plus the
  row-local inference invariant, stateful/online inference as out of V1 scope,
  the evaluator declared-requirements contract, determinism tolerance as
  identity-bearing contract state, and the V1 OutcomeSpec boundary/discontinuity
  semantics ADR-0010 delegates.
- **Components:** fit/infer + FittedState identity; **RepresentationModel in the
  same fit lifecycle**; **TrainingTargetFrame channel** (orchestrator-computed,
  config-declared OutcomeSpec); ModelOutputFrame + IntentFrame with a validated
  `intent_schema_id` literal (**sparse frames supported**); OutcomeSpec engine +
  reserved forward-reading namespace; **model/representation row-locality and
  forward leakage rigs**; **fold generation with derived purge**; plane-A
  evaluators with declared requirements; RunManifest, run dirs, state machine,
  heartbeats; **evaluation inventory**; **`scan_runs()` files-first run-record
  reconstruction**; CLI + detached execution; fingerprint/run_id/attempt;
  canonical-run Git check; reference models end-to-end; statistical trial
  counting; OOS access recording in the run record (auditing itself already live
  since Phase 1).
- **Dependencies:** Phase 2.
- **Blocking decisions:** **none remain.** All three were resolved on 2026-08-13:
  initial OutcomeSpec set → **OD-3A** (exactly one, `forward_log_return_v1`,
  observed-row horizon, `decision_delay_rows = 1`, boundary → `NaN`, no
  truncation); determinism tolerance defaults → **OD-3B** (exact by default;
  declared, identity-bearing tolerance only; never a verifier knob);
  fitted-state materialization default → **OD-3C** (**confirmed as already
  settled** by ADR-0007 and ADR-0008 — refit-per-run with `cache_if_expensive` —
  not reopened). Further approved scope: **OD-3D** SQLite deferred to Phase 5,
  **OD-3E** RepresentationModel included, **OD-3F** no carried-state/online
  inference API in V1, and **OD-3G** the 2026-08-14 owner approval of the
  identity-free `FittedPayload` / platform-bound `FittedState` authority
  correction recorded in ADR-0022 Amendment 3.
- **Acceptance:** config-only run → stored result < 30 s (dummy, no sim);
  N-policy fan-out with zero re-inference (stage counters prove it);
  double-run determinism verification passes; kill −9 → INTERRUPTED
  detected → re-run reuses cache; **run-record scan golden test** (replacing the
  former "index rebuild golden test", since SQLite moved to Phase 5 under
  OD-3D); plus, from the independent review:
  - **explicit TrainingTargetFrame channel**: targets reach `fit` only from
    the orchestrator via the config-declared OutcomeSpec; features,
    inference and policies cannot consume the outcome namespace; a model
    attempting its own forward read fails the sentinel rigs;
  - **fold-boundary purge enforced**: derived from the declared information
    span, applied automatically, recorded per fold in the RunManifest;
    reductions require a recorded reason; walk-forward leakage tests cover
    **fold** boundaries, not only view edges;
  - **evaluation inventory persisted** per run (model/fitted state,
    ModelOutput artifact/columns, OutcomeSpec, horizon/delay, evaluator/metric
    family) with **no policy dimension**; executed policies are recorded
    separately — the run count is not the trial count;
  - **sparse output/intent frames** round-trip: absence of a row stays
    distinct from a dense `NaN` row through persistence and evaluation;
  - **concurrency rules honoured** for run/cache persistence: atomic
    temp-file + rename writes for mutable run state, immutable no-replace
    publication for artifacts, append-only fsync'd audit log, race-free
    attempt assignment under concurrent launches (derived from run
    directories per ADR-0011 Amendment 3, not a stored counter). SQLite's
    single-writer and busy-timeout rules move with the index to Phase 5; the
    later atomic-generation realization is recorded in ADR-0017 Amendment 2;
  - **row-local inference enforced**: model and representation output at an
    evaluation row depends only on that row's declared inputs plus the fixed
    FittedState, proven by forward and row-locality perturbation rigs over
    every registered component; stateful/online inference is out of V1 scope
    (OD-3F, [ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md));
  - **determinism tolerance discipline**: exact by default, any tolerance
    declared and identity-bearing, never reachable from the CLI or verifier;
    the ADR-0016 stored-verification-report obligation discharged.
- **Excluded:** simulator, sweeps, portfolio logic, **the SQLite run index
  (Phase 5)**, the intent-schema registry, any carried-state or
  online-inference API, any Plane-B/trade metric.
  - **Corrected (2026-08-14):** this entry formerly assigned the intent-schema
    registry to Phase 4. Phase-4 planning (OD-4J) determined that one schema
    and one simulator give a registry nothing to resolve; the simulator instead
    declares its supported schema ids as a frozen literal tuple, exactly as
    `decisions/` does today. **The registry arrives with the second intent
    schema**, not with the first simulator.

## Phase 4 — Cost audit, simulator & trade evaluation — **COMPLETE (2026-08-15) · CP-13/CP-14/CP-15/CP-16 GREEN · OWNER APPROVED**

- **Objective:** measured execution realism for hypotheses that already
  survived Plane A, with the evidence status of every cost assumption
  permanently attached to the result.
- **Detailed plan (normative):**
  [docs/phases/PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) — owner
  decisions OD-4A…OD-4K, the closed public vocabulary, exact simulator
  semantics, slices 4A–4H, checkpoints CP-13…CP-16 plus the external CP-13C,
  the correctness remediation RM-1…RM-5 (§1.1), the test matrix,
  performance protocol and completion checklist. This roadmap entry is
  status-only; do not duplicate detail here — update the plan document instead.
- **Contract:** [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md)
  (**ACCEPTED 2026-08-14, Revision 1 2026-08-14** — the executable price path is
  separated from the monetary arithmetic and its side-dependent interpretation
  left explicitly unbound until the audit verifies the price side), with
  [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) **Amendment 1** (the
  cost quarantine gates *claims*, not construction, and `verified` is
  fail-closed against a repository-controlled binding) and
  [ADR-0015](../adr/ADR-0015-v1-single-symbol-scope.md) **Amendment 1** (no
  `PortfolioPolicy` symbol in V1).
- **Methodology boundary (normative):** Phase 4 adds
  `IntentFrame → Simulator → TradeLog/EquityCurve → Plane-B evaluation` and
  **changes no research methodology**. The simulator is a cost-and-execution
  filter applied to an edge Plane A already found; TP/SL tuning, execution
  settings and cost assumptions may not substitute for establishing predictive
  edge in Plane A first
  ([RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7).
- **Components:** the cost-semantics evidence record
  ([docs/research/COST_AUDIT.md](../research/COST_AUDIT.md)); a configuration-
  valued `ExecutionSpec`; **one** simulator —
  `bracket_single_position_v1`: one symbol, one open trade, fixed lot,
  LONG/SHORT/FLAT, configurable take-profit and stop-loss, deterministic costs
  carried in an identified `ExecutionCostBinding` with a declared
  `ExecutionPriceModel`;
  `TradeLog` and `EquityCurve` artifacts; **two** Plane-B evaluators
  (`trade_summary_v1`, `equity_drawdown_v1`); leakage/timing rigs with eight
  adversarial controls; harness-inversion canaries (a deliberately cheating
  model must win absurdly; a random model must lose ≈ costs).
- **Dependencies:** Phase 3 (COMPLETE). Broker documentation is required for
  the audit but **does not block implementation** — see below.
- **Blocking decisions:** **none remain.** The former "first supported
  intent-schema set" is resolved by OD-4A/OD-4B — exactly
  `target_exposure_intent_v1`, restricted to `{+1.0, 0.0, −1.0, NaN, absent}`,
  with any other finite value refused explicitly and **no intent-schema
  registry** while one schema exists. The cost audit is now an **evidence
  record** whose UNKNOWN **closure-required** facts gate only the promotion of
  money-denominated results to authoritative real-money estimates and any claim
  of verified execution-side semantics; synthetic-fixture verification,
  end-to-end qualification and the TRAIN performance smoke proceed while it is
  OPEN. **Slice 4A's baseline record is COMPLETE and CP-13 is GREEN** as the
  evidence-classification gate; audit closure is the separate external
  checkpoint **CP-13C** and is not a Phase-4 completion condition.
- **Acceptance:** hand-computed fixture truth exact; stop-loss wins every
  ambiguous bar and the ambiguity rate is reported; gapped opens priced at the
  observed open; no fill before the next observed row and none priced from a
  later row; each fold-policy simulation starts flat and closes deterministically
  at the boundary; canaries behave; exact bitwise determinism for `TradeLog`
  and `EquityCurve`; evaluation inventory exactly `F·S·E` (Plane A) + `F·P·B`
  (Plane B); `cost_semantics: verified` unreachable while no verified cost
  binding exists in the repository; full-period dense-intent simulation
  **< 60 s**; zero gated access and a byte-identical audit log.
  - **Corrected (2026-08-14):** the former acceptance line *"no fills inside
    GapIndex gaps per declared boundary policy"* is superseded. `GapIndex`
    lives in `data`, which `simulation` may not import, and execution happens
    only at **observed rows** — a bar that does not exist is not a fill
    opportunity, so no gap oracle can add information. The property is proven
    structurally instead ([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §9).
- **Implementation outcome (2026-08-15):** Slices 4B–4H are complete; CP-14
  and CP-15 are GREEN. The default gate is green (1,578 cases, Ruff and strict
  mypy). The clean full-axis qualification at `571fa6a` simulated all
  1,414,538 TRAIN rows with one warm-up plus five timed repetitions: median
  **1.325094530 s**, minimum 1.319865595 s, 211,961 completed trades and peak
  RSS 523,923,456 bytes. Audit, D-6 and all five view manifests remained exact.
  Results were permanently `UNVERIFIED` and made no research or real-money
  claim. CP-16 review returned **APPROVE WITH NON-BLOCKING NOTES**; F-1…F-3
  were remediated at `4351b9a`, follow-up review passed, N-1…N-9 remained
  untouched, and the owner approved Phase-4 closeout and merge.
- **Excluded:** portfolio logic and any `PortfolioPolicy` object · multi-symbol
  · multiple simultaneous trades · partial fills · trailing stops · a general
  order engine · limit/order-book simulation · margin/leverage · advanced or
  equity-dependent position sizing · pyramiding · netting/hedging modes · a
  slippage framework · a position-limit framework · order-type intent schemas
  and the intent-schema registry · a `GapIndex` dependency in `simulation` ·
  Sharpe or any return-ratio statistic, percentage return and percentage
  drawdown (no verified account base) · sweeps and deflated statistics
  (Phase 5).
  - **Corrected (2026-08-15):** sweeps are indeed Phase 5, but **deflated
    statistics are not** — owner decision **OD-5A** and
    [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md) exclude every
    deflated or multiple-testing-adjusted statistic from **V1 entirely**, not
    merely from Phase 4. The Phase-4 exclusion above stands and is permanent.

## Phase 5 — Experiment operations — **COMPLETE (2026-08-16) · 5A–5I COMPLETE · CP-17…CP-21 GREEN · CP-20 OWNER APPROVED**

- **Objective:** scale out experiments, not infrastructure.
- **Detailed plan (normative):**
  [docs/phases/PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) —
  owner decisions OD-5A…OD-5F, the discovered contradictions C-1…C-9, the
  approved architecture and authority boundaries, slices 5A–5I, checkpoints
  CP-17…CP-21, the test matrix, the performance-qualification protocol and the
  completion checklist. **This roadmap entry is status-only; do not duplicate
  detail here — update the plan document instead.**
- **Contract:** [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md)
  (**ACCEPTED 2026-08-15**) records the approved trial-counting rule and that
  **no deflated or multiple-testing-adjusted statistic ships in V1**. Four
  accompanying amendments, none reversing a decision:
  [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) **Amendment 4**
  (its Amendment-1 blocker is resolved),
  [ADR-0016](../adr/ADR-0016-determinism-tiers.md) **Amendment 1** (the
  thread-clamp obligation is implemented with one controlled environment and
  a fail-closed worker assertion),
  [ADR-0017](../adr/ADR-0017-storage-files-as-truth.md) **Amendments 1–2** (the
  concrete index realization, including that rebuild must use a **non-mutating**
  read path and publish one atomic staged generation) and
  [ADR-0018](../adr/ADR-0018-execution-model.md) **Amendment 1** (whole-run
  parallelism and the planning defaults).
- **Components:** sweep generation (child configs are full runs);
  **bounded process-pool execution of whole runs** with BLAS/thread clamps;
  resumable walk-forward; **the SQLite run index (moved here from Phase 3 by
  OD-3D, 2026-08-13) — a derived, rebuildable convenience over the files-first
  truth, built from the Phase-3 run-directory scan through a read-only path so
  "rebuild from files" is true by construction; a single private
  `journal_mode=DELETE` generation + atomic rename, busy timeout and bounded
  batches per
  [ADR-0017](../adr/ADR-0017-storage-files-as-truth.md)**; comparison CLI over
  the index; the **Trial Ledger** (an honest denominator, **no deflated
  metric**); disk telemetry (70 % warning).
- **Dependencies:** Phase 3 (Phase 4 for simulated sweeps).
- **Blocking decisions:** **none remain.** All six were resolved on
  2026-08-15: **OD-5A** multiple-testing output (Trial Ledger / honest
  denominator only; no deflated statistic, adjusted metric, p-value,
  significance claim or effective-independent-trial estimate; the production
  guard against `deflated` is not weakened) · **OD-5B** default trial-report
  scope is all runs in the runs root, with explicit narrowing · **OD-5C**
  whole-run parallelism only, folds sequential · **OD-5D** planning defaults of
  8 workers, 1 GiB assumed per-run admission RSS, 16 GiB budget, with
  qualification exercising 10 workers · **OD-5E** no evaluation-inventory schema
  bump for `fold_index`; the one-model-per-config invariant is pinned by an
  executable canary instead · **OD-5F** the Plane-B question tuple and ordered
  plural run-to-audit linkage. The former blocking decision recorded against
  [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) is **closed by
  ADR-0024**.
- **Acceptance:** 100-run sweep saturates ≥ 10 workers within RAM budget
  (workers × peak RSS ≤ 16 GiB); mid-sweep kill −9 → re-run executes only
  missing work; comparison query < 1 s over 1 000 runs; the multiple-testing
  report consumes the evaluation inventory and recorded OOS access counts,
  not raw run counts.
  - **Qualified (2026-08-15):** clean commit
    `b9f74ea748c399be657335aaf38ac9492da57b7d` passed H-1…H-10: 100/100
    children, 10-worker maximum with 75.1773% of active samples at capacity,
    4,674,699,264-byte maximum concurrent observed peak RSS, forced-kill
    resume dispatching exactly the 48 missing fingerprints, and 0.132875 s
    median comparison over a 1,000-run index. Audit/views/D-6 were unchanged.
    CP-17/CP-18/CP-19/CP-21 are GREEN. Remediation requalifications at
    `ecb9e5d` and `717fc03` kept H-1…H-10 green; exact evidence is in the
    Phase-5 plan §§19.5–19.7. The independent review chain, including bounded
    F-1…F-7 follow-ups, passed at reviewed head `22c08a2`; CP-20, Phase-5
    closeout and merge were OWNER APPROVED on 2026-08-16.
  - **Corrected (2026-08-15):** this entry formerly listed *"process-pool
    fold/sweep execution"* and *"basic multiple-testing report (trial
    denominator → deflated metrics)"*. Both are superseded. **OD-5C** limits
    process-pool execution to **whole runs**, because folds run inside the
    orchestrator that owns the fit lifecycle and derived purge, and
    parallelising them would risk the guarantees CP-9…CP-12 established for
    single-run latency the phase does not need. **OD-5A** replaces *"→ deflated
    metrics"* with the **Trial Ledger**, because evaluators produce
    `MetricValue` scalars with no null distribution, no p-value exists anywhere
    in the repository, and Phase 4 excluded every ratio statistic — so every
    named deflation method would require an input the platform does not record
    ([ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md)).
- **Excluded:** queue daemon · remote or distributed workers · dashboards ·
  PostgreSQL · generic cluster scheduler · portfolio/multi-symbol
  infrastructure · retention, GC or automatic deletion · plugin-specific
  experiment abstractions · **fold-level parallelism** · **nested parallelism
  of any kind** · **any deflated, adjusted or multiple-testing-corrected
  statistic, p-value or effective-independent-trial estimate** · query DSL ·
  ORM · schema-migration framework.

## Phase 6+ — Research plugins — **FIRST STRATEGY COMPLETE AND MERGED; PHASE 7 PLANNING**

- First real features/representations/similarity model/regime model land as
  plugins with their own tests and configs. Any infrastructure change they
  demand is an architecture finding requiring an ADR, not a quiet patch.
- **Zone Trader v13 — S-1…S-9 IMPLEMENTED 2026-08-19** (plan Revision 2).
  The normative plan and full TRAIN evidence are in
  [docs/phases/PHASE6_ZONE_TRADER.md](../phases/PHASE6_ZONE_TRADER.md);
  its owner decisions (OD-6A…OD-6D and G-1/G-2/G-3), implementation
  specifications (ID-1, ID-2), slices (S-1…S-9) and checkpoints (CP-22…CP-24)
  live there and are **not duplicated here**. The preregistered TRAIN H1 run is
  complete: five folds, two Plane-A trials, no OOS/LOCKBOX, and no consistent
  positive sign information. The independent review's dense-axis reach and
  documentation findings were remediated at `a200898` and `811db03`; the final
  verdict is APPROVE. The independent reviewer ran the full gate at `a200898`,
  and the implementing agent repeated it at `811db03` and the closeout head.
  Closeout/merge were OWNER APPROVED 2026-08-23 and the reviewed branch was
  merged into `main` at `aecceef`; the post-merge gate is GREEN.
- **Convention for Phase 6:** one normative plan per strategy family, named
  `PHASE6_<STRATEGY>.md` — Phase 6 covers unrelated research families, and a
  single accreting document would become unreviewable.

---

## Current status (Phase 6 first-strategy implementation complete, 2026-08-19)

| Item | Status |
|---|---|
| Phase 0A — baseline + documentation freeze | **COMPLETE** |
| Phase 0B — independent architecture review | **COMPLETE** |
| Phase 0 final ratification | **COMPLETE** |
| Phase 1 planning | **COMPLETE** (2026-08-07) |
| Phase 1 implementation | **COMPLETE (2026-08-10)** |
| Slice 1E planning | **COMPLETE** (2026-08-08, owner decisions OD-1…OD-6 approved) |
| Slice 1E implementation | **COMPLETE — reviewed head merged into `main`** |
| Focused independent Slice 1E review | **COMPLETE — APPROVE; owner approval COMPLETE** |
| Slice 1F planning/reconciliation | **COMPLETE** (2026-08-09, owner decisions OD-F1…OD-F6 approved) |
| Slice 1F Stage A implementation | **COMPLETE — reviewed head merged into `main`** |
| Focused independent Slice 1F Stage-A review | **COMPLETE — APPROVE WITH NON-BLOCKING NOTES; owner approval COMPLETE (2026-08-09)** |
| Slice 1F Stage-B production preparation | **COMPLETE** (2026-08-09, owner decisions D-1…D-6 and refinements R-1…R-3 approved) |
| Slice 1F Stage-B operational script | **COMPLETE — reviewed APPROVE WITH NON-BLOCKING NOTES; owner approval COMPLETE; merged into `main`** |
| Slice 1F Stage B production generation | **COMPLETE** (2026-08-09, fresh mode, no OD-F6 recovery) |
| Checkpoint CP-4 | **APPROVED** (2026-08-10) — independent review COMPLETE, verdict APPROVE WITH NON-BLOCKING NOTES |
| Slice 1F | **COMPLETE** |
| Slice 1G | **COMPLETE** — owner-approved 2026-08-10 |
| Slice 1H-A (legacy retirement + performance harness) | **COMPLETE** — reviewed APPROVE WITH NON-BLOCKING NOTES; owner-approved 2026-08-10 |
| OD-H2 (fully-gated SNAPSHOT qualification) | **OWNER APPROVED** (2026-08-10) |
| Performance qualification (Slice 1H-B) | **COMPLETE** (2026-08-10) — all measured budgets passed; cold unmeasured, sudo unavailable; audit 2→3 |
| Checkpoint CP-5 | **COMPLETE** (2026-08-10) — APPROVE WITH NON-BLOCKING NOTES; OWNER APPROVED |
| Phase 1 | **COMPLETE** (2026-08-10) |
| §13 post-Phase-1 operational checklist | **COMPLETE** (2026-08-12) — item 3 Hyper-V export owner-attested; item 4 owner review COMPLETE, all three items accepted; **Phase-2 gate CLEARED** |
| Phase 2 implementation | **COMPLETE (2026-08-12) — Slices 2A–2H COMPLETE; CP-6/CP-7/CP-8 GREEN; independent whole-phase verdict APPROVE** |
| Phase 2 planning | **COMPLETE** (2026-08-12) — OD-1…OD-5 APPROVED 2026-08-11 plus the 2026-08-12 implementation directions ([PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md)); **ADR-0021 ACCEPTED** |
| Phase 3 decisions | **COMPLETE** — OD-3A…OD-3F OWNER APPROVED 2026-08-13; OD-3G OWNER APPROVED 2026-08-14; **ADR-0022 ACCEPTED**; no blocking decision remains |
| Phase 3 | **COMPLETE (2026-08-14)** — Slices 3A–3J COMPLETE; CP-9/CP-10/CP-11/CP-12 GREEN; independent CP-12 verdict **APPROVE WITH NON-BLOCKING NOTES**, OWNER APPROVED |
| Phase 4 planning | **COMPLETE** (2026-08-14; RM-1…RM-4) — OD-4A…OD-4K OWNER APPROVED and unchanged; **ADR-0023 ACCEPTED (Revision 1)**; ADR-0020 Amendment 1 (revised) and ADR-0015 Amendment 1 |
| Phase 4 Slice 4A / CP-13 | **Slice 4A baseline record COMPLETE; CP-13 GREEN** (evidence-classification gate). External evidence collection continues in parallel and blocks no code |
| Phase 4 | **COMPLETE (2026-08-15)** — Slices 4A–4H complete; CP-13/CP-14/CP-15/CP-16 GREEN; CP-16 **APPROVE WITH NON-BLOCKING NOTES**, F-1…F-3 remediated at `4351b9a` and accepted by follow-up review; N-1…N-9 untouched; OWNER APPROVED |
| Phase 5 planning | **COMPLETE** (2026-08-15) — OD-5A…OD-5F OWNER APPROVED; normative record [PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md); **[ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md) ACCEPTED**; ADR-0011/0016/0017/0018 reconciled; no Phase-5 blocking design decision remains |
| Phase 5 implementation | **COMPLETE (2026-08-16)** — slices 5A–5I complete; H-1…H-10 green through remediation requalification; CP-17…CP-21 GREEN; independent review and F-1…F-7 follow-ups passed at `22c08a2`; CP-20 and closeout OWNER APPROVED |
| Phase 6 (Zone Trader) planning | **COMPLETE** (Revision 2); OD-6A…OD-6D and G-1/G-2/G-3 OWNER APPROVED; ID-1/ID-2 fixed |
| Phase 6 (Zone Trader) implementation | **COMPLETE**; S-1…S-9 and CP-22…CP-24 GREEN; independent verdict APPROVE at clean `811db03`; owner closeout/merge approved 2026-08-23 |
| Phase 6 merge | **COMPLETE** at `aecceef`; post-merge gate GREEN |
| Phase 7 Zone TP selector planning | **COMPLETE**; TRAIN inventory complete (100 three-arm configurations, 92,330 complete matched trades); ADR-0025 and OD-7A…OD-7D OWNER APPROVED 2026-08-23; implementation authorized; no VALIDATION access before CP-26 |
| Phase 7 Zone TP selector | **COMPLETE — NO VALIDATION EVIDENCE OF IMPROVEMENT**; CP-25 APPROVE at `61c39ac`; CP-26 froze 92,330-row TRAIN corpus and fitted state; exactly 400/400 VALIDATION runs completed with zero failures; dynamic mean total gross R 6.070725 versus best fixed TP800 7.828575; 19 paired wins, 45 losses, 36 ties; both primary criteria failed; no tuning, OOS or LOCKBOX |
| First H1 result | TRAIN only, five folds, `horizon_rows=5` observed M1 rows, 2 Plane-A trials; sign accuracy fold range 0.446875…0.516667 and rank-IC range -0.135103…0.052631; no consistent positive evidence; Plane B remains deferred |
| Exact next action | **Stop Phase 7 as preregistered. Do not tune on VALIDATION and do not access OOS/LOCKBOX. Any new selector requires a fresh owner-approved TRAIN-only study.** Result: [ZONE_TRADER_TP_SELECTOR_VALIDATION_V1.md](../research/ZONE_TRADER_TP_SELECTOR_VALIDATION_V1.md) |
| Phase 4 cost audit | **OPEN** — [COST_AUDIT.md](../research/COST_AUDIT.md); external broker documentation required for the **closure-required** facts; gates authoritative money claims and any claim of verified execution-side semantics, not implementation. Closure is checkpoint **CP-13C**, not a Phase-4 completion condition |

Architecture V1 and ADR-0001…0020 are **ACCEPTED** (2026-08-06), with
ADR-0007, 0008, 0009, 0010, 0011, 0012 and 0014 amended in response to the
[independent review](../architecture/reviews/2026-08-06-independent-review.md).
[ADR-0021](../adr/ADR-0021-columnar-feature-contract.md) (2026-08-12),
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) (2026-08-13) and
[ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) (2026-08-14) and
[ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md) (2026-08-15) are
also ACCEPTED; Phase-4 planning additionally amended
[ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) and
[ADR-0015](../adr/ADR-0015-v1-single-symbol-scope.md) once each, and Phase-5
planning amended
[ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md),
[ADR-0016](../adr/ADR-0016-determinism-tiers.md),
[ADR-0017](../adr/ADR-0017-storage-files-as-truth.md) and
[ADR-0018](../adr/ADR-0018-execution-model.md) once each, reversing no
decision.
Phase 1's detailed implementation plan is owner-approved:
[docs/phases/PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md). Slice 1C
is complete at `aaf1dba`, independently approved at CP-2, and merged into
`main` through reviewed commit `a0dcb3f`. Slice 1D's original implementation
at `76f55864a5116b751b5f817b5f0f7536a07d34c1` (after dependency commit
`144dbae56a6eda41154d33c919a61046939571c8`) was followed by remediation
commits `16474b7cce383e028d3262e5c06e3f32c152b786`,
`e266455fda691c6162aba817eef49f5ace4fe09f`, and
`41db586845455d8fd32db731869674258302f188`; second-remediation commits
`27c5567f69dd8719be8f27b80d21caa2fade8c2e` and
`bf9285d4580d05d30884885a8f526d108295eb8d`, with completion record
`1d7dc70550ac50323138a3edd8fc83156d26ef1b`; and third-remediation guard
commit `8ee33f1175c0186445088a9e8b4aff3d7c707be7`, followed by the documentation
head `742a75635313a2fd43afd9263bffc561d6cf8928`. Final bounded guard commit
`11e2f4f1976aa7e43d40647370e7ca5358b58032` pins canonical public bindings
and rejects module-level dynamic public attributes; the following
documentation completion record defines the finite architecture-test threat
model and stopping rule. The final bounded independent review approved the
runtime Slice 1D contract and confirmed the closed ten-mutation
architecture-test contract at reviewed head
`2e474532c6cea4a19ebfd94928b13cbec998fc1f`. One
cloud-environment/interpreter-sensitive test observation was non-blocking and
does not change the accepted runtime contract. The owner approved CP-3 on
2026-08-08.

Slice 1E **planning** is COMPLETE: the owner approved six decisions
(OD-1…OD-6) covering the single approved composition route
`open_bounded_view` alongside the unchanged sole decoder
`MarketFrameLoader.load`, the `segments`/`eval_frame` model replacing a
cross-view `frame()`, structured insufficient-context evidence with no
durable write in Phase 1, the LOCKBOX evaluation-vs-fitting split, the
consecutive policy-role context chain, and empty-evaluation-range refusal.
Details are in [PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) with
implementation guidance in
[ADR-0012](../adr/ADR-0012-two-tier-splits.md) and
[ADR-0013](../adr/ADR-0013-lockbox-enforcement.md). The implementation is now
**COMPLETE** through commits `423b39f`, `8b09cdc`, `58d5fad`, and
`28764c8`; reviewed implementation head
`84bee8187a72aa361f3b9b986bf84b4d00a63818` is merged into `main`. The focused Slice
1E/loader/frame/architecture suite passed 225 tests, and the full gate passed
594 tests, Ruff, and strict mypy. The finite seven-case Slice 1E architecture
mutation/regression set passed. Tests used synthetic manifests, temporary
Parquet roots, and in-memory audit facilities only: no real-data contact,
generated views, real audit log, or persistent run artifact occurred. The
focused independent review is COMPLETE with verdict APPROVE, and owner
approval is COMPLETE. Its private `_policy` coupling and tautological
`ViewUnionMember.require_matches()` observations are non-blocking, schedule no
immediate remediation, and do not block Slice 1F.

Slice 1F **planning and reconciliation** are COMPLETE: the owner approved six
decisions (OD-F1…OD-F6) on 2026-08-09 covering the internal generation library
and thin Stage-B script, the single fresh-generation keys-only source
ceremony, the one-bundle timestamp with deterministic SNAPSHOT-anchor resume,
the two-record audit model, and the internal dtype declaration bound to the
adapter by conformance test. Complete column preservation, multi-dataset
generality and file-layout independence are approved; an earlier proposal to
refuse non-cap straddling partitions was not approved and is superseded. The
normative contract is in
[PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) (Slice 1F); no ADR
changed.

A pre-implementation architecture check on `codex/phase1f` then correctly
stopped before writing code and surfaced two real contradictions, both now
resolved without any schema, loader, gating or audit change. **OD-F6**
withdraws the zero-reread resume requirement: a SNAPSHOT partition entry
retains only aggregate `first_key`/`last_key`/`rows` per physical file, which
cannot reconstruct per-role facts when a file straddles a Tier-1 boundary, so
resume now performs one new audited keys-only scan, adding one
`dataset_access_decision` and no second `snapshot_binding`, and refusing when
generator code identity differs. Separately, an approved **Stage-A
no-real-data validation profile** excludes the pre-existing real TRAIN smoke,
which `scripts/gate.sh` would otherwise execute unfiltered on this VM. Slice
1F **Stage A implementation is COMPLETE** after 56 focused
foundation/generation tests, 279 relevant data-layer tests, 76 architecture
tests and all 654 tests in the owner-approved no-real-data suite passed,
together with Ruff, strict mypy and `git diff --check`. Only synthetic/temp
Parquet and in-memory audit facilities were used; the real dataset tree was
unchanged and the real views root and audit log remain absent. The focused
bounded independent Stage-A review is **COMPLETE** with verdict **APPROVE WITH
NON-BLOCKING NOTES** and found no blocker; the **owner APPROVED Stage A on
2026-08-09** and the exact reviewed head
`79bac876f84bcc915532b9bad246e74200e67041` was fast-forwarded into `main`. The
review's four non-blocking notes are recorded in
[PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) and schedule no
immediate remediation. Stage-B production generation has since **COMPLETED**
(2026-08-09, fresh mode, no OD-F6 recovery): the five hash-pinned Tier-1 view
manifests exist under the generated-views root. **CP-4 was owner-approved on
2026-08-10 and Slice 1F is COMPLETE**; Slice 1G and Slice 1H-A have since been
completed and owner-approved, while performance qualification, CP-5 and Phase 2
remain NOT_STARTED.

Stage-A validation is green under the owner-approved no-real-data profile.
The pre-existing real TRAIN integration smoke was intentionally excluded
because executing it would violate the Stage-A no-real-data boundary. This
was not an unfiltered full gate.

Slice 1F **Stage-B production preparation is COMPLETE**: the owner approved
production decisions D-1…D-6 (view ids, generator identity, CURATED access
reason, two-mode script interface, real-smoke placement, canonical
immutable-dataset fingerprint) and operational refinements R-1…R-3 (real smoke
after owner confirmation; preflight may create only the empty canonical views
root; a complete bundle is a terminal no-op before authorization) on
2026-08-09. Detail is normative in
[PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) (Slice 1F); no ADR
changed and OD-F1…OD-F6 are unchanged.

The thin Stage-B operational script is now **COMPLETE / REVIEW PENDING**. Its
default and explicit `preflight` modes are metadata-only with only the approved
empty-views-root creation side effect; `execute` requires the exact `GENERATE
VIEWS` confirmation before the real TRAIN smoke and CURATED authorization; and
a complete bundle is a terminal no-op. The maintained commands, fresh/OD-F6
resume/complete sequences, D-6 fingerprint method and CP-4 evidence candidate
are recorded in [PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) (Slice
1F). The script was then independently reviewed (**APPROVE WITH NON-BLOCKING
NOTES**), owner-approved and merged into `main` at
`f8b6168511fa2544479c0ad96a16c50cf0ab7890`, and **Stage-B production generation
completed successfully on 2026-08-09** in **fresh** mode with no OD-F6 recovery:
five hash-pinned Tier-1 view manifests published, snapshot bound key
`1785542220`, exactly two audit records (`dataset_access_decision` then
`snapshot_binding`), and the immutable-source D-6 fingerprint unchanged. No
research use of the generated views has occurred.

The independent CP-4 review is **COMPLETE** with verdict **APPROVE WITH
NON-BLOCKING NOTES** and **CP-4 was OWNER APPROVED on 2026-08-10**, closing
**Slice 1F**. Its four non-blocking notes are recorded in
[PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) and schedule no
immediate remediation.

Slice 1G is **COMPLETE and OWNER APPROVED on 2026-08-10**. Planning approved
decision **OD-G2**: V1 GapIndex ships no `boundary_class` field, because no
current component can honestly supply one. The reasoning and the additive-only
condition for adding boundary facts later are recorded in
[ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md); the accepted contract
and implementation outcome are normative in
[PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md) (Slice 1G). No ADR
decision changed.

The implementation ships one public symbol `GapIndex` in `data/gaps.py` —
arithmetic-only, discontinuities only, an explicit positive signed-int64
`nominal_interval`, four immutable `int64` fields (`prev_key`, `next_key`,
`delta`, `deviation`), no persistence, no caching, no semantic session or
calendar claims, and no data-loading, gating or audit authority. The focused
independent review returned **APPROVE**; its two findings — N-1 (an oversized
`nominal_interval` escaping the refusal contract) and N-2 (`isinstance`
admitting `MarketFrame` subclasses) — were remediated by
`fa69733367ee4c340894ff9a0d5d8b6b99f92a6e` and confirmed **RESOLVED** on
re-review. That exact reviewed head was fast-forwarded into `main`.

Slice 1H planning followed, with **OD-H1 OWNER APPROVED on 2026-08-10** fixing
the exact legacy retirement scope, and **Slice 1H-A is COMPLETE and OWNER
APPROVED on 2026-08-10** at reviewed head
`64be3756bc7d4fdff2c5465774c3b5fc26998c71`. It retired the obsolete
provider-driven path with no replacement shim, preserved the accepted
`ResearchModel` → `ModelOutput` → `DecisionPolicy` separation, left the Phase-2
deferred carriers intact, and added a test-only, dependency-free, TRAIN-only
performance harness with no gated performance route. The focused independent
review returned **APPROVE WITH NON-BLOCKING NOTES**; its five notes schedule no
remediation.

**OD-H2 was OWNER APPROVED on 2026-08-10**, authorizing one fully-gated load of
the exact published SNAPSHOT `XAUUSD_S_M1_SNAPSHOT_V1` solely for Phase-1
integrity, timing and peak-RSS qualification — explicitly not research use, with
no value-column content inspected, derived or retained. The Stage 1H-B
operational contract is recorded, **reconciled with §8 and PLANNED**: one
authorization in one long-lived process covers the SNAPSHOT peak-RSS reading,
the five warm repetitions, **one discarded cold trial plus five measured cold
repetitions** and the integrity evidence, with TRAIN reported separately as the
common case. The expected audit
delta is exactly **+1** (2 → 3), with execution required to STOP on any other
cardinality.

> **HISTORICAL RECORD — SUPERSEDED. Not a current instruction.**
> The paragraph below states the position as it stood *before* the
> qualification ran. **Stage 1H-B qualification is COMPLETE (2026-08-10)** with
> every measured budget passed and the audit log at 3 records; **CP-5 is
> COMPLETE and OWNER APPROVED**; **Phase 1 and Phase 2 are COMPLETE**; and
> Phase-3 planning is COMPLETE. The current next action is in
> [PROJECT_STATE.md](../../PROJECT_STATE.md).
>
> *"Performance qualification has not been executed and no gated SNAPSHOT access
> has occurred. The exact next action is: Implement/reconcile only the minimal
> Stage 1H-B harness changes required by the OD-H2 operational contract, review
> them if code changes are needed, then execute the owner-approved qualification
> exactly once according to the recorded runbook. Do not begin CP-5 or Phase 2."*
