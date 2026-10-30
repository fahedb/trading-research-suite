# ADR-0012 — Two-tier splits: integrity views and procedural folds (AO-2)

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
Materializing splits as curated views is leak-resistant, but applied
literally to walk-forward research it would demand dozens of view manifests
per fold-geometry configuration (Architectural Objection AO-2 in the freeze
proposal). Runtime date slicing, conversely, is the easiest leak vector in
the whole design.

## Decision
**Tier 1 — integrity splits** (TRAIN / VALIDATION / OOS / LOCKBOX):
materialized, hash-pinned curated views whose roles are enforced by the
loader. These are leak barriers. **Tier 2 — procedural folds**
(walk-forward windows, CV folds, embargo zones): runtime-generated
native-key ranges that must lie **entirely inside one Tier-1 view**
(runtime-checked), generated deterministically, and fully recorded in the
RunManifest. Folds never cross views: a study spanning TRAIN + VALIDATION
is a promotion decision, not a fold (see the adjacent-view union rule in
Amendment 2).

### Normative V1 split policy

**This section is the single normative source for the V1 split dates.**
Other documents (V1.md, RESEARCH_POLICY.md, PROJECT_STATE.md, CLAUDE.md)
summarize and link here; where they differ, this ADR governs.

| View | Range (`native-wallclock-v1`) |
|---|---|
| TRAIN | 2020-01-01 → 2023-12-31 |
| VALIDATION | 2024-01-01 → 2024-12-31 |
| OOS | 2025-01-01 → 2025-12-31 |
| LOCKBOX | 2026-01-01 → final observed bar on 2026-07-31 |

These dates are **research policy, not platform assumptions**: no platform
code may hardcode them. Changing them requires an ADR **and new view
identities** — never editing an existing view.

## Amendment 1 — mandatory purge at fit/evaluation boundaries (2026-08-06, finding B-2)

The original text listed "embargo zones" only as an example of a Tier-2
object, so nothing required one. Tier-1 boundaries are protected
structurally (view truncation destroys edge targets), but a Tier-2 fold
boundary *inside* one view was protected by nothing: a model fitted on
`[t0, t1]` with targets of span H trains on samples near `t1` whose targets
were computed from bars inside the evaluation range `(t1, t2]`. Similarity
reference libraries carrying future-outcome statistics have the identical
defect. The result is a silent, favourable-direction bias that grows with
horizon — on the platform's workhorse workflow.

**Sample information span.** Purge is not a single global constant. Every
training sample occupies an explicit span that the platform derives from
declared contracts, conceptually:

```
[ context_start ................ decision_key ................ target_end ]
   ^ earliest bar the sample's                                  ^ latest bar the
     features/representation/                                     declared OutcomeSpec
     model state actually read                                    needed for its target
```

- `context_start` comes from declared feature lookback / WindowSpec and any
  carried model or representation state.
- `decision_key` is the sample's own native key.
- `target_end` comes from the declared training OutcomeSpec (horizon,
  decision delay, alignment, gap/boundary policy) —
  [ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md).

**Rule.** No training sample whose span overlaps the evaluation range may
remain in the fit set. The platform **derives the minimum required purge
from the actual declared OutcomeSpec and sample information span** — never
from a hardcoded bar count — and applies it automatically at every Tier-2
fit/evaluation boundary.

Requirements:
1. **Automatic by default.** A fold geometry that omits purge does not exist;
   the orchestrator computes it.
2. **Recorded.** The applied purge (and its derivation inputs) is written to
   the RunManifest for every fold.
3. **Override only with a reason.** Reducing or disabling purge requires an
   explicit structured reason in the config; the reduction and its reason are
   permanently recorded. It never happens silently.
4. **Embargo is additive and optional.** A method may declare an embargo
   *after* the evaluation range (serial-correlation buffer) on top of the
   derived purge; it is recorded the same way.
5. **Coverage.** The rule binds supervised models, statistical models, and
   similarity/reference-library models alike — anything whose fitted state
   embeds target information.
6. **Tested at fold boundaries.** Walk-forward leakage rigs must exercise
   **fold** boundaries, not only Tier-1 view edges (Phase 2/3 acceptance).

The purge algorithm itself is deliberately **not** specified here; this ADR
fixes the contract and the acceptance obligation.

