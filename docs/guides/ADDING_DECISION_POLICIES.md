# Adding a DecisionPolicy — authoring guide

**Audience:** anyone adding a decision rule to this platform — a researcher who
knows how they want to act on a model's output, or a coding agent asked to
implement one safely.

**Scope:** this guide is *explanatory*. It creates no new platform rule. If
anything here disagrees with an accepted ADR or with
[`docs/phases/PHASE3_RESEARCH_RUNS.md`](../phases/PHASE3_RESEARCH_RUNS.md),
**the contract documents win** — fix the guide, not the contract. The binding
contracts are
[ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md) (the
generic-output / IntentFrame separation) and
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) (the concrete Phase-3
contract).

**Status of the code this describes:** Phase 3 is COMPLETE. Everything below is
implemented and enforced today. Nothing here is aspirational.

**Companion guides:**
[ADDING_FEATURES.md](ADDING_FEATURES.md) ·
[ADDING_REPRESENTATIONS.md](ADDING_REPRESENTATIONS.md) ·
[ADDING_RESEARCH_MODELS.md](ADDING_RESEARCH_MODELS.md) ·
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
ResearchModel.infer(...)                       -> ADDING_RESEARCH_MODELS.md
    |
    v
ModelOutputFrame            <-- ONE frame, computed ONCE per fold
    |
    v   DecisionPolicy.decide(model_output)                        THIS GUIDE
