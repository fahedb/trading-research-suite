# ULS joint TP/entry selector — single VALIDATION rerun v2

**Status:** COMPLETE — exactly one frozen-model VALIDATION run

**Date:** 2026-08-29

## Protocol

The owner reset the derived run root and rebuilt exactly 1,200 canonical TRAIN
runs: 300 ULS+PUSH configurations paired with TP200/400/600/800, fixed SL800
and `bracket_non_interruptible_v1`. An artifact audit counted 1,174,981 trades
and zero `TARGET_CHANGE` exits. One additional TRAIN-only context carrier
materialized the causal `uls_market_context_v1` inputs required by the model.

The immutable bundle
`uls-joint-action-selector-v1-418c3796be96945f9d41` was built at clean commit
`bcdf8f81f6905673054d19b48be0b453cd08c56c`. Corpus construction and fitted
payload fitting were each repeated and compared exactly. The corpus retained
201,376 complete four-arm rows from 38,872 unique market episodes, excluded
223,939 incomplete occurrences and 419 boundary-forced closes, and selected
deployment strategy ordinal 141 on TRAIN.

The model action is no-entry or one TP level from TP200, TP400, TP600 and TP800;
SL remains 800. TRAIN selected fixed TP400 as the descriptive comparator and
calibration selected cutoff zero. Calibration provisional net was 1,596.57,
but the frozen internal test reversed to -1,276.27. Nothing was retuned after
that warning.

The freeze request
`uls-joint-action-non-interruptible-single-validation-v1` expands to exactly one
child. It uses deployment ordinal 141, `frozen_state_v1`,
`uls_joint_action_forest_v1`, `uls_joint_bracket_exposure_v1`, the exact
VALIDATION identity and non-interruptible execution. It contains no fixed
controls and no additional ULS configurations.

## Result

Canonical VALIDATION run `01M16GM1YAW02T6166NZCCKD8G` completed successfully.
The model evaluated 2,869 ULS opportunities, returned no-entry for 2,613 and
emitted 256 entry/TP intents. The one-position execution constraint opened 204
trades.

| Measure | Result |
|---|---:|
| Gross PnL | 91.96 |
| Provisional cost | 53.04 |
| Provisional net PnL | 38.92 |
| Win rate | 55.392% |
| Maximum drawdown | 115.83 |
| TP exits / SL exits | 113 / 91 |
| TARGET_CHANGE / boundary closes | 0 / 0 |

Model intent choices were TP200 9, TP400 7, TP600 37 and TP800 203. Executed
trade choices were TP200 9, TP400 6, TP600 25 and TP800 164; later intents can
be ignored while a non-interruptible position is already open.

## Interpretation limits

VALIDATION was consumed by earlier development before the owner reset the run
root. Deleting derived artifacts does not restore statistical independence, so
this result is reused exploratory development evidence rather than a fresh
independent validation result. The negative frozen internal TRAIN test is an
explicit instability warning. The result also does not isolate adaptive TP
value from entry filtering, and 80.39% of executed choices were TP800.

Cost semantics and the executable-price model remain unverified. All money
figures are conditional simulator outputs, not real-money profitability
estimates. OOS and LOCKBOX were not accessed.

After a full file-truth rebuild, Explorer contains 1,202 canonical COMPLETE
runs with zero scan errors: 1,200 TRAIN corpus children, one TRAIN context
carrier and exactly one VALIDATION model run.
