# Zone Trader three-choice TP selector — VALIDATION result v1

Date: 2026-08-23 · Outcome: **NO VALIDATION EVIDENCE OF IMPROVEMENT — STOP**

## Frozen study identity

- execution code: `61c39acf4a1543288084dde9cf311cc7e6626082`;
- dependency lock SHA-256:
  `7be98b7ae16987591810f219c82a3075ea23cded7f97c505b14f0fe2f2174c6b`;
- TRAIN bundle: `zone-tp-selector-v1-8a2d7dffcbad64d7e472`;
- corpus identity:
  `8a2d7dffcbad64d7e472b8f483a31b78f2e61bd448ad7a9f36bde5dc453d0d5a`;
- fitted-state identity:
  `4c12e9e9d91270577ecc54e5058d8f8b642d341f98689e1c870170fc5a788358`;
- fitted-state artifact SHA-256:
  `9f3b7e50a6a46d3efc813006415625ba6df7cc6d38dafa41379f358b9bd36eb6`;
- bundle-manifest SHA-256:
  `a03cfc51c6f945a7034f09f491a1ae46b33c78ff3a9f2562f71a90509fb33e14`;
- VALIDATION freeze-manifest SHA-256:
  `7f4e22ef15e003f28b7d8c00d63a455a9fae963e0d3a2da778fc0b8bbe1507e8`.

The bundle used 92,330 complete matched TRAIN entries from 100 configurations.
It excluded 15,210 incomplete entries: 8,352 with one observed arm and 6,858
with two. The corpus, fit payload and fitted-state encoding each passed exact
double-build determinism checks. A frozen-path TRAIN smoke run completed before
VALIDATION was opened.

The chronological TRAIN qualification used 74,260 fit rows, 18,059 holdout
rows and purged 11 boundary-overlapping rows. Its selector mean realized gross
R was 0.005116, below fixed TP800 at 0.006823. The accepted protocol did not
tune or stop on this result; it froze the fixed model and proceeded once.

## Exact VALIDATION family

All four preregistered sweeps completed with 100 canonical runs and zero
failures. Full child configs, configuration order, platform, dependency and run
fingerprints were checked against the freeze before reporting.

| Family | Mean total gross R | Mean trades | Mean gross R/trade* | Zero-trade configs |
|---|---:|---:|---:|---:|
| Dynamic selector | 6.070725 | 348.52 | 0.123448 | 15 |
| Fixed TP200 | 3.972475 | 378.65 | 0.038416 | 15 |
| Fixed TP400 | 5.489700 | 348.52 | 0.081701 | 15 |
| Fixed TP800 | **7.828575** | 326.58 | **0.146179** | 15 |

\* Rate means use the 85 configurations with at least one trade. The same 15
paired configurations had zero trades in all four families. Their total gross
R is the mathematical empty sum zero, so they remain in both primary means and
paired comparisons as zero ties. Undefined rates and choice shares remain
`null`; they are not replaced with zero.

The dynamic selector's defined-config mean choice mix was 30.002% TP200,
4.173% TP400 and 65.825% TP800.

## Preregistered decision

The best fixed family was TP800.

- Criterion 1 failed: 6.070725 is not greater than 7.828575.
- Criterion 2 failed: dynamic beat TP800 in 19 of 100 paired configurations,
  not more than 50. It lost 45 and tied 36; median paired difference was 0.
- Primary success is therefore **false**.

The exact result artifact is
`/path/to/research-runs/phase7-zone-tp-selector-validation-report-v1.json`,
SHA-256
`95c842fef00904d97de3e64a055ce205abb8a23da5fd0d4e3c04034f2498cb3a`.
The post-VALIDATION zero-trade reporting correction is commit `634c1f5`; it
changed no run, model, config, stored metric artifact or success rule and was
independently reviewed APPROVE. The rebuilt Explorer index contains 749 runs,
13 sweeps and zero scan errors.

This study provides no VALIDATION evidence that this fixed ridge selector
improves on the fixed TP choices. Per the accepted stopping rule, there is no
retuning on VALIDATION and no OOS or LOCKBOX access. Cost/money values remain
unverified and are not used in this conclusion.
