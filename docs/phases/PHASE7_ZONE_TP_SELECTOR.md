# Phase 7 — Zone Trader three-choice TP selector

Date: 2026-08-23 · Status: **COMPLETE; CP-25 INDEPENDENT REVIEW APPROVE; CP-26
TRAIN FREEZE COMPLETE; EXACTLY 400 VALIDATION RUNS COMPLETE; PRIMARY RULE FAILED;
NO VALIDATION EVIDENCE OF IMPROVEMENT; STOPPED WITHOUT OOS/LOCKBOX** · Accepted
architecture:
[ADR-0025](../adr/ADR-0025-cross-run-training-and-frozen-state.md) · TRAIN
inventory:
[ZONE_TRADER_TP_SELECTOR_TRAIN_INVENTORY_V1.md](../research/ZONE_TRADER_TP_SELECTOR_TRAIN_INVENTORY_V1.md) · Result:
[ZONE_TRADER_TP_SELECTOR_VALIDATION_V1.md](../research/ZONE_TRADER_TP_SELECTOR_VALIDATION_V1.md)

## 1. Objective

Run one bounded feasibility study: from prior Zone Trader TRAIN trades, learn a
row-local choice among TP 200, TP 400 and TP 800 while SL remains fixed at 400;
freeze the model on TRAIN; then compare it once on VALIDATION with the three
fixed-TP baselines over the same 100 feature configurations.

This phase does not optimize Zone Trader feature parameters, stop distance,
position size, execution timing or costs. It does not touch OOS or LOCKBOX.

## 2. Decisions requiring owner approval

### OD-7A — Complete observed arms only — OWNER APPROVED 2026-08-23

Use the 92,330 TRAIN samples for which the same feature configuration,
entry and direction were actually traded under all three TP values. Collapse
attempts and sweep reuse. Exclude 15,210 incomplete entries and the 35
TP-800-only neighbourhood configurations; never impute a missing arm.

Reason: prior TP changes position occupancy, so an absent later trade has no
honest observed outcome. This is a conditional matched-entry pilot, not a
counterfactual replay engine.

### OD-7B — Fixed multi-output ridge pilot — OWNER APPROVED 2026-08-23

Use one deterministic standardized multi-output ridge model with `lambda=1.0`
and no hyperparameter sweep. It emits generic `score` as the signed Zone-event
passthrough plus three predictions (`value_0`, `value_1`, `value_2`)
representing expected gross R for TP 200/400/800. Trading vocabulary remains in
the policy, not the model output.

Inputs are row-local and available at decision time:

- signed event;
- descriptive risk distance, reward distance, zone width and signed
  entry-to-zone-midpoint distance, normalized by entry price;
- one-hot zone kind; and
- every canonical Zone Trader configuration parameter, with constant columns
  retained in schema and mapped to zero after standardization.

Configuration parameters are not duplicated into market feature columns. At
inference the model validates the existing `FeatureOrigin` shared by its Zone
input columns, reads that origin's canonical parameter metadata and broadcasts
the constants across the rows in that run. The model-output artifact already
pins the feature-stage identity, so this metadata cannot vary invisibly.

Native key is used only for chronological grouping and never as a predictor.
Rows sharing `(opening_intent_key, direction)` across configurations receive
inverse-frequency weights so the episode's total weight is one. Ridge
intercept is unpenalized; zero-variance inputs use scale one. There is no random
behavior.

Before the final fit, perform one chronological TRAIN qualification: first 80%
of unique decision keys for fit, final 20% for evaluation, with any training
sample whose maximum three-arm exit key reaches the holdout boundary purged.
Report per-arm prediction error, chosen-arm gross R, regret to the observed
three-arm oracle, choice distribution and fixed-arm comparisons. This report
does not tune the model. Then refit the identical frozen specification on all
92,330 rows.

For every valid sparse Zone event the policy uses `score` for the exposure sign
and always chooses one TP; exact prediction ties select the first arm in the
frozen order 200 → 400 → 800. It emits that TP distance and SL distance 4.0
price units through `bracketed_exposure_intent_v1`. Non-event rows remain
absent, and a malformed/non-finite event is refused rather than traded.

