# Check, prepare and launch a general experiment

Use after the design is frozen and while integrating its dependencies. A pure
MCP host can use the launch tools once the code/data/config exist. Implementing
missing code still requires a coding-capable host; data import is not an MCP tool.

## Input binding

`experiment_check` and `experiment_prepare` take `experiment_id`, `revision`,
the complete resolved engine `config`, `bindings` and a `policy_name`.
The policy must be a tracked `config/split-policies/<policy_name>.json` in the
configured suite. The default is `adr-0012-v1`; never apply a synthetic policy
to real market observations. Inspect actual schemas and accepted metadata.

Each named non-data component in the frozen specification maps to one or more
config pointers of the matching kind. A single dataset dependency can bind to
`/dataset`; its `inputs` must name actual source columns. Multiple datasets are
not supported by this interface. Every configured component must be covered
exactly once. For example:

```json
{
  "close-position": ["/features/0"],
  "feature-score": ["/model"],
  "future-return": ["/evaluation_outcomes/0"],
  "recorded-intent": ["/policies/0"],
  "sign-measure": ["/evaluators/0"]
}
```

The engine requires a decision policy even for predictive runs without a trade
simulation. Such recorded intents are not executed trades. Explain this in the
saved technical design without turning a predictive question into a strategy.

Binding checks cover dataset identity when frozen, instrument, timeframe, clock,
unit, role, required source columns, experiment kind and component coverage.
They cannot prove free-text formula equivalence. Inspect and test the actual
implementation; do not use a convenient but different formula to clear a check.

## Gates and receipts

`experiment_check` writes a report outside the checkout and runs engine preflight
in a fresh process. It performs full integrity/access checks, computes ephemeral
features and checks usable fit/evaluation observations and target horizons. No
model fitting, metrics, trade simulation or run registration occurs.

`experiment_verify` actually runs the repository gate plus adapter tests/lint.
It can take several minutes. A coding host can equivalently run
`trading-research-mcp --config <machine-config> verify-checkout`. Keep the Git
tree stable while checks run. Logs and a successful commit/lock-bound receipt
remain outside code; do not fabricate a receipt or submit an asserted test pass.

`experiment_prepare` returns either explicit blockers or an immutable `plan_id`.
`experiment_start(plan_id)` repeats checks, verifies identities and launches the
engine detached. Repeating a plan returns the same receipt. Preparing another
plan does not reset the frozen revision's run budget. An uncertain start blocks
further starts until operator inspection; never delete receipts to clear it.

Both Gaps and general runs share the adapter's one-active-run exclusion.
`experiment_status(plan_id)` reads state without reconciliation.
`experiment_results(plan_id)` verifies recorded result hashes before returning
metrics, sample counts and limitations. It never recomputes results.

Numerical limits recorded with the frozen approval are enforced. The supported
maximums are: ordinary fold-fit TRAIN/VALIDATION experiments, full integrity,
at most 2 million admitted observations, one worker, 300 seconds per input-check
or execution process, and 4 GiB address space. POSIX limits apply to the detached
worker; CPU hard termination can leave a stale run needing ordinary engine
recovery. The alarm is handled at Python signal checkpoints. These are execution
limits, not model-performance promises. Cross-run frozen-state configs, external
feeds, protected-data unlocks and arbitrary URL downloads are not exposed.

## Synthetic walkthrough

The adapter command `create-demo --suite-root <checkout> --destination <new-dir>`
creates a new, explicitly synthetic dataset, specification draft and preparation
request. It refuses an existing destination and does not freeze or launch.
Follow the generated `specification.json` and `request.json` through the same
review/freeze/check/verify/prepare/start workflow. Use the new directory's
`codex.json` for this demonstration; do not replace the user's main configuration.

The example measures a finite trailing close-position feature on 512 generated
bars. Its schema uses the existing MT5-shaped adapter with explicit synthetic
provenance and a separate native clock. It is a software demonstration, never
market evidence. Detailed command setup is in `docs/GENERAL_EXPERIMENTS.md`.
