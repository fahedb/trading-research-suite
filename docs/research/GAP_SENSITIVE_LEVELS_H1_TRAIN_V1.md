# Gap Sensitive Levels H1 — single TRAIN simulation

**Status:** COMPLETE · **Date:** 2026-10-03

## Owner instruction and methodology status

The owner supplied the Pine v6 indicator `Gap Sensitive Levels 1H+` and
explicitly requested one simulation through the existing experiment system.
This is a visible owner-directed Plane-B-first methodology exception. No Plane-A
edge has been established, so a favourable simulator result cannot be presented
as predictive evidence, cannot justify protected-data access, and cannot be used
to tune a follow-up parameter.

This family is XAUUSD TRAIN only. It contains one frozen configuration, no
sweep, and no access to VALIDATION, OOS, LOCKBOX, SNAPSHOT, RAW, or CURATED
market rows.

## Frozen Pine mapping

- Provider: `gap_sensitive_levels_h1_v1`, version 1.
- Model: `gap_sensitive_gate_v1`, version 1.
- Policy: `event_exposure_v1`, version 1.
- Arithmetic grid: closed H1 buckets, 3,600 seconds, phase zero, on the native
  broker-wall-clock axis. No UTC, session, weekend, holiday, or closure meaning
  is inferred.
- The display-only EMA plots, lines, labels, and alerts are omitted. Signal
  computation preserves the supplied defaults: minimum gap 0.10%, swing length
  5, RSI(14) at 30/70, EMA 20/50, and minimum confirmation score 2.
- The score preserves the Pine body literally: the mere existence of support or
  resistance contributes one point while the signal also requires that level.
  Therefore `min_confirm=2` means level existence plus at least one of the RSI
  or EMA conditions; it does not mean two additional confirmations.
- Only the latest bull gap and latest bear gap are active, matching the two
  scalar `var` pairs in the Pine source. A filled gap is cleared before the
  current bar's signal is evaluated.
- A pivot becomes available only after five right-hand H1 bars. Equal extrema
  are not pivots in this port; the extremum must be strict on both sides.
- `newLong` and `newShort` remain rising-edge events. If both rise on the same
  H1 bar, the provider records `gsl_conflict=1` and emits no direction because
  the supplied indicator defines two alerts but no executable ordering.

## Required finite-state bounds

The Pine source contains unbounded recursive and persistent state, which is not
representable by the accepted finite-reach FeatureProvider contract. The
following identity-bearing approximations are frozen before any real signal or
result is inspected:

- RSI Wilder/RMA finite window: 300 H1 bars;
- EMA finite window: 2,000 H1 bars;
- maximum active gap age: 2,000 H1 bars;
- maximum support/resistance age after pivot confirmation: 2,000 H1 bars.

The declared conservative reach is 2,013 H1 buckets, or **120,780 observed M1
rows**. The leading arithmetic bucket is discarded because it may begin before
the bounded context. These choices make the implementation causal and
leak-testable but mean the run is not a byte-for-byte TradingView reproduction:
an older active gap or level expires, and recursive indicators retain only the
frozen finite approximation.

## Frozen experiment and execution

- Config: `config/experiments/gap-sensitive-levels-h1-train-plane-b-v1.json`.
- Canonical config SHA-256:
  `b5c1de675ef4ee96f8fd2b0467ca355ee087c34dc4b41516419ba0057f2715b5`.
- Dataset: `XAUUSD_S_M1_TRAIN_V1`, full integrity verification.
- Bounds: context key 1577926800; evaluation keys 1588751700 through
  1703894160. The first evaluation key follows exactly 120,780 TRAIN rows of
  context; key selection used only the authorized data layer and inspected no
  price result.
- Five sequential folds: 3 fit rows, 258,751 evaluation rows, 258,751-row
  step, no embargo, and no purge override.
- Simulator: `bracket_single_position_v1`, fixed lot size 0.01, at most one
  position.
- There is no take profit and no stop loss. A sparse LONG or SHORT event becomes
  eligible at the next observed M1 row and fills at that row's recorded open.
  An opposite event closes and reverses at one observed open; otherwise the
  position remains open until an opposite event or the mandatory fold-boundary
  close. This is the minimum executable state mapping because the Pine indicator
  supplies entries but no exits.
