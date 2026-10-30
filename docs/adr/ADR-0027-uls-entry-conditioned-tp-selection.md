# ADR-0027 — ULS entry-conditioned seven-arm take-profit selection

**Status:** ACCEPTED · **Date:** 2026-08-26 · **Owner approval:** the owner
explicitly requested training a TP model behind the frozen entry gate and then
testing it on VALIDATION.

## Context

ADR-0026 produced a frozen ULS entry gate that passed its paired VALIDATION
criterion with TP800/SL800. The next question is whether an entered trade should
receive TP200, 300, 400, 500, 600, 700 or 800 while SL remains 800.

Training the TP selector from the same in-sample predictions that trained the
entry gate would exaggerate gate quality. Concatenating separately generated TP
runs without matching episodes would also let availability and position state
change the target population. VALIDATION has already been consumed by the
entry-gating study, so a new check there is development evidence, not fresh
independent validation.

## Decision

### Forward-only TRAIN source evidence

Generate exactly two expanding, chronological entry-gate products from the
existing immutable entry corpus:

1. fit before 35%, calibrate the cutoff on 35–45%, evaluate 45–70%;
2. fit before 60%, calibrate the cutoff on 60–70%, evaluate 70–100%.

An exit reaching a later boundary is purged. Each evaluation span is unseen by
its gate fit and cutoff selection. For every fold, run the same gated events at
the seven declared TP values with SL800, producing exactly 14 canonical TRAIN
children. No VALIDATION, OOS or LOCKBOX row participates.

### Matched TP corpus and target

An observation is identified by feature origin, opening intent key and
direction. Retain it only when all seven arms contain the matching executed
episode. Exclude every boundary-forced close and refuse duplicate sample
identities. Missing arms are excluded, never imputed.

The authorized target spec is `matched_oof_tp_net_pnl_7arm_v1`: seven recorded
provisional `net_pnl` columns, one for each TP arm, with SL800 and permanently
`unverified` cost semantics. Targets are conditional simulator evidence, not
verified profit.

### Inputs and frozen model

Use the exact ADR-0026 inputs available when the ULS event completes: ULS event
geometry, ATR/volume/confirmation fields, causal market context and the complete
identity-bearing ULS configuration. Add no exit, future path, PnL, selected arm,
run rank, fold number or validation value.

Chronologically split complete matched rows 85/15, purging fit episodes whose
latest arm exit reaches the test boundary. Fit one deterministic seven-output
regression forest on the first span: 96 trees, depth 6, minimum leaf 12,
`max_features=0.75`, no bootstrap, seed `20260826`, one thread. Report the last
15% once. The per-row choice is the greatest predicted value, with the lowest TP
winning exact ties.

The deployable bundle combines that selector with an exact byte-for-byte
reproduction of the already-frozen ADR-0026 entry forest and cutoff. It emits a
sparse signed `score` only for accepted entries and seven generic value columns.
The policy maps their argmax to price distances 2–8 and attaches stop distance
8. The simulator core and fixed-stage architecture do not change.

### Reused VALIDATION comparison

After code, protocol and tests pass at a clean commit, freeze exactly eight
VALIDATION children sharing the same dataset, bounds, features, frozen combined
state, SL800 and provisional cost binding:

- one dynamic child choosing TP per trade; and
- seven fixed children at TP200 through TP800 using the same frozen entry gate.

The primary comparator is the fixed TP chosen as best on the selector's
internal TRAIN test before VALIDATION is opened. Primary success requires
higher provisional net PnL for the dynamic selector and at least 30 dynamic
trades. All seven fixed results remain visible descriptively. No parameter or
success-rule change follows the read. This reused VALIDATION is explicitly
development evidence; OOS and LOCKBOX remain untouched.

## Consequences

The study tests adaptive TP separately from the entry decision without training
either stage on its own evaluation outcomes. Complete-case matching produces a
clean seven-way comparison but can select for shorter or otherwise commonly
completed episodes; that limitation must be reported. A negative result is a
valid stopping result and does not authorize retuning on the same VALIDATION
period.

## Related

[ADR-0012](ADR-0012-two-tier-splits.md) ·
[ADR-0016](ADR-0016-determinism-tiers.md) ·
[ADR-0018](ADR-0018-execution-model.md) ·
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) ·
[ADR-0024](ADR-0024-multiple-testing-trial-ledger.md) ·
[ADR-0025](ADR-0025-cross-run-training-and-frozen-state.md) ·
[ADR-0026](ADR-0026-uls-entry-gating-recorded-trades.md)
