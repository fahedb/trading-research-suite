# ADR-0030 — Local Codex adapter for the frozen Gaps study

Status: ACCEPTED for the public integration, following the owner's instruction
to connect the existing Gaps research to Codex.

## Boundary

An external package in `integrations/codex/` provides a local stdio MCP server.
It depends on the engine; the engine never imports the adapter. Plugin metadata
and one workflow Skill live in `plugins/trading-research/`. Each environment keeps
its own lockfile. This introduces no engine stage or engine contract change.

The first template is the exact frozen Gap Sensitive Levels H1 TRAIN config.
It is pinned by its canonical configuration digest. Parameter changes, other
datasets, sweeps and protected-data unlocks are not exposed. The historical run
is read in place; no private datasets or run artifacts enter this repository.

## Observation and execution

The adapter reads versioned JSON run files, as the Explorer does. It validates
the run ID, containment, schemas, frozen config and recorded file hashes before
returning terminal results. It reports recorded status without reconciling stale
runs. It reads only metadata until the exact allowed TRAIN identity is checked.
Metrics are decoded from recorded float bits; additive fold values can be
summed, while drawdown is reported as the worst individual fold. No new research
statistic is computed. Missing files are reported, never replaced with example
results. The bundled study report is explicitly historical documentation.

Preparation checks configuration, manifest metadata, split-policy identity,
clean Git identity and lockfile identity. It does not read market partitions and
is not a full runtime preflight. Plans live outside the checkout and datasets.
Execution calls the existing detached engine CLI with the exact prepared inputs.
The engine remains responsible for integrity verification, access checks,
registration and lifecycle. New runs use a separate configured output root.

Before launch, a locked, durable request record is written. Repeating a plan's
start returns its recorded launch result. An interrupted/uncertain launch blocks
further launches pending operator inspection; it is never automatically retried.
Only one adapter-launched run may be active per workspace. This is an invocation
guard, not a replacement scheduler or run-state authority.

Explorer links use a configured loopback URL and report whether its run identity
matches. Read tools do not start servers, rebuild indexes or open market rows.
Native market keys retain broker wall-clock meaning. Unverified cost/execution
semantics and the Plane-B-first limitation accompany every results response.

## Consequences

This first integration requires the already admitted private TRAIN dataset for
a real rerun. Public installation alone supplies the code and study description,
not the owner's data. Personal import, catalog download and an engine-driven
synthetic installation example remain later work. A public-checkout rerun has a
new code identity, even if its strategy source matches the historical engine.
