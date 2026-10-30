# Project state

This is the combined public distribution of Trading Research Suite: the engine
at the root and the Explorer in `explorer/`.

The engine includes the data, feature, research, simulation, experiment-operation,
and cross-run model components present in the source snapshot. The Explorer
includes experiment browsing, run overviews, trade charts, multi-timeframe views,
and recorded signal context. A synthetic demo is available without market data.

Start with [the documentation](docs/README.md). Read
[ADR-0029](docs/adr/ADR-0029-public-suite-distribution.md) for this distribution's
layout and Git-identity consequences. The original machine-specific progress
ledger is not distributed. Historical phase documents are reference material;
no new research phase or protected-data access is authorized by publication.

## Codex integration

The owner selected the existing frozen Gaps TRAIN study for the first Codex
integration. The local MCP server, original seven tools, Codex plugin, Gaps Skill and
installer are implemented. See [Research with Codex](docs/CODEX.md), the
[release plan](docs/CODEX_MVP.md) and
[ADR-0030](docs/adr/ADR-0030-codex-gaps-adapter.md).

Stored results are read from the owner's external run store. Exact-template
reruns use a separate workspace and the existing detached engine CLI. No tuning,
sweeps or protected-data unlocks are exposed. The original source repositories,
historical runs and immutable dataset roots are not modified by integration.

The general `research-experiment` Skill is now implemented with four additional
MCP tools: component/dataset metadata inventory, structured specification review,
immutable specification freeze and revision retrieval. It guides interview,
agreement, missing-component integration, testing and execution handoff.
See [the workflow](docs/EXPERIMENT_WORKFLOW.md) and
[ADR-0031](docs/adr/ADR-0031-experiment-authoring-workflow.md).

Version 0.3 adds six general execution tools: input checking, checkout
verification, immutable preparation, idempotent detached start, status and results.
The engine's new `preflight` command checks permitted observations, full integrity,
actual features and usable folds/target horizons before run registration. A new
finite trailing close-position feature and stateless score model exercise this
path using a separately generated synthetic TRAIN example. See
[the walkthrough](docs/GENERAL_EXPERIMENTS.md) and
[ADR-0032](docs/adr/ADR-0032-prepared-experiment-launch.md).

Supported launch scope is ordinary fold-fit TRAIN/VALIDATION experiments with
clean verified code and bounded resources/attempts. Missing code still uses a
coding-capable host. No arbitrary strategy's scientific validity is implied by
passing readiness checks; synthetic results are software evidence only.

NEXT: guided personal-data admission, then an approved downloadable catalog and
new-user installation walkthroughs. The synthetic research example is distinct
from the Explorer's illustrative UI demo. Historical research phases do not
advance through this integration work.