## Amendment 2 — view context semantics and adjacent-view composition (2026-08-06, finding B-4)

The original model defined only a view's upper bound ("the PIT boundary")
and left the lower bound undefined. Two consequences went unaddressed: a
lookback feature evaluated at a view's left edge either burns in its window
(computing on different history than at fit time — a silent train/eval
inconsistency) or crosses the boundary with no rule saying whether that is
legal; and the standard promotion refit on TRAIN + VALIDATION had no
expressible form, inviting exactly the hand-written date slicing this ADR
bans.

**Evaluation-view semantics.** A bounded evaluation view is defined by three
native keys:

| Field | Meaning |
|---|---|
| `context_start` | earliest bar readable for warm-up (features, representations, model state) |
| `eval_start` | first bar that may produce a prediction/metric |
| `eval_end` | last observable bar — **the point-in-time upper boundary** |

Rules:
1. Predictions, intents and metrics belong **only** to
   `[eval_start, eval_end]`. Context bars produce no scored output.
2. Backward context **may** extend before `eval_start` where features,
   representations or model state require it.
3. Backward context may come **only from strictly earlier authorized
   views** — never a later view, never a gated view unless that run holds
   the corresponding authorization
   ([ADR-0014](ADR-0014-snapshot-and-view-roles.md)).
4. Nothing reads past `eval_end`. OutcomeSpec remains the only component
   permitted to read forward, and only for evaluation targets
   ([ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md)).
5. Fold containment (Tier-2 ⊆ Tier-1) and purge apply to
   `[eval_start, eval_end]` together with the sample information span of
   Amendment 1.

**Adjacent-view composition.** A **fit** input may be a recorded, ordered
union of **adjacent, non-gated Tier-1 views** — the mechanical form of a
promotion decision. Each member view is pinned individually by id and
manifest hash, and the union is recorded in the RunManifest. Legal examples:

- TRAIN alone (default);
- TRAIN + VALIDATION, after an explicit promotion decision;
- TRAIN + VALIDATION + OOS, only where policy permits and OOS access is
  explicit and recorded ([ADR-0014](ADR-0014-snapshot-and-view-roles.md)).

Non-adjacent unions, gated members without authorization, and **arbitrary
hand-written date ranges as a substitute for view composition** remain
prohibited.

## Amendment 3 — exact split-policy identity (2026-08-07, Slice 1C owner decision)

ADR-0012 remains the human-readable normative source for split policy. The
platform also keeps one strict, machine-readable JSON record for each policy
version under `config/split-policies/`; the V1 record is
`adr-0012-v1.json`. It carries the native clock domain, time unit, calendar
policy, the four inclusive ranges, and provenance back to this ADR.

A resolved split policy is identified exactly by
`(adr, policy_version, policy_sha256)`, where `policy_sha256` is computed over
the file's exact bytes. The path is location, not identity: relocating
identical bytes preserves identity. Generated manifest format 1.1 therefore
pins all three fields in `split_policy_ref`; a logical `(adr,
policy_version)` match without the exact digest is insufficient for access.

A policy record is immutable once referenced. Any policy change requires a
new `policy_version`, exact digest, and new view identities; a change to the
research policy itself also requires an ADR amendment. Future experiment
fingerprints record the exact policy identity for every role, including
TRAIN and VALIDATION, so ordinary access cannot drift under a same-named
policy either.

## Implementation guidance — Slice 1E view composition (2026-08-08; no decision change)

Recorded from the owner-approved Slice 1E decisions. This guidance
operationalizes Amendment 2 for implementation; it does not change the
decision or the normative split policy above. The maintained detail lives in
[the Phase 1 plan](../phases/PHASE1_DATA_LAYER.md) (Slice 1E, OD-4 and OD-5).

### Evaluation roles vs. fitting-union roles are deliberately different

| Surface | Legal Tier-1 roles |
|---|---|
| bounded **evaluation** view (`BoundedView`) | TRAIN · VALIDATION · OOS · **LOCKBOX** |
| V1 **fitting/promotion** union (`ViewUnion`) | TRAIN · VALIDATION · OOS — **LOCKBOX forbidden** |

