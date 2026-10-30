# Phase 7 follow-up — Zone TP selector V2

Date: 2026-08-23 · Status: **COMPLETE — IMPROVED VERSUS V1, BUT BOTH
PREREGISTERED VALIDATION CRITERIA FAILED; STOP** · Parent study:
[PHASE7_ZONE_TP_SELECTOR.md](PHASE7_ZONE_TP_SELECTOR.md) · Accepted lifecycle:
[ADR-0025](../adr/ADR-0025-cross-run-training-and-frozen-state.md)

## 1. Question and evidence class

The owner requested a second bounded study after V1 failed: enrich the
point-in-time inputs, train a new nonlinear model on TRAIN, freeze it, and test
it on VALIDATION. SL remains fixed at 400 pips. The policy chooses TP 200, 400
or 800 pips per opened trade.

V1 already exposed the same VALIDATION period. V2 is therefore an iterative
development check on a previously used VALIDATION view, not fresh independent
evidence. It cannot erase V1, carries a new trial identity, and will not access
OOS or LOCKBOX regardless of outcome.

The study reuses `matched_bracket_gross_r_v1`, the immutable bundle lifecycle
and frozen-state experiment mode accepted by ADR-0025. It adds replaceable
feature/model plugins and a versioned builder composition only; it changes no
core, data, simulator, execution, target-authority or gating contract.

## 2. TRAIN sources and matching

The outcome authority remains the 300 canonical COMPLETE V1 TRAIN action runs:
100 Zone configurations times TP 200/400/800, all with SL 400. Attempts and
sweep reuse collapse exactly as in V1; incomplete action sets remain excluded
and are never imputed.

V2 adds exactly 100 canonical TRAIN context-carrier runs, one for each of the
same Zone configurations. Every carrier contains the unchanged
`zone_trader_v13` provider plus `zone_context_v1`. Before a context row is
admitted, the builder exact-compares the carrier's complete V13 key axis and
all seven event/geometry arrays with the V1 action source. It then matches by
the existing `(feature origin, opening intent key, direction)` identity.

The context carrier is not a new bracket-quality observation. Its trade result
is ignored. Its sole authority is the immutable, content-verified feature
artifact generated through the normal TRAIN data layer.

Rows sharing one native `(opening intent key, direction)` episode across Zone
configurations retain inverse-frequency weight, so the episode's total fit
weight is one.

## 3. Frozen point-in-time inputs

All inputs are available when the completed confirmation bar emits the Zone
event. Market context uses completed M5 bars only and the broker-native wall
clock under `native-wallclock-v1`; it never assigns UTC semantics.

The V1 descriptive inputs and canonical Zone configuration parameters remain.
V2 adds these exact `zone_context_v1` columns:

1. `ztc_atr_over_entry`;
2. `ztc_zone_width_over_atr`;
3. `ztc_zone_age_bars`;
4. `ztc_zone_touch_count`;
5. `ztc_pending_bars`;
6. `ztc_entry_zone_position` (direction-normalized, divided by zone width);
7. `ztc_confirmation_wick_fraction`;
8. `ztc_volume_zscore_20`;
9. `ztc_atr_ratio_12_48`;
10. `ztc_directional_return_3`;
11. `ztc_directional_return_12`;
12. `ztc_directional_return_48`;
13. `ztc_room_to_swing_48_atr`;
14. `ztc_nearest_zone_room_atr` (capped at 20; 20 when absent);
15. `ztc_nearest_zone_present`;
16. `ztc_minute_sin`; and
17. `ztc_minute_cos`.

The provider declares enough prior-row reach for every 48-bar statistic. The
feature leakage rig must prove invariance to every post-decision mutation.
Exit reason, future high/low, MFE/MAE, bars-to-exit and every realized arm value
remain targets/diagnostics only and are prohibited inputs.

## 4. Frozen model

Use one deterministic multi-output random-forest regressor:

- model id `zone_forest_selector_v2`, version 1;
- `n_estimators=96`;
- `max_depth=8`;
- `min_samples_leaf=64`;
- `max_features=0.75`;
- `bootstrap=false`;
- `random_seed=20260823`;
- squared-error split criterion; and
- one fit thread.

The fit consumes the same three generic gross-R targets as V1. Durable state is
pickle-free: tree offsets, child indices, split features, thresholds and
three-value leaf arrays are stored in the existing canonical fitted-state NPZ
container. Inference is implemented from those arrays and emits generic
`score`, `value_0`, `value_1`, `value_2`; TP vocabulary remains solely in the
existing adaptive bracket policy. Exact double fit/encoding must reproduce.

No model, feature, threshold or hyperparameter sweep is permitted in this
study.

## 5. TRAIN qualification and freeze

Use the same chronological qualification as V1: first 80% of unique TRAIN
decision keys for fit, final 20% for holdout, purging every fit row whose
latest three-arm exit reaches the holdout boundary. Report per-arm MAE/RMSE,
fixed-arm means, chosen-arm mean gross R, oracle regret and choice counts.

