# Zone Trader v13 — per-trade bracket TRAIN qualification

Status: **COMPLETE — infrastructure qualification GREEN (2026-08-22).**

## What this family is, and what it is not

This is an **infrastructure qualification of the per-trade bracket path**, not a
performance hypothesis. Its question is operational: does a bracket chosen by a
DecisionPolicy travel intact from model output, through
`bracketed_exposure_intent_v1`, through `bracket_single_position_v1`, into a
recorded `TradeLog`, and out to the Explorer?

**The bracket is deliberately fixed and is not the Zone Trader stop or target.**
Take profit 8.00 and stop loss 4.00 price units are configured constants. They
were chosen for exactly one reason: on the frozen instrument binding
`price_units_per_pip = 0.01`, so 8.00 and 4.00 are exactly the researched
TP800/SL400 geometry, which makes an equivalence check against the completed
fixed-bracket family possible.

**No result from this family is evidence that 8.00/4.00 is a good bracket, or
that per-trade brackets improve anything.** The signal is byte-for-byte the
signal `event_exposure_v1` already produces; only the transport changed. The
real Zone Trader stop and target are the per-event ATR/zone-edge and R-multiple
quantities the provider already computes as `zt_stop_price` and
`zt_target_price`, which `zone_event_gate_v1` does not yet pass through to model
output. Reaching them is separate future work and is not attempted here.

## Frozen configuration

- sweep: `zone-trader-v13-train-per-trade-bracket-qualification-5-v1`;
- five children, the exact ordinals 0–4 zipped vectors of
  `zone-trader-v13-train-plane-b-qualification-5-tp800-sl400-v1`, so the signal
  configurations are identical to the already-researched family;
- dataset: `XAUUSD_S_M1_TRAIN_V1` only;
- feature: `zone_trader_v13`; model: `zone_event_gate_v1` — **both unchanged**;
- policy: `zone_fixed_bracket_exposure_v1`, parameters
  `take_profit_distance = 8.0` and `stop_loss_distance = 4.0`, in **price
  units**;
- intent schema: `bracketed_exposure_intent_v1`;
- simulator: `bracket_single_position_v1` — one identity, unchanged semantics;
- **`execution.take_profit_pips = null` and `execution.stop_loss_pips = null`**;
- lot size 0.01, spread 20 pips, commission 6.00 per lot round turn,
  `contract_size = 100`, `account_currency_per_quote_currency = 1`;
- five 200,000-row evaluation folds with `fit_rows = 385824`;
- supporting Plane A: `sign_accuracy_v1` and `rank_ic_v1`;
- Plane B: `trade_summary_v1` and `equity_drawdown_v1`.

### The null global brackets are the point

Both `ExecutionSpec` bracket fields are `null` **deliberately and mandatorily**.
The simulator resolves each side per opening instruction — instruction first,
then the fixed execution value, then no bracket — so leaving a global value set
would let a run pass while silently exercising the old path. With both sides
null there is no fallback to reach: every bracket on every trade must have
arrived through the intent, or there is no bracket at all.

## Evidence status

The binding remains permanently `unverified-audit-input-v1`, with
`cost_semantics = unverified` and
`execution_price_model = recorded_path_unverified`. Money values are conditional
research outputs, not verified broker-money estimates.

No VALIDATION, OOS or LOCKBOX data may be accessed.

## Recorded qualification outcome

The frozen five-child sweep completed successfully from clean platform commit
`f25b96f707823cc3c2f77f4e47b381e62a24458a`: **5 COMPLETE, 0 failed, 0
interrupted**. The canonical first attempts were:

| Ordinal | Run id | Recorded intents | Recorded trades |
|---:|---|---:|---:|
| 0 | `01M0N77DSDDH4QVEPZMTDHTRKG` | 140 | 119 |
| 1 | `01M0N77DTTND5AZ15N1FZ98GZT` | 1,059 | 890 |
| 2 | `01M0N77DW17X25S7ARJQXQ5N9W` | 125 | 120 |
| 3 | `01M0N77DX9489H3F11ZXRRR4XW` | 1,189 | 987 |
| 4 | `01M0N77DYJKTP5PP8H9RSKYHG0` | 263 | 234 |

