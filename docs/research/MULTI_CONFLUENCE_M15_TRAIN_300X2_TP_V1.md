# Multi Confluence M15 — 300 × 2 fixed-bracket TRAIN sweep

**Status:** COMPLETE — 600/600 canonical TRAIN children; zero failures and no
protected-data access.

**Date:** 2026-09-06

## Owner instruction and methodology status

After the default Multi Confluence M15 vector showed no consistent positive
directional information in Plane A, the owner explicitly requested an
exploratory fixed-bracket simulation over 300 strategy-setting combinations,
with take-profit values 400 and 800 and stop-loss 500 fixed.

This is a visible owner-directed exception to the prior Plane-A stopping rule.
It authorizes this exact TRAIN sweep only. It does not reverse the negative
Plane-A result or authorize VALIDATION, OOS or LOCKBOX access.

The arithmetic is exactly **300 strategy vectors × 2 bracket settings = 600
ordinary experiment configurations**. Every child remains part of the record,
including unfavourable, zero-trade and failed children.

## Frozen artifacts

- Base config:
  `config/experiments/multi-confluence-m15-train-plane-b-tp400-sl500-v1.json`.
- Base-config canonical SHA-256:
  `ffefe5b0f33122cf91285d3d8ba3077fa2a4de94a9240ba5ead7f215149da1e6`.
- Sweep request:
  `config/sweeps/multi-confluence-m15-train-300x2-tp400-tp800-sl500-v1.json`.
- Exact request-file SHA-256:
  `c351b4942c6dad8dcb8b2abea0ae7a57a0ade2c9e575a127ae8925a426060db6`.
- Deterministic generator: `scripts/generate_multi_confluence_sweep.py`.

## Deterministic strategy design

Vector 0 is the exact previously tested default. Vectors 1–299 are unique and
generated without randomness by a fixed Halton low-discrepancy enumeration,
skipping any duplicate discrete projection. Only inputs present in the
supplied Pine strategy vary:

| Input | Frozen options |
|---|---|
| Pivot Left | 2, 3, 5 |
| Pivot Right | 2, 3, 5 |
| RSI Length | 7, 14, 21, 28 |
| RSI Long / Short | 35/65, 40/60, 45/55, 48/52 |
| EMA Fast / Slow | 20/100, 20/200, 50/100, 50/200, 100/200 |
| ATR Length | 7, 14, 21, 28 |
| S/R proximity in ATR | 0.30, 0.45, 0.60, 0.75, 0.90 |
| Trendline proximity in ATR | 0.25, 0.50, 0.75, 1.00 |
| Liquidity lookback | 10, 20, 30, 40, 60 |
| Minimum score | 3, 4, 5 |

RSI thresholds and EMA lengths are paired profiles, so every child preserves
the provider's ordering constraints. Pivot values do not exceed the default
five; consequently every child has declared prior reach no greater than the
frozen 645,120-row context. The M15/HTF grids, finite recursive windows, level
and trendline lifetimes, gap policy, model and decision policy remain fixed.

The checked-in test requires 300 exact unique strategy documents, two identical
ordered copies of that strategy set, 600 unique complete experiment documents,
successful provider/config construction, exact domain coverage and the two
approved bracket counts.

## Fixed data and execution

- Dataset: `XAUUSD_S_M1_TRAIN_V1` only, manifest
  `30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e`.
- Bounds and folds are unchanged from Plane A: 645,120 prior context rows,
  followed by five contiguous 153,883-row evaluation folds and a three-row
  technical fit span for the stateless model.
- Plane-A outcomes and evaluators are omitted; this family asks Plane-B
  simulator questions only.
- Simulator: `bracket_non_interruptible_v1`. Entry occurs at the next observed
  M1 open. At most one trade is open; later intents are ignored until TP, SL or
  the mandatory fold-boundary close.
- The two ordered 300-child blocks are TP400/SL500 and TP800/SL500.
- Fixed lot size is 0.01. With `price_units_per_pip=0.01`, TP distances are
  4.00 and 8.00 recorded quote-price units and the SL distance is 5.00.
- The provisional binding charges 20 spread pips and commission 6.00 per lot
  round turn. Stop wins an unprovable same-row TP/SL ordering; a gap uses the
  observed open.
