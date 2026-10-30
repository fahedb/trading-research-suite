# Phase 2 — Features & Calculations — Implementation Plan

**Status:** **PHASE 2 COMPLETE (2026-08-12) · SLICES 2A–2H COMPLETE ·
CP-6/CP-7/CP-8 GREEN · INDEPENDENT WHOLE-PHASE VERDICT APPROVE.**
This document is **normative for Phase 2 execution**, in the role
`PHASE1_DATA_LAYER.md` plays for Phase 1. The contract it operationalizes is
[ADR-0021](../adr/ADR-0021-columnar-feature-contract.md).
The
[Phase 1 §13 post-Phase-1 operational checklist](PHASE1_DATA_LAYER.md#13-post-phase-1-operational-checklist)
is **COMPLETE (2026-08-12)** — item 3 (Hyper-V VM export) completed on the
owner's explicit attestation and item 4 (owner review of items 1–3) COMPLETE
with all three items accepted — so **the §13 gate on Phase 2 is CLEARED** and
**Phase-2 implementation is permitted** under this plan and ADR-0021. Slice 2A
has implemented the contracts-only foundation; Slice 2B has added deterministic
feature execution and three reference providers; Slice 2C has added the
release-blocking leakage rigs; Slice 2D has added pure stage-identity hashing;
Slice 2E has added the deterministic artifact container and immutable store;
Slice 2F has added the clock-free materialization policy and end-to-end cache
composition; and Slice 2G has added cache-bypassing exact determinism
verification.
Slice 2H's first clean-commit qualification measured a 124.4 ms verified cache
hit over a 45,268,047-byte canonical artifact. On 2026-08-12 the owner
explicitly changed the verified cache-hit budget from 100 ms to **150 ms
median**, required every verification and integrity check to remain unchanged,
and prohibited optimization work aimed at the former 100 ms target.
**Planning record:** 2026-08-11 · **Planning baseline:** `main` @
`fc7984b736a78f7f57081e9cf6e238d19350c84d` (clean worktree) ·
**Implementation evidence updated:** 2026-08-12 on
`codex/phase2h-closeout`
Architecture reference: [docs/architecture/V1.md](../architecture/V1.md) ·
Roadmap entry: [docs/roadmap/V1_ROADMAP.md](../roadmap/V1_ROADMAP.md)
(status only) · Phase 1 plan: [PHASE1_DATA_LAYER.md](PHASE1_DATA_LAYER.md)

> **This document now records both the approved plan and implementation
> evidence.** Slice 2A added only core feature contracts, Slice 2B added the
> bounded execution path plus three reference providers, Slice 2C added only
> synthetic release-blocking leakage tests and architecture guards, and Slice
> 2D added a pure `artifacts → core` identity package, Slice 2E added only
> synthetic in-memory/container and temporary-directory store qualification,
> Slice 2F added exact materialization composition, and Slice 2G added only
> cache-bypassing verifier code plus synthetic qualification.
> Slice 2H used only the published NORMAL TRAIN view for engineering
> qualification and created the named immutable qualification artifacts through
> the public store. It added no data-read path or audit record and performed no
> hypothesis, model, evaluation, OOS or LOCKBOX research use.

This remains the normative Phase-2 execution plan (the role
`PHASE1_DATA_LAYER.md` plays for Phase 1) and records only approved decisions,
implemented evidence and explicit open items.

---

## 1. Owner-approved decisions — **OWNER APPROVED 2026-08-11**

Each decision below was independently verified against the accepted
architecture and the current implementation before being recorded. The
verification verdict is stated with each decision. None required an ADR
amendment; **OD-1 commits Phase 2 to writing one new ADR before implementation
begins.**

### OD-1 — Dedicated Phase-2 feature-contract ADR — **OWNER APPROVED**

Phase 2 will create a new focused ADR, provisionally **ADR-0021**, for the new
columnar feature-computation contract.

Replacing the current per-point contract

```
FeatureProvider.compute(MarketContext) -> FeatureSet
```

with the Phase-2 columnar contract is an **architectural / public-contract
change** and must not be treated as a silent implementation detail. It is
therefore subject to the change-control rule in
[CLAUDE.md](../../CLAUDE.md): *"Architectural changes (new stage, new layer
dependency, contract change, split-policy change) require a new or amended ADR
in `docs/adr/` BEFORE implementation."*

ADR-0021 will ultimately carry the approved `FeatureProvider`, `WindowSpec`,
feature-output, gap, backward-reach and composition semantics — including
OD-2…OD-5 below.

**ADR-0021 is deliberately not drafted yet.** Further Phase-2 owner decisions
are pending, and the repository's ADR convention does not call for a stub: the
index table in [docs/adr/README.md](../adr/README.md) lists only existing,
ratified ADR files and opens with *"ADR-0001 … ADR-0020 are ACCEPTED"*. Adding
a row for an unwritten ADR would create a broken link and misstate the
ratification set. The commitment is recorded here and in `PROJECT_STATE.md`
instead, and `docs/adr/README.md` is updated when the real ADR lands.

**Verification: CONSISTENT.** A new focused ADR is the correct instrument
rather than amending [ADR-0003](../adr/ADR-0003-columnar-hot-path.md) (whose
own *"when to reconsider"* warns against exactly this kind of accretion onto
the `MarketFrame` decision) or
[ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md) (which owns the closed
stage list). The *decision to replace* the per-row carrier is already
authorized by [ADR-0019](../adr/ADR-0019-keep-and-refactor-skeleton.md)
(*"Replace: the per-object bulk carrier…"*) and ADR-0003; what no accepted ADR
records is the **concrete replacement contract**, which is what ADR-0021 is for.

### OD-2 — Finite row-based backward reach — **OWNER APPROVED**

V1 `FeatureProvider`s use a **finite, observed-row-based backward reach**, not
an unbounded or expanding history contract. Every provider declares a finite
maximum backward reach.

- The exact required Phase-1 context boundary is derived through the
  **already-authorized keys-only route** — `MarketFrameLoader.load(grant,
  columns=())` — using the existing loader and the **exact grants** that will be
  passed to `open_bounded_view`. **No second data-access path is introduced.**
- At a TRAIN → VALIDATION boundary, or any other authorized context boundary,
  earlier authorized segments may supply backward context **according to the
  existing Phase-1 `BoundedView` rules** (ordered consecutive policy-role chain,
  exact grant per member).
- **Insufficient required context refuses by default**, under the existing
  Phase-1 short-context discipline (Slice 1E OD-3: no bare bypass; explicit
  acceptance requires structured evidence).
- **No UTC or timezone semantics are invented.** Row counts are arithmetic over
  the observed native-key axis.

**Verification: CONSISTENT.**

