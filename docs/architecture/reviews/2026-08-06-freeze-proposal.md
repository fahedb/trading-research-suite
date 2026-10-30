# V1 Architecture Freeze Proposal — Design-Review Record

> **Provenance note.** This document was generated during the architecture
> discovery/freeze session of 2026-08-06, before any implementation. It is
> **not** the concise operating architecture — for day-to-day use it is
> superseded by [docs/architecture/V1.md](../V1.md). It is retained verbatim
> as design provenance: the objections, alternatives, and trade-offs below
> are part of the historical record and must not be silently rewritten.
> Owner ratifications applied after this document (AO-1/AO-2 acceptance,
> split dates, OOS non-default discovery, runs root, lockbox ceremony
> without agent hook, MarketFrame minimality, statistical trial counting)
> are recorded in the ADRs and PROJECT_STATE.md.

Statement tags: **[OF]** observed fact · **[AD]** accepted decision ·
**[PP]** provisional policy · **[AS]** assumption · **[DD]** deferred decision.

---

## Architectural objections (raised before the decision table)

### ARCHITECTURAL OBJECTION AO-1 — D11's snapshot view contains D10's lockbox rows

D11 creates a snapshot view covering first-usable-bar → 2026-07-31. D10
locks 2026-01-01 → 2026-07-31. Therefore **the snapshot view itself contains
the entire lockbox period**. If the snapshot becomes the "canonical V1
dataset" that default discovery returns, every ordinary experiment can read
lockbox rows and D10's protections are void.

**Resolution proposed:** the snapshot is an *identity anchor and parent*,
never a working dataset. View manifests gain a `role` and a
`discovery_default` flag; default discovery returns only non-gated roles.
The full snapshot and the lockbox both carry `discovery_default: false` and
sit behind the same loader gate. Consequence: no experiment config can name
the full snapshot without triggering the lockbox ceremony.
*(Ratified 2026-08-06, with the further owner amendment that OOS is also
excluded from default discovery: defaults are TRAIN + VALIDATION only.)*

### ARCHITECTURAL OBJECTION AO-2 — "All splits as materialized views" collides with walk-forward research

D10 mandates materialized views over runtime slicing. Applied literally, a
20-fold walk-forward study needs ~40 view manifests per fold-geometry
configuration, exploding on every sweep. The mandate is right for
*integrity boundaries* and wrong for *procedural folds*.

**Resolution proposed — a two-tier split model:**
- **Tier 1, integrity splits** (TRAIN / VALIDATION / OOS / LOCKBOX):
  materialized curated views, hash-pinned, roles enforced by the loader.
  These are leak *barriers*.
- **Tier 2, procedural folds** (walk-forward windows, CV folds, embargo
  zones): runtime-derived native-key ranges **strictly inside one Tier-1
  view**, fully recorded in the RunManifest, with a runtime check that every
  fold ⊆ its parent view and a test that fold generation is deterministic.
*(Ratified 2026-08-06.)*

Two smaller formalization additions flagged inline: the fit lifecycle
extends to RepresentationModel (§6), and OOS received an access-recording
regime (§4) — later strengthened by owner amendment to non-default
discovery.

---

## 1. Executive decision table

