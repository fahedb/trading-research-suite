# Zone Trader v13 — ZT100-047 local TRAIN neighbourhood

Status: frozen before execution; no result from this batch has been inspected.

## Purpose and provenance

This is an adaptive, exploratory TRAIN-only follow-up to the completed fixed-
bracket batch documented in
`ZONE_TRADER_PLANE_B_FIXED_BRACKETS_V1_RESULTS.md`. It tests whether the
high-sample but fold-unstable `ZT100-047` result belongs to a local parameter
region or is an isolated spike. It is not a confirmation study.

The reference configuration is FVG-only, `minFvgATR=0.20`,
`rejection_wick`, `minWick=0.20`, `maxBarsWait=2`, `breakBuf=0.50`,
and `maxZones=20`. The reference itself is intentionally omitted because it
already exists as a completed hypothesis. The 35 rows below are new semantic
configurations.

## Fixed semantics

- TRAIN dataset only; no VALIDATION, OOS or LOCKBOX;
- FVG zones only; Volume and Demand/Supply remain disabled;
- M5 strategy bars, approved H1 anchor, tick volume, `w_atr=300`, and
  `max_zone_age_rows=5000`;
- `rejection_wick` confirmation only;
- existing `event_exposure_v1` and `bracket_single_position_v1`;
- TP 800 pips, SL 400 pips, 0.01 lot, and the same permanently unverified
  spread/commission binding as the prior Plane-B batch;
- the H100 Plane-A outcome is retained only as a supporting evaluation;
- five existing 200,000-row evaluation folds and `fit_rows=385824`;
- no change to state-following reversal semantics.

The prior batch's event-to-trade caveat remains: a positive result here would
still require a separate semantic review because opposite target changes
closed 19.60% of `ZT100-047` trades.

## Deterministic selection algorithm

1. Enumerate the ordered Cartesian core
   `minFvgATR=[0.10,0.20,0.30]` ×
   `minWick=[0.10,0.20,0.30]` ×
   `maxBarsWait=[1,2,3]`, last dimension varying fastest.
2. Keep `breakBuf=0.50` and `maxZones=20`.
3. Remove the already-completed reference tuple `(0.20,0.20,2)`, leaving
   26 new core configurations.
4. Add four one-factor break-buffer spokes
   `[0.00,0.25,0.75,1.00]` at the reference values.
5. Add five one-factor capacity spokes
   `maxZones=[5,10,15,30,50]` at the reference values.

Frozen total: **35 generated and 35 unique new semantic configurations**.
The five-child qualification request is an exact prefix of the full request
and adds no hypothesis when reused.

## Frozen rows

| Ordinal | Label | Region | minFvgATR | minWick | maxBarsWait | breakBuf | maxZones |
|---:|---|---|---:|---:|---:|---:|---:|
| 0 | ZT047-N01 | core | 0.1 | 0.1 | 1 | 0.5 | 20 |
| 1 | ZT047-N02 | core | 0.1 | 0.1 | 2 | 0.5 | 20 |
| 2 | ZT047-N03 | core | 0.1 | 0.1 | 3 | 0.5 | 20 |
| 3 | ZT047-N04 | core | 0.1 | 0.2 | 1 | 0.5 | 20 |
| 4 | ZT047-N05 | core | 0.1 | 0.2 | 2 | 0.5 | 20 |
| 5 | ZT047-N06 | core | 0.1 | 0.2 | 3 | 0.5 | 20 |
| 6 | ZT047-N07 | core | 0.1 | 0.3 | 1 | 0.5 | 20 |
| 7 | ZT047-N08 | core | 0.1 | 0.3 | 2 | 0.5 | 20 |
| 8 | ZT047-N09 | core | 0.1 | 0.3 | 3 | 0.5 | 20 |
| 9 | ZT047-N10 | core | 0.2 | 0.1 | 1 | 0.5 | 20 |
| 10 | ZT047-N11 | core | 0.2 | 0.1 | 2 | 0.5 | 20 |
| 11 | ZT047-N12 | core | 0.2 | 0.1 | 3 | 0.5 | 20 |
| 12 | ZT047-N13 | core | 0.2 | 0.2 | 1 | 0.5 | 20 |
| 13 | ZT047-N14 | core | 0.2 | 0.2 | 3 | 0.5 | 20 |
| 14 | ZT047-N15 | core | 0.2 | 0.3 | 1 | 0.5 | 20 |
| 15 | ZT047-N16 | core | 0.2 | 0.3 | 2 | 0.5 | 20 |
| 16 | ZT047-N17 | core | 0.2 | 0.3 | 3 | 0.5 | 20 |
| 17 | ZT047-N18 | core | 0.3 | 0.1 | 1 | 0.5 | 20 |
| 18 | ZT047-N19 | core | 0.3 | 0.1 | 2 | 0.5 | 20 |
| 19 | ZT047-N20 | core | 0.3 | 0.1 | 3 | 0.5 | 20 |
| 20 | ZT047-N21 | core | 0.3 | 0.2 | 1 | 0.5 | 20 |
| 21 | ZT047-N22 | core | 0.3 | 0.2 | 2 | 0.5 | 20 |
| 22 | ZT047-N23 | core | 0.3 | 0.2 | 3 | 0.5 | 20 |
| 23 | ZT047-N24 | core | 0.3 | 0.3 | 1 | 0.5 | 20 |
| 24 | ZT047-N25 | core | 0.3 | 0.3 | 2 | 0.5 | 20 |
| 25 | ZT047-N26 | core | 0.3 | 0.3 | 3 | 0.5 | 20 |
| 26 | ZT047-N27 | break-spoke | 0.2 | 0.2 | 2 | 0.0 | 20 |
| 27 | ZT047-N28 | break-spoke | 0.2 | 0.2 | 2 | 0.25 | 20 |
| 28 | ZT047-N29 | break-spoke | 0.2 | 0.2 | 2 | 0.75 | 20 |
| 29 | ZT047-N30 | break-spoke | 0.2 | 0.2 | 2 | 1.0 | 20 |
| 30 | ZT047-N31 | capacity-spoke | 0.2 | 0.2 | 2 | 0.5 | 5 |
| 31 | ZT047-N32 | capacity-spoke | 0.2 | 0.2 | 2 | 0.5 | 10 |
| 32 | ZT047-N33 | capacity-spoke | 0.2 | 0.2 | 2 | 0.5 | 15 |
| 33 | ZT047-N34 | capacity-spoke | 0.2 | 0.2 | 2 | 0.5 | 30 |
| 34 | ZT047-N35 | capacity-spoke | 0.2 | 0.2 | 2 | 0.5 | 50 |

## Interpretation rule

The primary question is local robustness, not the maximum PnL. A useful
region should show similar net performance across neighbouring core cells,
adequate trade counts, and improvement in more than two of five folds.
Lifecycle spokes are diagnostic one-factor comparisons and must not be pooled
with the core as if they were a balanced factorial design.

If all configurations complete, this follow-up adds 35 hypotheses, 70 Plane-A
questions and 315 Plane-B questions; the two planes remain separate in the
Trial Ledger. Nearby configurations are correlated; no effective-independent-
trial estimate is claimed.
