# Multi Confluence M15 V1

**Status:** COMPLETE — implementation verified; TRAIN Plane A found no
consistent positive directional evidence.

## Purpose

This plugin is the single-symbol, asset-agnostic research port of the supplied
Pine v6 `Multi Confluence Crypto Indicator`. It is designed to run first on the
registered XAUUSD M1 TRAIN view and later on a separately ingested BTC M1 view
without changing strategy code.

The implementation is intentionally a Plane-A event forecast. The supplied
indicator contains no exit or trade-management rule, so this version does not
invent one.

## Owner-approved semantic freeze

The owner approved the following result-bearing decisions on 2026-09-05:

- the strategy bar is a closed, arithmetic M15 bar;
- the two cross-asset `TOTAL` and `BTC` context votes are omitted in V1;
- the remaining five votes are S/R, RSI, EMA, trendline and liquidity;
- the event threshold is four of five votes, with the winning side required to
  have a strictly higher score than the other side;
- RSI and ATR use a finite 300-M15-bar recursive window;
- both EMAs use a finite 2,000-M15-bar recursive window;
- an S/R pivot remains active for at most 52 completed bars of its own
  timeframe after confirmation;
- each trendline pivot remains active for at most 512 completed M15 bars after
  confirmation;
- only completed base and higher-timeframe bars are usable;
- M15, H4, H6, H8 and H12 grids have phase zero in the source clock domain;
- the weekly grid starts at arithmetic phase 345,600 seconds, corresponding to
  Monday 00:00 in the same native wall-clock encoding;
- observed-row gaps use `GapPolicy.CROSS` and do not reset indicator state.

## Platform mapping

| Stage | Registered component | Responsibility |
|---|---|---|
| FeatureProvider | `multi_confluence_m15_v1` | Aggregate M1 to closed M15/HTF bars, compute all causal indicator state and emit signed first-transition events plus long/short scores |
| ResearchModel | `multi_confluence_gate_v1` | Validate the three feature columns and expose signed events as sparse generic `score` rows |
| DecisionPolicy | existing `threshold_v1` | Map positive/negative generic scores to intent; no new policy is required |
| Plane A | existing forward-return outcomes and evaluators | Evaluate event direction at the frozen 240-row primary horizon and 60/720-row diagnostics |

The feature provider consumes only `high`, `low` and `close`. No symbol name,
venue, tick size, price scale or external feed is embedded in its arithmetic.

## Pine-to-platform arithmetic

- Pivots use the supplied default `left=5`, `right=5` and become usable only on
  the confirmation bar, never on the pivot bar itself.
- S/R uses the latest still-active confirmed low/high pivot on W, H12, H8, H6
  or H4 and the M15 ATR proximity radius of `0.60`.
- RSI keeps the supplied conditions: long is `RSI <= 45` or rising above 50;
  short is `RSI >= 55` or falling below 50. The two RSI votes are deliberately
  not forced to be mutually exclusive.
- EMA votes retain `close > EMA50 > EMA200` and
  `close < EMA50 < EMA200`.
- Trendlines connect the two latest still-active confirmed M15 pivots. No slope
  restriction is added because the Pine source adds none.
- Liquidity sweeps retain the previous-20-bar, current-bar-excluded low/high
  test and close-back-through condition.
- A signed event is emitted only on the first bar where a side's qualifying
  state changes from false to true, matching `newLong` / `newShort`.

## Information reach and event timing

The default declared reach is 645,120 prior M1 observations. The weekly branch
dominates it:

`(52 active bars + 5 left + 5 right + 2 boundary bars) * 10,080 M1 rows`.

The extra boundary allowance prevents a truncated leading weekly bucket from
entering a pivot. Every stateful branch has an explicit expiry or finite
window, so rows older than the declaration cannot influence output.

An M15 bar becomes usable at the first already-observed M1 row belonging to a
later M15 bucket. Event output is sparse at that first usable row; long and
short score diagnostics are carried forward from the latest completed M15 bar.

## Deliberate differences from the supplied indicator

- No `CRYPTOCAP:TOTAL` or separate `BTCUSDT` feed is read. V1 has one dataset
  and one symbol per experiment.
- The score denominator and threshold are 5 and 4 instead of 7 and 5.
- Recursive indicators and remembered pivots have the approved finite bounds.
- Developing HTF values are not used, so research output does not repaint.
- Plots, labels, background coloring, dashboard cells and alert text are UI
  concerns and are not research-stage outputs.

## Frozen TRAIN Plane-A protocol

The owner approved the final result-bearing choices before execution on
2026-09-05. The frozen config is
`config/experiments/multi-confluence-m15-train-plane-a-v1.json`, whose canonical
SHA-256 is
`400474e29edf34c494595691b88b1e8d7b2d712e196875c9a19acc79c8b3eac1`.

