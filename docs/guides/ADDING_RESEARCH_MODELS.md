# Adding a ResearchModel — authoring guide

**Audience:** anyone adding a research model to this platform — a researcher who
knows the method but not the internals, or a coding agent asked to implement one
safely.

**Scope:** this guide is *explanatory*. It creates no new platform rule. If
anything here disagrees with an accepted ADR or with
[`docs/phases/PHASE3_RESEARCH_RUNS.md`](../phases/PHASE3_RESEARCH_RUNS.md),
**the contract documents win** — fix the guide, not the contract. The binding
contract is [ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md), with
[ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md) (fit lifecycle),
[ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md) (generic
output) and
[ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) (the
training-target channel).

**Status of the code this describes:** Phase 3 is COMPLETE. Everything below is
implemented and enforced today. Nothing here is aspirational.

**Companion guides:**
[ADDING_FEATURES.md](ADDING_FEATURES.md) ·
[ADDING_REPRESENTATIONS.md](ADDING_REPRESENTATIONS.md) ·
[ADDING_DECISION_POLICIES.md](ADDING_DECISION_POLICIES.md) ·
[ADDING_OUTCOMES.md](ADDING_OUTCOMES.md)

---

## 0. Where this stage sits

```
market data (authorized, bounded, point-in-time)
    |
    v   FeatureProvider.compute(request)                 -> ADDING_FEATURES.md
FeatureFrame
    |
    v   RepresentationModel.encode(...)          -> ADDING_REPRESENTATIONS.md
RepresentationFrame                       (optional; at most ONE such stage)
    |
    v   ModelInputs(features, representation?)
ResearchModel.fit(view, inputs, training_targets=None) -> FittedPayload   THIS
ResearchModel.infer(view, fitted_state, inputs)        -> ModelOutputFrame GUIDE
    |
    v
ModelOutputFrame
    |
    v   DecisionPolicy.decide(model_output)      -> ADDING_DECISION_POLICIES.md
IntentFrame
```

`OutcomeSpec` touches this stage in exactly one place — the fit-time
`TrainingTargetFrame` channel of §4 and §6. It is **never** a model input in the
`ModelInputs` sense, and `infer` never sees it.

---

## 1. What a ResearchModel is responsible for

A **research model** learns something from a bounded set of feature (and
optionally representation) rows plus, when configured, platform-computed
training targets — and then produces **generic numeric output** for each
evaluation row.

### What enters

| `fit` | `infer` |
|---|---|
| `view: ViewSpan` (a rowless descriptor) | `view: ViewSpan` |
| `inputs: ModelInputs` | `fitted_state: FittedState` |
| `training_targets: TrainingTargetFrame \| None = None` | `inputs: ModelInputs` |

`ModelInputs` is the **complete** input surface: a `FeatureFrame` and an
optional `RepresentationFrame`, aligned to the same keys. Nothing else. No
market columns, no `MarketFrame`, no `FeatureRequest`, no `OutcomeRequest`, no
fold record, no store, no path.

### What it produces

- `fit` returns a **`FittedPayload`** — learned numeric arrays plus JSON-safe
  metadata, and nothing more.
- `infer` returns a **`ModelOutputFrame`** — generic named numeric columns, on a
  dense or **sparse** subset of the evaluation keys.

### What it owns

**The method, and what the method learned.** That is the whole job.

### What the platform owns instead

Do not re-implement any of this:

| The platform handles | What that means for you |
|---|---|
| **Loading and authorization** | You never see a file, a grant, a dataset path or a view manifest. |
| **Which rows you may fit on** | The orchestrator subsets rows to the fold's *effective fit set* — fit window minus purge minus embargo — and refuses a `fit` whose keys differ (`_FitIsolation._validate_fit`). |
| **Targets** | The config names an `OutcomeSpec`; the orchestrator computes the `TrainingTargetFrame` and passes it in. You never compute a forward value. |
| **Fitted-state identity** | You return payload; the composition root binds the complete twelve-field identity document and mints `FittedState` ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 3). |
| **Serialization and caching** | Versioned NPZ + JSON, never pickle; `cost_hint` selects whether state is persisted. |
| **Decisions** | Thresholds, sizing, direction and abstention are `DecisionPolicy`'s job, not yours. |
| **Evaluation** | Metrics are an `Evaluator`'s job, over your persisted output. |
| **Determinism verification** | The verifier recomputes with caching forced off and compares canonical bytes. |

> **Rule of thumb.** If a name in your output would tell a trader what to do,
> you have crossed into `DecisionPolicy`. If a number in your `fit` came from a
> bar after the decision row and did not arrive through `training_targets`, you
> have written a leakage bug.

---

## 2. What a ResearchModel must NOT do

- **No forward reads, ever, anywhere.** Output at evaluation row *i* must not
  depend on any row after *i*. Enforced by the forward rig in
  [`tests/test_model_leakage.py`](../../tests/test_model_leakage.py).
- **No dependence on any other evaluation row, in either direction.** `infer` is
  **row-local** ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §5):
  row *i*'s output depends only on row *i*'s declared inputs and the fixed
  `FittedState`. No evaluation-range normalization, no cumulative state, no
  online update. Enforced by the row-locality rig.
- **No hidden fit-time data access.** A model performing its own forward read
  inside `fit` is a leakage bug, not a shortcut
  ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)). The
  sentinel control `SelfForwardReadFitModelControl` exists to prove the boundary
  holds.