| ID | Decision (status) | Rationale | Alternative rejected | Key consequences |
|---|---|---|---|---|
| D1 | Generic `NativeTimeKey` {value:int64, unit, clock_domain}; no UTC invention; "no untagged time" **[AD]** | Honest to `BROKER_WALL_CLOCK_ENCODED_AS_EPOCH` data **[OF]**; MT5 field name stays adapter-side | Force-UTC (falsifies by +2–3 h, unverifiable DST history); naive datetimes (ambiguity returns) | Calendar/cross-domain work blocked behind explicit resolvers |
| D2 | Columnar hot path; domain objects only at boundaries/tests **[AD]** | 2.33 M rows ≈ 165 MB columnar vs GBs + 10⁷ allocations as objects **[OF]** | Object model; DF-library as public contract | Alignment discipline replaces per-object validation → leakage sentinels mandatory |
| D3 | Curated manifest is sole admission authority; hash-verified loads; `GapIndex` (arithmetic facts only) **[AD]** | Prevents re-admitting excluded 2011–2019 by glob; avoids session claims without a calendar policy | Trust-the-directory loading; semantic session model in V1 | A market-closure claim anywhere in V1 is a bug by definition |
| D4 | Fixed-stage pipeline; windowing is a spec consumed by plugins, not a stage **[AD]** | Cheap experimentation; no DAG engine; window-free models pay nothing | General DAG engine; fused single call | Stage list closed in V1; new stage = ADR |
| D5 | `MaterializationPolicy`: ephemeral / cache_if_expensive / persist / persist_on_failure **[AD]** | Avoids write-everything I/O tax while keeping reuse and resume | Persist-everything; persist-nothing | Identity key (cheap) split from content hash (on materialize/verify) |
| D6 | Explicit `fit → FittedState`, `infer(fitted_state)`; identity pins all training inputs **[AD]** (+extension to RepresentationModel) | The leakage surface of every learned approach must be a recorded object | Fit hidden inside infer; fitted state as opaque pickle | "What data did this model see" is a query, not archaeology |
| D7 | Generic ModelOutput; **IntentFrame family**, `TargetExposureIntentV1` first; schema-id negotiated with simulator **[AD]** | Multi-policy fan-out over one persisted inference; exposure not enshrined forever | Direction enum + conviction; order-level output now | Simulators declare supported intent schemas; pre-run compatibility check |
| D8 | Two evaluation planes: forecast (no sim) and trade (post-sim); versioned OutcomeSpec **[AD]** | Most hypotheses die cheaply at plane A | Single terminal evaluation | OutcomeSpec is the only forward-reading component — quarantined |
| D9 | `experiment_fingerprint` ≠ `run_id` (ULID) ≠ `attempt_number`; canonical requires clean Git **[AD]** | Same fingerprint run twice = determinism verification | run_id = content hash (cannot represent retries) | Dirty-tree runs only as noncanonical with diff hash captured |
| D10 | Integrity splits as views: TRAIN 2020–23, VAL 2024, OOS 2025, LOCKBOX 2026-01-01→07-31 **[PP — research policy]**; two-tier model per AO-2 **[AD]** | Leak barriers materialized and hash-pinned; folds stay practical | All-runtime slicing; all-materialized folds | Platform code never hardcodes these dates |
| D11 | New immutable snapshot view ending at last observed bar ≤ 2026-07-31 (native encoded calendar); raw untouched; later data = new identity **[AD]**; snapshot non-default per AO-1 | Frozen research universe; live-edge partition neutralized | In-place raw truncation; ignoring the live edge | Boundary **value** binds at view creation — no parquet reader in this session's env **[OF]** **[DD → Phase 1]** |
| D12 | Single symbol/instrument/frame per experiment; PortfolioPolicy inert seam **[AD]** | Keeps Phase 4 small; contracts avoid foreclosing multi-symbol | Multi-asset contracts now | Cross-asset research out of V1 scope |
| D13 | Cost semantics quarantined until a dedicated audit; ExecutionSpec fields reserved but unset **[AD]** | Spread-column semantics unverified **[OF]** | Plausible-default costs now | No cost-bearing simulation before the audit |
| D14 | Determinism tiers: exact integer/structural; pinned+seeded+toleranced float; no cross-hardware bitwise claim **[AD]** | Honest and testable on one machine | Bitwise-everywhere | Double-run comparison is a first-class operation |
| D15 | Files (Parquet+JSON, run dirs) are truth; SQLite rebuildable index; no Postgres; no auto-deletion; warn at 70 % disk; no pickle **[AD]** | Single-user machine; index corruption is an inconvenience, never a loss | Postgres now; DB-as-truth | Registry rebuild-from-dirs is a tested code path |
| D16 | Modular monolith; one experiment = one detached CLI process; linger + `systemd-run --user`, tmux fallback; heartbeats + stage caching; no /tmp bulk **[AD]** | SSH-independence with zero new infrastructure; `/tmp` is 11.5 GiB tmpfs **[OF]** | Queue daemon; nohup-and-hope | INTERRUPTED is a detected state |
| D17 | Keep & refactor `~/trading-research-suite` in place; Git baseline first; incremental with tests green **[AD]** | Contracts/tests/layering are assets; 1.1 k lines cheap to reshape | Fresh repo | Migration plan governs sequencing |

## 2. Architecture V1

**Purpose.** A single-machine, strategy-agnostic research platform in which
a research idea = plugin implementation(s) + experiment config + tests, and
in which every result is reproducible, leak-audited, and comparable. Not
built around vector similarity, XAUUSD, M1, MT5, or any model family; the
current dataset is merely the first adapter.