IntentFrame
```

**This is the architecture's load-bearing separation.** Research output is
generic and carries no trading vocabulary; every decision — direction, size,
abstention — happens here and only here
([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md),
[CLAUDE.md](../../CLAUDE.md)). Keeping the two apart is what lets several
decision rules be tested against one inference pass.

`OutcomeSpec` never touches this stage. The reserved outcome namespace is
**prohibited** as a `DecisionPolicy` input
([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)) — see
[ADDING_OUTCOMES.md](ADDING_OUTCOMES.md) §1.

---

## 1. What a DecisionPolicy is responsible for

A **decision policy** converts one already-computed `ModelOutputFrame` into one
`IntentFrame` under a single declared intent schema. That is its entire job.

### What enters

Exactly one thing:

```python
def decide(self, model_output: ModelOutputFrame) -> IntentFrame
```

No view, no features, no representation, no fitted state, no targets, no market
data, no fold record, no store, no path, no model object.

### What it produces

An `IntentFrame` under `intent_schema_id`. V1 implements exactly one schema,
`target_exposure_intent_v1`: a single `float64` `target_exposure` column with
values in `[-1, +1]` or `NaN`, on keys that are a subset of the model output's
keys.

### What it owns

**The decision rule.** Thresholds, mappings from a statistic to an exposure,
abstention rules, and which rows it chooses to speak on.

### What the platform owns instead

| The platform handles | What that means for you |
|---|---|
| **Inference** | The model output frame is computed once per fold, before any policy runs. You are handed the frame. |
| **Fan-out** | Every configured policy receives that same frame. The orchestrator asserts one inference call, zero re-inference calls, and one call per policy. |
| **Compatibility checking** | Your `required_output_columns` are checked against the model's declared output **before** inference, and against the real frame before you execute. |
| **Frame validation** | Schema id, axis tags, key subsetting, dtype, `±inf` and the `[-1, +1]` bound are all checked at minting and again after you return. |
| **Serialization** | The canonical intent encoding, its bit-exact round trip, and sparse alignment against a dense axis are platform code. |
| **Identity** | Your `policy_id`, `version` and canonical `params` enter the experiment fingerprint and the evaluation inventory. You declare; the platform records. |

> **Rule of thumb.** A policy is a pure function from one frame to one frame. If
> it needs anything else, it is doing someone else's job.

---

## 2. What a DecisionPolicy must NOT do

- **No re-inference.** A policy receives a frame, not a model. It has no
  reference to a `ResearchModel`, no `FittedState`, no `ModelInputs`, and no way
  to call `infer`. Multiple policies must consume one computed output with
  **zero re-inference**
  ([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)); the
  orchestrator counts the calls and an architecture test asserts the fan-out
  helper never names `infer_model`, `fit_model`, `ModelInputs`, `FittedState` or
  `ResearchModel`.
- **No research logic.** Do not fit anything, do not learn anything, do not
  compute a statistic that belongs in a model. If your rule needs to *estimate*
  something from data, that estimate belongs in a `ResearchModel` or a
  `RepresentationModel` — which is exactly why those stages have a fit
  lifecycle and this one does not.
- **No forward reads.** You cannot perform one: the only data you receive is the
  model output frame. Do not attempt to reconstruct one from the key axis.
- **No dependence on other rows** beyond what your declared inputs at that key
  provide. Nothing enforces this with a rig at this stage, so it is an
  **authoring rule**: a policy that accumulates across rows is carrying state
  the platform cannot see or identify.
- **No outcome/target access.** `required_output_columns` may not use the
  reserved `target` namespace — `_snapshot_policy` refuses it outright.
- **No market data, no features, no representation.** There is no parameter
  through which any of them could arrive, and `decisions` may import **only**
  `core` (`test_layer_respects_its_dependency_budget`).
- **No cross-plugin imports.** Not from `features`, `representations`, `models`,
  `outcomes`, `evaluation`, `experiments`, `artifacts` or `data`.
- **No filesystem, registry, store or run-directory access.** An architecture
  test asserts the fan-out module `experiments/decisions.py` imports none of
  `trading_research.artifacts`, `trading_research.data`, `fcntl`, `os`,
  `pathlib` or `shutil`, and calls none of `open`, `save`, `savez`,
  `write_bytes`, `write_text`, `put` or `replace`.
- **No new intent schema.** `intent_schema_id` must be a member of the pinned
  set — in V1 exactly `{"target_exposure_intent_v1"}`. Adding a second schema is
  an architecture decision, not a plugin addition
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) lists
  "an intent-schema registry now" under *Alternatives considered*: V1 has one
  schema and no simulator that could declare support for a second, so the
  registry "arrives when it has a job").
- **No collapsing of the trichotomy.** Absent row, `NaN` and `0.0` are three
  different statements (§5). Do not densify a sparse frame "to be tidy", and do
  not substitute `0.0` for "I have nothing to say".
- **No hidden nondeterminism.** No environment variables, wall-clock reads,
  local config files, module-level mutable state, or unseeded randomness.
- **No undeclared parameter.** Every knob that can change an emitted exposure
  must be in `params` — see §5.
- **No declaration mutation during execution.** `_execute_policy` re-snapshots
  the policy after `decide` returns and refuses if the declaration changed.

---

## 3. Exact production contract

`DecisionPolicy` in
[`core/interfaces.py`](../../src/trading_research/core/interfaces.py) is a
`typing.Protocol`; structural conformance is enough. An architecture test
asserts each reference policy's public surface is **exactly** these six names.

```python
policy_id: str                            # stable identity of this implementation
version: int                              # positive; identity-bearing
params: Mapping[str, ParamValue]          # every semantic knob, canonically encoded
required_output_columns: tuple[str, ...]  # ordered, unique; MAY be empty
intent_schema_id: str                     # V1: exactly "target_exposure_intent_v1"

