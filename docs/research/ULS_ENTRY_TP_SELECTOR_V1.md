# ULS entry-gated seven-choice TP selector v1

**Status:** COMPLETE — TRAIN and reused-VALIDATION checks both found no
improvement; primary criterion failed; stopped without OOS or LOCKBOX access.

**Date:** 2026-08-26

## Question

Behind the exact frozen ULS entry gate, can a model choose TP200–800 per trade
better than one TRAIN-selected fixed TP on the existing VALIDATION period? SL
stays fixed at 800.

## Frozen TRAIN protocol

- Upstream gate: `uls-entry-selector-v1-cdb9bd758cdd9d3c48df`, including cutoff
  `-0.5440342564262345`.
- Sources: two chronological, expanding, forward-only entry-gate folds and
  seven TP arms per fold; exactly 14 canonical TRAIN runs.
- Fold 0: fit `<35%`, calibrate `35–45%`, evaluate `45–70%`.
- Fold 1: fit `<60%`, calibrate `60–70%`, evaluate `70–100%`.
- Arms: TP 200, 300, 400, 500, 600, 700, 800; SL 800 throughout.
- Sample identity: feature origin + intent key + direction. Only complete
  seven-arm episodes survive. Duplicate identities, missing arms and every
  boundary-forced close are excluded.
- Targets: the seven recorded provisional net-PnL outcomes. Costs remain
  unverified and no counterfactual is imputed.
- Selector split: chronological 85/15 over unique decision keys with purge by
  the maximum of all seven exit keys.
- Forest: 96 trees, depth 6, minimum leaf 12, `max_features=0.75`, no bootstrap,
  seed `20260826`, one thread. Greatest predicted value wins; ties choose the
  lower TP.
- The combined deployment state must reproduce the existing frozen entry
  forest exactly before it is published.

The immutable source sweep id is `uls-entry-tp-oof-train-v1`. The final bundle
will live under `<runs_root>/training-bundles/uls-entry-tp-selector-v1-*`.

## Frozen reused-VALIDATION protocol

After the TRAIN bundle exists, freeze sweep `uls-entry-tp-validation-v1` with
exactly eight children:

| Child | Entry rule | TP rule | SL |
|---|---|---:|---:|
| `model_dynamic_tp` | frozen entry gate | per-trade argmax over 200–800 | 800 |
| `fixed_tp_200` … `fixed_tp_800` | same frozen entry gate | corresponding fixed TP | 800 |

All children use `XAUUSD_S_M1_VALIDATION_V1`, bounds
`1704157200 / 1706658840 / 1735689360`, identical features, frozen model and
unverified execution/cost binding. The primary baseline is whichever fixed TP
has the greatest total provisional net PnL on the selector's untouched internal
TRAIN test. Primary success requires dynamic provisional net PnL greater than
that baseline and at least 30 dynamic trades.

Report trade count, gross PnL, provisional cost, provisional net PnL, win rate
and maximum drawdown for all eight children and index them into Explorer.

## Mandatory interpretation limits

The same VALIDATION period was already exposed by the entry-gating study. This
check is therefore reused development evidence, not independent validation.
Complete-case matching can select the target population, the execution/cost
binding remains unverified, and no result is a real-money profitability claim.
No post-read tuning is authorized. OOS and LOCKBOX are outside this study.

## Frozen result

Two deterministic OOF entry bundles were built from TRAIN at commit
`37f5e075425bb6b0b8da89f7adaef1c0fba3f080`, followed by 14/14 canonical
complete TP-arm runs with zero failures. The matched corpus contained 730
complete episodes after excluding 185 incomplete episodes and two
boundary-forced closes. The selector used 619 fit rows and 110 untouched
internal-test rows.

The final combined bundle is
`uls-entry-tp-selector-v1-3bf400bab9d5e85e484d`, built at clean commit
`ac8160d422981c2d112c35265e5d9f680f84d2e2`. Its fitted-state identity is
`b9b192e118fa0f86bca2dfaaa1b9544a4acef3c2a77ffc98c5894a8d0d06c88e`.
Double corpus construction and double model fitting were exact, and the entry
sub-state matched the previously frozen gate exactly.

On the internal TRAIN test, the dynamic selector recorded provisional net PnL
92.20 versus 114.69 for the best fixed arm, TP600, a difference of -22.49. The
frozen rule nevertheless required the requested reused-VALIDATION check without
retuning.

All eight canonical VALIDATION children completed:

| Arm | Run | Trades | Gross PnL | Provisional cost | Provisional net PnL | Win rate | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|
| Dynamic TP | `01M101X3522RCV62854XJ9CBAK` | 481 | 102.84 | 125.06 | **-22.22** | 60.0832% | 89.27 |
| Fixed TP200 | `01M101X36BQ3V9N4514YFANP4T` | 525 | 140.61 | 136.50 | 4.11 | 79.4286% | 115.44 |
| Fixed TP300 | `01M101X37HWBYC9PZP3MEH5JQ9` | 510 | 123.04 | 132.60 | -9.56 | 70.7843% | 111.00 |
| Fixed TP400 | `01M101X38SZ8C5Q97PE5BA39XC` | 498 | 154.22 | 129.48 | 24.74 | 64.8594% | 91.54 |
| Fixed TP500 | `01M101X3A2FD5W0ECM6NP82PHR` | 485 | 176.27 | 126.10 | 50.17 | 59.3814% | 91.76 |
| Fixed TP600 | `01M101X3B99G7G3RVM1A303MZE` | 467 | 161.24 | 121.42 | **39.82** | 55.0321% | 116.39 |
| Fixed TP700 | `01M101X3CP75GVC281AS2Y8AC2` | 462 | 192.36 | 120.12 | **72.24** | 51.7316% | 100.99 |
| Fixed TP800 | `01M101X3E3JK9N8V64X63TA0ZS` | 455 | 165.77 | 118.30 | 47.47 | 48.1319% | 109.47 |

Against the preregistered TRAIN-selected TP600 baseline, the dynamic model had
14 more trades, 58.40 less gross PnL, 3.64 more provisional cost and 62.04 less
provisional net PnL. It exceeded the 30-trade minimum but failed the primary
improvement rule. TP700 was the best fixed arm on this reused VALIDATION read,
but that observation is descriptive and is not permission to retune or promote
TP700.

The freeze-manifest SHA-256 is
`4a00486ef44586c6f76b2022eea9d493f12411ace1799f78070b5f5e1c7cc105`;
the frozen request SHA-256 is
`808944e4595cb32219b8dedb2c1c6b3c9968d142263f970cf4fe83241e28701e`;
and the immutable result-report SHA-256 is
`ac55c19355aa8c8d48260045ca56397617fb6bcc83e3585b6089b05b1a201029`.
Explorer index generation 4 contains 831 runs with zero scan errors, including
all eight VALIDATION children. This remains reused development evidence under
unverified cost semantics, not a profitability claim.
