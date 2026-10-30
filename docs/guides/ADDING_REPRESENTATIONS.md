# Adding a RepresentationModel — authoring guide

**Audience:** anyone adding a learned encoding of feature columns to this
platform — a researcher who knows the transform but not the internals, or a
coding agent asked to implement one safely.

**Scope:** this guide is *explanatory*. It creates no new platform rule. If
anything here disagrees with an accepted ADR or with
[`docs/phases/PHASE3_RESEARCH_RUNS.md`](../phases/PHASE3_RESEARCH_RUNS.md),
**the contract documents win** — fix the guide, not the contract. The binding
contract is
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) (with
[ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md) for the fit
lifecycle and [ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md) for why there
is exactly one representation stage).

**Status of the code this describes:** Phase 3 is COMPLETE. Everything below is
implemented and enforced today. Nothing here is aspirational.

**Companion guides:**
[ADDING_FEATURES.md](ADDING_FEATURES.md) ·
[ADDING_RESEARCH_MODELS.md](ADDING_RESEARCH_MODELS.md) ·
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
    v   RepresentationModel.fit(view, features)          -> THIS GUIDE
    |   RepresentationModel.encode(view, state, features)
RepresentationFrame                       (optional; at most ONE such stage)
    |
    v   ModelInputs(features, representation?)
ResearchModel.fit / .infer                          -> ADDING_RESEARCH_MODELS.md
    |
    v
ModelOutputFrame
    |
    v   DecisionPolicy.decide(model_output)      -> ADDING_DECISION_POLICIES.md
IntentFrame
```

`OutcomeSpec` does **not** appear anywhere on this path. A representation has no
target channel at all — see [ADDING_OUTCOMES.md](ADDING_OUTCOMES.md) §1.

---

## 1. What a RepresentationModel is responsible for

A **representation model** learns a small amount of state from a bounded set of
feature rows, and then re-expresses each feature row using that fixed state. A
standardizer, a whitening transform, a projection: fitted preprocessing that
must not be hidden inside a model.

### What enters

Exactly two things, both supplied by the platform:

| `fit` | `encode` |
|---|---|
| `view: ViewSpan` (a rowless descriptor) | `view: ViewSpan` |
| `features: FeatureFrame` | `fitted_state: FittedState` |
| | `features: FeatureFrame` |

Nothing else. No market columns, no `MarketFrame`, no `FeatureRequest`, no
`ModelInputs`, no targets, no fold record, no store, no path.

### What it produces

- `fit` returns a **`FittedPayload`** — learned numeric arrays plus JSON-safe
  metadata, and nothing more.
- `encode` returns a **`RepresentationFrame`** — dense `float64` columns on
  exactly the key axis of the `FeatureFrame` it was given.

### What it owns

**The transform, and what the transform learned.** That is the whole job.

### What the platform owns instead

Do not re-implement any of this:

| The platform handles | What that means for you |
|---|---|
| **Loading and authorization** | You never see a file, a grant, a dataset path or a view manifest. |
| **Which rows you may fit on** | The orchestrator subsets the feature rows to the fold's *effective fit set* — after purge and embargo — and refuses a `fit` whose keys differ from that set (`_FitIsolation._validate_fit`). |
| **Fitted-state identity** | You return payload; the composition root binds the complete twelve-field identity document and mints `FittedState` ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) Amendment 3). |
| **Serialization and caching** | Versioned NPZ + JSON, never pickle; `cost_hint` selects whether the state is persisted. |
| **Alignment and validation** | Your declaration is checked before you run and your output is checked after. |
| **Determinism verification** | The verifier recomputes with caching forced off and compares canonical bytes. |

> **Rule of thumb.** If you find yourself writing anything other than array
> arithmetic over the feature columns you declared, plus arrays you learned in
> `fit`, you are probably outside the contract.

---

## 2. What a RepresentationModel must NOT do

Each of these is refused structurally, by a guard, or by a release-blocking rig.

- **No forward reads.** Output at evaluation row *i* must not depend on any row
  after *i*. Enforced by the forward rig
  ([`tests/test_model_leakage.py`](../../tests/test_model_leakage.py)).
- **No dependence on any other evaluation row, in either direction.**
  `encode` is **row-local** ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md)
  §5): row *i*'s output depends only on row *i*'s declared inputs and the fixed
  `FittedState`. No cumulative sums, no evaluation-range means, no carried
  state. Enforced by the row-locality rig.
- **No fitting during `encode`.** Recomputing a mean or a scale from the frame
  you are encoding defeats the entire stage; the rigs catch it — that is exactly
  what `EvaluationRangeFitRepresentationControl` demonstrates.
- **No direct data access.** No `open()`, no loader, no `AccessGrant`, no
  `DatasetRegistry`, no `MarketFrame`, no market column. `representations` may
  import **only** `core` (`test_layer_respects_its_dependency_budget`).
- **No cross-plugin imports.** Not from `features`, not from `models`, not from
  `outcomes`, not from `decisions`, not from `experiments`, not from
  `artifacts`, not from `data`.
- **No targets, no outcomes, no forward returns.** A representation has no
  target channel — `fit` has no `training_targets` parameter and never will
  without a new ADR. Computing a forward value yourself is a leakage bug
  ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)).
- **No reserved-namespace output.** Column names equal to `target` or beginning
  `target.` are refused at frame minting.
- **No representation chaining.** `fit`/`encode` take a `FeatureFrame`
  **directly**, never `ModelInputs`. There is no parameter through which another
  `RepresentationFrame` could arrive, and an architecture test asserts the
  `representations` layer never even names `ModelInputs`.
- **No identity authority.** The names `ArtifactStore`, `MaterializationPolicy`,
  `PlatformIdentity`, `SplitPolicyIdentity`, `StageIdentity`,
  `_FittedStateIdentity`, `_bind_fitted_state`, `_make_fitted_state`,
  `dependency_lock_sha256` and `fit_isolation` are on an explicit forbidden list
  for this layer.
- **No filesystem, registry or run-directory access.** Not from `fit`, not from
  `encode`, not at import time.
- **No hidden nondeterminism.** No environment variables, wall-clock reads,
  local config files, module-level mutable state, or unseeded randomness.
- **No undeclared parameter.** Anything that can change a returned number must
  be visible in `params` — see §5.
- **No decision vocabulary.** Direction/exposure/trading words belong in
  `DecisionPolicy` only; an architecture test forbids `Direction` anywhere in
  this layer.

---

## 3. Exact production contract

`RepresentationModel` in
[`core/interfaces.py`](../../src/trading_research/core/interfaces.py) is a
`typing.Protocol`, so structural conformance is enough — there is no base class
to inherit. An architecture test asserts the reference implementation's public
surface is **exactly** these nine names and nothing else.

```python
representation_id: str                    # stable identity of this implementation
version: int                              # positive; identity-bearing contract version
params: Mapping[str, ParamValue]          # every semantic knob, canonically encoded
required_feature_names: tuple[str, ...]   # ordered, unique, non-empty
output_column_names: tuple[str, ...]      # ordered, unique, non-empty
cost_hint: CostHint                       # CHEAP | EXPENSIVE — a caching hint only
comparison_tolerance: ToleranceSpec | None # None means exact; the default

