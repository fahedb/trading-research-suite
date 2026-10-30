# Zone Trader v13 — H20M5 research reset

Status: reset completed; subsequent H50M1/H100M1 batch definition is frozen,
but no experiment has yet been run under the reset.

On 2026-08-20 the owner reset the Zone Trader research history and replaced the
prior five-M1-row outcome with a look-ahead of 20 nominal M5 candles.

The platform's `forward_log_return_v1` counts observed source rows, so the
active definition is:

- strategy timeframe: M5, built deterministically from M1;
- `decision_delay_rows = 1` M1 row;
- `horizon_rows = 100` observed M1 rows (`20 * 5`);
- target at event row `X`: `log(Close[X+101] / Close[X+1])`;
- a long event is directionally correct when the target is positive;
- a short event is directionally correct when the target is negative;
- Plane A remains `sign_accuracy_v1` plus `rank_ic_v1`;
- no TP/SL, costs, simulator, OOS, LOCKBOX, or Plane B is implied.

Because the source data may contain missing M1 observations, 100 observed M1
rows represent 20 nominal M5 candles but are not a guarantee of exactly 100
wall-clock minutes across a gap. Implementing an exact completed-M5 outcome
axis would be a separate platform change and is not part of this reset.

The active baseline is
`config/experiments/zone-trader-v13-h20m5-train-v1.json`. All previous sweep
request files and result reports were removed so they cannot be mistaken for
evidence under the new outcome. The later, newly frozen 200-configuration
H50M1/H100M1 exploratory family is documented separately in
`docs/research/ZONE_TRADER_EXPLORATORY_200_H50_H100_V1.md`; it does not restore
or rely on any deleted result.

Deleted external artifacts:

- all 163 prior run directories;
- all four prior sweep execution directories;
- feature and fitted-state caches;
- disposable run index;
- temporary research-run files.

The append-only `audit.jsonl` was retained byte-for-byte. Market datasets,
strategy implementation, source code and tests were not deleted or modified by
the cleanup.