### OD-7C — Accept ADR-0025's bundle/frozen-state architecture — OWNER APPROVED 2026-08-23

Accept the immutable cross-run training bundle, `CorpusTargetSpec`, sibling
`CorpusResearchModel` lifecycle and evaluation-only frozen-state experiment
mode exactly as proposed in ADR-0025. Existing experiment configs and fitted
states remain unchanged.

Reason: the current single-view `FeatureFrame` cannot contain multiple Zone
geometries at one native key, and the current orchestrator would otherwise fit
on VALIDATION. A standalone bypass would be faster but would not be an auditable
platform experiment.

### OD-7D — One frozen 400-run VALIDATION family and success rule — OWNER APPROVED 2026-08-23

After CP-26 freezes the bundle, model, 100 configurations, configs and source
commit, execute exactly:

- 100 dynamic TP-selector runs;
- 100 fixed TP-200/SL-400 runs;
- 100 fixed TP-400/SL-400 runs; and
- 100 fixed TP-800/SL-400 runs.

All runs use the same VALIDATION view bounds, feature warm-up rule, execution
model, fixed lot and open unverified-cost binding. No configuration is selected
or discarded after viewing VALIDATION.

For each run define total gross R as the sum over trades of
`direction * (exit - entry) / 4.0`. The **primary feasibility success rule** is
both:

1. the dynamic family's mean total gross R across 100 configurations exceeds
   the largest mean among the three fixed families; and
2. the dynamic policy beats that single best fixed family on total gross R in
   more than 50 of the 100 paired configurations.

Secondary diagnostics: median paired difference, gross R per trade, trade
count, choice mix, gross-R drawdown, ambiguity/gap rates and the complete Trial
Ledger. No p-value, adjusted statistic or independence claim is produced.
Money/net-cost fields may be shown only under their permanent `unverified`
label and are not part of the success rule.

If the primary rule fails, record **no validation evidence of improvement** and
stop; do not tune on VALIDATION and do not access OOS. If it passes, record that
the pilot merits a separately approved next study; it is not promotion or
lockbox evidence.

## 3. Ordered implementation slices

| Slice | Scope | Exit condition |
|---|---|---|
| S-1 | Accept ADR-0025; add immutable corpus/bundle contracts and identity | Exact schema/identity/corruption tests; old artifacts unchanged |
| S-2 | File-truth Zone matched-arm corpus builder and `matched_bracket_gross_r_v1` | Inventory reproduces 100 configs, 92,330 complete rows and every exclusion count without SQLite authority |
| S-3 | `CorpusResearchModel`, deterministic ridge trainer and complete frozen-state publication | Literal solver fixtures, double-run equality, source/target provenance pinned |
| S-4 | Config v2 frozen-state reference and evaluation-only orchestration | Fit method provably never called; bad/mutable/cache-only state refused; v1 fingerprints unchanged |
| S-5 | TP-selector model inference, policy and gross-R evaluator/reporting | Row-locality, decision-prefix causality, tie-break, bracket and hand-computed trade fixtures pass |
| S-6 | TRAIN qualification and full-TRAIN final fit | CP-26 evidence recorded; exact bundle/state/config identities frozen at a clean commit |
| S-7 | VALIDATION execution | Exactly 400 declared children complete or failures remain recorded; no OOS/LOCKBOX |
| S-8 | Results, Explorer and Trial Ledger | Primary/secondary outcomes reported without tuning or verified-money claims |

## 4. Checkpoints

### CP-25 — Architecture and implementation

ADR-0025 owner-accepted; S-1…S-5 complete; release-blocking architecture,
leakage, causality, corruption, compatibility and determinism rigs green;
unfiltered gate green after bounded independent review.

### CP-26 — TRAIN freeze before VALIDATION

S-6 complete on TRAIN only. Freeze and record:

- the 100 feature-origin digests and every source artifact identity;
- the 92,330-row corpus identity and exclusions;
- input/target column order, weights, split/purge, lambda and tie-break;
- fitted-state identity/content hash;
- four VALIDATION sweep definitions and the success rule; and
- clean Git/dependency identities.

After this checkpoint no code, weight, feature, target, policy, baseline,
configuration, metric or success-rule change is allowed before VALIDATION.

