# Architecture Decision Records — Index

ADR-0001 … ADR-0020 are **ACCEPTED** (ratified 2026-08-06 after the Phase 0B
[independent review](../architecture/reviews/2026-08-06-independent-review.md),
verdict *accept with required changes*). Architectural changes require a new
or amended ADR before implementation.

**ADR-0021 is ACCEPTED (2026-08-12)**, added during Phase-2 planning. It records
the columnar feature contract replacing the skeleton's per-point
`FeatureProvider.compute(MarketContext) -> FeatureSet`, and exists because
replacing a public plugin contract is exactly the change the rule above governs.
Its Slice-2B implementation guidance records the owner-approved exact reserved
namespace (`target` and names beginning `target.`) without broadening or changing
the decision.
It amends no earlier ADR.

**ADR-0022 is ACCEPTED (2026-08-13)**, added during Phase-3 planning. It records
the Phase-3 research-contract replacement — `ResearchModel`,
`RepresentationModel`, `DecisionPolicy` and `Evaluator` — and exists for the same
reason ADR-0021 does. Most of it operationalizes ADR-0006…ADR-0012 and
ADR-0016…ADR-0018 unchanged; its genuinely new decisions are the **row-local
inference invariant**, **stateful/online inference as out of V1 scope** with its
reconsideration trigger, the **evaluator declared-requirements contract**,
**determinism tolerance as identity-bearing contract state rather than a
verifier knob**, the **V1 OutcomeSpec boundary/discontinuity trigger semantics**
that ADR-0010 delegates, and the record that **`X → X+1` is an approved
alignment policy, not a claim that `X`-origin forecasting inherently leaks**.
It amends no earlier ADR. **Amended twice on 2026-08-13** during the
pre-implementation reconciliation and **once on 2026-08-14** during Slice 3C,
always against itself and never touching an earlier ADR: *Amendment 1* closes
the concrete Slice-3A vocabulary
(one `ViewSpan` descriptor for both lifecycle positions; each research stage
consumes frames and never market columns, with `ResearchModel` taking
`ModelInputs` and `RepresentationModel` taking a `FeatureFrame` **directly** so
representation chaining is structurally unavailable; distinct
`TrainingTargetFrame` / `OutcomeFrame` carriers making ADR-0010's channel
structural; `MetricValue`; `ToleranceSpec`), and *Amendment 2* restores the
**dependency identity** that ADR-0008 requires of `FittedState` and that
ADR-0022's original restatement omitted. *Amendment 3* separates plugin-owned
`FittedPayload` from platform-owned `FittedState`: `fit` returns only learned
arrays and metadata, while the orchestrator alone binds the complete identity;
the correction was **OWNER APPROVED as OD-3G on 2026-08-14** without changing
the amendment's substance.

**ADR-0023 is ACCEPTED (2026-08-14)**, added during Phase-4 planning, for the
same reason ADR-0021 and ADR-0022 exist: it records a public-contract addition —
`ExecutionSpec`, `SimulationRequest`, `TradeLog`, `EquityCurve`,
`TradeExitReason` and the `TradeEvaluator` protocol — together with the
normative execution semantics that give them meaning. Its decisions are the
**methodology boundary** (Plane A first; the simulator is a cost-and-execution
filter, never an edge-discovery mechanism), the **closed minimal simulator
scope**, **discrete fixed-lot intent semantics with explicit refusal** of any
other finite `target_exposure` value, **next-observed-row entry timing as
execution semantics only**, **stop-loss-wins intrabar ambiguity** with a
reported ambiguity rate, **gapped opens priced at the observed open**,
**state-following target semantics** for re-entry and reversal, **per-fold flat
start with a deterministic boundary close**, **costs as explicitly united money
charges behind an evidence gate**, and the **Plane-B evaluation-inventory
identity** that carries the `DecisionPolicy` dimension Plane A deliberately does
not.

It **amends no earlier ADR**. In particular
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) is unchanged:
its frame-family mechanism permits the additive
`bracketed_exposure_intent_v1` schema accepted by ADR-0023 Amendment 1 while
leaving `target_exposure_intent_v1` unchanged. Fixed fallback brackets remain
execution configuration; per-trade distances may be policy-emitted intent. The
simulator's `{+1, 0, −1}` restriction remains a declared capability limit for
the exposure channel, not a reinterpretation of either schema.
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) is likewise unchanged:
its Amendment 1 already admits the policy dimension "where a decision plane was
evaluated", and Plane B is that place. Two separate, narrowly scoped amendments
accompany it and are listed below.

