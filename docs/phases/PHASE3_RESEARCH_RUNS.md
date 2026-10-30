# Phase 3 — Models, Decisions, Forecast Evaluation & Runs — Implementation Plan

**Status:** **PHASE 3 COMPLETE (2026-08-14) · SLICES 3A–3J COMPLETE ·
CP-9/CP-10/CP-11/CP-12 GREEN · CP-12 OWNER APPROVED.**
This document is **normative for Phase 3 execution**, in the role
[PHASE1_DATA_LAYER.md](PHASE1_DATA_LAYER.md) plays for Phase 1 and
[PHASE2_FEATURES.md](PHASE2_FEATURES.md) plays for Phase 2. The contract it
operationalizes is
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md), together with the
already-accepted ADR-0006…ADR-0012 and ADR-0016…ADR-0018.

**Planning record:** 2026-08-13 · **Planning baseline:** `main` @
`6767fbd5e4b6f85d2eaf1fcbc449e79abc04816e` (clean worktree) ·
**Owner decisions:** OD-3A…OD-3F, approved 2026-08-13; OD-3G, approved
2026-08-14 ·
Architecture reference: [docs/architecture/V1.md](../architecture/V1.md) ·
Roadmap entry: [docs/roadmap/V1_ROADMAP.md](../roadmap/V1_ROADMAP.md)
(status only)

> **Historical execution record.** Phase-3 implementation, qualification and
> independent closeout are complete. Phase 4 has not begun.

---

## 1. Owner-approved decisions — **OD-3A…OD-3F APPROVED 2026-08-13;
OD-3G APPROVED 2026-08-14**

### OD-3A — Initial OutcomeSpec — **OWNER APPROVED**

Phase 3 starts with **exactly one** concrete OutcomeSpec:
**`forward_log_return_v1`**. No speculative outcome library is created.

V1 parameters and semantics:

| Field | Rule |
|---|---|
| `horizon_rows` | explicit positive `int`, measured in **observed rows**, never elapsed clock time |
| `decision_delay_rows` | explicit `int`; **V1 legal value is exactly `1`**; no default |
| `reference_price_column` | explicit market column name; no default |
| `boundary_policy` | `"nan"` in V1 (see OD-3A.3) |
| Alignment | the target is attached to the **decision key** |

Additional approved rules:

1. **No implicit or default target.** The config names the OutcomeSpec
   explicitly ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)
   Amendment requirement 1).
2. **Current MT5 native time semantics remain untouched.** No UTC, session,
   holiday, weekend, outage or market-closure semantics is invented anywhere on
   the outcome path.
3. Row-based horizons are required rather than preferred, for the same reason
   [ADR-0021](../adr/ADR-0021-columnar-feature-contract.md) §2 gives for feature
   reach: a time-distance horizon over a broker-wall-clock axis containing
   thousands of discontinuities would have to attribute meaning to a multi-day
   key delta, which [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md)
   forbids asserting.

**Verification: CONSISTENT.** ADR-0010 names *"horizon (**observed bars** or
another explicit unit)"* as the first option, requires a reference price, a
decision delay, a gap/boundary policy and alignment rules — all of which this
spec declares.

### OD-3A.1 — Decision timing and alignment — **OWNER APPROVED**

**A decision based on observed row `X` uses row `X+1` as the measurement
origin**, represented through ADR-0010's decision-delay contract as
`decision_delay_rows = 1`, explicit with no default.

**Why: this is the owner-approved V1 OutcomeSpec alignment policy — a deliberate
research-definition choice.** It is recorded so it is neither re-litigated nor
re-justified with a different rationale.

Two justifications are **explicitly rejected** and must not appear in any
repository document:

- **It is not a leakage rule.** Defining a forecast from a price known at `X` to
  a later price is a legitimate, leakage-free construction, provided row `X` was
  genuinely known at decision time. No document may state or imply that an
  `X`-origin outcome inherently reads the future.
- **It is not an execution model.** No fill, spread, slippage, commission or
  order semantics is imported from Phase 4 to justify it. Those remain
  quarantined by [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) and
  have no place in Plane-A forecast evaluation.

A future research family wanting a different alignment changes
`decision_delay_rows` — an identity-bearing parameter — under the normal
reviewed process. Nothing here makes any particular value more "correct" than
another on leakage grounds.

### OD-3A.2 — Horizon arithmetic — **OWNER APPROVED (Reading A)**

For a decision row at axis position `p`:

```
p_origin   = p + decision_delay_rows          # V1: p + 1
p_terminal = p_origin + horizon_rows          # V1: p + 1 + horizon_rows
target     = log( P[p_terminal] / P[p_origin] )
```

Worked, with `decision_delay_rows = 1`:

| Decision | Origin | `horizon_rows` | Terminal |
|---|---|---|---|
| `X` | `X+1` | `1` | `X+2` |
| `X` | `X+1` | `20` | `X+21` |

**`horizon_rows = H` therefore means `H` observed price-movement steps from the
measurement origin.** This avoids the degenerate reading in which
`horizon_rows = 1` would compare `X+1` with itself.

Every derived quantity is a direct sum, so no subtraction and therefore no
off-by-one is possible — the same discipline that made `max_prior_rows`
off-by-one-proof in Phase 2:

| Quantity | Value |
|---|---|
| Measurement origin position | `p + decision_delay_rows` |
| Terminal position | `p + decision_delay_rows + horizon_rows` |
| Future rows required beyond the decision row | `decision_delay_rows + horizon_rows` |
| `target_end` for ADR-0012 purge | key at `p + decision_delay_rows + horizon_rows` |

**This decision is CLOSED.**

### OD-3A.3 — End-of-data boundary — **OWNER APPROVED**

A target is valid **only when the complete requested horizon exists**. If
`p_terminal` does not exist on the resolved axis, the target is
**`NaN` / unavailable**.

**The horizon is never truncated to the last available row.** A shortened
seven-row return must never silently enter a declared twenty-row outcome; the
two are different random variables, and mixing them in one column is a research
error, not a rounding detail. This is the same reasoning ADR-0021 §3 gave for
refusing `TRUNCATE` in feature windows.

V1 boundary behaviour is therefore **`boundary → NaN`**. `truncate` and `skip`
remain reserved ADR-0010 vocabulary, **unimplemented in V1**, refused with an
explicit message.

### OD-3A.4 — Observed-key discontinuities — **OWNER APPROVED**

Observed-row horizons **cross** arithmetic discontinuities. A discontinuity does
not make an observed-row horizon incomplete: twenty observed rows remain twenty
observed rows, however much wall-clock they span.

It must never be inferred that a discontinuity means a weekend, session
boundary, holiday, outage or closure. These remain arithmetic facts under
ADR-0005. Refusing to compute a return across a discontinuity would itself be
such an inference.

**Reconciliation with ADR-0010 — verified, no amendment required.** ADR-0010
requires an OutcomeSpec to define a *"gap/boundary policy
(`NaN | truncate | skip`)"*. That phrase occurs **exactly once in the entire
accepted ADR corpus** and fixes two things only: the vocabulary, and the
obligation for a spec to declare such a policy. It specifies no trigger
conditions, does not require all three values to be implemented, and nowhere
requires a discontinuity to render a horizon unavailable. The V1 trigger
semantics are therefore a delegated implementation detail, recorded in
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §9:

| Trigger | V1 behaviour |
|---|---|
| End-of-data boundary | `NaN` / unavailable |
| Observed-key discontinuity | does not fire; the horizon continues over the observed rows |

**Naming.** The outcome field is `boundary_policy`, a **distinct type** from
ADR-0021's feature-window `GapPolicy` (`CROSS | BREAK`). They govern different
axes and must never share an enum; an architecture guard enforces the
separation.

### OD-3B — Determinism — **OWNER APPROVED**

1. **Exact comparison is the default.**
2. **Structural and discrete information is always exact** and can never be
   toleranced.
3. **Floating-point artifacts also begin exact by default.**
4. A specific model or artifact type that **genuinely cannot** reproduce
   bit-for-bit may declare a justified tolerance.
5. Such a tolerance must be **explicit, visible, identity-bearing and
   recorded**.
6. **A tolerance is never a CLI or verifier knob** that can be loosened after
   seeing a failure.
7. When a tolerance is legitimately required the comparison form is combined —
   `|a − b| <= atol + rtol · |b|` — with artifact/plugin-specific declared
   values.
8. The determinism verifier reports the **observed deviation**, not merely
   pass/fail.
9. **No claim** is made that Phase 2 proves future ML or BLAS-heavy algorithms
   are bit-exact. Phase 2 demonstrated deterministic vectorized array arithmetic
   on this machine and environment only.
10. **No cross-hardware bitwise reproducibility is claimed** anywhere.

Per-artifact defaults:

| Artifact | Default |
|---|---|
| Manifests, identities, fingerprints, schemas, column order, key sets, dtypes, sparse presence, run states, inventory rows | **exact — tolerance never admissible** |
| `TrainingTargetFrame` | exact |
| `FittedState` — identity/metadata | exact |
| `FittedState` — numeric payload | exact by default; declared opt-in |
| `ModelOutputFrame` — structure | exact |
| `ModelOutputFrame` — numeric columns | exact by default; declared opt-in |
| `IntentFrame` including the float column | exact (the trichotomy is semantic, not numerical) |
| Plane-A metrics | exact by default; opt-in requires a stated reason |

**Inherited obligation.** ADR-0016's *"the verification report is a stored
artifact"*, explicitly deferred by [PHASE2_FEATURES.md](PHASE2_FEATURES.md) §4.4
because Phase 2 had no run directories, **is discharged in Phase 3** (Slice 3H
writes `determinism_report.json`; Slice 3I produces it).

### OD-3C — FittedState materialization — **CONFIRMED, NOT REOPENED**

Already settled by accepted ADRs and restated here only because the roadmap had
carried it as an open item:

- **refit-per-run is the V1 default** —
  [ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md), Consequences;
- **`FittedState` uses `cache_if_expensive`** —
  [ADR-0007](../adr/ADR-0007-materialization-policy.md), Decision, and ADR-0008.

**Inherited obligation.** ADR-0007's rule that *"the orchestrator must upgrade
`ephemeral` when a downstream consumer is configured"* was recorded by
PHASE2_FEATURES.md §4.2 as a Phase-3 obligation because Phase 2 had no
orchestrator. **It is discharged in Slice 3I.**

### OD-3D — SQLite deferred to Phase 5 — **OWNER APPROVED**

Persistent SQLite run indexing is **deferred to Phase 5**. Phase 3 provides the
minimal files-first mechanism: **`scan_runs()`**, which reconstructs structured
run records from run directories.

Kept deliberately minimal: **no query DSL, no persistent cache, no indexing
infrastructure.** Ordinary Python filtering over the returned records is enough.

When SQLite arrives in Phase 5 it must remain a **derived, rebuildable
convenience** over these files — and `scan_runs()` is the rebuild path, so
"rebuild from files" is true by construction rather than through a second code
path that can drift.

> **Forward reference (2026-08-15, OD-3D unchanged).** Phase-5 planning found
> that `scan_runs()` **mutates**: it locks each run directory exclusively and
> can terminalize a stale-heartbeat `RUNNING` run to `INTERRUPTED`. The
> conclusion above stands — the index is still built from the files-first scan,
> not a second implementation — but it must use the scanner's **read-only
> mode**, added in Phase-5 Slice 5A. Recorded normatively in
> [ADR-0017](../adr/ADR-0017-storage-files-as-truth.md) Amendment 1 and planned
> in [PHASE5_EXPERIMENT_OPS.md](PHASE5_EXPERIMENT_OPS.md) §7 and §12.

**Approved acceptance-criterion change:** the Phase-3 criterion *"index rebuild
golden test"* becomes **"run-record scan golden test"**. Recorded in the roadmap.

**Verification: CONSISTENT.** [ADR-0017](../adr/ADR-0017-storage-files-as-truth.md)
defines SQLite as *"a derived, rebuildable query index only"*, and
[ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) Amendment 3
explicitly permits deriving `attempt_number` *"at query time from the run
directories rather than storing a racing counter"*, which removes the only
apparent need for a database in Phase 3.

### OD-3E — RepresentationModel — **OWNER APPROVED**

`RepresentationModel` is included in the Phase-3 fit lifecycle. Purpose:

- learned preprocessing such as scaling must be fitted **only on permitted
  training data**;
- fitted preprocessing **must not be hidden inside another stage**;
- representation fitted state **participates in identity and provenance**.

Scope is minimal: the contract and lifecycle plus **exactly the minimum
reference representation needed to prove the lifecycle**. **No representation
library** — no PCA, no embeddings, no dimensionality-reduction family.