Component diagram, stable boundaries, plugin boundaries, dependency
direction, and prohibited dependencies: as recorded in
[docs/architecture/V1.md §2](../V1.md) (identical content, maintained there).

## 3. Time model

**NativeTimeKey** = `{value: int64, unit: seconds|ms|us|ns, clock_domain}`.
Dataset: unit `seconds`, domain `mt5.justmarkets-demo3.wallclock` **[OF]**.
Frames carry a bare int64 index + one frame-level tag — never per-row key
objects. Cross-domain comparison/joins raise; no resolver exists in V1.

One `CalendarTimePolicy` ships: `native-wallclock-v1` — mechanical decode
treated as broker wall-clock civil time, explicitly non-UTC, valid only in
this domain; the same calendar D10/D11 themselves invoke.

Works without resolution: ordering, PIT slicing, observed-bar windows,
GapIndex arithmetic, fit/infer/decision alignment, bar-unit forecast
evaluation, simulation. Blocked until a real resolver exists: UTC mapping,
external time-based feeds, cross-domain studies — an external-data feature
request in V1 is an automatic ADR trigger.

## 4. Data and view model

**DatasetRef** = `(dataset_id, manifest_sha256, role)`; runs pin all views
by id and hash. View manifest **v1.1** adds `role`, `discovery_default`, and
optional per-partition `admitted_native_range` (the 1.0.0 format has none of
these **[OF]**).

Proposed view family **[PP]**: snapshot (row-bounded, non-default), TRAIN
2020–2023 / VAL 2024 / OOS 2025 (partition-aligned), LOCKBOX 2026-01-01 →
boundary (row-bounded, gated). Rows after the snapshot boundary are
unreachable through any V1 view; future exports create a new raw identity.

**MarketFrame** — columnar arrays keyed by schema name, indexed by strictly
increasing native keys, tagged with (unit, clock_domain, DatasetRef); views
bounded, zero-copy where practical; a view's upper bound is the PIT
boundary.

**GapIndex / ContinuityMap** — purely arithmetic; no weekend/holiday/
session/outage semantics. An optional annotator under `native-wallclock-v1`
may label gaps, output clearly marked policy-derived, stored separately.

**Integrity** — `verify: full | cached`; `full` required for canonical runs
and any gated-view access; mismatch = hard refusal; curated manifest is the
only admission source; no globbing (tested).

**Lockbox enforcement:** discovery filter; loader gate (CLI flag AND config
reason, both required); write-ahead audit (append-only JSONL, fsync, before
data; audit failure ⇒ denial); permanent `lockbox_touched`; tests; optional
hardening (agent hook — *not approved for V1*; chmod tripwire). **Honest
residual risk:** on a single-user machine the owner can bypass via bespoke
code outside the platform; the goal is no accidental, no silent access.

**OOS regime [PP]:** originally access-recorded but discoverable; owner
amendment (ratified) removed OOS from default discovery — explicit access,
recorded, lighter than lockbox.

## 5. Stage and artifact model

Stages are pure, deterministic functions of (declared inputs, identity,
seed), declaring input/output artifact types, identity contribution, and
cost hint. **Identity key** (pre-execution, from inputs; always computed;
cache key; key mismatch = invalidation; immutable entries, no TTL, no GC in
V1) vs **content hash** (on materialization or verification).

| Policy | Meaning | V1 defaults |
|---|---|---|
| `ephemeral` | in-memory; identity key recorded | cheap features; IntentFrames without sim |
| `cache_if_expensive` | cache if wall-time ≥ threshold (default 5 s) or plugin hints expensive | feature frames; representations; FittedState |
| `persist` | always written to run dir / cache + reference | ModelOutputFrame, TradeLog, EquityCurve, OutcomeFrames |
| `persist_on_failure` | dump stage inputs/partial state on exception | opt-in debug |
| *(always)* | RunManifest, resolved config, metrics, identity, audit, failure record | — |

Serialization: Parquet/Arrow frames; JSON manifests; versioned array formats
for FittedState payloads; **pickle prohibited** as durable format.

## 6. Plugin contracts (semantic)

Common laws: declared identity `name@semver` + validated params; causality
(output at t depends only on inputs ≤ t — except OutcomeSpec); determinism
under (inputs, params, seed); vectorized; pure.

