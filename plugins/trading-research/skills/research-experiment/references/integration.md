# Integration after design freeze

Read this only when assessing implementation needs or performing authorized
integration. Obtain suite/workspace roots from `experiment_inventory` or the
user's configured workspace. Plugin cache paths are not research checkouts.

## 1. Inspect and map

Read the suite's `AGENTS.md`, `CLAUDE.md`, `PROJECT_STATE.md` and applicable ADRs.
Use the public registries and source declarations to map each frozen requirement.
Record an implementation location/version and supporting tests when reusing code.
Check formulas, parameters, output names, finite information reach and timing.
A combined strategy feature that contains RSI internally is not automatically a
general RSI provider. Do not promise reuse based on a substring in a name.

Produce a compact requirements table: requirement, available evidence, needed
action and blocker. Metadata inspection is allowed before freeze; code changes
and acquisition follow the accepted design and the user's authorization.

## 2. Data and indicators are different jobs

| Need | Supported action |
| --- | --- |
| Existing admitted dataset | Resolve exact identity, declared role, clock, columns, span and context through the data layer; access checks still apply |
| Derived indicator from available fields | Reuse an equivalent provider or implement the precisely frozen formula |
| Missing implementation | Add the appropriate stage component and tests in an isolated checkout |
| Missing source observations/columns | Identify a permitted source and admission procedure; record unavailable coverage instead of inventing it |
| Unsupported timed feed or cross-clock join | Record an architecture blocker and request the necessary scoped design decision |

Personal CSV import and catalog download are not implemented MCP operations.
If an authorized development task builds an importer, it needs explicit column
and clock mapping, duplicate/gap rules, provenance, integrity and a new immutable
dataset identity. Never write into existing immutable dataset roots. Do not
relabel RAW/CURATED data as TRAIN or bypass split-policy admission.

Do not infer actual traded volume from tick volume, UTC from native keys, an
unbounded EMA from a finite approximation, or missing prices from neighboring
candles. Resolve a material approximation with the user and freeze a new revision
when it changes the agreed experiment. Use clearly synthetic observations only
for implementation tests, never to silently replace the user's research data.

## 3. Implement in the correct stage

Use an isolated worktree/branch of the suite for the new experiment. Keep the
frozen specification and local progress under the external workspace. Keep local
credentials and raw data out of Git. Publishing new user-specific research is a
separate action from building it locally.

Read only the relevant guide under the suite root:

- `docs/guides/ADDING_FEATURES.md`: causal indicator arithmetic and declared
  columns/lookback/gap behavior; no IO, forward outcomes or hidden state.
- `docs/guides/ADDING_REPRESENTATIONS.md`: fitted transforms over permitted fit
  rows; no full-dataset normalization that leaks evaluation information.
- `docs/guides/ADDING_RESEARCH_MODELS.md`: generic outputs; targets enter only
  through the engine's declared training-outcome channel.
- `docs/guides/ADDING_DECISION_POLICIES.md`: entry/exit/position intent from model
  output; preserve sparse observations and exact signal availability.
- `docs/guides/ADDING_OUTCOMES.md`: forward outcomes and explicit horizons.

Implement only the frozen semantics. Use new component IDs/versions where the
meaning changes. Follow documented registration/inventory changes; do not relax
architecture assertions, dependency boundaries, causality rigs or purge rules.
Resolve architecture conflicts through the repository's ADR process before
making a conflicting change. A Skill supplies no exception to accepted ADRs.

## 4. Verify and bind

Use independent hand-computed or authoritative formula examples where available,
plus synthetic edge cases: warm-up, gaps, ties, conflicts, horizon boundaries,
closed-bar availability, future perturbation and declared backward reach.
Run the applicable leakage tests and full `./scripts/gate.sh`. Do not edit the
checkout while the gate is running: code-identity tests observe Git state.

Before a real run, bind the frozen digest to the executable config hash, exact
dataset identities, clean suite commit, engine lock hash, split policy, tests and
the approved run/resource budget in the integration record. Parsing config JSON
alone is not runtime preflight; the engine owns loading, integrity and access.

Preserve Plane-A-first methodology. `simulation.methodology_basis` must point to
actual relevant predictive evidence or an explicit owner-directed exception for
this experiment. The original Gaps exception cannot be reused automatically.
Current cost/execution semantics remain unverified. Native market time is not UTC.

## 5. Prepare and launch

Use the [launch reference](launch.md) to bind every frozen requirement to the
resolved configuration and run `experiment_check`. Complete the missing work,
commit the authorized checkout and run `experiment_verify`. It issues evidence
only after the fixed checks actually pass. Then use `experiment_prepare` and
`experiment_start`; do not substitute a direct CLI run when a gate fails.

A pure MCP host can launch a prepared supported experiment, but still needs a
coding host to implement missing components or provision source data. Report
unsupported modes explicitly. The Gaps tools retain their exact-template scope.

Use the plan's durable receipt, `experiment_status` and `experiment_results`.
An uncertain launch requires inspection rather than a fresh attempt. Do not
remove earlier attempts or mutate COMPLETE artifacts. The check/prepare process
is not a research evaluation and does not prove arbitrary free-text semantics.

For actual results, preserve file/run/fold provenance, sample counts, undefined
metrics and cost caveats. Explorer trade pages require recorded TradeLogs;
descriptive and forecast-only experiments should report their actual artifacts.