def fit(self, view: ViewSpan, features: FeatureFrame) -> FittedPayload: ...
def encode(self, view: ViewSpan, fitted_state: FittedState,
           features: FeatureFrame) -> RepresentationFrame: ...
```

The declaration is read and validated **once** by
`_snapshot_representation` in
[`representations/execution.py`](../../src/trading_research/representations/execution.py),
and re-read after a cache lookup by `_revalidate_representation`; a declaration
that changes between those two reads is refused.

### `representation_id`

The stable string identity of the implementation. It is the registry name, it
appears in every produced column's `StageOrigin`, and it is hashed into fitted
state identity. **Enforced:** exact non-empty `str`. Do not rename an
implementation to change its behaviour; bump `version` instead.

### `version`

**Enforced:** exact Python `int`, strictly positive (not `bool`, not
`numpy.int64`). Increment it when the same declaration should now produce
different numbers.

### `params`

Every semantic knob. **Allowed value types** (`ParamValue`): `str`, `int`,
`float`, `bool`, `None`, and nested `tuple`s of those. Encoding is canonical and
type-preserving — key order is irrelevant (sorted), `4` and `4.0` are distinct,
`True` and `1` are distinct, floats are encoded via `float.hex()`, and
`float('nan')` is refused. **Enforced:** a `Mapping` whose keys are non-empty
strings and whose values fit `ParamValue`.

### `required_feature_names`

The ordered feature columns you consume. **Enforced:** exact non-empty `tuple`
of unique non-empty `str`.

> **This is an equality, not a subset.** `_validate_representation_features`
> refuses unless `features.column_names == required_feature_names` — same names,
> same order. In practice the experiment's configured feature set must be
> exactly this tuple; see §7.

### `output_column_names`

The ordered representation columns you emit. **Enforced:** exact non-empty
`tuple` of unique non-empty `str`; the frame you return must carry exactly these
names in exactly this order; reserved (`target`, `target.…`) names are refused
at minting.

### `cost_hint`

`CostHint.CHEAP` or `CostHint.EXPENSIVE`. It answers one question: under the
default `CACHE_IF_EXPENSIVE` materialization policy, should the **fitted state**
be written to the artifact store? It is **not** part of the identity key, so two
declarations differing only in `cost_hint` produce the same fitted-state
identity. No wall clock is ever consulted
([ADR-0007](../adr/ADR-0007-materialization-policy.md) Amendment 2).
**Enforced:** exact `CostHint` member.

### `comparison_tolerance`

`None` (exact comparison — the default that requires no declaration) or a frozen
`ToleranceSpec(atol, rtol, reason)` with finite non-negative bounds and a
non-empty reason. A declared tolerance is **identity-bearing**: it is hashed into
the fitted-state identity, so loosening one changes identity and is visible in
review. It is never a CLI or verifier flag
([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §8). The V1 reference
representation declares `None`; start there.

### `fit(view, features) -> FittedPayload`

`view` is a frozen `ViewSpan`: `source_identities`, `unit`, `clock_domain`,
`context_start`, `eval_start`, `eval_end`. It is a **descriptor, not a data
handle** — no rows, no columns, no loader, no grant. Its normal use in a plugin
is none at all; the reference implementation ignores it.

`features` is the `FeatureFrame` restricted to the rows you are permitted to fit
on. Read columns with `features.column(name)` (fresh read-only `float64`
descriptors) and the axis with `features.keys`.

You must return an exact `FittedPayload`:

```python
FittedPayload(arrays={...}, metadata={...})
```

- `arrays`: `str` → non-object NumPy arrays with metadata-free real numeric
  dtypes (`bool`/`int`/`uint`/`float`). Detached and made read-only on
  construction; names are canonically sorted.
- `metadata`: a `Mapping[str, ParamValue]`, validated with the same canonical
  encoder as `params`.

`FittedPayload` carries **no identity, view, split policy, purge record,
platform identity or dependency identity** — it cannot, by construction.

### `encode(view, fitted_state, features) -> RepresentationFrame`

`fitted_state.payload` is the `FittedPayload` you returned from `fit` (restored
from the store if it was cached). `fitted_state.identity_key()` is a 64-character
hex string you may read but must never compute, assert on, or embed in output.

You must return a frame minted by the platform's private helper:

```python
from trading_research.core.research_frames import _make_representation_frame

