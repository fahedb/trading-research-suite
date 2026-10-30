# ADR-0031 — Describe, freeze and integrate a new experiment

Status: ACCEPTED for this distribution, following the owner's request for a
Skill that resolves an experiment's missing details before integrating it.

## Decision

Add a general experiment-authoring Skill to the existing plugin, with structured
specification review and immutable specification storage in the external MCP
adapter. A specification is an agreement about the research question and its
semantics, not an engine configuration or proof of runtime readiness. This
does not add an engine stage or change any engine public contract.

The assistant extracts known requirements, asks only material unanswered
questions, and presents the concrete experiment before freezing it. A freeze
records the exact specification digest and the user's already-given authorization
to integrate, or integrate and run. The authorization record is assistant-reported
conversation evidence, not user authentication or permission to bypass access
controls. Do not require repeated approval when the existing instruction covers
the exact specification; do not infer acceptance of new substantive assumptions.

Unresolved strategy semantics block freezing. Known implementation gaps may
remain: identifying those gaps is the next stage. Classify each dependency as
available, derivable, needing implementation, needing source data, or unsupported.
Preserve an immutable revision chain; changing the frozen hypothesis requires a
new revision. Resuming work reads the saved specification and compares its digest.

The Skill directs an authorized coding-capable Codex host to integrate missing
components through the existing feature/model/decision/outcome contracts, in an
isolated checkout, with the repository's required tests. Pure MCP clients lacking
code-editing capabilities receive an explicit integration handoff. An arbitrary
shell/code-execution MCP tool is not introduced. Source data cannot be invented,
silently filled, downloaded from arbitrary URLs or admitted by changing a role.

Existing ADR-0030 Gaps tools remain limited to that exact template. General
specification tools do not call those launch tools or disguise new experiments
as Gaps. Generic run preparation/launch, CSV admission and catalog download remain
separate implementation work. The authoring Skill must report those limitations
and never equate a frozen specification or passing schema with a runnable study.

## Research integrity

Preserve the Plane-A/Plane-B distinction, native clocks, finite lookback, causal
signal availability, strict data access, immutable results and unverified cost
semantics. A historic Plane-B-first exception applies only to its original study.
Any new exception needs its own explicit owner instruction. External timed feeds
and cross-clock joins remain unsupported by V1; a Skill cannot override that.

## Verification

Exercise incomplete and complete descriptions, simulation-only requirements,
missing versus unimplemented data, digest-bound approval, concurrent/idempotent
freezes, revision lineage, symlink refusal and read-only access. Validate Skill
packaging and discovery. Mechanical checks do not prove conversational judgment;
keep that distinction visible in the release notes.
