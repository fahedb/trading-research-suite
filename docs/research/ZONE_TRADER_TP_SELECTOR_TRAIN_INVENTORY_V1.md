# Zone Trader TP selector — TRAIN inventory v1

Date: 2026-08-23 · Scope: **TRAIN only** · VALIDATION/OOS/LOCKBOX rows were not
opened · Source authority: canonical COMPLETE run files under the configured
runs root; SQLite was not used as research truth.

## Purpose

This inventory answers the owner's first question before any model is trained:
which prior Zone Trader observations can support a fair three-way choice among
TP 200, TP 400 and TP 800 while SL remains fixed at 400?

The answer is **92,330 matched three-arm trades over 100 distinct Zone Trader
feature configurations**. This is large enough for the first feasibility
study. It is not all observed trades: incomplete action sets are excluded
rather than assigned invented outcomes.

## File-truth inventory

| Item | Count |
|---|---:|
| Canonical COMPLETE Zone Trader TRAIN experiment fingerprints | 340 |
| Distinct Zone Trader feature configurations | 135 |
| Configurations with TP 200/400/800 and SL 400 all present | 100 |
| Additional configurations with TP 800/SL 400 only | 35 |
| Canonical action runs: TP 200/SL 400 | 100 |
| Canonical action runs: TP 400/SL 400 | 100 |
| Canonical action runs: TP 800/SL 400 | 140 |
| Sweep memberships across the active Zone families | 355 |
| Reused/qualification memberships collapsed | 220 |

The 140 TP-800 runs comprise the 100 primary configurations, the 35 ZT047
neighbourhood configurations and five per-trade transport qualifications. The
qualification family uses a different policy and is not a bracket-quality
hypothesis.

## Trade matching and exclusions

Across the primary 100 configurations, the three fixed-action families contain:

| Action | Raw TradeLog rows |
|---|---:|
| TP 200 / SL 400 | 107,369 |
| TP 400 / SL 400 | 99,207 |
| TP 800 / SL 400 | 92,482 |
| **Total** | **299,058** |

Attempts, qualification reuse and repeated sweep membership first collapse by
canonical experiment fingerprint and artifact identity. Within one feature
configuration, a candidate match is keyed by its actual opened trade's
`(entry_key, direction)` and must map one-to-one back to the opening intent and
the Zone feature row that caused it. The feature-origin digest remains part of
the sample identity; two configurations at the same native key are not assumed
to be the same event.

| Action availability for a unique configuration/entry/direction | Count |
|---|---:|
| All three actions observed | **92,330** |
| Exactly two actions observed | 6,858 |
| Exactly one action observed | 8,352 |
| **Incomplete and excluded from the three-way target** | **15,210** |

The missing arms are state-dependent: a prior TP choice can keep a position
open and suppress a later entry. They are therefore not losses, abstentions or
zeroes. The pilot uses complete cases only and records the resulting selection
bias explicitly. It does not claim to have reconstructed counterfactual trades.

## Why timestamp-only deduplication is invalid

The 135 representative Zone feature artifacts contain 266,116 raw event rows.
An exact signature over feature origin, native key, signed event, entry,
stop/target, zone geometry and zone kind leaves 76,479 distinct signatures and
collapses 189,637 exact repeats (71.261%). There are 60,722 distinct
`(native_key, direction)` pairs, but 12,908 of those pairs carry more than one
zone geometry, with as many as seven geometries at one pair.

Consequently the v1 training sample identity is:

```
(feature_origin_digest, opening_intent_key, direction)
```

and never just a timestamp. Repeated market episodes across different feature
configurations remain legitimate rows, but receive inverse episode-frequency
weights so one heavily repeated native event has total training weight one.

## Target shape and ties

Each complete sample retains three realized simulator outcomes; it is not
forced into one class. The target for arm `j` is gross realized R:

```
direction * (exit_price_j - entry_price_j) / 4.0
```

where 4.0 price units is the fixed SL-400 risk distance. This avoids treating
the open cost audit as verified money and preserves actual stop, target,
gap/ambiguity and forced-boundary behavior. Costs are identical across the
three arms of a matched entry and are not needed to rank its TP choice.

| Best-arm set on the matched TRAIN rows | Count |
|---|---:|
| TP 200 only | 15,932 |
| TP 400 only | 12,934 |
| TP 800 only | 20,572 |
| TP 400 and TP 800 tied | 1,068 |
| TP 200, TP 400 and TP 800 all tied | 41,824 |

The 41,824 three-way ties are why a classifier with an arbitrary tie label is
rejected. The proposed model predicts one generic value per arm and the policy
chooses the largest prediction with a frozen first-arm tie-break.

## Research limitation

This is a conditional matched-entry study. It can test whether row-local Zone
geometry and configuration parameters contain useful information about which
TP worked better when all three fixed-action runs opened the same trade. It
cannot prove that the unobserved arms of the 15,210 incomplete entries would
have behaved a particular way, and it cannot remove the state-distribution
shift created when the learned policy changes holding duration on VALIDATION.
Those limitations remain in the final report regardless of the result.