- **Targets never reach inference.** `infer` has no `training_targets`
  parameter, by design. Do not stash targets in the payload in order to consult
  them at inference time as if they were features — the arrays you learned are
  legitimate; a per-evaluation-row target lookup is not.
- **No trading vocabulary in output.** Column names whose lowercase form is one
  of `action`, `buy`, `direction`, `position`, `sell`, `side`, `signal` are
  refused at frame minting ([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)).
- **No reserved outcome namespace in output.** `target` and names beginning
  `target.` are refused.
- **No decision logic.** Do not emit "+1/-1", do not apply a trade threshold, do
  not size a position. Emit the statistic; let a policy decide.
- **No direct data access.** No `open()`, no loader, no `AccessGrant`, no
  `DatasetRegistry`, no `MarketFrame`, no market column, no `OutcomeRequest`.
  `models` may import **only** `core`
  (`test_layer_respects_its_dependency_budget`).
- **No cross-plugin imports.** Not from `features`, `representations`,
  `outcomes`, `decisions`, `evaluation`, `experiments`, `artifacts` or `data`.
- **No identity authority.** The names `ArtifactStore`, `MaterializationPolicy`,
  `PlatformIdentity`, `SplitPolicyIdentity`, `StageIdentity`,
  `_FittedStateIdentity`, `_bind_fitted_state`, `_make_fitted_state`,
  `dependency_lock_sha256` and `fit_isolation` are on an explicit forbidden list
  for this layer.
- **No filesystem, registry or run-directory access** — not in `fit`, not in
  `infer`, not at import time.
- **No hidden nondeterminism.** No environment variables, wall-clock reads,
  local config files, module-level mutable state, or unseeded randomness.
- **No undeclared parameter or dependency.** Anything that can change a returned
  number must be visible in `params` — see §5.