def decide(self, model_output: ModelOutputFrame) -> IntentFrame: ...
```

Note what is **absent** and deliberately so: there is no `cost_hint` (a policy
produces no cached artifact of its own), no `comparison_tolerance` (intent is
structural and always compared exactly —
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §8 clause 2), and no
`fit` (a policy is stateless).

The declaration is read and validated by `_snapshot_policy` in
[`decisions/execution.py`](../../src/trading_research/decisions/execution.py).

### `policy_id`

The stable string identity. It is the registry name and it appears in the run's
policy-execution records and evaluation inventory. **Enforced:** exact non-empty
`str`.

### `version`

**Enforced:** exact Python `int`, strictly positive. Increment when the same
declaration should now produce different decisions.

### `params`

Every semantic knob. **Allowed value types** (`ParamValue`): `str`, `int`,
`float`, `bool`, `None`, nested `tuple`s of those. Canonical, type-preserving
encoding: key order irrelevant (sorted), `4` and `4.0` distinct, `True` and `1`
distinct, floats via `float.hex()`, `float('nan')` refused. **Enforced:** a
`Mapping` with non-empty string keys and `ParamValue` values.

### `required_output_columns`

The ordered `ModelOutputFrame` columns you read. **Enforced:** an exact `tuple`
of unique non-empty `str`; it **may be empty** (the reference `AbstainPolicy`
declares `()` because it reads no model values); no name may be `target` or
begin `target.`.

The platform checks these names twice: once against the model's *declared*
`output_column_names` **before inference**, so an incompatible pairing fails in
milliseconds rather than after an hour; and once against the produced frame's
real columns before `decide` runs.

### `intent_schema_id`

The one schema this policy emits. **Enforced:** must be a member of
`_INTENT_SCHEMA_IDS` in
[`core/research_frames.py`](../../src/trading_research/core/research_frames.py),
whose literal contents an architecture test pins to exactly
`{"target_exposure_intent_v1"}`.

### `decide(model_output) -> IntentFrame`

`model_output` is an exact `ModelOutputFrame`. Read it with:

| Member | Meaning |
|---|---|
| `model_output.keys` | read-only `int64`, strictly increasing; may be a **strict subset** of the evaluation axis, and may be empty |
| `model_output.column(name)` | read-only array for one column (declare it first) |
| `model_output.column_names`, `schema` | ordered names and their dtype names |
| `model_output.num_rows` | emitted-row count |
| `model_output.unit`, `clock_domain`, `source_identities` | axis tags |
| `model_output.origin(name)` | the producing `StageOrigin` |

Mint the result with the platform's private helper — the only route available to
a policy, since `IntentFrame.__init__` raises:

```python
from trading_research.core.research_frames import _make_intent_frame_from_model_output

_make_intent_frame_from_model_output(
    model_output=model_output,
    intent_schema_id=self.intent_schema_id,
    keys=...,               # subset of model_output.keys, strictly increasing
    target_exposure=...,    # float64, same length as keys
)
```

Enforced at minting and again after you return:

| Rule | Detail |
|---|---|
| **Keys** | strictly increasing, unique `int64`, and a **subset of `model_output.keys`**; may be empty |
| **`target_exposure`** | one-dimensional, exact metadata-free `float64`, same length as `keys` |
| **`±inf`** | refused |
| **Range** | every **finite** value must lie in `[-1, +1]` |
| **Schema** | the returned frame's `intent_schema_id` must equal your declaration |
| **Axis** | `unit` and `clock_domain` must match the model output |
| **Declaration** | must be unchanged after `decide` returns |

Note the asymmetry with model output: a `ModelOutputFrame` may carry any real
numeric dtype, while `target_exposure` is `float64` only.

---

## 4. Lifecycle

A decision policy is **stateless**. In this platform that has a precise meaning:

- **No fit step exists.** There is no `fit` method, no `FittedPayload`, no
  `FittedState`, and nothing about a policy is ever cached as a learned
  artifact.
- **The object holds only configuration**, fixed at construction from the
  config's `params` and never mutated. `ThresholdPolicy` uses `__slots__` and
  assigns its two fields once.
- **`decide` is a pure function of its single argument.** Calling it twice with
  the same frame must return bit-identical exposure values.
- **Nothing carries between calls** — not between folds, not between policies,
  not between runs.

Per fold, inside `_infer_once_and_fan_out` in
[`experiments/decisions.py`](../../src/trading_research/experiments/decisions.py):

```
1.  every policy's declaration is snapshotted
2.  every policy's required columns are checked against the model's DECLARED
    output columns                                   <-- before inference