**Revision 1 (2026-08-14, pre-merge review remediation).** An independent review
of the planning branch found that ADR-0023 §8 bound the *executable price path*
permanently while the price side of the recorded OHLC series is still UNKNOWN —
a one-sided series would displace executable entries and every bracket level
derived from them, which is wrong trigger timing, not an accounting detail. §8
is rewritten: the price-path semantics are separated from the monetary
arithmetic, V1 declares a **named provisional** price model, and the
side-dependent interpretation stays **explicitly unbound** until the audit
verifies it. The contingent cost/unit fields move into an identified
`ExecutionCostBinding`. **No owner decision changed**; the remediation is
recorded as RM-1…RM-4 in
[PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §1.1.

**Amendment 1 (2026-08-22).** The Zone Trader family fired ADR-0023's explicit
per-decision-bracket reconsideration trigger. A second schema,
`bracketed_exposure_intent_v1`, carries independently optional TP/SL distances
in price units on each opening instruction; the existing simulator identity
accepts both schemas, resolves each side per instruction with fixed-config
fallback, freezes resolved levels for the trade lifetime, and records them in
TradeLog format v2. No registry was added because one simulator still creates
no schema-resolution choice. Existing configs, the original intent schema and
historical experiment fingerprints are unchanged.

**ADR-0024 is ACCEPTED (2026-08-15)**, added during Phase-5 planning. It exists
because it records a **research-methodology contract**, not an implementation
detail: it closes the multiple-testing denominator question that
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) Amendment 1 left
explicitly open as *"a blocking decision before any deflated statistic ships in
Phase 5"*. Its decisions are the **trial unit** — one trial is one distinct
`(hypothesis, question)` pair that produced at least one evaluation, with
`hypothesis = experiment_fingerprint` and the *question* composed from
evaluation-inventory dimensions — the **collapse and exclusion rules** (Plane A
and Plane B counted separately and never summed; folds collapse; attempts
sharing a fingerprint collapse; descriptive runs and pre-evaluation failures
contribute zero; executed-but-unevaluated policies contribute zero; policy
fan-out affects Plane B only where a Plane-B evaluation actually occurred), the
explicit record that **correlation between related variants cannot currently be
estimated** from what the platform persists so such variants are counted
individually as a conservative denominator without any claim of independence,
and — as **owner decision OD-5A (2026-08-15)** — that **no deflated or
multiple-testing-adjusted statistic ships in V1**, because the repository lacks
the approved statistical inputs one would require and inventing a null
distribution would violate the platform's research-integrity goals. What ships
instead is the **Trial Ledger**: the denominator itself, with a mandatory
caveat, defaulting to all runs in the runs root (**OD-5B**). It amends
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) by resolution, recorded
there as *Amendment 4*, and amends no other earlier decision.

**ADR-0025 is ACCEPTED (2026-08-23)** for the owner-requested Zone Trader TP
selector. It would add an immutable cross-run training-bundle artifact, the
sole platform authority for fit targets derived from recorded evaluation
artifacts, a sibling corpus-fit model lifecycle, a fitted-state training-source
union and an evaluation-only frozen-state experiment mode. ADR-0025 and
OD-7A…OD-7D were explicitly owner-approved on 2026-08-23.

**Amended during Phase-5 planning (2026-08-15), no decision reversed:**
**ADR-0011** (*Amendment 4* — Amendment 1's blocking decision is resolved by
ADR-0024; raw run count remains invalid, the evaluation inventory remains the
counting source, and no adjusted statistic ships in V1) · **ADR-0016**
(*Amendment 1* — the accepted BLAS/thread-clamp obligation is recorded as
the implementation gap found during planning and its Phase-5 closure: every
worker runs with an explicit five-variable single-thread clamp, all launch
paths receive equivalent controlled environments, and the worker **fails
closed** when a clamp is absent or wrong) ·
**ADR-0017** (*Amendments 1–2* — the concrete Phase-5 index realization: derived
and disposable with files authoritative, located at `<runs_root>/index/`
outside the run collection, **rebuilt through a non-mutating read path**
correcting the schedule note's assumption that the mutating `scan_runs()` could
populate it, rebuild/delete never modifying research truth, corruption recovery
by rebuild-from-files, no participation in identity, canonicality, gating,
audit or evaluation results, one atomic `journal_mode=DELETE` staged
generation with busy-timeout/bounded-batch values, and
the 70 % disk warning as a concrete implementation obligation with no GC) ·
**ADR-0018** (*Amendment 1* — **OD-5C**: local parallelism is whole-run only,
folds stay sequential, no nested parallelism, no queue daemon, no remote
workers, correcting the roadmap's former "process-pool fold/sweep execution"
wording; and **OD-5D**: planning defaults of 8 workers, 1 GiB assumed per-run
admission RSS and a 16 GiB budget, with qualification exercising 10 workers —
recorded as budgeting constants, not architecture assumptions about every
future model).