- The keys-only route is the exact pattern already used in production under
  Slice 1F **OD-F2** (*"one logical keys-only read of the complete admitted
  source via the existing sole decoder `MarketFrameLoader.load(...,
  columns=())`"*). Confirmed in code: `data/integrity.py::_validated_columns`
  returns an empty tuple for `columns=()`, and `_validated_bounds` defaults to
  the member's full admitted range when `lo`/`hi` are `None`.
- Metadata alone provably cannot substitute for the keys read. A
  `NormalizedManifest` carries only aggregate per-partition `first_key`,
  `last_key` and `rows`, from which an interior row position cannot be mapped to
  a key — the identical argument the repository already accepted in **OD-F6**.
- Backward context across an authorized boundary composes with
  `data/views.py::open_bounded_view` without modification: each member is
  bounded to `max(resolved_context_start, admitted.start)` …
  `min(eval_end, admitted.end)`, so a TRAIN member contributing context to a
  VALIDATION evaluation materializes **only** the required trailing rows, and
  `_resolve_context` sees `requested >= earliest_available` and raises nothing.
- Finiteness is required by
  [ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 1, which derives the
  sample information span — and therefore the mandatory fold purge — from a
  **declared** feature lookback. An unbounded/expanding reach has no declarable
  span.
- Row-based (rather than native-time-distance) reach is what keeps
  [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md) satisfied: a
  native-time window would have to attribute meaning to a multi-day key delta,
  which is precisely the session/holiday/closure semantics ADR-0005 forbids
  asserting.

### OD-3 — V1 gap policies: CROSS / BREAK only — **OWNER APPROVED**

V1 supports **exactly two** explicit gap policies:

| Policy | Semantics |
|---|---|
| **CROSS** | A feature window may use observed rows from **both sides** of an arithmetic gap. |
| **BREAK** | An arithmetic gap **breaks/resets** the usable window; the feature remains **unavailable** until a complete declared window has accumulated again after the gap. |

Additional approved rules:

1. **There is no default gap policy** and none is inferred. Every
   `FeatureProvider` declares its policy explicitly.
2. The **resolved** gap-policy choice is explicit and **identity-bearing** — it
   enters the feature/stage identity key and therefore the cache key.
3. **TRUNCATE / partial-window output is deliberately not supported in V1**,
   because there is no current concrete research requirement that justifies the
   additional semantics. *This is a scope decision, not a judgement that
   truncation is invalid.*
4. If a future research family genuinely requires partial-window behaviour, it
   may be added through the **normal reviewed architecture-change process**.
5. **No gap policy may allow a provider to reach farther backward than its
   declared finite maximum** (OD-2).
6. Gap semantics remain **purely arithmetic**. No session, holiday, outage or
   closure meaning may ever be inferred.
7. **BREAK must be genuinely enforced and tested**, so post-gap output cannot
   depend on pre-gap values.

Deliberately **not** reintroduced: `min_observations`, `TRUNCATE`, arbitrary
provider-defined gap vocabularies, or any speculative window-language feature.

**Verification: CONSISTENT.**

- Rule 1 directly satisfies
  [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md)'s consequence
  *"Window specs must explicitly choose their gap policy."*
- Rule 6 preserves ADR-0005's core decision and its OD-G2 clarification;
  "arithmetic gap" asserts nothing the dataset cannot prove.
- Rule 5, together with BREAK's full-window requirement, keeps the declared
  maximum a valid **upper bound** on backward reach under both policies, which
  is all ADR-0012 Amendment 1's derived purge requires. ADR-0012's own *"when to
  reconsider"* explicitly names over-conservatism as the acceptable direction:
  *"tighten the declared information span — never the enforcement."*
- Rule 2 matches [ADR-0007](../adr/ADR-0007-materialization-policy.md)'s
  identity-key inputs (plugin id/version/**params**). Recording the *resolved*
  choice rather than a provider's supported set is correct: the resolved policy
  is a computation input; a capability declaration is already covered by
  provider id + version.
- Rule 7 is discharged by the Phase-2 sentinel design, in which a BREAK
  provider's post-gap values must be **bitwise unchanged** when pre-gap rows are
  adversarially perturbed.
- Gap policy does **not** affect context resolution: both policies require the
  same declared number of prior observed rows, so OD-2's derivation and its
  refuse-by-default rule stand verbatim, with no generalization.

### OD-4 — Separate dense columnar feature-output carrier — **OWNER APPROVED**

Phase 2 introduces a **distinct computed-feature carrier**, working name
`FeatureFrame`, separate from `MarketFrame`.

Approved semantics:

- **columnar**;
- **dense**;
- **aligned exactly to evaluation native keys**;
- context rows may participate in computation but **do not appear as evaluation
  output**;
- feature columns are **`float64` in V1**;
- **finite values are valid** feature values;
- **`NaN` means feature unavailable** at that evaluation key;
- **positive/negative infinity is invalid and refused**;
- **feature-name collisions across composed providers fail fast**;
- sufficient **provenance** is retained to bind each output column to its
  provider and to its relevant `WindowSpec` / reach declaration.

**Sparse feature rows** and **non-numeric/categorical feature columns** are
deliberately **deferred** until a concrete research requirement justifies them.

`MarketFrame` **must not** be reused for computed features: it retains its
meaning as **loader-issued, verified, admitted market data**.

**Verification: CONSISTENT.**

- A separate carrier is **structurally required**, not stylistic:
  `core/frames.py::MarketFrame.__init__` raises, and instances are minted only
  by the private loader integration factory `_make_market_frame`. Reusing it
  would require widening that seam and would blur verified admitted market rows
  with computed values.
- Density does not contradict
  [ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md): its
  sparsity amendment legalizes a strict key subset for **ModelOutputFrame and
  IntentFrame** only, and says nothing about feature frames. ADR-0009's
  load-bearing *"absence of a row ≠ a dense `NaN` row"* distinction is
  inapplicable to a dense carrier, which has no absence — and remains fully
  intact for the frames it governs.
- `float64` + `NaN`-for-unavailable is consistent with
  [ADR-0003](../adr/ADR-0003-columnar-hot-path.md)'s recorded guidance that
  read-only NumPy arrays are the public column contract, and with the
  established `core/frames.py` pattern of exact-dtype validation with no casts.
- Refusing ±infinity is an addition, not a contradiction: `MarketFrame`
  validates dtype but not values, and no accepted contract requires infinity to
  be representable in a computed feature column.
- Retaining per-column provider and reach provenance is what lets Phase 3 derive
  the ADR-0012 Amendment 1 information span from the declaration.

### OD-5 — Platform-owned private cross-segment computation buffer — **OWNER APPROVED**

When an authorized feature calculation needs backward context from **more than
one** Phase-1 `BoundedView` segment, the platform may construct a **private,
temporary, contiguous computation buffer** from already-authorized key/value
arrays.

Approved constraints — the buffer:

- is **not** a `MarketFrame`;
- has **no invented or synthetic `DatasetRef`**;
- is **not** a new dataset or view;
- performs **no file I/O**;
- performs **no authorization**;
- **cannot bypass** the Phase-1 loader/grant boundary;
- is **not persisted** as market data;
- keeps **contributing source identities and exact relevant bounds visible** in
  computation provenance and identity;
- produces output limited to **evaluation keys** only.

Segment-stitching complexity is **platform-owned** rather than reimplemented
independently by every `FeatureProvider`.

**Verification: CONSISTENT — and specifically consistent with the exact
normative wording of Slice 1E OD-2.** That decision reads (this document,
Slice 1E):

> *"Consequently: no multi-identity `MarketFrame` will be created; no
> concatenated `MarketFrame` may hide multiple dataset identities; the caller
> must not receive an unbounded underlying frame."*

OD-5 satisfies all three clauses literally: it mints no `MarketFrame`; it
**hides no identity**, because contributing source identities and bounds remain
visible in provenance and enter the stage identity; and no unbounded frame
reaches a caller, because output is limited to evaluation keys.

The alternative — requiring each provider to iterate segments itself — would
push the leakage-critical seam-handling logic into every plugin, which
contradicts the reason
[ADR-0003](../adr/ADR-0003-columnar-hot-path.md) gives for the columnar hot
path in the first place: *"Alignment discipline replaces per-object validation,
which makes the leakage sentinel rigs (Phase 2) mandatory rather than
optional."* Centralizing the seam is what makes those rigs able to test it.

> **Wording note for future readers.** `PROJECT_STATE.md`'s summary of Slice 1E
> OD-2 uses the shorter phrase *"No multi-identity or concatenated frame is
> created"*, without the qualifier `MarketFrame`. The **normative** text is this
> document's Slice 1E section (and §5), both of which say `MarketFrame`
> explicitly. OD-5 is consistent with the normative wording; the summary is a
> summary, not a broader prohibition.

---

## 2. Recorded normalizations

Two minor normalizations were applied when recording the decisions above. Both
preserve owner intent exactly and neither changes a contract.

| # | Normalization | Why it does not change intent |
|---|---|---|
| N-1 | OD-3's "arithmetic gap" is recorded as requiring an **explicit `nominal_interval`** supplied by the provider's window declaration. | A gap is defined as an adjacent-key delta differing from a nominal interval. The repository already forbids inferring that interval: `data/gaps.py::GapIndex.from_frame` takes it as a **required, keyword-only, positive** `int` with no default, and OD-G2 records that it is never parsed from the free-text `timeframe` label, derived from `TimeUnit`, hardcoded, or inferred from the data. Without an explicit interval, BREAK is undefined; this makes the existing discipline explicit rather than adding a rule. |
| N-2 | OD-5's buffer is recorded as spanning the **resolved contiguous context axis, including cross-segment seams**, so a seam transition is an ordinary arithmetic discontinuity for OD-3 purposes. | This follows from OD-5 (a contiguous buffer across segments) plus OD-3 rule 6 (gaps are arithmetic only). A TRAIN→VALIDATION seam delta is an observed arithmetic fact and carries no additional meaning, exactly as ADR-0005 requires. |

---

## 3. Previously open implementation details — **RESOLVED 2026-08-12**

The owner delegated these as implementation refinements, with the instruction to
prefer naming that makes off-by-one behaviour impossible to misunderstand, and
to bind feature output to the actual observed evaluation axis.

### 3.1 Backward reach — resolved as `max_prior_rows`

**The declared field is `max_prior_rows: int >= 0` — the maximum number of
observed rows *strictly before* the evaluation row that a provider may read.**

This spelling was chosen because **every derived quantity equals the field
itself**, so no arithmetic and therefore no off-by-one is possible:

| Quantity | Value |
|---|---|
| Backward reach (purge input, sentinel bound) | `max_prior_rows` |
| Leading unavailable evaluation rows (warm-up) | `max_prior_rows` |
| Unavailable rows after each discontinuity under `BREAK` | `max_prior_rows` |
| Window-free provider | `max_prior_rows == 0` |
| Total observations spanned by the window | `max_prior_rows + 1` |

The rejected alternative — declaring total window length — makes all four
derived quantities `N − 1`, which is exactly the arithmetic the owner asked to
eliminate. A 20-bar rolling mean therefore declares `max_prior_rows = 19`.

`max_prior_rows == 0` never incurs warm-up and can never be invalidated by
`BREAK`, because a single-observation window contains no adjacent transition —
so ADR-0006's *"models that do not use windows neither pay for nor depend on
that abstraction"* holds literally.

### 3.2 Evaluation-key binding — resolved

**`FeatureFrame.keys` is exactly `BoundedView.eval_frame.keys`** — identical
length, order and values. Output is bound to the **observed** evaluation axis,
never to requested boundaries, which need not coincide with observed keys (a
requested boundary may fall inside a gap, Slice 1E OD-6).

### 3.3 Correction carried forward — **RESOLVED, recorded**

An earlier Phase-2 planning draft proposed exposing a `GapIndex` on the
provider-facing request object. **That is not implementable in this
repository:** `GapIndex` lives in `trading_research.data.gaps`, and
`tests/test_architecture.py::ALLOWED` grants `features → {"core"}` and
`core → set()`, so neither layer may import it. `GapIndex.from_frame` also
requires `type(frame) is MarketFrame` exactly, which the OD-5 buffer
deliberately is not. Discontinuity detection for OD-3 BREAK is therefore
computed **inline from the key array and the explicit nominal interval**, using
the identical arithmetic definition. `GapIndex` remains the public
arithmetic-facts symbol of the data layer, unchanged.

---

## 4. Additional owner-approved directions — **OWNER APPROVED 2026-08-12**

### 4.1 Stage identity — ADR-0007 implemented directly and minimally

The identity key represents every semantic input that can change the
computation, and nothing else:

1. ordered contributing authorized input identities — `(dataset_id, manifest_sha256)`
   per segment, **role excluded** (role is validated metadata, not identity);
2. the exact bounded interval — `resolved_context_start`, `eval_start`, `eval_end`
   as raw integers, plus `time_unit` and `clock_domain` identifier;
3. exact split-policy identity `(adr, policy_version, policy_sha256)`;
4. ordered provider entries — `provider_id`, `version`, canonical `params`,
   ordered `feature_names`, ordered `required_columns`, and the **canonical
   resolved window declaration** (`max_prior_rows`, `gap_policy`,
   `nominal_interval`);
5. platform-code identity — Git commit, dirty flag, and diff digest when dirty.

**Deliberately excluded, each for a stated reason:**

| Excluded | Reason |
|---|---|
| `uv.lock` digest | Owner-approved exclusion. A committed lockfile change already changes the Git commit; an uncommitted one already changes the dirty/diff identity. It also fails to detect the real risk — installed-environment drift relative to the lockfile — which is ADR-0011's Phase-3 canonical-run "lockfile in sync" precondition |
| Separate `seed` field | **Smaller design:** a stochastic provider declares `seed` as an ordinary canonical parameter, which is already identity-bearing under item 4. ADR-0007's requirement that seed enter the identity is satisfied with zero new surface. No V1 reference provider is stochastic |
| `identity_algorithm_version` | Redundant with platform-code identity by the same argument as `uv.lock`: the encoding lives in the repository, so changing it changes the commit |
| `stage_kind` | The cache path already namespaces feature artifacts under `cache/features/`; adding it to the hashed payload buys nothing |
| `ViewRole`, filesystem paths, hostname, pid, wall time, machine identity | Not computation inputs. ADR-0011 captures machine identity but excludes it from fingerprints; ADR-0004 makes a path resolver configuration, never identity |

`resolved_context_start` is a **deterministic function** of items 1, 2 and 4 —
a pinned manifest digest pins the exact key array — so it is recorded as
provenance in the artifact, and a test pins the invariant *same identity key ⇒
same resolved context_start*. This is also what makes a cache hit require no
probe and no load.

**Canonical encoding:** one JSON document, `sort_keys=True`,
`ensure_ascii=True`, `separators=(",", ":")`, floats via `float.hex()`, then
SHA-256 → 64 lowercase hex.

### 4.2 MaterializationPolicy

All four accepted policies exist. Feature default is `CACHE_IF_EXPENSIVE`.

| Policy | Phase-2 behaviour |
|---|---|
| `EPHEMERAL` | Compute and return. Never reads or writes the store |
| `CACHE_IF_EXPENSIVE` | Expensive iff **any** provider declares `CostHint.EXPENSIVE`; then behaves as `PERSIST`, else as `EPHEMERAL`. **No wall-time measurement anywhere** (ADR-0007 Amendment 2), architecture-tested |
| `PERSIST` | Look up; on hit verify and return; on miss compute and publish |
| `PERSIST_ON_FAILURE` | **Recognized but inert in V1** — writes nothing. The exception carries the identity key and the offending `provider_id`, which is sufficient to debug a single-stage computation |

V1 cost hints are exactly `CHEAP | EXPENSIVE`, because the hint feeds one boolean
decision. **No policy may create an alternate persistence path for protected
market rows**, and no generalized debug-dump framework is built. When
`persist_on_failure` is implemented (Phase 3, with run directories) it may write
only stage-owned diagnostic artifacts, never raw market input.

ADR-0007's rule that *"the orchestrator must upgrade `ephemeral` when a
downstream consumer is configured"* has no orchestrator in Phase 2 and is
recorded as a **Phase-3 obligation**, not silently ignored.

### 4.3 Artifact store

Filesystem-based, immutable, identity-addressed. Layout under the runs root:

```
<runs-root>/cache/features/<identity_key>/<content_hash>.npz
```

The entry filename **is its own content hash**, so an entry is self-verifying and
no separate manifest file is needed. Presence under the final name means
complete and verified.

- **Payload: uncompressed `.npz`**, members written in fixed declared order —
  `__meta__` (canonical JSON as a `uint8` array), `__keys__` (int64), then each
  feature column in declared order. Read with `allow_pickle=False`, which refuses
  object arrays and structurally enforces ADR-0017's pickle ban.
- **Content-hash rule:** `content_hash = sha256(entire file bytes)`. This is the
  smallest deterministic contract that satisfies ADR-0007 (SHA-256 of the
  canonical serialized output) and ADR-0017 (versioned array formats + JSON
  metadata), and it is directly regression-testable.
- **Repository verification (performed 2026-08-11, scratchpad only, synthetic
  arrays):** on 1,414,538 rows × (int64 + 3 × float64) with pyarrow 25.0.0 /
  NumPy 2.5.1, `.npz` bytes were **identical across separate processes, across a
  3-second gap, and across differing `PYTHONHASHSEED`**, because `np.savez` pins
  every ZIP member timestamp to the constant `(1980, 1, 1, 0, 0, 0)`. Read +
  SHA-256 + materialize measured **59.0 ms** (versus 48.0 ms for Parquet and
  34.8 ms for a custom binary container), comfortably inside the then-planned
  100 ms cache-hit budget. **No blocker was found, so `.npz` is adopted and both a
  custom container and a second Parquet path are rejected.**
- Because NumPy's constant timestamp is an implementation behaviour rather than
  a documented guarantee, a **regression test** asserts byte-identical repeat
  writes, byte-identical subprocess writes, and the constant ZIP member
  `date_time`. A NumPy upgrade that broke it would fail the gate loudly. (Entries
  are never read cross-version anyway, since the platform commit is in the
  identity key.)
- **Atomic publication** (ADR-0017): temp file in the same directory → `fsync` →
  no-replace publication → parent `fsync` → unlink temp. A process best-effort
  unlinks only its own temp; stray temps are inert because lookup matches only
  the content-hash name.
- **Integrity verified on every read:** recompute SHA-256 and compare to the
  filename, then check the embedded identity key matches the requested key. Any
  mismatch raises — never a silent miss, never a served value.
- **Conflict is a hard failure.** Two differing entries under one identity key
  means the same semantic inputs produced different content: a determinism defect
  that must be loud. **Never a silent overwrite.** Republishing byte-identical
  content is an idempotent success.
- **Not built:** cache database or index, daemon, TTL, LRU, automatic GC,
  eviction, compression, distributed cache, cache CLI, or any cache-management
  framework.

The durability primitive is implemented **inside `artifacts`** and
`data/atomic_write.py` is **left untouched**. Verified this pass: `core` performs
no filesystem I/O whatsoever — no module under `src/trading_research/core/`
imports `os` or `pathlib` or calls `open()` — so promoting a publication
primitive into `core` would introduce the first I/O there and change its
character. The new module is written to the same discipline and includes the
parent-symlink hardening described in CP-4 note 3 from the outset; that note
remains open against `data/atomic_write.py`, whose trigger ("may gain callers
later") does not fire, because Phase 2 gives it no new callers.

### 4.4 Determinism verification

Proportional to the MVP. Three distinct properties, three instruments:

| Property | Instrument |
|---|---|
| **P1** — stage purity within one process (module state, mutable defaults, RNG carry-over) | Ordinary provider tests, auto-parametrised over the registry: call `compute` twice on the same request and assert bitwise equality; interleave a different request and re-check |
| **P2** — independent-run reproducibility | `verify_determinism(...)` recomputes independently; the test additionally runs a fresh subprocess with a differing `PYTHONHASHSEED` |
| **P3** — cache-bypass correctness | Poisoned-cache test, a raising store double, and an unchanged cache inventory |

`verify_determinism` **forces the ephemeral path regardless of the configured
policy**, so bypass cannot be misconfigured, and it **neither reads nor writes**
the store. Normal feature requests compute **once**; there is no mandatory
double computation.

**No global floating-point tolerance framework is built.** ADR-0016's existing
distinction is retained: structural properties (column set and order, dtypes,
keys, NaN positions, provenance) are compared **exactly**; the V1 reference
providers are simple trailing arithmetic and reproduce exactly on the pinned
environment. A future provider that genuinely cannot is an escalation under
ADR-0016, not a test relaxation.

ADR-0016's *"the verification report is a stored artifact"* is satisfied fully in
Phase 3, where run directories exist; Phase 2 returns the report object and the
qualification records it. This gap is stated rather than papered over.

### 4.5 Reserved outcome/target namespace — **OWNER APPROVED 2026-08-12**

V1 reserves exactly the feature name `target` and every feature name beginning
with the case-sensitive prefix `target.`. The same exact rule applies to both
`FeatureProvider.feature_names` and `FeatureProvider.required_columns`, and a
violation refuses before any provider computation begins.

No case folding, alternate separator, synonym, wildcard or broader target-like
vocabulary is reserved in V1. Examples: `target` and `target.forward_return_1`
refuse; `Target`, `TARGET`, `target_value` and `mytarget.value` are not captured
by this naming rule.

---

## 5. Final planning reconciliation (2026-08-12)

A bounded reconciliation confirmed the plan is implementable against the
repository as it exists. Findings:

| Check | Result |
|---|---|
| Slice plan implementable as the repository stands | **Yes.** The only structural prerequisite is adding `ALLOWED["artifacts"] = {"core"}` and creating the package, which **implements** V1.md §2's accepted dependency graph rather than widening it |
| OD-1…OD-5 plus §4 directions compose cleanly | **Yes.** No pair conflicts; §4.1's exclusions and §3.1's naming remove the last ambiguities |
| Dependency boundaries respected | **Yes**, with one real constraint already designed around: `ALLOWED["features"] = {"core"}`, so `features` **cannot** import `BoundedView` from `data`. The executor therefore consumes primitives, and the `BoundedView` → request adaptation lives in `experiments`, the accepted composition root |
| Any abstraction existing solely for hypothetical future use | **None survived.** `min_observations`, `TRUNCATE`, `uv.lock` digest, separate `seed` field, `identity_algorithm_version`, `stage_kind`, cache directory fan-out, a custom binary container, `FeatureFrame.identity_key`, an aggregate `max_prior_rows` accessor and a per-column NaN-count accessor were each removed after failing the minimalism rule |
| Remains a small modular-monolith extension | **Yes.** Two new `core` modules, two new `features` modules, one new `artifacts` package, one bounded `experiments` module. No new dependency |
| Leakage / reach / TRAIN-VALIDATION / gated-data protections intact | **Yes.** Market data still enters only through `MarketFrameLoader.load(AccessGrant, …)`; `open_bounded_view` remains the sole composition route; gating, audit and the split policy are untouched; no Phase-2 component holds an `AccessGrant` or opens a file under the dataset or views roots |
| Reference providers remain mundane infrastructure probes | **Yes** — three, each proving a distinct platform property, none a research signal (§7, Slice 2B) |

Also confirmed: the `core` package export guard
(`test_core_package_exposes_only_the_approved_market_frame_symbol`) filters to
bindings sourced from `core.frames`, so adding exports from the new `core`
modules does not disturb it — new modules receive their own exact-surface guards.

---

## 6. Minimal contracts

Semantic sketches; module placement is an implementation detail.

```
GapPolicy (StrEnum)      CROSS | BREAK
CostHint  (StrEnum)      CHEAP | EXPENSIVE

WindowSpec (frozen)
    max_prior_rows: int >= 0
    gap_policy: GapPolicy                 # explicit, no default
    nominal_interval: int | None          # required iff BREAK; positive, <= int64 max;
                                          # never parsed, derived, defaulted or inferred

FeatureRequest (frozen, issued only by the platform)
    unit · clock_domain · source_identities: tuple[DatasetIdentity, ...]
    keys        -> read-only int64 array over the resolved context axis,
                   strictly increasing, spanning segment seams, none > eval_end
    column(name)-> read-only array; resolvable ONLY for the caller's declared
                   required_columns (undeclared name refuses)
    eval_offset: int · eval_length: int
    classmethod from_segments(frames, *, eval_start, eval_end, required_columns)

FeatureProvider (Protocol)
    provider_id: str · version: int
    feature_names: tuple[str, ...]        # ordered, non-empty, unique
    required_columns: tuple[str, ...]
    window: WindowSpec
    params: Mapping[str, ParamValue]      # ParamValue = str|int|float|bool|None|tuple[...]
    cost_hint: CostHint
    compute(request: FeatureRequest) -> Mapping[str, NDArray[float64]]

FeatureFrame (frozen, immutable, private construction)
    keys -> read-only int64 == BoundedView.eval_frame.keys (dense)
    unit · clock_domain · num_rows · source_identities
    column_names -> tuple[str, ...]       # deterministic order
    schema       -> Mapping[str, str]     # V1: every value "float64"
    column(name) -> read-only float64 array
    origin(name) -> FeatureOrigin(provider_id, version, params, window)

MaterializationPolicy   EPHEMERAL | CACHE_IF_EXPENSIVE | PERSIST | PERSIST_ON_FAILURE
PlatformIdentity        git_commit · git_dirty · diff_sha256 | None   (caller-supplied)
StageIdentity           -> identity_key() -> 64 hex
ArtifactStore           get(key) -> bytes | None ;  put(key, payload) -> content_hash
DeterminismReport       identity_key · content hashes · structural comparison · verdict
```

**Public-contract boundary.** Feature columns are read-only NumPy `float64`
arrays. No PyArrow/pandas/Polars/DuckDB type appears in any public signature.
`PlatformIdentity` is **caller-supplied**, exactly as `AccessContext` is today —
no platform library shells out to `git`.

---

## 7. Slices

Order: **2A → 2B → 2C → CP-6 → 2D → 2E → 2F → CP-7 → 2G → 2H**.

### 2A — Feature contracts + retirement of the per-point `FeatureProvider` — **COMPLETE (2026-08-12)**

- **Objective.** Land the full public vocabulary with zero computation and zero I/O; delete the one contract Phase 2 replaces.
- **Responsibilities.** `GapPolicy`, `CostHint`, `WindowSpec`, `FeatureOrigin`, `FeatureRequest` (validation + `from_segments`), `FeatureFrame`, the new `FeatureProvider` protocol, the `ParamValue` grammar and canonical encoding, new error types, registry re-typing, guard extension.
- **Files.** new `core/features.py`, new `core/feature_frames.py`; `core/interfaces.py` (delete old protocol, add new); `core/errors.py`; `core/__init__.py`; `features/registry.py`; `tests/doubles.py`; `tests/test_contracts.py`; `tests/test_architecture.py`; new `tests/test_feature_contracts.py`. **`core/frames.py` is not touched.**
- **Excluded.** Any provider, execution, identity, hashing or I/O; any change to `MarketFrame`, loader, gating, audit or manifest schema; retirement of any Phase-3 carrier.
- **Tests.** Contract validation (ordered/unique/non-empty names; undeclared-column refusal; wrong output length/dtype/name set; `max_prior_rows < 0`; `BREAK` without `nominal_interval`; oversized `nominal_interval`; non-canonical `params`; ±inf refusal). `from_segments` refusals: unordered, overlapping, duplicate keys, mixed unit, mixed clock domain, mixed dtype, **any key > `eval_end`**, empty evaluation range. `FeatureFrame` immutability and fresh-descriptor isolation. Architecture: exact public surfaces; `features` imports only `core`; no table-library type in a public annotation; no dynamic public module attributes.
- **Acceptance.** Gate green; old protocol gone with **no shim**; `core/domain.py` byte-unchanged; architecture-test count grows with no guard weakened.
- **Rollback boundary.** One commit, `core/` + `features/registry.py` + tests.
- **Evidence.** Implemented on `codex/phase2a-implementation`; 833 tests
  collected; unfiltered `./scripts/gate.sh` green; Ruff and strict mypy green;
  focused independent reviews returned **APPROVE / no blockers** after all
  findings were remediated. The old per-point protocol is gone with no shim;
  `core/domain.py`, `core/frames.py`, `data/*`, dependencies and lockfile are
  unchanged. The audit log remains exactly 3 lines with SHA-256
  `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
  the D-6 source fingerprint remains
  `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
  all five generated-view manifest digests are unchanged.
- **Slice-boundary reconciliation.** 2A pins the provider declaration types and
  validates the request/frame carriers. Ordered/unique/non-empty provider
  declarations and declared-versus-emitted output names are runtime plan
  validation and remain in 2B, where provider invocation first exists.

### 2B — Feature execution + reference providers — **COMPLETE (2026-08-12)**

- **Objective.** Produce a correct dense aligned `FeatureFrame`, with the platform — not the plugin — owning warm-up and gap semantics.
- **Responsibilities.** `features/execution.py` (plan validation, deterministic ordering, provider invocation, output validation, **platform-applied unavailability mask**, frame assembly); `features/reference.py` (three providers + registration); `experiments/features.py` (the single `BoundedView` → `FeatureRequest` adaptation).
- **Alignment.** Output index ≡ `view.eval_frame.keys`. Column order = supplied
  provider-plan order, then each provider's declared `feature_names` order.
  Shuffling providers must yield bitwise-identical values and origins **by
  feature name**; it changes only the frame's declared column order.
- **Mask.** Evaluation row `i` is unavailable if fewer than `max_prior_rows` observed rows precede it on the resolved axis; under `BREAK`, additionally if any in-window adjacent transition deviates from `nominal_interval`. Applied **after** `compute` returns.
- **Errors.** Name collision names both providers and fails **before** computation; reserved-namespace name refuses; wrong key set/length/dtype refuses naming the provider; missing required column refuses; short context refuses unless explicitly accepted.
- **Reference providers** — three, each proving a distinct platform property, none a research signal:
  | Provider | `max_prior_rows` | Proves |
  |---|---|---|
  | `log_return_1` | 1 | minimal warm-up (exactly one leading NaN), single required column |
  | `rolling_mean_close` (window param) | `window − 1` | non-trivial warm-up, parameters entering identity, both gap policies, real vectorized cost at 1.4 M rows |
  | `bar_range` | 0 | multiple required columns, zero warm-up, and that a window-free provider pays nothing |
  All declare `CostHint.CHEAP` honestly; the `EXPENSIVE` branch is exercised by a **test-only** provider rather than by mis-declaring a reference provider.
- **Excluded.** Identity, hashing, caching, persistence, the rigs, config, CLI, run directories, parallelism, any second `experiments` entry point.
- **Tests.** Hand-computed goldens for all three; alignment/order/length properties; warm-up boundary (exactly `max_prior_rows` NaNs, no more, no fewer); gap-policy behaviour at a synthetic gap; multi-segment seam equivalence against a single contiguous axis; provider-shuffle bitwise equality; **P1 in-process purity** for every registered provider; all refusal paths.
- **Acceptance.** Goldens exact; synthetic data only; `experiments` gains exactly one module with a guarded exact surface.
- **Evidence.** Implemented on `codex/phase2b-implementation`. One isolated
  `FeatureRequest` is minted per provider after complete-plan validation; the
  platform owns warm-up and `BREAK` masking; `compute_features` is the sole
  `BoundedView` adapter; and the live registry contains exactly
  `log_return_1`, `rolling_mean_close`, and `bar_range`. The owner-approved
  reserved predicate is enforced exactly in both declaration fields before
  computation. The repository collects 923 tests (920 active in the default
  gate and 3 explicitly deselected performance tests); the full gate, Ruff and
  strict mypy are green. Independent contract, semantic and test reviews found
  no remaining blocker after signed-int64 gap arithmetic, huge-reach handling,
  and architecture-boundary findings were remediated. Production data-layer,
  cache, artifact, config and Phase-3 carrier code remain untouched. The audit
  log remains exactly 3 lines with SHA-256
  `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
  the D-6 metadata fingerprint remains
  `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
  all five generated-view manifest digests remain unchanged.

### 2C — Leakage, backward-reach and gap-enforcement rigs (release-blocking)

- **Objective.** Prove mechanically, for **every registered provider**, that nothing at or before a cutoff depends on later data, that no declared reach is under-declared, and that `BREAK` genuinely isolates across a gap.
- **Rigs.** (1) **Future perturbation** — replace all value rows after cutoff `k_c` with adversarial values (keys untouched), recompute, assert **bitwise** equality including NaN positions at every evaluation index ≤ c. (2) **Backward reach** — perturb rows strictly before `keys[i − max_prior_rows]`, assert value `i` unchanged. (3) **BREAK isolation** — perturb pre-gap rows, assert post-gap output unchanged (OD-3 rule 7). (4) **Seam** — (1)–(3) across a multi-segment view.
- **Cutoffs.** Derived per provider from its declared reach: just past warm-up, midpoint, `N − 2`, and immediately after a synthetic gap.
- **Comparison: exact, never toleranced.** For a trailing vectorized computation over unchanged inputs the arithmetic is the same operations on the same bits. A provider whose past values shift when future rows change is rejected; a last-ULP failure would be an ADR-level decision, never a test relaxation.
- **Auto-coverage.** Parametrised over `FEATURE_PROVIDERS.names()`, plus a meta-test asserting covered set **equals** registry set, so registering a provider without coverage fails the gate.
- **Negative controls (test-only; each must be caught).** `FutureShiftProvider` (reads `close[i+1]`), `CenteredWindowProvider`, `WholeDatasetNormalizationProvider`, `EvalEndPeekProvider`, `UnderDeclaredReachProvider` (declares 5, reads 50 — caught only by rig 2), `GapCrossingUnderBreakProvider` (caught only by rig 3). An architecture test asserts none is registered in the production registry.
- **Acceptance.** All six controls detected; every registered provider green; **no skip, no xfail, no tolerance**; a rig failure fails the gate.
- **Evidence.** COMPLETE on `codex/phase2c-leakage-rigs` (2026-08-12), with
  no production-source change. The future, declared-backward-reach, arithmetic
  `BREAK` and multi-segment seam rigs run over the exact live registry; the
  `BREAK` declaration wrapper retains each live computation and declared
  reach. Comparisons pin float64 NaN masks and raw uint64 bytes, every future
  comparison has a finite witness, and all six named test-only controls fail
  through their intended dedicated mismatch path. Architecture guards prohibit
  control registration, registry mutation, authority/I/O, skips, xfails,
  tolerances and a relaxed or unwired comparator. The repository collects 958
  tests (955 active in the default gate and 3 explicitly deselected performance
  tests); the full gate, Ruff and strict mypy are green. Independent contract,
  semantic and test reviews returned APPROVE with no remaining blocker. The
  audit remains exactly 3 lines with SHA-256
  `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
  the D-6 fingerprint remains
  `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
  all five generated-view manifest digests remain unchanged.

### CP-6 — **GREEN (2026-08-12)** — engineering verification gate: *"the feature path cannot see the future"*

The bounded attack passed: requests expose no key beyond `eval_end` and only
declared columns; the exact reserved namespace is rejected before computation;
platform masks run after output validation; all live providers are covered by
exact future, reach, `BREAK` and seam rigs; all six controls are caught; and no
guard was weakened. This cleared Slice 2D, which is complete; no owner decision
is required. Slices 2E–2H and CP-8 are now complete; Phase 2 is complete.

### 2D — Stage identity (pure) — **COMPLETE (2026-08-12)**

- **Objective.** A deterministic pre-execution identity key over exactly §4.1's inputs.
- **Files.** new `artifacts/__init__.py`, `artifacts/identity.py`; `tests/test_architecture.py` (`ALLOWED["artifacts"] = {"core"}`); new `tests/test_stage_identity.py`.
- **Excluded.** Any I/O; any `git` subprocess; the experiment fingerprint; narrowing platform identity (ADR-0007: *measure before narrowing*).
- **Tests.** Stability across processes and `PYTHONHASHSEED`. Sensitivity: each §4.1 input individually changes the key. Insensitivity: pid, hostname, cwd, wall time, `ViewRole`, path and `params` dict ordering do not. Non-canonical `params` refuses. Invariant: same key ⇒ same resolved `context_start`. Architecture: `artifacts` imports only `core`; no filesystem, subprocess or `time` import in `identity.py`.
- **Evidence.** COMPLETE on `codex/phase2d-stage-identity` (2026-08-12).
  `PlatformIdentity` and `StageIdentity` are the package's exact public
  surface. The identity snapshots exactly §4.1's ordered sources,
  resolved/evaluation bounds and native axis, split-policy triple, ordered
  provider declarations and caller-supplied platform triple; excluded role,
  path, cost and ambient facts are structurally absent. One independent fixed
  canonical-document golden plus complete sensitivity, exclusion, refusal,
  detachment and cross-process matrices pass. Architecture guards pin the
  exact `artifacts → core` dependency, compact sorted ASCII JSON, UTF-8 and
  SHA-256 chain, and prohibit Git/filesystem/environment/time authority. The
  repository collects 1004 tests (1001 active in the default gate and 3
  explicitly deselected performance tests); the full gate, Ruff and strict
  mypy are green. Independent contract, semantic and test reviews returned
  APPROVE with no blocker. The audit remains exactly 3 lines, D-6 and all five
  generated-view manifest digests remain unchanged, and no cache entry or
  durable feature artifact was created.

### 2E — Artifact store — **COMPLETE (2026-08-12)**

- **Objective.** A store that can never serve a partial, corrupted or mismatched artifact, and survives `kill -9` and concurrent writers.
- **Files.** new `artifacts/container.py` (`.npz` encode/decode + canonical meta), `artifacts/store.py` (layout, lookup, publication, its own hardened atomic publisher).
- **Excluded.** GC, TTL, LRU, eviction, any index or database, compression, disk telemetry, remote storage. `data/atomic_write.py` untouched.
- **Tests.** Round-trip fidelity for every dtype/shape used; `content_hash == sha256(file)`; byte-determinism across repeat and subprocess writes plus the constant ZIP-timestamp assertion; truncated file, flipped byte, wrong magic, identity mismatch → refusal; two entries under one key → conflict refusal; fault injection after temp write / after fsync / before publication → no readable partial entry; concurrent double-write from two subprocesses → exactly one byte-identical entry, both succeed; idempotent republish; path traversal refused; `allow_pickle=False` refuses an object array.
- **Evidence.** Implemented on `codex/phase2e-artifact-store`. The exact public
  package surface adds only `ArtifactStore`; container helpers, decoded state
  and artifact errors remain private. Stage identity retains its canonical
  preimage privately so metadata provenance is re-hashed and bound to the
  requested identity on every decode. The container is canonical uncompressed
  NPZ with fixed order and timestamps, safe ordinal members, eager
  `allow_pickle=False` loading, exact dtypes/shapes and raw float-bit fidelity.
  The store uses descriptor-relative no-follow traversal, per-identity
  interprocess locks, whole-file SHA-256 names, same-directory temp files,
  complete-write and file-fsync, atomic no-replace hard-link publication and
  identity-directory fsync. Corruption and competing content are always loud;
  identical publication is idempotent. Fault injection includes short writes,
  every publication boundary, descriptor ownership and FD-number reuse; real
  subprocess races cover both identical and conflicting writers. The repository
  collects 1105 tests (1102 active in the default gate and 3 performance tests
  deselected by default); unfiltered `./scripts/gate.sh`, Ruff and strict mypy
  are green. The focused identity/container/store/architecture set is 252/252
  green, and independent architecture, test and filesystem-security reviews
  returned APPROVE with no blocker. `data/atomic_write.py` remains byte-exact at
  SHA-256 `85e0691f8b1b5da18c6bb3708bd2bf64bb6f223111c2fa772445af92656ec763`
  with no new caller. The audit remains exactly 3 lines with SHA-256
  `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`;
  D-6 remains
  `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
  and all five generated-view manifest digests remain unchanged. Slice 2E's new
  tests use only synthetic values and pytest-owned temporary directories; the
  existing read-only full-gate TRAIN smoke ran and left external state
  unchanged. No persistent production cache entry was created.

### 2F — MaterializationPolicy + composition — **COMPLETE (2026-08-12)**

- **Objective.** End to end: view + providers + policy → `FeatureFrame`, with ADR-0007 semantics exactly.
- **Files.** new `artifacts/materialization.py`; `experiments/features.py` extended to the second and final public symbol.
- **Tests.** hit/miss/hit bitwise identical; same content hash across miss-then-hit. **Invalidation matrix:** Git commit, dirty flag, diff digest, provider version, a param, `WindowSpec`, `eval_end`, a source `manifest_sha256` — each turns a hit into a miss. **Non-invalidation:** pid, hostname, cwd, wall time. `CACHE_IF_EXPENSIVE` writes nothing when all hints are CHEAP and persists when any is EXPENSIVE. `EPHEMERAL` provably never touches the store (raising store double). `PERSIST_ON_FAILURE` writes nothing. Architecture: no `time`/`perf_counter`/`monotonic` reachable from the policy decision path; no write path from a `MarketFrame`/`FeatureRequest` to the store on any failure branch.
- **Evidence.** Implemented on `codex/phase2f-materialization`. The package adds
  only the exact four-value `MaterializationPolicy`; `experiments.features`
  adds `materialize_features` as its second and final public entry point.
  Identity, cost and execution consume one detached ordered provider sequence;
  a persistent miss revalidates every declared semantic field after `get` and
  executes the refreshed equivalent plan, so callback-driven mutation refuses
  before compute or `put`. Hits validate exact evaluation keys, axis, ordered
  sources, columns and canonical origins before private validated restoration.
  Every failure path is write-free; `PERSIST_ON_FAILURE` is V1-inert and carries
  only scalar identity/provider provenance; all other policies preserve the
  original exception chain. The 41 materialization tests cover real-store
  miss/hit raw-bit parity, policy/failure matrices, executable-state TOCTOU,
  every required invalidation, ambient-state non-invalidation and subprocess
  reuse. The repository collects 1152 tests (1149 active by default and 3
  performance tests deselected); the full gate, Ruff and strict mypy are green.
  Independent contract, runtime and security reviews returned APPROVE with no
  blocker. Audit/D-6/view-manifest evidence is unchanged and no persistent
  production cache entry was created.

### CP-7 — **GREEN (2026-08-12)** — engineering verification gate: *"the cache cannot lie"*

Independent identity recomputation and the full invalidation/exclusion matrix
passed; truncated, flipped, hash-mismatched and identity-mismatched containers
remain loud; store fault and subprocess race tests prove whole immutable files;
the policy decision path is clock-free; no failure path reaches encoding or
publication; and `data/atomic_write.py` remains byte-exact with its sole
pre-existing caller. The post-lookup mutation attack additionally proved that
one semantic provider identity cannot silently execute another declaration.
No owner decision was required. Slice 2G was cleared and is now complete.

### 2G — Determinism verification — **COMPLETE (2026-08-12)**

- **Files.** new `artifacts/determinism.py` (`verify_determinism`, structural comparison, `DeterminismReport`).
- **Tests.** **Poisoned cache** — pre-publish a deliberately wrong artifact under the correct key; normal mode returns the wrong values (proving the cache is genuinely consulted); verification returns correct values and never observes the entry. Raising store double. Cache inventory byte-identical before and after. A deliberately nondeterministic provider is detected. Subprocess recomputation with differing `PYTHONHASHSEED` agrees.
- **Evidence.** Implemented on `codex/phase2g-determinism` with a five-scalar,
  frozen `DeterminismReport` and one explicit verifier. The trusted
  recomputation capability is invoked exactly twice with forced `EPHEMERAL`;
  each exact `FeatureFrame` is bound to the same `StageIdentity`, encoded as a
  canonical artifact, hashed over its complete bytes and compared directly.
  Structural equality separately covers exact keys, axis, ordered
  sources/schema, dtypes/shapes, NaN positions and canonical origins; finite
  value bits, signed zero and NaN payload bits remain in the exact payload
  comparison. The verifier imports no store, data, experiment, filesystem,
  clock or RNG authority and adds no tolerance vocabulary.

  The poisoned-cache test publishes a valid same-identity artifact containing
  deliberately wrong values; normal `PERSIST` returns those wrong bits with
  zero provider calls. Verification then recomputes the hand-known correct
  bits twice while the same store wrapper raises on any `get` or `put`; both
  hashes equal the independently encoded correct artifact, differ from the
  poison hash, and the entire path-and-byte inventory remains unchanged. A
  deliberately alternating, non-random provider yields a negative verdict, signed-zero
  and NaN-payload controls refuse tolerant equality, and two fresh processes
  with different working directories and `PYTHONHASHSEED` values return the
  same positive report. The repository collects 1182 tests (1179 active by
  default and 3 performance tests deselected); the full gate, Ruff and strict
  mypy are green. Focused determinism/architecture qualification is 154/154
  green, and independent contract, runtime-test and production-security
  reviews returned APPROVE with no blocker. External integrity evidence is
  unchanged and no production cache entry was created.

### 2H — Performance qualification, documentation, closeout — **COMPLETE (2026-08-12)**

- **Files.** new `tests/performance/test_feature_budgets.py` (marked `performance`); this document's measured-results section; §11 checklist; `PROJECT_STATE.md`, `V1_ROADMAP.md`.
- **Excluded.** Any optimization, profiling framework, gated access or
  parallelism; any SLA change not explicitly owner-approved from measured
  qualification evidence.
- **Evidence.** The final clean qualification ran at
  `61d4446dbb5aef1e50895db359a4665f1fcc8033`. The verified hit passed at
  **107.817 ms median** against the owner-approved 150 ms target; the full
  reference pass passed at **94.530 ms median** against 5 s. All mandatory
  verification remained in-path. The artifact is 45,268,047 bytes with
  identity key
  `604e761adc826c0d416981e003faa58d3489c7cedfe20eb0888f3b1c33a9eeeb`
  and content SHA-256
  `4d72ca555b7dcdadeffcfb6fc72f2b9a61fb45e7e74c2e0f8741b312a42c834b`.
  Audit, D-6 and all five view-manifest digests remained exact.

---

## 8. Checkpoints

Engineering verification gates, not owner-approval stops. If a checkpoint is
green, tests pass, the work stays inside ADR-0021 and no genuine owner decision
is required, implementation continues.

**CP-6 (after 2C).** Attack: can any provider observe a key past `eval_end`, an
undeclared column, or the reserved namespace? Is the mask applied by the platform
*after* `compute`? Is the sentinel exact and genuinely parametrised over the live
registry? Does rig 2 catch under-declared reach, and rig 3 catch `BREAK`
crossing? Does the seam match a single contiguous axis? Blocks: any control not
caught, any tolerance or skip, any undeclared reach, non-dense output, any
weakened guard.

**CP-7 (after 2F).** Attack: recompute identity keys independently. Does the full
invalidation matrix hold, and does anything irrelevant leak in? Can a truncated,
flipped or mismatched artifact ever be served? Can concurrent writers produce a
mixed file? Is the policy path provably clock-free? Can any market value reach
the cache on the failure path? Is `data/atomic_write.py` byte-unchanged and
without new callers? Blocks: any served corrupt artifact, any silent overwrite,
any clock in the policy path, any market value in a failure artifact.

**CP-8 — GREEN (2026-08-12, reviewed candidate `09ce941`).** The independent
review re-ran the poisoned-cache test, reproduced the performance numbers,
confirmed the 3-record audit and dataset/view fingerprints, verified that the
Phase-3 legacy cluster stayed intact and guards were extended rather than
weakened, and checked §11 item by item with named evidence.

Independent contract/checklist, runtime and store/external-state reviewers all
returned **APPROVE / no blockers** at clean, pushed commit
`09ce94193266f3be5dcb221f5ba480ba080545a9`. The poisoned-cache suite passed
28/28; the full default gate passed 1,181 active tests (1,185 collected, 4
performance tests deselected), architecture passed 128/128, Ruff passed and
strict mypy passed 91 files. Exact-head performance reproduction passed at
112.814 ms median verified hit and 94.226 ms median full pass. Independent
stage-document hashing matched identity
`9e6e9e273d835d144dba6053dcd212a42639f92f22de7109f4f0d8099d013b70`;
the 45,268,047-byte artifact's whole-file SHA-256 matched
`c2155a1e44a67f752161a2f40f0998c1f2683d355001eb4440474d74d1922422`.
The audit remained 3 records / `deeb73ed…01f5`, D-6 remained
`5125b9ff…b180`, all five manifests remained exact, every cache final remained
whole and immutable, and no temporary or conflicting entry remained. No prior
guard was weakened, no Phase-3 vocabulary leaked into Phase-2 production, and
the deferred Phase-3 carriers remain intact.

---

## 9. Testing matrix

| # | Area | What it must prove |
|---|---|---|
| T1 | Feature contract validation | Ordered/unique/non-empty names; declared vs emitted key set; float64 dtype; exact length; ±inf refused; non-canonical `params` refused |
| T2 | Alignment | Index ≡ `eval_frame.keys`, dense, strictly increasing; column order deterministic; provider-shuffle bitwise identical |
| T3 | Warm-up | Exactly `max_prior_rows` leading NaNs; zero when 0; platform mask overrides a provider emitting inside warm-up |
| T4 | Gap semantics | `CROSS` computes across a gap; `BREAK` yields exactly `max_prior_rows` NaNs after each discontinuity; missing `nominal_interval` refused; never inferred |
| T5 | Context boundaries | No key > `eval_end` reachable; short context refuses unless explicitly accepted; boundary inside a gap is legal |
| T6 | Multi-segment seam | Value at first evaluation key of segment 2 equals the single-contiguous-axis value; malformed segment sequences refused |
| T7 | Registry | Duplicate registration refused; every registered provider zero-arg constructible; registry names ≡ rig coverage set |
| T8 | Name collisions | Two providers on one name refuse **before** computation, naming both |
| T9 | Reserved namespace | Feature name in the outcome/target namespace refused; no outcome concept importable from `features` |
| T10 | Future perturbation | Bitwise equality at every evaluation index ≤ cutoff, multiple cutoffs including post-gap, every registered provider |
| T11 | Adversarial providers | All six negative controls caught; none registered in production |
| T12 | Backward reach | Perturbing before `keys[i − max_prior_rows]` never changes value `i` |
| T13 | BREAK isolation | Perturbing pre-gap rows never changes post-gap output |
| T14 | Identity stability | Same inputs → same key across processes and hash seeds; 64 lowercase hex |
| T15 | Identity sensitivity | Each §4.1 input individually changes the key |
| T16 | Identity insensitivity | pid, hostname, cwd, wall time, `ViewRole`, path, `params` order do not |
| T17 | Context-derivation invariant | Same identity key ⇒ same resolved `context_start` |
| T18 | Cache hit/miss/invalidation | miss→publish→hit bitwise identical; every invalidation input flips hit to miss |
| T19 | Platform-code invalidation | Different commit, dirty flag, diff digest each invalidate (roadmap acceptance criterion) |
| T20 | Artifact corruption | Truncation, byte flip, wrong magic, identity mismatch, hash mismatch → refusal, never a served value or silent miss |
| T21 | Incomplete writes / crash | Fault injection at every stage → no readable entry under a final name; stray temps inert |
| T22 | Concurrency / atomic race | Two concurrent publishers → exactly one byte-identical entry, both succeed; differing content under one key → loud conflict |
| T23 | Container determinism | Repeat and subprocess writes byte-identical; ZIP member `date_time` constant; `allow_pickle=False` refuses object arrays |
| T24 | Determinism cache bypass | Poisoned cache; raising store double; cache inventory unchanged |
| T25 | Exact structural reproducibility | Two independent recomputations agree on column set+order, dtypes, keys, payload bytes, NaN positions, origins, content hash |
| T26 | Nondeterminism detection | A deliberately nondeterministic provider is detected |
| T27 | Performance | Verified cache hit < 150 ms; full TRAIN reference pass < 5 s; median of 5 after one discarded warm-up |
| T28 | Architecture boundaries | `features` and `artifacts` import only `core`; `experiments` is the sole composition point; exact public surfaces; no dynamic public module attributes |
| T29 | No table-library leakage | No pyarrow/pandas/polars/duckdb in any new public annotation or export |
| T30 | No OutcomeSpec/target in features | No forward-reading vocabulary reachable from `features`/`artifacts` |
| T31 | No model/policy/simulator leakage | No `ResearchModel`, `DecisionPolicy`, `Direction`, simulation or trading vocabulary in Phase-2 modules |
| T32 | No forbidden data access | `features`/`artifacts` open no Parquet, import no loader/adapter, perform no directory scan, hold no `AccessGrant`; `MarketFrameLoader.load` remains the sole decoder and `open_bounded_view` the sole composition route |
| T33 | Legacy carrier scheduling | Phase-3 deferred cluster unreferenced from `data`, `features` and `artifacts`; guard set strictly extended |
| T34 | Failure-path safety | No policy branch writes any array payload or market value on failure |
| T35 | Clock-free cost decision | No `time`/`perf_counter`/`monotonic` reachable from the materialization decision path |
| T36 | No civil time / UTC | Existing civil-time guards extended to the new modules |
| T37 | Real-data smoke (TRAIN only) | One reference pass over published TRAIN yields expected row count, expected NaN counts and a stable content hash; audit log unchanged |

---

## 10. Performance qualification plan

**Budgets (owner-adjusted 2026-08-12):** verified cache hit **< 150 ms**;
full-view reference feature pass **< 5 s**.

The 150 ms budget replaces the original 100 ms target. The change is based on
the clean-commit qualification at `61ab858fde7ec3a2275dceb0a462c88d60e89aa8`,
which measured a **124.422036 ms** median verified hit for an immutable
**45,268,047-byte** artifact. The artifact SHA-256 was
`4b9eb7bce5140129c0e8aba6953be9a59507e70ec40f8869eab2c03ab7f984e2`.
All five timed hits recomputed the whole-file SHA-256 and performed the same
container, identity and frame checks; none of those checks may be removed or
relaxed. Per the owner's decision, no optimization is to be performed merely
to meet the superseded 100 ms target.

**View: published TRAIN `XAUUSD_S_M1_TRAIN_V1` (`30ca7b4d…f374e`), 1,414,538
rows.** TRAIN is `NORMAL`-classified: no ceremony, no unlock, **no audit event**,
no owner authorization. "Full view" means the full TRAIN view. **No SNAPSHOT,
OOS, LOCKBOX, RAW or CURATED access is required or permitted**; the expected
audit delta for all of Phase 2 is **zero** and the log must still hold exactly 3
records at CP-8.

**Timed regions.** Both begin with everything Phase 1 already qualified already
done — split policy read, registry discovered, grant issued, `BoundedView`
opened, `PlatformIdentity` resolved once per process.

| Region | Includes | Excludes |
|---|---|---|
| Cache hit | identity-key construction · path resolution · file read · **mandatory SHA-256 verification** · header parse · array reconstruction · frame assembly | process start, imports, registry discovery, grant issue, view opening, platform-identity resolution |
| Full pass (miss) | `from_segments` (concatenation + validation) · plan validation · all three providers' `compute` · mask application · frame assembly | durable publication (**reported separately**), everything above |

Also reported, unbudgeted: `load + full pass`, and `full pass + persist`.

**Method** (mirrors the owner-approved Phase-1 §8): 1 discarded warm-up + **5
timed repetitions**; report **median and min**; a budget is missed only if the
**median** exceeds it. Page cache warm; no cold measurement (sudo unavailable and
neither budget requires it). Recorded: per-repetition wall time, median, min,
peak RSS, artifact bytes, content hash, identity key, row count, per-column NaN
counts, Git identity.

**Evidence-only, no new SLA.** Peak RSS and artifact size carry **no budget**,
exactly as Phase 1 treated `retained_payload_bytes`.

**Measured risk disposition.** The scratchpad estimate understated the real
cache-root path: the first official run measured 124.4 ms median. The owner
therefore set the evidence-based 150 ms budget above. Verification stayed
inside the timed region and is unchanged. Serving an unverified artifact to hit
a number remains forbidden.

**GapIndex obligation.** Phase-1 §12 deferred GapIndex persistence "until Phase 2
profiling shows repeated recomputation actually matters". 2H must measure
discontinuity-detection cost inside a full `BREAK` pass and either confirm and
close the deferral or escalate.

### Measured qualification results — clean commit `61d4446` (2026-08-12)

The marked qualification passed against the published NORMAL TRAIN view with
1 discarded warm-up plus 5 timed repetitions per region:

| Region | Five samples (seconds) | Median | Minimum | Result |
|---|---|---:|---:|---|
| Full reference pass | 0.094530, 0.094919, 0.094705, 0.094425, 0.093454 | **0.094530 s** | 0.093454 s | PASS `< 5 s` |
| Verified cache hit | 0.106839, 0.109011, 0.107817, 0.106545, 0.112208 | **0.107817 s** | 0.106545 s | PASS `< 0.150 s` |
| Load + full pass | 0.183547, 0.177174, 0.178687, 0.179515, 0.181923 | 0.179515 s | 0.177174 s | evidence only |
| Full pass + persist | 0.277223, 0.277446, 0.279099, 0.279651, 0.280951 | 0.279099 s | 0.277223 s | evidence only |
| Full `BREAK` pass | 0.121147, 0.121936, 0.121824, 0.121619, 0.122211 | 0.121824 s | 0.121147 s | evidence only |
| `GapIndex.from_frame` | 0.001783, 0.001761, 0.001667, 0.001776, 0.001833 | **0.001776 s** | 0.001667 s | evidence only |

- Rows: **1,414,538**. Provider order: `log_return_1`,
  `rolling_mean_close`, `bar_range`.
- CROSS NaNs: `1`, `19`, `0`; BREAK NaNs: `1`, `23,386`, `0`.
- Discontinuities: **1,314**. At ~1.8 ms, standalone GapIndex construction is
  negligible relative to the 121.8 ms full BREAK pass; the Phase-1 persistence
  deferral is therefore **confirmed and closed — keep GapIndex ephemeral**.
- Artifact: **45,268,047 bytes**; identity
  `604e761adc826c0d416981e003faa58d3489c7cedfe20eb0888f3b1c33a9eeeb`;
  SHA-256
  `4d72ca555b7dcdadeffcfb6fc72f2b9a61fb45e7e74c2e0f8741b312a42c834b`.
- Pre-measure RSS: **96,768,000 bytes**; peak RSS: **534,773,760 bytes**.
  Both are evidence only, with no invented SLA.
- Audit: exactly 3 records before and after; SHA-256 remained
  `deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5`.
  D-6 remained `5125b9ffd23511fcbf8b41ed44eb1f29b28966f16343fb13ad34bea88d78b180`;
  all five generated-view manifest digests remained unchanged.
- The earlier `61ab858` artifact and the passing `61d4446` artifact remain
  separate, whole and immutable as required. No cache entry was deleted or
  overwritten.

---

## 11. Phase-2 completion checklist

Phase 2 is COMPLETE only when every item is green and independently verified at a
named commit with named evidence.

**Contracts and execution**
- [x] All §6 contracts implemented; ADR-0021 ratified and indexed.
- [x] Per-point `FeatureProvider` deleted with **no shim, adapter or compatibility layer**; `core/domain.py` byte-unchanged.
- [x] `core/frames.py`, `data/*` and the manifest schema behaviourally unchanged.
- [x] Output dense, aligned exactly to `eval_frame.keys`, deterministically ordered, float64, NaN for unavailable, ±inf refused.
- [x] Warm-up and gap invalidity applied by the **platform**, provably overriding a provider that emits inside them.
- [x] Declared reach and gap policy exposed per column, sufficient for Phase-3 purge derivation.
- [x] Providers structurally cannot read undeclared columns, keys past `eval_end`, another provider's output, or the reserved namespace.

**Leakage (release-blocking)**
- [x] Future-perturbation rig green for every registered provider, multiple cutoffs including post-gap, **exact** comparison, no skip/xfail/tolerance.
- [x] Backward-reach rig green; no provider under-declares.
- [x] BREAK-isolation rig green; post-gap output provably independent of pre-gap values.
- [x] All six adversarial controls caught; none registered in production.
- [x] Registry-coverage meta-test proves an uncovered provider fails the gate.

**Identity, cache, materialization**
- [x] Identity key independently recomputed by the reviewer and matching.
- [x] Full invalidation matrix green, including platform commit, dirty flag and diff digest.
- [x] Full non-invalidation matrix green.
- [x] `content_hash == sha256(file bytes)`; container byte-deterministic across processes; ZIP-timestamp regression test present.
- [x] Entries immutable; differing content under one identity key is a loud refusal; identical republish is an idempotent success.
- [x] Atomic publication proven under fault injection at every stage.
- [x] Concurrent double-write yields exactly one byte-identical entry, both succeeding.
- [x] Corruption always refuses and never serves a value or degrades to a silent miss.
- [x] `cache_if_expensive` consults only the declared hint; **no clock in the decision path** (architecture-tested).
- [x] `PERSIST_ON_FAILURE` writes nothing; no policy branch writes any market value.
- [x] `data/atomic_write.py` byte-unchanged and without new callers.

**Determinism**
- [x] Verification explicit and opt-in; normal requests compute **once**.
- [x] Verification **never reads and never writes** the cache — proven by poisoned cache, raising double, and unchanged inventory.
- [x] Independent recomputations agree exactly on all structural fields and content hash.
- [x] A deliberately nondeterministic provider is detected.

**Performance and evidence**
- [x] Verified cache hit **< 150 ms** median-of-5 on published TRAIN, with all SHA-256/container/identity/frame checks inside the timed region.
- [x] Full TRAIN reference pass **< 5 s** median-of-5.
- [x] Memory and artifact size recorded as evidence only, no new SLA.
- [x] GapIndex recomputation cost measured; Phase-1 §12 deferral confirmed and closed, or escalated.

**Boundaries and integrity**
- [x] Architecture tests **extended, never weakened**; `ALLOWED["artifacts"]` added and no other budget widened.
- [x] `features` and `artifacts` import only `core`; `experiments` holds exactly one bounded composition module with a guarded exact surface.
- [x] No table-library type in any new public annotation or export.
- [x] No civil-time or UTC machinery on the feature path.
- [x] No Phase-3 concept (OutcomeSpec, targets, folds, purge, fitted state, model output, RunManifest, run id) in Phase-2 code.
- [x] **Zero gated access:** no SNAPSHOT/OOS/LOCKBOX/RAW/CURATED read; audit log still exactly 3 records, byte-identical.
- [x] Dataset D-6 fingerprint and all five view manifest digests unchanged.
- [x] `./scripts/gate.sh` green unfiltered, Ruff and strict mypy clean.
- [x] Documentation duties complete; `PROJECT_STATE.md`, `V1_ROADMAP.md` and this document updated; working tree clean; commits pushed to the private remote.
- [x] **Phase 2 independently reviewed (CP-8) with an explicit verdict.**

---

## 12. Status, gating and non-goals

- **Phase 2 implementation: COMPLETE (2026-08-12). Slices 2A–2H are COMPLETE;
  CP-6, CP-7 and CP-8 are GREEN; the independent whole-phase verdict is
  APPROVE.** Deterministic provider computation,
  release-blocking leakage
  rigs, pure stage identity, the corruption-refusing artifact store, exact
  materialization/cache composition and cache-bypassing determinism
  verification and bounded performance qualification are complete.
- **The pre-2B namespace decision is RESOLVED (owner-approved 2026-08-12):**
  exactly `target` and the case-sensitive prefix `target.` are reserved in both
  provider name declarations and refuse before computation. No broader V1 rule
  exists.
- **Phase 1 §13 is COMPLETE (2026-08-12) and is no longer a blocker.** Item 3 (Hyper-V VM export) is recorded as **completed on 2026-08-12 on the owner's explicit attestation** — host-side storage location as provided by the owner: **under the virtual machine's host files, in a folder named `export`**; **OWNER ATTESTED, not independently visible or verified from this guest VM**. Item 4 (owner review of items 1–3) is **COMPLETE**, with item 1 accepted, item 2 accepted, and item 3 accepted on the owner's attestation. The §13 requirement *"Do not begin Phase 2 until items 1–3 are reviewed by the owner"* is therefore **satisfied and the gate is CLEARED**.
- **ADR-0021 exists and is ACCEPTED**, satisfying OD-1's precondition for Slice 2A.
- **No hypothesis, model, evaluation, OOS or LOCKBOX research use of the
  generated views has occurred.** Slice 2H's only row-derived use is the
  explicitly planned engineering qualification on NORMAL TRAIN.
- **Deferred to Phase 3+ and explicitly not built here:** `RepresentationModel` and fitted state · OutcomeSpec, `TrainingTargetFrame`, target computation · fold generation, purge algorithm, embargo · `ModelOutputFrame`, `IntentFrame`, `DecisionPolicy` · RunManifest, run directories, run states, heartbeats, evaluation inventory, experiment fingerprint · CLI, config schema, resolver, detached execution · SQLite index, cache index, GC, TTL, LRU, disk telemetry · simulator and cost semantics · narrowing platform-code cache identity (ADR-0007: *measure before narrowing*) · GapIndex persistence · native-time-distance and calendar-aware windows · sparse or non-numeric feature columns · a technical-analysis feature library · feature-of-feature composition or any DAG engine · parallel/multiprocess computation · retirement of the Phase-3 legacy carriers.

---

*This document is normative for Phase 2 execution. §13 is resolved
(2026-08-12); Slices 2A–2H are complete; CP-6, CP-7 and CP-8 are green; the pre-2B
namespace decision and the evidence-based 150 ms verified-hit budget are
resolved; Phase 2 is complete.*