- Dataset: `XAUUSD_S_M1_TRAIN_V1` only.
- Context starts at native key `1577926800`. A keys-only load through the
  platform data layer selected native key `1635374280` as the first evaluation
  boundary with all 645,120 declared prior observations available.
- Primary outcome: 240 observed M1 rows (approximately four continuous-market
  hours), with diagnostics at 60 and 720 observed rows. Every outcome uses a
  one-row decision delay, `close` reference and `nan` boundary policy.
- Five contiguous evaluation folds contain 153,883 rows each. Because
  `multi_confluence_gate_v1` is stateless and consumes no training target, each
  rolling fold has only the platform-required three-row technical fit span.
- The evaluators are `sign_accuracy_v1` and `rank_ic_v1`. The policy is the
  zero-threshold `threshold_v1` mapping of sparse signed score rows.
- This is Plane A only: no execution, fill, spread, cost, TP, SL or money
  semantics are configured.

Observed-row horizons are not promises about elapsed wall-clock time when M1
observations are missing. The 240-row result is primary; the 60- and 720-row
results are diagnostics and may not be used to redefine the primary question
after inspection.

## TRAIN result

Canonical run `01M1SWXWFNXDHMC98HKVNCZYQN` completed from clean commit
`b4fda7a857663b7f26d6eaf746a13ec0fd4f501f`. Its experiment fingerprint is
`41758667a66a4a6507c25d2fcc7d8049a73aa614730f13e8142d027f5a96ec4c`.
The run recorded all 30 expected Plane-A evaluation entries, remained
canonical, and touched neither OOS nor LOCKBOX.

The five folds emitted 38, 36, 30, 47 and 46 sparse events respectively: 197
in total, split into 100 positive and 97 negative events.

| Fold | Signals | H240 accuracy | H240 Rank IC | H60 accuracy | H60 Rank IC | H720 accuracy | H720 Rank IC |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 38 | 0.5789473684210527 | 0.11534996014569447 | 0.5526315789473685 | 0.11534996014569447 | 0.39473684210526316 | -0.1826374368973496 |
| 1 | 36 | 0.5000000000000000 | -0.08406827962379276 | 0.4444444444444444 | -0.05694947974514994 | 0.5555555555555556 | -0.08406827962379276 |
| 2 | 30 | 0.43333333333333335 | 0.003995835844941255 | 0.5333333333333333 | 0.059937537674118815 | 0.5333333333333333 | 0.1318625828830614 |
| 3 | 47 | 0.40425531914893614 | -0.2927451592441275 | 0.3829787234042553 | -0.3735024445528523 | 0.3404255319148936 | -0.4172459740950783 |
| 4 | 46 | 0.5434782608695652 | 0.2482005486031741 | 0.6521739130434783 | 0.3599729810867227 | 0.5217391304347826 | 0.12985326715000498 |

Across the 197 effective observations, the descriptive effective-sample
weighted summaries are:

| Horizon | Correct direction | Accuracy | Weighted fold Rank IC |
|---:|---:|---:|---:|
| 60 | 101/197 | 0.5126903553299492 | 0.015915662847409862 |
| **240 primary** | **97/197** | **0.49238578680203043** | **-0.00439127794060861** |
| 720 | 91/197 | 0.4619289340101523 | -0.09973661764280424 |

These weighted numbers are descriptive summaries, not additional registered
estimators. The small event count and the variation between folds preclude a
significance or independence claim.

Cache-bypassing double verification produced run IDs
`01M1SWYNH71NBPJ18VYK3YQ0R6` and `01M1SWYNJFHW1TZCX3BZWXC59Y`. All 49 compared
feature, outcome, state, model-output, policy-intent and metric artifacts were
structurally and exactly equal, with maximum absolute deviation `0.0`.
Incremental index rebuild found three runs with zero scan errors. The since-run
Trial Ledger collapsed the two verification attempts, recorded one distinct
hypothesis and six Plane-A trials, and found zero Plane-B trials, noncanonical
runs, pre-evaluation failures, discrepancies, or OOS/LOCKBOX access events.

## Conclusion and stop

The primary H240 result does not show positive directional information: its
aggregate accuracy is below 0.5, two folds are below 0.5, one is exactly 0.5,
and the weighted fold Rank IC is slightly negative. H60 is marginally above
0.5 in aggregate but reverses materially between folds; H720 is negative in
aggregate. The frozen study therefore provides no basis for a Plane-B TP/SL or
cost simulation. No parameter change, secondary-horizon promotion or protected
data access is authorized by this result.
