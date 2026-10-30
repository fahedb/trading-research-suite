# Multi Confluence M15 — XAU portfolio replay 6 × 2

**Status:** COMPLETE — 12/12 canonical TRAIN replays, zero failures and no
protected-data access.

**Date:** 2026-10-06

## Purpose and interpretation

The owner requested 10–15 gold experiments from an already-tested strategy so
the Research Explorer contains a useful comparison portfolio. This protocol
freezes exactly 12 configurations from the completed 600-child Multi
Confluence M15 XAUUSD TRAIN sweep.

This is a display-oriented replay of previously inspected configurations. It
is not a new parameter search, an independent confirmation, or evidence of a
tradable edge. Result-selected members remain visibly post-search.

## Frozen selection

Six existing strategy vectors are each replayed with TP400/SL500 and
TP800/SL500, keeping the comparison paired:

| Vector | Source children | Selection reason |
|---:|---:|---|
| 0 | 0, 300 | Exact baseline |
| 75 | 75, 375 | Fixed high-threshold diagnostic (`minimum_score=5`) |
| 100 | 100, 400 | Fixed low-threshold diagnostic (`minimum_score=3`) |
| 160 | 160, 460 | Prior sole five-fold-positive TP800 selection |
| 197 | 197, 497 | Prior highest TP400 result |
| 289 | 289, 589 | Prior highest TP800 result |

The replay request is
`config/sweeps/multi-confluence-m15-xau-portfolio-replay-6x2-v1.json`.
Its frozen SHA-256 is
`5a30034645b57eea12e2f4829bf901fe80309734f63ae7c9dbe38ae33a8f55d0`.
Every expanded config must be byte-equivalent as a document to its named child
in the original frozen request. The checked-in regression requires 12 unique
configs, six instances of each bracket, score-threshold coverage of 3/4/5 and
TRAIN-only access.

## Fixed execution contract

- Dataset: `XAUUSD_S_M1_TRAIN_V1` only.
- Feature/model: `multi_confluence_m15_v1` and
  `multi_confluence_gate_v1`.
- Five contiguous 153,883-row evaluation folds after the frozen context span.
- Simulator: `bracket_non_interruptible_v1`, one open trade at a time.
- Brackets: TP400/SL500 and TP800/SL500.
- Lot size: 0.01.
- Trade evaluators: `trade_summary_v1` and `equity_drawdown_v1`.
- No VALIDATION, OOS or LOCKBOX access is authorized.

Costs and execution remain explicitly unverified. Spread, commission, trigger
price side, slippage, financing, latency, rejection and broker rules are not
complete real-account evidence. Monetary results are conditional simulator
outputs only.

## Execution record

The request was planned and executed from clean commit
`897a4170d9ee467a1cec1828819e596604c27139`. Sweep
`multi-confluence-m15-xau-portfolio-replay-6x2-v1` has spec digest
`b749016a33be3067138fe9189ecca919a60f05a34c7a8acca04813c859c202c5`.
Six workers completed all 12 children in 13.38 seconds with zero failed,
interrupted, pending, retried, adopted or skipped children.

All 12 runs are canonical and COMPLETE. Each records five policy executions,
10 evaluator entries, `oos_touched=false` and `lockbox_touched=false`. The
scoped Trial Ledger reports 12 distinct related hypotheses, 108 Plane-B
questions, zero discrepancies or protected-data events, and 120 evaluator
entries marked with unverified cost semantics.

The incremental Explorer index reached generation 3 with 15 total physical
runs and zero scan errors. The Explorer exposes a separate Multi Confluence
family containing exactly these 12 runs. API reconciliation opened every run:
overview trade count, serialized TradeLog count and matched signal context were
equal for every run, covering 9,863 alternative-configuration trades in total.

## Descriptive replay result

The table preserves the frozen vector order. Net PnL and drawdown are
conditional simulator values under the unverified cost/execution binding.

| Vector | TP / SL | Run ID | Trades | Net PnL | Worst fold drawdown |
|---:|---:|---|---:|---:|---:|
| 0 | 400 / 500 | `01M49BW47VNWRQ3G90XXBQS8RP` | 161 | -54.89 | 46.58 |
| 0 | 800 / 500 | `01M49BW49EQRCR4CQYFXG23JMZ` | 148 | -102.52 | 67.08 |
| 75 | 400 / 500 | `01M49BW4B5NVJWW6BCSMV9C6SM` | 2 | -1.52 | 7.44 |
| 75 | 800 / 500 | `01M49BW4CXRWRS6JW82W97YFDQ` | 2 | 2.48 | 7.44 |
| 100 | 400 / 500 | `01M49BW4ENBB1PG1GPJFKKFA0J` | 2,693 | -495.22 | 276.30 |
| 100 | 800 / 500 | `01M49BW4GEW95EGQ0DHBY150EQ` | 1,826 | -6.04 | 195.11 |
| 160 | 400 / 500 | `01M49BW9EJ0A352TDTZ7YP0ZYR` | 227 | 11.98 | 45.18 |
| 160 | 800 / 500 | `01M49BW9HFE8ZVZESDY0F4S4D3` | 195 | 105.03 | 45.79 |
| 197 | 400 / 500 | `01M49BWA3RB6PX9A66S05T12HB` | 164 | 55.37 | 36.05 |
| 197 | 800 / 500 | `01M49BWA5J6PJXMTQ4W6YTM1EW` | 151 | -11.47 | 60.90 |
| 289 | 400 / 500 | `01M49BWA7BX858YZN6TR747W15` | 2,565 | -576.77 | 351.43 |
| 289 | 800 / 500 | `01M49BWA93Y1YE51R5EQCF9NRW` | 1,729 | 147.36 | 332.80 |

Five of the 12 replayed configurations are net-positive and seven are
net-negative. The largest net value is vector 289 under TP800/SL500, but it is
the previously selected TRAIN maximum and carries a 332.80 worst-fold drawdown.
Vector 160 under TP800/SL500 reproduces the prior 105.03 result with all five
folds positive, but one fold contributes only 0.08. Vector 75's positive 2.48
comes from only two trades and is not meaningful performance evidence.

These alternatives must not be summed as if they were one trading portfolio.
They are mutually exclusive configurations selected to make the Explorer
comparison useful. The replay successfully restores a compact, varied visual
record; it does not alter the original conclusion that the wider TRAIN
landscape was negative and no tradable edge has been established.