3.  inference() is called EXACTLY ONCE      -> one ModelOutputFrame
4.  the frame's columns are re-checked against that pre-execution declaration
5.  every policy's compatibility is re-checked against the real frame
6.  each policy's decide() is called once, on that same frame
7.  counters are asserted: 1 inference, 0 re-inference, len(policies) decisions
```

An architecture test pins that ordering: the compatibility check must appear
before the inference call, and the inference call before the policy executions.

Every resulting `IntentFrame` enters determinism comparison
(`_intent_comparable`, always, with tolerance fixed at `None`). Whether it is
also **written to the run directory** depends on the run: a Plane-B run — one
configuring an `execution` block — persists each intent as
`fold-NNNN-intent-NNNN.npz`, while a Plane-A-only run keeps it in memory. The
canonical intent codec (`_encode_intent_frame` / `_decode_intent_frame`) proves
that the exact bit pattern of every exposure — including the sign of zero and
the `NaN` payload — survives a round trip. Downstream consumers align a sparse
intent against a dense axis with `_align_intent_to_dense_axis`, which returns
**presence and exposure as two separate arrays** precisely so absence is never
confused with `NaN`.

---

## 5. The trichotomy — the semantics you must preserve

`target_exposure_intent_v1` distinguishes three states, and every consumer in
the platform preserves the distinction
([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)
amendment):

| State | Encoding | Meaning |
|---|---|---|
| **absent** | the key is not in `keys` | this policy produced **no statement** at this key |
| **abstain** | `NaN` at that key | explicit **no new instruction**; the downstream holder keeps prior state |
| **flat** | `0.0` at that key | explicit **target flat** |

The two reference policies show both idioms:

- `ThresholdPolicy` emits a **dense** frame over every model-output key: finite
  values map to `-1.0`, `0.0` or `+1.0`; non-finite model values become `NaN`
  (explicit abstention), never `0.0`.
- `AbstainPolicy` emits a frame with the **same keys** as the model output and
  `NaN` everywhere — preserving sparse row presence while making every row an
  abstention.

> **The mistake this section exists to prevent:** substituting `0.0` when you
> mean "no opinion". `0.0` is an instruction to be flat. If you do not have an
> opinion at a key, either omit the key or emit `NaN`, and be deliberate about
> which.

---

## 6. Identity, parameters, and reproducibility

### What you declare

`policy_id`, `version` and canonical `params`. These become the policy's
`_ComponentSelection` in the resolved config, which flows into:

- the **experiment fingerprint** (via the canonical resolved-config document);
- the **component inventory** hashed into experiment identity;
- the run's **policy-execution records** and the Plane-A/Plane-B evaluation
  inventory ([ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md)).

### What the platform owns

Run identity, fingerprints, canonicality, the trial denominator and the
evaluation inventory. **Do not duplicate any of it inside your plugin** and
never compute a fingerprint or hash of your own.

Because a policy has no fitted state, it has **no fitted-state identity** —
there is nothing to cache and nothing to bind.

### The one hard rule

> **Every knob that can change an emitted exposure must be in `params`.**

Two differently-configured instances with identical declarations are
indistinguishable to the fingerprint, so two genuinely different experiments
would collide on one identity. `ThresholdPolicy` declares both of its
constructor arguments:

```python
@property
def params(self):
    return {"output_column": self._output_column, "threshold": self._threshold}