**Verification: CONSISTENT.** ADR-0008 states it directly: *"RepresentationModel
participates in the same lifecycle (a scaler or PCA is fitted state; hiding it
would recreate the problem)."*

### OD-3F — No carried-state or online inference in V1 Phase 3 — **OWNER APPROVED**

**No generic model or representation `max_prior_rows` / carried-state API is
built.** The declaration field proposed during planning is **rejected and
withdrawn**.

Supported adaptive models use:

```
declared features / current permitted inputs
    + fixed FittedState learned from TRAIN
    -> ModelOutputFrame
```

A regime-adaptive model is allowed and expected to work when its market-state
inputs are ordinary declared features whose history and reach are already
controlled by Phase 2's `max_prior_rows`.

**Row-local inference invariant for V1 Phase 3:**

> Output at evaluation row *i* depends only on the declared input values for row
> *i* plus the fixed `FittedState`. It must not depend on other rows of the
> evaluation range.

Explicitly **out of scope**:

- mutable state carried row to row during inference;
- online or incremental learners;
- inference whose result at row *i* depends on other evaluation rows.

**Reconsideration trigger:** a concrete model family that genuinely cannot be
expressed as row-local inference over declared inputs plus a fixed
`FittedState`. At that point the lifecycle is extended through a future ADR,
which must also define the carried-state information-reach contract honestly.
**The capability is not pre-built now.**

**Enforcement is by adversarial rigs, not by a declaration field** — forward and
backward/row-locality perturbation rigs over every registered model and
representation (Slice 3D).

**Reconciliation with ADR-0012.** Amendment 1 says `context_start` comes from
*"declared feature lookback / WindowSpec **and any carried model or
representation state**"*. The second term is a **conditional contribution**, and
the diagram annotates `context_start` as *"earliest bar the sample's
features/representation/model state **actually read**"*. Under the row-local
invariant nothing beyond declared features is read, so the term contributes
nothing and the information span is fully determined by declared feature reach
and the declared OutcomeSpec. Amendment 1's own **Coverage** clause confirms the
rule's target is *"anything whose **fitted state embeds target information**"* —
the `target_end` side — which ADR-0008's FittedState identity already pins.

### OD-3G — Fit result and FittedState identity authority — **OWNER APPROVED 2026-08-14**

A model or representation `fit` returns the small, identity-free
`FittedPayload` defined by
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 3. The
platform's `experiments` composition root is exclusively responsible for
combining that payload with the complete eleven-component identity and
provenance and minting the private-construction `FittedState`. Plugins receive
no split, purge, platform, dependency, storage or materialization authority.

This decision owner-approves the mid-implementation contract correction exactly
as reviewed and implemented. It changes none of Amendment 3's substance and
adds no research capability.

---

## 2. Slice-3A public vocabulary — concrete and closed

**This section is closed. Slice 3A implements exactly these types and no
others.** No placeholder remains; Codex must not invent a public type during
implementation. If a type appears to be missing, that is a planning defect to
raise, not a coding decision. Recorded in
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 1.

Public column contracts remain read-only NumPy arrays; no
PyArrow/pandas/Polars/DuckDB type appears in any public signature. Module
placement inside `core` is an implementation detail; the **layer** is not.

### 2.1 Dependency direction — the constraint that shapes everything here

`core` imports nothing internal, and `models`/`representations`/`decisions`/
`evaluation` import only `core`. **`BoundedView` lives in `data` and therefore
cannot appear in any `core` signature.** Phase 2 solved this exact problem and
its solution is the precedent: `FeatureRequest` is a `core` type built from
`core` primitives, and `experiments/features.py::compute_features` performs the
sole `BoundedView` → primitives adaptation. Phase 3 follows it: the
`BoundedView` → `ViewSpan` adaptation lives only in `experiments`.

**No storage or data-layer type is leaked into a public research contract.**

### 2.2 Core-owned public types

```
ViewSpan (frozen, core)                       # descriptor, NOT a data handle
    source_identities: tuple[DatasetIdentity, ...]     # ordered
    unit: TimeUnit · clock_domain: ClockDomain
    context_start: NativeTimeKey
    eval_start: NativeTimeKey · eval_end: NativeTimeKey
    # exposes no rows, no columns, no loader, no grant.
    # ONE type serves both the fit-time and inference-time positions.

StageOrigin (frozen, core)                    # per-column provenance
    plugin_id: str · version: int · params: Mapping[str, ParamValue]
    # reused by RepresentationFrame, ModelOutputFrame, TrainingTargetFrame
    # and OutcomeFrame. FeatureFrame keeps its existing FeatureOrigin, which
    # additionally carries a WindowSpec that no other stage has.

ToleranceSpec (frozen, core)
    atol: float >= 0 · rtol: float >= 0 · reason: str (non-empty)
    # declared per plugin as `comparison_tolerance: ToleranceSpec | None`;
    # None means exact and is the default requiring no declaration.

MetricValue (frozen, core)
    name: str · value: float          # NaN when the metric is undefined
    effective_samples: int >= 0

ModelInputs (frozen, core)
    features: FeatureFrame
    representation: RepresentationFrame | None
    # the COMPLETE input surface of a ResearchModel, and of nothing else.
    # No MarketFrame, no market column, no FeatureRequest, ever.
    # A RepresentationModel does NOT receive this type -- it takes a
    # FeatureFrame directly, so a representation can never consume another
    # representation (see section 2.6).

RepresentationFrame (frozen, core, private construction)
    keys -> read-only int64, dense, == the evaluation axis
    unit · clock_domain · num_rows · source_identities
    column_names · schema (all "float64") · column(name) -> read-only float64
    origin(name) -> StageOrigin
    # +-inf refused; NaN means unavailable at that key.

ModelOutputFrame (frozen, core, private construction)
    keys -> read-only int64, strictly increasing, MAY be a strict subset
    unit · clock_domain · num_rows · source_identities
    column_names · schema · column(name) · origin(name) -> StageOrigin
    # generic named numerics; NO required trading vocabulary.

TrainingTargetFrame (frozen, core, private construction)
    keys · unit · clock_domain · num_rows · source_identities
    column_names · schema · column(name) · origin(name) -> StageOrigin
    # the ONLY type `fit` accepts as training_targets.

OutcomeFrame (frozen, core, private construction)
    # structurally identical to TrainingTargetFrame;
    # the ONLY type an Evaluator accepts as outcome targets.

IntentFrame (frozen, core, private construction)
    intent_schema_id: str             # V1: exactly "target_exposure_intent_v1"
    keys -> read-only int64, strictly increasing, MAY be a strict subset
    unit · clock_domain · num_rows
    target_exposure -> read-only float64 in [-1, +1] or NaN; +-inf refused
    # absent row = no statement · NaN = explicit abstain · 0.0 = explicit flat

FittedPayload (frozen, core, public construction)
    array_names -> tuple[str, ...]
    array(name) -> fresh read-only non-object numeric NumPy array
    metadata -> immutable Mapping[str, ParamValue]
    # the COMPLETE output surface of plugin fit, and nothing more.
    # Contains learned content only: no identity, view, split, purge,
    # platform, dependency, storage or materialization authority.

FittedState (frozen, core, private construction)
    identity_key() -> 64 lowercase hex        # field list in section 2.4
    payload -> FittedPayload
    # minted only by the experiments composition root after binding every
    # identity component; serialized as versioned arrays + JSON, never pickle.

BoundaryPolicy (StrEnum, core)    NAN        # V1: the only member.
    # a DISTINCT type from ADR-0021's feature-window GapPolicy (CROSS|BREAK);
    # an architecture guard asserts the two never share an enum.

OutcomeRequest (frozen, core, private construction)
    # the platform's ONLY forward-reading input carrier, built from
    # already-issued MarketFrames exactly as FeatureRequest is.
    keys -> read-only int64 over the resolved axis, strictly increasing
    column(name) -> read-only array; ONLY the spec's declared columns
    decision_offset: int · decision_length: int
    # rows to compute targets FOR; the key axis deliberately extends BEYOND
    # them, which is the forward asymmetry that makes a distinct type honest.
```

### 2.3 Core-owned public protocols

```
OutcomeSpec (Protocol)
    outcome_id: str · version: int · params: Mapping[str, ParamValue]
    horizon_rows: int > 0                  # explicit, no default
    decision_delay_rows: int               # V1: exactly 1, explicit, no default
    reference_price_column: str            # explicit, no default
    boundary_policy: BoundaryPolicy        # V1: NAN
    required_columns: tuple[str, ...]
    target_column_names: tuple[str, ...]
    comparison_tolerance: ToleranceSpec | None
    compute(request: OutcomeRequest) -> Mapping[str, NDArray[float64]]
    # the platform wraps the result into a TrainingTargetFrame or an
    # OutcomeFrame according to which range it requested. A spec never
    # chooses which carrier it produces.

ResearchModel (Protocol)
    model_id: str · version: int · params: Mapping[str, ParamValue]
    required_feature_names: tuple[str, ...]
    required_representation_names: tuple[str, ...]      # empty when unused
    output_column_names: tuple[str, ...]
    cost_hint: CostHint
    comparison_tolerance: ToleranceSpec | None
    fit(view: ViewSpan, inputs: ModelInputs,
        training_targets: TrainingTargetFrame | None = None) -> FittedPayload
    infer(view: ViewSpan, fitted_state: FittedState,
          inputs: ModelInputs) -> ModelOutputFrame

RepresentationModel (Protocol)             # same lifecycle, emits columns
    representation_id: str · version: int · params: Mapping[str, ParamValue]
    required_feature_names: tuple[str, ...]
    output_column_names: tuple[str, ...]
    cost_hint: CostHint
    comparison_tolerance: ToleranceSpec | None
    fit(view: ViewSpan, features: FeatureFrame) -> FittedPayload
    encode(view: ViewSpan, fitted_state: FittedState,
           features: FeatureFrame) -> RepresentationFrame
    # Takes a FeatureFrame DIRECTLY, never ModelInputs. It therefore cannot
    # receive another RepresentationFrame, which makes representation
    # chaining structurally unavailable rather than merely discouraged.
    # Never receives training_targets: a representation has no target channel.
    # Never receives a MarketFrame or a market column.

DecisionPolicy (Protocol)
    policy_id: str · version: int · params: Mapping[str, ParamValue]
    required_output_columns: tuple[str, ...]
    intent_schema_id: str
    decide(model_output: ModelOutputFrame) -> IntentFrame

Evaluator (Protocol)
    evaluator_id: str · version: int · params: Mapping[str, ParamValue]
    required_output_columns: tuple[str, ...]
    required_outcome_columns: tuple[str, ...]
    metric_names: tuple[str, ...]
    comparison_tolerance: ToleranceSpec | None
    evaluate(model_output: ModelOutputFrame,
             outcome_targets: OutcomeFrame) -> Mapping[str, MetricValue]
```

**The resulting fixed-stage flow is exactly ADR-0006's, with one optional
representation stage and no way to build a second:**

```
FeatureFrame
    |
    v  RepresentationModel.encode(view, fitted_state, features)
RepresentationFrame                       <-- optional; at most ONE stage
    |
    v  ModelInputs(features, representation?)
ResearchModel.infer(view, fitted_state, inputs)
    |
    v
ModelOutputFrame
    |
    v  DecisionPolicy.decide(model_output)
IntentFrame
```

`ModelInputs` is accepted by `ResearchModel` **and by nothing else**;
`RepresentationModel` takes a `FeatureFrame` directly. There is therefore no
parameter anywhere through which a `RepresentationFrame` could reach a
`RepresentationModel`.

The fit side has one equally fixed authority boundary:

```
declared inputs -> plugin.fit(...) -> FittedPayload
complete caller-supplied identity + FittedPayload -> orchestrator -> FittedState
```

Plugins can construct `FittedPayload` but cannot construct `FittedState` and do
not receive the platform-only values needed to identify one. This is
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 3.

### 2.4 `FittedState` identity — the complete pinned field list

Governed by [ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md), its
amendment, and ADR-0022 Amendment 2. **All eleven components are required:**

| # | Component | Source |
|---|---|---|
| 1 | model/representation `id`, `version`, canonical `params` | ADR-0008 |
| 2 | ordered feature identities (provider id/version/params/window per consumed column) | ADR-0008 |
| 3 | representation identity, where a representation was used | ADR-0008 |
| 4 | training `ViewSpan` — ordered source identities, `context_start`, `eval_start`, `eval_end`, `unit`, `clock_domain` | ADR-0008 ("training DatasetRef/view range") |
| 5 | split-policy identity `(adr, policy_version, policy_sha256)` | ADR-0012 Amendment 3 |
| 6 | training OutcomeSpec `id`, `version`, canonical `params`, resulting target range, alignment | ADR-0010 Amendment req. 4 |
| 7 | applied purge and embargo, with their derivation inputs | ADR-0012 Amendment 1 |
| 8 | platform code identity — Git commit, dirty flag, diff digest | ADR-0008, ADR-0011 |
| 9 | **dependency identity — content digest of the resolved dependency lockfile, caller-supplied** | **ADR-0008; added by ADR-0022 Amendment 2** |
| 10 | seed, where relevant, as an ordinary canonical param | ADR-0007 precedent |
| 11 | declared `comparison_tolerance` (`None` = exact) | ADR-0022 §8 |

