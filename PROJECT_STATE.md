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

This is not generic automatic execution: coding integration uses a capable host,
and the automatic MCP launch tools still accept only the frozen Gaps template.
No new user strategy or dataset was implemented/evaluated by adding the Skill.

NEXT: validate one new user-described experiment through the complete workflow,
then implement general prepared-run tooling and guided dataset admission. A
standalone synthetic engine example and approved downloadable catalog remain
release requirements. The existing synthetic Explorer demo is a UI preview only.
This integration does not advance historical research phases.