```

Note that `output_column` is in `params` **and** drives
`required_output_columns`. Both are correct: `params` is what identity records,
`required_output_columns` is what compatibility checks.

### Incorrect — invisible to identity

```python
threshold = float(os.environ["THRESHOLD"])   # environment dependence
if datetime.now().weekday() < 5: ...         # wall clock
cfg = json.load(open("mypolicy.json"))       # local config file
_SEEN.append(1); bias = len(_SEEN)           # mutable module-global state
noise = np.random.randn(n)                   # unseeded randomness
```

A stochastic policy — if one is ever justified — declares its seed as an
ordinary identity-bearing `params["seed"]`
([ADR-0007](../adr/ADR-0007-materialization-policy.md) precedent). Neither V1
reference policy is stochastic.

---

## 7. Time and information boundaries

- **The axis is `NativeTimeKey`**: `int64` values plus a `TimeUnit` and an
  opaque `ClockDomain`. The dataset clock domain is **broker wall-clock, not
  UTC** ([ADR-0002](../adr/ADR-0002-native-time-model.md)). Never attach
  `tzinfo`, never label derived time as UTC, never "correct" an offset, never
  import `datetime`.
- **A policy has no point-in-time responsibility of its own**, because it has no
  route to any data other than one already-bounded model output frame. That is
  the design: the leakage surface was closed upstream.
- **You may not widen the axis.** Intent keys must be a subset of the model
  output's keys. There is no way to emit an instruction at a key the model did
  not speak on.
- **Do not read the key values as time-of-day or calendar structure.** A key
  discontinuity is arithmetic and nothing else — never a weekend, session
  boundary, holiday or outage
  ([ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md)). If a research
  question genuinely requires a time-of-day rule, that is a declared
  `CalendarTimePolicy` question and a feature-side/architecture decision, not
  something to introduce inside a decision policy.
- **No execution semantics.** Fills, spreads, slippage, commissions and order
  types are quarantined
  ([ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md)) and belong to the
  simulator, not here. An `IntentFrame` states a target exposure; it does not
  state how it would be achieved.

---

## 8. Registration and configuration

### Registration

One line at the bottom of
[`decisions/reference.py`](../../src/trading_research/decisions/reference.py):

```python
_DECISION_POLICIES.add("your_policy_v1", YourPolicy)
```

- The **registry name** is what config uses; keep it equal to `policy_id`, as
  both reference policies do.
- The second argument is a **factory** — normally the class itself.
  `DECISION_POLICIES.create(name, **params)` calls `factory(**params)`.
  A parameterised policy is fully supported here: `ThresholdPolicy` takes
  keyword-only `output_column` and `threshold`.
- `decisions/__init__.py` imports `reference` for its registration side effect,
  so importing `trading_research.decisions` populates the registry.
- Duplicate names raise `DuplicateComponentError` at import time.
- There is **no decorator-based or automatic discovery** in use here, and no
  entry-point scanning.

### Configuration

A resolved experiment config
([`experiments/config.py`](../../src/trading_research/experiments/config.py))
carries a **non-empty** ordered `policies` array — an experiment always has at
least one policy:

```json
"policies": [
  {"name": "threshold_v1", "version": 1,
   "params": {"output_column": "score",
              "threshold": {"__float_hex__": "0x0.0p+0"}}},
  {"name": "abstain_v1", "version": 1, "params": {}}
]
```

Float params use the canonical hex form. Exact duplicates within the array are
refused at parse time.

At resolution `_create_component` constructs the component and then **re-checks**
that the live object's `policy_id`, `version` and canonical `params` match the
config exactly; a mismatch is a contract error. `_preflight_components` then
refuses any policy whose `required_output_columns` are not all in the selected
model's `output_column_names`.

### Architecture guards you must update

Registering a policy necessarily updates pinned inventories, and the tests fail
until you do. **Only add names.**

| File | Constant / test | Change needed |
|---|---|---|
| `tests/test_architecture.py` | `SLICE_3F_POLICY_CLASSES` | add `"registry_name": "ClassName"` |
| `src/.../decisions/reference.py` | `__all__` | add the class name (sorted) |

Two guard properties constrain *how* you write the class:

- **Module public names must be exactly the class names plus `annotations`.**
  That is why every import in `decisions/reference.py` is underscore-aliased
  (`import numpy as _np`, `... import IntentFrame as _IntentFrame`). A plain
  `import numpy as np` fails
  `test_slice_3f_policy_modules_registry_and_schema_vocabulary_are_exact`.
- **The file inventory is pinned** to `__init__.py`, `execution.py`,
  `reference.py`, `registry.py` (`SLICE_3F_DECISION_INVENTORY`). Do not add a
  module to this layer.

Unlike the model and representation layers, there is **no guard forbidding a
constructor** on a decision-policy class, so a parameterised policy needs no
architecture change.

---

## 9. Minimal working example

Deliberately simple and strategy-agnostic: emit a long-only exposure at rows
whose declared model column exceeds a configured level, abstain elsewhere. It is
not a trading strategy; it exists to show the contract. It mirrors the style of
[`decisions/reference.py`](../../src/trading_research/decisions/reference.py),
including the underscore-aliased imports required by §8.

Assume this module preamble (already present in `reference.py`):

```python
from __future__ import annotations

import math as _math
import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import _make_intent_frame_from_model_output
from trading_research.decisions.registry import DECISION_POLICIES as _DECISION_POLICIES

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    from trading_research.core.features import ParamValue as _ParamValue