**`StageIdentity` does not satisfy this.** Its hashed document contains exactly
`source_identities`, `bounds`, `split_policy`, `providers`, `platform` —
verified in `artifacts/identity.py`. It carries **no dependency identity**;
Phase 2 excluded the `uv.lock` digest from it with explicit owner approval
([PHASE2_FEATURES.md](PHASE2_FEATURES.md) §4.1), correctly, because ADR-0007
does not require dependency identity for a feature stage. ADR-0008 *does*
require it for fitted state.

**`FittedState` identity therefore reuses `StageIdentity`'s canonical-encoding
and SHA-256 machinery but is a distinct identity with additional components.**
The Phase-2 feature-stage exclusion is unchanged. Dependency identity is
caller-supplied, like `PlatformIdentity`: no platform library reads the lockfile
or shells out.

The `experiments` composition root computes this complete identity before
materialization and is the only issuer of `FittedState`. A model or
representation returns only `FittedPayload`; it neither sees nor asserts any of
these eleven components.

### 2.5 Implementation-only types — deliberately NOT public `core` API

| Type | Home | Why not public |
|---|---|---|
| `FoldSpec`, `FoldPlan`, `PurgeRecord`, `EmbargoRecord` | `experiments` | the orchestrator owns fold generation; no plugin sees a fold |
| `RunManifest`, `RunState`, `EvaluationInventoryEntry`, `scan_runs()` | `experiments` | run bookkeeping is the composition root's job |
| resolved-config types | `experiments` | Slice 3I |
| fitted-state identity/binder and serialization container | `artifacts` / `experiments` | reuses the Phase-2 canonical encoding, hashing, store and NPZ container; plugins never see identity authority or bytes |
| `_ObservedProvider`-style counters, test doubles, adversarial controls | `tests` | test-only |

### 2.6 Types deliberately NOT created

- **A second span type** for the inference position — `ViewSpan` serves both.
- **A `RunContext` / `StageContext` god-object** — nothing needs it.
- **A `FitContext`, public state factory or plugin-visible identity binder** —
  `fit` returns `FittedPayload`; the composition root already owns every input
  needed to mint `FittedState`.
- **A generic `Frame` base class** — the existing frames share no useful
  supertype and inventing one would invite convenience methods, which
  ADR-0003's "when to reconsider" warns against.
- **A metric registry, a fold registry, an intent-schema registry** — see §1
  and the Phase-4/5 deferrals.
- **A public `OutcomeOrigin`** — `StageOrigin` covers it; horizon and delay live
  in `params` and are read from the spec declaration for purge derivation.
- **A `RepresentationInputs` bundle, or any path by which a
  `RepresentationModel` could consume a `RepresentationFrame`.** V1 has exactly
  **one optional representation stage**
  ([ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md)), and giving
  `RepresentationModel` a `ModelInputs` argument would have quietly permitted
  representation chaining, representation-on-representation pipelines, recursive
  representation dependencies and a mini-DAG inside the fixed-stage pipeline.
  There is no concrete research requirement for any of that. Taking a
  `FeatureFrame` directly makes the non-capability **structural**: there is no
  parameter through which a prior representation could arrive.

### 2.7 Rolling walk-forward geometry — closed, deterministic, no hidden defaults

**Closed before Slice 3E. Codex implements exactly this.** One generator. No
`fit_mode` parameter exists, because the fit window is **fixed-length rolling
and never expanding** — an expanding variant is a Phase-4+ ADR, not a flag.

**Parameters — all explicit, none has a default:**

| Parameter | Rule |
|---|---|
| `fit_rows` | `int > 0` — fixed-length fit window, in observed rows |
| `eval_rows` | `int > 0` — fixed-length evaluation window, in observed rows |
| `step_rows` | `int > 0` — advance between consecutive folds, in observed rows |
| `embargo_rows` | `int >= 0` — additive serial-correlation buffer (ADR-0012 Amendment 1 rule 4) |

**Everything is expressed in observed rows** as positional indices into the
resolved axis of **exactly one** Tier-1 view. Fold containment (Tier-2 ⊆ Tier-1)
therefore holds by construction, and is additionally asserted at runtime.

**All positional ranges are half-open `[start, stop)`.**

For a view with `N` observed rows and fold index `k = 0, 1, 2, …`:

```
fit  positions : [ k*step_rows                        , k*step_rows + fit_rows              )
eval positions : [ k*step_rows + fit_rows             , k*step_rows + fit_rows + eval_rows  )

fold k is generated  IFF  k*step_rows + fit_rows + eval_rows <= N
```

- **Partial final folds are not generated.** If the last window does not fit
  completely, that fold simply does not exist — never truncated. This is the
  same principle as OD-3A.3: a short window is a different object, not a shorter
  version of the same one.
- **Ordering is ascending `k`**, i.e. chronological, and is the recorded order.
- **If no fold satisfies the condition, generation refuses** with a
  configuration error. A geometry that produces nothing is a mistake, not an
  empty result.

**Derived purge — automatic, never a constant.** With `e0 = k*step_rows +
fit_rows` (the first evaluation position) and `R = decision_delay_rows +
horizon_rows` taken as the **maximum** over every OutcomeSpec used for training:

```
purged fit positions : [ max(fit_start, e0 - R) , e0 )
purge_rows           : min(fit_rows, R)
```

Derivation: a training sample at position `p` has `target_end = p + R`; it
overlaps the evaluation range iff `p + R >= e0`, i.e. `p >= e0 - R`. Under the
row-local inference invariant (OD-3F) the `context_start` term of ADR-0012's
information span contributes nothing, and in a strictly fit-before-eval geometry
a backward `context_start` cannot reach a later evaluation range in any case.

**Embargo — additive and optional.** With `e1 = e0 + eval_rows`:

```
embargoed positions : [ e1 , e1 + embargo_rows )
```

These positions are excluded from the fit set of **every** fold whose fit window
would otherwise include them. `embargo_rows = 0` is the normal case and must
still be written explicitly.

**Effective fit set for fold `k`** = fit positions − purged − embargoed.
**If the effective fit set is empty, the fold refuses.**

Each fold records: `k`, the fit and eval position ranges, the resolved key
boundaries, `R` and its derivation inputs, `purge_rows`, `embargo_rows`, the
effective fit-set size, and any override (§2.7.1).

#### 2.7.1 Purge override — the only way an overlapping sample may survive

ADR-0012 Amendment 1 rule 3 permits reducing purge **only** with an explicit
structured reason, permanently recorded. V1 implements exactly that and nothing
looser:

- **Default path (no override): no training sample whose information span
  overlaps the evaluation range survives.** This is the path the reference and
  end-to-end qualification uses (§5).
- **Override path:** a config may declare a structured override carrying a
  reduced `purge_rows` and a non-empty reason. **Only the overlap the override
  explicitly permits may survive**; the override, its reason, and its exact
  applied effect are recorded per fold in the RunManifest and pinned into
  `FittedState` identity (§2.4 component 7).
- An override may never disable fold containment, and may never reduce purge
  below zero or above the derived value's own bound.

### 2.8 Reference evaluator semantics — closed

**Closed before Slice 3G. Codex implements exactly this.** Both are **reference
plugins**, not assumptions of the `Evaluator` contract. Neither delegates tie or
NaN behaviour to a library default.

#### `rank_ic_v1`

| Aspect | Rule |
|---|---|
| Params (explicit, no defaults) | `output_column: str`, `outcome_column: str`, `min_effective_samples: int >= 2` |
| Pair inclusion | a row is included **iff both values are finite**. `±inf` is already impossible by frame contract, so this excludes `NaN` only |
| Statistic | **Spearman rank correlation** = Pearson correlation computed over the *ranks* of the included pairs |
| Ties | **average (fractional) ranks**, computed by the platform explicitly |
| Constant vector | if either rank vector has zero variance the statistic is undefined → value `NaN` |
| Below minimum | `effective_samples < min_effective_samples` → value `NaN` |
| Value range | `[-1, +1]` when defined |
| `effective_samples` | count of included pairs |
| Emits | `rank_ic` |

#### `sign_accuracy_v1`

| Aspect | Rule |
|---|---|
| Params (explicit, no defaults) | `output_column: str`, `outcome_column: str`, `min_effective_samples: int >= 1` |
| Pair inclusion | a row is included **iff both values are finite and both are strictly non-zero** |
| Zero handling | a pair where **either** value is exactly zero is **excluded** — counted neither correct nor incorrect — because neither `0.0` expresses a direction. Zero/zero is likewise excluded |
| Signed zero | `-0.0 == 0.0` under IEEE comparison, so both signed zeros are excluded by the rule above; stated explicitly so it is not rediscovered as a bug |
| Value | `count( (output > 0) == (outcome > 0) ) / effective_samples` |
| Below minimum | `effective_samples < min_effective_samples` → value `NaN` |
| Value range | `[0, 1]` when defined |
| `effective_samples` | count of included pairs — **deliberately differs from `rank_ic_v1`'s on the same data**, which is precisely why every metric reports its own count |
| Emits | `sign_accuracy` |

**Both remain in V1.** Neither became complicated once specified: each is four
rules plus a minimum. They earn their place by proving *different* declared
requirements — `rank_ic_v1` needs an ordinally meaningful output, while
`sign_accuracy_v1` needs a sign-meaningful one — which is what exercises the
pre-run compatibility refusal. This is not a metric library and must not grow
into one.

---

## 3. Slices

Order: **3A → 3B → CP-9 → 3C → 3D → 3E → CP-10 → 3F → 3G → 3H → 3I → CP-11 →
3J → CP-12.**

Ordering rationale, deliberately chosen: research contracts and stages come
first, run infrastructure after. This mirrors Phases 1 and 2, and designing
`RunManifest` before knowing exactly what the stages produce is how manifests end
up wrong. Phase 2 proved stages can be fully developed and tested through
in-memory composition with no run infrastructure at all.

Every slice assumes the Phase-1/2 rules hold unchanged: **synthetic data for
development, TRAIN-only for qualification, zero audit delta, no SNAPSHOT / OOS /
LOCKBOX / RAW / CURATED reads.**

---

### 3A — Research contracts + skeleton retirement

- **Objective.** Land the complete Phase-3 public vocabulary with zero
  computation and zero I/O; delete the skeleton contracts Phase 3 replaces.
- **Exact scope.** `ModelOutputFrame`, `RepresentationFrame`,
  `TrainingTargetFrame`, `IntentFrame`, `FittedState`; the `OutcomeSpec`,
  `ResearchModel`, `RepresentationModel`, `DecisionPolicy` and `Evaluator`
  protocols; sparse-key semantics; the reserved outcome namespace predicate; the
  row-local inference invariant as a documented contract term; the
  tolerance-declaration surface (OD-3B); `BoundaryPolicy` as a type distinct from
  `GapPolicy`. **The complete, closed list is §2 — implement exactly those types
  and no others.** ADR-0022 Amendment 3 later adds only `FittedPayload`, in
  Slice 3C, to correct the fit/state authority boundary found during implementation.
- **New/changed contracts.** All of the above, in `core/`. Sparse frames are
  new: keys a strict subset of the evaluation range, strictly increasing, in
  bounds, with **absence ≠ dense `NaN`** structurally representable from the
  first line of code.
- **Existing areas touched.** `core/interfaces.py` (delete old protocols, add
  new); `core/domain.py` (retire `Bar`, `MarketContext`, `FeatureSet`,
  `ResearchInput`, `ModelOutput`, `Signal`, `Direction`, `Representation`,
  `PipelineRecord`); `core/errors.py`; `core/__init__.py`; the four stage
  `registry.py` files; `core/pipeline.py`; `tests/test_architecture.py` guard
  inventories — a **deletion** from `DEFERRED_RESEARCH_CARRIERS`, which makes the
  guard stricter, still called out in review per CLAUDE.md.
  **`core/frames.py`, `core/features.py`, `core/feature_frames.py`, all of
  `data/`, all of `artifacts/` and all of `features/` are not touched.**
- **Dependencies.** ADR-0022 accepted; this plan approved.
- **Explicit exclusions.** Any computation, I/O, identity/hashing, run concept,
  metric, fold, or policy execution; the resolved-config schema (Slice 3I); any
  simulator contract; `TradeLog`; `EquityCurve`; order-type intent schemas; any
  intent-schema registry.