_make_representation_frame(view=view, features=features,
                           columns={...}, origins={...})
```

This is the only route to a `RepresentationFrame` available to a plugin —
`RepresentationFrame.__init__` raises. The helper takes the key axis, unit,
clock domain and source identities **from the `FeatureFrame`**, so you cannot
misalign them.

---

## 4. Lifecycle

A representation is **stateful**: it has a real fit step whose result is a
distinct, identified, cacheable artifact.

Per fold, inside `_execute_fold` in
[`experiments/orchestration.py`](../../src/trading_research/experiments/orchestration.py):

```
1.  fit_features    = feature rows of the fold's EFFECTIVE FIT SET
                      (fit window minus purge minus embargo)
2.  representation_state = fit_representation(plugin,
                              view=fold.training_view,
                              features=fit_features, ...)      -> FittedState
3.  fit_representation_frame  = encode(view=fold.training_view,
                                       state, fit_features)
4.  eval_representation_frame = encode(view=fold.evaluation_view,
                                       state, eval_features)
5.  both frames are handed to the ResearchModel as
    ModelInputs(features, representation)
```

Points that matter:

- **`fit` is called once per fold**, on training rows only. Its result is bound
  to a complete identity and materialized under `CACHE_IF_EXPENSIVE`; an
  identical identity restores the stored state instead of refitting.
- **`encode` is called twice per fold with the same fitted state** — once on the
  training rows (so the model fits on encoded training inputs) and once on the
  evaluation rows. The state does not change between them. If your `encode`
  derives anything from the frame's own distribution, the two calls disagree and
  the rigs fail.
- **The evaluation-side `RepresentationFrame` is persisted** into the run
  directory and enters determinism comparison under your declared tolerance.
- **There is no second representation stage.** The output of a representation
  can only be consumed by a `ResearchModel`, never by another representation.

`fit` receives no targets and `encode` receives no targets — there is no
parameter for them.

---

## 5. Identity, parameters, and reproducibility

### What you declare

Only these five things of yours enter fitted-state identity:
`representation_id`, `version`, canonical `params`, and — indirectly — the
`FeatureOrigin` of every column you declared in `required_feature_names`, plus
your `comparison_tolerance`.

### What the platform adds

The complete identity document assembled by `_FittedStateIdentity` in
[`artifacts/fitted.py`](../../src/trading_research/artifacts/fitted.py) has
twelve fields: `format`, `format_version`, `plugin`, `features`,
`representation`, `training_view`, `split_policy`, `training_outcome`,
`fit_isolation`, `platform`, `dependencies`, `tolerance`. For a representation
fit, `representation` and `training_outcome` are `null` — a representation
consumes neither.

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
    return {"clip_sigma": self._clip_sigma}   # the knob is declared
```

