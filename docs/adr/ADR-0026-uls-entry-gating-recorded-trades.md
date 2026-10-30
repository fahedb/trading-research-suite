# ADR-0026 — ULS + PUSH entry gating from recorded TRAIN trades

**Status:** ACCEPTED · **Date:** 2026-08-26 · **Owner approval:** the owner
explicitly requested corpus extraction, training, one frozen VALIDATION run and
a simultaneous comparison with the best fixed TRAIN configuration.

## Context

The 200 × 4 ULS + PUSH search produced many repeated market episodes. Simply
concatenating all 800 runs would count the same setup four times for its bracket
copy and would let popular timestamps dominate the learner. Training only on
the 1,848-trade TRAIN winner would instead condition the model on a selected
maximum and provide too little independent evidence.

The current simulator also records only executed trades. A position can close
because the source strategy changes target, so its recorded result depends on
the later signal path of that exact configuration. Events suppressed while a
position was open have no recorded counterfactual result. Those limitations
must remain visible; they cannot be repaired by assigning an invented zero or
loss.

## Decision

### Corpus and target

Use exactly children 200–399 of frozen sweep
`uls-push-v7-train-owner-200x4-brackets-v1`: the 200 unique strategy vectors at
TP800/SL800. The other three bracket blocks are not additional observations.
Only canonical COMPLETE TRAIN artifacts are accepted and every consumed file
hash is recorded.

Each retained sample is one actually executed opening intent matched to its ULS
feature row and TradeLog result. The generic model target is minted by
`recorded_trade_net_pnl_v1` from the recorded provisional `net_pnl`. The 344
`BOUNDARY_FORCED_CLOSE` rows are excluded because their outcome is a fold
artifact. No unexecuted event is imputed. Costs remain permanently
`unverified`; this target and every later result are conditional simulator
evidence, not verified money.

An exact sample is identified by ULS FeatureOrigin, intent key and direction;
exact duplicates are refused. Samples with the same intent key and direction
under different strategy configurations remain legitimate conditional
variants, but they always occupy the same chronological split. Their weights
sum to one per market episode, preventing a frequently repeated timestamp from
receiving extra influence.

### Inputs

The model receives only information available at the completed M5 event:

- all 22 `uls_push_v7` event/source/score/geometry/ATR/volume/confirmation
  fields, normalized by entry and signal ATR with explicit missingness flags;
- fixed direction-neutral market context from `uls_market_context_v1`: ATR,
  volume z-score and ratio, short/long true-range ratio, 1/3/12/48-bar returns,
  EMA distance and slope, candle range/body/wicks, 48-bar room and close
  location; and
- the complete identity-bearing ULS configuration, with categorical values
  one-hot encoded.

The context provider uses only completed M5 bars and declares a finite
2,569-row M1 reach. It contains no exit, future path, PnL, run rank, fold number
or validation value. Native-clock session/hour claims remain excluded.

### Fit, calibration and internal test

Unique decision keys are split chronologically 70% / 15% / 15%. An exit that
reaches the next boundary purges its earlier sample. A deterministic 64-tree
regression forest (`max_depth=8`, `min_samples_leaf=96`,
`max_features=0.75`, no bootstrap, seed `20260826`, one thread) fits only the
first span.

The gate cutoff is selected once on the middle span for the declared deployment
configuration, child 344. Candidates are its prediction quantiles from 0% to
95% in 5-point increments, plus zero and an all-pass boundary. A candidate must
retain at least 25 rows and 5% of deployment calibration rows. The primary
ordering is greatest recorded total provisional net PnL, then more retained
rows, then the lower cutoff. The last 15% is reported once and is not used to
change the forest or cutoff. The immutable fitted state is the exact 70%-fit
state; there is no refit after seeing the internal test.

### Frozen VALIDATION comparison

After the carrier, corpus, model, cutoff and request code pass the full gate at
a clean commit, create one immutable two-child VALIDATION sweep. Both children
share the exact dataset, bounds, features, frozen state, TP800/SL800 execution
and provisional costs:

1. `model_gate` uses generic output `score`, which abstains below the frozen
   cutoff.
2. `fixed_best_baseline` uses generic output `value_0`, the untouched signed ULS
   event. It therefore reproduces the selected fixed child-344 strategy without
   using the model estimate.

The two children run concurrently. Primary success requires model provisional
net PnL greater than baseline and at least 30 model trades. Gross PnL, cost,
trade count, win rate and drawdown are descriptive secondary measurements. No
tuning follows this VALIDATION read. OOS and LOCKBOX remain prohibited.

## Consequences

The experiment answers a narrow feasibility question with one deployable,
row-local gate and a paired baseline. It does not establish that every ULS
opportunity has a valid label, that target-change exits are configuration
independent, or that the chosen TRAIN maximum is a stable strategy. A negative
VALIDATION result is a valid stopping result, not permission to retune on the
same period.

## Related

[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0018](ADR-0018-execution-model.md) ·
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md) ·
[ADR-0025](ADR-0025-cross-run-training-and-frozen-state.md)
