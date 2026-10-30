# Multi Confluence M15 — vector 160 TP1200/SL500 TRAIN follow-up

**Status:** COMPLETE — canonical TRAIN run; TP1200 improves aggregate net but
reduces fold stability versus TP800.

**Date:** 2026-09-06

## Owner instruction and selection status

The owner explicitly requested that run `01M1TSCA6SMZARBR5H9WGR15MC`, the sole
five-fold-positive configuration selected from the prior 600-child TRAIN
sweep, be rerun with take profit 1200 while retaining its stop loss 500.

This is one adaptive, post-search TRAIN follow-up. It is not an independent
confirmation or validation trial and does not authorize a new parameter sweep,
VALIDATION, OOS or LOCKBOX access. Its result will remain visible regardless of
sign.

## Exact frozen comparison

The source run has experiment fingerprint
`0d980bcad0b57e93138f9e0065870c519614895e75b7fe058132132a9fac04a1`.
The follow-up config is
`config/experiments/multi-confluence-m15-train-vector160-tp1200-sl500-v1.json`.
Its canonical config SHA-256 is
`e37b39f7d5e09453d685b89970b9a25b84a0b68567af97c2c9331144521d5681`;
its exact file SHA-256 is
`8a03511b11e17fc6e0fc1f4eb0ce12cc32ce7c16366255855d24052f892e3b52`.

A checked-in regression constructs sweep child 460 and proves that the new
config differs from it only at `execution.take_profit_pips`, changing 800 to
1200. The following remain exact:

- Pivot left/right 2/3.
- RSI length 7 and thresholds 48/52.
- EMA 50/100; ATR length 28.
- S/R radius 0.60 ATR; trendline radius 0.50 ATR.
- Liquidity lookback 30; minimum score 4.
- Non-interruptible execution, M15/HTF semantics and five TRAIN folds.
- Lot size 0.01, SL500 and the same provisional cost binding.

With the declared but unverified `price_units_per_pip=0.01`, TP1200 represents
12.00 recorded quote-price units and SL500 represents 5.00. Entry remains on
the next observed M1 open; later intents are ignored until TP, SL or the
mandatory fold-boundary close.

## Existing comparator

Under TP800/SL500, the source configuration made 195 trades with conditional
gross 155.73, provisional cost 50.70 and conditional net 105.03. Its five fold
net values were 49.90, 0.08, 8.72, 33.82 and 12.51, and its worst single-fold
drawdown was 45.79. These already-inspected values caused the TP1200 follow-up
to be selected and therefore cannot serve as a preregistered baseline result.

The follow-up will be reported directly against TP800 on trade count, gross
result, provisional cost, net result, weighted win rate, drawdown and all five
folds. No metric threshold or promotion rule is declared after selection.

## Mandatory limitations

The binding remains `unverified-audit-input-v1`, with unverified cost semantics
and `recorded_path_unverified`. Pip size, contract size, price side, spread and
commission have not been verified against the exact broker/account. Slippage,
financing, latency, rejection and broker distance rules are absent. PnL and
drawdown are conditional simulator outputs, not verified money estimates.

## Execution and verification record

Canonical run `01M1TTSWWTJPYW7X4MG0VH03D4`, experiment fingerprint
`3e190b49f1c44a258c36cc6a3c352422b37e4593bf211320b6332a4bd25f5447`,
completed from clean freeze commit
`ac88ed8bdf99c5690bdc9a9d11697c529fa964eb`. It produced all ten expected
Plane-B evaluator entries over five folds and recorded neither OOS nor LOCKBOX
access.

Forced-ephemeral, cache-bypassing verification runs
`01M1TTWKTWCCZ9ZFP9M2GMMJ50` and `01M1TTWKW34Q4VGZ44HJA9BB2F` reproduced all
feature, model, intent, trade, equity, inventory and metric artifacts exactly,
with maximum absolute deviation `0.0`.

The incremental Explorer index reached generation 5 with 610 total runs and
zero scan errors. The since-run Trial Ledger reports one distinct hypothesis,
nine Plane-B questions, two verification attempts collapsed, ten
unverified-cost entries and no Plane-A, noncanonical, failure, discrepancy,
OOS or LOCKBOX events.

## Result

All values sum the same five disjoint TRAIN folds. They are conditional
simulator outputs under the unverified execution and cost binding.

| TP / SL | Trades | Gross | Provisional cost | Net | Weighted win rate | Positive folds | Worst fold drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| 400 / 500 | 227 | 71.00 | 59.02 | 11.98 | 59.0308% | 2 / 5 | 45.18 |
| 800 / 500 | 195 | 155.73 | 50.70 | 105.03 | 44.6154% | 5 / 5 | 45.79 |
| **1200 / 500** | **187** | **169.73** | **48.62** | **121.11** | **34.7594%** | **4 / 5** | **68.56** |

Relative to TP800, TP1200 executes eight fewer trades, adds 14.00 gross,
reduces provisional cost by 2.08 and improves aggregate net by **16.08**. Its
weighted win rate is 9.856 percentage points lower, as expected for the more
distant target. Its 187 trades exit as 65 TP and 122 SL, with zero target-change
or boundary exits, one gapped exit and zero ambiguous exits.

| Fold | TP800 net | TP1200 net | TP1200 - TP800 |
|---:|---:|---:|---:|
| 0 | 49.90 | 70.90 | 21.00 |
| 1 | 0.08 | 27.60 | 27.52 |
| 2 | 8.72 | 10.98 | 2.26 |
| 3 | 33.82 | -11.66 | -45.48 |
| 4 | 12.51 | 23.29 | 10.78 |

TP1200 improves four fold values but materially reverses fold 3. Consequently,
the aggregate gain comes with one fewer positive fold and a worst-fold
drawdown increase of 22.77, from 45.79 to 68.56.

## Conclusion

TP1200 is the best of the three tested targets for aggregate TRAIN net PnL on
this exact vector, but TP800 remains better on fold consistency and drawdown.
The extra 16.08 net is modest relative to the loss of five-fold positivity and
the 49.7% drawdown increase. Because vector 160 was selected after a
600-configuration TRAIN search and TP1200 was then selected adaptively, this
result has additional selection bias and is not independent evidence.

No protected data was opened. A later choice between TP800 and TP1200 must
state whether aggregate TRAIN net or fold stability is the intended criterion;
this run alone does not establish either setting as tradable.
