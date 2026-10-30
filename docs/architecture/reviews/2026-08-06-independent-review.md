# Independent Architecture Review — Phase 0B record

> **Provenance.** Independent, read-only architecture review conducted
> 2026-08-06 by a fresh review session with no involvement in the design.
> **Verdict: ACCEPT WITH REQUIRED CHANGES.**
> Architecture reviewed at commit `d32b843c578a6064f2e12aafd2a04bcca2c278cc`
> (`docs: record proposed V1 architecture and project operating rules`),
> covering `docs/architecture/V1.md`, `docs/roadmap/V1_ROADMAP.md`,
> `docs/research/RESEARCH_POLICY.md`, ADR-0001…0020, the 2026-08-06 freeze
> proposal, the skeleton source and tests, and verified VM/dataset facts.
>
> The report is **preserved verbatim below** and must not be rewritten,
> shortened, sanitized, or silently reconciled with later decisions. The
> owner-ratified responses to its findings live in the amended ADRs
> (ADR-0007, 0008, 0009, 0010, 0011, 0012, 0014) and in
> [docs/architecture/V1.md](../V1.md) — not in this file.
>
> *Path note:* file references inside the report are written relative to the
> reviewer's working directory (`/path/to/workspace`), e.g.
> `trading-research/src/...`. They are left exactly as written rather than
> re-pointed to this document's location, because the report is verbatim.

---

# Independent Architecture Review Report — trading-research V1

Reviewer: independent session, 2026-08-06. Mode: read-only. Basis: all listed documents read in full; all 20 ADRs read in full; skeleton source and tests read; claims verified against the VM where practical. No lockbox market rows were read (verification touched manifests, whole-file hashes, and TRAIN-period data only).

**Verification of documented claims performed:** gates green — OBSERVED (50 tests pass, ruff clean, mypy strict clean); clean git tree at `d32b843`, two commits as described — OBSERVED; `Linger=yes` — OBSERVED; dataset manifests match PROJECT_STATE row counts and ranges — OBSERVED; curated 2020 partition SHA256 matches its manifest — OBSERVED (spot check); skeleton matches ADR-0019's characterization (per-row `Bar`/`MarketContext` carriers, tz-aware-only rule, fused `run_pipeline`, infer-only lifecycle, `Direction`/`Signal`) — OBSERVED in `domain.py` (historical source), [pipeline.py](../../../src/trading_research/core/pipeline.py), [interfaces.py](../../../src/trading_research/core/interfaces.py).

---

## 1. EXECUTIVE VERDICT

**B. ACCEPT WITH REQUIRED CHANGES.**

This is an unusually disciplined proposal: honest about its data (the no-UTC stance is correct and rare), honest about residual risks, and restrained about infrastructure. Nothing requires redesign. However, I found four genuine contract gaps that are cheap to fix now and expensive after Phase 1–3 exist: the fit-time training-label channel is self-contradictory as written; purge/embargo at fold boundaries is not required anywhere; the raw/curated parent datasets are an undocumented lockbox bypass; and view-composition semantics (backward context, adjacent-view unions) are unspecified in a way that directly shapes the Phase 1 view contract. All four are resolvable by ADR amendment with no code. Freeze after those amendments.

---

## 2. BLOCKING FINDINGS

**B-1 — The training-label channel is contradictory/undefined**
- **Severity:** HIGH · **Area:** research contracts / leakage (ADR-0008, ADR-0010)
- **Evidence:** DOCUMENTED CLAIM — ADR-0010: "OutcomeSpec is the ONLY component permitted to read forward of t; its outputs live in a reserved namespace that **features, models, and policies cannot consume**." ADR-0008: "`fit(train_view, inputs) → FittedState`; fit sees only the training view." The first planned plugin is explicitly "historical similarity and **future outcome statistics**," and any supervised model needs forward-looking targets **at fit time**.
- **Failure scenario:** Phase 3 implementer must choose between (a) models computing forward returns internally inside `fit` — silently reintroducing distributed forward-reading, the exact archaeology ADR-0008 exists to kill — or (b) a platform on which supervised learning is structurally impossible. Both are wrong, and whichever gets built first becomes precedent.
- **Why it matters:** This is the single most-used interface of the whole research program, and the current text makes the flagship plugin unimplementable without violating an ADR.
- **Recommended resolution:** Amend ADR-0010 (and cross-reference in ADR-0008): the reserved-namespace ban applies to **inference-time and feature/policy inputs**. Fit-time consumption of outcome targets is legal only through a declared channel: the experiment config names an OutcomeSpec whose targets are computed *within the training view* (view truncation still bounds them), and that OutcomeSpec identity is pinned into FittedState identity. Ad-hoc forward reads inside `fit` remain banned and sentinel-tested.
- **ADR/docs affected:** ADR-0008, ADR-0010, V1.md §6, RESEARCH_POLICY.md §5. **Requires code now?** NO.