- Trade evaluators are `trade_summary_v1` and `equity_drawdown_v1`.

## Mandatory limitations and reporting rule

The cost binding remains `unverified-audit-input-v1`, with
`cost_semantics=unverified` and
`execution_price_model=recorded_path_unverified`. Pip size, contract size,
spread, commission basis and the recorded OHLC price side are not verified
against the exact broker/account. Slippage, financing, latency, rejection and
broker distance rules are absent. Any PnL or drawdown is therefore a
conditional simulator output, not a verified money estimate.

This is one related 600-configuration exploratory search, not 600 independent
confirmations. Results are reported for both bracket blocks, paired by strategy
vector, with trade count, gross result, provisional costs, net result, drawdown
and fold stability. Selected maxima are post-search observations. There is no
p-value, significance claim, hidden child, parameter addition or protected-data
access.

## Execution record

The exact frozen request was planned and run from clean commit
`3d743cdff59000ad4a9616662ac682062d624642`. Sweep
`multi-confluence-m15-train-300x2-tp400-tp800-sl500-v1`, with spec digest
`16aaed44ca7a9e18a1e9c00d0c50dd12811e1f0064aa61006ea15003bf2e1751`,
completed all **600/600** children with eight workers in 318.627774 seconds.
There were zero failed, interrupted, pending, duplicate or resumed children.
All runs are canonical, used TRAIN only and recorded `oos_touched=false` and
`lockbox_touched=false`.

The incremental Explorer index rebuild reached generation 4 with 607 total run
records and zero scan errors. The scoped Trial Ledger reports 600 distinct
related hypotheses and 5,400 Plane-B questions, with five folds per question.
It reports zero Plane-A trials, noncanonical runs, pre-evaluation failures,
audit discrepancies or protected-data events. All 6,000 evaluator entries
retain the unverified-cost marker. Four verification attempts were correctly
collapsed rather than counted as new hypotheses.

An artifact audit opened all 3,000 stored TradeLogs and reconciled every trade
count, gross result, cost and net result against its registered evaluator
metric. The logs contain 476,978 trades across alternative configurations:
231,101 TP exits, 245,065 SL exits and 812 mandatory fold-boundary closes.
There are exactly zero `TARGET_CHANGE` exits, confirming the requested
non-interruptible execution. There are 29 ambiguous exits (0.00608%) and 4,917
gapped exits (1.03087%).

The selected aggregate maximum and the sole five-fold-positive configuration
were each rerun twice with forced-ephemeral, cache-bypassing verification. All
feature, model, intent, trade, equity, inventory and metric comparisons were
structurally and exactly equal, with maximum absolute deviation `0.0`.
Verification run IDs are `01M1TSSKT1XHW9AVZ96CSN57G5` and
`01M1TSSKXM1ZNC6DQST4TAG1Y1` for the aggregate maximum, and
`01M1TSSKT17JR4P2PEP2X4DAC0` and `01M1TSSKXEQSAA8XQRK4VR9XEE` for the
five-fold-positive configuration.

## Descriptive result

Each configuration below sums its five disjoint TRAIN folds. Zero-trade
configurations are retained with zero aggregate PnL and undefined rates.
Medians and means are landscape summaries across related alternatives, not
portfolio returns or estimates of future performance.

| TP / SL | Active | Gross-positive | Net-positive | Net-positive and >=3 positive folds | All 5 folds positive | Mean gross | Mean provisional cost | Mean net | Median net, all / active | Best net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 400 / 500 | 240 / 300 | 93 | 28 | 14 | 0 | -19.98 | 242.76 | -262.75 | -55.57 / -127.19 | 55.37 |
| 800 / 500 | 240 / 300 | 144 | 41 | 21 | 1 | 51.21 | 170.62 | -119.41 | -37.49 / -83.10 | 147.36 |

TP800/SL500 is the less-negative landscape. It wins the within-vector net
comparison for 174 of 300 strategy vectors, versus 52 wins for TP400/SL500 and
74 ties. The median paired TP800-minus-TP400 improvement is 18.31, the mean is
143.34 and paired net correlation is 0.8265. Fifteen vectors are net-positive
under both brackets, 54 under at least one and none is positive in all five
folds under both. TP800 holds positions longer and executes a median 11.5 fewer
trades per paired vector.