A seed, if a representation ever needs one, is declared as an ordinary
identity-bearing parameter (`params["seed"]`) — that is what
[ADR-0007](../adr/ADR-0007-materialization-policy.md) requires and it needs no
extra machinery. No V1 reference representation is stochastic.

### Incorrect — invisible to identity

```python
scale = float(os.environ["REP_SCALE"])       # environment dependence
if datetime.now().weekday() < 5: ...         # wall clock
cfg = json.load(open("myrep.json"))          # local config file
_CACHE.append(x); bias = len(_CACHE)         # mutable module-global state
noise = np.random.randn(n)                   # unseeded randomness
```

The first three are also blocked outright by the no-I/O and no-clock rules. The
last two are the dangerous ones, because they *look* like ordinary code.

### Fitted-state identity is not yours to assert

`FittedState` is minted only by `experiments`. Never construct one, never derive
one, never write `identity_key()` into a payload array or metadata value.

---

## 6. Time and information boundaries

- **The axis is `NativeTimeKey`**: `int64` values plus a `TimeUnit` and an opaque
  `ClockDomain`. The dataset clock domain is **broker wall-clock, not UTC**
  ([ADR-0002](../adr/ADR-0002-native-time-model.md)). Never attach `tzinfo`,
  never label derived time as UTC, never "correct" an offset, never import
  `datetime`.