- **FeatureProvider**: declared columns, per-output lookback, WindowSpec with
  explicit gap policy where windowed; window-free providers declare none.
- **RepresentationModel**: `space@semver`, dim; same fit lifecycle
  (formalization addition — a scaler/PCA is fitted state).
- **ResearchModel**: output schema (floats + labels + optional confidence);
  `fit_requirements`; fit sees only the training view; **no trading
  vocabulary in outputs** (banned-keys test carried forward).
- **FittedState**: opaque payload + identity manifest pinning model id/
  params, training DatasetRef + range, input identities, code hash, dep lock
  hash, seed.
- **ModelOutputFrame**: eval-view native-key index; provenance header; the
  fan-out artifact.
- **DecisionPolicy**: `required_output_keys` pre-checked; backward-looking
  state allowed; emits declared intent schema.
- **IntentFrame family**: `intent_schema_id`; `TargetExposureIntentV1`:
  `target_exposure ∈ [−1,+1]`, `NaN` = abstain/no new instruction (holder
  keeps prior state), `0.0` = explicit flat. Future: orders/limits/stops/
  staged entries — new ids, models untouched.
- **OutcomeSpec**: horizon (explicit unit), reference price, decision delay,
  boundary policy (`NaN | truncate | skip`), alignment; the only
  forward-reader; reserved namespace models cannot consume.
- **Evaluators**: plane A (ModelOutputFrame + OutcomeFrame) and plane B
  (TradeLog + EquityCurve); consume artifacts only; structurally cannot
  recompute upstream.
- **Simulator**: (IntentFrame, view, ExecutionSpec) → TradeLog + EquityCurve;
  ExecutionSpec fields reserved (delay, fill reference, spread/commission/
  slippage models, position limits) — semantically unbound until the D13
  audit, which is the simulation phase's entry gate.

## 7. Experiment lifecycle

Hypothesis (mandatory free text) → config → resolution & gating (registry,
params, model↔policy keys, policy↔simulator schema, integrity, lockbox gate,
Git state → canonical determination, fingerprint, ULID, run dir, PENDING) →
split/fold resolution (Tier-1 pinned; Tier-2 computed, checked ⊆ view,
recorded) → features/representations (cache-aware) → fit (train view only)
→ inference (ModelOutputFrame persisted) → policy fan-out → plane-A
forecast evaluation (**a config may legitimately end here — the cheap
kill**) → optional plane-B simulation + trade evaluation → persistence &
indexing (metrics, hashes, environment, COMPLETE, trial counted, flags) →
comparison via index queries.

**Interruption/recovery:** heartbeat cadence; stale-heartbeat RUNNING is
marked INTERRUPTED by the next CLI contact; recovery = re-run same config:
same fingerprint, new run_id/attempt, cached stages skip; walk-forward folds
are independent cache units.

## 8. Run identity and state machine

Fingerprint composition as in [V1.md §7](../V1.md); machine identity
captured but excluded. run_id = ULID; attempt_number derived;
`(fingerprint, attempt_number) ↔ run_id`.

Canonical requirements: clean tree @ recorded commit; lockfile in sync;
`full` integrity; no lockbox access unless a declared lockbox-evaluation run
(own audited class). Noncanonical: dirty tree only with diff captured/hashed
+ visible flag; second-class in the index.

```
PENDING ──start──▶ RUNNING ──success──▶ COMPLETE   (terminal)
                     │ ├──recorded error──▶ FAILED  (terminal per attempt)
                     │ └──SIGTERM trap───▶ INTERRUPTED (graceful self-mark)
                     └──crash/kill −9: RUNNING + stale heartbeat ⇒ observer
                        marks INTERRUPTED on next CLI contact
FAILED / INTERRUPTED ──new attempt (same fingerprint, new run_id)──▶ PENDING
```

FAILED = process-recorded deterministic error; INTERRUPTED = external
termination. Attempts append-only; COMPLETE never re-opened.

## 9. Reproducibility contract

| Dimension | Captured | Tier |
|---|---|---|
| Code | commit, dirty flag, diff hash, per-plugin source hashes | exact |
| Environment | uv.lock hash, Python, OS/kernel, BLAS env, machine descriptor (not in fingerprint) | exact capture; no cross-hardware equality claim |
| Data | DatasetRef id + manifest SHA256 per view, ranges, integrity mode | exact |
| Plugins/params | ids, semvers, canonical params, source hashes | exact |
| Config | resolved canonicalized form | exact |
| Seeds | global + per-stage derivation | exact |
| Ordering/splits/alignment | native-key ranges, fold specs | exact |
| Float artifacts | content hashes; documented per-artifact tolerances | toleranced |

