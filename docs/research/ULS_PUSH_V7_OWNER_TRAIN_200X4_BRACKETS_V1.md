# ULS + PUSH v7 — owner-directed 200 × 4 fixed-bracket TRAIN sweep

**Status:** COMPLETE — 800/800 canonical TRAIN children, zero failures.

**Date:** 2026-08-26

## Owner instruction and methodology status

After the default ULS + PUSH vector was negative on both Plane A and the
TP400/SL400 simulator, the owner explicitly requested 200 strategy-setting
combinations and supplied these four fixed bracket pairs:

| Take profit (pips) | Stop loss (pips) |
|---:|---:|
| 200 | 200 |
| 800 | 800 |
| 600 | 300 |
| 300 | 600 |

This is a visible owner-directed exploratory exception to the earlier stopping
rule. It authorizes this exact TRAIN sweep only. It does not reverse the
negative Plane-A result, establish predictive information, or authorize
VALIDATION, OOS or LOCKBOX access.

The arithmetic is exactly **200 strategy vectors × 4 bracket pairs = 800
ordinary experiment configurations**. All 800 configurations count and remain
visible, including zero-trade, failed and unfavourable children. No result may
cause a vector to be changed, replaced or added to this family.

## Deterministic strategy design

The frozen request is
`config/sweeps/uls-push-v7-train-owner-200x4-brackets-v1.json`; its deterministic
generator is `scripts/generate_uls_push_sweep.py`.

- Vector 0 is the exact previously tested default ULS + PUSH vector.
- Vectors 1–199 are generated without randomness by a fixed Halton
  low-discrepancy enumeration over the declared option lists.
- Engine allocation is exactly 100 `merged` and 100 `parallel` vectors. Both
  modes keep ULS and Push active; standalone ULS-only and Push-only strategies
  are outside this family.
- The search varies liquidity/pivot construction, sweep and IFVG thresholds,
  Push-zone creation/retest settings, confirmation logic, momentum/key-level
  profiles and one optional filter profile at a time.
- Related min/max fields are selected as valid pairs. Derived ATR, EMA and
  momentum windows are recomputed exactly. Parameters belonging to disabled
  optional filters are reset to one canonical baseline rather than creating
  inactive-parameter identity splits.
- Fixed fields include M5 aggregation, phase offset, `minimum_tick=0.01`, hour
  and session filters off, the generic pass-through model, threshold-zero
  exposure, lot size and the provisional cost binding.

The checked-in test requires 200 exact unique strategy parameter documents,
100 per engine mode, four identical ordered copies of the strategy set, the
four supplied brackets at 200 children each, 800 unique complete experiment
documents, and successful provider/config construction for every child.

## Fixed data and simulation

- Dataset: `XAUUSD_S_M1_TRAIN_V1` only, manifest
  `30ca7b4dbec4c7565d6bba312ae5c7d79fe9d7645ecd3c6868df89e62dff374e`.
- Bounds: context `1577926800`; evaluation `1580437860` through `1703894160`.
- Five contiguous 200,000-row folds after 385,824 fit rows.
- Plane-A outcomes and evaluators are omitted; this family asks Plane-B
  simulator questions only.
- Simulator: `bracket_single_position_v1`, one open position at a time; entry
  on the next observed M1 open; fixed lot size `0.01`.
- `price_units_per_pip=0.01`; spread 20 pips; commission 6.00 per lot round
  turn. Stop wins an unprovable same-bar TP/SL ordering, gaps use observed open,
  and open fold-end positions close at the boundary.
- Trade evaluators: `trade_summary_v1` and `equity_drawdown_v1`.

The four bracket blocks are ordered 200/200, 800/800, 600/300, 300/600. Each
block uses the same 200 strategy vectors in the same order. This ordering also
lets later bracket blocks reuse strategy feature artifacts without changing
experiment identity.

## Mandatory limitations and reporting rule

The binding remains permanently `unverified-audit-input-v1`, with
`cost_semantics=unverified` and
`execution_price_model=recorded_path_unverified`. Pip size, contract size,
spread, commission basis and recorded OHLC price side are not verified against
the exact broker/account. Slippage, financing, latency, rejection and broker
distance rules are absent. Every PnL/drawdown value is therefore a conditional
simulator output, not a verified money estimate or a TradingView reproduction.