- **Reach is expressed in observed rows, never in wall-clock duration.**
- **You have no reach budget of your own.** All legitimate history reaches this
  stage through **declared features**, whose backward reach is finite, declared
  and adversarially enforced by
  [ADR-0021](../adr/ADR-0021-columnar-feature-contract.md)'s `max_prior_rows`.
  A representation therefore has **no `max_prior_rows` field**, deliberately
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §6): under
  row-local `encode` there is no carried state, so the information span stays
  computable from feature declarations alone
  ([ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 1).
- **Fit-time forward information is not available to you at all.** The one
  controlled fit-time forward channel in this platform is
  `TrainingTargetFrame`, and it reaches `ResearchModel.fit` only. A
  representation has no such parameter. Do not simulate one.
- **A key discontinuity is arithmetic and nothing else** — never a weekend, a
  session boundary, a holiday or an outage
  ([ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md)). Do not read
  `features.keys` to infer market structure. In practice the reference
  representation never touches `keys` at all.
- **The fit rows you receive are already purged and embargoed.** Do not attempt
  to widen them; `_FitIsolation._validate_fit` refuses a fit whose keys differ
  from the generated effective fit set.

---

## 7. Registration and configuration

### Registration

One line at the bottom of
[`representations/reference.py`](../../src/trading_research/representations/reference.py):

```python
_REPRESENTATION_MODELS.add("your_name_v1", YourRepresentation)
```

- The **registry name** is what config uses; keep it equal to
  `representation_id`, as the reference implementation does.
- The second argument is a **factory** — normally the class itself.
  `REPRESENTATION_MODELS.create(name, **params)` calls `factory(**params)`.
- `representations/__init__.py` imports `reference` for its registration side
  effect, so importing `trading_research.representations` populates the
  registry.
- Duplicate names raise `DuplicateComponentError` at import time.
- There is **no decorator-based or automatic discovery** in use here, and no
  entry-point scanning. `Registry.register` exists as a decorator form of `add`
  but the representation layer does not use it.

### Configuration

A resolved experiment config
([`experiments/config.py`](../../src/trading_research/experiments/config.py))
carries a single optional `representation` block:

```json
"representation": {"name": "trailing_standardizer_v1", "version": 1, "params": {}}
```

At resolution time `_create_component` constructs the component and then
**re-checks** that the live object's `representation_id`, `version` and
canonical `params` match the config exactly; a mismatch is a contract error.

`_preflight_components` additionally refuses, before any computation:

- a configured representation whose `required_feature_names` are not all
  produced by the configured features;
- a configured representation that the selected model does not consume;
- a model whose `required_representation_names` are not all in your
  `output_column_names`;
- a model requiring a representation when the config declares none.

> **Then the stricter execution-time rule applies:** the `FeatureFrame` handed
> to `fit`/`encode` contains *all* configured feature columns, and
> `_validate_representation_features` requires that set to **equal**
> `required_feature_names` exactly, in order. Preflight checks membership;
> execution checks equality. In practice, configure exactly the features your
> representation (and your model) declare.

### Architecture guards you must update

Registering a representation necessarily updates pinned inventories, and the
tests fail until you do. **Only add names.**

| File | Constant / test | Change needed |
|---|---|---|
| `tests/test_architecture.py` | `SLICE_3C_REPRESENTATION_CLASSES` | add `"registry_name": "ClassName"` |
| `tests/test_architecture.py` | `SLICE_3D_COVERED_REPRESENTATIONS` | add the registry name |
| `tests/test_model_leakage.py` | `COVERED_REPRESENTATION_NAMES` | add the registry name |
| `src/.../representations/reference.py` | `__all__` | add the class name (sorted) |

Two guard properties constrain *how* you write the class:

- **Module public names must be exactly the class names plus `annotations`.**
  That is why every import in `representations/reference.py` is
  underscore-aliased (`import numpy as _np`, `... import CostHint as
  _CostHint`). A plain `import numpy as np` fails
  `test_slice_3c_reference_components_and_registry_provenance_are_exact`.
- **The file inventory is pinned** to `__init__.py`, `execution.py`,
  `reference.py`, `registry.py` (`SLICE_3C_LAYER_INVENTORIES`). Do not add a
  module to this layer.

> **Known constraint — read before designing a parameterised representation.**
> The same architecture test asserts that **every class listed in
> `SLICE_3C_REPRESENTATION_CLASSES` has no `__init__`**. The platform contract
> itself supports parameters (config params flow through
> `Registry.create(**params)` into a constructor, exactly as they do for
> `ThresholdPolicy` and `ForwardLogReturn`), but adding a constructor here would
> require relaxing that assertion. [CLAUDE.md](../../CLAUDE.md) treats widening
> an architecture test as an architecture change. **Do not do it inside a plugin
> addition — stop and raise it.** Until then, a new representation registered in
> `reference.py` must be constructible with zero arguments and declare
> `params = {}`.

---

## 8. Minimal working example

Deliberately simple and strategy-agnostic: rescale one declared feature column
by a fixed scale learned on the fit rows. It mirrors the style of
[`representations/reference.py`](../../src/trading_research/representations/reference.py),
including the underscore-aliased imports required by §7.

Assume this module preamble (already present in `reference.py`):

```python
from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedPayload as _FittedPayload
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import RepresentationFrame as _RepresentationFrame
from trading_research.core.research_frames import _make_representation_frame
from trading_research.representations.registry import (
    REPRESENTATION_MODELS as _REPRESENTATION_MODELS,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
```

```python
_FEATURE = "log_return_1"
_OUTPUT = "scaled_log_return_1"


class FitScaleRescaler:
    """Divide each row by a scale learned from the fit view only."""

    @property
    def representation_id(self) -> str:
        """Return the stable representation identity."""
        return "fit_scale_rescaler_v1"

    @property
    def version(self) -> int:
        """Return the representation contract version."""
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
    def output_column_names(self) -> tuple[str, ...]:
        """Return the sole dense representation output."""
        return (_OUTPUT,)

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap fitting."""
        return _CostHint.CHEAP

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def fit(self, view: _ViewSpan, features: _FeatureFrame) -> _FittedPayload:
        """Learn one positive scale from the finite fit-view values only."""
        values = features.column(_FEATURE)
        finite = values[_np.isfinite(values)]
        if finite.size == 0:
            raise _ContractViolationError("fit_scale_rescaler_v1 has no finite fit values")
        scale = float(_np.abs(finite).max())
        if scale == 0.0:
            scale = 1.0
        return _FittedPayload(
            arrays={"scale": _np.array([scale], dtype=_np.float64)},
            metadata={"state_schema": "fit_scale_rescaler_v1"},
        )

    def encode(
        self,
        view: _ViewSpan,
        fitted_state: _FittedState,
        features: _FeatureFrame,
    ) -> _RepresentationFrame:
        """Rescale each row independently using only the fixed fit-time scale."""
        if fitted_state.payload.metadata != {"state_schema": "fit_scale_rescaler_v1"}:
            raise _ContractViolationError("fitted payload is not rescaler state")
        stored = fitted_state.payload.array("scale")
        if stored.shape != (1,) or stored.dtype != _np.dtype(_np.float64):
            raise _ContractViolationError("rescaler scale must be one float64 scalar")
        scale = float(stored[0])
        if scale <= 0.0:
            raise _ContractViolationError("rescaler scale must be positive")
        output = (features.column(_FEATURE) / scale).astype(_np.float64)
        origin = _StageOrigin(self.representation_id, self.version, self.params)
        return _make_representation_frame(
            view=view,
            features=features,
            columns={_OUTPUT: output},
            origins={_OUTPUT: origin},
        )


_REPRESENTATION_MODELS.add("fit_scale_rescaler_v1", FitScaleRescaler)
```

Points worth copying:

- `encode` **verifies the state schema it received** before trusting the arrays.
- Every value in `encode` comes from `features.column(...)` at the same row, or
  from the fixed state. No `mean()`, `std()` or `cumsum()` over the frame being
  encoded.
- The `StageOrigin` is built from the plugin's own declaration; the platform
  compares it against the snapshot and refuses a mismatch.
- Arrays returned from `features.column(...)` are read-only; copy first if you
  need a mutable buffer.

---

## 9. Tests an author should write

### Already provided globally by the platform — no test to write

Registering a representation enrols it automatically in these
release-blocking rigs:

| Rig / guard | What it proves |
|---|---|
| `test_every_registered_representation_is_forward_blind` | Perturbing declared input rows strictly **after** *i* leaves output at *i* bit-for-bit unchanged. |
| `test_every_registered_representation_is_row_local` | Perturbing every row **except** *i* leaves output at *i* bit-for-bit unchanged. |
| `test_rig_coverage_is_an_exact_independent_match_for_both_live_registries` | The rig's covered set equals the live registry — you cannot register without rig coverage. |
| `test_slice_3c_reference_components_and_registry_provenance_are_exact` | Public surface, `__all__`, module public names, registry provenance. |
| `test_slice_3c_plugins_return_payloads_but_have_no_state_identity_authority` | `fit` returns `FittedPayload`, never `FittedState`; no identity-authority symbol is named. |
| `test_slice_3c_representation_chaining_remains_structurally_unavailable` | The layer never names `ModelInputs`; `fit`/`encode` take a `FeatureFrame`. |
| `test_layer_respects_its_dependency_budget` | `representations` imports only `core`. |
| Determinism verification (`verify` CLI verb) | Recomputes with caching forced off and compares canonical bytes under your declared tolerance. |

Comparison in the rigs is **exact** — NaN masks compared as bytes, values as raw
`uint64` bits. No tolerance, no `skip`, no `xfail`; an architecture guard forbids
introducing any.

### Tests the author should add — in `tests/test_models.py`

Use **synthetic data only**; never a real market value in a fixture.

- [ ] **Declaration test.** Assert the full nine-member surface exactly:
      `representation_id`, `version`, `params`, `required_feature_names`,
      `output_column_names`, `cost_hint`, `comparison_tolerance`.
- [ ] **Hand-computed golden.** A tiny frame (5–8 rows) with literal expected
      numbers — not a re-implementation of your own formula.
- [ ] **Fit/encode separation.** Fit on one frame, encode a *different* frame,
      and assert the output depends only on the stored state — e.g. encoding
      frame B after fitting on A differs from encoding B after fitting on B.
- [ ] **Shape and schema.** Output `dtype == float64`, `ndim == 1`,
      `num_rows == features.num_rows`, `column_names == output_column_names`,
      `keys` equal to the input frame's keys.
- [ ] **NaN semantics and no `±inf`.** Where the transform is undefined, assert
      `NaN`; assert no infinities anywhere (minting refuses them).
- [ ] **Alignment refusal.** A `FeatureFrame` whose columns differ from
      `required_feature_names` is refused before any computation.
- [ ] **Invalid input refusal.** An all-NaN fit frame, or a malformed fitted
      payload, raises `ContractViolationError` rather than fabricating a value.
- [ ] **Identity sensitivity.** If parameterised, assert two configurations
      produce different `params` and different fitted-state identity keys
      (`fit_representation(...).identity_key()`).
- [ ] **Registry resolution.** `"your_name_v1" in REPRESENTATION_MODELS.names()`
      and `REPRESENTATION_MODELS.create("your_name_v1")` returns your class.
- [ ] **Rigs green with the plugin registered** — run
      `uv run pytest tests/test_model_leakage.py tests/test_architecture.py -q`.
- [ ] **Full gate before merge** — `./scripts/gate.sh`, unfiltered and green.

> **Do not register until the plugin can pass the live rigs.** They are
> release-blocking: a registered representation that fails them turns the whole
> gate red for everyone. Develop against a local unregistered instance first —
> the rig helpers accept a plain object.

---

## 10. Common mistakes

| # | Anti-pattern | Why it violates the architecture | Caught by |
|---|---|---|---|
| 1 | `encode` standardizes with `values.mean()` / `values.std()` of the frame it was given | that is fitting at inference time; train and eval get different transforms | forward **and** row-locality rigs (`EvaluationRangeFitRepresentationControl`) |
| 2 | `np.cumsum(values)` in `encode` | carried state across evaluation rows; ADR-0022 §6 excludes it from V1 | row-locality rig |
| 3 | `values[i + 1]` or peeking at `values[-1]` | forward read | forward rig |
| 4 | Trying to compute a forward return inside `fit` "just for training" | representations have no target channel; forward reads belong to `OutcomeSpec` alone | ADR-0010; no parameter exists to receive one |
| 5 | Emitting `standardized` under the name `target.z` | reserved outcome namespace | frame minting refuses it |
| 6 | Returning a `FittedState`, or hashing your own identity | plugins own learned content; the platform owns identity | ADR-0022 Amendment 3; architecture guard |
| 7 | Importing `trading_research.features` to reuse a helper | cross-plugin import; the layer may import only `core` | dependency-budget test |
| 8 | Adding a constructor argument that is not in `params` | two configurations collide on one fitted-state identity | nothing automatic — this is why §5 exists |
| 9 | A plain `import numpy as np` in `reference.py` | adds `np` to module public names | exact-public-surface guard |
| 10 | Returning a frame whose `column_names` order differs from `output_column_names` | order is part of the declaration | `_encode_representation_frame` |
| 11 | Consuming a `RepresentationFrame` inside a representation | representation chaining; ADR-0006 refuses a DAG engine | no such parameter exists; architecture guard |
| 12 | Reading `features.keys` to detect a weekend or session | a discontinuity is arithmetic only | ADR-0005 (authoring rule; the rigs perturb values, not the axis) |

---

## 11. Author checklist

- [ ] The transform is genuinely **fitted preprocessing**, not a model
      (if it produces a forecast, it is a `ResearchModel`).
- [ ] `encode` is row-local: row *i* uses row *i*'s declared inputs plus fixed
      state, and nothing else.
- [ ] `fit` reads only the `FeatureFrame` it was given.
- [ ] Nine-member public surface, exactly; no extra public method or attribute.
- [ ] `representation_id` == registry name; `version` a positive `int`.
- [ ] Every semantic knob is in `params`; `params` values fit `ParamValue`.
- [ ] `required_feature_names` and `output_column_names` are ordered, unique,
      non-empty tuples; no reserved `target`/`target.` name.
- [ ] `cost_hint` declared honestly; `comparison_tolerance` is `None` unless a
      genuine reason is written down.
- [ ] `fit` returns `FittedPayload` only; no identity, no store, no path.
- [ ] `encode` mints via `_make_representation_frame` with a `StageOrigin` built
      from its own declaration.
- [ ] No I/O, no clock, no environment, no module-level mutable state, no
      unseeded randomness.
- [ ] Class constructible with zero arguments (see the §7 known constraint).
- [ ] Registered in `reference.py`; `__all__` updated; imports underscore-aliased.
- [ ] §7 guard inventories updated **by addition only**, and called out
      explicitly in the commit message or PR description.
- [ ] §9 author tests added, synthetic data only.
- [ ] `uv run pytest tests/test_model_leakage.py tests/test_models.py tests/test_architecture.py -q` green.
- [ ] `./scripts/gate.sh` green, unfiltered.
- [ ] No production file outside `representations/reference.py` modified.
- [ ] No dataset, view manifest, audit record, run directory or cache artifact
      touched.

### Stop and raise instead of working around

Halt and ask for an architecture decision if making the representation fit
requires any of:

- carried state between evaluation rows, or online/incremental learning
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §6 — the
  row-locality rig is precisely the detector for this trigger);
