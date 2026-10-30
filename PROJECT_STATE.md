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
integration. The local MCP server, seven tools, Codex plugin, workflow Skill and
installer are implemented. See [Research with Codex](docs/CODEX.md), the
[release plan](docs/CODEX_MVP.md) and
[ADR-0030](docs/adr/ADR-0030-codex-gaps-adapter.md).

Stored results are read from the owner's external run store. Exact-template
reruns use a separate workspace and the existing detached engine CLI. No tuning,
sweeps or protected-data unlocks are exposed. The original source repositories,
historical runs and immutable dataset roots are not modified by integration.

NEXT: a standalone synthetic engine example for people without the private
TRAIN dataset, then guided personal-data import and an approved downloadable
catalog. The existing synthetic Explorer demo remains a UI preview only.
Integration does not advance the historical research phases.