SemVer is a human label; the source hash is the proof — both recorded, only
the hash trusted.

## 10. Target repository structure

As adopted (see V1.md and the repo itself): `docs/{architecture,adr,roadmap,
research}`, `scripts/gate.sh`, `src/trading_research/{core,data,features,
representations,models,decisions,outcomes,simulation,evaluation,artifacts,
experiments}`, `tests/{architecture,unit,properties,leakage,golden,fixtures}`;
runs root and TMPDIR outside the repo (CLAUDE.local.md).

## 11. Migration plan from the existing skeleton

**Kept:** layer packages + registry mechanism; AST architecture tests
(extended); errors hierarchy (grows Lockbox/ClockDomain/Integrity errors);
Protocol contract style; ModelOutput→DecisionPolicy separation +
banned-vocabulary test; pydantic configs (`extra="forbid"`); fingerprint
mechanics (repositioned per D9); test scenario vocabulary.

**Replaced/retired:** Bar/MarketContext tuple carrier → MarketFrame + views;
tz-aware-only invariant → NativeTimeKey/ClockDomain; fused `run_pipeline` →
staged orchestrator; per-row `Signal` + `Direction` enum → IntentFrame
family; `PipelineRecord` object lists → artifacts; per-step FeatureSet/
ModelOutput dicts → frames (types demoted to boundary/test vocabulary);
infer-only lifecycle → fit/infer + FittedState.

**Sequence:** git baseline → ADRs → add core/time + core/frames alongside
old types (add-then-migrate) → data layer on new types → re-point contract
tests (same scenarios, new carriers) → artifacts + orchestrator →
single-commit removal of retired types. Estimated survival of the 50 tests:
~70 % of intent, ~40 % of text.

## 12. Phase roadmap

Maintained operationally in [docs/roadmap/V1_ROADMAP.md](../../roadmap/V1_ROADMAP.md)
(identical phase set with objectives, components, dependencies, acceptance
criteria, performance budgets, exclusions, statuses).

## 13. V1 non-goals

As in [V1.md §10](../V1.md).

## 14. ADR plan

Realized as [docs/adr/](../../adr/README.md), ADR-0001…0020.

## 15. Architecture risks and failure modes

| # | Risk | Likelihood | Blast radius | Mitigation |
|---|---|---|---|---|
| R1 | Vectorized alignment bug leaks one future bar everywhere | high without rigs | every result invalid, silently | sentinel rigs on every provider; cheater/random canaries; view-edge property tests |
| R2 | Cache identity omits an input → stale artifact reuse | medium | wrong results marked reproducible | identity-composition tests; double-run verification |
| R3 | Lockbox bypassed via ad-hoc script outside the platform | medium | research-integrity loss | gate + audit + tests + review discipline; honest residual |
| R4 | Overfitting-to-VALIDATION via unlimited replays | high (process risk) | inflated expectations | trial counting from day one; OOS recording; deflated metrics in Phase 5 |
| R5 | Float nondeterminism erodes trust | medium | noisy regressions | tiers; BLAS clamps; tolerances; verification reports |
| R6 | Broker re-export hash churn on 2026 | certain eventually | identity confusion | new IDs only; snapshot anchor; loader refusals |
| R7 | Disk fill mid-sweep (59 GB free) | medium | failed runs | 70 % warning; materialization defaults; revisit GC post-V1 |
| R8 | GapIndex annotation drifting into session claims | medium | subtle feature bias | ADR-0005 boundary; policy-derived labels segregated |
| R9 | Orchestrator creep toward a DAG engine | medium | maintenance tax | closed stage list; new stage = ADR |
| R10 | SQLite index/files divergence | low | wrong comparisons | index derived-only; rebuild golden |
| R11 | Heartbeat misdetection | low | spurious restarts | generous staleness threshold; idempotent restart |
| R12 | `native-wallclock-v1` mistaken for UTC | medium | wrong session features | naming; no `utc` identifier anywhere; schema test |

## 16. Architecture review checklist (for an independent attacker)

