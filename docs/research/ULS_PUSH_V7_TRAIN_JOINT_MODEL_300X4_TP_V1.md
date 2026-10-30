# ULS + PUSH v7 — 300 × 4 TP TRAIN corpus

**Status:** PURGED — 1,200 superseded system-test run directories deleted

**Date:** 2026-08-26

## Question and authorized scope

The owner requested a clean TRAIN corpus for a later joint model that can choose
between no entry and four take-profit levels. This experiment runs exactly 300
unique ULS + PUSH strategy-setting vectors at TP 200, 400, 600 and 800 pips,
with stop loss fixed at 800 pips. The arithmetic is exactly **300 × 4 = 1,200
ordinary TRAIN configurations**.

This document authorizes only this TRAIN sweep. It does not authorize model
training, VALIDATION, OOS or LOCKBOX access. All 1,200 configurations remain in
the corpus, including zero-trade, failed and unfavourable children.

## Frozen design

The request is
`config/sweeps/uls-push-v7-train-joint-model-300x4-tp-v1.json`; its deterministic
generator is `scripts/generate_uls_push_tp_training_sweep.py`.

- Vector 0 is the exact default ULS + PUSH vector.
- Vectors 1–299 use the fixed Halton low-discrepancy enumeration and declared
  domains in `scripts/generate_uls_push_sweep.py`.
- The first 200 vectors exactly preserve the previous 200-vector family. This
  retains the historically strongest configurations without selecting new
  settings from this sweep's outcomes.
- Vectors 200–299 extend the same predeclared coverage; no randomness or
  result-dependent replacement is allowed.
- Engine allocation is exactly 150 `merged` and 150 `parallel` vectors.
- The four ordered blocks contain the exact same 300 strategy documents. Only
  TP changes between matched positions; SL is always 800.

All dataset, bounds, folds, simulator rules, lot size, provisional transaction
costs and fixed feature fields are inherited unchanged from the earlier
checked-in Plane-B experiment. The dataset role is TRAIN, and the request omits
Plane-A outcomes and evaluators.

## Intended learning value and limitations

This paired design is suitable evidence for a first TP-selection model because
every strategy vector is observed at all four TP choices. Deduplication must use
the entry event identity plus strategy state; folds must remain chronological;
and no row from VALIDATION may participate in feature selection, labels,
calibration or threshold choice.

The corpus is useful but not a perfect target for a single joint no-entry/TP
model. `bracket_single_position_v1` permits one open position at a time, so a TP
choice can change when a later signal is eligible to become a trade. The later
training pipeline must therefore distinguish raw emitted entry events from
executed trades and retain only causally available entry-time inputs. For TP
labels, matched events with complete outcomes at all four TP levels are the
cleanest subset. A no-entry class requires a separately frozen utility rule and
valid negative examples; absence of a recorded trade is not automatically a
negative label.

This experiment is a related 1,200-configuration exploratory search, not 1,200
independent confirmations. Costs remain `unverified-audit-input-v1`; all PnL and
drawdown values are conditional simulator outputs. Results may be summarized
descriptively on TRAIN, but they do not establish an edge or justify a
profitability claim.

## Execution record

The request was planned and executed from clean commit
`cdf39f13197171c0c3f8aea330c3e442881cf0dd`. Sweep
`uls-push-v7-train-joint-model-300x4-tp-v1` completed all **1,200/1,200**
children with eight workers in 1,618.180971 seconds. There were zero failed,
interrupted, pending, duplicate or retry children. All 1,200 experiment
fingerprints are unique; every run is canonical TRAIN and records
`oos_touched=false` and `lockbox_touched=false`.

The final matrix contains exactly 300 runs at each of TP 200, 400, 600 and 800
pips, with SL 800 in every run. The Trial Ledger records 1,200 related distinct
hypotheses and 10,800 Plane-B questions, zero Plane-A trials, zero protected-data
events, zero pre-evaluation failures and 12,000 evaluator entries carrying the
mandatory unverified-cost marker.

The Explorer index was rebuilt from scratch at generation 1. It contains 1,200
runs with zero scan errors and a fresh source token. Final derived storage is
approximately 94 GiB: 72 GiB strategy cache, 23 GiB run artifacts and 33 MiB
index data; approximately 111 GiB remains free on the volume.

## System-test purge record

On 2026-08-27 the owner declared this corpus superseded during pre-production
system testing and authorized deletion under ADR-0011 Amendment 5 and ADR-0017
Amendment 3. Preflight resolved exactly 1,200 unique COMPLETE run directories
from commit `cdf39f13197171c0c3f8aea330c3e442881cf0dd`, with zero fingerprint
overlap against the non-interruptible replacement sweep. All 1,200 directories
were permanently deleted and none remained.

The sweep specification remains as the historical question record. The compact
purge receipt is
`<runs_root>/reports/purge-uls-push-v7-train-joint-model-300x4-tp-v1.json`;
its target-set SHA-256 is
`b7bfe7992b813e531b37e1bcf1bc269f2546a9e4c4fec0caba94eca17fb83b60`.
The deleted evaluations no longer appear in the live file-derived Trial Ledger
and can only be reconstructed by rerunning the old sweep.