- **No widening of the abstraction to fit one model.** If your method genuinely
  cannot be written as row-local inference over declared inputs plus fixed
  state, **stop and raise it** (§11): that is precisely
  [ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §6's reconsideration
  trigger, and it needs an ADR, not a workaround.

---

## 3. Exact production contract

`ResearchModel` in
[`core/interfaces.py`](../../src/trading_research/core/interfaces.py) is a
`typing.Protocol`; structural conformance is enough. An architecture test
asserts each reference model's public surface is **exactly** these ten names.

```python
model_id: str                                   # stable identity of this implementation
version: int                                    # positive; identity-bearing
params: Mapping[str, ParamValue]                # every semantic knob, canonically encoded
required_feature_names: tuple[str, ...]         # ordered, unique, NON-EMPTY
required_representation_names: tuple[str, ...]  # ordered, unique; MAY be empty
output_column_names: tuple[str, ...]            # ordered, unique, NON-EMPTY
cost_hint: CostHint                             # CHEAP | EXPENSIVE — caching hint only
comparison_tolerance: ToleranceSpec | None      # None means exact; the default

def fit(self, view: ViewSpan, inputs: ModelInputs,
        training_targets: TrainingTargetFrame | None = None) -> FittedPayload: ...
def infer(self, view: ViewSpan, fitted_state: FittedState,
          inputs: ModelInputs) -> ModelOutputFrame: ...
```

The declaration is read and validated once by `_snapshot_model` in
[`models/execution.py`](../../src/trading_research/models/execution.py) and
re-read after a cache lookup by `_revalidate_model`; a declaration that changes
between the two reads is refused.

### `model_id`

The stable string identity. It is the registry name, it appears in every output
column's `StageOrigin`, and it is hashed into fitted-state identity.
**Enforced:** exact non-empty `str`.

### `version`

**Enforced:** exact Python `int`, strictly positive. Increment when the same
declaration should now produce different numbers.

### `params`

Every semantic knob. **Allowed value types** (`ParamValue`): `str`, `int`,
`float`, `bool`, `None`, nested `tuple`s of those. Canonical, type-preserving
encoding: key order is irrelevant (sorted), `4` and `4.0` are distinct, `True`
and `1` are distinct, floats via `float.hex()`, `float('nan')` refused.

### `required_feature_names`

The ordered feature columns you consume. **Enforced:** exact non-empty `tuple`
of unique non-empty `str`.

> **This is an equality, not a subset.** `_validate_model_inputs` refuses unless
> `inputs.features.column_names == required_feature_names` — same names, same
> order. In practice the experiment's configured feature set must be exactly
> this tuple; see §7.

### `required_representation_names`

The ordered representation columns you consume, or `()` if none. **Enforced:**
exact `tuple` (possibly empty) of unique non-empty `str`. The platform then
enforces both directions:

- non-empty → a `RepresentationFrame` **must** be present and its
  `column_names` must equal this tuple exactly;
- empty → a `RepresentationFrame` must **not** be present at all.

### `output_column_names`

The ordered generic output columns. **Enforced:** exact non-empty `tuple` of
unique non-empty `str`; the frame you return must carry exactly these names in
exactly this order. Reserved and banned vocabulary (§5, §2) is refused at
minting.

### `cost_hint`

`CostHint.CHEAP` or `CostHint.EXPENSIVE`. It answers one question: under the
default `CACHE_IF_EXPENSIVE` materialization policy, should the **fitted state**
be written to the artifact store? It is **not** part of the identity key. No
wall clock is ever consulted
([ADR-0007](../adr/ADR-0007-materialization-policy.md) Amendment 2). The
reference nearest-library model declares `EXPENSIVE`; the two trivial ones
declare `CHEAP`. **Enforced:** exact `CostHint` member.

### `comparison_tolerance`

`None` (exact — the default requiring no declaration) or a frozen
`ToleranceSpec(atol, rtol, reason)` with finite non-negative bounds and a
non-empty reason. It is **identity-bearing**: hashed into fitted-state identity,
so loosening one changes identity and is visible in review, and it can never be
a CLI or verifier flag
([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §8). All V1 reference
models declare `None`; start there.

### `fit(view, inputs, training_targets=None) -> FittedPayload`

`view` is a frozen `ViewSpan` — `source_identities`, `unit`, `clock_domain`,
`context_start`, `eval_start`, `eval_end`. It is a **descriptor, not a data
handle**. The reference models ignore it.

`inputs.features` and `inputs.representation` are read with `.column(name)`
(fresh read-only descriptors) and `.keys`.

`training_targets` is `None` unless the config names a training `OutcomeSpec`.
When present it is an exact `TrainingTargetFrame` whose keys **exactly equal**
the fit `FeatureFrame`'s keys — the platform validates that before calling you.
Read target columns with `.column("target.<name>")`.

You must return an exact `FittedPayload`:

- `arrays`: `str` → non-object NumPy arrays with metadata-free real numeric
  dtypes (`bool`/`int`/`uint`/`float`); detached and read-only after
  construction; names canonically sorted.
- `metadata`: `Mapping[str, ParamValue]`, validated with the same canonical
  encoder as `params`.

`FittedPayload` carries **no identity, view, split policy, purge record,
platform identity or dependency identity** — it cannot, by construction.

### `infer(view, fitted_state, inputs) -> ModelOutputFrame`

`fitted_state.payload` is the `FittedPayload` from `fit` (restored from the
store if cached). `fitted_state.identity_key()` is readable but must never be
computed, asserted on, or embedded in output.

Mint the result with the platform's private helper — the only route available
to a plugin, since `ModelOutputFrame.__init__` raises:

```python
from trading_research.core.research_frames import _make_model_output_frame

_make_model_output_frame(view=view, keys=..., columns={...}, origins={...})
```

Output rules, all enforced:

| Rule | Detail |
|---|---|
| **Keys** | strictly increasing, unique `int64`; inside `[view.eval_start, view.eval_end]`; a **subset of `inputs.features.keys`**; may be a strict subset, and may be empty |
| **Columns** | exactly `output_column_names`, in order |
| **Dtype** | any real (non-complex) numeric dtype — this stage is *not* float64-only |
| **`±inf`** | refused |
| **`NaN`** | means "unavailable / no value at this key" |
| **Origins** | one `StageOrigin(model_id, version, params)` per column, compared against the snapshot |
| **Vocabulary** | banned trading words and the reserved `target` namespace refused |

> **Sparsity is first-class** ([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)
> amendment). A model that speaks on 400 events must not emit 2.3 M abstention
> rows. **Absence of a row is not a dense `NaN` row**: absence means "no
> statement at this key"; `NaN` means "explicitly unavailable". Preserve the
> distinction.

---

## 4. Lifecycle

Per fold, inside `_execute_fold` in
[`experiments/orchestration.py`](../../src/trading_research/experiments/orchestration.py):

```
1.  fit_features    = feature rows of the fold's EFFECTIVE FIT SET
    eval_features   = feature rows of the fold's evaluation range
2.  (optional) representation fit + encode on both ranges
3.  training_targets = orchestrator computes a TrainingTargetFrame from the
                       config-declared training OutcomeSpec over the fold's
                       training view, restricted to the fit keys        (or None)
4.  model_state = fit_model(plugin, view=fold.training_view,
                            inputs=ModelInputs(fit_features, fit_representation),
                            training_targets=...)                -> FittedState
5.  model_output = infer(view=fold.evaluation_view,
                         fitted_state=model_state,
                         inputs=ModelInputs(eval_features, eval_representation))
6.  every configured DecisionPolicy consumes THAT ONE frame — zero re-inference
```

Points that matter:

- **`fit` runs once per fold**, on training rows only. Its result is bound to a
  complete identity and materialized under `CACHE_IF_EXPENSIVE`; an identical
  identity restores the stored state instead of refitting. Refit-per-run is the
  V1 default ([ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md)).
- **`infer` runs exactly once per fold.** The orchestrator asserts counters:
  one inference call, zero re-inference calls, one call per configured policy.
- **A no-op `fit` is a first-class implementation**, not a workaround. A
  stateless model returns `FittedPayload(arrays={}, metadata={...})` and — as
  the reference `StatelessRuleModel` does — may explicitly refuse a
  `training_targets` argument it does not use.
- **The output frame is persisted** into the run directory and enters
  determinism comparison under your declared tolerance.
- **`fit` and `infer` see different frames.** Never assume the evaluation frame
  resembles the fit frame in length, distribution or key spacing.

### `TrainingTargetFrame`, precisely

`TrainingTargetFrame` is the **only** type `fit` accepts as targets, and it is
minted only by the platform's outcome path
([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 1 §3). It
carries:

`keys`, `unit`, `clock_domain`, `num_rows`, `source_identities`,
`column_names` (all in the reserved `target.` namespace), `schema` (all
`float64`), `column(name)`, `origin(name) -> StageOrigin`.

Validate what you require and refuse what you do not understand — the reference
supervised models check `frame.column_names == ("target.forward_log_return",)`
and raise otherwise. Targets may be `NaN` where the horizon was incomplete; mask
them (`np.isfinite`) rather than propagating.

### `FittedPayload` → `FittedState`

```
declared inputs -> plugin.fit(...) -> FittedPayload
complete caller-supplied identity + FittedPayload -> orchestrator -> FittedState
```

A plugin can construct `FittedPayload` but cannot construct `FittedState` and
does not receive the platform-only values needed to identify one. This is
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 3, and an
architecture test asserts `fit`'s return annotation names `FittedPayload` and
not `FittedState`.

---

## 5. Identity, parameters, and reproducibility

### What you declare

Only `model_id`, `version`, canonical `params`, `comparison_tolerance`, and —
indirectly — the origins of the feature and representation columns you declared.

### What the platform adds

The identity document assembled by `_FittedStateIdentity` in
[`artifacts/fitted.py`](../../src/trading_research/artifacts/fitted.py) has
twelve fields: `format`, `format_version`, `plugin`, `features`,
`representation`, `training_view`, `split_policy`, `training_outcome`,
`fit_isolation`, `platform`, `dependencies`, `tolerance` — discharging the
eleven required components listed in
[PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) §2.4.

Two consequences worth internalising:

- **`training_outcome` pins the target *definition*, not the target values.**
  It records the OutcomeSpec's id, version, canonical params, the resulting key
  range (first key, last key, row count, SHA-256 of the key bytes) and the
  alignment params. Two states trained on the same bars under different target
  definitions can never share an identity — that is
  [ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md)'s amendment made
  mechanical.
- **`fit_isolation` pins the applied purge and embargo** and the effective fit
  size, so a state fitted under a weakened purge is a different artifact.

**Do not duplicate any of it inside your plugin.** You cannot reach the split
policy, the purge record, the platform commit or the dependency digest, and
naming those symbols in this layer fails an architecture test.

### The one hard rule

> **Every semantic input that can change your output must be reachable from your
> declaration — in practice, from `params`.**

Two differently-configured instances with the same declaration receive the
**same fitted-state identity**, and the second is served the first one's stored
state. That is a silent, durable wrong answer.

### Correct

```python
@property
def params(self):
    return {"neighbours": self._neighbours, "metric": self._metric}
```

A stochastic model declares its seed as an ordinary identity-bearing parameter
(`params["seed"]`), which is what
[ADR-0007](../adr/ADR-0007-materialization-policy.md) requires and needs no
extra machinery. Random behaviour must be seeded through configuration, never ad
hoc ([CLAUDE.md](../../CLAUDE.md)). No V1 reference model is stochastic.

### Incorrect — invisible to identity

```python
neighbours = int(os.environ["K"])            # environment dependence
if datetime.now().weekday() < 5: ...         # wall clock
cfg = json.load(open("mymodel.json"))        # local config file
_CALLS.append(1); bias = len(_CALLS)         # mutable module-global state
noise = np.random.randn(n)                   # unseeded randomness
```

The first three are also blocked outright by the no-I/O and no-clock rules. The
last two are the dangerous ones, because they *look* like ordinary code.

---

## 6. Time and information boundaries

- **The axis is `NativeTimeKey`**: `int64` values plus a `TimeUnit` and an
  opaque `ClockDomain`. The dataset clock domain is **broker wall-clock, not
  UTC** ([ADR-0002](../adr/ADR-0002-native-time-model.md)). Never attach
  `tzinfo`, never label derived time as UTC, never "correct" an offset, never
  import `datetime`.
- **Reach is expressed in observed rows, never in wall-clock duration.**
- **You have no reach budget of your own.** All legitimate history reaches a
  model through **declared features**, whose backward reach is finite, declared
  and adversarially enforced by
  [ADR-0021](../adr/ADR-0021-columnar-feature-contract.md)'s `max_prior_rows`.
  There is deliberately **no model-level `max_prior_rows` field**
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §6): under row-local
  inference there is no carried state, so
  [ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 1's information span
  stays computable from feature and outcome declarations alone.
- **A regime-adaptive model is fully expressible** under this invariant: its
  market-state inputs are ordinary declared features, and its regime-dependent
  behaviour is learned into `FittedState`.

### The one controlled fit-time forward channel

```
training view -> config-declared OutcomeSpec -> orchestrator computes
TrainingTargetFrame -> ResearchModel.fit(inputs, training_targets)
-> FittedPayload -> orchestrator binds identity -> FittedState
```

This is the **only** legal route by which forward-derived information may
influence a fit ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)
amendment, [CLAUDE.md](../../CLAUDE.md)). It is a controlled exception, not
permission for arbitrary future reads:

1. the config **explicitly names** the training OutcomeSpec — there is no
   implicit or default target;
2. the **orchestrator** computes it, never the model;
3. targets are bounded by the admitted training information range, and samples
   whose target span would cross the fold boundary are **purged** — the purge is
   derived from the OutcomeSpec's declared
   `decision_delay_rows + horizon_rows`;
4. the OutcomeSpec identity, params, target range and alignment are pinned into
   fitted-state identity;
5. stateless and unsupervised models **omit the channel entirely**;
6. **inference never receives targets** — enforced by the signature, not by
   convention.

Anything else is a leakage bug and a release blocker, not a cleanup item.

- **A key discontinuity is arithmetic and nothing else** — never a weekend, a
  session boundary, a holiday or an outage
  ([ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md)). Do not read
  `features.keys` to infer market structure. In practice the reference models
  touch `keys` only to pass them straight to the frame minter.
- **The fit rows you receive are already purged and embargoed.** Do not attempt
  to widen them; `_FitIsolation._validate_fit` refuses a fit whose keys differ
  from the generated effective fit set.

---

## 7. Registration and configuration

### Registration

One line at the bottom of
[`models/reference.py`](../../src/trading_research/models/reference.py):

```python
_RESEARCH_MODELS.add("your_model_v1", YourModel)
```

- The **registry name** is what config uses; keep it equal to `model_id`, as all
  three reference models do.
- The second argument is a **factory** — normally the class itself.
  `RESEARCH_MODELS.create(name, **params)` calls `factory(**params)`.
- `models/__init__.py` imports `reference` for its registration side effect, so
  importing `trading_research.models` populates the registry.
- Duplicate names raise `DuplicateComponentError` at import time.
- There is **no decorator-based or automatic discovery** in use here, and no
  entry-point scanning.

### Configuration

A resolved experiment config
([`experiments/config.py`](../../src/trading_research/experiments/config.py))
carries exactly one `model` block:

```json
"model": {"name": "stateless_rule_v1", "version": 1, "params": {}}
```

At resolution `_create_component` constructs the component and then **re-checks**
that the live object's `model_id`, `version` and canonical `params` match the
config exactly; a mismatch is a contract error. Float params in config JSON use
the canonical hex form (`{"__float_hex__": "0x1.8p+1"}`).

`_preflight_components` refuses, before any computation:

- a model whose `required_feature_names` are not all produced by the configured
  features;
- a model requiring a representation when the config declares none;
- a configured representation the model does not consume;
- a model whose `required_representation_names` are not all in the
  representation's `output_column_names`;
- a decision policy whose `required_output_columns` are not all in your
  `output_column_names`.

> **Then the stricter execution-time rule applies:** the `FeatureFrame` handed
> to `fit`/`infer` contains *all* configured feature columns, and
> `_validate_model_inputs` requires that set to **equal**
> `required_feature_names` exactly, in order. Preflight checks membership;
> execution checks equality. In practice, configure exactly the features your
> model (and your representation) declare.

### Architecture guards you must update

Registering a model necessarily updates pinned inventories, and the tests fail
until you do. **Only add names.**

| File | Constant / test | Change needed |
|---|---|---|
| `tests/test_architecture.py` | `SLICE_3C_MODEL_CLASSES` | add `"registry_name": "ClassName"` |
| `tests/test_architecture.py` | `SLICE_3D_COVERED_MODELS` | add the registry name |
| `tests/test_model_leakage.py` | `COVERED_MODEL_NAMES` | add the registry name |
| `tests/test_model_leakage.py` | `TARGETED_MODEL_NAMES` | add it **only** if the model requires `training_targets` |
| `tests/test_model_leakage.py` | `REPRESENTED_MODEL_NAMES` | add it **only** if the model requires a representation (it must then also be in `TARGETED_MODEL_NAMES` — the rig asserts that inclusion) |
| `src/.../models/reference.py` | `__all__` | add the class name (sorted) |

Two guard properties constrain *how* you write the class:

- **Module public names must be exactly the class names plus `annotations`.**
  That is why every import in `models/reference.py` is underscore-aliased
  (`import numpy as _np`, `... import CostHint as _CostHint`). A plain
  `import numpy as np` fails
  `test_slice_3c_reference_components_and_registry_provenance_are_exact`.
- **The file inventory is pinned** to `__init__.py`, `execution.py`,
  `reference.py`, `registry.py` (`SLICE_3C_LAYER_INVENTORIES`). Do not add a
  module to this layer.

> **Known constraint — read before designing a parameterised model.** The same
> architecture test asserts that **every class listed in `SLICE_3C_MODEL_CLASSES`
> has no `__init__`**. The platform contract itself supports parameters (config
> params flow through `Registry.create(**params)` into a constructor, exactly as
> they do for `ThresholdPolicy` and `ForwardLogReturn`), but adding a constructor
> here would require relaxing that assertion.
> [CLAUDE.md](../../CLAUDE.md) treats widening an architecture test as an
> architecture change. **Do not do it inside a plugin addition — stop and raise
> it.** Until then, a new model registered in `reference.py` must be
> constructible with zero arguments and declare `params = {}`.

---

## 8. Minimal working example

Deliberately simple and strategy-agnostic: learn one scalar from the training
targets and emit it wherever the declared feature is available. It is a
*baseline*, not a strategy. It mirrors the style of
[`models/reference.py`](../../src/trading_research/models/reference.py),
including the underscore-aliased imports required by §7.

Assume this module preamble (already present in `reference.py`):

```python
from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.research import ModelInputs as _ModelInputs
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame
from trading_research.core.research_frames import _make_model_output_frame
from trading_research.models.registry import RESEARCH_MODELS as _RESEARCH_MODELS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping
```

```python
_FEATURE = "log_return_1"
_TARGET = "target.forward_log_return"
_OUTPUT = "score"


class TargetMedianModel:
    """Emit one median learned through the platform target channel."""

    @property
    def model_id(self) -> str:
        """Return the stable model identity."""
        return "target_median_v1"

    @property
    def version(self) -> int:
        """Return the model contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the empty identity-bearing parameter mapping."""
        return {}

    @property
    def required_feature_names(self) -> tuple[str, ...]:
        """Return the sole declared feature input."""
        return (_FEATURE,)

    @property
    def required_representation_names(self) -> tuple[str, ...]:
        """Declare that this model consumes no representation."""
        return ()

    @property
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole generic model output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap fitting."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(
        self,
        view: _ViewSpan,
        inputs: _ModelInputs,
        training_targets: _TrainingTargetFrame | None = None,
    ) -> _FittedPayload:
        """Learn the finite-pair target median through the platform channel."""
        if training_targets is None or training_targets.column_names != (_TARGET,):
            raise _ContractViolationError(f"target_median_v1 requires {_TARGET}")
        targets = training_targets.column(_TARGET)
        features = inputs.features.column(_FEATURE)
        available = _np.isfinite(features) & _np.isfinite(targets)
        if not available.any():
            raise _ContractViolationError("target_median_v1 has no finite training pairs")
        median = _np.array([_np.median(targets[available])], dtype=_np.float64)
        return _FittedPayload(
            arrays={"target_median": median},
            metadata={"state_schema": "target_median_v1"},
        )

    def infer(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        inputs: _ModelInputs,
    ) -> _ModelOutputFrame:
        """Emit the fixed learned median wherever the declared feature exists."""
        if fitted_state.payload.metadata != {"state_schema": "target_median_v1"}:
            raise _ContractViolationError("fitted payload is not target_median_v1 state")
        stored = fitted_state.payload.array("target_median")
        if stored.shape != (1,) or stored.dtype != _np.dtype(_np.float64):
            raise _ContractViolationError("target_median must be one float64 scalar")
        feature = inputs.features.column(_FEATURE)
        values = _np.where(_np.isfinite(feature), float(stored[0]), _np.nan).astype(
            _np.float64
        )
        origin = _StageOrigin(self.model_id, self.version, self.params)
        return _make_model_output_frame(
            view=view,
            keys=inputs.features.keys,
            columns={_OUTPUT: values},
            origins={_OUTPUT: origin},
        )


_RESEARCH_MODELS.add("target_median_v1", TargetMedianModel)
```

Points worth copying:

- The model **states what it requires** from the target channel and refuses
  anything else, rather than silently indexing column 0.
- `NaN` targets (incomplete horizons) are masked, never propagated into the
  learned scalar.
- `infer` reads only row *i*'s feature value and the fixed state — no `mean()`,
  `std()` or `cumsum()` over the evaluation frame.
- The output name `score` is generic. It says nothing about direction.
- Arrays returned from `.column(...)` are read-only; copy first if you need a
  mutable buffer.

### Sparse output

To speak on a subset of rows, pass a subset of `inputs.features.keys` and a
matching value array:

```python
selected = _np.isfinite(feature)
return _make_model_output_frame(
    view=view,
    keys=inputs.features.keys[selected],
    columns={_OUTPUT: values[selected]},
    origins={_OUTPUT: origin},
)
```

Absent rows mean "no statement at this key". They are **not** equivalent to
dense `NaN`s, and downstream consumers preserve the difference.

---

## 9. Tests an author should write

### Already provided globally by the platform — no test to write

Registering a model enrols it automatically in these release-blocking rigs:

| Rig / guard | What it proves |
|---|---|
| `test_every_registered_model_is_forward_blind` | Perturbing declared input rows strictly **after** *i* leaves output at *i* bit-for-bit unchanged. |
| `test_every_registered_model_is_row_local` | Perturbing every row **except** *i* leaves output at *i* bit-for-bit unchanged. |
| `test_rig_coverage_is_an_exact_independent_match_for_both_live_registries` | The rig's covered set equals the live registry — you cannot register without rig coverage. |
| `test_fit_boundary_catches_a_model_attempting_its_own_forward_read` | The sentinel proving `fit` has no route to data beyond its bounded inputs. |
| `test_slice_3c_reference_components_and_registry_provenance_are_exact` | Public surface, `__all__`, module public names, registry provenance. |
| `test_slice_3c_plugins_return_payloads_but_have_no_state_identity_authority` | `fit` returns `FittedPayload`, never `FittedState`. |
| `test_model_output_refuses_trading_and_outcome_vocabulary` | The banned-keys guard on output column names. |
| `test_layer_respects_its_dependency_budget` | `models` imports only `core`. |
| `test_n_policy_fan_out_performs_one_inference_and_zero_reinference` | Your output is computed once and shared by every policy. |
| Determinism verification (`verify` CLI verb) | Recomputes with caching forced off and compares canonical bytes under your declared tolerance. |

Comparison in the rigs is **exact** — NaN masks as bytes, values as raw `uint64`
bits. No tolerance, no `skip`, no `xfail`; an architecture guard forbids
introducing any.

### Tests the author should add — in `tests/test_models.py`

Use **synthetic data only**; never a real market value in a fixture.

- [ ] **Declaration test.** Assert the full ten-member surface exactly.
- [ ] **Hand-computed golden.** A tiny frame (5–8 rows) with literal expected
      numbers — not a re-implementation of your own formula.
- [ ] **Fit/inference separation.** Fit on frame A, infer on frame B, and assert
      the output depends only on the stored state; refitting on B must change
      the result if your method is genuinely fitted.
- [ ] **Target-channel behaviour.** If supervised: assert `fit` refuses
      `training_targets=None` and refuses an unexpected target column set; assert
      `NaN` targets are excluded. If stateless: assert `fit` refuses a
      `training_targets` argument it does not use, as `StatelessRuleModel` does.
- [ ] **Identity sensitivity.** Assert that two different target *definitions*
      over the same rows produce different `fit_model(...).identity_key()`
      values, and — if parameterised — that two configurations do too.
- [ ] **Shape and schema.** `column_names == output_column_names`; keys strictly
      increasing, inside the view, a subset of input keys; no `±inf`.
- [ ] **Sparsity, if you emit it.** Assert the emitted key set is exactly the
      intended subset and that absent rows are absent rather than `NaN`-filled.
- [ ] **Forbidden output vocabulary.** Assert your chosen names are accepted and
      that an intentionally banned name (e.g. `direction`) is refused — the
      shared guard covers the rule; this documents your naming choice.
- [ ] **Alignment refusal.** A `FeatureFrame` whose columns differ from
      `required_feature_names`, a missing required `RepresentationFrame`, and an
      unexpected `RepresentationFrame` are each refused before any computation.
- [ ] **Invalid input refusal.** An all-NaN fit frame or a malformed fitted
      payload raises `ContractViolationError` rather than fabricating a value.
- [ ] **Registry resolution.** `"your_model_v1" in RESEARCH_MODELS.names()` and
      `RESEARCH_MODELS.create("your_model_v1")` returns your class.
- [ ] **Rigs and guards green with the model registered** — run
      `uv run pytest tests/test_model_leakage.py tests/test_architecture.py -q`.
- [ ] **Full gate before merge** — `./scripts/gate.sh`, unfiltered and green.

> **Do not register until the model can pass the live rigs.** They are
> release-blocking: a registered model that fails them turns the whole gate red
> for everyone. Develop against a local unregistered instance first — the rig
> helpers accept a plain object.

---

## 10. Common mistakes

| # | Anti-pattern | Why it violates the architecture | Caught by |
|---|---|---|---|
| 1 | `values[i + 1]`, or normalizing by `values.mean()` of the evaluation frame | forward read / whole-range dependence | forward rig (`ForwardShiftModelControl`, `WholeEvaluationRangeNormalizerControl`) |
| 2 | `np.cumsum(...)` or any accumulator in `infer` | carried state; ADR-0022 §6 excludes it from V1 | row-locality rig (`CarriedStateAccumulatorControl`) — it passes the forward rig |
| 3 | Computing a forward return inside `fit` "just for training" | bypasses the one controlled channel | ADR-0010; `SelfForwardReadFitModelControl` sentinel |
| 4 | Storing per-key targets in the payload and looking them up during `infer` | targets at inference time, laundered through state | authoring rule; the row-locality rig will not see it, so **do not write it** |
| 5 | Emitting a column named `signal`, `Side` or `target.x` | trading vocabulary / reserved namespace | frame minting refuses it |
| 6 | Emitting `+1` / `-1` after applying a threshold | decision logic inside the model | ADR-0009; the platform cannot detect intent — this is why the rule is explicit |
| 7 | Returning a `FittedState`, or hashing your own identity | plugins own learned content; the platform owns identity | ADR-0022 Amendment 3; architecture guard |
| 8 | Importing `trading_research.features` or `...outcomes` for a helper | cross-plugin import | dependency-budget test |
| 9 | A constructor argument absent from `params` | two configurations collide on one fitted-state identity | nothing automatic — this is why §5 exists |
| 10 | A plain `import numpy as np` in `reference.py` | adds `np` to module public names | exact-public-surface guard |
| 11 | Output keys not a subset of the input feature keys, or out of order | sparsity relaxes coverage, never ordering or bounds | `_infer_model_output` |
| 12 | Reading `features.keys` to detect a weekend or session | a discontinuity is arithmetic only | ADR-0005 (authoring rule; the rigs perturb values, not the axis) |
| 13 | Assuming `fit` and `infer` see the same rows | they see different folds/ranges by construction | your own golden tests, if you write them |

---

## 11. Author checklist

- [ ] The component is genuinely a **model** — it produces a statistic, not a
      trade instruction and not a fitted preprocessing transform.
- [ ] `infer` is row-local: row *i* uses row *i*'s declared inputs plus fixed
      state, and nothing else.
- [ ] `fit` reads only `inputs` and `training_targets`.
- [ ] Ten-member public surface, exactly; no extra public method or attribute.
- [ ] `model_id` == registry name; `version` a positive `int`.
- [ ] Every semantic knob is in `params`; `params` values fit `ParamValue`; any
      randomness is seeded through a declared `params["seed"]`.
- [ ] `required_feature_names` non-empty; `required_representation_names` empty
      unless a representation is genuinely consumed; `output_column_names`
      non-empty, generic, free of banned and reserved vocabulary.
- [ ] `cost_hint` declared honestly; `comparison_tolerance` is `None` unless a
      genuine reason is written down.
- [ ] `fit` returns `FittedPayload` only; no identity, no store, no path.
- [ ] `fit` states its target-channel expectations and refuses anything else.
- [ ] `infer` mints via `_make_model_output_frame` with a `StageOrigin` built
      from its own declaration.
- [ ] Sparse output, if any, preserves the absent/`NaN` distinction.
- [ ] No I/O, no clock, no environment, no module-level mutable state, no
      unseeded randomness.
- [ ] Class constructible with zero arguments (see the §7 known constraint).
- [ ] Registered in `reference.py`; `__all__` updated; imports underscore-aliased.
- [ ] §7 guard inventories updated **by addition only**, and called out
      explicitly in the commit message or PR description.
- [ ] §9 author tests added, synthetic data only.
- [ ] `uv run pytest tests/test_model_leakage.py tests/test_models.py tests/test_architecture.py -q` green.
- [ ] `./scripts/gate.sh` green, unfiltered.
- [ ] No production file outside `models/reference.py` modified.
- [ ] No dataset, view manifest, audit record, run directory or cache artifact
      touched; no OOS or LOCKBOX data read.

### Stop and raise instead of working around

Halt and ask for an architecture decision if making the model fit requires any
of:

- carried state between evaluation rows, online or incremental learning, or any
  inference whose result at row *i* depends on other evaluation rows
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §6 — the
  row-locality rig is precisely the detector for this trigger, and the first
  model that *legitimately* fails it is the first model that needs that ADR);
