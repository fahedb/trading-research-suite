# Adding an OutcomeSpec — authoring guide

**Audience:** anyone defining what a research result is *measured against* in
this platform — a researcher who knows the outcome definition they want, or a
coding agent asked to implement one safely.

**Scope:** this guide is *explanatory*. It creates no new platform rule. If
anything here disagrees with an accepted ADR or with
[`docs/phases/PHASE3_RESEARCH_RUNS.md`](../phases/PHASE3_RESEARCH_RUNS.md),
**the contract documents win** — fix the guide, not the contract. The binding
contracts are
[ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) (two-plane
evaluation and the versioned OutcomeSpec) and
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §9–§10 (V1 boundary and
alignment semantics).

**Status of the code this describes:** Phase 3 is COMPLETE. Everything below is
implemented and enforced today. Nothing here is aspirational.

> **Read §8 before you start.** V1 deliberately ships **exactly one**
> `OutcomeSpec`, and an architecture test pins that inventory to one class and
> one registry name. Adding a second is an architecture-boundary decision, not a
> routine plugin addition. This guide documents the contract so that decision
> can be implemented correctly when it is taken — it is not permission to take
> it.

**Companion guides:**
[ADDING_FEATURES.md](ADDING_FEATURES.md) ·
[ADDING_REPRESENTATIONS.md](ADDING_REPRESENTATIONS.md) ·
[ADDING_RESEARCH_MODELS.md](ADDING_RESEARCH_MODELS.md) ·
[ADDING_DECISION_POLICIES.md](ADDING_DECISION_POLICIES.md)

---

## 0. Where this stage sits — and where it does **not**

An `OutcomeSpec` is **not** part of the forward research chain. It is not a
model input, not a feature input, and not a decision input. It sits beside the
chain and feeds two distinct, platform-controlled destinations.

```
market data
    |
    v   FeatureProvider -> FeatureFrame
    v   RepresentationModel -> RepresentationFrame (optional)
    v   ResearchModel.infer -> ModelOutputFrame
    v   DecisionPolicy.decide -> IntentFrame
```

```
                 OutcomeSpec  (the ONLY component permitted to read forward)
                       |
     +-----------------+------------------------+
     |                                          |
     v  TRAINING-TARGET USE (fit time)          v  EVALUATION USE (Plane A)
config names ONE training OutcomeSpec      config names 0..N evaluation
     |                                          OutcomeSpecs
orchestrator computes, per fold,                |
over the fold's TRAINING view              orchestrator computes, once per run,
     |                                     over the run's OVERALL view
     v                                          |
TrainingTargetFrame                             v
     |                                     OutcomeFrame
     v                                          |
ResearchModel.fit(view, inputs,                 v
                  training_targets)        Evaluator.evaluate(model_output,
                                                              outcome_targets)
```

Three facts follow, and every one of them is structural:

1. **`TrainingTargetFrame` and `OutcomeFrame` are distinct types** even though
   they are structurally identical. `fit` accepts only the first; an `Evaluator`
   accepts only the second. Evaluation-range targets therefore cannot reach
   `fit`, and training-range targets cannot reach an evaluator, without any
   runtime role check ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md)
   Amendment 1 §3).
2. **The spec never chooses which carrier it produces.** It returns a plain
   mapping of arrays; the platform wraps it according to which range it
   requested.
3. **The reserved outcome namespace is prohibited** as a `FeatureProvider`
   input, as an inference input, and as a `DecisionPolicy` input
   ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)). The
   fit-time channel above is the single, controlled exception.

---

## 1. What an OutcomeSpec is responsible for

An **outcome spec** defines a forward-looking measurement: for each decision
row, a number derived from rows that come *after* it.

### What enters

```python
def compute(self, request: OutcomeRequest) -> Mapping[str, NDArray[float64]]
```

`OutcomeRequest` is minted only by the platform's outcome path and exposes:

| Member | Meaning |
|---|---|
| `request.keys` | read-only `int64` — the **complete resolved axis** of the already-authorized source frame. It deliberately extends **beyond** the decision rows; that forward asymmetry is what makes this a distinct type. |
| `request.column(name)` | read-only array for one **declared** market column, over the same resolved axis. Any other name raises. |
| `request.decision_offset` | index into `request.keys` of the **first row targets are requested for**. |
| `request.decision_length` | number of consecutive decision rows — the exact length of every array you must return. |

Nothing else. No `ViewSpan`, no `FeatureFrame`, no `ModelOutputFrame`, no
loader, no grant, no path.

### What it produces

A mapping from **reserved-namespace** target column names to dense `float64`
arrays of length `decision_length`.

### What it owns

**The measurement definition, and the arithmetic that realises it** — horizon,
origin, reference price, and what happens when the horizon does not complete.

### What the platform owns instead

| The platform handles | What that means for you |
|---|---|
| **Loading and authorization** | The source `MarketFrame` is already loaded, integrity-verified and authorized. You never see a file, grant, dataset path or split policy. |
| **Where the forward axis ends** | `request.keys` stops at the end of the authorized frame. You cannot read past it even if you try. |
| **Which rows to compute for** | `decision_offset` / `decision_length` are chosen by the orchestrator from the training or evaluation range. |
| **Which carrier is produced** | `TrainingTargetFrame` or `OutcomeFrame`, never your choice. |
| **The fold purge** | Derived from your declared `decision_delay_rows + horizon_rows`; it is what stops a training target's span from reaching into the evaluation range. |
| **Identity and persistence** | Origins, artifact identity keys and run-directory writes are platform-side. |

