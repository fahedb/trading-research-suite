# ULS joint entry/TP selector v1

**Status:** COMPLETE — TRAIN bundle and reused VALIDATION comparison recorded

**Date:** 2026-08-26

## Objective

Train one model from the completed 300 × 4 TRAIN sweep to choose among no trade,
TP200, TP400, TP600 and TP800 while SL stays fixed at 800. Then compare it on
reused VALIDATION against all four fixed-TP baselines using the same ULS
configuration selected on TRAIN.

## Frozen protocol

The source is the 1,200-child sweep
`uls-push-v7-train-joint-model-300x4-tp-v1`. An observation must be a matched
executed opening event present in all four TP arms of one ULS configuration.
Boundary-forced closes and incomplete matches are excluded; missing arms are not
imputed. Repeated market episodes across configurations receive inverse-frequency
weights.

The input schema contains only event-time ULS fields, causal market context and
the complete ULS configuration. The four labels are recorded provisional net
PnL for TP200/400/600/800 at SL800. One deterministic multi-output random forest
uses 96 trees, depth 8, minimum leaf 128, `max_features=0.75`, no bootstrap and
seed `20260826`.

Unique event times are split chronologically 70% fit, 15% cutoff calibration and
15% internal test, with exit-boundary purging. The deployable ULS configuration
and fixed-TP comparator are chosen on fit only. The abstention cutoff is chosen
on calibration only and cannot be negative. The internal test is read once.
If every non-negative cutoff misses the calibration minimum, the frozen
fallback is cutoff zero. It may retain a sparse set of positive predictions,
but this does not waive the later minimum-trade success rule.

VALIDATION is frozen to five children: one dynamic model and fixed TP200, 400,
600 and 800 controls. Primary success requires the dynamic result to beat both
zero and the TRAIN-selected fixed TP in net PnL with at least 30 trades.

## Interpretation limits

VALIDATION was used in previous development, so the coming comparison is not an
independent validation. Costs remain unverified and all performance values are
conditional simulator evidence. OOS and LOCKBOX are untouched and unauthorized.

## TRAIN result

The immutable bundle is
`uls-joint-action-selector-v1-48ad66fa819b0a731a11`, built from clean commit
`ccd87481e85ace533ea83e063c80f8c69b173a57`. Both corpus builds and both fitted
payloads were exactly equal.

The paired corpus retained 508,216 complete configuration-event rows covering
68,302 unique market episodes. It excluded 138,501 incomplete episode
occurrences and 485 boundary-forced closes. The chronological fit contained
355,587 rows. TRAIN fit selected ULS configuration ordinal 212 and fixed TP800
as its deployment comparator.

No non-negative cutoff reached the 30-trade calibration minimum, so the frozen
zero cutoff was used. On the deployment configuration:

- calibration selected 3/40 trades, all TP800, for net PnL 23.22;
- the untouched internal test selected 4/37 trades, all TP800, for net PnL
  -30.43;
- internal fixed TP800 was -32.22, but no-trade was 0 and fixed TP400 was 5.44.

The internal result therefore did not demonstrate stable entry selection or
meaningful adaptive TP choice.

## Reused VALIDATION result

Sweep `uls-joint-action-validation-v1` completed 5/5 canonical children with no
failure, retry, OOS access or LOCKBOX access. The primary success rule failed.

| Policy | Trades | Net PnL | Win rate | Max drawdown |
|---|---:|---:|---:|---:|
| joint dynamic | 4 | -19.04 | 25.00% | 20.98 |
| fixed TP200 | 78 | -28.38 | 75.64% | 47.20 |
| fixed TP400 | 78 | -47.76 | 60.26% | 103.27 |
| fixed TP600 | 77 | -44.28 | 51.95% | 106.41 |
| fixed TP800 (TRAIN comparator) | 76 | -34.74 | 46.05% | 111.45 |

The dynamic model chose TP600 twice and TP800 twice. One TP600 trade won 5.74
net; the other three trades stopped out at -8.26 each. It beat the TRAIN-selected
fixed TP800 by 15.70 and the best descriptive fixed arm, TP200, by 9.34, mainly
by avoiding 72–74 trades. It still lost 19.04 versus no-trade, produced only four
trades versus the required 30, and showed no TP200 or TP400 choices. This is not
evidence that the joint model improved the strategy.

The Explorer index was fully rebuilt at generation 1 with 1,206 runs and zero
scan errors. All five VALIDATION children are visible under model
`uls_joint_action_forest_v1`; the dynamic run id is
`01M10A6Q2A02GQVHE26XYSVNYY`. The Trial Ledger records five related hypotheses,
45 Plane-B trials and zero protected-data events.
