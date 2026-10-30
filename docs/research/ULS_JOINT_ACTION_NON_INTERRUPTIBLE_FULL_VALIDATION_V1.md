# ULS joint entry/TP selector — non-interruptible full validation v1

**Status:** COMPLETE — frozen primary criterion passed on reused VALIDATION

**Date:** 2026-08-27

## Research question

Can one frozen model use causal event-time ULS and market-context inputs to
choose `no entry`, TP200, TP400, TP600 or TP800, with SL800 fixed, more
effectively than fixed-TP policies when every opened position remains active
until TP, SL or the mandatory fold boundary?

The source is the completed 1,200-child TRAIN sweep
`uls-push-v7-train-non-interruptible-300x4-tp-v1`. The builder refuses any
source child whose execution identity is not `bracket_non_interruptible_v1`.
No source from the purged target-change corpus is admissible.

## Frozen TRAIN protocol

An observation is an executed opening event present in all four TP arms of one
ULS configuration. Boundary-forced closes and incomplete four-arm matches are
excluded and never imputed. Repeated market episodes across configurations use
inverse-frequency fitting weights. This complete-case rule is necessary for
observed four-action labels, but it conditions the corpus on all four policies
being flat at the same event and is therefore an explicit selection limitation.

Inputs are event-time ULS fields, causal market context and the complete ULS
configuration. Targets are recorded provisional net PnL under TP200/400/600/800
and SL800. The deterministic multi-output random forest has 96 trees, depth 8,
minimum leaf 128, `max_features=0.75`, no bootstrap and seed `20260826`.

Unique decision times are split chronologically 70% fit, 15% calibration and
15% internal test with exit-boundary purging. The fixed-TP comparator is chosen
on aggregate fit results across all 300 configurations. A single non-negative
entry cutoff is selected on aggregate calibration results across all
configurations; the internal test is read once. TP ties choose the lower TP.

## Frozen VALIDATION protocol

The validation sweep has 1,500 unique children grouped by the 300 ULS
configurations:

- one dynamic model child per configuration, where the model chooses both
  entry and TP;
- four fixed controls per configuration at TP200/400/600/800.

The original 1,200 combinations are therefore all retained as controls, and
300 dynamic children are added. Running 1,200 dynamic children by repeating
each ULS vector four times would produce four behaviorally identical runs per
vector because the per-trade TP chosen by the model overrides the fixed
fallback TP; that duplicate design is prohibited here.

The preregistered unit is one ULS configuration. Primary success requires mean
dynamic provisional net PnL across 300 configurations to exceed both zero and
the mean TRAIN-selected fixed-TP comparator, while median dynamic trade count
is at least 30. Paired wins/losses/ties against every fixed TP, medians,
drawdown and trade counts are descriptive secondary results. No tuning is
allowed after VALIDATION.

## Interpretation limits

VALIDATION has already been consumed in earlier development, so this is reused
exploratory evidence rather than an independent validation. The 300
configurations are related hypotheses, not 300 independent replications.
Costs and executable-price semantics remain unverified, so money figures are
conditional simulator outputs and not profitability estimates. OOS and
LOCKBOX remain untouched and unauthorized.

## TRAIN result

The immutable bundle is
`uls-joint-action-selector-v1-768b3bc0b51564c84111`, built at clean commit
`9164f28f0c14db3ece806b1f9a9926735c82c637`. Both corpus builds and both fitted
payloads were exactly equal. The corpus retained 201,376 complete rows from
38,872 unique market episodes, excluded 223,939 incomplete occurrences and
419 boundary-forced closes, and bound all 1,200 non-interruptible source runs.

TRAIN fit chose TP400 as the aggregate fixed comparator and calibration chose
cutoff zero. Calibration selected 4,450/31,261 rows for provisional net
1,596.57, but the untouched internal test selected 3,747/28,525 for -1,276.27.
The sign reversal is explicit instability evidence; no parameter was changed
before VALIDATION.

## Reused VALIDATION result

Sweep `uls-joint-action-non-interruptible-full-validation-v1` completed all
1,500 canonical children in 1,472.774 seconds with zero failures,
interruptions, pending children, retries, duplicates or protected-data access.
The frozen primary criterion passed.

| Policy | Mean trades | Median trades | Mean net | Median net | Net-positive configs |
|---|---:|---:|---:|---:|---:|
| joint dynamic | 60.70 | 45.0 | 28.13 | 15.68 | 192/300 |
| fixed TP200 | 525.65 | 338.0 | -138.69 | -65.61 | 49/300 |
| fixed TP400 | 418.44 | 305.0 | -79.06 | -42.91 | 70/300 |
| fixed TP600 | 361.03 | 284.5 | -67.99 | -38.09 | 88/300 |
| fixed TP800 | 324.46 | 274.0 | -21.14 | -7.19 | 121/300 |

Against fixed TP200/400/600/800 respectively, the dynamic model recorded
245/231/202/178 paired wins, 32/46/75/99 losses and 23 ties in each comparison.
Its mean net advantage was 166.81, 107.18, 96.12 and 49.26 respectively.

Across the 300 dynamic runs, 18,211 executed trades used TP200 537 times
(2.95%), TP400 475 times (2.61%), TP600 2,706 times (14.86%) and TP800 14,493
times (79.58%). The full 1,500-run artifact audit counted 507,085 trades:
332,888 TP exits, 173,848 SL exits, 349 fold-boundary exits and exactly zero
target-change exits.

The result artifact is
`reports/uls-joint-action-non-interruptible-full-validation-v1.json`. A
report-only correction represents zero-trade additive metrics as zero and
undefined rates as null; a run with trades and an undefined required additive
metric still fails closed. No experiment result or frozen criterion changed.

## Research interpretation

The result supports an entry-filtering effect under the provisional simulator:
the dynamic policy traded far less, had positive mean and median conditional
net results, and beat every raw fixed-TP arm in most paired configurations.
It does not yet isolate value from adaptive TP selection. Almost 80% of its
executed choices were TP800, and the design lacks a model-gated fixed-TP800
control using exactly the same accepted events. The model is therefore best
described as a sparse entry gate with a strong TP800 preference, not as proven
evidence of broadly adaptive TP choice.

The cost dependence is material. Dynamic mean gross was 43.91 versus fixed
TP800 mean gross 63.23, while dynamic mean provisional cost was 15.78 versus
84.36. Its net advantage over TP800 is therefore driven by avoiding many
trades and their provisional costs, not by higher gross PnL. Because the cost
binding and executable-price path remain unverified, this is not a real-money
profitability conclusion.

The Trial Ledger records 1,500 related hypotheses and 13,500 Plane-B trials,
with no multiple-testing adjustment and no OOS/LOCKBOX events. After a full
file-truth rebuild, Explorer generation 1 contains 2,706 runs with zero scan
errors; its latest-2,000 view contains all 1,500 new children grouped as 300
dynamic and 1,200 fixed controls.

To keep the filesystem below its 90% refusal threshold, 300 old TRAIN feature
cache entries (72 GiB) and the recomputable VALIDATION feature-cache entries
were evicted only after they were no longer in use. No run, bundle, manifest or
research evidence was removed. Final free space is approximately 28 GiB.