Artifact inspection covered every fold of every child. All 2,776 recorded
instructions use `bracketed_exposure_intent_v1`; every instruction carries
exactly `take_profit_distance = 8.0` and `stop_loss_distance = 4.0`. All 2,350
trades use TradeLog format version 2 and carry recorded take-profit and stop-loss
levels on the correct side of their actual entry. Reconstructing the distance
from stored binary floating-point prices differs from 8.0/4.0 by at most
`2.2737367544323206e-13`; the intent distances themselves differ by exactly
zero. Both global ExecutionSpec bracket fields remained null, so the old global
fallback could not have supplied these levels.

Two complete determinism verifications of ordinal 0 produced four canonical
verification attempts. All four reports passed all 82 artifacts with maximum
observed absolute deviation `0.0`.

The disposable SQLite index was fresh with 348 runs and zero scan errors when
checked. After reloading the read-only Explorer service, its API returned this
family, opened run `01M0N77DSDDH4QVEPZMTDHTRKG`, and transcribed all 119 trades
as TradeLog version 2 with `bracket_mode = per_trade`, `bracket_source =
recorded`, and distances 8.0/4.0. The fixed-bracket transport is therefore
qualified end to end, including presentation.


## Equivalence against the completed global TP800/SL400 runs

The frozen fixture test proves per-trade and global brackets agree on a
hand-built path. This is the same comparison against **real runs**, which is the
stronger statement because it exercises the whole Zone Trader chain on TRAIN
data rather than a synthetic path.

The five children were compared fold-for-fold and trade-for-trade against the
already-completed `zone-trader-v13-train-plane-b-qualification-5-tp800-sl400-v1`
runs, which use the identical signal vectors with the bracket supplied globally
by the `ExecutionSpec` instead of by the intent:

| Ordinal | New (per-trade, globals null) | Old (global TP800/SL400) |
|---:|---|---|
| 0 | `01M0N77DSDDH4QVEPZMTDHTRKG` | `01M0G3K2Y33X40VYY7D1C8D2AR` |
| 1 | `01M0N77DTTND5AZ15N1FZ98GZT` | `01M0G3K304NX6KS9F51D9GG40W` |
| 2 | `01M0N77DW17X25S7ARJQXQ5N9W` | `01M0G3K326CPD71GCSKYJBTC9R` |
| 3 | `01M0N77DX9489H3F11ZXRRR4XW` | `01M0G3K34BQ4FMJR9ZVHARFWE1` |
| 4 | `01M0N77DYJKTP5PP8H9RSKYHG0` | `01M0G3KB794W3TRD5BNYV58R3Q` |

Across **25 folds, 25 equity curves and all 2,350 trades**, every one of the
twelve original TradeLog columns is **bitwise identical**: entry key, exit key,
direction, entry price, exit price, exit reason, ambiguity flag, gap flag, gross
PnL, spread cost, commission cost and net PnL. Every equity curve's keys and
values are bitwise identical. As a readable cross-check, the summed net PnL over
all 2,350 trades is exactly `-538.3099999999971` in both modes — the same binary
float, not a rounded agreement. That figure is a conditional research value under
an unverified cost binding and is quoted here only as an equality witness.

The recorded levels also equal exactly what the old mode could only derive from
`entry ± direction × pips × price_units_per_pip`.

The only differences are provenance: `intent_schema_id`
(`bracketed_exposure_intent_v1` versus `target_exposure_intent_v1`),
`format_version` (2 versus 1), the policy origin
(`zone_fixed_bracket_exposure_v1` versus `event_exposure_v1`) and the now-null
global bracket fields.

**This is the load-bearing result of the whole qualification.** Given the same
geometry, routing the bracket through the DecisionPolicy and the intent changes
nothing about what was traded. It also means these five runs carry no
independent research information: they reproduce an existing study exactly, and
must never be reported as confirming it.

This is transport evidence only. It does not promote 8.0/4.0 to a researched
strategy choice and does not authorize a protected-data evaluation.

## Research accounting

These five children are ordinary runs and count in the Trial Ledger like any
others. They are **not** a new hypothesis about Zone Trader: they re-execute five
already-researched signal configurations under an equivalent bracket geometry in
order to test plumbing. Any Plane-A or Plane-B number they produce is a
duplicate of an existing question, not an independent one, and must not be
reported as confirmatory.
