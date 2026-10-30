# trading-research — Codex project instructions

The Git repository is the source of truth.
AI memory and previous chats are not the source of truth.

## Read before work

At the start of every new task, read:

1. CLAUDE.md
2. PROJECT_STATE.md
3. docs/adr/ADR-0029-public-suite-distribution.md

Then read the relevant documents referenced by PROJECT_STATE.md, especially:

- docs/architecture/V1.md for architecture
- docs/phases/PHASE1_DATA_LAYER.md for Phase 1 implementation
- docs/roadmap/V1_ROADMAP.md for phase status
- relevant ADRs under docs/adr/ for architectural decisions

CLAUDE.md contains project-wide engineering and research-integrity rules.
Treat those rules as applying to Codex as well.

## Working rules

- Do not override an ACCEPTED ADR without explicitly raising an architecture
  conflict and stopping for owner review.
- Do not modify anything under the dataset root unless an accepted task
  explicitly creates a NEW allowed artifact outside the immutable dataset root.
- Never invent UTC semantics for market timestamps.
- Never bypass OOS, snapshot, raw/curated-parent, or lockbox access rules.
- Administrative manifest metadata may be inspected without access ceremony:
  identities, roles, native ranges, row counts, partition and lineage metadata,
  split-policy references, and classification diagnostics. Such inspection must
  never open Parquet or expose market rows, values, outcomes, charts, or
  statistics.
- Do not begin a later Phase or Slice unless PROJECT_STATE.md says it is NEXT
  and the user explicitly asks for it.
- Keep changes scoped to the requested Slice.
- Run the repository quality gate before claiming completion:

      ./scripts/gate.sh

- Update project documentation when the Phase/Slice plan requires it.
- Do not weaken architecture tests to make an implementation pass.
- If code behavior conflicts with accepted architecture, stop and report the
  conflict instead of silently changing the design.

## Current state

Do NOT hardcode the current Phase or Slice in this file.
Always obtain current project status from PROJECT_STATE.md.
