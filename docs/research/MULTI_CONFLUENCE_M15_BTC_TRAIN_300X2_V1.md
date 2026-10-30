# Multi Confluence M15 — BTC 300 × 2 TRAIN sweep

**Status:** SUPERSEDED — completed with four folds due to a geometry error.
The request and methodology record are retained; the 600 run directories were
deleted with explicit owner approval on 2026-09-10 to recover 35.21 GiB.

**Date:** 2026-09-09

## Scope

This is the owner-approved BTC projection of the completed XAUUSD exploratory
sweep. It preserves the exact ordered set of 300 Multi Confluence M15 strategy
vectors, the model, threshold policy, non-interruptible bracket simulator and
five-fold TRAIN-only design. It does not authorize VALIDATION, OOS or LOCKBOX.

The original frozen request is
`config/sweeps/multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-v1.json`.
Its deterministic generator is `scripts/generate_multi_confluence_sweep.py`.

Post-run inspection showed that fold generation consumes the three configured
fit rows before each first evaluation window. With 279,421-row evaluation
windows, only four complete folds fit. The request had been intended and
documented as five-fold, so its 600 successful children are methodologically
superseded. The original configuration and sweep specification remain preserved
in Git, while the bulky run directories were later removed under the storage
decision recorded above. The corrected successor is
`mc-m15-btc-train-300x2-tp18000-tp36000-sl22500-5fold-v2`.

## Data and folds

- Dataset: `BTCUSD_S_M1_TRAIN_V1`, manifest
  `73454e8dacf36b233144f1d7b7866ec30cf0c60f7a47efd366c43bc63b632b9b`.
- Split policy: ADR-0012 `btc-v1`, digest
  `3896cd9bedfe9357279c02df4904cda92d698b2d079c65e7bed6455503d55899`.
- The 2,042,228 admitted TRAIN rows supply 645,120 prior context rows and five
  contiguous evaluation folds of 279,421 rows. Three unused tail rows prevent
  unequal folds.
- Bounds are `1577923200`, `1619598600`, and `1704066960` for context start,
  evaluation start, and evaluation end respectively.

## BTC execution projection

The MT5 `BTCUSD.s` specification supplied by the owner records two digits,
point and tick size `0.01`, tick value `0.01`, contract size `1.0`, minimum and
step volume `0.01`, floating spread and a current spread snapshot of 600 points.
The sweep therefore uses lot size `0.01`, `price_units_per_pip=0.01`,
`contract_size=1.0`, and fixed provisional `spread_pips=600`.

The XAU bracket distances were scaled by the ratio of median finite M15
ATR(14) on the two TRAIN views (BTC/XAU approximately 45.13), then rounded to
simple quote-price distances. The two blocks are TP $180 / SL $225 and
TP $360 / SL $225, encoded as 18,000/22,500 and 36,000/22,500 pips.

No commission figure was supplied, so `commission_per_lot=0` is an explicit
provisional input, not a verified claim. The spread is one live floating-spread
snapshot rather than a historical execution record. The platform therefore
retains `cost_semantics=unverified` and
`execution_price_model=recorded_path_unverified`; all monetary results are
conditional simulator outputs, not real-money estimates.
