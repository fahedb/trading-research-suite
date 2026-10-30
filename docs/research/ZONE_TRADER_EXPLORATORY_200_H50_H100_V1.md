# Zone Trader v13 — frozen TRAIN exploratory batch H50M1 + H100M1

Status: frozen before execution; no result has been inspected.

## Search family

This is one exploratory TRAIN-only family containing the exact 100 Zone Trader
parameter configurations from the prior `ZT100` design, evaluated at two
forward-return horizons:

- `H50M1`: `decision_delay_rows=1`, `horizon_rows=50`, target
  `log(Close[X+51] / Close[X+1])`;
- `H100M1`: `decision_delay_rows=1`, `horizon_rows=100`, target
  `log(Close[X+101] / Close[X+1])`.

The arithmetic is 100 parameter configurations times 2 identity-bearing
outcomes = **200 unique experiment configurations**. The two horizon batches
are fixed together before either produces a result. No result from one horizon
may change the other batch.

## Deterministic parameter set

The 100 ordered parameter vectors are copied exactly from the prior frozen
Zone Trader exploratory design. They remain an explicit Phase-5 `zip`
enumeration with 14 axes and 100 values per axis:

- 100 exact and semantic-unique parameter combinations;
- family allocation: V=12, F=12, D=14, VF=13, VD=13, FD=12, VFD=24;
- confirmation allocation: rejection-wick=49, immediate-touch=29,
  close-back-outside=22;
- parameters of disabled families remain at one canonical baseline and do not
  create inactive-parameter identity splits;
- no random draw, adaptive resampling or post-result configuration generation.

Full sweeps:

- `zone-trader-v13-train-exploratory-100-h50m1-v1`;
- `zone-trader-v13-train-exploratory-100-h100m1-v1`.

Qualification subsets contain ordinals 0–4 from each corresponding full sweep.
They have identical experiment fingerprints to those full-sweep children and
must be reused rather than attempted twice.

## Fixed TRAIN geometry and reach correction

The source view remains `XAUUSD_S_M1_TRAIN_V1`; there is no VALIDATION, OOS or
LOCKBOX access. M5 construction, H1 anchor, tick volume, finite ATR,
`max_zone_age_rows=5000`, model, policies and Plane-A evaluators remain fixed.
There is no Plane B, simulator or TP/SL optimization.

The prior batch provided 26,534 context rows, while 34 configurations declared
larger feature reach; the maximum across the frozen 100 is 28,714 rows. This
batch therefore uses:

- `context_start=1577926800`;
- `eval_start=1580437860`, TRAIN row offset 28,714;
- `fit_rows=385824`;
- `eval_rows=200000`, `step_rows=200000`, five folds;
- `eval_end=1703894160`.

The original first evaluated row was global TRAIN offset 414,538. The corrected
geometry preserves it exactly: `28,714 + 385,824 = 414,538`. The model fit is a
no-op, so only unused early fit context is trimmed; all five 200,000-row
evaluation windows remain byte-for-byte on the same TRAIN rows. This removes
the earlier warm-up-only failures without changing evaluated observations.

## Execution and accounting rules

The two five-child qualification subsets run first through the same supported
dispatcher. Only after identity, Trial Ledger, cache and failure recording are
verified may the two full batches launch detached. The full sweeps must reuse
the ten completed qualification identities, so the family contains exactly 200
distinct hypotheses and at most 200 ordinary execution attempts absent genuine
failures/retries.

Every attempt and retry remains visible. Plane-A questions are counted
separately per horizon. No result is independently confirmed merely because it
is positive in this 200-configuration exploratory family.
