# Zone Trader v13 — TP200/SL400 TRAIN follow-up

Status: frozen before execution; no TP200/SL400 result has been inspected.

## Owner request and research accounting

After the completed TP400/SL400 and TP800/SL400 studies, the owner requested
one additional fixed-bracket TRAIN-only follow-up: repeat the exact same 100
semantic Zone Trader parameter configurations with take profit 200 pips and
stop loss 400 pips.

This is a sequential follow-up informed by the existence of the earlier
fixed-bracket results, not part of their jointly preregistered 200-run family.
It adds 100 unique experiment configurations to the research denominator. No
result from this follow-up may be described as confirmatory or independent of
the earlier bracket studies.

## Frozen configuration

- sweep: `zone-trader-v13-train-plane-b-100-tp200-sl400-v1`;
- source vectors: exactly the 100 zipped vectors in
  `zone-trader-v13-train-plane-b-100-tp800-sl400-v1`;
- dataset: `XAUUSD_S_M1_TRAIN_V1` only;
- policy: `event_exposure_v1`;
- simulator: `bracket_single_position_v1`;
- take profit: 200 pips;
- stop loss: 400 pips;
- lot size: 0.01;
- spread: 20 pips;
- commission: 6.00 per lot, round turn;
- five 200,000-row evaluation folds with `fit_rows=385824`;
- supporting Plane A: H100 `sign_accuracy_v1` and `rank_ic_v1`;
- Plane B: `trade_summary_v1` and `equity_drawdown_v1`.

All other parameters, bounds, cost inputs and execution behavior are identical
to the prior TP800/SL400 100-child sweep. The binding remains permanently
`unverified-audit-input-v1`, with `cost_semantics=unverified` and
`execution_price_model=recorded_path_unverified`. Money values are conditional
research outputs, not verified broker-money estimates.

No VALIDATION, OOS or LOCKBOX data may be accessed. The full 100-child sweep
may be launched detached after the request file passes repository validation
and is frozen in a clean commit.