- a forward read at fit time outside the `TrainingTargetFrame` channel, or
  targets at inference time;
- a market column, a `MarketFrame`, an `OutcomeRequest` or any data-layer handle;
- a second representation stage, or a feature-of-feature path;
- a constructor on a class in `models/reference.py` (§7);
- relaxing, deleting or skipping any architecture guard or rig assertion;
- touching `core/`, `artifacts/`, `experiments/` or the data layer.

---

## 12. References

Canonical sources. **If this guide conflicts with any of them, they win.**

**Contracts and decisions**

- [ADR-0022 — Phase-3 research contracts](../adr/ADR-0022-phase-3-research-contracts.md) — the binding contract (§5 row-locality, §6 no carried state, §8 tolerance, Amendment 1, Amendment 2 dependency identity, Amendment 3 payload/identity split)
- [ADR-0008 — Fit lifecycle and FittedState identity](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md)
- [ADR-0009 — Generic model output and the IntentFrame family](../adr/ADR-0009-generic-output-and-intentframe-family.md)
- [ADR-0010 — Two-plane evaluation and versioned OutcomeSpec](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) — the fit-time target channel
- [ADR-0006 — Fixed stage pipeline](../adr/ADR-0006-fixed-stage-pipeline.md) · [ADR-0007 — MaterializationPolicy](../adr/ADR-0007-materialization-policy.md) · [ADR-0012 — Two-tier splits](../adr/ADR-0012-two-tier-splits.md) · [ADR-0016 — Determinism tiers](../adr/ADR-0016-determinism-tiers.md) · [ADR-0021 — Columnar feature contract](../adr/ADR-0021-columnar-feature-contract.md)
- [ADR index](../adr/README.md)