**B-2 — No purge/embargo requirement at Tier-2 fold boundaries**
- **Severity:** HIGH · **Area:** research integrity / walk-forward (ADR-0012)
- **Evidence:** DOCUMENTED CLAIM — ADR-0012 lists "embargo zones" only as an *example* of a Tier-2 object; nothing requires one. INFERENCE — with fit-time labels of horizon H (B-1), a fold that fits on `[t0, t1]` and evaluates `(t1, t2]` trains on labels at bars near `t1` that were computed from bars **inside the evaluation range**. A similarity reference library carrying future-outcome statistics has the same defect: neighbours near the fold edge embed evaluation-period outcomes. Tier-1 boundaries are protected structurally (view truncation kills edge labels); Tier-2 boundaries inside one view are protected by nothing.
- **Failure scenario:** Every walk-forward result on overlapping-label models is inflated, silently, by an amount that grows with horizon. This is the classic overlapping-label leak; it produces exactly the "convincing but wrong" result class this platform exists to prevent.
- **Why it matters:** Walk-forward inside TRAIN is the intended workhorse workflow. A leak here invalidates the platform's core loop, not an edge case.
- **Recommended resolution:** Amend ADR-0012: the platform computes a **minimum purge** at every Tier-2 fit/eval boundary equal to the fit-time label horizon (plus declared feature lookback where state carries over), applies it by default, records it in the RunManifest, and requires an explicit recorded override to reduce it. Extend the Phase 2/3 leakage rigs to cover fold boundaries, not just view edges.
- **ADR/docs affected:** ADR-0012, RESEARCH_POLICY.md §4–5, V1_ROADMAP Phase 3/5 acceptance. **Requires code now?** NO.

**B-3 — Raw and curated parent datasets are an unstated lockbox bypass**
- **Severity:** HIGH · **Area:** lockbox enforcement (ADR-0013, ADR-0014)
- **Evidence:** OBSERVED FACT — `XAUUSD_S_M1_CURATED_V1` admits the 2026 partition: 210,573 rows through 2026-08-06, i.e. the entire lockbox period **and** post-snapshot rows. DOCUMENTED CLAIM — ADR-0013/0014 gate the SNAPSHOT and LOCKBOX **views** and set default discovery to TRAIN+VALIDATION, and ADR-0014's role enum includes `raw`, but no document states that resolving the raw or curated **dataset identity directly** is gated or even non-default.
- **Failure scenario:** An experiment config names `XAUUSD_S_M1_CURATED_V1` instead of a view role. The loader verifies hashes, admission is manifest-legal, no ceremony fires, and 2026 data flows into ordinary research with no `lockbox_touched` marking. AO-1 was raised for exactly this shape of hole in the snapshot — the parent datasets have the identical property and were missed.
- **Why it matters:** The lockbox is only as strong as its weakest platform-legal path. This one is fully platform-legal as documented.
- **Recommended resolution:** Amend ADR-0014: any DatasetRef whose admitted native range extends past the OOS upper boundary — today: the raw dataset, the curated dataset, SNAPSHOT, LOCKBOX — carries `discovery_default: false` **and** sits behind the full ADR-0013 ceremony. Add this to the Phase 1 gate tests ("a config naming the curated parent without ceremony raises and audits").
- **ADR/docs affected:** ADR-0014 (ADR-0013 cross-ref), V1.md §4 table, CLAUDE.md splits section. **Requires code now?** NO (test lands in Phase 1).