The qualification result does not tune or stop the accepted study. After it is
reported, refit the identical model on all complete TRAIN rows, publish one
immutable V2 bundle, run a frozen-path TRAIN smoke test, and freeze the four
VALIDATION requests at one clean Git/dependency identity.

## 6. VALIDATION family and success rule

Execute exactly 400 new runs at the V2 freeze commit:

- 100 dynamic V2 selector runs;
- 100 fixed TP200/SL400 runs;
- 100 fixed TP400/SL400 runs; and
- 100 fixed TP800/SL400 runs.

All four families use the same ordered 100 V13 configurations, the same
`zone_context_v1` computation, frozen V2 model, VALIDATION identity/bounds,
warm-up semantics, simulator and open unverified execution/cost binding. The
fixed policies consume only the event sign and ignore all three learned value
columns; computing the same context in every family removes a platform/cache
asymmetry without changing their fixed bracket. No child is selected or removed
after results are visible.

The primary feasibility rule is unchanged from V1 and requires both:

1. dynamic mean total gross R across 100 configurations exceeds the largest
   fixed-family mean; and
2. dynamic total gross R beats that same best fixed family in more than 50 of
   100 paired configurations.

Secondary diagnostics are median paired difference, gross R/trade, trade
count, choice mix, gross-R drawdown and ambiguity/gap rates. No p-value,
independence claim or verified-money claim is produced.

After reporting, stop. A failure does not permit another VALIDATION retune. A
pass means only that V2 merits a separately approved promotion study; it does
not authorize OOS or LOCKBOX.

## 7. Ordered execution

1. implement and gate the plugins, builder v2, requests and release-blocking
   leakage/determinism/corruption tests;
2. commit a clean implementation identity;
3. execute the 100 TRAIN context carriers;
4. build, qualify and freeze the V2 bundle on TRAIN;
5. execute one frozen TRAIN smoke run;
6. hash-freeze and execute the four 100-child VALIDATION sweeps;
7. report every result, Trial Ledger denominator and limitation; and
8. rebuild the Explorer index and run the full repository gate.

## 8. Pre-data operational amendment

The first carrier invocation at clean commit `34b5fa0` produced no canonical
COMPLETE carrier and was stopped after exposing two implementation boundaries:
early events from the provider's prior-only calculation span were being
validated as evaluation events, and the uncompressed combined feature frame
exceeded the run store's unchanged 128-MiB per-file limit. Those failed and
interrupted attempts are excluded by the source-discovery contract.

Before any successful carrier or V2 VALIDATION result existed, the
implementation was amended to validate context completeness only on evaluation
events and to encode oversized run-frame NPZ envelopes with deterministic ZIP
deflate. The 128-MiB stored-file limit remains unchanged, and compressed run
frames have a new 512-MiB expanded-size refusal. The feature values, TRAIN
rows, targets, model, qualification, four VALIDATION families and success rule
remain unchanged. The replacement carrier sweep has identity
`zone-trader-v13-context-v1-train-carrier-100-v2-attempt-3`. Attempt 2 then
confirmed deterministic compression but exposed the same prior-only boundary
for events with unavailable early ATR; it was stopped after six COMPLETE
children. The final builder accepts context carriers only from its exact clean
Git/dependency platform, so neither intermediate attempt can be mixed into the
final 100-carrier corpus. Both remain audit history and are not retried.

## 9. Completed result

The final carrier sweep completed 100/100 canonical TRAIN runs. The resulting
bundle contains 92,330 complete matched rows from 29,310 unique opening
episodes; 15,210 incomplete action sets remain excluded. All three exact
determinism checks and the frozen-path TRAIN smoke passed.

Chronological TRAIN qualification used 74,260 fit rows and 18,059 holdout rows
after purging 11 boundary-overlapping rows. The selector's holdout mean realized
gross R was -0.007971, below all three fixed-arm means. The accepted protocol
then refit and froze the unchanged model once and proceeded to the single V2
VALIDATION check.

All four frozen VALIDATION families completed 100/100 runs with zero failures.
Dynamic mean total gross R was 7.285750 versus 7.828575 for best fixed TP800.
Dynamic beat TP800 in 29 paired configurations, lost 55 and tied 16; median
paired difference was -0.98 gross R. Both primary criteria failed. V2 improved
over V1's 6.070725 dynamic mean and 19 paired wins, but did not establish
superiority over fixed TP800.

The exact report, limitations and immutable identities are recorded in
[ZONE_TRADER_TP_SELECTOR_VALIDATION_V2.md](../research/ZONE_TRADER_TP_SELECTOR_VALIDATION_V2.md).
The Explorer index was rebuilt with 1,350 runs, 21 sweeps and zero scan errors.
No OOS or LOCKBOX row was accessed.