- **Research-integrity protections.** Reserved namespace refused in every
  declaration field before computation; no trading vocabulary in model output
  (banned-keys guard extended); the absent/`NaN`/`0.0` trichotomy enforced at
  construction; `infer` structurally cannot accept targets — enforced by
  signature **and** an architecture guard.
- **Required tests.** Contract validation and every refusal path; sparse-frame
  construction and the trichotomy; immutability and fresh-descriptor isolation;
  `BoundaryPolicy` and `GapPolicy` provably distinct types; architecture guards
  for exact public surfaces; no table-library type in any public annotation; old
  protocols gone with **no shim**.
- **Acceptance criteria.** Gate green unfiltered; skeleton contracts deleted with
  no compatibility layer; architecture-test count grows and no guard weakened;
  audit log, D-6 fingerprint and all five view-manifest digests unchanged.
- **Checkpoint.** None (mirrors Slice 2A).

**Implementation evidence — COMPLETE.** Slice 3A landed on
`codex/phase3-implementation` with exactly the closed §2 public vocabulary,
private validated minting seams, sparse output/intent semantics, fresh read-only
NumPy descriptors, the fixed `FeatureFrame → RepresentationFrame? →
ModelInputs → ModelOutputFrame → IntentFrame` type flow, and no computation,
I/O, hashing, run or metric implementation. The nine per-point research
carriers were deleted with no shim. Architecture guards pin the exact value,
frame and protocol surfaces; prove `infer` has no target position; prove
`RepresentationModel.fit` / `encode` consume `FeatureFrame` directly and can
never receive `ModelInputs` or a prior `RepresentationFrame`; reject table/data
handles in public annotations; and refuse a generic frame base. The unfiltered
gate is green: 1,193 active tests (1,197 collected, 4 performance tests
deselected), 133 architecture tests, Ruff clean and strict mypy clean over 94
source files. Audit, D-6 and all five published view-manifest digests are
byte-identical to the implementation baseline.

**Post-slice contract correction.** Slice 3C exposed that a plugin cannot mint
the complete eleven-component `FittedState` from its deliberately narrow fit
arguments. OD-3G owner-approves ADR-0022 Amendment 3, which adds the single
public `FittedPayload` value and changes both plugin `fit` return annotations.
This is implemented as part of 3C; the completed 3A behavior and all structural
non-capabilities remain unchanged.

---

### 3B — OutcomeSpec engine + TrainingTargetFrame + reserved namespace

- **Objective.** Build the single forward-reading component and prove it reads
  forward exactly as far as declared and no further.
- **Exact scope.** `forward_log_return_v1` per OD-3A…OD-3A.4; the outcome
  execution path; orchestrator-side `TrainingTargetFrame` construction;
  `target_end` derivation; the outcome registry.
- **New/changed contracts.** `OutcomeSpec` implementation and registration;
  `TrainingTargetFrame` production; `OutcomeOrigin` provenance per target column.
- **Existing areas touched.** A new outcomes module whose dependency budget is
  `core` only; `experiments/` gains the target-construction entry point.
  Nothing in `data/`, `features/` or `artifacts/`.
- **Dependencies.** 3A.
- **Explicit exclusions.** Any second OutcomeSpec; `truncate` or `skip` boundary
  policies; any discontinuity-triggered policy; any evaluation metric; Plane-B
  outcomes; fold generation.
- **Research-integrity protections.** **The phase's most important slice.**
  Forward-reach rigs mirroring Phase 2's backward-reach rigs: perturb rows beyond
  `p_terminal` → target at `p` bitwise unchanged; perturb rows before `p_origin`
  → likewise. Reserved-namespace outputs structurally unreachable from features,
  inference and policies.
- **Required tests.** Hand-computed goldens for `forward_log_return_v1`;
  alignment to the decision key; `horizon_rows = 1` yields
  `log(P[X+2]/P[X+1])`, never zero; boundary `NaN` at the range end with **no
  truncation**; discontinuities crossed without unavailability; `target_end`
  derivation exact; every refusal path. **Negative controls, each of which must
  be caught:** an off-by-one horizon; an alignment inversion attaching the target
  to the future key; a spec reading past its declared horizon; a spec truncating
  an incomplete horizon instead of emitting `NaN`; a spec treating a
  discontinuity as a closure.
- **Acceptance criteria.** Goldens exact; forward-reach rig green with **no
  tolerance, no skip, no xfail**; every negative control caught; synthetic data
  only; architecture guard proves `BoundaryPolicy` never aliases `GapPolicy`.
- **Checkpoint.** **CP-9 immediately follows.**

**Implementation evidence — COMPLETE.** `outcomes/` contains exactly one
registered component, `forward_log_return_v1`, whose four constructor choices
are explicit keyword-only arguments with no defaults. The composition root
builds a column-restricted `OutcomeRequest` from an already-issued
`MarketFrame`, validates exact observed evaluation endpoints, and mints either
the structurally distinct `TrainingTargetFrame` or `OutcomeFrame`. Goldens prove
`horizon_rows=1` computes `log(P[X+2]/P[X+1])` on the decision key; incomplete
horizons are `NaN`; arithmetic key discontinuities are crossed; and purge sees
the exact `decision_delay_rows + horizon_rows` terminal reach. Non-finite
endpoints remain unavailable and non-positive log-return endpoints refuse.

The release-blocking exact-bit rigs pass for the reference component and catch
all five test-only controls: off-by-one arithmetic, future-key alignment,
reading one row beyond the declared terminal, horizon truncation, and treating
an arithmetic discontinuity as a closure. Architecture guards keep
`OutcomeRequest` inside the fixed outcome stage, reject table/data handles,
keep `BoundaryPolicy` distinct from `GapPolicy`, prohibit session/calendar
semantics in production outcome code, and prove none of the controls is
registered. The unfiltered gate is green: 1,219 active tests (1,223 collected,
4 performance tests deselected), 140 architecture tests, Ruff clean and strict
mypy clean over 101 source files. External state is byte-identical to baseline.

---

### 🔶 CP-9 — *"forward reading is quarantined, bounded, and never truncated"*

Engineering verification gate. Attack: can any component other than the
OutcomeSpec read forward? Can a spec read further forward than declared? Is the
target attached to the decision key? Is an incomplete horizon ever silently
shortened? Is a discontinuity ever treated as a closure? Is `target_end` exactly
what purge will consume? Can the reserved namespace reach a feature, an
inference input or a policy?
**Blocks on:** any control not caught; any tolerance or skip; any truncated
horizon; any reserved-namespace escape; any alignment ambiguity; any invented
session semantics.

**CP-9 outcome: GREEN.** Forward reading is confined to `OutcomeSpec`; the
declared terminal is exact; values remain attached to decision keys; incomplete
horizons are never shortened; discontinuities carry no semantic interpretation;
all controls are caught with no tolerance, skip or xfail; and the reserved
target namespace cannot enter features, inference, representations or policy
inputs. Slice 3C is permitted to begin.

---

### 3C — Fit/infer lifecycle + FittedState + RepresentationModel + reference models

- **Objective.** Make fit-time and inference-time separation structural, with
  identity that cannot hide what data a fitted state saw.
- **Exact scope.** `fit(train_view, inputs, training_targets=None) → FittedPayload`;
  the orchestrator binds the complete identity and mints `FittedState`;
  `infer(eval_view, fitted_state, inputs) → ModelOutputFrame`; `FittedState`
  identity and serialization; `RepresentationModel` in the same lifecycle
  (OD-3E), consuming a `FeatureFrame` **directly** so representation chaining is
  structurally impossible; first consumer of the existing-but-unused `ViewUnion` for
  promotion-style fit inputs; **three reference models and one reference
  representation.**
- **New/changed contracts.** ADR-0022 Amendment 3's small, publicly
  constructible `FittedPayload` is the only plugin fit result. It carries
  learned arrays and immutable metadata but no identity or platform authority;
  only the composition root may bind it into a private-construction
  `FittedState`. `FittedState` identity covers the **complete
  eleven-component field list in §2.4**, which **includes dependency identity**
  (ADR-0022 Amendment 2). It **reuses** `artifacts/StageIdentity`'s
  canonical-encoding and SHA-256 machinery but is a **distinct identity with
  additional components** — `StageIdentity`'s hashed document carries no
  dependency identity, and the Phase-2 feature-stage exclusion of the `uv.lock`
  digest stands unchanged. Dependency identity is **caller-supplied**, like
  `PlatformIdentity`; no platform library reads the lockfile or shells out.
  Serialization reuses the Phase-2 canonical NPZ container plus JSON metadata,
  which already enforces ADR-0017's pickle ban structurally via
  `allow_pickle=False`.

  | Reference component | Proves |
  |---|---|
  | stateless rule model (no-op `fit`) | fit is not mandatory; a rule strategy pays no ML tax |
  | trivial supervised model consuming `TrainingTargetFrame` | the platform-controlled target channel end to end |
  | tiny reference-library model | `FittedState` carrying training-derived state |
  | one trailing standardiser representation | learned preprocessing bounded by the fit view and entering identity |

  Each proves a distinct platform property and **none is a research signal** —
  the Phase-2 reference-provider standard.
- **Existing areas touched.** `models/`, `representations/`; `artifacts/` gains a
  fitted-state identity and serialization path reusing, not duplicating, the
  store; `experiments/` gains fit orchestration.
- **Dependencies.** 3A, 3B.
- **Explicit exclusions.** Any representation library; folds; purge; policies;
  evaluation; run directories; **any carried-state or online-inference API
  (OD-3F)**.
- **Research-integrity protections.** `FittedState` identity pins the full
  ADR-0008/ADR-0010 field list including the training OutcomeSpec and the applied
  purge (populated in 3E). `infer` structurally cannot receive targets. Refit-per-run
  with `cache_if_expensive` (OD-3C).
- **Required tests.** Two states trained on identical bars under different target
  definitions never share an identity (ADR-0008 amendment, explicit); no-op fit
  path; `training_targets=None` path; round-trip fidelity; `infer` refuses
  targets; plugins can construct payloads but cannot mint states or receive
  identity authority; identity sensitivity and exclusion matrices; no pickle
  anywhere; representation fitted only on the fit view.
- **Acceptance criteria.** Full identity matrix green; serialization
  round-trips exactly; every reference component zero-arg constructible and
  registered; gate green.
- **Checkpoint.** None (folds into CP-10).

**Implementation evidence — COMPLETE.** Plugins return only the public,
identity-free `FittedPayload`; the composition root binds all eleven §2.4
components into private-construction `FittedState`. The identity has complete
sensitivity and explicit exclusions, including proof that identical target
values under different definitions diverge while learned payload bytes, target
values under an unchanged definition and mapping order do not alter the key.
Fitted arrays and canonical metadata round-trip through deterministic NPZ with
`allow_pickle=False`, and expensive state reuses the Phase-2 immutable store
through the single private `fitted-states` namespace while cheap state stays
ephemeral.

Exactly three zero-argument reference models and one zero-argument reference
representation are registered. The execution seams snapshot and revalidate
declarations, enforce exact input columns, axes and origins, and keep targets
out of inference. Representation fit/encode consumes `FeatureFrame` directly;
architecture guards prove no chaining path and no plugin state-minting,
platform, split, dependency, storage or materialization authority exists. The
first `ViewUnion` consumer verifies both its ordered source identities and its
split-policy identity against the fit descriptor.

The unfiltered gate is green: 1,239 active tests (1,243 collected, 4
performance tests deselected), 145 architecture tests, Ruff clean and strict
mypy clean over 109 checked source files. Audit remains 3 records /
`deeb73ed…01f5`; D-6 remains `5125b9ff…b180`; all five published view-manifest
digests and the four existing feature-cache entries are unchanged; the external
runs root contains zero fitted-state entries. Development and component goldens
used synthetic rows only; the ordinary gate's sanctioned read-only TRAIN
integrity smoke left external state unchanged.

---

### 3D — Model & representation leakage rigs (release-blocking, test-only)

- **Objective.** Prove mechanically, for **every registered model and
  representation**, that inference is row-local and forward-blind.
- **Exact scope.** The forward-perturbation rig and the row-locality rig,
  parametrised over the live model and representation registries; a meta-test
  asserting rig coverage **equals** the registry set; test-only negative
  controls.
- **New/changed contracts.** None. **This slice changes no production source.**
- **Existing areas touched.** `tests/` only, plus architecture guards.
- **Dependencies.** 3C.
- **Explicit exclusions.** Any production source change; any tolerance, skip or
  expected-failure marker (architecture-guarded); any fold-boundary test (3E).
- **Research-integrity protections.** **This slice is what discharges ADR-0012's
  carried-state term (OD-3F).** Comparison is exact — NaN masks as bytes plus raw
  `uint64` value bits — with a finite-witness precondition so a comparison can
  never pass vacuously. Registering a model without rig coverage fails the gate.