- a target channel, or any forward read at fit time;
- consuming another representation's output, or a second representation stage;
- a market column, a `MarketFrame` or any data-layer handle;
- a non-`float64` or sparse representation column;
- a constructor on a class in `representations/reference.py` (§7);
- relaxing, deleting or skipping any architecture guard or rig assertion;
- touching `core/`, `artifacts/`, `experiments/` or the data layer.

---

## 12. References

Canonical sources. **If this guide conflicts with any of them, they win.**

**Contracts and decisions**

- [ADR-0022 — Phase-3 research contracts](../adr/ADR-0022-phase-3-research-contracts.md) — the binding contract (§5 row-locality, §6 no carried state, §8 tolerance, Amendment 1 §2 no chaining, Amendment 3 payload/identity split)
- [ADR-0008 — Fit lifecycle and FittedState identity](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md)
- [ADR-0006 — Fixed stage pipeline](../adr/ADR-0006-fixed-stage-pipeline.md) — one optional representation stage, no DAG engine
- [ADR-0007 — MaterializationPolicy; identity key vs content hash](../adr/ADR-0007-materialization-policy.md)
- [ADR-0010 — Two-plane evaluation and OutcomeSpec](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) · [ADR-0012 — Two-tier splits](../adr/ADR-0012-two-tier-splits.md) · [ADR-0016 — Determinism tiers](../adr/ADR-0016-determinism-tiers.md) · [ADR-0021 — Columnar feature contract](../adr/ADR-0021-columnar-feature-contract.md)
- [ADR index](../adr/README.md)