A bounded evaluation view may use those Tier-1 evaluation roles, with exact
valid authorization for **every** contributing member. LOCKBOX evaluation
remains fully ceremony-gated and audited through
[ADR-0013](ADR-0013-lockbox-enforcement.md) and
[ADR-0014](ADR-0014-snapshot-and-view-roles.md); nothing here creates a new
or easier LOCKBOX route.

RAW, CURATED and SNAPSHOT are **not** legal research members of either
surface.

Amendment 2 never enumerated LOCKBOX as a fitting/promotion member and
already requires authorization for gated members such as OOS. Slice 1E makes
the V1 implementation boundary explicit: `ViewUnion` supports TRAIN,
VALIDATION and authorized OOS; LOCKBOX is excluded from fitting/promotion
while remaining available for fully ceremonial bounded evaluation.

### "Consecutive" means policy-role order, not key arithmetic

Backward-context members must form a **consecutive Tier-1 policy-role chain
ending at the evaluation view**:

| Chain | Outcome |
|---|---|
| TRAIN → VALIDATION | legal |
| VALIDATION → OOS | legal with OOS authorization |
| TRAIN → VALIDATION → OOS | legal with required authorization |
| TRAIN → OOS, omitting VALIDATION | **refused** |
| reordered or duplicated roles | **refused** |

"Consecutive" means consecutive in the **resolved split-policy role order**.
It explicitly does **not** mean `next.start == previous.end + 1`, nor
`next.start == previous.end + timeframe`, nor that physical/native keys are
arithmetically contiguous, nor that bars exist at every expected interval.

**Observed-key gaps are legal facts and do not break authorization
adjacency** — consistent with [ADR-0005](ADR-0005-gapindex-not-sessionmodel.md),
where gaps are arithmetic provenance rather than semantics. A fixture whose
TRAIN segment ends at key 202 while VALIDATION begins at key 1100 is still a
valid adjacency case and must remain one.

The rule must be derived from exact split-policy role order and identity
(Amendment 3's `(adr, policy_version, policy_sha256)`), never from hardcoded
V1 dates or naive key arithmetic — the same prohibition the normative policy
table above already states.

## Alternatives considered
- All splits materialized, folds included — impractical view explosion.
- All-runtime slicing by config — flexible and leak-prone; rejected.

## Amendment 4 — clock-domain-specific policy projections (2026-09-09)

The owner approved onboarding `BTCUSD.s` as a second, independent
single-symbol MT5 dataset.  It comes from `JustMarkets-Demo`, while the
existing `XAUUSD.s` data comes from `JustMarkets-Demo3`; ADR-0002 therefore
requires distinct clock domains and prohibits pretending that the two axes are
interchangeable.

The normative calendar split dates above remain unchanged.  A machine-readable
policy is now a clock-domain-specific projection of those dates: each dataset
uses an immutable policy record carrying its own exact clock domain and digest.
`config/split-policies/adr-0012-v1.json` remains the existing XAUUSD projection;
`config/split-policies/adr-0012-btc-v1.json` is the BTCUSD projection.  A run
still contains exactly one symbol and one market frame, so this does not add a
cross-symbol join, portfolio path, or cross-clock-domain resolver and does not
widen ADR-0015.

Adding another projection never authorizes protected data.  RAW, CURATED,
SNAPSHOT and LOCKBOX remain fully gated; OOS remains explicit and audited; and
every generated view and experiment pins the exact policy digest it used.

## Consequences
Walk-forward stays practical; the barriers stay hash-pinned. The fold ⊆
view check, deterministic fold generation, derived-purge application, and
context/eval-boundary semantics are tested platform invariants. Fold
geometry now depends on the declared OutcomeSpec, so a horizon change
legitimately changes the usable fit set — that is the contract working, not
a regression.

## When to reconsider
If a legitimate research procedure requires folds spanning integrity
boundaries, that is a research-policy change: new Tier-1 views by ADR. If
purge derivation proves too conservative for a specific method, tighten the
declared information span — never the enforcement.

## Related
[ADR-0008](ADR-0008-fit-lifecycle-and-fittedstate.md) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0013](ADR-0013-lockbox-enforcement.md) ·
[ADR-0014](ADR-0014-snapshot-and-view-roles.md) ·
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) · V1.md §4.