---

## 2. What an OutcomeSpec must NOT do

- **No reading past your declared reach.** You may read forward exactly
  `decision_delay_rows + horizon_rows` observed rows from each decision row.
  Reading one row further is an over-read, and the forward-reach rig catches it
  ([`tests/test_outcome_leakage.py`](../../tests/test_outcome_leakage.py),
  `OverreachOutcomeControl`).
- **No truncation.** A horizon that runs off the end of the data is **not**
  shortened to the last available row. A seven-row return is a different random
  variable from a declared twenty-row return, not a shorter version of it
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §9). Emit `NaN`.
  `TruncatingOutcomeControl` exists to prove the golden catches it.
- **No session, weekend, holiday, closure or outage semantics.** An observed-key
  discontinuity is an arithmetic fact and nothing more
  ([ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md)). Refusing to
  measure across a discontinuity would itself be such an inference —
  `ClosureInferringOutcomeControl` is the negative control for it. An
  architecture test scans the whole `outcomes` layer source for the words
  `closure`, `holiday`, `outage`, `session` and `weekend`, and for the
  identifiers `CalendarTimePolicy`, `GapIndex`, `GapPolicy`, `datetime`,
  `timedelta` and `timezone` — **do not put any of them in the source file**,
  not even in a comment or docstring.
