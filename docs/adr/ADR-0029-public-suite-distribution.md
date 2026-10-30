# ADR-0029 — Combined public source distribution

Status: ACCEPTED for this distribution, following the owner's request to publish
a combined copy of the engine and Explorer in a new repository.

## Context

The source system kept the engine and Explorer in sibling repositories to avoid
changing research identities during viewer development. The public distribution
needs one entry point and one clone containing both components.

## Decision

The engine occupies the repository root and the Explorer lives in `explorer/`.
Each component keeps its own environment and lockfile. The Explorer depends on
the engine through an editable `..` path dependency.

The existing conservative fingerprint and cache rules remain in effect: the
platform identity uses this repository's Git commit and dirty state. UI-only and
documentation-only commits change future fingerprints and cache keys. Dirty
worktrees remain noncanonical. No code-identity fields or research guards are
removed or narrowed.

This decision supersedes the separate-repository placement requirement in the
source Explorer architecture for this public copy only. Read-only artifact
access, native time semantics, and protected-data access rules remain applicable.

The demo is a separate FastAPI application, serving generated illustrative data
in memory. It never imports the real viewer's data readers or research runner.
Its pages and API responses explicitly identify the data as synthetic.

## Consequences

One clone contains the complete source. Maintaining two environments avoids
mixing UI server dependencies into the engine environment. Whole-repository code
identity can cause extra recomputation after interface changes; this is accepted
for this distribution. Historical source-system run identities cannot be
reproduced from this new history and are not claimed to be preserved here.