**Plans and policy**

- [docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) — normative Phase-3 plan (§2 closed vocabulary, §2.4 identity field list)
- [docs/architecture/V1.md](../architecture/V1.md) §6 — research contracts
- [docs/research/RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §5 — why leakage discipline exists
- [PROJECT_STATE.md](../../PROJECT_STATE.md) · [CLAUDE.md](../../CLAUDE.md)

**Code**

- [`src/trading_research/representations/reference.py`](../../src/trading_research/representations/reference.py) — the live reference representation
- [`src/trading_research/representations/execution.py`](../../src/trading_research/representations/execution.py) — declaration snapshot, input and output validation
- [`src/trading_research/representations/registry.py`](../../src/trading_research/representations/registry.py) — `REPRESENTATION_MODELS`
- [`src/trading_research/core/interfaces.py`](../../src/trading_research/core/interfaces.py) — the `RepresentationModel` protocol
- [`src/trading_research/core/research.py`](../../src/trading_research/core/research.py) — `ViewSpan`, `StageOrigin`, `ToleranceSpec`
- [`src/trading_research/core/research_frames.py`](../../src/trading_research/core/research_frames.py) — `RepresentationFrame`, `FittedPayload`, `FittedState`
- [`src/trading_research/experiments/fitting.py`](../../src/trading_research/experiments/fitting.py) — `fit_representation`, `encode_representation`
- [`src/trading_research/artifacts/fitted.py`](../../src/trading_research/artifacts/fitted.py) — fitted-state identity and serialization

**Tests**

- [`tests/test_model_leakage.py`](../../tests/test_model_leakage.py) — release-blocking forward and row-locality rigs, and their negative controls
- [`tests/test_models.py`](../../tests/test_models.py) — lifecycle, declaration refusal, fit-view isolation
- [`tests/test_research_frames.py`](../../tests/test_research_frames.py) — carrier construction and immutability
- [`tests/test_fitted_state.py`](../../tests/test_fitted_state.py) — identity sensitivity and pickle-free round trip
- [`tests/test_architecture.py`](../../tests/test_architecture.py) — dependency budgets, exact surfaces, guard inventories
- [`scripts/gate.sh`](../../scripts/gate.sh) — the gate you must run
