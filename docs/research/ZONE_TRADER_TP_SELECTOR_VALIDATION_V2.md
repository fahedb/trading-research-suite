# Zone Trader three-choice TP selector — VALIDATION result v2

Date: 2026-08-23 · Outcome: **IMPROVED VERSUS V1, BUT NO VALIDATION
EVIDENCE OF SUPERIORITY — STOP**

## Frozen study identity

- execution code: `57e04b4765c5bf8b3792cf72328aa86cb1615efa`;
- dependency lock SHA-256:
  `52db27888cdf564215a1244377224838b5f51ae074e7b703d47a9e57f2f55394`;
- TRAIN bundle: `zone-tp-selector-v2-91166a1d33c2585d9a34`;
- corpus identity:
  `91166a1d33c2585d9a34c97bcc9f88b4a9e7ea7b0bfd3c1295a7e72def26675f`;
- fitted-state identity:
  `98661685a5094a6f61809c3ca0622b148fdc92b83eca380fc68c896d7ee0daa1`;
- fitted-state artifact SHA-256:
  `acb5c4663e0f829883e82610a47aba9f71af5f1fdce21cac6863a4e7f8939499`;
- bundle-manifest SHA-256:
  `7afd68482047b3a53a933839eec848237c84f65fb7577843af9bb23826cdd812`;
- VALIDATION freeze-manifest SHA-256:
  `71dc55a97635d4b9e26133a5dc2a960c2f2f810f245a536d69311f61ff4c74fd`.

The nonlinear random-forest selector used the V1 descriptive/configuration
inputs plus 17 point-in-time Zone and market-context inputs. The bundle used
92,330 complete matched TRAIN entries from 100 configurations and 100
content-verified context carriers. It excluded 15,210 incomplete entries:
8,352 with one observed arm and 6,858 with two. The 92,330 rows represent
29,310 unique opening episodes and retain inverse-episode-frequency weighting.
Corpus construction, fit payload and fitted-state encoding each passed exact
double-build determinism checks. A frozen-path TRAIN smoke run completed before
VALIDATION was opened.

## TRAIN qualification

The chronological qualification used 74,260 fit rows, 18,059 holdout rows and
purged 11 boundary-overlapping rows. The three fixed-arm holdout means were
0.006107 gross R for TP200, 0.003568 for TP400 and 0.006823 for TP800. The
selector chose TP200 8,349 times, TP400 3,670 times and TP800 6,040 times, but
its mean realized gross R was **-0.007971**. Its oracle mean was 0.377946 and
mean oracle regret was 0.385917.

This was a negative TRAIN holdout warning, not a tuning or stopping threshold
in the preregistered study. The identical model was therefore refit once on all
complete TRAIN rows, frozen and taken through the one authorized V2 VALIDATION
check without any hyperparameter or feature change.

## Exact VALIDATION family

All four frozen sweeps completed with 100 canonical runs and zero failures.
Full child configurations, configuration order, platform, dependency and run
fingerprints were checked against the freeze before reporting. Trial Ledgers
show no OOS or LOCKBOX access and no scan or audit discrepancy.

| Family | Mean total gross R | Mean trades | Mean gross R/trade* | Zero-trade configs |
|---|---:|---:|---:|---:|
| Dynamic V2 selector | 7.285750 | 355.45 | 0.047677 | 15 |
| Fixed TP200 | 3.972475 | 378.65 | 0.038416 | 15 |
| Fixed TP400 | 5.489700 | 348.52 | 0.081701 | 15 |
| Fixed TP800 | **7.828575** | 326.58 | **0.146179** | 15 |

\* Rate means use the 85 configurations with at least one trade. The same 15
paired configurations had zero trades in every family. Their total gross R is
the mathematical empty sum zero, so they remain in the primary means and
paired comparisons as zero ties. Undefined rates and choice shares remain
`null`.

The dynamic selector's defined-configuration mean choice mix was 50.918%
TP200, 10.049% TP400 and 39.033% TP800.

## Preregistered decision

The best fixed family was TP800.

- Criterion 1 failed: 7.285750 is not greater than 7.828575. The shortfall is
  0.542825 mean total gross R.
- Criterion 2 failed: dynamic beat TP800 in 29 of 100 paired configurations,
  not more than 50. It lost 55 and tied 16; the median paired difference was
  -0.98 gross R.
- Primary success is therefore **false**.

V2 nevertheless improved its dynamic mean by 1.215025 gross R relative to V1
(7.285750 versus 6.070725) and increased paired wins against TP800 from 19 to
29. This is useful diagnostic progress, but it does not satisfy either frozen
success criterion and is not evidence that per-trade learned TP choice is
better than fixed TP800.

The exact result artifact is
`/path/to/research-runs/phase7-zone-tp-selector-validation-report-v2.json`,
SHA-256
`03347528a7f0d046bb0dfd5d427f22cf4542a8822662058733dd297602815c07`.
The rebuilt Explorer index contains 1,350 runs, 21 sweeps and zero scan errors.

This same VALIDATION view had already been exposed by V1, so V2 is an
iterative development check rather than fresh independent evidence. Per the V2
stopping rule, there is no further tuning on VALIDATION and no OOS or LOCKBOX
access. Cost/money semantics remain unverified and are not used in the
conclusion.