**Time.** Any path where two clock domains compare without a resolver? Where
a naive decoded datetime participates in computation rather than display?
Where `native-wallclock-v1` output is silently treated as UTC?
**Leakage.** Can any feature/model/policy read index > t through view edges,
fold boundaries, or fitted state trained on overlapping data? Does the
sentinel rig cover stateful policies? Can outcome columns reach model inputs
under renaming?
**Splits.** Can a Tier-2 fold escape its Tier-1 view? Can a config name the
snapshot or lockbox without the ceremony? Is OOS access genuinely recorded?
**Identity.** Change one plugin default param without a version bump — does
the fingerprint change anyway (source hash)? Does a dirty tree ever produce
`canonical: true`? Can two executions collide on run_id?
**Artifacts.** Is any input to any stage absent from its identity key? Can an
ephemeral artifact be silently required by a later resumed stage?
**Data.** Does any loader path glob directories? Is a hash mismatch ever
downgraded to a warning? Can an excluded partition be reached via any API?
**Determinism.** Does double-run verification actually compare content
hashes per tolerance tier? Are BLAS clamps applied in workers, and tested?
**Recovery.** Kill −9 at each stage boundary — is the run dir always
recoverable? Is COMPLETE ever mutated?
**Scope discipline.** Any portfolio, multi-symbol, cost-assumption, or UTC
code hiding behind a flag? Any pickle writes?
**Honesty.** Are residual risks (lockbox owner-bypass, cross-hardware
floats) stated in-repo rather than implied away?

## 17–18. CLAUDE.md and CLAUDE.local.md drafts

Realized as the committed `CLAUDE.md` and `CLAUDE.local.md.example` (Phase
0A); the drafts in the original session message are superseded by those
files.

## 19. Enforcement map

| Rule | Enforced by |
|---|---|
| Excluded partitions unreachable | type/contract (manifest-only loading) + test |
| Hash verification hard-fail | runtime validation + test; not FS-enforced (residual) |
| No directory globbing for data | test + review |
| No untagged time; frame domain tags | type/contract + runtime validation |
| Cross-domain compare/join refusal | runtime validation (+ type where expressible) + test |
| No UTC invention | contract (no UTC identifier) + test + CLAUDE.md for out-of-band |
| PIT causality | **test only** (sentinel rigs + canaries) — why the rigs are mandatory |
| Outcome namespace quarantine | runtime validation + architecture test |
| Lockbox default-deny | runtime gate + discovery filter + write-ahead audit + tests; ad-hoc owner bypass: CLAUDE.md only + audit-absence detectability — residual, stated |
| OOS access recording | runtime validation (flag + index) |
| Fold ⊆ integrity view | runtime validation + property test |
| Canonical ⇒ clean Git | runtime CLI check + Git policy |
| Fingerprint completeness | test (param/source-hash sensitivity) |
| No mutation of COMPLETE runs | runtime state machine + append-only layout |
| Registry rebuildable | test (rebuild golden) |
| No pickle artifacts | architecture test (import ban) + review |
| Layer budgets; no cross-plugin imports | architecture tests (AST) |
| Outputs free of trading vocabulary | test (banned keys) |
| Config unknown keys rejected | type/contract (pydantic forbid) |
| BLAS clamps in workers | runtime bootstrap + worker env test |
| No /tmp bulk | runtime path config + CLAUDE.md for ad-hoc |
| Disk 70 % warning | runtime validation |
| Determinism | golden tests + double-run verification |
| Long jobs detached | CLAUDE.md instruction + CLI defaults |

## 20. Remaining open decisions (as of the freeze session)

*Items 1–4 (AO rulings, split dates, runs root, lockbox ceremony) were
ratified by the owner on 2026-08-06 — see PROJECT_STATE.md. The rest remain
open:*

**Before Data Layer:** view-manifest v1.1 field sign-off *(implicitly
ratified via AO acceptance; final confirmation in Phase 0B)*; snapshot
boundary binding procedure; integrity default for dev loops; broker DST
documentation. **Before Models:** initial OutcomeSpec set; determinism
tolerances; fitted-state materialization default. **Before Simulation:**
cost audit (spread semantics, commission, slippage, lot value); supported
intent schemas. **Safely deferred:** retention/GC · multi-symbol · queue
daemon · PostgreSQL (indefinite) · ANN backends · deflated-Sharpe details ·
cross-domain resolvers · additional calendar policies · lockbox tooling
polish.