- **No `GapPolicy`.** The feature-window `GapPolicy` (`CROSS | BREAK`) and the
  outcome `BoundaryPolicy` govern different axes and must never share an enum
  ([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §9).
- **No alignment drift.** Shifting a computed value onto a different decision
  row, or using the decision row itself as the measurement origin when you
  declared a delay, is an arithmetic bug that changes what is being measured
  (`AlignmentInversionOutcomeControl`, `OffByOneOutcomeControl`).
- **No reading the reserved namespace.** `required_columns` may not contain
  `target` or a name beginning `target.`; `_make_outcome_request` refuses it.
- **No direct data access.** No `open()`, no loader, no `AccessGrant`, no
  `DatasetRegistry`, no `MarketFrame`. `outcomes` may import **only** `core`
  (`test_layer_respects_its_dependency_budget`).
- **No cross-plugin imports.** Not from `features`, `representations`, `models`,
  `decisions`, `evaluation`, `experiments`, `artifacts` or `data`.
- **No knowledge of models, policies or decisions.** An outcome definition must
  be independent of what is being evaluated; an architecture test also forbids
  `Direction` anywhere in this layer.
- **No filesystem, registry or run-directory access.**
- **No hidden nondeterminism.** No environment variables, wall-clock reads,
  local config files, module-level mutable state, or unseeded randomness.
- **No undeclared parameter.** Every research-definition choice must be an
  explicit constructor keyword **and** appear in `params` — see §5.
- **No hidden default.** An architecture test asserts the reference spec's
  constructor has no default for any of its four research-definition arguments.
- **No fabricated value.** Where the measurement is undefined, emit `NaN` or
  refuse — never substitute `0.0`.

---

## 3. Exact production contract

`OutcomeSpec` in
[`core/interfaces.py`](../../src/trading_research/core/interfaces.py) is a
`@runtime_checkable typing.Protocol`; `_validated_spec` performs an
`isinstance` check before anything else. An architecture test asserts the
reference spec's public surface is **exactly** these eleven names.

```python
outcome_id: str                            # stable identity of this implementation
version: int                               # positive; identity-bearing
params: Mapping[str, ParamValue]           # every research choice, canonically encoded
horizon_rows: int                          # > 0, explicit, no default
decision_delay_rows: int                   # V1: exactly 1, explicit, no default
reference_price_column: str                # explicit, no default
boundary_policy: BoundaryPolicy            # V1: BoundaryPolicy.NAN
required_columns: tuple[str, ...]          # ordered, unique, NON-EMPTY market columns
target_column_names: tuple[str, ...]       # ordered, unique, NON-EMPTY, ALL reserved
comparison_tolerance: ToleranceSpec | None # None means exact; the default

def compute(self, request: OutcomeRequest) -> Mapping[str, NDArray[float64]]: ...
```

Validation lives in `_validated_spec` in
[`outcomes/execution.py`](../../src/trading_research/outcomes/execution.py) and
runs on **every** use — request construction, execution, and purge derivation.

### `outcome_id`

The stable string identity. It is the registry name, it appears in every target
column's `StageOrigin`, and it is hashed into fitted-state identity when the
spec is used as a training outcome. **Enforced:** exact non-empty `str`.

### `version`

**Enforced:** exact Python `int`, strictly positive. Increment when the same
declaration should now produce different numbers. An outcome definition is a
versioned, comparable research asset — treat renaming and re-versioning with the
seriousness that implies.

### `params`

Every research-definition choice, with **no hidden default**. The reference spec
declares all four of its constructor arguments:

```python
{"boundary_policy": ..., "decision_delay_rows": ...,
 "horizon_rows": ..., "reference_price_column": ...}
```

**Allowed value types** (`ParamValue`): `str`, `int`, `float`, `bool`, `None`,
nested `tuple`s of those. Canonical, type-preserving encoding: key order
irrelevant (sorted), `4` and `4.0` distinct, `True` and `1` distinct, floats via
`float.hex()`, `float('nan')` refused. Enum values are declared by their `.value`
string (`boundary_policy.value`), which is what the config carries and what
`_create_component` converts back.

### `horizon_rows`

The measurement span, **counted in observed rows** — not minutes, not bars of
wall-clock time. **Enforced:** exact Python `int`, strictly positive.

### `decision_delay_rows`

Rows between the decision row and the measurement origin. **Enforced:** exact
Python `int`, and in V1 **exactly `1`** — both in the reference constructor and
in the shared `_validated_spec`.

`X → X+1` is the **owner-approved V1 alignment policy**, a deliberate
research-definition choice recorded in
[ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §10. It is explicitly
**not** a leakage rule and **not** an execution model. No document may state that
an `X`-origin outcome inherently reads the future, and no fill, spread,
commission or order semantics may be imported to justify it. If a future
research family wants a different alignment, it changes this identity-bearing
parameter under the normal reviewed process — and the `!= 1` refusal in
`_validated_spec` is part of what that process must revisit.

### `reference_price_column`

The market column the measurement is defined on. **Enforced:** non-empty `str`,
not in the reserved namespace.

### `boundary_policy`

What happens when the required terminal position does not exist on the resolved
axis. `BoundaryPolicy` is a `StrEnum` whose **only V1 member is `NAN`**.
`truncate` and `skip` remain reserved
[ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) vocabulary,
unimplemented in V1: a config naming one is refused with
`"outcome boundary_policy is unsupported"`. **Enforced:** exact `BoundaryPolicy`
member.

| Trigger | V1 behaviour |
|---|---|
| **End-of-data boundary** — the required terminal position does not exist | target is **`NaN` / unavailable** |
| **Observed-key discontinuity inside the forward span** | **does not fire.** The horizon is counted in observed rows, so a discontinuity never makes it incomplete |

### `required_columns`

The exact market columns you may read. **Enforced:** exact non-empty `tuple` of
unique non-empty `str`, none in the reserved namespace. `request.column()`
refuses every other name. The orchestrator adds these to the set of columns it
opens the view with, so declaring a column the dataset does not carry fails at
load.

### `target_column_names`

The ordered target columns you emit. **Enforced:** exact non-empty `tuple` of
unique non-empty `str`, and **every** name must be exactly `target` or begin
`target.` — the inverse of the rule that applies to features, representations
and model output. The mapping you return must have exactly these keys in exactly
this order.

The reserved predicate is exact, narrow and case-sensitive:

| Name | Reserved? |
|---|---|
| `target` | **yes** |
| `target.forward_log_return` | **yes** |
| `Target`, `TARGET.x`, `target_value`, `targets` | no — and therefore **refused** as a target name |

### `comparison_tolerance`

`None` (exact — the default requiring no declaration) or a frozen
`ToleranceSpec(atol, rtol, reason)`. It is **identity-bearing**: it is recorded
in artifact metadata, flows into fitted-state identity when the spec is a
training outcome, and is applied when the determinism verifier compares the
persisted outcome artifact. It is never a CLI or verifier flag
([ADR-0022](../adr/ADR-0022-phase-3-research-contracts.md) §8). The V1 reference
spec declares `None`; start there.

### `compute(request) -> Mapping[str, NDArray[float64]]`

The index relationship you must internalise:

```
output row j            (0 <= j < request.decision_length)
  decision position p  = request.decision_offset + j
  origin position      = p + decision_delay_rows
  terminal position    = p + decision_delay_rows + horizon_rows

if terminal position >= request.keys.size  ->  emit NaN for row j
```

Output rules, all enforced by `_execute_outcome`:

| Rule | Detail |
|---|---|
| **Keys and order** | `tuple(result) == target_column_names` exactly |
| **Dtype** | exact metadata-free `numpy.float64` — not `float32`, not `int64` |
| **Dimension** | `values.ndim == 1` |
| **Length** | `values.shape[0] == request.decision_length` |
| **`±inf`** | refused |
| **`NaN`** | means "unavailable at this decision key" — the only way to say "no value here" |
| **Detachment** | the platform copies each array before minting; do not rely on returning a view |

---

## 4. Lifecycle

An `OutcomeSpec` is **stateless**. In this platform that has a precise meaning:

- **No fit step exists.** No `fit`, no `FittedPayload`, no `FittedState`,
  nothing cached as learned content.
- **The object holds only configuration**, fixed at construction from the
  config's `params` and never mutated.
- **`compute` is a pure function of its single argument.** Calling it twice with
  the same request must return bit-identical arrays.
- **Nothing carries between calls** — not between folds, not between the
  training and evaluation uses, not between runs.

### Training-target use — per fold, over the fold's training view

`_training_targets_for_fold` in
[`experiments/orchestration.py`](../../src/trading_research/experiments/orchestration.py):

```
1. compute_training_targets(source_frame, fold.training_view, training_outcome)
2. restrict the result to the fold's EFFECTIVE FIT keys (post purge and embargo)
3. hand the TrainingTargetFrame to ResearchModel.fit(...)
```

The config names **at most one** training outcome (`training_outcome`, or
`null`). A stateless or unsupervised model omits the channel entirely.

**This is where the purge comes from.** `_forward_rows(spec)` returns exactly
`decision_delay_rows + horizon_rows`; fold generation derives

```
derived_purge_rows = min(fit_rows, max forward_rows over the training outcome)
```

and removes that many rows from the **end of the fit window**, so a training
target's forward span cannot reach into the evaluation range. Only the
**training** outcome contributes to this derivation — evaluation outcomes never
enter a fit and therefore need no purge. A purge override exists but requires an
explicit recorded reason and is itself identity-bearing
([ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 1,
[PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) §2.7.1).

> **Under-declaring `horizon_rows` or `decision_delay_rows` shrinks the purge.**
> That is a silent, favourable-direction bias in every backtest that uses the
> outcome — a release blocker, not a cleanup item.

### Evaluation use — once per run, over the run's overall view

`_evaluation_targets` computes each configured evaluation outcome **once** over
the whole run's `ViewSpan`, persists it as `outcome-NNNN.npz`, and registers it
for determinism comparison under the spec's declared tolerance. Evaluators then
consume that `OutcomeFrame` together with the model output.

The config carries an ordered `evaluation_outcomes` array which may be empty.
Config parsing enforces that `evaluation_outcomes` and `evaluators` are **either
both non-empty or both empty** — a descriptive run that ends before any
evaluation is legitimate and recorded, but is not a statistical evaluation
([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md),
[ADR-0011](../adr/ADR-0011-run-identity-and-canonical-runs.md)).

### The same spec in both roles

One spec instance may be named as the training outcome *and* as an evaluation
outcome; those are two separate config entries and two separate computations
over two different ranges. The type system keeps the products apart.

---

## 5. Identity, parameters, and reproducibility

### What you declare

`outcome_id`, `version`, canonical `params` and `comparison_tolerance`. As a
`StageOrigin` these are attached to every target column you produce, and they
flow into:

- the **experiment fingerprint** (via the canonical resolved-config document);
- the **outcome artifact identity key**, together with the view document;
- **fitted-state identity**, when the spec is the training outcome — recording
  the id, version, canonical params, the resulting key range (first key, last
  key, row count, SHA-256 of the key bytes) and the alignment params.

That last point is what makes
[ADR-0008](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md)'s rule mechanical:
**two states trained on the same bars under different target definitions can
never share an identity.** Note that identity pins the target *definition and
range*, not the target *values*.

### What the platform owns

Artifact identity keys, run identity, the evaluation inventory and the trial
denominator. **Do not duplicate any of it inside your plugin** and never compute
a hash of your own.

### The one hard rule

> **Every research-definition choice must be an explicit constructor keyword
> with no default, and must appear in `params`.**

Two differently-configured instances with identical declarations are
indistinguishable to identity, so a fitted state trained under one horizon could
be served for another. The reference spec's four keyword-only arguments — with
`kw_defaults == [None, None, None, None]`, asserted by
`test_slice_3b_constructor_has_no_hidden_outcome_default` — are the pattern to
follow.

### Incorrect — invisible to identity

```python
horizon = int(os.environ["HORIZON"])         # environment dependence
if datetime.now().weekday() < 5: ...         # wall clock (also a forbidden identifier here)
cfg = json.load(open("myoutcome.json"))      # local config file
_SEEN.append(1); bias = len(_SEEN)           # mutable module-global state
noise = np.random.randn(n)                   # unseeded randomness
```

---

## 6. Time and information boundaries

- **The axis is `NativeTimeKey`**: `int64` values plus a `TimeUnit` and an
  opaque `ClockDomain`. The dataset clock domain is **broker wall-clock, not
  UTC** ([ADR-0002](../adr/ADR-0002-native-time-model.md)). Never attach
  `tzinfo`, never label derived time as UTC, never "correct" an offset. The
  identifiers `datetime`, `timedelta` and `timezone` are forbidden in this layer
  outright.
- **Horizons are counted in observed rows, never in wall-clock duration.** A
  twenty-row horizon spans twenty observed rows regardless of the key deltas
  between them.
- **Forward reading is quarantined here and nowhere else.** This is the only
  component in the platform permitted to read forward of decision time
  ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md),
  [V1.md](../architecture/V1.md) §6). An architecture test enumerates the exact
  files permitted to name `OutcomeRequest` — `core/__init__.py`,
  `core/interfaces.py`, `core/research.py`, `outcomes/execution.py`,
  `outcomes/reference.py`, `experiments/outcomes.py` — and fails if the type
  escapes them.
- **Your forward reach is bounded twice**: by your own declared
  `decision_delay_rows + horizon_rows`, and physically by the end of
  `request.keys`, which is the end of the already-authorized source frame.
- **The fit-time exception is controlled, not general.** The permission to read
  forward exists so that supervised fitting is possible through *one audited
  component*. It is not permission for arbitrary future reads anywhere else:
  features, representations, models at inference time, and decision policies all
  remain forward-blind, and a model computing its own forward value inside `fit`
  is a leakage bug ([RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §5).
- **A key discontinuity has no meaning beyond arithmetic.** Do not skip, blank
  or reinterpret a measurement because the keys are not evenly spaced.
- **Do not read backwards.** The reference spec touches only the origin and
  terminal positions; a rig asserts that perturbing rows *before* the origin
  leaves the target bit-for-bit unchanged.

---

## 7. Minimal working example

> This example is written against the current contract and is **illustrative**.
> Registering a second `OutcomeSpec` today requires the architecture change
> described in §8 — read that section first.

Deliberately simple and strategy-agnostic: the **simple** (arithmetic) forward
return over a complete observed-row horizon, alongside the existing log return.
It mirrors the style of
[`outcomes/reference.py`](../../src/trading_research/outcomes/reference.py),
including the underscore-aliased imports and the deliberate absence of any
calendar vocabulary.

Assume this module preamble (already present in `reference.py`):

```python
from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.research import BoundaryPolicy as _BoundaryPolicy
from trading_research.core.research import OutcomeRequest as _OutcomeRequest
from trading_research.core.research import ToleranceSpec as _ToleranceSpec

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
```

```python
_SIMPLE_TARGET = "target.forward_simple_return"


class ForwardSimpleReturn:
    """Arithmetic price return over an explicit complete observed-row horizon."""

    def __init__(
        self,
        *,
        horizon_rows: int,
        decision_delay_rows: int,
        reference_price_column: str,
        boundary_policy: _BoundaryPolicy,
    ) -> None:
        if type(horizon_rows) is not int or horizon_rows <= 0:
            raise _ContractViolationError("horizon_rows must be a positive Python int")
        if type(decision_delay_rows) is not int or decision_delay_rows != 1:
            raise _ContractViolationError("V1 decision_delay_rows must be exactly 1")
        if type(reference_price_column) is not str or not reference_price_column:
            raise _ContractViolationError("reference_price_column must be a non-empty string")
        if reference_price_column == "target" or reference_price_column.startswith("target."):
            raise _ContractViolationError(
                "reference_price_column cannot use the reserved target namespace"
            )
        if (
            type(boundary_policy) is not _BoundaryPolicy
            or boundary_policy is not _BoundaryPolicy.NAN
        ):
            raise _ContractViolationError("V1 boundary_policy must be BoundaryPolicy.NAN")
        self._horizon_rows = horizon_rows
        self._decision_delay_rows = decision_delay_rows
        self._reference_price_column = reference_price_column
        self._boundary_policy = boundary_policy

    @property
    def outcome_id(self) -> str:
        """Return the stable V1 identity."""
        return "forward_simple_return_v1"

    @property
    def version(self) -> int:
        """Return the outcome contract version."""
        return 1

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return all identity-bearing outcome choices with no hidden default."""
        return {
            "boundary_policy": self._boundary_policy.value,
            "decision_delay_rows": self._decision_delay_rows,
            "horizon_rows": self._horizon_rows,
            "reference_price_column": self._reference_price_column,
        }

    @property
    def horizon_rows(self) -> int:
        """Return the positive observed-row movement horizon."""
        return self._horizon_rows

    @property
    def decision_delay_rows(self) -> int:
        """Return the explicit one-row decision delay."""
        return self._decision_delay_rows

    @property
    def reference_price_column(self) -> str:
        """Return the explicit price input column."""
        return self._reference_price_column

    @property
    def boundary_policy(self) -> _BoundaryPolicy:
        """Return the V1 end-of-data policy."""
        return self._boundary_policy

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the sole market column this spec may read."""
        return (self._reference_price_column,)

    @property
    def target_column_names(self) -> tuple[str, ...]:
        """Return the sole reserved-namespace target column."""
        return (_SIMPLE_TARGET,)

    @property
    def comparison_tolerance(self) -> _ToleranceSpec | None:
        """Require exact comparison."""
        return None

    def compute(
        self,
        request: _OutcomeRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute complete horizons and emit NaN where the terminal row is absent."""
        if type(request) is not _OutcomeRequest:
            raise _ContractViolationError("request must be an exact OutcomeRequest")
        prices = request.column(self._reference_price_column)
        result = _np.full(request.decision_length, _np.nan, dtype=_np.float64)
        resolved_length = request.keys.size
        for local_position in range(request.decision_length):
            decision_position = request.decision_offset + local_position
            origin_position = decision_position + self._decision_delay_rows
            terminal_position = origin_position + self._horizon_rows
            if terminal_position >= resolved_length:
                continue
            origin = float(prices[origin_position])
            terminal = float(prices[terminal_position])
            if not _np.isfinite(origin) or not _np.isfinite(terminal):
                continue
            if origin <= 0.0:
                raise _ContractViolationError(
                    "forward simple return requires a strictly positive origin price"
                )
            result[local_position] = (terminal - origin) / origin
        return {_SIMPLE_TARGET: result}
```

Points worth copying:

- **Start from an all-`NaN` array and fill only complete horizons.** The
  incomplete tail stays `NaN`; it is never truncated to the last row.
- **`terminal_position >= resolved_length` is the boundary test.** It is
  `>=` because the terminal row must actually exist on the resolved axis.
- **Non-finite endpoints yield `NaN`; a degenerate origin raises.** Refusing is
  better than fabricating a return.
- **The keys are never consulted for spacing or structure** — only
  `request.keys.size`, to know where the axis ends.
- **No calendar vocabulary anywhere**, including in comments and docstrings
  (§2).

---

## 8. Registration and configuration

### Registration — and the V1 single-spec pin

Registration for outcomes happens in
[`outcomes/__init__.py`](../../src/trading_research/outcomes/__init__.py), not
in `reference.py` — the one asymmetry against the other three plugin families:

```python
from trading_research.outcomes.reference import ForwardLogReturn as _ForwardLogReturn
from trading_research.outcomes.registry import OUTCOME_SPECS

__all__ = ["OUTCOME_SPECS"]

OUTCOME_SPECS.add("forward_log_return_v1", _ForwardLogReturn)
```

`OUTCOME_SPECS.create(name, **params)` calls the class with the config's params;
`_create_component` converts the `boundary_policy` string back to the enum
first. There is **no decorator-based or automatic discovery** in use here.

> ### Read this before writing any code
>
> Two live assertions pin the outcome layer to **exactly one** spec:
>
> | File | Assertion |
> |---|---|
> | `tests/test_architecture.py` | `test_slice_3b_outcome_layer_has_one_exact_public_component` — `_declared_exports(reference_tree) == {"ForwardLogReturn"}`, module public names `== {"ForwardLogReturn", "annotations"}`, and `OUTCOME_SPECS.names() == frozenset({"forward_log_return_v1"})` |
> | `tests/test_outcome_leakage.py` | `test_no_negative_control_is_registered_in_production` — `OUTCOME_SPECS.names() == {"forward_log_return_v1"}` |
>
> Its docstring states the intent plainly: *"V1 ships one OutcomeSpec and no
> speculative outcome library."* That inventory was an owner-approved planning
> decision (OD-3A). Adding a second spec therefore means **changing an
> architecture assertion**, which [CLAUDE.md](../../CLAUDE.md) treats with the
> same review discipline as changing an accepted ADR boundary.
>
> **Do not do it inside a plugin addition. Stop, and raise it as a decision**
> — stating the research question the existing outcome cannot express, and
> whether the new definition belongs in the roadmap. This guide exists so that,
> once that decision is taken, the implementation is correct; it is not the
> decision.

If and when a second spec is approved, the inventories to extend — **by addition
only** — are: the two assertions above, the reference module's `__all__` and its
declared-exports/public-names guard, and any use-case inventory the phase
qualification pins. Additional guard properties that constrain *how* you write
it:

- **Module public names must be exactly the class names plus `annotations`** —
  hence underscore-aliased imports throughout `outcomes/reference.py`.
- **The layer must contain no calendar vocabulary or gap identifier** (§2).
- **`test_slice_3b_constructor_has_no_hidden_outcome_default`** pins the
  reference constructor's keyword-only argument list and its absent defaults.
- Unlike models and representations, a constructor **is** expected here.

### Configuration

A resolved experiment config
([`experiments/config.py`](../../src/trading_research/experiments/config.py))
carries two independent outcome positions:

```json
"training_outcome": {
  "name": "forward_log_return_v1", "version": 1,
  "params": {"boundary_policy": "nan", "decision_delay_rows": 1,
             "horizon_rows": 1, "reference_price_column": "close"}
},
"evaluation_outcomes": [
  {"name": "forward_log_return_v1", "version": 1,
   "params": {"boundary_policy": "nan", "decision_delay_rows": 1,
              "horizon_rows": 1, "reference_price_column": "close"}}
]
```

- `training_outcome` is a single object or `null`.
- `evaluation_outcomes` is an ordered array, possibly empty; exact duplicates
  are refused; it must be non-empty **iff** `evaluators` is non-empty.
- At resolution, `_create_component` re-checks the live object's `outcome_id`,
  `version` and canonical `params` against the config; a mismatch is a contract
  error.
- `_preflight_components` validates each spec's declaration and checks that
  every evaluator's `required_outcome_columns` are available among the
  configured evaluation outcomes' `target_column_names`.
- The orchestrator adds every spec's `required_columns` to the market columns it
  opens the view with.

---

## 9. Tests an author should write

### Already provided globally by the platform — no test to write

| Guard / rig | What it proves |
|---|---|
| `test_slice_3b_forward_reading_is_confined_to_outcomes` | `OutcomeRequest` never reaches features, representations, models or policies. |
| `test_slice_3b_outcome_source_has_no_feature_gap_or_session_semantics` | The layer names no calendar identifier and no closure vocabulary. |
| `test_slice_3b_outcome_layer_has_one_exact_public_component` | Layer inventory, `__all__`, public names, exact eleven-member surface, registry contents. |
| `test_slice_3b_constructor_has_no_hidden_outcome_default` | Every research choice is an explicit keyword with no default. |
| `test_slice_3b_leakage_rigs_name_all_controls_and_cannot_be_softened` | The five negative controls exist, are test-only, and no tolerance/skip/`allclose` may be introduced. |
| `test_slice_3b_experiment_entry_points_only_mint_the_two_target_channels` | `experiments/outcomes.py` exports exactly `compute_outcomes` and `compute_training_targets` and has no storage authority. |
| `test_training_and_evaluation_target_channels_are_distinct` | The two carriers are distinct types. |
| `test_target_end_derivation_is_the_exact_declared_sum` | Purge derivation uses exactly `decision_delay_rows + horizon_rows`. |
| `test_layer_respects_its_dependency_budget` | `outcomes` imports only `core`. |
| Determinism verification (`verify` CLI verb) | Persisted outcome artifacts are recomputed and compared under your declared tolerance. |

Rig comparison is **exact** — NaN masks as bytes, values as raw `uint64` bits.
An architecture test asserts the words `allclose` and `tolerance` never appear
in the rig file, and that no `skip`/`skipif`/`xfail` decorator is added.

> **Important asymmetry.** Unlike the feature rigs
> (`tests/test_feature_leakage.py`) and the model/representation rigs
> (`tests/test_model_leakage.py`), which parametrise over the **live registry**
> and therefore sweep a new plugin automatically, the outcome rigs in
> `tests/test_outcome_leakage.py` invoke `_assert_forward_reach` and
> `_assert_pre_origin_blind` against the single reference spec explicitly. A new
> `OutcomeSpec` is **not** swept automatically — its rig invocations are part of
> the author's work below.

### Tests the author should add — in `tests/test_outcomes.py` and `tests/test_outcome_leakage.py`

Use **synthetic data only**; never a real market value in a fixture.

- [ ] **Declaration test.** Assert the full eleven-member surface exactly.
- [ ] **Hand-computed golden.** A tiny price array with literal expected values,
      asserting the exact origin/terminal rows used — this is what catches
      alignment inversion and off-by-one.
- [ ] **Boundary golden.** Assert the last `decision_delay_rows + horizon_rows`
      decision rows are `NaN`, and that nothing is truncated.
- [ ] **Discontinuity golden.** Assert an uneven key spacing inside the forward
      span does **not** make the target unavailable.
- [ ] **Forward-reach rig.** Perturb every row after the declared terminal
      position and assert the computed targets are bit-for-bit unchanged.
- [ ] **Pre-origin blindness.** Perturb rows before the measurement origin and
      assert the targets are bit-for-bit unchanged.
- [ ] **Shape and schema.** `dtype == float64`, `ndim == 1`,
      `len == decision_length`, keys/order equal to `target_column_names`, no
      `±inf`.
- [ ] **Reserved-namespace conformance.** Every emitted name is `target` or
      `target.…`; `required_columns` contains none.
- [ ] **Declaration refusal paths.** Non-positive `horizon_rows`,
      `decision_delay_rows != 1`, an empty `reference_price_column`, a reserved
      `reference_price_column`, and a non-`NAN` `boundary_policy` each raise
      `ContractViolationError`.
- [ ] **Degenerate-input refusal.** Prices that make the measurement undefined
      raise rather than fabricating a number.
- [ ] **Both carriers.** Assert `compute_training_targets` yields a
      `TrainingTargetFrame` and `compute_outcomes` an `OutcomeFrame`, and that
      neither type is accepted where the other is required.
- [ ] **Purge sensitivity.** Assert a fold generated with your spec applies a
      purge of exactly `decision_delay_rows + horizon_rows` rows (bounded by
      `fit_rows`).
- [ ] **Negative controls, if you add any.** They are test-only, must be named
      in the architecture inventory, and must never appear in production source.
- [ ] **Registry resolution.** Your name resolves through `OUTCOME_SPECS.create`
      with the config params.
- [ ] **Full gate before merge** — `./scripts/gate.sh`, unfiltered and green.

---

## 10. Common mistakes

| # | Anti-pattern | Why it violates the architecture | Caught by |
|---|---|---|---|
| 1 | Using the decision row itself as the origin while declaring a delay | changes what is measured; the purge no longer matches the reach | golden (`OffByOneOutcomeControl`) |
| 2 | `np.roll(result, 1)` — shifting values onto neighbouring decision rows | alignment inversion | golden (`AlignmentInversionOutcomeControl`) |
| 3 | Reading one row past the terminal position | undeclared forward reach; shrinks the derived purge relative to actual reach | forward-reach rig (`OverreachOutcomeControl`) |
| 4 | `terminal = min(origin + horizon, size - 1)` | silent truncation; mixes different random variables in one column | boundary golden (`TruncatingOutcomeControl`) |
| 5 | Emitting `NaN` because the keys are unevenly spaced | infers a market closure the dataset cannot prove | discontinuity golden (`ClosureInferringOutcomeControl`) |
| 6 | Any of the words `session`, `weekend`, `holiday`, `closure`, `outage` in the source | forbidden semantic vocabulary in this layer | architecture text scan |
| 7 | Importing `datetime` to convert a key | there is no UTC and no calendar here | architecture identifier scan |
| 8 | Emitting `forward_return` (no `target.` prefix) | outcome outputs must use the reserved namespace | target minting refuses it |
| 9 | Declaring `required_columns = ("target.x",)` | outcomes may not read the reserved namespace | `_make_outcome_request` refuses it |
| 10 | A constructor default for `horizon_rows` | hides a research-definition choice | no-hidden-default guard |
| 11 | A knob absent from `params` | two definitions collide on one fitted-state identity | nothing automatic — this is why §5 exists |
| 12 | Returning `0.0` where the horizon is incomplete | fabricates data and biases every metric computed from it | your own boundary golden |
| 13 | Returning `float32`, or a length other than `decision_length` | output validation | `_execute_outcome` |
| 14 | Treating the OutcomeSpec as a model input | it is not on the forward chain; the namespace is prohibited there | ADR-0010; declared-input checks |

---

## 11. Author checklist

- [ ] The **decision to add a second OutcomeSpec has been taken explicitly**
      (§8) — not assumed as part of a plugin task.
- [ ] Eleven-member public surface, exactly; no extra public method or attribute.
- [ ] `outcome_id` == registry name; `version` a positive `int`.
- [ ] Every research choice is an explicit keyword-only constructor argument with
      **no default**, and appears in `params`.
- [ ] `horizon_rows > 0`; `decision_delay_rows == 1`;
      `boundary_policy is BoundaryPolicy.NAN`.
- [ ] `required_columns` non-empty, non-reserved, and actually available on the
      dataset.
- [ ] `target_column_names` non-empty and **entirely** in the reserved
      `target`/`target.` namespace.
- [ ] `compute` reads no further than
      `decision_delay_rows + horizon_rows` rows forward, and never backwards past
      the origin.
- [ ] Incomplete horizons are `NaN`, never truncated, never `0.0`.
- [ ] Key discontinuities are ignored — no calendar inference, no calendar
      vocabulary in the source.
- [ ] Output is exact `float64`, one-dimensional, `decision_length` long, free of
      `±inf`, with keys and order equal to `target_column_names`.
- [ ] `comparison_tolerance` is `None` unless a genuine reason is written down.
- [ ] No I/O, no clock, no environment, no module-level mutable state, no
      unseeded randomness.
- [ ] Registered in `outcomes/__init__.py`; `reference.py` `__all__` updated;
      imports underscore-aliased.
- [ ] §8 guard assertions updated **by addition only**, and called out
      explicitly in the commit message or PR description as an architecture-test
      change.
- [ ] §9 author tests and rigs added, synthetic data only.
- [ ] `uv run pytest tests/test_outcomes.py tests/test_outcome_leakage.py tests/test_folds.py tests/test_architecture.py -q` green.
- [ ] `./scripts/gate.sh` green, unfiltered.
- [ ] No production file outside `outcomes/` modified.
- [ ] No dataset, view manifest, audit record, run directory or cache artifact
      touched; no OOS or LOCKBOX data read.

### Stop and raise instead of working around

Halt and ask for an architecture decision if the outcome requires any of:

- a second registered `OutcomeSpec` at all (§8);
- `decision_delay_rows` other than `1`, or a `BoundaryPolicy` other than `NAN`
  — including `truncate` or `skip`;
- a horizon expressed in wall-clock time rather than observed rows;
- any session, calendar or closure semantics;
- reading a non-price artifact, another plugin's output, or a `FeatureFrame`;
- a target the model would consume at inference time;
- relaxing, deleting or skipping any architecture guard or rig assertion;
- touching `core/`, `artifacts/`, `experiments/` or the data layer.

---

## 12. References

Canonical sources. **If this guide conflicts with any of them, they win.**

**Contracts and decisions**

- [ADR-0010 — Two-plane evaluation and versioned OutcomeSpec](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md) — the binding contract and the fit-time channel amendment
- [ADR-0022 — Phase-3 research contracts](../adr/ADR-0022-phase-3-research-contracts.md) — §9 boundary/discontinuity semantics, §10 the `X → X+1` alignment policy, §8 tolerance, Amendment 1 §3 the two carriers
- [ADR-0008 — Fit lifecycle and FittedState identity](../adr/ADR-0008-fit-lifecycle-and-fittedstate.md) — target definition pinned into fitted-state identity
- [ADR-0012 — Two-tier splits](../adr/ADR-0012-two-tier-splits.md) — Amendment 1 derives the purge from declared reach
- [ADR-0005 — GapIndex, not SessionModel](../adr/ADR-0005-gapindex-not-sessionmodel.md) · [ADR-0011 — Run identity and canonical runs](../adr/ADR-0011-run-identity-and-canonical-runs.md) · [ADR-0020 — Cost-semantics quarantine](../adr/ADR-0020-cost-semantics-quarantine.md) · [ADR-0021 — Columnar feature contract](../adr/ADR-0021-columnar-feature-contract.md)
- [ADR index](../adr/README.md)

**Plans and policy**

- [docs/phases/PHASE3_RESEARCH_RUNS.md](../phases/PHASE3_RESEARCH_RUNS.md) — normative Phase-3 plan (OD-3A and its sub-decisions, §2 closed vocabulary, §2.7 fold geometry and purge override)
- [docs/architecture/V1.md](../architecture/V1.md) §6 — research contracts and the two evaluation planes
- [docs/research/RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §5 — lookahead leakage and the one controlled exception
- [PROJECT_STATE.md](../../PROJECT_STATE.md) · [CLAUDE.md](../../CLAUDE.md)

**Code**

- [`src/trading_research/outcomes/reference.py`](../../src/trading_research/outcomes/reference.py) — the single live OutcomeSpec
- [`src/trading_research/outcomes/execution.py`](../../src/trading_research/outcomes/execution.py) — declaration validation, output validation, forward-reach derivation
- [`src/trading_research/outcomes/__init__.py`](../../src/trading_research/outcomes/__init__.py) — `OUTCOME_SPECS` and the registration line
- [`src/trading_research/core/interfaces.py`](../../src/trading_research/core/interfaces.py) — the `OutcomeSpec` protocol
- [`src/trading_research/core/research.py`](../../src/trading_research/core/research.py) — `BoundaryPolicy`, `OutcomeRequest`, `ToleranceSpec`, `StageOrigin`
- [`src/trading_research/core/research_frames.py`](../../src/trading_research/core/research_frames.py) — `TrainingTargetFrame`, `OutcomeFrame`
- [`src/trading_research/experiments/outcomes.py`](../../src/trading_research/experiments/outcomes.py) — `compute_training_targets`, `compute_outcomes`
- [`src/trading_research/experiments/folds.py`](../../src/trading_research/experiments/folds.py) — purge derivation from declared reach

**Tests**

- [`tests/test_outcome_leakage.py`](../../tests/test_outcome_leakage.py) — release-blocking forward-reach and pre-origin rigs and the five negative controls
- [`tests/test_outcomes.py`](../../tests/test_outcomes.py) — goldens, boundary, discontinuity, carrier distinction, refusal paths
- [`tests/test_folds.py`](../../tests/test_folds.py) — derived purge and override discipline
- [`tests/test_architecture.py`](../../tests/test_architecture.py) — forward-read confinement, semantic-vocabulary scan, exact surfaces
- [`scripts/gate.sh`](../../scripts/gate.sh) — the gate you must run
