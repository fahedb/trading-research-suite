# ULS + PUSH v7 — TRAIN simulation with fixed TP400 / SL400

**Status:** COMPLETE — negative gross and net simulator result ·
**Date:** 2026-08-26

## Owner instruction and methodology status

The owner explicitly requested this simulation after the first Plane-A study
found no consistent positive signed information. This is therefore a visible,
owner-directed methodology exception: it does not reverse or supersede the
negative Plane-A result, and a favourable simulation cannot be presented as
predictive evidence.

This family is TRAIN only. It must not access VALIDATION, OOS or LOCKBOX, and
it contains one fixed execution vector rather than a TP/SL sweep.

## Frozen signal and simulation

- Config: `config/experiments/uls-push-v7-train-plane-b-tp400-sl400-v1.json`.
- Canonical config SHA-256:
  `9bed953127cc60aba69698196003638f79e5fccadb6069aeb41d007f37f80d9a`.
- Signal provider/model/policy and five evaluation folds are identical to the
  first ULS + PUSH family. The policy maps sparse signed `score` events to unit
  exposure with threshold zero.
- Plane A evaluators are omitted so the two prior Plane-A questions are not
  counted again under a new execution fingerprint.
- Simulator: `bracket_single_position_v1`, one open position at a time.
- Entry: next observed M1 row's open.
- Lot size: `0.01`.
- Take profit: `400` pips.
- Stop loss: `400` pips.
- `price_units_per_pip=0.01`, so both bracket distances equal 4.00 price units.
- Spread: 20 pips.
- Commission: 6.00 per lot, round turn; 0.06 for a completed 0.01-lot trade.
- Same-bar TP/SL ambiguity resolves to the stop; gaps use the observed open;
  positions still open at a fold boundary are forcibly closed.
- Trade evaluators: `trade_summary_v1` and `equity_drawdown_v1`.

## Mandatory limitations

The cost binding is permanently `unverified-audit-input-v1` with
`cost_semantics=unverified` and
`execution_price_model=recorded_path_unverified`. The recorded OHLC price side,
pip size, contract size, spread and commission basis have not been verified
against the exact broker/account. There is no slippage, financing, latency,
rejection or broker-distance model. The fixed 400/400 bracket is not the Pine
strategy's dynamic sweep/ATR stop and target.

Any monetary output is a conditional simulator result under these assumptions,
not a broker-realistic profit estimate. Gross and net values must both be
reported with this caveat; neither may be used to override the Plane-A result.

## Result

Canonical TRAIN run `01M0YE5SE5YR24P9XN5RXZ3XTR`, experiment fingerprint
`ef6337ce7bfcf67e8fbc040d5bf876fa4fd3294cd047538c679d74ea23135562`,
completed from clean commit
`795431013f7ec9adb3a56ed33610a4dcd3ea9e07` in 12.413924805 seconds. It
recorded five independent fold simulations and ten Plane-B inventory entries.
`oos_touched` and `lockbox_touched` are both false.

| Fold | Trades | Win rate | Gross PnL | Cost | Net PnL | Max drawdown |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 365 | 46.027397% | -19.88 | 94.90 | -114.78 | 165.32 |
| 1 | 356 | 43.258427% | -101.48 | 92.56 | -194.04 | 218.71 |
| 2 | 359 | 46.518106% | -48.81 | 93.34 | -142.15 | 165.47 |
| 3 | 372 | 48.387097% | 34.69 | 96.72 | -62.03 | 88.86 |
| 4 | 365 | 44.657534% | -52.09 | 94.90 | -146.99 | 158.94 |

Across folds there were **1,817 trades**: 832 net winners and 985 net losers,
for a trade-weighted win rate of **45.789763%**. The conditional simulator sums
are **-187.57 gross**, **472.42 cost** and **-659.99 net**. Only fold 3 was
positive before costs; every fold was negative after costs. Fold equity resets,
so drawdowns must not be added; the largest fold drawdown was **218.71**.

Exit reasons were 796 take-profit, 742 stop-loss and 279 target-change exits.
There were nine gapped exits, zero ambiguous exits and zero boundary-forced
closes. Direction was balanced: 926 long and 891 short trades.

The cache-bypassing double verification produced run ids
`01M0YE7CZWF8HEEJZZPK4EZ90P` and `01M0YE7D0Z053C9WSPF3AFQHRQ`.
All 70 feature, model, intent, trade, equity and metric comparisons were
structurally and exactly equal; maximum observed absolute deviation was `0.0`.

The since-run Trial Ledger examined the canonical run plus both verification
attempts and collapsed them to one distinct hypothesis. It recorded zero
Plane-A trials and nine Plane-B questions (the eight trade-summary metrics plus
drawdown), five folds each, zero noncanonical runs, zero pre-evaluation
failures, zero audit discrepancies and zero OOS/LOCKBOX events. All ten
fold-evaluator entries retain the mandatory unverified cost-semantic marking.

## Conclusion

TP400/SL400 is negative even before the provisional cost charge and becomes
substantially more negative after it. This result agrees with, rather than
overrides, the prior negative Plane-A evidence. It provides no research basis
for a TP/SL sweep, protected-data access or a claim of profitability.

Every PnL and drawdown number above is a conditional output of the unverified
simulator assumptions listed earlier. None is a verified broker/account money
estimate.