Across alternatives, TP400 produced 280,110 trades at a 55.2604% weighted win
rate, while TP800 produced 196,868 at 38.9769%. TP400 was negative before
costs on average. TP800 was positive before costs on average, but its mean
gross 51.21 did not cover mean provisional cost 170.62. This relative result
depends on the unverified execution and cost assumptions.

### Minimum-score behaviour

The score threshold is the dominant landscape distinction:

| Minimum score | Configs | TP400 active / median active trades / mean net / net-positive | TP800 active / median active trades / mean net / net-positive |
|---:|---:|---:|---:|
| 3 | 102 | 102 / 2,565.5 / -704.94 / 0 | 102 / 1,767 / -302.70 / 4 |
| 4 | 103 | 103 / 164 / -66.40 / 18 | 103 / 150 / -46.98 / 24 |
| 5 | 95 | 35 / 2 / -0.85 / 10 | 35 / 2 / -1.14 / 13 |

Score 3 overtrades and is strongly negative. Score 5 is mostly inactive: 60 of
95 vectors make no trade, and the active vectors have a median of only two
trades, so its small positive counts are not meaningful evidence. Score 4 is
the only region with nontrivial activity and multiple positive candidates,
but its mean and median remain negative under both brackets.

### Default and selected post-search configurations

The exact default vector remains negative under both exits. TP400 produced 161
trades, gross -13.03, provisional cost 41.86 and net -54.89, with two positive
folds and worst single-fold drawdown 46.58. TP800 produced 148 trades, gross
-64.04, provisional cost 38.48 and net -102.52, with one positive fold and
worst single-fold drawdown 67.08. Thus the relative TP800 advantage does not
hold for the default itself.

The selected maximum is child 589, run `01M1TSDWQQNXVMV3C0HKMC13NX`, vector
289 under TP800/SL500. Its settings are pivot 3/3, RSI length 28 with 45/55
thresholds, EMA 100/200, ATR length 14, S/R radius 0.45 ATR, trendline radius
0.50 ATR, liquidity lookback 10 and minimum score 3. Its 1,729 trades produced
gross 596.90, provisional cost 449.54 and net 147.36, with weighted win rate
41.0642% and worst single-fold drawdown 332.80. Fold net values were 258.77,
41.33, 9.39, -242.88 and 80.75. The same vector under TP400 was net -576.77.
The maximum is therefore bracket-sensitive and materially concentrated in one
TRAIN regime despite being positive in four folds.

The only configuration positive in all five folds is child 460, run
`01M1TSCA6SMZARBR5H9WGR15MC`, vector 160 under TP800/SL500. Its settings are
pivot 2/3, RSI length 7 with 48/52 thresholds, EMA 50/100, ATR length 28, S/R
radius 0.60 ATR, trendline radius 0.50 ATR, liquidity lookback 30 and minimum
score 4. Its 195 trades produced gross 155.73, provisional cost 50.70 and net
105.03, with weighted win rate 44.6154% and worst single-fold drawdown 45.79.
Fold net values were 49.90, **0.08**, 8.72, 33.82 and 12.51. Under TP400, the
same vector was net 11.98 but positive in only two folds. The 0.08 fold margin,
post-search selection and missing real slippage make this a fragile TRAIN
candidate, not confirmation.

The highest TP400 result was child 197, run
`01M1TS8G3CDQ8RGN2T7T5YSRK2`: 164 trades, gross 98.01, provisional cost 42.64
and net 55.37, with only two positive folds. No TP400 configuration was
positive in all five folds.

## Conclusion

TP800/SL500 is clearly preferable to TP400/SL500 within this fixed search, but
the broad result remains negative: both mean and active-configuration median
net PnL are below zero, the exact default loses under both exits and only one
of 600 searched configurations is positive in all five TRAIN folds. That one
candidate has a fold with only 0.08 net margin under incomplete execution and
cost assumptions.

This sweep therefore does not reverse the earlier negative Plane-A result and
does not establish a tradable edge. Child 460 is the least fold-unstable TRAIN
candidate if the owner later chooses to freeze a separate validation question;
no VALIDATION, OOS or LOCKBOX data was opened here, and no such next run is
authorized by this report.