_INTENT_SCHEMA_ID = "target_exposure_intent_v1"
```

```python
class LongOnlyThresholdPolicy:
    """Target full long exposure above one level and abstain elsewhere."""

    __slots__ = ("_level", "_output_column")

    def __init__(self, *, output_column: str, level: float) -> None:
        if type(output_column) is not str or not output_column:
            raise _ContractViolationError("output_column must be non-empty")
        if type(level) is not float or not _math.isfinite(level):
            raise _ContractViolationError("level must be a finite Python float")
        self._output_column = output_column
        self._level = level

    @property
    def policy_id(self) -> str:
        """Return the stable policy identity."""
        return "long_only_threshold_v1"

    @property
    def version(self) -> int:
        """Return the policy contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the complete identity-bearing parameters."""
        return {"level": self._level, "output_column": self._output_column}

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        """Return the sole generic model-output input."""
        return (self._output_column,)

    @property
    def intent_schema_id(self) -> str:
        """Return the sole V1 intent schema identifier."""
        return _INTENT_SCHEMA_ID

    def decide(self, model_output: _ModelOutputFrame) -> _IntentFrame:
        """Map one generic column to long-or-abstain, preserving row presence."""
        values = model_output.column(self._output_column)
        finite = _np.isfinite(values)
        exposure = _np.full(values.shape, _np.nan, dtype=_np.float64)
        exposure[finite] = 0.0
        exposure[finite & (values > self._level)] = 1.0
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=self.intent_schema_id,
            keys=model_output.keys,
            target_exposure=exposure,
        )


