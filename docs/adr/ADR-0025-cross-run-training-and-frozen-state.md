# ADR-0025 — Cross-run training bundles and frozen-state experiment inference

**Status:** ACCEPTED · **Date:** 2026-08-23 · **Owner approval:** ADR-0025 and
OD-7A…OD-7D explicitly approved 2026-08-23 in
[PHASE7_ZONE_TP_SELECTOR.md](../phases/PHASE7_ZONE_TP_SELECTOR.md)

## Context

The owner requested a first machine-learning feasibility study for Zone Trader:
train from prior TP-200/400/800, SL-400 TRAIN runs, freeze the model, and then
compare it on VALIDATION with the three fixed-TP baselines.

The existing contracts cannot express that study honestly:

1. `ResearchModel.fit()` consumes one time-axis `ModelInputs` plus an optional
   `TrainingTargetFrame` aligned to one `ViewSpan`. Multiple Zone configurations
   legitimately produce different event geometries at the same native key, so
   their rows cannot be concatenated into one `FeatureFrame` without violating
   the unique native-key axis.
2. The required targets are matched outcomes already recorded in separate
   Plane-B `TradeLog` artifacts. They are not one `OutcomeSpec` over one market
   frame, and models may not open those artifacts or compute forward outcomes
   themselves.
3. The experiment orchestrator always fits inside each procedural fold of the
   dataset being evaluated. Pointing it at VALIDATION would train on VALIDATION,
   not load the state frozen on TRAIN.
4. The fitted-state cache is disposable. A validation run cannot use a cache
   entry as research truth; it needs an immutable, content-verified reference to
   a completed training product.

Hiding coefficients inside model or policy parameters would conceal which runs
and outcomes produced them and contradict ADR-0008. Hand-encoding the corpus as
a fake `FeatureFrame` would fabricate a time axis. Quietly bypassing the fit
lifecycle is therefore rejected.

## Decision

Add a narrow sibling lifecycle for
**cross-run training bundles** and an evaluation-only experiment mode that
loads one frozen model state. The ordinary fold-fit path and every v1 config
remain byte-for-byte compatible.

### 1. Training bundle: immutable files, not a synthetic dataset

A canonical bundle lives under `<runs_root>/training-bundles/<bundle_id>/`, a
sibling of `runs/` and `cache/`. It is not a `MarketFrame`, dataset, view or new
pipeline stage. It contains versioned, pickle-free artifacts:

- `source-manifest.json`: the exact canonical COMPLETE source run IDs and
  fingerprints, dataset roles/identities, and SHA-256 identities of every
  feature, intent and TradeLog artifact consumed;
- `corpus.npz`: immutable composite sample IDs, named float64 input/target
  columns, decision keys retained only for grouping/purge evidence, weights and
  exclusion-reason counts;
- `fitted-state.npz`: the complete versioned `FittedState` payload, not merely a
  cache reference;
- `training-report.json`: split, purge, solver, diagnostics and deterministic
  content identities; and
- terminal `manifest.json`/`state.json` using an atomic PLANNED → RUNNING →
  COMPLETE/FAILED lifecycle. COMPLETE artifacts are never edited.

The bundle builder scans authoritative run files directly. SQLite may speed a
discovery preview but cannot select sources, establish canonicality, supply an
artifact or affect identity. A canonical bundle requires a clean Git commit,
full source-artifact validation and only TRAIN-role source runs. Any
VALIDATION/OOS/LOCKBOX source is refused before opening an artifact.

The corpus identity pins builder id/version/parameters, ordered source artifact
identities, deduplication and complete-case rules, input/target schemas, sample
count, weighting/split/purge rules, platform/dependency identities and seed.

### 2. One platform target authority for recorded-artifact targets

ADR-0010 remains unchanged for market-forward targets: only `OutcomeSpec` may
read forward market rows. This ADR adds one separate, equally narrow authority
for cross-run training: a versioned **`CorpusTargetSpec`** in the experiments
composition root may read already-completed, content-verified evaluation
artifacts and mint corpus target columns. Models, features, representations and
policies still cannot open run directories, TradeLogs or forward market rows.

The target origin pins the target-spec id/version/parameters and every source
artifact identity into both corpus and fitted-state identities. This is not a
third evaluation plane: it consumes recorded Plane-B evidence solely to create
a fit target, and it produces no evaluation metric.

The initial authorized spec is `matched_bracket_gross_r_v1`: three
actually observed TP arms, fixed SL distance 4.0 price units, target
`direction * (exit - entry) / 4.0`, complete cases only. Missing arms are
excluded, never imputed. Cost fields are not targets.

[ADR-0026](ADR-0026-uls-entry-gating-recorded-trades.md) separately authorizes
`recorded_trade_net_pnl_v1` for one ULS entry-gating study, retaining its
unverified-cost marker and refusing boundary-close or imputed samples.

[ADR-0027](ADR-0027-uls-entry-conditioned-tp-selection.md) separately
authorizes `matched_oof_tp_net_pnl_7arm_v1` for one entry-conditioned ULS TP
study. It requires forward-only TRAIN gate predictions, exact seven-arm
complete cases, SL800, exclusion of boundary closes and permanent unverified
cost semantics.