### CP-25 implementation evidence — 2026-08-23

The implementation candidate now contains:

- a sibling `CorpusResearchModel` contract and registry, immutable
  `CorpusModelInputs`, corpus-specific fitted-state identity and corruption
  refusal without changing the existing view-fit identity format;
- a file-truth builder which re-reads and hashes 300 canonical TRAIN source
  runs, compares each three-arm feature axis and value array exactly, and
  reproduces 100 unique feature origins, 92,330 complete rows, 15,210 excluded
  rows (8,352 one-arm and 6,858 two-arm), 29,310 unique episodes and total
  inverse-frequency weight 29,310;
- deterministic standardized multi-output ridge, exact first-arm tie-break,
  row-local inference, adaptive TP policy with SL 4.0 and the literal gross-R
  evaluator;
- strict config v2 and a separate evaluation-only path whose frozen-fold body
  calls state load plus inference/simulation and contains no fit, representation
  fit or training-target call; and
- an immutable Phase-7 operations composer which produces four hash-verified
  zip sweeps of exactly 100 paired configurations each. It freezes the observed
  28,714-row warm-up, VALIDATION identity/bounds, execution/cost quarantine,
  bundle reference and accepted success rule. One-byte request corruption is
  refused.

The unfiltered `./scripts/gate.sh` is GREEN (pytest, Ruff and mypy). The bounded
independent review chain found and closed source-truth, full-pairing,
leakage-rig, BLAS-clamp, bundle-lifecycle, frozen-platform and registration-race
defects. Its final verdict is **APPROVE** at exact clean
`61c39acf4a1543288084dde9cf311cc7e6626082`. No VALIDATION, OOS or LOCKBOX row
was opened while producing CP-25 evidence.

### CP-26 TRAIN freeze evidence — 2026-08-23

The immutable bundle is `zone-tp-selector-v1-8a2d7dffcbad64d7e472`; its corpus
identity is `8a2d7dff…453d0d5a`, fitted-state identity is
`4c12e9e9…5a788358`, fitted artifact SHA-256 is `9f3b7e50…9bd36eb6`, and
bundle-manifest SHA-256 is `a03cfc51…09fb33e14`. It is bound to clean execution
commit `61c39ac` and dependency SHA-256 `7be98b7a…2174c6b`.

The builder reproduced 92,330 complete rows and 15,210 exclusions, passed all
three double-build/double-fit checks and published the exact lifecycle
PLANNED → RUNNING → COMPLETE. The chronological qualification used 74,260 fit
rows, 18,059 holdout rows and 11 purged rows. A frozen-state TRAIN smoke run
completed. The four 100-child requests were frozen outside the repository at
freeze-manifest SHA-256 `7f4e22ef…be1507e8`, then every planned child was
full-config and platform/dependency matched before VALIDATION access.

### CP-27 — VALIDATION result

**COMPLETE — PRIMARY FAILED.** Exactly 400/400 frozen children completed with
zero failures. Mean total gross R was dynamic 6.070725, fixed TP200 3.972475,
fixed TP400 5.489700 and fixed TP800 7.828575. TP800 was the best fixed family;
dynamic recorded 19 paired wins, 45 losses and 36 ties, so both preregistered
criteria failed. The same 15 paired configurations had no trades in any family
and were retained as zero-total ties; undefined rates remain `null`.

The initial report correctly refused the evaluator's zero-trade NaNs. A
report-only correction at `634c1f5` implemented empty additive sums as zero,
left undefined rates null, added denominator counts and changed no run, stored
metric artifact or success rule. Independent follow-up verdict: APPROVE. Exact
result and hashes are recorded in
[ZONE_TRADER_TP_SELECTOR_VALIDATION_V1.md](../research/ZONE_TRADER_TP_SELECTOR_VALIDATION_V1.md).
Explorer was rebuilt with 749 runs, 13 sweeps and zero scan errors.

## 5. Exact next action

Stop this study. Record **no VALIDATION evidence of improvement**; do not tune
the selector on VALIDATION and do not access OOS or LOCKBOX. Any different
model, target, feature set or decision mechanism requires a new owner-approved
study with a new TRAIN-only design and fresh preregistration.