- A completed H1 signal is exposed at the first observed row in a later H1
  bucket. The simulator's next-observed-row rule therefore adds one observed M1
  row after that exposure; this deliberate timing is not TradingView H1 order
  timing and remains visible in the protocol.
- Trade evaluators: `trade_summary_v1` and `equity_drawdown_v1`. No Plane-A
  evaluator is included.

## Mandatory cost and execution limitations

The cost binding is permanently `unverified-audit-input-v1`, with
`cost_semantics=unverified` and
`execution_price_model=recorded_path_unverified`. The conditional inputs are
0.01 price units per pip, contract size 100, 20 spread pips, and 6.00 commission
per lot per round turn. Their broker/account meanings, and the recorded OHLC
price side, remain unverified.

There is no slippage, financing/swap, latency, rejection, or broker constraint
model. Financing omission is especially material because an unbracketed trade
may remain open across native day boundaries. Every money-denominated result is
only a conditional output of these provisional assumptions, never an
authoritative real-money estimate.

## Frozen stopping rule

The run and its exact determinism verification are reported whether favourable,
unfavourable, or empty. No parameter, bracket, age bound, grid phase, fold,
instrument, or conflict rule is changed after inspecting the result. Any later
variant is a separately preregistered hypothesis and is not authorized by this
document.

## Canonical result

Canonical run `01M4110M4MMQ9E73DV2ZJMAS92` completed from clean commit
`47bb978e25e9e12682022c82f1a5724e74a78405`. Its experiment fingerprint is
`efbdea718f9477b2760c407f12166b2fb6557200f09d0e8440a000e88f69b31e`.
The run was evaluated on TRAIN only; `oos_touched=false` and
`lockbox_touched=false`.

| Fold | LONG intents | SHORT intents | Trades | Gross PnL | Provisional cost | Conditional net PnL | Win rate | Max drawdown |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1 | 0 | 1 | -95.52 | 0.26 | -95.78 | 0% | 132.86 |
| 1 | 0 | 44 | 1 | 46.69 | 0.26 | 46.43 | 100% | 239.23 |
| 2 | 0 | 0 | 0 | undefined | undefined | undefined | undefined | 0.00 |
| 3 | 12 | 0 | 1 | 116.95 | 0.26 | 116.69 | 100% | 74.89 |
| 4 | 72 | 0 | 1 | 57.74 | 0.26 | 57.48 | 100% | 260.31 |

Across folds there were 129 sparse intents: 85 LONG, 44 SHORT, and zero
suppressed same-bar conflicts. Repeated same-direction intents did not create
additional positions. The four resulting trades comprised three conditional
winners and one loser, for a simple trade win rate of 75%. Summing the four
closed trades gives gross PnL 125.86, provisional cost 1.04, and conditional
net PnL 124.82. The largest individual-fold drawdown was 260.31; fold
drawdowns are not additive.

All four trades were mandatory fold-boundary closes. There were no
opposite-event exits, ambiguous exits, or gapped exits. Consequently, the
positive aggregate is dominated by four long-duration directional exposures
and artificial research boundaries, not by repeated completed signal cycles.
The sample is far too small and structurally weak to establish an edge or to
support a profitability claim. It is principally evidence that the frozen port
and the existing simulator execute end to end.

## Determinism and Trial Ledger

Cache-bypassing verification runs `01M4113QZC0J0JZ2K6894A185H` and
`01M4113R0Y2A9ZA4CM50571Z1W` reproduced the feature artifact, all fold model
outputs, intents, trade logs, equity curves, and evaluator results exactly.
Structural equality passed and the maximum observed absolute deviation was
0.0.

The since-run mechanical Trial Ledger examined the canonical run and both
verification attempts. It records one distinct hypothesis, nine Plane-B
questions, zero Plane-A questions, two attempts collapsed, no failures or
audit discrepancies, and zero OOS or LOCKBOX access events. Cost semantics
and recorded-path execution remain unverified exactly as frozen above.
