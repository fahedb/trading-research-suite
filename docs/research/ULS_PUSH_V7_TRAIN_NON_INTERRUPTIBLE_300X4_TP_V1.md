# ULS+PUSH v7 — non-interruptible 300 × 4 TP TRAIN corpus

**Status:** COMPLETE — 1,200/1,200 canonical TRAIN children, zero failures

**Date:** 2026-08-27

## Question and authorized scope

The owner requested a replacement TRAIN corpus for the same 300 ULS+PUSH
strategy vectors and TP200/400/600/800 arms at fixed SL800, but with
non-interruptible trades. Once opened, a trade ignores every later LONG, SHORT
or FLAT intent and remains active until TP, SL or the mandatory fold boundary.

The request is
`config/sweeps/uls-push-v7-train-non-interruptible-300x4-tp-v1.json`; its
deterministic generator is
`scripts/generate_uls_push_non_interruptible_tp_sweep.py`. It contains exactly
1,200 ordinary TRAIN configurations and changes only the execution identity
relative to `uls-push-v7-train-joint-model-300x4-tp-v1`.

## Frozen execution semantics

- Entry remains at the next observed row's open.
- While a position is active, every subsequent intent is ignored, not queued.
- TP/SL levels are immutable for the life of the trade.
- After TP/SL, only a fresh later non-FLAT intent can open another trade.
- Intrabar ambiguity, gaps, costs and price-path evidence retain ADR-0023 rules.
- A still-active trade is forcibly closed at its fold boundary and must be
  excluded from complete TP labels rather than read beyond the fold.

The execution identity is `bracket_non_interruptible_v1`. The historical
`bracket_single_position_v1` implementation and all prior runs remain intact.

## Interpretation limits

This design removes future-signal target changes from each opened trade's
label, but TP choice still changes holding time and therefore which later
signals are executable under the one-position constraint. The four arms are
paired at the configuration level, not guaranteed to contain identical trade
opportunity sequences. Results are exploratory TRAIN evidence and add 1,200
related hypotheses to the Trial Ledger; they do not replace prior trials.

Costs and executable-price semantics remain unverified. No VALIDATION, OOS or
LOCKBOX access is authorized by this protocol.

## Execution record

The implementation, frozen request and full repository gate completed at clean
commit `64ad34972852a66cbe98a10081160e86914ef6f1`. Sweep
`uls-push-v7-train-non-interruptible-300x4-tp-v1` dispatched 1,200 children
through eight memory-bounded workers and completed all 1,200 in 1,729.063107812
seconds of wall time. There were zero failed, interrupted, pending, duplicate or
retry children. Every run is canonical TRAIN with `oos_touched=false` and
`lockbox_touched=false`.

An artifact-level audit opened all 6,000 persisted TradeLogs and counted
1,174,981 trades: 768,176 take-profit exits, 405,295 stop-loss exits and 1,510
mandatory fold-boundary exits. The `TARGET_CHANGE` exit count is exactly zero.

The Trial Ledger records 1,200 related distinct hypotheses, 10,800 Plane-B
questions, zero Plane-A trials, zero protected-data events, zero
pre-evaluation failures and 12,000 evaluator entries with unverified cost
semantics. After the owner-authorized purge of the superseded system-test
corpus, the Explorer index was fully rebuilt at generation 1 with 1,206 runs
and zero scan errors; the primary ULS+PUSH family contains exactly these 1,200
replacement runs. Final derived storage is approximately 72 GiB of feature
cache, 23 GiB of run artifacts and 34 MiB of index data; approximately 110 GiB
remains free on the volume.
