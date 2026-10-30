# ULS + PUSH v7 — first TRAIN-only Plane-A experiment

**Status:** COMPLETE — no consistent positive signed information on TRAIN ·
**Date:** 2026-08-25

## Question

Does the signed ULS/merged-Push setup-completion event carry directional
information about the forward log return over the next 100 observed M1 rows
(20 nominal M5 bars)?

This is a Plane-A forecast test. It contains no simulator, positions, stop,
target, spread, commission, slippage or money result. Plane B remains
conditional on this result.

## Frozen family

- Config: `config/experiments/uls-push-v7-h20m5-train-exploratory-v1.json`.
- Canonical config SHA-256:
  `1c544c4256b6f258e7e8eb3551c012650a6b984f5433fc4fd08cb78110438c05`.
- Dataset: `XAUUSD_S_M1_TRAIN_V1` only, manifest
  `30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e`.
- Signal axis: complete M5 buckets projected to the earliest provable M1 row.
- Forecast: `up_event_signed` through `uls_push_gate_v1.score`.
- Outcome: `forward_log_return_v1`, decision delay 1 observed M1 row,
  horizon 100 observed M1 rows, reference `close`, boundary policy `nan`.
- Evaluators: `sign_accuracy_v1` and `rank_ic_v1`.
- Geometry: context `1577926800`; evaluation begins `1580437860`; five
  contiguous 200,000-row folds after 385,824 fit rows; no embargo override and
  no purge override.
- One vector only. There is no parameter sweep.

The H100M1 outcome is reused from the repository's active H20M5 Plane-A
definition. Missing M1 observations mean that 100 observed rows are not a
guarantee of exactly 100 wall-clock minutes.

## Frozen strategy vector

The feature parameters are exactly `UlsPushV7().params` and are pinned by the
Phase-8 preregistration test. The material defaults are:

- M5; XAUUSD profile resolved explicitly; `minimum_tick = 0.01`;
- hour baseline off and session filter off;
- merged engine: ULS active, Push zones available as merged POIs, standalone
  Push events off;
- structural plus equal liquidity, close-return sweep confirmation, close
  IFVG inversion, trendline-or-BOS entry confirmation;
- current EMA, momentum, sweep-volume, displacement, IFVG-size, score-floor
  and volatility-regime filters off;
- key levels calculated for ranking/score but not required as a hard filter;
- default declared feature reach 5,734 observed M1 rows.

## Provenance limitation and owner authorization

The owner explicitly authorized this first exploratory run on 2026-08-25 after
being told that two formal S-10 provenance inputs were unavailable. The
supplied Pine text remains the semantic source of truth, but its complete body
and SHA-256 are not present in the repository; the visible truncation is in
display-only session-table code. `minimum_tick = 0.01` is therefore frozen for
this exploratory family under that authorization rather than independently
verified against an exact TradingView feed.

Consequently this experiment is not a TradingView reproduction, does not test
the hour-of-day/session "Adaptive" behaviour, and cannot establish Pine parity.
Its result remains a counted research trial and must not be hidden or replaced.

## Decision rule and stopping rule

Report every fold with `effective_samples`. Positive evidence requires a
consistent positive directional pattern across folds; one favourable aggregate
or one favourable fold is insufficient. Rank IC is correlation, not causation.
No significance or independence claim is attached to the two questions.

After execution, stop and record the result. Do not access VALIDATION, OOS or
LOCKBOX. Do not implement or run Plane B unless this Plane-A result supplies a
clear basis and the owner separately authorizes that next step.

## Result

Canonical TRAIN run `01M0X915JS4H9EBDJQ5NFDFGR9`, experiment fingerprint
`d0f68c3d9207b691dd02a7160f87eb22ad85a9fcd3db2c4b8028017740b1f3a5`,
completed from clean commit
`aa447b2adf4cf5a34451fb8c93eebf07b5b0e2d6` in 13.863157588 seconds. It
recorded five folds and ten Plane-A evaluation entries. `oos_touched` and
`lockbox_touched` are both false.

| Fold | Sign accuracy | Effective samples | Rank IC | Effective samples |
|---:|---:|---:|---:|---:|
| 0 | 0.50502512562814073 | 398 | 0.007965153073025538 | 399 |
| 1 | 0.49500000000000000 | 400 | -0.04281825889560151 | 401 |
| 2 | 0.45635910224438903 | 401 | -0.09959199700883546 | 402 |
| 3 | 0.48655256723716384 | 409 | -0.04733415259956744 | 409 |
| 4 | 0.49528301886792453 | 424 | 0.01935919019966331 | 426 |

Across folds there are 2,032 effective sign observations. The descriptive
effective-sample-weighted accuracy is **0.48769685039370081**; four of five
folds are below 0.5. There are 2,037 effective rank observations. The
descriptive effective-sample-weighted Rank IC is
**-0.03197872455901688**, with three negative and two positive folds and a
range of `[-0.09959199700883546, 0.01935919019966331]`. These weighted values
are summaries, not additional preregistered estimators.

The cache-bypassing double verification produced run ids
`01M0X929PXZ1M5A519D198BJH7` and `01M0X929R4W6GZGVYZ1KYS7KAJ`. All 27 compared
feature, outcome, model, policy and metric artifacts were structurally and
exactly equal; maximum observed absolute deviation was `0.0`. The incremental
index rebuild found 1,353 runs and 21 sweeps with zero scan errors.

The since-run Trial Ledger examined the canonical run plus the two verification
attempts, collapsed them to one distinct hypothesis and **two Plane-A trials**
(one per evaluator question), with five folds each. It recorded zero Plane-B
trials, zero noncanonical runs, zero pre-evaluation failures, zero audit
discrepancies, and zero OOS or LOCKBOX access events. The two questions are not
claimed statistically independent; no effective independent-trial estimate,
multiple-testing adjustment or significance claim exists.

## Conclusion and stop

This first Plane-A experiment does **not** show consistent positive directional
information on TRAIN. Accuracy is below 0.5 in four folds, while Rank IC changes
sign and is negative in three folds. Under the frozen stopping rule, the honest
decision is to stop this family. It supplies no basis for Plane B, TP/SL work,
VALIDATION, OOS or LOCKBOX access.

Before the successful run, the first launch command exposed a provider config
round-trip omission for the three derived finite-window fields. Registration
failed before a run id, data access or evaluation existed. Commit `aa447b2`
made those fields reconstructible only at their exact derived values and added
fail-closed regressions; the full gate was green before the recorded run.