**B-4 — View-composition semantics unspecified: backward context and adjacent-view unions**
- **Severity:** MEDIUM · **Area:** data/view contract, evaluation correctness (ADR-0012, V1.md §4)
- **Evidence:** DOCUMENTED CLAIM — "a view's upper bound IS the point-in-time boundary" defines the future edge; nothing defines the **lower** edge's meaning. Folds "must lie entirely inside one Tier-1 view." INFERENCE — two unaddressed consequences: (1) evaluating on VALIDATION with a W-bar lookback feature either burns in W bars (a 30-day rolling window ≈ 43k M1 bars ≈ 8–15% of the year, computed on different history than during training — a silent train/eval inconsistency) or an implementer silently lets reads cross into TRAIN with no rule saying which is legal. (2) The final pre-lockbox workflow ("developed on TRAIN, selected on VALIDATION, then OOS/lockbox") standardly refits on TRAIN+VALIDATION(+OOS) — but no contract says whether a fit view may be an ordered union of adjacent Tier-1 views, and RESEARCH_POLICY §4's "spanning TRAIN+VALIDATION is a promotion decision, not a fold" can be misread as forbidding it.
- **Failure scenario:** Phase 1 ships views as simple closed ranges; Phase 3 evaluation silently differs from fit-time feature behavior at every view's left edge; final lockbox evaluations test artificially stale models, or people hand-hack combined ranges — the exact runtime slicing ADR-0012 bans.
- **Why it matters:** Backward reads are causally safe; conflating "no forward leakage" with "no backward context" loses data and distorts metrics without protecting anything. This decision is the *shape* of the Phase 1 view/MarketFrame contract — deciding it later means rework.
- **Recommended resolution:** Amend ADR-0012/V1.md §4: a bounded evaluation view has `(context_start, eval_start, eval_end)`; backward context may extend into strictly **earlier** Tier-1 views (never later ones, never into gated views the run isn't cleared for); the fold-containment and purge rules apply to `[eval_start, eval_end]` plus label horizon. Separately: a fit view may be a recorded, ordered union of **adjacent** non-gated Tier-1 views, each hash-pinned individually (this is the mechanical form of a "promotion decision").
- **ADR/docs affected:** ADR-0012, V1.md §4, RESEARCH_POLICY.md §4, Phase 1 roadmap components. **Requires code now?** NO (defines Phase 1 work).

No other findings block the freeze.

---

## 3. NON-BLOCKING FINDINGS

**N-1 — Stage cache identity omits platform-code identity** · **Severity:** HIGH · **Resolve by: Phase 2 entry.**
Identity key = input identities + **plugin** id/version/params + code hash + seed (ADR-0007). A fix to core (e.g., window alignment in the frame code) changes results but no plugin hash → stale cache artifacts are reused and, worse, double-run "determinism verification" can degenerate into comparing a cached artifact with itself. Failure scenario: a leakage bug is fixed in core; every cached feature frame silently retains the leak; runs remain "reproducible." Resolution: amend ADR-0007 — V1 identity keys include a platform-code identity (hash over `src/trading_research`, or the commit); accept the cache-invalidation-on-platform-commit cost; refine later only if hit rates hurt. Determinism-verification mode must bypass cache reads. (Amend the text now while flipping statuses; code lands Phase 2.)

**N-2 — Trial-counting granularity: run ≠ hypothesis evaluation** · **Severity:** MEDIUM · **Resolve by: Phase 3 (metadata) / Phase 5 (semantics).**
ADR-0011 counts *runs* reaching valid evaluation. One run can fan out N policies × M OutcomeSpecs × H horizons — that is closer to N×M×H correlated trials than 1. If the Phase 5 deflated metrics divide by runs, the multiple-testing correction is understated by exactly the factor the fan-out feature encourages. Resolution: record per-run evaluation inventory (policy × spec × horizon) from Phase 3; define the denominator semantics (with a correlation caveat) before any deflated statistic ships.

**N-3 — Fingerprint includes the repo commit → identical experiments fingerprint differently across unrelated commits** · **Severity:** LOW · **Resolve by: Phase 3.**
A docs-only commit changes every fingerprint; attempt counting and determinism comparison fragment across commits even when all plugin source hashes are unchanged. This is the *safe* direction (over-segmentation, never collision) — the reverse attack (two materially different experiments, same fingerprint) fails as designed because source hashes and resolved configs are included. Resolution: accept and document, or split "reproducibility fingerprint" (with commit) from a "hypothesis key" (without) for comparison/trial grouping. Interacts with N-2.

**N-4 — OOS access recording arrives two phases after OOS access is possible** · **Severity:** MEDIUM · **Resolve by: Phase 1.**
The audit log and view gates land in Phase 1, but `oos_touched` recording is scheduled with the Phase 3 index. In Phases 1–2 an explicit OOS load would be possible but unrecorded — violating "access recorded" during the window when discipline is newest. Resolution: OOS resolution writes a write-ahead audit event from Phase 1, same log as the lockbox (marking in the index can still arrive in Phase 3).

**N-5 — Concurrency mechanics for SQLite, audit log, and cache writes unspecified** · **Severity:** LOW · **Resolve by: Phase 3.**
Multiple detached processes: SQLite needs WAL + busy_timeout + short transactions (or index writes funneled through the observer CLI); audit JSONL needs single-`write()` O_APPEND lines; cache/artifact writes need temp-file + atomic rename (identical deterministic content makes last-wins safe); attempt_number assignment has a read-modify-write race — derive it at registration under a lock or from the index at query time. All standard; write it down so the implementer doesn't improvise.

**N-6 — Dataset manifests embed the semantic gap vocabulary ADR-0005 bans** · **Severity:** LOW · **Resolve by: Phase 1 (adapter note).**
OBSERVED: the raw manifest ships `total_weekend_gap_count`, `classification: 'normal_boundary'`. These predate the ADR and live in dataset metadata, but they are exactly the labels a GapIndex implementer will be tempted to copy. Resolution: one line in the MT5 adapter docs — manifest gap labels are provenance quotes, never GapIndex facts.

**N-7 — Snapshot-boundary binding must be defined as keys-only, audited tooling access** · **Severity:** LOW · **Resolve by: Phase 1 (already an open decision).**
Binding "last bar ≤ 2026-07-31" requires scanning native keys inside the lockbox period. Legitimate — but the binding procedure should state that view-materialization tooling reads timestamps only, and logs an audit event, so the first sanctioned touch of lockbox-period rows is on the record.

**N-8 — Sparse output/intent frames not explicitly permitted** · **Severity:** LOW · **Resolve by: Phase 3.**
Event-driven research decides sparsely. `NaN = abstain` covers dense frames; state explicitly in ADR-0009 that ModelOutputFrame/IntentFrame indexes may be a strict subset of the eval view's keys, so sparse approaches aren't forced to emit 2.3M abstain rows.

---

## 4. ARCHITECTURAL STRENGTHS (do not "simplify" these away)

1. **The no-UTC native time model (ADR-0002).** OBSERVED: the +10,801s delta is real, recorded, and the DST history genuinely unrecoverable. Refusing to invent UTC is correct and unusual. Any future "convenience" UTC conversion is data falsification.
2. **Manifest-only admission + hash-verified loads (ADR-0004).** Verified working (spot check passed). The excluded 2011–2019 partitions are structurally unreachable — keep it that way.
3. **The AO-1 resolution and default discovery = TRAIN+VALIDATION.** The friction is the protection. (B-3 extends it; nothing should shrink it.)
4. **OutcomeSpec as the single quarantined forward-reader** — right idea; B-1 completes it rather than weakening it.
5. **Two-plane evaluation with the cheap plane-A kill (ADR-0010)** and **cost quarantine (ADR-0020)** — refusing to fabricate spread/commission semantics is the difference between research and fiction. ADR-0020 is the best decision in the set.
6. **Trial counting including bad results, failures recorded forever (ADR-0011)** — the anti-self-deception core. N-2 refines the denominator; the never-delete rule is non-negotiable.
7. **Generic ModelOutput + banned trading vocabulary, policies fan out over persisted inference (ADR-0009)** — already present in the skeleton's test vocabulary; carry it forward.
8. **Files as truth, SQLite rebuildable (ADR-0017)** — correct for one machine; resist any future "the index knows best" drift.
9. **AST-enforced layer boundaries as tests** — OBSERVED working in [test_architecture.py](../../../tests/test_architecture.py). Rules in tests survive; rules in prose don't.
10. **Honest residual-risk statements** (owner bypass of lockbox, no cross-hardware float claims). Keep stating them; deleting an admission does not delete the risk.

---

## 5. RESEARCH-INTEGRITY REVIEW

**Future-leakage protection: STRONG at Tier-1, INCOMPLETE at Tier-2 (B-1, B-2).** View truncation makes cross-zone leakage structurally hard; the sentinel-rig plan (perturb future bars, fail if the past changes) is the right enforcement for what types can't express. The two holes are fit-time labels (undefined channel) and fold-boundary label overlap (no purge requirement). Both are methodology holes, not code bugs, and both are fixed by amendment.

**Split design: SOUND, correctly labeled as policy.** 2020–2023 TRAIN (~1.41M bars spanning COVID, the 2022 rate shock), 2024 VALIDATION (354,856 bars), 2025 OOS (353,647 bars), 2026H1 LOCKBOX (~195k bars) — verified against manifests. One year of validation is thin for regime-sensitive selection, but the correct mitigation is already available in-design: walk-forward selection across TRAIN with VALIDATION as confirmation, not enlargement of VALIDATION at TRAIN's expense. No usable history is wasted — 2011–2019 exclusion is a data-quality fact (42–259 rows/year of daily-resolution data), not a policy choice. **The dates must remain research policy, not architecture — the documents already say this; keep it true in code.**

**OOS vs lockbox: meaningfully different, correctly tiered.** OOS is reusable-with-recording and burns down gradually (RESEARCH_POLICY §2 describes the decay mechanism honestly); the lockbox is one-shot per research line. Recorded OOS access provides *accounting*, not prevention — that is the honest best available; the deflated statistics must actually consume the `oos_touched` counts (Phase 5 requirement).

**Lockbox enforcement: as strong as a single-owner machine permits, once B-3 closes.** Ceremony (flag AND config reason AND write-ahead audit AND permanent taint) makes access impossible to do accidentally or silently through platform paths. The stated residual (owner bespoke-code bypass) is real and correctly stated rather than implied away. Post-opening policy (period spent, fresh lockbox only via time + new snapshot identity) is correct; note honestly that the *researcher* is contaminated after opening regardless — future sessions know the result. That is unfixable and worth a sentence in RESEARCH_POLICY §3.

**Walk-forward: coexists correctly with fixed views** given AO-2, **provided** B-2 (purge) and B-4 (context) are adopted. The fold ⊆ view rule is right; without B-4's context rule it is over-broad.

**Trial counting: right in spirit** (evaluations count, infrastructure failures don't, nothing deleted); N-2's granularity gap must be closed before any deflated statistic is published, or the correction will be cosmetic.

---

## 6. COMPLEXITY REVIEW

| Component | Verdict | Reason |
|---|---|---|
| 20 ADRs | **Keep** | 872 lines total; each is short and each earns its file. Not ceremony. |
| NativeTimeKey + ClockDomain | **Keep** | Removing it = silent UTC lies. Cost is one tag per frame. |
| TimeResolver "seam" | **Keep** | It's a *refusal*, not code. Zero carrying cost. |
| MarketFrame (minimal) | **Keep** | Smallest viable contract ≈ int64 index + named typed arrays + (unit, domain, DatasetRef) + bounded views. ADR-0003's growth tripwire is the right guard. |
| GapIndex | **Keep** | Window gap policy needs it; without it rolling features silently span closures. |
| Annotator (gap labels) | **Defer** | Nothing in Phases 1–3 needs it. Build when a session-shaped feature does. |
| MaterializationPolicy (4 levels) | **Simplify** | Keep the 4 names, but make `cache_if_expensive` decide on plugin hint only in V1 — the measured-wall-time threshold makes cache presence nondeterministic for near-threshold stages, complicating recovery reasoning for marginal I/O savings. |
| Identity key vs content hash | **Keep** (amend per N-1) | Two hashes with two jobs is correct; single-hash alternatives are worse. |
| Fit/FittedState lifecycle | **Keep** (amend per B-1) | The largest leakage surface must be a recorded object. |
| IntentFrame family | **Keep** | The "family" is one id field; single schema ships. Cheap insurance against exposure-forever. |
| PortfolioPolicy inert seam | **Keep** | A named no-op is ~free; extraction later isn't. Borderline — deleting it would also be fine. |
| Experiment state machine + heartbeats | **Keep, minimal form** | Detached processes need INTERRUPTED detection; implement heartbeat as an mtime touch, nothing fancier. |
| SQLite registry (Phase 3) | **Keep** | Comparison queries over hundreds of runs justify it; rebuildable-only keeps it honest. |
| Lockbox ceremony | **Keep** | Research-integrity load-bearing. Not overengineering. |
| Snapshot as gated parent anchor | **Keep** | AO-1 was a real hole; this is its fix. |
| PostgreSQL / queue daemon / DAG engine | **Remove (stay removed)** | Correctly refused. |
| Determinism double-run verification | **Keep** | Cheap, and the only way regressions get caught. Must bypass cache (N-1). |
| Fingerprint/run_id/attempt triple | **Keep** | Each identity does a distinct job; collapsing any two breaks retries or verification. |

Net: this design is *not* overengineered. The genuine fat is limited to the wall-time cache threshold and (arguably) the inert portfolio seam.

---

## 7. CONTRACT STRESS TEST

1. **Rule-based (e.g., breakout threshold rule).** No-op fit → `rule_score` output → threshold DecisionPolicy → TargetExposure. Plane A vs an OutcomeSpec, or stop before evaluation. **No infra change.** Sparse firing wants N-8's clarification.
2. **Similarity-based (the planned plugin).** Fit builds reference library over TRAIN with future-outcome stats — **requires B-1's label channel**; neighbour stats out, policies fan out. Walk-forward variants **require B-2's purge** (reference windows near fold edges). With amendments: **no infra change** — notable, since the review brief asked whether the architecture secretly encodes this plugin; it doesn't, and its needs surface as generic contract gaps, which is the right sign.
3. **Supervised ML (gradient boosting, P(up over H bars)).** Needs B-1 (targets), B-4 (eval-view lookback context), B-2 (fold purge). FittedState pins everything. With amendments: **no infra change.**
4. **Unsupervised regime detection (HMM/clustering).** Fit unsupervised on TRAIN (no labels needed — B-1 channel simply unused); infer regime probabilities/labels; evaluate regime-conditional outcomes in plane A, or store descriptively. One clarification needed: a config that ends *before* any evaluation (descriptive run) should be legal and not counted as a trial — implied by ADR-0011, worth one sentence.
5. **Unusual — intraday seasonality study (pure statistics, no learned state, aggregate deliverable).** Expressible as a no-op-fit model emitting per-bar conditional expectations, with an evaluator aggregating — workable but mildly contorted: the pipeline's per-key frame shape forces time-indexed outputs even when the real deliverable is a table. Acceptable V1 friction; if it recurs, a "summary artifact" output type is a small ADR, not a redesign.
6. **(Bonus) Online/streaming learner.** Does **not** fit — fit-once/infer-many can't express per-bar state updates. ADR-0008 already names this as a future lifecycle extension by ADR. Known, acceptable V1 limit; frequent-refit walk-forward approximates it.

Score: 4/6 clean, 1 contorted-but-possible, 1 known exclusion. The stress test supports the strategy-agnosticism claim **conditional on B-1/B-2/B-4**.

---

## 8. FAILURE-MODE ATTACK — five ways to a convincing but wrong result

1. **Fold-boundary label overlap (B-2).** Silent, systematic, favorable-direction bias on the workhorse workflow; scales with horizon; every walk-forward "discovery" inflated. The most dangerous because it flatters.
2. **Vectorized alignment off-by-one in feature/outcome alignment.** One index shift = 2.3M leaked bars, results wonderful, nothing crashes. Mitigation exists on paper (sentinel rigs, cheater/random canaries) — those rigs are the platform's immune system and must be treated as Phase 2 acceptance, never "test debt."
3. **Stale cache reuse after a core-code change (N-1).** Wrong artifacts wearing a "reproducible" badge; double-run verification can self-confirm the staleness. Silent until someone recomputes from scratch months later.
4. **Validation burn-down with an understated denominator (N-2 + R4).** Hundreds of fanned-out policy variants counted as a handful of "runs" → deflated metrics that barely deflate → the best coin looks skilled. Not silent in any one run — silent in aggregate, which is worse.
5. **Eval-view context inconsistency (B-4).** Features computed over truncated history at each view's left edge behave differently than at fit time; metrics shift subtly; neither favorable nor unfavorable reliably — just quietly wrong, and invisible because NaN-handling hides it.

(Honorable mention: invented cost semantics — already structurally blocked by ADR-0020, which is why it doesn't make the list.)

---

## 9. DOCUMENTATION REVIEW — required changes only

1. **ADR amendments per B-1…B-4, N-1** (texts identified in each finding).
2. **RESEARCH_POLICY.md** — add purge/embargo to §4–5 (B-2); one sentence in §3 on researcher contamination after lockbox opening; §7 note that raw/curated parents are gated (B-3).
3. **V1.md §4** — add raw/curated rows to the view table with "gated" (B-3); state view-composition semantics (B-4).
4. **Declare ADR-0012 the single normative home for split dates**; make V1.md, RESEARCH_POLICY.md, PROJECT_STATE.md, CLAUDE.md reference rather than restate. The dates currently appear in six documents — OBSERVED — and will drift on the first revision.
5. **V1_ROADMAP** — move OOS audit-event recording into Phase 1 (N-4); add fold-purge enforcement to Phase 3 acceptance and fold-boundary leakage rigs to Phase 2/3 acceptance (B-2).
6. **PROJECT_STATE.md / ADR statuses** — flip only after the above; record this review's outcome under `docs/architecture/reviews/`.

CLAUDE.md itself is good: concise, rule-dense, correctly delegating detail. PROJECT_STATE.md is operational, not historical, as required. The freeze proposal's provenance framing (superseded-but-preserved) is exactly right. A fresh AI session can survive this repository — the reading order works; I am the test.

---

## 10. ADR REVIEW

| ADR | Verdict | One-line reasoning |
|---|---|---|
| 0001 | **ACCEPT** | Working as intended — this review consumed it. |
| 0002 | **ACCEPT** | Honest to observed data (+10,801s verified in manifest); refusal-not-resolver is correct V1 scope. |
| 0003 | **ACCEPT** | Right size; the growth tripwire ("stop and re-review") is the best clause in it. |
| 0004 | **ACCEPT** | Verified working (hash spot check); manifest-only admission is load-bearing. |
| 0005 | **ACCEPT** | Correct epistemic humility; N-6 adapter note suffices. |
| 0006 | **ACCEPT** | Closed stage list + windowing-as-spec is the right anti-DAG stance. |
| 0007 | **AMEND** | Add platform-code identity to identity keys; verification bypasses cache; hint-only `cache_if_expensive` in V1 (N-1, §6). |
| 0008 | **AMEND** | Cross-reference the B-1 label channel into FittedState identity; otherwise sound. |
| 0009 | **AMEND** | State sparse-frame legality (N-8); otherwise accept as-is. |
| 0010 | **AMEND** | Scope the reserved-namespace ban to inference/features/policies; define the fit-time target channel (B-1). |
| 0011 | **AMEND** | Record per-run evaluation inventory; define trial-denominator granularity (N-2); document commit-sensitivity of fingerprints (N-3). |
| 0012 | **AMEND** | Require boundary purge (B-2); define context/eval bounds and adjacent-view fit unions (B-4). |
| 0013 | **ACCEPT** | Strongest honest design available on one machine; B-3 lands in 0014. |
| 0014 | **AMEND** | Gate raw/curated parent identities explicitly (B-3). |
| 0015 | **ACCEPT** | Correct scope refusal; seam is near-free. |
| 0016 | **ACCEPT** | Honest determinism claims; add cache-bypass note via the 0007 amendment. |
| 0017 | **ACCEPT** | Right for one machine; N-5 is implementation guidance, not a decision change. |
| 0018 | **ACCEPT** | Verified feasible (Linger=yes); minimal and sufficient. |
| 0019 | **ACCEPT** | Verified accurate: the skeleton is as described, and keep-and-refactor beats a rewrite spiral. |
| 0020 | **ACCEPT** | The most important honesty mechanism in the platform. Do not weaken. |

---

## 11. REQUIRED CHANGES BEFORE FREEZE

1. Amend ADR-0010 (+ ADR-0008 cross-ref): fit-time OutcomeSpec target channel; inference-time ban unchanged; targets pinned in FittedState identity. (B-1)
2. Amend ADR-0012: mandatory label-horizon purge (+ optional embargo) at Tier-2 boundaries, platform-computed, manifest-recorded, override-only-with-record. (B-2)
3. Amend ADR-0014: raw + curated parent datasets gated identically to lockbox; Phase 1 test obligation recorded. (B-3)
4. Amend ADR-0012 / V1.md §4: `(context_start, eval_start, eval_end)` view semantics — backward context into earlier views only; fit views may be ordered unions of adjacent non-gated Tier-1 views, each hash-pinned. (B-4)
5. Amend ADR-0007: platform-code identity in stage identity keys; determinism verification bypasses cache; `cache_if_expensive` by hint only. (N-1)
6. Amend ADR-0011: per-run evaluation inventory recorded from Phase 3; denominator semantics deferred-but-named as a Phase 5 blocking decision. (N-2)
7. Amend ADR-0009: sparse output/intent frames explicitly legal. (N-8)
8. Apply the documentation edits in §9 items 2–5 (RESEARCH_POLICY, V1.md, roadmap OOS-audit move, single normative home for dates).
9. Flip ADR statuses and V1.md to ACCEPTED (as amended); update PROJECT_STATE.md; commit this review's findings under `docs/architecture/reviews/`.

Nothing on this list requires implementation code.

---

## 12. PHASE 1 READINESS

**YES.** With B-3 and B-4 folded in, Phase 1 — Data Layer & Time Model — is well-defined enough to implement without another architecture pass: contracts named (NativeTimeKey, MarketFrame, DatasetRef, view manifest v1.1 fields), acceptance criteria quantified (<2s warm load, <1 GiB RSS, gate-raises-and-audits tests), exclusions explicit, and the three pre-existing open decisions (snapshot binding, integrity default, DST annotation) are correctly scoped as Phase 1 decisions rather than blockers — with N-7's keys-only constraint added to the binding procedure. B-1/B-2 amendments don't change Phase 1 code but must be in the ADRs before Phase 1 starts so the view/GapIndex contracts are built against final semantics.

---

## 13. IMPLEMENTATION GUARDRAILS FOR PHASE 1

1. Never write anything under the dataset root; corrections and new data are new dataset identities, always.
2. Loaders read only manifest-listed files; no directory globbing anywhere, including tests; a hash mismatch is a hard stop, never a warning or a retry.
3. Everything containing lockbox rows is gated: LOCKBOX, SNAPSHOT, **and the raw and curated parents**. Default discovery returns TRAIN + VALIDATION, nothing else. Gate-without-ceremony must raise **and** audit.
4. No UTC, ever: int64 keys + one frame-level `(unit, clock_domain)` tag; the encoded naive datetime is display/annotation only; no `utc` identifier in the data layer; cross-domain comparison raises.
5. A view's upper bound is the PIT boundary; implement the amended `(context_start, eval_start, eval_end)` semantics — context reaches only *earlier*, never-gated data; nothing ever reads past `eval_end` except (later) OutcomeSpec.
6. GapIndex records arithmetic facts only — no "weekend", "session", "holiday", "closure" identifiers in platform code; the manifest's semantic labels are quoted provenance, nothing more.
7. Write-ahead audit before any gated data is returned; audit-write failure = access denied; single-line O_APPEND JSONL + fsync; OOS access writes an audit event from day one.
8. Snapshot boundary binding: timestamps-only scan, procedure and resulting NativeTimeKey recorded in the view manifest, binding itself audited.
9. `tests/test_architecture.py` is load-bearing: if a change breaks it, stop and raise an ADR — never adjust the test. Migrate add-then-migrate with gates green at every step (`./scripts/gate.sh`).
10. No pickle anywhere durable; no bulk data in `/tmp` (RAM-backed); respect the Phase 1 performance budget (< 2 s warm load, < 1 GiB RSS) as acceptance, not aspiration.

---

## 14. FINAL RECOMMENDATION

- **Freeze Architecture V1: YES, after the §11 amendments** — the design is sound, honest, and correctly sized; the blocking findings are contract-text gaps, not structural flaws.
- **ADRs that must change first:** 0007, 0009, 0010, 0011, 0012, 0014 (amendments as specified); the other fourteen accept as written.
- **Roadmap changes:** minor only — OOS audit recording into Phase 1; fold-purge enforcement and fold-boundary leakage rigs into Phase 2/3 acceptance. Phase order and content otherwise stand.
- **Research split policy:** keep TRAIN 2020–2023 / VAL 2024 / OOS 2025 / LOCKBOX 2026H1, and keep it as *policy* (normative home: ADR-0012). No date changes required; prefer walk-forward selection inside TRAIN over any enlargement of VALIDATION.
- **Phase 1 may begin** immediately after the amendments are committed, statuses flipped, and PROJECT_STATE.md updated.

The two most valuable things this review found, in the brief's own terms: a design flaw that would have invalidated months of walk-forward research (B-2, compounded by B-1), and a platform-legal lockbox bypass (B-3). Both cost a paragraph to fix today.

Review complete. No files in the repository were modified; ADR statuses remain PROPOSED pending owner ratification.
