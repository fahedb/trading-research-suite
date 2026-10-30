# ADR-0028 — One ULS model for entry abstention and four-way TP selection

**Status:** ACCEPTED · **Date:** 2026-08-26 · **Owner approval:** the owner
explicitly requested resetting the earlier two-model approach, training one model
from the 300 × 4 TRAIN corpus and then running it on VALIDATION.

## Context

The earlier design used one model to decide entry and another model to choose TP.
The new question is whether a single learner can estimate the value of each
available trade action directly. The source sweep contains the same 300 ULS
configuration vectors at TP200, TP400, TP600 and TP800, always with SL800.

VALIDATION has already been read by earlier ULS studies. Any result there is
reused development evidence, not fresh independent validation. OOS and LOCKBOX
remain prohibited.

## Decision

### Matched TRAIN corpus

For each ULS configuration, match an executed opening event by feature origin,
intent key and direction across all four TP runs. Retain only complete four-arm
episodes and exclude boundary-forced closes. Missing outcomes are excluded and
never imputed. Exact sample identities must be unique. Because the same market
episode may occur under many configurations, weight every row by the inverse
number of retained rows sharing its intent key and direction.

Inputs are limited to causal information available when the event completes:
ULS geometry and confirmation fields, market context, direction, and the full
identity-bearing ULS configuration. Exit data, PnL, future prices, run rank and
VALIDATION results are forbidden inputs. The four targets are recorded net PnL
at TP200, TP400, TP600 and TP800 with SL800 and unverified cost semantics.

### One multi-output model

Fit one deterministic four-output regression forest: 96 trees, depth 8,
minimum leaf 128, `max_features=0.75`, no bootstrap, seed `20260826`, one
thread. Its four outputs estimate action values; their argmax chooses TP, with
the lowest TP winning an exact tie.

No trade is a known zero-value action rather than a fabricated fifth label. The
model abstains unless its largest predicted action value exceeds one
non-negative cutoff calibrated on TRAIN. If no non-negative cutoff reaches the
calibration minimum, the only allowed fallback is cutoff zero. It may retain a
sparse set of positive predictions, but the VALIDATION minimum-trade success
rule still applies.

Chronologically split unique decision keys 70/15/15 into fit, calibration and
internal test. Purge an earlier segment when any matched arm exits at or after
its next boundary. Select one deployable ULS configuration and its fixed-TP
comparator on fit only; select the abstention cutoff on calibration only; read
the internal test once. Build both corpus and fitted payload twice and require
byte-identical results.

### Reused VALIDATION comparison

After the implementation and protocol are committed cleanly, freeze exactly
five VALIDATION children sharing dataset, bounds, selected ULS configuration,
frozen model, SL800 and cost binding:

- one dynamic model child choosing abstention or TP200/400/600/800 per event;
- four ungated fixed-TP children at TP200, TP400, TP600 and TP800.

The primary comparator is the fixed TP selected on TRAIN fit. Success requires
dynamic net PnL above that comparator, dynamic net PnL above zero, and at least
30 dynamic trades. No rule or parameter may be changed after reading the
result. All five children remain visible descriptively in Explorer.

## Consequences

This is one fitted learner and one decision rule, while still exposing fixed
baselines that test whether it adds value. Complete-case matching avoids
invented counterfactual targets but can favour episodes that completed under
every TP arm. Position occupancy can also change later trade availability, so
the conclusions remain conditional simulator evidence. A negative result is a
valid stopping result and does not authorize tuning on the same VALIDATION.

## Related

[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) ·
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md) ·
[ADR-0025](ADR-0025-cross-run-training-and-frozen-state.md) ·
[ADR-0026](ADR-0026-uls-entry-gating-recorded-trades.md) ·
[ADR-0027](ADR-0027-uls-entry-conditioned-tp-selection.md)