### 3. Corpus-fittable model contract and fitted-state identity

Add a sibling `CorpusResearchModel` contract rather than weakening
`ResearchModel.fit()`:

- declarations remain the same neutral model id/version/params, required
  inference feature names, generic output columns, cost hint and tolerance;
- `fit_corpus(CorpusModelInputs) -> FittedPayload` receives only an immutable
  platform-issued numeric corpus carrier; and
- `infer(ViewSpan, FittedState, ModelInputs) -> ModelOutputFrame` is the same
  row-local inference boundary used by ordinary models.

The registry resolves ordinary and corpus-fittable models explicitly; a model
cannot ambiguously implement both fit modes under one id/version. Existing
models and protocol signatures do not change.

Inference may consume the canonical `FeatureOrigin` metadata already attached
to each required feature column. This is how one cross-configuration model can
use identity-bearing configuration constants without fabricating dense market
columns. The model must require one exact shared Zone origin across those
columns, and the model-output artifact continues to pin the complete feature
stage identity.

`FittedState` identity gains a discriminated training-source union:

- `view_fit_v1`: the existing training view, OutcomeSpec, feature origins and
  fold isolation, unchanged; or
- `corpus_fit_v1`: corpus identity/content hash, CorpusTargetSpec origin,
  source-manifest identity, split/weight/purge specification and fit plugin
  identity.

There is no state whose identity silently names VALIDATION as its training
view when its coefficients came from TRAIN artifacts.

### 4. Frozen-state experiment mode

Resolved experiment config v2 adds an exact fit-mode discriminator:

- `fold_fit_v1`: current v1 behavior; or
- `frozen_state_v1`: a path-free reference containing `bundle_id`, bundle
  manifest SHA-256, fitted-state identity key and artifact SHA-256.

In frozen mode the orchestrator resolves only a canonical COMPLETE bundle,
verifies every named identity/content hash and exact model declaration, and
**never calls any fit method**. It computes the declared dataset features,
loads the frozen state once, performs row-local inference, then follows the
existing policy/simulation/evaluation stages. The frozen-state reference enters
`experiment_fingerprint`, model-output identity, run manifest and Explorer
projection.

Frozen mode has one declared evaluation span rather than a fake fit fold. A
single position lifecycle therefore spans the configured evaluation range;
there is no hidden per-fold reset. Feature warm-up remains the existing
platform masking rule and may consume only authorized context from the same
integrity view.

All role gates remain unchanged. VALIDATION is default research access; OOS
still requires its audited ceremony; LOCKBOX and sweeps over LOCKBOX remain
prohibited as before. A frozen-state reference never grants data access.

### 5. Determinism, run truth and compatibility

- Bundle construction and fitting have exact double-run verification: corpus
  IDs, arrays, reports, fitted payload and content hashes must match.
- BLAS thread clamps apply to bundle workers exactly as to run workers.
- Bundle files are truth; any future bundle index is disposable.
- Normal experiment runs still live under `runs/`, use the existing state
  machine and Trial Ledger, and remain visible in Explorer. A frozen-state run
  is an ordinary evaluated hypothesis, not a collapsed continuation of its
  training bundle.
- Existing `research-experiment-config-v1`, `FittedState` v1 artifacts and
  experiment fingerprints remain readable and unchanged.

## Alternatives considered

- **Standalone notebook/script and direct VALIDATION calculation.** Fast, but
  it bypasses gating, run identity, Trial Ledger and Explorer. Rejected.
- **Timestamp-only synthetic FeatureFrame.** Multiple valid geometries share a
  timestamp; synthetic keys would also invent a clock. Rejected.
- **Weights embedded in model/policy params.** Hides training provenance and
  cannot prove the coefficients match the claimed corpus. Rejected.
- **Treat every missing arm as a loss or zero.** Missingness is caused by prior
  position state, so this fabricates targets. Rejected.
- **Refit inside VALIDATION folds.** Answers a different, leaked question.
  Rejected.
- **Make the cache authoritative.** Contradicts ADR-0017 and makes research
  state disappear under normal cache cleanup. Rejected.

## Consequences

The platform gains a reproducible path for models trained from multiple prior
experiments without weakening native-time frames or the fit/target boundaries.
The cost is a new public corpus-fit contract, a versioned bundle artifact and a
config/orchestrator mode that require release-blocking identity, leakage,
causality, corruption and compatibility tests.

This ADR authorizes only recorded-artifact training with explicit source and
target specs. It does not authorize online learning, cross-clock joins, mutable
model registries, portfolio training, imputed counterfactual outcomes or access
to protected data.

## When to reconsider

When more than one materially different corpus target family exists and a
registry has a real resolution job · when online/incremental state is required ·
when a promoted model is intentionally refit on an accepted TRAIN+VALIDATION
union under ADR-0012 · when multi-symbol portfolio training becomes real scope.

## Related

[ADR-0006](ADR-0006-fixed-stage-pipeline.md) ·
[ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0017](ADR-0017-storage-files-as-truth.md) ·
[ADR-0018](ADR-0018-execution-model.md) ·
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md).