**Amended during Phase-4 planning (2026-08-14, revised the same day):**
**ADR-0020** (*Amendment 1* —
the cost quarantine is re-scoped from a construction ban to a claims gate: an
explicit evidence record in
[docs/research/COST_AUDIT.md](../research/COST_AUDIT.md), an explicit
`cost_semantics` declaration, a permanent run marking, and a prohibition on
representing unverified money output as a real-money estimate; the reserved
field list is confirmed as not a construction mandate, so slippage and
position-limit fields are omitted rather than stubbed — and, as revised,
`verified` is **fail-closed** against a single repository-controlled
`VERIFIED_COST_BINDING` constant created only when the audit closes, over a
closure rule stated on **closure-required** facts so that deliberately
non-modelled effects cannot make closure impossible) · **ADR-0015**
(*Amendment 1* — V1 ships **no** `PortfolioPolicy` symbol: with one symbol, one
open trade and a fixed lot there is nothing for a pass-through seam to pass
through; the single-symbol decision itself is unchanged).

**Amended during ratification** (decision text changed in response to review
findings): **ADR-0007** (platform-code identity in cache keys; verification
bypasses cache; hint-driven `cache_if_expensive`) · **ADR-0008** and
**ADR-0010** (fit-time `TrainingTargetFrame` channel; forward-reading ban
scoped to features/inference/policies) · **ADR-0009** (sparse output and
intent frames) · **ADR-0011** (per-run evaluation inventory; denominator
semantics deferred to a Phase 5 blocking decision; fingerprint commit
sensitivity; attempt-assignment concurrency) · **ADR-0012** (mandatory
derived purge at fold boundaries; `context_start`/`eval_start`/`eval_end`
view semantics; adjacent-view fit unions; normative split-date table) ·
**ADR-0014** (raw/curated parent identities gated like the lockbox;
keys-only audited snapshot binding).

**Cross-references and implementation guidance** were recorded without
changing any decision: ADR-0005 (manifest gap labels are provenance only,
never GapIndex semantics) · ADR-0013 (its discovery filter now points at
ADR-0014's range-derived gated set, so it cannot be read alone and
under-applied) · ADR-0016 (verification bypasses cache) · ADR-0017 (atomic
temp-file + rename writes, append-only fsync'd audit log, and a single-writer
SQLite generation with busy timeout **in Phase 5** — the guidance originally named Phase 3; owner
decision **OD-3D (2026-08-13)** moved persistent SQLite run indexing to Phase 5,
which is a schedule reconciliation and no decision change).

**Further guidance recorded during Phase 1 planning (2026-08-07), no
decision change:** ADR-0003 (read-only NumPy arrays are the public column
contract; PyArrow never crosses into a public signature) · ADR-0004
(canonical `DatasetRef` identity is exactly `dataset_id` + `manifest_sha256`;
`role` is a validated field, not a third identity component — mismatch
against the manifest's declared role is a contract error; a filesystem path
is resolver configuration, never identity) · ADR-0014
(the gating boundary is read from a versioned split-policy record and
every normalized manifest requires a concrete observed admitted range;
missing or unresolved ranges are malformed and refused, not default-gated;
generated view manifests live under a separate platform-managed root, never
the dataset root). Detailed Phase 1 execution plan:
[docs/phases/PHASE1_DATA_LAYER.md](../phases/PHASE1_DATA_LAYER.md).

**ADR-0012 is the single normative source for the V1 split dates.** Other
documents summarize and link to it.