- **Required tests.** Rigs green over every registered model and representation.
  **Negative controls, each of which must be caught, each testing a distinct
  failure mode:** a forward-shift model; a whole-evaluation-range normaliser; a
  carried-state accumulator; a representation fitted on the evaluation range
  rather than the fit view; a model attempting its own forward read inside `fit`.
  An architecture test asserts no control is registered in production.
- **Acceptance criteria.** All controls detected; every registered component
  green; **no skip, no xfail, no tolerance**; coverage meta-test exact; a rig
  failure fails the gate.
- **Checkpoint.** None (folds into CP-10).

**Implementation evidence — COMPLETE.** Forward-perturbation and row-locality
rigs execute over every live model and representation registry entry. The
comparison is exact: row presence and origins, NaN-mask bytes and raw `uint64`
value bits, with a required finite witness. Independent covered-name sets equal
the live registries, so an unreviewed registration fails the gate.

The five test-only controls are all caught by their intended boundary: forward
shift, whole-evaluation-range normalization, carried-state accumulation,
evaluation-range representation fitting, and a model attempting its own
forward read during fit. Architecture guards prove that no control appears in
production, no tolerance/skip/xfail or vacuous comparison can soften the rig,
no model or representation source gains I/O or forward-reading authority, and
`infer`/`encode` cannot mutate component instance state. Slice 3D changed tests
only.

The unfiltered gate is green: 1,256 active tests (1,260 collected, 4
performance tests deselected), 148 architecture tests, Ruff clean and strict
mypy clean over 110 checked source files. Audit remains 3 records /
`deeb73ed…01f5`; D-6, all five view manifests, four existing feature-cache
entries and zero external fitted-state entries remain exact. Every rig uses
synthetic rows.

---

### 3E — Fold generation + information-span-derived purge

- **Objective.** Make the mandatory purge derived, automatic, recorded and
  adversarially tested at **fold** boundaries.
- **Exact scope.** **One** rolling walk-forward fold generator, whose geometry,
  purge derivation, embargo effect and override rule are **closed in §2.7 —
  implement exactly that**; the fold ⊆ Tier-1 view runtime check;
  information-span computation; automatic purge application; the optional
  additive embargo; the structured override of §2.7.1.
