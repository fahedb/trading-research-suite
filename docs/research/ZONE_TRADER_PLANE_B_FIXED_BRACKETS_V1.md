# Zone Trader v13 — fixed-bracket TRAIN Plane-B batch

Status: frozen before execution; no Plane-B result has been inspected.

## Research family

This exploratory TRAIN-only family applies the existing
`bracket_single_position_v1` simulator to the exact 100 semantic Zone Trader
parameter configurations frozen in
`ZONE_TRADER_EXPLORATORY_200_H50_H100_V1.md`. It tests two owner-approved,
fixed execution specifications:

- `TP400_SL400`: take profit 400 pips, stop loss 400 pips;
- `TP800_SL400`: take profit 800 pips, stop loss 400 pips.

The arithmetic is 100 signal configurations times two execution
specifications = **200 unique experiment configurations**. The two brackets
are frozen together before either produces a result. There is no adaptive
resampling and no TP/SL sweep beyond these two predeclared choices.

The H50/H100 OutcomeSpec distinction is not a Plane-B dimension and would
produce duplicate simulations. These runs therefore retain H100 only as a
supporting Plane-A evaluation; Plane B is identified by policy, execution
specification and trade evaluator.

## Fixed execution semantics

- policy: `event_exposure_v1`, sparse signed Zone Trader events;
- simulator: `bracket_single_position_v1`, one open position at a time;
- entry: next observed M1 row's open;
- fixed lot size: `0.01`;
- `price_units_per_pip=0.01`, so 400 pips = 4.00 price units and 800 pips =
  8.00 price units;
- `contract_size=100` and `account_currency_per_quote_currency=1`;
- spread: 20 pips;
- commission: 6.00 per lot, round turn, equivalent to 0.06 for a completed
  0.01-lot trade;
- no slippage, financing, latency, rejection or broker-distance modelling;
- same-bar TP/SL ambiguity resolves to stop loss; gap exits use the observed
  open; any remaining position is forcibly closed at the fold boundary.

The binding is permanently labelled `unverified-audit-input-v1`,
`cost_semantics=unverified`, and
`execution_price_model=recorded_path_unverified`. Money results are conditional
on these owner-approved exploratory assumptions and are not verified broker
estimates.

This fixed-bracket simulator is deliberately not represented as the canonical
Pine execution. The source strategy's per-event ATR/zone-edge stop and 2R
target are not representable by V1 Plane B.

## Fixed evaluation and data boundary

- dataset: `XAUUSD_S_M1_TRAIN_V1` only;
- no VALIDATION, OOS or LOCKBOX access;
- exact prior 100 zipped Zone Trader vectors, no inactive-parameter splits;
- five 200,000-row evaluation folds with the corrected 28,714-row reach and
  `fit_rows=385824`;
- supporting Plane A: H100 `sign_accuracy_v1` and `rank_ic_v1`;
- Plane B: `trade_summary_v1(min_effective_trades=1)` and
  `equity_drawdown_v1(min_effective_rows=1)`.

Full sweeps:

- `zone-trader-v13-train-plane-b-100-tp400-sl400-v1`;
- `zone-trader-v13-train-plane-b-100-tp800-sl400-v1`.

Each has a five-child qualification subset containing ordinals 0–4 with
identical experiment fingerprints to the corresponding full sweep. Completed
qualification children must be reused, not attempted twice.

