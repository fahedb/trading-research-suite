# Prepare and run your own experiment

The general Skill turns an agreed idea into components and an executable plan.
Before launching, it checks the actual data and indicator calculations, verifies
the code, and records exactly what will run. A missing indicator is development
work; missing source observations need a source. Neither is silently ignored.

This version supports ordinary fold-fit experiments on already-admitted TRAIN
or VALIDATION data. Personal CSV import, downloads, external timed feeds and
cross-run frozen-state models are not part of this launch path.

## The process

1. Describe the experiment and resolve meaningful unknowns with Codex.
2. Review and freeze its definition, scope and numerical row/time/memory limits.
3. Map every component to the engine configuration. Run `experiment_check`.
4. Implement missing components, test them, and save a clean source commit.
5. Run `experiment_verify`, then `experiment_prepare`. Any remaining blocker
   prevents preparation. Changing code, data or the plan requires new checks.
6. Launch with `experiment_start`; follow the returned plan with
   `experiment_status` and `experiment_results`.

A prepared plan is tied to its specification, executable config, dataset,
split policy, source/lock identity and successful checks. Start repeats the data
checks in a fresh process. The engine verifies the expected fingerprint before
registering a run and performs its ordinary worker integrity checks again.
Repeating a plan does not create another attempt; new plans share the frozen
revision's attempt budget. Uncertain starts require inspection.

## Try it without private data

After synchronizing the engine and adapter environments, create a new external
directory. Replace the example paths with your own:

```bash
uv sync --locked
uv sync --locked --project integrations/codex
integrations/codex/.venv/bin/trading-research-mcp create-demo \
  --suite-root /absolute/path/to/trading-research-suite \
  --destination /absolute/path/to/new-research-demo
```

This writes 512 deterministic synthetic bars, a specification draft, a config
binding request and a separate local MCP configuration. Existing destinations
are refused. Nothing is frozen or launched by creation. Ask Codex to review the
generated `specification.json` and run the example through the general tools,
using the generated `codex.json` as the MCP server's `--config` argument. Keep
your main configuration separate. Developers can also use the service through
the adapter environment; no test fixture imports are required.

The example computes the closed price's centered position in the last 20
observed bars' high/low range, then measures its sign against a forward return.
Flat ranges produce zero; gaps are crossed as observed rows; no observations are
filled. It uses a finite 19-row history and no learned model. The engine's required
decision policy records intents but there is no trade simulation. Metrics and
sample counts come from the actual engine run; there is no trading-edge claim.
Its MT5-shaped file schema is an adapter format, not broker provenance. Dataset
names, source metadata and the separate native clock explicitly mark it synthetic.

## Scope and limits

- Full integrity and normal TRAIN/VALIDATION access are mandatory.
- Input checks calculate features and check usable fold/target rows. They do
  not fit models, evaluate metrics or promise the eventual research result.
- One adapter run can be active at a time, across Gaps and general experiments.
- Enforce the recorded numerical limits, up to 2 million admitted observations; one worker; 300 seconds wall/CPU
  budget per preflight or worker; 4 GiB address space. The POSIX alarm executes
  at Python signal checkpoints; CPU hard termination can require ordinary
  stale-run recovery. No parallel parameter search is launched automatically.
- Code verification executes trusted local checkout code. Local receipts are
  integrity records, not signatures or a security boundary against local users
  who can rewrite files and checksums.
- Component bindings check coverage and kinds. Codex must still establish that
  the implementation matches the agreed free-text formula and methodology.

The quality receipt comes from actually running `scripts/gate.sh` plus adapter
tests/lint, and is reusable only for the exact clean commit and lockfiles. If a
client has a short tool timeout, run the same verification from its coding host:

```bash
trading-research-mcp --config /absolute/path/to/codex.json verify-checkout
```

The adapter's reports, logs, frozen definitions and launches stay in the external
workspace. Read-only status/results do not reconcile runs. Results preserve
empty/unfavorable evaluations and execution-cost limitations; trade pages in
Explorer require actual simulation artifacts.

See [ADR-0032](adr/ADR-0032-prepared-experiment-launch.md) and the
[launch reference](../plugins/trading-research/skills/research-experiment/references/launch.md)
for developer details.