- **New/changed contracts.** Fold descriptor and purge record, both recorded per
  fold. Span composition: `context_start` ← declared feature reach (Phase 2's
  `max_prior_rows`) **and nothing else, because V1 inference is row-local**;
  `decision_key` ← the sample's own key; `target_end` ← `p + decision_delay_rows
  + horizon_rows` from 3B.
- **Existing areas touched.** `experiments/`; `FittedState` identity gains the
  applied purge.
- **Dependencies.** 3B, 3C, 3D.
- **Explicit exclusions.** Expanding-window, k-fold and combinatorial purged CV;
  any fold-geometry plugin registry; sweep generation; cross-view folds
  (forbidden by ADR-0012).
- **Research-integrity protections.** Purge derived from declarations, **never** a
  hardcoded bar count. A fold geometry that omits purge is unrepresentable.
  Reduction requires a structured reason, permanently recorded (ADR-0012
  Amendment 1 rule 3). Embargo is additive and recorded the same way. Walk-forward
  leakage rigs exercise **fold** boundaries, not only Tier-1 view edges (ADR-0012
  Amendment 1 rule 6).
- **Required tests.** Purge magnitude exactly equals the derived span across a
  matrix of horizons, delays and feature reaches; a horizon change legitimately
  changes the usable fit set; fold-containment refusal; embargo additive; **a
  purge-disabled negative control is caught by the fold-boundary rig**; the
  applied purge and its derivation inputs recorded per fold.
- **Acceptance criteria.** No training sample whose information span overlaps the
  evaluation range survives in any fit set, proven adversarially; every fold's
  purge recorded; gate green.
- **Checkpoint.** **CP-10 immediately follows.**

**Implementation evidence — COMPLETE.** The single private generator implements
the closed fixed-length rolling geometry with eight explicit keyword-only
arguments and no defaults. All ranges are half-open observed-row positions;
partial final folds are omitted, empty configurations and empty effective fit
sets refuse, and the FeatureFrame evaluation axis plus its complete ordered
source identities must match exactly one already-bounded Tier-1 view. Bounds
between observed keys remain legal. Backward context is composed from the
maximum declared feature reach across the full bounded context axis; no
carried-state term exists.

For every fold, purge is derived as
`min(fit_rows, max(decision_delay_rows + horizon_rows))` over its complete
training OutcomeSpec sequence and applied before accumulated embargo zones.
Zero-outcome fits record zero derivation explicitly. A private structured
override can only strictly reduce the derived rows, carries a non-empty reason,
and records its exact permitted overlap and applied effect. The private
`FitIsolation` then binds the generated ViewSpan, exact effective fit keys,
feature/outcome reaches, purge, embargo and override into fitted-state identity;
both model and representation fit paths enforce it.

Goldens cover exact geometry, no partial fold, horizon/feature-reach matrices,
multiple and zero OutcomeSpecs, additive embargo, override bounds, fold
containment, context composition and fitted-state identity sensitivity. The
fold-boundary rig examines every default fold and catches both a purge-disabled
control and an overbroad-override control. Architecture guards pin the one
private generator, exact arithmetic and purge-before-embargo order and keep both
controls test-only. The unfiltered gate is green: 1,290 active tests (1,294
collected, 4 performance tests deselected), 151 architecture tests, Ruff clean
and strict mypy clean over 112 checked source files. Audit, D-6, all five view
manifests, four feature-cache entries and zero external fitted states remain
exact; all new data is synthetic TRAIN data.

---

### 🔶 CP-10 — *"models cannot see the future or their own past, and purge is derived from declarations"*

Covers Slices 3C, 3D and 3E. Attack: is inference row-local for every registered
model and representation? Are all negative controls caught? Can a training sample
whose target span reaches into the evaluation range survive? Is purge derived or
hardcoded? Is `context_start` correctly composed given carried state is out of
scope? Is a disabled purge caught? Was any representation fitted on data outside
its fit view? Does `FittedState` identity distinguish two states trained under
different target definitions?
**Blocks on:** any control not caught; any **undeclared** information-span
overlap surviving in a fit set; any overlap **beyond** what a recorded override
explicitly permits; any mismatch between a recorded override and the applied fit
set; any hardcoded or wrongly derived purge; any silent reduction; any
non-row-local inference.

**CP-10 outcome: GREEN.** Exact row-locality rigs cover every registered model
and representation; every 3D control remains caught. The generated fit set is
identity-bound and cannot silently restore purged or embargoed rows; the default
path permits no overlap, while the override path permits exactly its recorded
range and no more. Purge comes only from validated OutcomeSpec declarations,
feature context comes only from declared WindowSpecs, representation fitting is
confined to the generated fit keys, and both fold-boundary controls fail as
required. Slice 3F is permitted to begin.

*Note on scope.* CP-10 verifies that **no undeclared leakage** exists; it does
**not** ban the purge-reduction capability ADR-0012 Amendment 1 rule 3
explicitly permits. With no override declared — the default path, and the path
the §5 qualification uses — "no undeclared overlap" and "no overlap at all"
coincide.

*This checkpoint may be split into CP-10a (after 3D) and CP-10b (after 3E) at the
owner's discretion; they are kept together because 3E's correctness is not
independently assessable if 3D is wrong.*

---

### 3F — DecisionPolicy + IntentFrame + fan-out

- **Objective.** Convert model output to intent, and prove N policies consume one
  persisted inference with zero re-inference.
- **Exact scope.** `DecisionPolicy` execution; `TargetExposureIntentV1` as a
  **validated literal** legal-id set; fan-out orchestration; stage counters; two
  reference policies (threshold, abstain).
- **New/changed contracts.** None beyond 3A; this slice is execution.
  `intent_schema_id` is required and identity-bearing, validated against a frozen
  literal set containing exactly `"target_exposure_intent_v1"`.
- **Existing areas touched.** `decisions/`; `experiments/`.
- **Dependencies.** 3A, 3C.
- **Explicit exclusions.** **No intent-schema registry**; no second schema; no
  simulator; no execution or cost semantics; no `IntentFrame` persistence beyond
  ADR-0007's `ephemeral` default; no `ExecutionSpec`, `TradeLog` or
  `EquityCurve` in any form; no portfolio policy.
- **Research-integrity protections.** Direction vocabulary confined to
  `decisions/` (the existing
  `test_only_decisions_and_core_name_a_trading_direction` guard, extended). The
  absent/`NaN`/`0.0` trichotomy preserved by every consumer.
- **IntentFrame materialization — precise rule (no durable storage).**
  `IntentFrame` remains **`ephemeral`** by ADR-0007's default; Phase 3 has no
  simulator, so no downstream consumer triggers the orchestrator's upgrade rule,
  and **no durable `IntentFrame` storage is introduced**. What Phase 3 proves is
  therefore not persistence but **canonical round-trip**: the trichotomy
  survives (i) construction and accessor access, (ii) canonical in-memory
  encode → decode through the same canonical encoding used for determinism
  comparison, and (iii) alignment of a sparse frame against a dense evaluation
  axis by a consumer. No `IntentFrame` is written to the artifact store, and no
  materialization default changes.
- **Required tests.** N policies over one `ModelOutputFrame` with a provably zero
  inference count; **trichotomy exact across canonical encode → decode**, with
  absent rows, `NaN` rows and `0.0` rows all distinguishable afterwards;
  `target_exposure ∈ [−1,+1]` bounds; `±inf` refused; sparse intent alignment
  against a dense view declared and tested; an unknown `intent_schema_id`
  refused; an architecture guard proving no store write path exists from an
  `IntentFrame`.
- **Acceptance criteria.** Stage counters prove zero re-inference; the trichotomy
  survives canonical round-trip; `IntentFrame` remains ephemeral with no durable
  write path; gate green.
- **Checkpoint.** None.

**Implementation evidence — COMPLETE.** The decision registry contains exactly
`threshold_v1` and `abstain_v1`. The threshold policy carries explicit
identity-bearing output-column and finite non-negative threshold parameters;
the abstain policy reads no value column. Both emit the sole frozen literal
schema id, `target_exposure_intent_v1`. The execution seam snapshots all
declarations, rejects target-namespace inputs, validates every policy against
the model's declared columns before inference, and checks each returned sparse
axis and schema against the one shared `ModelOutputFrame`.

The private fan-out invokes its supplied inference callable exactly once and
then exposes only the resulting frame to N policies. Live counters prove one
inference, zero re-inferences and N policy calls; an incompatible policy refuses
before inference and before partial fan-out. Intent remains entirely ephemeral.
A strict canonical in-memory JSON codec encodes keys plus raw float64 bits and
decodes against an exact ViewSpan without any store or filesystem import. Its
round-trip and the dense-axis consumer keep presence in a separate boolean
mask, so absent, explicit `NaN` abstention and exact `0.0` flat remain distinct.

The unfiltered gate is green: 1,309 active tests (1,313 collected, 4 performance
tests deselected), 154 architecture tests, Ruff clean and strict mypy clean over
115 checked source files. Architecture guards pin the two policies, one schema
literal, compatibility-before-inference order, exact single inference and the
absence of any durable IntentFrame write path. All new data is synthetic.

---

### 3G — Plane-A evaluators + evaluation inventory

- **Objective.** Cheap hypothesis kills, and an honest record of what was
  actually evaluated.
- **Exact scope.** The minimal `Evaluator` contract with **declared
  requirements**; the pre-run compatibility check; **two** reference evaluators —
  `rank_ic_v1` and `sign_accuracy_v1`, whose semantics are **closed in §2.8 —
  implement exactly that**; the evaluation inventory; run outcome
  classification.
- **New/changed contracts.** `Evaluator` declares required output columns,
  required outcome columns and emitted metric names. Every metric reports its
  **effective sample count**.

- **Evaluation-inventory granularity — corrected, and load-bearing.**
  ADR-0011 Amendment 1 lists `DecisionPolicy` in an inventory entry **"where a
  decision plane was evaluated"**. A Plane-A evaluator consumes a
  `ModelOutputFrame` and an `OutcomeFrame` and **never sees an `IntentFrame`**,
  so policy fan-out does **not** multiply Plane-A evaluations. **Phase 3 contains
  no decision-plane evaluator at all** — evaluating intents requires a simulator,
  which is Phase 4 behind the ADR-0020 cost audit. The Plane-A evaluation
  identity is therefore:

  ```
  Plane-A evaluation identity =
        model / FittedState identity
      + ModelOutputFrame artifact identity + the evaluated output column(s)
      + OutcomeSpec identity (its horizon and delay are among its params,
        and are also recorded flat for queryability)
      + evaluator identity (the metric family)
                                        --- and NO policy dimension ---
  ```

  With `F` fitted states, `S` distinct OutcomeSpec instances and `E` compatible
  evaluators, a run records exactly **`F × S × E`** Plane-A inventory rows.
  `P` decision policies contribute **zero** additional rows.

  **`P` is still recorded honestly** — every executed policy, its identity and
  its produced `IntentFrame` shape are written to the RunManifest as **executed
  work**, so nothing is hidden. It is simply not a *statistical evaluation*,
  which is the same distinction ADR-0010 draws for a descriptive run that ends
  before any evaluation. When Phase 4 adds a decision-plane evaluator, those
  evaluations gain their own inventory rows and *then* carry the policy
  dimension.

  This corrects an earlier planning formula of `N·M·H·E`, which would have
  inflated the multiple-testing denominator by the policy count — the precise
  error ADR-0011 Amendment 1 exists to prevent, merely in the opposite
  direction from run-counting. **No policy evaluator is added to preserve the
  old formula.**

- **Existing areas touched.** `evaluation/`; `experiments/`;
  `core/pipeline.py`'s compatibility-check pattern generalized.
- **Dependencies.** 3B, 3F.
- **Explicit exclusions.** Plane-B / trade evaluation; **any decision-plane
  evaluator**; **Sharpe, drawdown, PnL, turnover and every other trade metric**;
  `quantile_spread` (deferred); **the multiple-testing denominator and any
  deflated statistic — Phase 5, explicitly blocked by ADR-0011**; comparison CLI;
  metric library growth.
- **Research-integrity protections.** Evaluators consume artifacts and **never**
  recompute upstream research logic — enforced by prohibited dependencies, not by
  promise. The inventory records **what was actually evaluated**, not what merely
  existed in the run. A descriptive run that ends before evaluation is recorded
  and is **not** a trial. An infrastructure failure before evaluation is recorded
  and is **not** a trial. Nothing is ever deleted or hidden.
- **Required tests.** Hand-computed metric goldens per §2.8, including every
  specified edge case (all-NaN pairs, constant vectors, ties, exact zeros, below-
  minimum samples); sample counts present and correct, and provably different
  between the two evaluators on the same data; **`F` fitted states × `S`
  OutcomeSpec instances × `E` evaluators produces exactly `F·S·E` inventory rows,
  and adding `P` decision policies leaves that count unchanged while `P` policy
  executions appear in the RunManifest**; a descriptive run produces zero rows;
  a pre-evaluation failure produces zero rows but a permanent failure record; a
  requirement mismatch refuses **before** the run starts; an architecture guard
  proves no deflated statistic and no decision-plane evaluator exists anywhere in
  the codebase.
- **Acceptance criteria.** Inventory granularity meets ADR-0011 Amendment 1
  exactly, with **no policy multiplier**; compatibility refusal is pre-execution;
  gate green.
- **Checkpoint.** None.

**Implementation evidence — COMPLETE.** The evaluation registry contains
exactly `rank_ic_v1` and `sign_accuracy_v1`, each with the three explicit §2.8
parameters and no defaults. Rank IC performs stable explicit average-tie
ranking followed by explicit Pearson correlation; sign accuracy admits only
finite, strictly non-zero pairs, including rejection of both signed zeros. Both
align sparse model-output keys to outcome keys and emit exact `MetricValue`
objects with honest per-metric effective counts. Every closed undefined case
returns `NaN` with the count preserved.

The generalized private compatibility primitive now serves policies and
evaluators. A Plane-A preflight snapshots all evaluator declarations and
refuses missing output or outcome columns before execution. The evaluation
layer consumes only completed `ModelOutputFrame` and `OutcomeFrame` artifacts,
has no upstream recomputation authority, validates axis tags and subset
alignment, and revalidates declarations and emitted metric names after each
call.

The private inventory records fitted-state identity, model-output artifact
identity and columns, OutcomeSpec identity plus flat horizon/delay, evaluator
identity, metric names, comparison tolerance and emitted metrics. The
orchestrator validates every combination before the first evaluator call and
asserts exactly `F·S·E` rows. Policy executions are separate records carrying
policy identity and IntentFrame schema/shape; a three-policy golden proves they
do not multiply the statistical inventory. Descriptive and pre-evaluation-
failure classifications both contain zero inventory rows; failures require an
immutable non-empty reason, which Slice 3H persists in the RunManifest.

Goldens cover ties, all-NaN pairs, constant vectors, below-minimum samples,
sparse alignment, exact and signed zeros and deliberately different effective
counts on identical input. Architecture guards pin the closed registry and
constructor surfaces, the explicit arithmetic, early preflight, exact formula
and absence of any policy dimension, upstream recomputation, Plane-B,
decision-plane evaluator, deflated statistic, quantile spread or trade metric.
The unfiltered gate is green: 1,328 active tests (1,332 collected, 4 performance
tests deselected), 158 architecture tests, Ruff clean and strict mypy clean over
119 checked source files. Audit, D-6, all five view manifests, four feature-cache
entries and zero external fitted states remain exact; all new data is synthetic.

---

### 3H — Run identity + RunManifest + run directories + state machine + heartbeats + `scan_runs()`

- **Objective.** Every execution is permanently and honestly recorded, and
  nothing silently disappears.
- **Exact scope.** `experiment_fingerprint`; `run_id` (ULID); `attempt_number`
  **derived at query time from run directories** (ADR-0011 Amendment 3 — no
  racing counter, no lock); `RunManifest`; the run-directory layout; the state
  machine PENDING → RUNNING → COMPLETE | FAILED and RUNNING → INTERRUPTED;
  heartbeats; canonical/noncanonical Git state; `oos_touched` and
  `lockbox_touched` flags; **`scan_runs()`** (OD-3D).
- **New/changed contracts.** Run-directory layout:

  ```
  <runs-root>/runs/<run_id>/
      state.json                  # mutable until terminal
      heartbeat.json              # mutable until terminal
      config.resolved.json
      identity.json               # fingerprint + all inputs
      manifest.json               # RunManifest, written at terminal state
      evaluation_inventory.json
      determinism_report.json     # discharges ADR-0016's stored-report obligation
      metrics/
      artifacts/                  # or pointers into the Phase-2 cache
      logs/
  ```

  A COMPLETE run directory is **immutable** — supersede, never mutate.
  `state.json` and `heartbeat.json` are the only mutable files and stop being
  written at a terminal state.

  A run-state primitive with **replace** semantics is written inside
  `experiments`, to the hardened Phase-2 pattern from the outset. It is
  deliberately **not** a new caller of `data/atomic_write.py`, whose deferred
  CP-4 note-3 parent-symlink hardening would otherwise be triggered; that helper
  remains byte-exact with its single pre-existing caller.
- **Existing areas touched.** `experiments/`. `PlatformIdentity` from Phase 2
  supplies the clean/dirty Git determination.
- **Dependencies.** 3G.
- **Explicit exclusions.** **SQLite (OD-3D)**; queue daemon; remote workers;
  dashboards; disk telemetry; automatic GC; any query DSL or persistent index.
- **Research-integrity protections.** Interrupted runs cannot vanish: a stale
  heartbeat causes the next CLI contact to mark the run INTERRUPTED. Failed runs
  are permanently recorded. Fingerprint over-segmentation from documentation-only
  commits is accepted and recorded (ADR-0011 Amendment 2).
- **Required tests.** Fingerprint sensitivity and insensitivity matrices;
  concurrent launches never share an attempt number; `kill -9` → INTERRUPTED
  detected; COMPLETE immutability enforced; no run-state write outside the run's
  own directory; **run-record scan golden test** — a fixture tree of run
  directories in known states (COMPLETE, FAILED, INTERRUPTED, descriptive)
  reconstructs to an exact expected record set from files alone.
- **Acceptance criteria.** `scan_runs()` reconstructs complete state from files
  alone; every state transition tested; gate green.
- **Checkpoint.** None (folds into CP-11).

**Implementation evidence — COMPLETE.** A pure experiment identity binds the
resolved config, ordered dataset identities, ordered component origins and
source digests, Git platform identity, dependency-lock digest, seed and declared
lockbox intent. Machine identity and canonicality evidence are recorded outside
the semantic fingerprint. Run IDs are canonical time-sortable ULIDs, and
attempts are derived at scan time by per-fingerprint ULID order; there is no
stored counter or index.

The files-first store atomically publishes a fully initialized PENDING
directory, pins all traversal to no-follow directory descriptors and permits
replace semantics only for `state.json` and heartbeat mtime before terminal
state. Immutable files use same-directory no-replace publication. Terminal
manifests are written before the final state, hash every immutable root payload
and every artifact/log/metric file, and the scanner reconciles that bounded
crash window before sealing the directory read-only.

`scan_runs()` reconstructs state from files alone and permanently changes stale
RUNNING work to INTERRUPTED. Evaluation inventory may be published immediately
after evaluation, preserving a statistical trial if later work fails or is
interrupted. Descriptive and pre-evaluation failures retain their exact zero-row
classification. `determinism_report.json` has an immutable, canonical,
manifest-hashed storage path ready for the 3I verifier. SQLite, a daemon, a
persistent counter, a query language and a hidden stale timeout are absent.

Goldens cover all legal and illegal state transitions, 24 concurrent launches,
a real SIGKILL and subsequent stale scan, manifest-before-state crash recovery,
the exact COMPLETE/FAILED/INTERRUPTED/descriptive scan record set, dirty and
gated canonicality, evaluation durability, heartbeat monotonicity, terminal
tampering, symlink escape refusal and determinism-report immutability.
Architecture guards pin the state and layout vocabulary, exact fingerprint
inputs, files-only attempts, descriptor-relative writes and absence of SQLite.
The unfiltered gate is green: 1,349 active tests (1,353 collected, 4 performance
tests deselected), 162 architecture tests, Ruff clean and strict mypy clean over
122 checked source files. Audit, D-6, all five view manifests, four feature-cache
entries and zero external fitted states remain exact; all run roots were
temporary and all identities synthetic.

---

### 3I — Orchestrator + resolved config + CLI + detached execution + cache/resume + determinism verification

- **Objective.** `config → detached run → stored, verifiable result.`
- **Exact scope.** The resolved-config schema (replacing `ExperimentConfig` and
  `ComponentSpec`); the fixed-stage orchestrator; **four CLI verbs** — `run`,
  `status`, `list`, `verify`; `systemd-run --user` detached execution with tmux
  fallback (ADR-0018); cache/resume composition; cache-bypassing determinism
  verification; discharge of ADR-0007's orchestrator `ephemeral`-upgrade
  obligation; discharge of ADR-0016's stored-verification-report obligation.
- **New/changed contracts.** Resolved config; the orchestrator stage sequence per
  ADR-0006's closed stage list.
- **Existing areas touched.** `experiments/` becomes the real composition root.
- **Dependencies.** 3H.
- **Explicit exclusions.** Sweep generation; process pools; cross-run
  walk-forward resumption; comparison CLI; any CLI plugin or sub-command
  framework; output formatters beyond plain text and JSON — **all Phase 5.**
- **Research-integrity protections.** **Resume must never certify cached
  output.** Phase 2 already solved this primitive: `verify_determinism` forces
  `EPHEMERAL`, imports no store, and was proven by the poisoned-cache test to
  recompute rather than certify. Phase 3 **reuses it, does not reinvent it.** The
  lockbox ceremony — CLI unlock flag **and** structured config reason **and**
  write-ahead audit **and** permanent taint — is wired here for the first time.
- **Required tests.** Config-only run → stored result; detached run survives SSH
  logout; `kill -9` → INTERRUPTED → re-run reuses cache; determinism verification
  across two `run_id`s sharing one fingerprint; run-level poisoned-cache test; an
  architecture guard proving no tolerance is reachable from the CLI; lockbox
  ceremony refusal paths; a dirty tree correctly produces a noncanonical run.
- **Acceptance criteria.** Verification never reads cache; zero audit delta for
  TRAIN-only runs; gate green.
- **Checkpoint.** **CP-11 immediately follows.**

**Implementation evidence — COMPLETE.** The strict
`research-experiment-config-v1` parser accepts one complete, path-free resolved
definition, rejects unknown/duplicate fields and non-canonical parameter
encodings, and binds the dataset role to the exact TRAIN/VALIDATION/OOS/LOCKBOX
ceremony. The fixed composition root now owns the complete
load → feature → fold/purge → outcome → optional representation → model →
single-inference policy fan-out → evaluation sequence. The retired
`ComponentSpec`, `ExperimentConfig`, `ExperimentResult`, `Registry.from_spec`
and `core/domain.py` skeleton are gone with no shim.

The CLI exposes exactly `run`, `status`, `list` and `verify`. Detached `run`
uses `systemd-run --user --collect` first and an argv-only detached tmux session
as fallback; launch refusal terminalizes the already-registered attempt.
Foreground and detached workers reconstruct runtime authority only from
explicit machine paths while the stored resolved config remains the sole
semantic definition. Imported implementation helpers were made private so the
new modules expose only the closed config/orchestrator/CLI surface.

Normal execution upgrades the initially ephemeral feature materialization to
`PERSIST` because later stages consume it. A real SIGKILL after the third fold
leaves the write-ahead run visible; `scan_runs()` makes it `INTERRUPTED`, and a
new attempt completes with feature computation and model fitting disabled,
proving reuse of the already-published immutable caches. Verification registers
two run IDs under one fingerprint, forces the Phase-2 feature verifier and the
whole pipeline through cache-bypassing `EPHEMERAL` execution, compares
structural fields exactly and floating fields only under component-declared
identity-bearing tolerances, and stores the same immutable manifest-hashed
report on both attempts. A poisoned-cache test makes every store read/write
raise and proves the cache byte inventory is unchanged.

Gated-access goldens cover every ceremony edge: TRAIN refuses an unlock flag
without audit; OOS requires a structured reason and records permanent OOS
taint; LOCKBOX requires reason + declared evaluation + CLI unlock, writes the
decision before loading and records permanent lockbox taint. Dirty Git evidence
produces a permanently noncanonical run. Architecture guards pin the exact
module inventory and public surfaces, closed config fields and CLI verbs,
two-attempt stored verification, approved detached launchers, cache bypass,
absence of CLI-reachable tolerance, SQLite, civil-time machinery, table
libraries and Phase-4 contracts.

The unfiltered gate is green: 1,366 active tests (1,370 collected, 4 performance
tests deselected), 166 architecture tests, Ruff clean and strict mypy clean over
127 checked source files. Audit remains 3 records with SHA-256
`deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
D-6 remains `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
all five published view manifests, four external feature-cache entries and zero
external fitted states remain exact. Every Slice-3I run root, audit writer and
dataset identity was isolated and synthetic; no external run, OOS or LOCKBOX
research access occurred.

---

### 🔶 CP-11 — *"runs cannot lie"*

Engineering verification gate. Attack: can a run disappear? Can resume certify a
cached artifact instead of recomputing? Can two concurrent launches collide on an
attempt? Can a dirty tree produce a canonical run? Can OOS or LOCKBOX be reached
without ceremony and without a permanent record? Does fan-out understate the
inventory? Is the determinism report stored? Is SQLite genuinely absent? Is any
tolerance reachable from the CLI?
**Blocks on:** any run that vanishes; any cache-certified verification; any
unrecorded gated access; any inventory undercount; any CLI-reachable tolerance.

**CP-11 outcome: GREEN.** Registered attempts cannot disappear; interruption
is permanent and resumable through immutable stage caches; verification is a
two-attempt forced recomputation and its report is stored on both runs; dirty
and gated state permanently affect the manifest; policy work and evaluation
inventory remain separately exact; SQLite and CLI-reachable tolerance are
absent. This outcome permitted Slice 3J to begin.

---

### 3J — End-to-end qualification + performance + closeout

- **Objective.** Prove the whole loop on real TRAIN data and close the phase.
- **Exact scope.** End-to-end qualification across all six supported use cases;
  performance qualification per §5; documentation duties; the §6 completion
  checklist.
- **Existing areas touched.** `tests/performance/`; this document;
  `PROJECT_STATE.md`; `V1_ROADMAP.md`.
- **Dependencies.** 3I.
- **Explicit exclusions.** Any optimization; any profiling framework; any
  research conclusion; any OOS or LOCKBOX use; any SLA change not explicitly
  owner-approved from measured evidence.
- **Research-integrity protections.** TRAIN-only, NORMAL classification, zero
  audit delta enforced by a rejecting audit writer as in Phase 2.
- **Required tests.** The §5 protocol and the §4 matrix in full.
- **Acceptance criteria.** Config-only run → stored result **< 30 s** (dummy, no
  simulation); N-policy fan-out with zero re-inference proven by stage counters;
  double-run determinism verification passes; `kill -9` → INTERRUPTED → re-run
  reuses cache; **run-record scan golden test** green; full gate green
  unfiltered; audit log, D-6 fingerprint and all five view-manifest digests
  unchanged.
- **Checkpoint.** **CP-12 — whole-phase independent review**, to the CP-8
  standard: independent, skeptical, reproduces the evidence, verifies no guard
  was weakened and no Phase-4 vocabulary leaked, with named evidence at a named
  clean commit.

**Implementation and qualification evidence — COMPLETE.** The closed
qualification matrix names and executes exactly six supported loops:
stateless descriptive, stateless evaluated, supervised target-channel,
representation + reference-library, multi-policy fan-out, and cache-bypassing
double-run verification. All six complete end to end on isolated synthetic
TRAIN-like rows. The fan-out case records two policy executions per fold while
the instrumented model still infers exactly once per fold; verification creates
two distinct run IDs, leaves cache empty and stores both reports. The active
kill/restart, scan golden, poisoned-cache, gated-access and dirty-tree tests
remain part of the unfiltered gate.

The published-TRAIN performance harness was committed before execution and ran
at clean commit `5668388bee16833f7a7c9752905c2aac2597b3b6`. It used the real
runs filesystem, a rejecting audit writer, one discarded warm-up and five timed
full `resolved config → runtime integrity/Git/lock checks → stored run →
scan_runs()` repetitions over `XAUUSD_S_M1_TRAIN_V1` (1,414,538 dataset rows;
1,414,537 bounded feature rows). Every run was canonical NORMAL/TRAIN, produced
one fold, one inventory row and one policy execution, and was removed with its
test-owned temporary root after evidence capture.

| Repetition | Wall time (s) |
|---:|---:|
| 1 | 2.418778292 |
| 2 | 2.419455498 |
| 3 | 2.422959821 |
| 4 | 2.413784259 |
| 5 | 2.427797053 |
| **Median** | **2.419455498** |
| **Minimum** | **2.413784259** |

The median is **well below the 30 s budget**; the performance-miss protocol did
not trigger and no optimization was attempted. Peak RSS was 494,874,624 bytes
(evidence only; no budget). The deterministic fingerprint was
`1f28eb98be7ee29a73d2f68d9687d78776a736fd8332ad50127c8509437e04af`,
feature identity
`fbc90c689e0e6252f8e453454929e389b02f0f2dfc50041232cd44f713c9cbc0`,
and fitted-state identity
`6a09fbc118dd6a900d3106e34fe5ae6e393fc87bbaa1f42f9f16890f143588be`.
The six attempts had distinct run IDs while all semantic hashes, artifact
hashes and sizes matched exactly. Recorded artifact sizes were 22,634,011 bytes
for features, 22,634,052 for outcomes, 22,473,936 for model output and 190 for
the fitted-state reference; the metric summary was 1,150 bytes. Their SHA-256
values were respectively `d5fb4236…6747`, `59cbcdea…b0b1`,
`2003db79…dbde`, `5abf8332…309c`, and metric summary `9f0f9feb…4f1a`.

The discarded warm-up exposed one correctness bug before timed measurements:
the run reader's former 16 MiB bound could not hash a valid 22.6 MiB full-TRAIN
artifact. The shared immutable writer/reader limit is now an explicit bounded
64 MiB V1 contract, enough for the approximately 46 MiB all-reference-feature
shape; the writer refuses larger payloads before publication and a unit plus
architecture guard pin both sides. The full gate returned green after this
fix, before qualification resumed.

External state is unchanged after qualification: audit 3 records / SHA-256
`deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
D-6 `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
all five published view-manifest digests exact; four external feature-cache
entries; zero external fitted states; no surviving qualification run root.
No OOS, LOCKBOX, SNAPSHOT, RAW or CURATED market rows were read. Slice 3J is
complete.

**CP-12 outcome — GREEN / OWNER APPROVED (2026-08-14).** The independent
whole-phase review attacked the exact clean, pushed candidate
`7cf56cefd23818a90322296dae79addea8ed674c` and returned **APPROVE WITH
NON-BLOCKING NOTES**. The owner approved that verdict and Phase-3 closeout.
No guard was weakened and no architecture or Phase-4/5 change was requested.

Non-blocking-note disposition is deliberately narrow:

- **N-2:** no optimization. The non-vectorized OutcomeSpec path is within the
  approved performance budget and remains unchanged unless a real performance
  requirement emerges.
- **N-3:** correct only the inaccurate run-file-limit explanation. The 64 MiB
  ceiling remains unchanged; the comment now cites the measured Phase-2
  three-column artifact at 45,268,047 bytes (~43.2 MiB) and the Phase-3
  artifact that exposed the old limit at 22,634,011 bytes (~21.6 MiB), and
  states that 64 MiB is a bounded V1 ceiling above the supported shapes.
- **N-4:** no change, per owner direction.

---

## 4. Testing matrix

| # | Area | What it must prove |
|---|---|---|
| T1 | Contract validation | Every declaration field validated; refusal paths exact; no shim survives |
| T2 | Sparse semantics | absent ≠ `NaN` ≠ `0.0` through construction, persistence and evaluation |
| T3 | OutcomeSpec goldens | Hand-computed `forward_log_return_v1`; `horizon_rows = 1` is not degenerate |
| T4 | Alignment | Target attached to the decision key; an inversion control is caught |
| T5 | Boundary | Incomplete horizon → `NaN`; **never truncated**; a truncating control is caught |
| T6 | Discontinuity | Horizon crosses observed-key discontinuities; no session semantics; a closure-inferring control is caught |
| T7 | Forward reach | Perturbing beyond `p_terminal` never changes the target |
| T8 | Reserved namespace | Outcome namespace unreachable from features, inference and policies |
| T9 | Fit lifecycle | No-op fit; `training_targets=None`; both plugin fits return identity-free `FittedPayload`; `infer` refuses targets |
| T10 | FittedState identity | Only the composition root binds payload into state; all eleven §2.4 components pinned, **including dependency identity**; same bars + different target definition ⇒ different identity; full sensitivity/exclusion matrix |
| T11 | Representation | Fitted only on the fit view; participates in identity; a control fitted on eval range is caught |
| T12 | Row-local inference | Forward and backward perturbation rigs green for every registered model and representation |
| T13 | Adversarial model controls | All five controls caught; none registered in production |
| T14 | Rig coverage | Covered set **equals** registry set; registering without coverage fails the gate |
| T15 | Fold containment | Tier-2 ⊆ Tier-1 enforced; cross-view folds refused |
| T16 | Derived purge | `purge_rows == min(fit_rows, decision_delay_rows + horizon_rows)` exactly, across a horizon/delay/reach matrix; no hardcoded count |
| T17 | Fold-boundary leakage | Walk-forward rigs at **fold** boundaries; a purge-disabled control is caught; **no undeclared overlap survives** |
| T18 | Purge override | Default path leaves zero overlap; a declared override permits **only** its declared overlap; recorded override matches the applied fit set exactly; an override exceeding its declaration is caught |
| T19 | Policy fan-out | N policies over one output with a provably zero inference count |
| T20 | Intent trichotomy | Preserved across **canonical encode → decode** and consumer alignment; `IntentFrame` stays ephemeral with **no durable write path**; unknown `intent_schema_id` refused |
| T21 | Evaluator requirements | Declared; mismatch refuses **before** execution |
| T22 | Reference evaluators | Hand-computed goldens plus every §2.8 edge case: all-NaN pairs, constant vector, ties (average ranks), exact and signed zeros, below-minimum samples; effective sample counts reported and provably different between the two evaluators on identical data |
| T23 | Evaluation inventory | Exactly **`F·S·E`** rows with **no policy multiplier**; adding `P` policies leaves the count unchanged while `P` executions appear in the RunManifest; descriptive run = 0; pre-evaluation failure = 0 with a permanent record |
| T24 | No deflated statistic | Architecture-guarded absence anywhere in the codebase |
| T25 | Run identity | Fingerprint sensitivity/insensitivity; concurrent attempts never collide |
| T26 | Run state machine | Every transition; `kill -9` → INTERRUPTED; COMPLETE immutable |
| T27 | `scan_runs()` | Run-record scan golden test from a fixture tree; files-only reconstruction |
| T28 | Determinism | Two `run_id`s, one fingerprint; verification never reads cache; poisoned-cache test at run level; observed deviation reported |
| T29 | Tolerance discipline | No CLI/verifier-reachable tolerance; structural fields never toleranced; a declared `ToleranceSpec` is identity-bearing and recorded |
| T37 | Fold geometry | §2.7 exactly: half-open ranges; no partial final fold; refusal when no fold fits or the effective fit set is empty; chronological ordering; fold ⊆ one Tier-1 view; embargo applied after purge |
| T38 | Core vocabulary | The §2 public surface is exactly implemented — including Amendment 3's sole additive `FittedPayload`; no extra public type, no missing type, `BoundedView` absent from every `core` signature, `ModelInputs` carries no market column |
| T39 | Fixed-stage flow | `RepresentationModel.fit`/`encode` accept a `FeatureFrame` and **never** `ModelInputs` or a `RepresentationFrame`; only `ResearchModel` accepts `ModelInputs`; an architecture guard proves no representation-chaining path exists |
| T30 | Cache/resume | Interrupted run re-run reuses cache; `ephemeral` upgraded when a downstream consumer is configured |
| T31 | Gated access | Lockbox ceremony refusal paths; OOS explicit and recorded; zero audit delta for TRAIN-only runs |
| T32 | Architecture boundaries | Layer budgets; exact public surfaces; no dynamic public module attributes |
| T33 | No table-library leakage | No pyarrow/pandas/polars/duckdb in any new public annotation or export |
| T34 | No Phase-4 vocabulary | No `ExecutionSpec`, `TradeLog`, `EquityCurve`, fill, spread, commission, slippage or order semantics |
| T35 | No civil time / UTC | Existing guards extended to the new modules |
| T36 | Real-data smoke (TRAIN only) | One end-to-end run over published TRAIN; audit log unchanged |

---

## 5. Performance qualification protocol

**Budget:** config-only run → stored result **< 30 s** (dummy models, no
simulation), per the roadmap acceptance criterion.

**View:** published TRAIN `XAUUSD_S_M1_TRAIN_V1`, 1,414,538 rows. TRAIN is
`NORMAL`-classified: no ceremony, no unlock, **no audit event**. **No SNAPSHOT,
OOS, LOCKBOX, RAW or CURATED access is required or permitted**; the expected
audit delta for all of Phase 3 is **zero**.

**Method** — mirrors the owner-approved Phase-1 §8 and Phase-2 §10 protocol:

- **1 discarded warm-up + 5 timed repetitions**;
- report **median and min**; a budget is missed only if the **median** exceeds
  it;
- real runs root; all verification and integrity checks inside the timed region
  and unchanged;
- page cache warm; no cold measurement (sudo unavailable, and no budget requires
  it);
- recorded: per-repetition wall time, median, min, peak RSS, artifact sizes,
  content hashes, identity keys, row counts, inventory row count, Git identity.

**Evidence-only, no new SLA:** peak RSS and artifact sizes carry no budget,
exactly as Phase 1 treated `retained_payload_bytes` and Phase 2 treated artifact
size.

**Performance-miss protocol:** if a median budget is missed — record the result,
collect only the approved diagnostics, **STOP**, and report to the owner. Do not
optimize automatically. Serving an unverified artifact to hit a number remains
forbidden.

---

## 6. Phase-3 completion checklist

Phase 3 is COMPLETE only when every item is green and independently verified at a
named commit with named evidence.

**Contracts and lifecycle**
- [x] All §2 contracts implemented; ADR-0022 ratified and indexed.
- [x] Skeleton research protocols and carriers deleted with **no shim**.
- [x] `infer` structurally cannot receive targets.
- [x] Both plugin `fit` methods return identity-free `FittedPayload`; only the
  composition root can mint a complete `FittedState`.
- [x] `RepresentationModel` participates in the fit lifecycle and its state enters identity.
- [x] `RepresentationModel` consumes a `FeatureFrame` directly; **no representation-chaining path exists** and only `ResearchModel` accepts `ModelInputs`.
- [x] Refit-per-run default; `FittedState` is `cache_if_expensive`.

**Forward reading (release-blocking)**
- [x] `forward_log_return_v1` goldens exact; alignment to the decision key.
- [x] Incomplete horizon → `NaN`; **no truncation** anywhere.
- [x] Discontinuities crossed; no session/weekend/holiday/outage/closure semantics.
- [x] Forward-reach rig green; every negative control caught; no tolerance/skip/xfail.
- [x] Reserved outcome namespace unreachable from features, inference and policies.

**Row-local inference (release-blocking)**
- [x] Forward and row-locality rigs green for every registered model and representation.
- [x] All five adversarial controls caught; none registered in production.
- [x] Coverage meta-test proves an uncovered model fails the gate.
- [x] **No carried-state or online-inference API exists.**

**Folds and purge (release-blocking)**
- [x] Purge derived from declarations, never hardcoded; applied automatically.
- [x] Recorded per fold with derivation inputs; a reduction survives only through a declared structured override whose recorded effect matches the applied fit set exactly.
- [x] Fold-boundary leakage rigs green; purge-disabled control caught; **no undeclared overlap survives**.
- [x] Fold geometry matches §2.7 exactly, with no hidden default and no partial final fold.
- [x] Fold ⊆ Tier-1 view enforced.

**Decisions and evaluation**
- [x] N-policy fan-out with provably zero re-inference.
- [x] Intent trichotomy preserved across canonical encode → decode; `IntentFrame` still ephemeral with no durable write path; one legal `intent_schema_id`.
- [x] Evaluators declare requirements; mismatch refuses before execution.
- [x] Every metric reports its effective sample count; every §2.8 edge case is covered by a test.
- [x] Evaluation inventory exactly **`F·S·E`** with **no policy multiplier**; policy executions recorded in the RunManifest as executed work; descriptive and failed runs excluded from the inventory but permanently recorded.
- [x] No deflated statistic and no Plane-B metric anywhere.

**Runs and determinism**
- [x] Fingerprint / `run_id` / attempt correct; concurrent attempts never collide.
- [x] `kill -9` → INTERRUPTED detected; re-run reuses cache; nothing disappears.
- [x] COMPLETE run directories immutable.
- [x] `scan_runs()` run-record scan golden test green; **no SQLite**.
- [x] Determinism verification never reads cache; report stored as a run artifact.
- [x] No CLI/verifier-reachable tolerance; structural fields never toleranced.

**Boundaries and integrity**
- [x] Architecture tests **extended, never weakened**.
- [x] No table-library type in any new public annotation or export.
- [x] No civil-time or UTC machinery on the research path.
- [x] No Phase-4 concept (ExecutionSpec, TradeLog, EquityCurve, fills, costs) present.
- [x] **Zero gated access:** no SNAPSHOT/OOS/LOCKBOX/RAW/CURATED read; audit log byte-identical.
- [x] Dataset D-6 fingerprint and all five view manifest digests unchanged.
- [x] `./scripts/gate.sh` green unfiltered; Ruff and strict mypy clean.
- [x] Documentation duties complete; working tree clean; commits pushed.
- [x] **Phase 3 independently reviewed (CP-12) with verdict APPROVE WITH
  NON-BLOCKING NOTES; GREEN and OWNER APPROVED 2026-08-14.**

---

## 7. Explicit Phase-4 / Phase-5 deferrals

**Deferred to Phase 4 and explicitly not built here:** the cost-semantics audit ·
`ExecutionSpec` binding · event-driven replay over `IntentFrame`s · `TradeLog`
and `EquityCurve` · Plane-B / trade evaluators · fills, spread interpretation,
commission, slippage, lot value, order pricing · harness-inversion canaries ·
order/limit/stop/cancellation/staged-entry intent schemas · the intent-schema
**registry** (arrives with the second schema or the first simulator).

> **Correction (2026-08-14, Phase-4 planning — no Phase-3 contract changes).**
> The registry clause above is superseded. Phase-4 planning evaluated the
> trigger and found that one intent schema plus one simulator gives a registry
> nothing to resolve: the simulator declares its supported schema ids as a
> frozen literal tuple, exactly as `decisions/` declares the one legal id
> today. **The intent-schema registry arrives with the second schema**, not
> with the first simulator (owner decision OD-4J;
> [PHASE4_SIMULATION.md](PHASE4_SIMULATION.md) §3.4,
> [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §9). Everything
> else in this deferral list stands, and Phase 4 also inherits an addition
> Phase 3 did not anticipate: **no `PortfolioPolicy` object**
> ([ADR-0015](../adr/ADR-0015-v1-single-symbol-scope.md) Amendment 1).

**Deferred to Phase 5 and explicitly not built here:** sweep generation ·
process-pool fold/sweep execution · resumable walk-forward across runs ·
comparison CLI over the index · **the SQLite run index** · multiple-testing
denominator semantics and any deflated metric · disk telemetry · retention/GC.

> **Forward reference (2026-08-15, historical record above unchanged).** These
> items are now planned in
> [PHASE5_EXPERIMENT_OPS.md](PHASE5_EXPERIMENT_OPS.md) (Phase-5 planning
> COMPLETE, implementation NOT_STARTED). Two of them landed differently from
> the wording above, by owner decision:
> **process-pool execution is whole-run only and folds stay sequential**
> (OD-5C, [ADR-0018](../adr/ADR-0018-execution-model.md) Amendment 1), and
> **no deflated metric ships in V1 at all** — what ships is the trial
> denominator itself (OD-5A,
> [ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md), which closes
> the denominator question and is recorded against
> [ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md) as
> Amendment 4). **Retention/GC remains deferred and is explicitly not built in
> Phase 5 either** — Phase 5 ships disk telemetry only.

**Deferred indefinitely / by ADR:** carried-state and online-inference models ·
expanding-window, k-fold and combinatorial purged CV · additional OutcomeSpecs ·
`truncate` and `skip` boundary policies · a representation library · a metric
library · `quantile_spread` · portfolio logic and multi-symbol scope ·
narrower platform-code cache identity.

---

## 8. Documentation obligations

Recorded at planning time and discharged in Slice 3J unless stated otherwise:

- **This document** — updated with implementation evidence per slice, in the
  role PHASE2_FEATURES.md plays for Phase 2.
- **`PROJECT_STATE.md`** — slice records, checkpoint outcomes, exact next action.
- **`docs/roadmap/V1_ROADMAP.md`** — Phase-3 status only; detail stays here.
- **`docs/adr/README.md`** — ADR-0022 indexed (done at planning time).
- **`docs/architecture/V1.md`** — ADR inventory kept current (done at planning
  time).
- **`README.md`** — kept accurate as skeleton carriers are retired in Slice 3A.
- **`docs/guides/ADDING_FEATURES.md`** — re-verify its relative links after Slice
  3A retires `core/domain.py` symbols; change only if actually necessary.
- **A Phase-3 authoring guide** (`docs/guides/ADDING_MODELS.md` or similar) was
  not added in Phase 3. Any future guide is a later planning decision.

---

## 9. Status and gating

- **Phase 3 planning: COMPLETE (2026-08-13), OWNER APPROVED.** OD-3A…OD-3F are
  recorded above; ADR-0022 is ACCEPTED. Mid-implementation decision OD-3G was
  OWNER APPROVED 2026-08-14 without changing Amendment 3's substance.
- **Phase 3: COMPLETE (2026-08-14).** Slices 3A–3J are COMPLETE;
  CP-9/CP-10/CP-11/CP-12 are GREEN; CP-12's independent verdict is **APPROVE
  WITH NON-BLOCKING NOTES** and is OWNER APPROVED. The `FittedPayload` /
  platform-bound `FittedState` correction is recorded as OD-3G and ADR-0022
  Amendment 3 and was implemented in Slice 3C.
- **Phase 2 is COMPLETE** (2026-08-12, CP-8 GREEN, independent whole-phase
  verdict APPROVE) and is the dependency of this phase.
- **No hypothesis, OOS or LOCKBOX research use has occurred.** Component and
  six-loop qualification used synthetic rows; the sanctioned published-TRAIN
  infrastructure/performance qualification made no research conclusion and
  changed no external state.
- **Blocking decisions remaining for Phase 3: none.** All three roadmap items
  are resolved: the initial OutcomeSpec set (OD-3A), determinism tolerances
  (OD-3B), and the fitted-state materialization default (OD-3C, confirmed as
  already settled by ADR-0007 and ADR-0008 rather than reopened).
- **Exact next action at the time — SUPERSEDED (2026-08-14):** *"Begin Phase-4
  planning only. Do not implement Phase 4 yet."* Phase-4 planning is now
  **COMPLETE** ([PHASE4_SIMULATION.md](PHASE4_SIMULATION.md), ADR-0023
  ACCEPTED). The current next action is in
  [PROJECT_STATE.md](../../PROJECT_STATE.md).

---

*This document is the normative Phase-3 execution record. OD-3A…OD-3F are
owner-approved (2026-08-13), OD-3G is owner-approved (2026-08-14), ADR-0022 is
ACCEPTED, Slices 3A–3J are complete, CP-9/CP-10/CP-11/CP-12 are GREEN, and
Phase 3 is COMPLETE.*