**Plans and policy**

- [docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) — normative Phase-3 plan (§2 closed vocabulary, §2.4 identity field list, §2.7 fold geometry)
- [docs/architecture/V1.md](../architecture/V1.md) §6 — research contracts
- [docs/research/RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §5 — lookahead leakage and the one controlled exception
- [PROJECT_STATE.md](../../PROJECT_STATE.md) · [CLAUDE.md](../../CLAUDE.md)

**Code**

- [`src/trading_research/models/reference.py`](../../src/trading_research/models/reference.py) — the three live reference models
- [`src/trading_research/models/execution.py`](../../src/trading_research/models/execution.py) — declaration snapshot, input and output validation
- [`src/trading_research/models/registry.py`](../../src/trading_research/models/registry.py) — `RESEARCH_MODELS`
- [`src/trading_research/core/interfaces.py`](../../src/trading_research/core/interfaces.py) — the `ResearchModel` protocol
- [`src/trading_research/core/research.py`](../../src/trading_research/core/research.py) — `ViewSpan`, `ModelInputs`, `StageOrigin`, `ToleranceSpec`
- [`src/trading_research/core/research_frames.py`](../../src/trading_research/core/research_frames.py) — `ModelOutputFrame`, `TrainingTargetFrame`, `FittedPayload`, `FittedState`
- [`src/trading_research/experiments/fitting.py`](../../src/trading_research/experiments/fitting.py) — `fit_model`, `infer_model`
- [`src/trading_research/experiments/folds.py`](../../src/trading_research/experiments/folds.py) — fold geometry, derived purge, `_FitIsolation`
- [`src/trading_research/artifacts/fitted.py`](../../src/trading_research/artifacts/fitted.py) — fitted-state identity and serialization

**Tests**

- [`tests/test_model_leakage.py`](../../tests/test_model_leakage.py) — release-blocking forward and row-locality rigs, and their negative controls
- [`tests/test_models.py`](../../tests/test_models.py) — lifecycle, target channel, declaration refusal
- [`tests/test_fitted_state.py`](../../tests/test_fitted_state.py) — identity sensitivity and pickle-free round trip
- [`tests/test_research_frames.py`](../../tests/test_research_frames.py) — carrier construction, sparsity, banned vocabulary
- [`tests/test_folds.py`](../../tests/test_folds.py) — purge derivation and fit isolation
- [`tests/test_architecture.py`](../../tests/test_architecture.py) — dependency budgets, exact surfaces, guard inventories
- [`scripts/gate.sh`](../../scripts/gate.sh) — the gate you must run