| ADR | Title |
|---|---|
| [ADR-0001](ADR-0001-record-architecture-decisions.md) | Record architecture decisions as ADRs |
| [ADR-0002](ADR-0002-native-time-model.md) | Native time model: NativeTimeKey + ClockDomain, no UTC invention |
| [ADR-0003](ADR-0003-columnar-hot-path.md) | Columnar hot path; minimal MarketFrame; domain objects at boundaries |
| [ADR-0004](ADR-0004-dataset-admission-and-integrity.md) | Curated-manifest admission authority and hash-verified integrity |
| [ADR-0005](ADR-0005-gapindex-not-sessionmodel.md) | GapIndex (arithmetic facts), not a semantic SessionModel |
| [ADR-0006](ADR-0006-fixed-stage-pipeline.md) | Fixed-stage pipeline; windowing is a plugin spec, not a stage |
| [ADR-0007](ADR-0007-materialization-policy.md) | MaterializationPolicy; identity key vs content hash |
| [ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) | Explicit fit lifecycle and FittedState identity |
| [ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) | Generic model output and the IntentFrame family |
| [ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) | Two-plane evaluation and versioned OutcomeSpec |
| [ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) | Run identity triple, canonical runs, statistical trial counting |
| [ADR-0012](ADR-0012-two-tier-splits.md) | Two-tier splits: integrity views and procedural folds (AO-2) |
| [ADR-0013](ADR-0013-lockbox-enforcement.md) | Lockbox enforcement model |
| [ADR-0014](ADR-0014-snapshot-and-view-roles.md) | Snapshot policy, view roles, default discovery (AO-1) |
| [ADR-0015](ADR-0015-v1-single-symbol-scope.md) | V1 scope: single symbol; PortfolioPolicy as inert seam |
| [ADR-0016](ADR-0016-determinism-tiers.md) | Determinism tiers and double-run verification |
| [ADR-0017](ADR-0017-storage-files-as-truth.md) | Storage: files as truth, SQLite as rebuildable index, no PostgreSQL |
| [ADR-0018](ADR-0018-execution-model.md) | Execution model: detached CLI processes, no queue daemon |
| [ADR-0019](ADR-0019-keep-and-refactor-skeleton.md) | Keep and incrementally refactor the existing skeleton |
| [ADR-0020](ADR-0020-cost-semantics-quarantine.md) | Cost semantics quarantined until a dedicated audit |
| [ADR-0021](ADR-0021-columnar-feature-contract.md) | Columnar feature contract: FeatureProvider, WindowSpec, FeatureFrame |
| [ADR-0022](ADR-0022-phase-3-research-contracts.md) | Phase-3 research contracts: fit/infer, row-local inference, evaluator requirements, declared tolerance |
| [ADR-0023](ADR-0023-phase-4-simulation-contracts.md) | Phase-4 simulation contracts: bracket execution, TradeLog/EquityCurve, Plane-B evaluation |
| [ADR-0024](ADR-0024-multiple-testing-trial-ledger.md) | Multiple-testing denominator: the Trial Ledger, and no adjusted statistic in V1 |
| [ADR-0025](ADR-0025-cross-run-training-and-frozen-state.md) | Cross-run training bundles and frozen-state experiment inference |
| [ADR-0026](ADR-0026-uls-entry-gating-recorded-trades.md) | ULS + PUSH entry gating from recorded TRAIN trades |
| [ADR-0027](ADR-0027-uls-entry-conditioned-tp-selection.md) | ULS entry-conditioned seven-arm take-profit selection |
| [ADR-0028](ADR-0028-uls-joint-entry-tp-selection.md) | One ULS model for entry abstention and four-way TP selection |

Format per ADR: Status · Date · Context · Decision · *(Amendment(s), where
ratification changed the decision)* · Alternatives considered ·
Consequences · When to reconsider · Related.

Amendments are appended in place with their date and originating review
finding rather than rewriting the original decision text, so the reasoning
trail stays visible in Git history and in the document itself.

- [ADR-0029 — Combined public source distribution](ADR-0029-public-suite-distribution.md)
- [ADR-0030 — Local Codex Gaps adapter](ADR-0030-codex-gaps-adapter.md)
- [ADR-0031 — General experiment-authoring workflow](ADR-0031-experiment-authoring-workflow.md)

- [ADR-0032 — Checked preparation and general launch](ADR-0032-prepared-experiment-launch.md)
