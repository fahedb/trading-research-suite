# Zone Trader v13 — fixed-bracket Plane-B TRAIN results

Status: completed 2026-08-20 on clean run commit
`3d748b4496dee68043d17bfa825a72756326ad27`.

## Verdict

Neither fixed bracket supplies a robust net-positive TRAIN region under the
declared exploratory execution assumptions. `TP800/SL400` is materially better
than `TP400/SL400`, but the evidence that remains positive after costs is sparse
or fold-unstable. This is not a confirmed tradable edge and no protected data
should be opened from this result.

All money values below use 0.01 lot, 0.01 price units per pip, contract size
100, spread 20 pips and commission 6.00 per lot round turn. The binding remains
permanently `unverified`; these are conditional research values rather than
verified broker-money estimates.

## Accounting and machine result

- generated and unique configurations: 200 (100 per fixed bracket);
- completed: 200; failed/interrupted/retried/resumed: 0;
- qualification reuse: 5 completed identities per full sweep; 95 new children
  dispatched per full sweep;
- duplicate sweep identities: 0;
- Plane A questions: 400; Plane B questions: 1,800;
- cost-unverified Plane-B inventory entries: 2,000;
- deterministic verification: one fixed configuration under both brackets,
  two cache-bypassing executions each; all arrays, artifacts, inventory and
  metrics exact, maximum absolute deviation 0;
- verification attempts collapsed by the Trial Ledger: 4; hypotheses remain
  exactly 200;
- canonical experimental runs: 200; stored verification runs: 4;
- full detached dispatch: two simultaneous pools, four workers each, 4 minutes
  34 seconds wall time; 190 fresh children imply about 2,490 fresh runs/hour;
- each pool reported an 8 GiB cgroup memory peak under an enforced 8 GiB cap;
- final index: 204 run records, zero scan errors; Trial Ledger has zero audit
  discrepancies and zero noncanonical runs;
- retained storage: about 39 GiB (30 GiB run records, 9.4 GiB cache);
- audit SHA-256 remained
  `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
- no VALIDATION, OOS or LOCKBOX evaluation occurred.

The aggregate PnL sums below add highly correlated alternative configurations;
they are landscape diagnostics, not a portfolio backtest.

## TP400 / SL400

- active configurations: 86; zero-trade configurations: 14;
- total trades across alternatives: 99,207; median active configuration: 884;
- net-profitable configurations: 6/86; net-positive in at least three folds:
  3/86; positive in all five folds: 0;
- active-config net-PnL distribution: min -2,408.42; median -281.09; p75
  -104.12; p90 -3.07; p95 3.56; max 10.70;
- active-config median gross PnL: -63.64; median costs: 229.84;
- correlated aggregate gross PnL: -4,938.80; costs: 25,793.82; net:
  -30,732.62;
- trade-weighted win rate: 45.07%;
- exit mix: TP 36.31%, SL 35.04%, opposite-target change 28.57%, fold boundary
  0.08%;
- ambiguity rate: 0.041%; gap rate: 0.814%.

The three configurations positive in at least three net-PnL folds are all too
small for a robust claim: `ZT100-008` has 26 trades, `ZT100-080` has 9, and
`ZT100-087` has 50. The only profitable configuration above 100 trades,
`ZT100-063`, has 145 trades but only two positive folds.

## TP800 / SL400

- active configurations: 86; zero-trade configurations: 14;
- total trades across alternatives: 92,482; median active configuration: 849;
- net-profitable configurations: 6/86; net-positive in at least three folds:
  2/86; positive in all five folds: 0;
- active-config net-PnL distribution: min -1,975.93; median -194.21; p75
  -90.81; p90 -8.12; p95 2.68; max 79.71;
- active-config median gross PnL: -0.63; median costs: 220.61;
- correlated aggregate gross PnL: 172.65; costs: 24,045.32; net: -23,872.67;
- trade-weighted win rate: 35.63%;
- exit mix: TP 20.46%, SL 43.04%, opposite-target change 36.40%, fold boundary
  0.10%;
- ambiguity rate: 0.019%; gap rate: 0.726%.

Forty-three active configurations are gross-positive, but only six remain
positive after the declared costs. The two configurations positive in at least
three net-PnL folds are `ZT100-058` with 38 trades and `ZT100-080` with 9.

`ZT100-047`, the strongest prior Plane-A candidate, is the notable high-sample
case: 1,051 trades, gross 352.97, costs 273.26 and net 79.71. Its five net fold
values are `[-87.95, -18.43, -10.59, 154.11, 42.57]`, so only two folds are
positive and the result is regime-concentrated. Under TP400/SL400 it is net
-201.76. It is a research clue, not a passing candidate.

## Paired bracket comparison

Among the 86 configurations active under both brackets:

- TP800/SL400 has higher net PnL in 62; TP400/SL400 in 23; one ties;
- median paired improvement from TP800/SL400 is 46.78;
- paired net-PnL correlation is 0.975;
- both brackets are net-positive for three configurations;
- only `ZT100-080` is net-positive in at least three folds under both, but it
  has only nine trades;
- the median TP800/SL400 configuration executes 38 fewer trades because its
  longer-held positions suppress more same-direction event instructions.

The better 2:1 landscape is broad in relative terms but remains negative in
absolute net terms. The low ambiguity and gap rates show that same-bar ordering
and gap handling are not the principal cause. The dominant finding is that a
nearly flat pre-cost 2:1 landscape does not clear the declared transaction
costs; the 1:1 landscape is already negative before costs.

## Relationship to the prior Plane-A shortlist

Of the eight configurations previously stable at both H50 and H100
(`ZT100-047`, `002`, `099`, `006`, `031`, `062`, `056`, `023`), only
`ZT100-047` is net-positive under either fixed bracket, and that result is
positive in only two folds. The other seven are net-negative under both.

## Recommended next action

Do not open VALIDATION and do not optimize another large TP/SL grid. First
inspect the event-to-trade mismatch around `ZT100-047`: 19.60% of its 2:1
trades exit on an opposite target change rather than a bracket, and costs flip
several gross-positive folds to net-negative. A small, frozen TRAIN-only study
should compare the existing state-following reversal semantics with an
explicit one-shot-event exit policy only if that policy can be represented
without changing platform architecture. If it cannot, stop and design the
semantic change before further experiments.
