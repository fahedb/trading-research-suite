# ADR-0013 — Lockbox enforcement model

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
A lockbox that is only documentation decays into another validation set.
On a single-user machine with owner privileges, absolute technical
prevention is impossible — the goal is that access can never be accidental
and never silent.

## Decision
Operational enforcement, V1 (owner-ratified):
1. **Discovery filter** — gated identities carry `discovery_default: false`
   and never appear in listings or resolve-by-role defaults. The set of
   gated identities is defined by range in
   [ADR-0014](ADR-0014-snapshot-and-view-roles.md) Amendment 1 (2026-08-06):
   anything extending past the OOS upper boundary — **the raw and curated
   parent datasets as well as the snapshot and lockbox views**. The ceremony
   below applies identically to all of them.
2. **Loader gate** — resolving a gated view raises unless the CLI was
   invoked with an explicit unlock flag AND the resolved experiment config
   carries a structured reason. Both are required so neither a copied
   config nor a habitual flag suffices alone.
3. **Write-ahead audit** — an append-only, fsync'd audit event is written
   BEFORE any data is returned; if the audit write fails, access is
   denied.
4. **Permanent taint** — the RunManifest and index carry
   `lockbox_touched: true` forever; a dedicated query lists such runs.
5. **Tests** — default discovery never yields gated roles; the gate raises
   without the ceremony.

**No agent filesystem hook in V1** (Phase 0/Phase 1) unless separately
approved. Out-of-band access by the owner via bespoke code remains
possible; it is covered by CLAUDE.md rules and by the detectability of a
missing audit trail. This residual risk is stated, not implied away.

## Implementation guidance — bounded architecture-test threat model (2026-08-08; no runtime decision change)

Architecture tests are structural regression guards against accidental and
ordinary architectural drift. They detect realistic API widening, aliases,
re-exports, wrappers, partials, canonical-binding substitutions and dynamic
module attributes that could plausibly arise during normal implementation or
refactoring. This is the test-layer expression of V1's accepted single-user
threat model: make accidental bypass structurally difficult and visible while
stating the residual owner-bypass risk honestly.

They are not a security sandbox against a malicious source-code author who
can also edit the tests, expected-origin tables, import machinery or source
itself. Deliberate import-time obfuscation, external monkeypatching, bytecode
or native-memory manipulation remain accepted residual risk for the V1 test
layer. Architecture tests protect the codebase against drift; they do not
protect the repository against their own author. Intentionally widening or
weakening an architecture allowlist, expected-origin table or guard is itself
an architecture-boundary change and receives the same review discipline as an
accepted ADR change.

The finite Slice 1D CP-3 mutation set and stopping rule are maintained in
[the Phase 1 plan](../phases/PHASE1_DATA_LAYER.md). This guidance does not
weaken or otherwise change the runtime gate, write-ahead audit, permanent
taint or residual owner-access decision above.

## Implementation guidance — the single approved Slice 1E composition route (2026-08-08; no runtime authorization change)

Slice 1D established that `MarketFrameLoader.load(AccessGrant, ...)` is the
sole public market-row loader/decoder. Slice 1E adds exactly one
owner-approved public **composition** entry point, `open_bounded_view(...)`,
and nothing else. This is a narrow, reviewed widening of the
architecture-test route table — **not** a change to the runtime
authorization decision recorded above.

The approved boundary:

- Its fixed dependency/authority inputs are explicit:

  ```
  open_bounded_view(
      *,
      loader: MarketFrameLoader,
      grants: Sequence[AccessGrant],
      context_start: NativeTimeKey,
      eval_start: NativeTimeKey,
      eval_end: NativeTimeKey,
      ...
  ) -> BoundedView
  ```

  Exact additional optional Slice 1E parameters may be derived during
  implementation from the approved contract.
- `loader` is the already-configured infrastructure dependency holding the
  `DatasetRegistry` and resolved `SplitPolicy`. It is **not authorization**:
  possession of a loader alone authorizes nothing.
- `grants` is non-optional authorization authority. Every contributing
  dataset/view requires its own exact grant, and every underlying
  `loader.load(...)` call receives the corresponding grant. The per-load
  `grant.require_matches(...)` discipline of Slice 1D is unchanged.
- `open_bounded_view` may orchestrate multiple existing loader calls, one per
  contributing authorized view. It is **not** an independent decoder, must
  not perform decoding or file I/O itself, and must not reauthorize grants.
- It must not rediscover or build a registry, resolve configuration from the
  environment or working directory, reconstruct a split policy, access paths
  directly, or create ambient/global loader state.
- It must **not** accept a registry, policy, bare `DatasetRef`,
  `DatasetIdentity`, `RegisteredManifest`, manifest object, filesystem path
  or root, or any caller-asserted authorization — including
  `already_authorized`, `trusted`, `unsafe` or any equivalent bypass flag.
- Loaded-data accessors on the returned `BoundedView` are **not** additional
  row loaders when they expose only data already composed and bounded during
  construction. The loader and grants are consumed during construction only;
  a returned view retains or exposes no `MarketFrameLoader`, `AccessGrant`,
  `DatasetRegistry`, `SplitPolicy` object, manifest object, filesystem path or
  root.

Slice 1E architecture tests must distinguish and explicitly constrain:

1. the sole public decoder / row loader, `MarketFrameLoader.load`; and
2. the one approved composition route, `open_bounded_view`.

The composition route may reference and call `MarketFrameLoader.load`, but
must satisfy every boundary above. No other new public data-package callable
may gain row-loading or composition capability without another owner-reviewed
architecture change. The tests enumerate this route rather than weakening or
removing the Slice 1D loader guard. This is a refinement of OD-1, not a
relaxation of the Slice 1D authorization boundary.

Gated roles are unaffected: OOS still requires explicit, recorded
authorization, and LOCKBOX still requires the full ceremony and audit. Each
member requires its own exact grant issued by the Slice 1C gate. Composition
creates no new access path and no new audit event type. ADR-0012 Amendment 2
never enumerated LOCKBOX as a fitting/promotion member and already requires
authorization for gated members such as OOS. Slice 1E makes the V1 boundary
explicit: `ViewUnion` supports TRAIN, VALIDATION and authorized OOS; LOCKBOX
is excluded from fitting/promotion while remaining available for fully
ceremonial bounded evaluation, as recorded in
[ADR-0012](ADR-0012-two-tier-splits.md).

## Alternatives considered
- Documentation-only protection — decays silently; rejected.
- Filesystem hardening (chattr +i, chmod) — needs root and/or is
  owner-bypassable; at best a tripwire, never the mechanism.
- Agent filesystem hook now — deferred by owner decision.

## Consequences
Accidental lockbox contact is structurally impossible through platform
code paths; deliberate access is ceremonial, reasoned, audited, and
permanently visible. Post-access consequences are research policy (the
period is spent for that line of research) — see RESEARCH_POLICY.md.

## When to reconsider
If the platform gains multiple users or remote execution, revisit
privilege separation (a dedicated user owning the lockbox files becomes
feasible and meaningful).

## Related
[ADR-0014](ADR-0014-snapshot-and-view-roles.md) ·
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) · V1.md §4.