_DECISION_POLICIES.add("long_only_threshold_v1", LongOnlyThresholdPolicy)
```

Points worth copying:

- **Non-finite model values become `NaN`, not `0.0`.** The model said nothing
  usable; the policy abstains rather than instructing flat.
- **Finite values below the level become an explicit `0.0`** — that is a
  deliberate "target flat" statement, and it is different from abstaining.
- The constructor validates types exactly (`type(x) is not float`), matching the
  repository's strictness convention; `bool` is not silently accepted as `int`.
- The keys passed to the minter come straight from `model_output.keys`, so row
  presence is preserved exactly.
- Arrays returned by `.column(...)` are read-only; `exposure` is a fresh buffer.

### Emitting a sparse intent

To speak on a subset of rows instead, pass a subset of the model output's keys:

```python
selected = _np.isfinite(values) & (values > self._level)
return _make_intent_frame_from_model_output(
    model_output=model_output,
    intent_schema_id=self.intent_schema_id,
    keys=model_output.keys[selected],
    target_exposure=_np.ones(int(selected.sum()), dtype=_np.float64),
)
```

That says "no statement" at the unselected keys — a different claim from the
dense version above, which says "explicitly flat" there. Choose deliberately and
document which you meant.

---

## 10. Tests an author should write

### Already provided globally by the platform — no test to write

| Guard / test | What it proves |
|---|---|
| `test_slice_3f_policy_modules_registry_and_schema_vocabulary_are_exact` | Layer inventory, `__all__`, module public names, exact six-member surface, registry provenance, and the pinned single intent-schema literal. |
| `test_slice_3f_fan_out_has_one_inference_call_and_no_storage_authority` | Fan-out calls inference once, checks compatibility first, and has no storage authority. |
| `test_n_policy_fan_out_performs_one_inference_and_zero_reinference` | N policies, one inference, zero re-inference. |
| `test_all_policy_compatibility_is_checked_before_any_policy_executes` | An incompatible policy fails before any policy runs. |
| `test_canonical_round_trip_preserves_absent_abstain_and_flat_bits` | The trichotomy survives encoding bit-for-bit. |
| `test_sparse_consumer_alignment_keeps_presence_separate_from_nan` | Presence and exposure stay separate arrays downstream. |
| `test_unknown_intent_schema_is_refused` | An unpinned schema id is rejected. |
| `test_target_exposure_refuses_out_of_contract_values` | Out-of-range and infinite exposures are refused. |
| `test_layer_respects_its_dependency_budget` | `decisions` imports only `core`. |
| Determinism verification (`verify` CLI verb) | Intent participates in exact comparison. |

### Tests the author should add — in `tests/test_decisions.py`

Use **synthetic data only**; never a real market value in a fixture.

- [ ] **Declaration test.** Assert the full six-member surface exactly:
      `policy_id`, `version`, `params`, `required_output_columns`,
      `intent_schema_id`.
- [ ] **Hand-computed golden.** A tiny model output frame (5–8 rows) with
      literal expected exposures.
- [ ] **Trichotomy test.** Assert explicitly which rows are absent, which are
      `NaN`, and which are `0.0`, and that a non-finite model value never becomes
      `0.0`.
- [ ] **Sparse-input behaviour.** Feed a sparse `ModelOutputFrame` and assert the
      emitted keys are exactly the subset you intend, and remain a subset of the
      input keys.
- [ ] **Empty-input behaviour.** A zero-row `ModelOutputFrame` must produce a
      zero-row `IntentFrame` without fabricating bounds.
- [ ] **Determinism.** `decide` twice on the same frame → bit-identical
      exposures (compare `.view(np.uint64).tobytes()` and the NaN mask).
- [ ] **Parameter identity.** Assert `params` reflects every constructor
      argument, and that two configurations produce different `params`.
- [ ] **Configuration refusal.** Invalid constructor arguments (wrong type,
      non-finite, empty string) raise `ContractViolationError`.
- [ ] **Compatibility refusal.** A policy declaring a column the model does not
      emit is refused before execution.
- [ ] **Separation from research logic.** Assert `decide` is a pure function of
      its argument — no attribute of the policy changes across calls.
- [ ] **Registry resolution.** `"your_policy_v1" in DECISION_POLICIES.names()`
      and `DECISION_POLICIES.create("your_policy_v1", **params)` returns your
      class.
- [ ] **Architecture guards green** —
      `uv run pytest tests/test_architecture.py -q` after the §8 inventory
      update.
- [ ] **Full gate before merge** — `./scripts/gate.sh`, unfiltered and green.

---

## 11. Common mistakes

| # | Anti-pattern | Why it violates the architecture | Caught by |
|---|---|---|---|
| 1 | Emitting `0.0` where the policy has no opinion | destroys the abstain/flat distinction | not machine-detectable — this is why §5 exists |
| 2 | Densifying a sparse model output to "fill the gaps" | absence is a statement; filling it fabricates one | your own trichotomy test |
| 3 | Fitting a threshold from the frame's own distribution (e.g. its 90th percentile) | research logic inside a policy; the estimate is unidentified and unfitted | authoring rule — a policy has no fit lifecycle for a reason |
| 4 | Holding a reference to the model to "just re-run it" | re-inference; policies must share one computed frame | no such parameter exists; fan-out guard and counters |
| 5 | Declaring `required_output_columns = ("target.forward_log_return",)` | the reserved outcome namespace is prohibited as a policy input | `_snapshot_policy` refuses it |
| 6 | Emitting exposure `2.0`, or `inf` | outside the declared `[-1, +1]` contract | intent minting refuses it |
| 7 | Emitting a key the model did not produce | intent keys must be a subset of model output keys | `_make_intent_frame_from_model_output` and `_execute_policy` |
| 8 | Inventing `"order_intent_v1"` as `intent_schema_id` | V1 pins exactly one schema; a family extension is an ADR | schema validation and the pinned-literal guard |
| 9 | Mutating `self._threshold` inside `decide` | declaration mutation during execution | `_execute_policy` re-snapshot |
| 10 | A constructor argument absent from `params` | two configurations collide on one experiment fingerprint | nothing automatic — this is why §6 exists |
| 11 | A plain `import numpy as np` in `reference.py` | adds `np` to module public names | exact-public-surface guard |
| 12 | Accumulating exposure across rows | carried state the platform cannot identify | authoring rule (no rig covers this stage) |
| 13 | Deciding a fill price, spread or lot size | execution semantics are quarantined | ADR-0020; intent states exposure only |

---

## 12. Author checklist

- [ ] The component is genuinely a **decision rule** — it contains no estimation
      that belongs in a model or representation.
- [ ] Six-member public surface, exactly; no extra public method or attribute.
- [ ] `policy_id` == registry name; `version` a positive `int`.
- [ ] Every knob is in `params`; `params` values fit `ParamValue`.
- [ ] `required_output_columns` are ordered, unique, non-reserved names actually
      emitted by the intended model — or deliberately `()`.
- [ ] `intent_schema_id` is `"target_exposure_intent_v1"`.
- [ ] `decide` is a pure function of its one argument; nothing on the object
      changes.
- [ ] Absent / `NaN` / `0.0` are used deliberately and documented in the tests.
- [ ] Finite exposures lie in `[-1, +1]`; no `±inf`; `float64` only.
- [ ] Intent keys are a subset of the model output's keys.
- [ ] Minted via `_make_intent_frame_from_model_output`.
- [ ] No I/O, no clock, no environment, no module-level mutable state, no
      unseeded randomness.
- [ ] Registered in `reference.py`; `__all__` updated; imports underscore-aliased.
- [ ] §8 guard inventory updated **by addition only**, and called out explicitly
      in the commit message or PR description.
- [ ] §10 author tests added, synthetic data only.
- [ ] `uv run pytest tests/test_decisions.py tests/test_architecture.py -q` green.
- [ ] `./scripts/gate.sh` green, unfiltered.
- [ ] No production file outside `decisions/reference.py` modified.
- [ ] No dataset, view manifest, audit record, run directory or cache artifact
      touched.

### Stop and raise instead of working around

Halt and ask for an architecture decision if the policy requires any of:

- a second intent schema, or any field beyond `target_exposure`;
- fitted or learned state of its own;
- access to features, representations, market data, outcomes or a model object;
- re-running inference, or a second inference pass;
- state carried between rows, folds or runs;
- execution, cost or order semantics;
- relaxing, deleting or skipping any architecture guard or test assertion;
- touching `core/`, `artifacts/`, `experiments/` or the data layer.

---

## 13. References

Canonical sources. **If this guide conflicts with any of them, they win.**

**Contracts and decisions**

- [ADR-0009 — Generic model output and the IntentFrame family](../adr/ADR-0009-generic-output-and-intentframe-family.md) — the binding output/intent separation and the sparsity amendment
- [ADR-0022 — Phase-3 research contracts](../adr/ADR-0022-phase-3-research-contracts.md) — §3 intent trichotomy, §8 exactness of structural artifacts
- [ADR-0006 — Fixed stage pipeline](../adr/ADR-0006-fixed-stage-pipeline.md) · [ADR-0010 — Two-plane evaluation and OutcomeSpec](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) · [ADR-0011 — Run identity and canonical runs](../adr/ADR-0011-run-identity-and-canonical-runs.md) · [ADR-0020 — Cost-semantics quarantine](../adr/ADR-0020-cost-semantics-quarantine.md)
- [ADR index](../adr/README.md)

**Plans and policy**

- [docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) — normative Phase-3 plan (§2 closed vocabulary, §3 slice 3F)
- [docs/architecture/V1.md](../architecture/V1.md) §6 — research contracts
- [docs/research/RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md)
- [PROJECT_STATE.md](../../PROJECT_STATE.md) · [CLAUDE.md](../../CLAUDE.md)

**Code**

- [`src/trading_research/decisions/reference.py`](../../src/trading_research/decisions/reference.py) — the two live reference policies
- [`src/trading_research/decisions/execution.py`](../../src/trading_research/decisions/execution.py) — declaration snapshot, compatibility, result validation
- [`src/trading_research/decisions/registry.py`](../../src/trading_research/decisions/registry.py) — `DECISION_POLICIES`
- [`src/trading_research/core/interfaces.py`](../../src/trading_research/core/interfaces.py) — the `DecisionPolicy` protocol
- [`src/trading_research/core/research_frames.py`](../../src/trading_research/core/research_frames.py) — `IntentFrame`, `ModelOutputFrame`, `_INTENT_SCHEMA_IDS`
- [`src/trading_research/experiments/decisions.py`](../../src/trading_research/experiments/decisions.py) — fan-out, canonical intent codec, sparse alignment

**Tests**

- [`tests/test_decisions.py`](../../tests/test_decisions.py) — trichotomy, fan-out, round trip, alignment, refusal paths
- [`tests/test_research_frames.py`](../../tests/test_research_frames.py) — carrier construction and immutability
- [`tests/test_architecture.py`](../../tests/test_architecture.py) — dependency budgets, exact surfaces, guard inventories
- [`scripts/gate.sh`](../../scripts/gate.sh) — the gate you must run
