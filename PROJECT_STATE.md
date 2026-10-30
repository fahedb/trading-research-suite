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

The owner requested a Codex-first package with MCP tools and Skills. Initial
source mapping and the [first-release specification](docs/CODEX_MVP.md) are
complete. This is a plan; the MCP server, plugin, Skills and guided data import
are not implemented. The existing synthetic Explorer demo is a UI preview, not
an engine-driven research example.

NEXT in the public integration plan: a standalone, deterministic synthetic
example that executes the real engine and opens its recorded trades in the
real Explorer (step 1 in the specification). Integration work takes place in
this public distribution and does not advance the source project's historical
research phases. Both personal import and a downloadable catalog remain planned.
