# Multi Confluence M15 — BTC 300 × 2 five-fold TRAIN sweep V2

**Status:** COMPLETE — 600/600 canonical TRAIN children, five folds each,
zero failures and no protected-data access.

**Date:** 2026-09-09

This is the corrected successor to the immutable four-fold V1 record. It is
byte-for-byte equivalent at the expanded experiment level except for fold
geometry: `eval_rows` and `step_rows` are each 279,420 instead of 279,421.
The 1,397,105-row bounded evaluation axis therefore contains three technical
fit rows, five complete evaluation folds, and two unused tail rows.

The frozen request is
`config/sweeps/multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-fivefold-v2.json`,
with file SHA-256
`89e2b2ee9836745de734ca7a7573c6f69a8268486cd8b1d485903e1d29c244e7`.

All other data, strategy, execution and limitation statements in
`MULTI_CONFLUENCE_M15_BTC_TRAIN_300X2_V1.md` apply unchanged. This remains a
TRAIN-only exploratory family; VALIDATION, OOS and LOCKBOX are not authorized.

## Execution record

The sweep was planned and executed from clean commit
`c1bbe35775c60b57889c9c2390a3152fbf0a7a0e`. Sweep
`mc-m15-btc-train-300x2-tp18000-tp36000-sl22500-5fold-v2` has spec digest
`7e9d6bc44f68cd4251999f333e51df6c5c69b16ba9130f89b544d6b12ff447a6`.
All 600 children completed with eight workers; there were zero failed,
interrupted, pending or resumed children. A post-run artifact check confirmed
five policy executions and five TradeLogs in a representative child, with
`oos_touched=false` and `lockbox_touched=false`.