This is a related 800-configuration exploratory search, not 800 independent
confirmations. Results may be ranked and summarized descriptively only, with
trade counts, gross PnL, provisional cost, net PnL and fold behaviour shown.
There is no p-value, significance claim, multiple-testing adjustment or
promotion decision. After completion, rebuild the Explorer index and record
every child and failure; do not access protected data.

## Execution record

The frozen request was planned and run from clean commit
`8ed0016d31a35535b251fd3035d14216b3d30c62`. Sweep
`uls-push-v7-train-owner-200x4-brackets-v1` completed all **800/800** children
with 8 workers in 1,080.017600 seconds. There were zero failed, interrupted,
pending or duplicate children and no retry attempts. Every run is canonical;
all 800 used TRAIN and recorded `oos_touched=false` and
`lockbox_touched=false`.

The Trial Ledger reports 800 distinct related hypotheses and 7,200 Plane-B
questions (nine evaluator questions per configuration), with five folds each.
It reports zero Plane-A trials, noncanonical runs, pre-evaluation failures,
audit discrepancies or protected-data events. All 8,000 evaluator entries
retain the mandatory unverified-cost marker. These 7,200 questions are not
statistically independent and have no multiple-testing correction.

## Descriptive result

All sums below are across the five disjoint TRAIN folds of one configuration.
Medians and means are descriptive summaries across 200 related searched
configurations, not portfolio returns or estimates of future performance.

| TP / SL | Gross-positive configs | Net-positive configs | All 5 folds net-positive | Median gross | Median net | Mean net | Best net |
|---|---:|---:|---:|---:|---:|---:|---:|
| 200 / 200 | 90 / 200 | 6 / 200 | 0 | 0.00 | -330.83 | -750.40 | 21.62 |
| 800 / 800 | 82 / 200 | 26 / 200 | 1 | -9.24 | -279.74 | -571.79 | 262.81 |
| 600 / 300 | 67 / 200 | 16 / 200 | 0 | -27.02 | -337.26 | -673.04 | 75.46 |
| 300 / 600 | 92 / 200 | 15 / 200 | 2 | 0.00 | -295.94 | -644.41 | 59.47 |

TP800/SL800 was the least-negative bracket by both mean and median net PnL and
won the within-vector net comparison for 94 of 200 strategy vectors. The other
paired winners were TP300/SL600 for 44 vectors, TP200/SL200 for 35 and
TP600/SL300 for 27. This is a descriptive TRAIN ranking only.

The highest net result was child 344, run `01M0ZECR10K8XERRYZYK3ZRPAT`, a
`merged` TP800/SL800 configuration. Its 1,848 trades produced conditional gross
PnL 743.29, provisional cost 480.48 and net PnL 262.81; its worst single-fold
drawdown was 177.18. Fold net PnL was -76.24, 88.10, 137.93, -12.62 and 125.64,
so the selected maximum was negative in two of five folds.

Only three of all 800 searched configurations were net-positive in all five
folds. The strongest of those was child 258, run
`01M0ZEADBPXDKQ0RWQXS1CAQDT`, also merged TP800/SL800: 98 trades, gross 171.10,
cost 25.48 and net 145.62, with fold net values 39.90, 32.84, 19.58, 4.10 and
49.20. The two others were TP300/SL600 configurations with only 16 and 63 total
trades. All three are post-search observations rather than preregistered
confirmations.

## Conclusion

The broad result is still weak: every bracket has a negative median and mean
net result, and only 63 of 800 configurations are net-positive on the searched
TRAIN folds. TP800/SL800 contains the most promising TRAIN candidates, but the
best aggregate configuration changes sign across folds and the only
five-fold-positive TP800/SL800 candidate has 98 trades. This sweep therefore
creates candidates for a separately frozen validation decision; by itself it
does not establish an edge or justify a profitability claim.

The run index was rebuilt incrementally with 806 total runs, one sweep and zero
scan errors. Explorer commit `6a25a66` requests up to 2,000 experiments so all
800 new runs are visible rather than only the newest 500, and its full test and
Ruff checks are green.
