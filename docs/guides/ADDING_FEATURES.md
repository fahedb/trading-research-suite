# Adding a feature provider — authoring guide

**Audience:** anyone adding a new feature calculation to this platform — a
researcher who knows the formula but not the internals, or a coding agent asked
to implement one safely.

**Scope:** this guide is *explanatory*. It creates no new platform rule. If
anything here disagrees with an accepted ADR or with
[`docs/phases/PHASE2_FEATURES.md`](../phases/PHASE2_FEATURES.md), **the contract
documents win** — fix the guide, not the contract. The binding contract is
[ADR-0021](../adr/ADR-0021-columnar-feature-contract.md).

**Status of the code this describes:** Phase 2 is COMPLETE. Everything below is
implemented and enforced today. Nothing here is aspirational.

**Companion guides:**
[ADDING_REPRESENTATIONS.md](ADDING_REPRESENTATIONS.md) ·
[ADDING_RESEARCH_MODELS.md](ADDING_RESEARCH_MODELS.md) ·
[ADDING_DECISION_POLICIES.md](ADDING_DECISION_POLICIES.md) ·
[ADDING_OUTCOMES.md](ADDING_OUTCOMES.md)

---

## 1. What a FeatureProvider is

A **feature provider** is a small, self-contained object that computes one or
more numeric columns from market data. That is its entire job.

### What the provider owns

Exactly one thing: **the arithmetic**. Given a block of market columns and a
statement of which rows are being evaluated, produce the numbers.

### What the platform already handles for you

Do not re-implement any of this. It is centralised precisely so that it can be
tested once instead of once per provider:

| The platform handles | What that means for you |
|---|---|
| **Loading and authorization** | You receive already-loaded, already-authorized, integrity-verified data. You never see a file, a grant, or a dataset path. |
| **Bounding the input** | The data you receive physically stops at the evaluation upper boundary. You cannot read past it even if you try. |
| **Stitching segments together** | If backward context spans a TRAIN→VALIDATION boundary, the platform joins the arrays into one contiguous axis for you. You write your formula once, for one flat array. |
| **Warm-up masking** | The platform overwrites the first `max_prior_rows` evaluation rows with `NaN` **after** your `compute` returns. You do not need to emit `NaN` there yourself (though it is harmless if you do). |
| **Gap invalidation** | Under `BREAK`, the platform blanks any row whose window straddles an arithmetic gap. Again, applied after you return. |
| **Alignment** | The platform binds your output to the exact observed evaluation keys and checks the alignment before and after you run. |
| **Caching, identity, determinism** | Content hashing, cache keys, immutable storage and reproducibility verification are all platform-side. You declare; the platform records. |

The masking point is worth restating because it is the safety property the whole
phase is built on: **your declaration is enforced, not trusted.** If you declare
`max_prior_rows = 19` and emit a value at evaluation row 3, the platform
replaces it with `NaN`. You cannot emit a value you could not have computed.

### What a provider must NOT do

- No file, network or database access of any kind.
- No `AccessGrant`, no loader, no registry, no dataset or view path.
- No importing anything from `trading_research.data` — the architecture tests
  forbid it, and `features` may import only `core`.
- No reading another provider's output. There is no feature-of-feature path in
  V1, by design ([ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md)).
- No targets, outcomes, labels, forward returns, model output, decisions,
  simulation or trading vocabulary. Those live in later stages
  ([ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)).
- No environment variables, wall-clock reads, config files, module-level mutable
  state, or unseeded randomness.
- No reading a market column you did not declare.
- No widening a platform abstraction to make your feature fit. If your feature
  genuinely cannot be expressed within this contract, **stop and raise it** —
  see §12 step 12.

> **Rule of thumb.** If you find yourself writing anything other than array
> arithmetic over the columns you declared, you are probably outside the
> contract.

---

## 2. Minimal provider contract

A provider is any object with these eight members. There is no base class to
inherit — `FeatureProvider` in
[`core/interfaces.py`](../../src/trading_research/core/interfaces.py) is a
`typing.Protocol`, so structural conformance is enough and mypy checks it
statically.

The reference providers pin this surface exactly, and an architecture test
asserts it is exactly these eight names and nothing else.

```python
provider_id: str                 # stable identity of this implementation
version: int                     # contract version of this implementation
feature_names: tuple[str, ...]   # ordered, unique, non-empty output names
required_columns: tuple[str, ...]# ordered, unique market columns you may read
window: WindowSpec               # finite reach + explicit gap policy
params: Mapping[str, ParamValue] # every semantic knob, canonically encoded
cost_hint: CostHint              # CHEAP | EXPENSIVE — a caching hint only
def compute(self, request: FeatureRequest) -> Mapping[str, NDArray[float64]]
```

### `provider_id`

**What it is.** The stable string identity of the implementation. It is what the
registry name maps to and what appears in cache identity and in per-column
provenance.

**What must be stable.** Everything. Once a `provider_id` has produced a cached
artifact, that string permanently means "this computation". Do not rename a
provider to change its behaviour.

**Enforced:** must be an exact non-empty `str`.

**Common mistakes:** reusing an existing id for different maths; embedding a
parameter value in the id (`"rolling_mean_20"`) instead of putting it in
`params`; changing the id when you meant to bump `version`.

### `version`

**What it is.** An integer you increment when **the same declaration should now
produce different numbers** — a bug fix in the formula, a change of convention.

**Why it matters.** Cache identity is built from your *declaration*, not from
your source code hash. Bumping `version` is how you tell the platform that old
cached artifacts no longer describe this computation.

> In practice the platform Git commit is also in the cache identity, so a code
> change already invalidates caches today. `version` is the *semantic* signal
> and is what survives if identity is ever narrowed. Bump it anyway.

**Enforced:** must be an exact Python `int` (not `bool`, not `numpy.int64`).

**Common mistakes:** changing the formula without bumping `version`; using a
string `"1"`; using a float.

### `feature_names`

**What it is.** The ordered tuple of output column names this provider emits.
Your `compute` must return exactly this set of keys — no more, no fewer.

**Enforced:** exact `tuple`, non-empty, every element a non-empty `str`, no
duplicates. Reserved names refused (see §5).

**Common mistakes:** returning a `list` instead of a `tuple`; returning a name
from `compute` that is not declared here (refused, naming your provider);
declaring a name another provider in the same plan also declares — this fails
**before any computation runs**, naming both providers and both plan positions.

### `required_columns`

**What it is.** The exact market columns you are permitted to read, e.g.
`("close",)` or `("high", "low")`. The platform builds your request from exactly
these; asking for anything else raises.

**Enforced:** exact `tuple`, may be empty, elements non-empty unique `str`.
Reserved names refused here too.

**Common mistakes:** declaring columns you do not use (harmless but noisy — it
enters cache identity, so it changes your cache key); *not* declaring a column
you do use (`request.column("high")` will raise `FeatureRequestError`); assuming
a column exists — if the view was opened without it, the request build refuses
and names your provider.

### `window`

A frozen `WindowSpec` — see §3 for the full rules.

```python
WindowSpec(max_prior_rows, gap_policy, nominal_interval=None)
```

**Enforced:** must be an exact `WindowSpec` instance.

### `params`

**What it is.** Every semantic knob that can change your output. This is the
identity-bearing record of *how you were configured*.

**Allowed value types** (`ParamValue`): `str`, `int`, `float`, `bool`, `None`,
and nested `tuple`s of those. Nothing else.

**Enforced:** must be a `Mapping`; keys non-empty `str`; values must fit
`ParamValue`. Encoding is canonical and type-preserving — key order is
irrelevant (sorted), `4` and `4.0` are distinct, `True` and `1` are distinct,
floats are encoded via `float.hex()`. **`float('nan')` is refused** as a
parameter, because its bit patterns have no injective hex encoding.

**Common mistakes:** leaving a constructor argument out of `params` (see §6 —
this is the single most dangerous mistake you can make); putting a NumPy scalar
or a `list` in `params`; putting non-semantic bookkeeping (an author name, a
description) in `params`, which needlessly fragments the cache.

### `cost_hint`

**What it is.** `CostHint.CHEAP` or `CostHint.EXPENSIVE`. It answers one
question only: under the default `CACHE_IF_EXPENSIVE` materialization policy,
should this stage's output be written to the artifact cache?

**It is not a computation semantic.** It does not enter the identity key. Two
providers identical except for `cost_hint` produce the same cache key. Changing
it changes *whether* the result is persisted, never *what* the result is.

**No wall clock is ever consulted** — that is the whole point of a declared hint
([ADR-0007](../adr/ADR-0007-materialization-policy.md) Amendment 2).

**Enforced:** must be an exact `CostHint` member.

**Guidance:** declare `CHEAP` honestly. All three reference providers are
`CHEAP`. For context, on the 1.4 M-row TRAIN view a full three-provider pass
takes ~110 ms while a verified cache hit takes ~104 ms — for cheap vectorised
arithmetic the cache is roughly break-even, so mis-declaring `EXPENSIVE` buys
you nothing and costs ~45 MB of disk per platform commit.

### `compute(request)`

**Signature:** `compute(self, request: FeatureRequest) -> Mapping[str, NDArray[np.float64]]`.

**What you get** (`FeatureRequest`, from
[`core/features.py`](../../src/trading_research/core/features.py)):

| Member | Meaning |
|---|---|
| `request.keys` | Read-only `int64` array — the **resolved context axis**: all observed keys from the resolved context start through `eval_end`, strictly increasing, spanning segment seams. Never contains a key past `eval_end`. |
| `request.column(name)` | Read-only array for one **declared** column, same length as `request.keys`. Any other name raises. |
| `request.eval_offset` | Index into `request.keys` of the **first evaluation row**. |
| `request.eval_length` | Number of evaluation rows — this is the exact length your output arrays must have. |
| `request.unit`, `request.clock_domain` | The native-time axis tags. |
| `request.source_identities` | Ordered `(dataset_id, manifest_sha256)` of every contributing segment. |

**The one index relationship you must internalise:**

```
output row j   (0 <= j < request.eval_length)
    corresponds to
context position  p = request.eval_offset + j
```

Everything before `eval_offset` is backward context. You read it; you never
write to it.

The arrays you receive are genuinely read-only — attempting to assign into them
raises `ValueError: assignment destination is read-only`. Copy first if you need
a mutable working buffer.

---

## 3. Window and information-reach rules

### `max_prior_rows` — the number that matters most

**Definition:** the maximum number of **observed rows strictly before** the
evaluation row that your provider may read.

This spelling was chosen so that no arithmetic is ever needed — every derived
quantity *equals the field itself*:

| Quantity | Value |
|---|---|
| Backward reach (feeds the Phase-3 fold purge) | `max_prior_rows` |
| Leading unavailable evaluation rows (warm-up) | `max_prior_rows` |
| Unavailable rows after each discontinuity under `BREAK` | `max_prior_rows` |
| Total observations spanned by the window | `max_prior_rows + 1` |

### Concrete examples

| Feature | Reads | `max_prior_rows` |
|---|---|---|
| `high − low` on the current bar | row `p` only | **0** |
| `close[p] − open[p]` | row `p` only | **0** |
| 1-bar return `log(close[p] / close[p−1])` | rows `p−1`, `p` | **1** |
| Rolling mean over **20 observations** | rows `p−19 … p` | **19** |
| Rolling std over **50 observations** | rows `p−49 … p` | **49** |
| Difference of two rolling means, 10 and 60 | rows `p−59 … p` | **59** (the maximum of the two) |

> A window of `N` observations declares `N − 1`. If you catch yourself writing
> `max_prior_rows = window`, you have an off-by-one.

For a provider that combines several internal windows, declare the **largest**
reach. Over-declaration is safe — it yields a purge at least as conservative as
required. Under-declaration is not.

### Under-declaration is a release blocker

If you declare 19 and actually read 21, that is a **correctness failure and a
release blocker**, not a cleanup item. It is caught mechanically: the
backward-reach rig perturbs every row strictly before `keys[i − max_prior_rows]`
and asserts value `i` is bit-for-bit unchanged. It catches single-row
over-reach, not just gross over-reach. There is no tolerance and no way to skip
it.

Why it matters beyond your own feature: Phase 3 derives the mandatory
train/evaluation **purge** from these declarations
([ADR-0012](../adr/ADR-0012-two-tier-splits.md) Amendment 1). A lie here becomes
a silent, favourable-direction bias in every future backtest that uses your
feature.

### Unbounded history is not representable

There is no "expanding window" and no "use everything so far". A method that
genuinely needs expanding statistics belongs in a fitted `RepresentationModel`
(Phase 3), fitted on TRAIN and applied forward — not in a feature.

### Gap policy: `CROSS` or `BREAK`

There is **no default**. You must choose explicitly.

| Policy | Meaning |
|---|---|
| `GapPolicy.CROSS` | Your window may use observed rows from both sides of an arithmetic gap. The gap is simply ignored. |
| `GapPolicy.BREAK` | An arithmetic gap resets the window. Output stays unavailable (`NaN`) until a complete declared window has re-accumulated **after** the gap — exactly `max_prior_rows` rows. |

An **arithmetic gap** is an adjacent-key delta that differs from a
caller-declared `nominal_interval`. Nothing more.

### `nominal_interval`

- `CROSS` → `nominal_interval` **must be `None`**. Supplying one raises.
- `BREAK` → `nominal_interval` **is required**: a positive Python `int` that
  fits in a signed int64.

It is never parsed from a timeframe label, never derived from `TimeUnit`, never
hardcoded in the platform, and never inferred from the data. For the current
M1 dataset the caller passes `60`.

```python
WindowSpec(19, GapPolicy.CROSS)                           # OK
WindowSpec(19, GapPolicy.BREAK, nominal_interval=60)      # OK
WindowSpec(19, GapPolicy.BREAK)                           # raises: BREAK needs an interval
WindowSpec(19, GapPolicy.CROSS, nominal_interval=60)      # raises: CROSS must not carry one
```

### Not available, deliberately

**There is no `TRUNCATE` policy and no `min_observations`.** Partial-window
output is out of scope for V1 — the platform cannot define the *meaning* of a
value computed from fewer observations, because that meaning is
estimator-specific. Do not add either concept; if a research family genuinely
needs it, it is a new ADR, not a provider feature.

---

## 4. Input access rules

1. **You may read only your declared `required_columns`.** `request.column()`
   refuses every other name with `FeatureRequestError`. This is structural, not
   a convention.
2. **All market data arrives through `FeatureRequest`.** There is no other
   route into a provider.
3. **No file I/O.** No `open()`, no `np.load`, no `glob`, no `memmap`, no
   `fromfile`. An architecture test scans for these identifiers.
4. **No `AccessGrant`, `MarketFrameLoader`, `DatasetRegistry`,
   `authorize_access`, `open_bounded_view` or `read_split_policy`.** These names
   are on an explicit forbidden list for feature modules.
5. **No imports from `trading_research.data`.** `features` may import only
   `core`. The dependency budget is enforced by
   `test_layer_respects_its_dependency_budget`.
   - Practical consequence: you cannot use `GapIndex`, which lives in the data
     layer. You do not need it — gap detection for `BREAK` is done for you by
     the platform from the key array and your declared `nominal_interval`.
6. **No feature-of-feature input.** Each provider receives its own isolated
   request built only from market frames. No provider's output is reachable from
   any request. Composition of features is a DAG engine, which
   [ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md) exists to refuse.
7. **No `OutcomeSpec`, target, `TrainingTargetFrame`, `ResearchModel`,
   `ModelOutputFrame`, `DecisionPolicy`, simulation or evaluation dependency.**
   Forward-looking values reach models only through the platform target channel,
   in Phase 3, never through a feature.

---

## 5. Output rules

Your `compute` returns a mapping. The platform validates it before it becomes a
`FeatureFrame`.

| Rule | Detail |
|---|---|
| **Keys are the evaluation axis** | Output is dense and aligned exactly to `BoundedView.eval_frame.keys` — identical length, order and values. Context rows participate in computation but never appear in output. |
| **One array per declared name** | The key set of the returned mapping must **equal** `feature_names`. Missing or extra names are refused, naming your provider and listing the difference. |
| **`float64` only** | Exact metadata-free `numpy.float64`. Not `float32`, not `int64`, not `bool`, not `object`. |
| **One-dimensional** | `values.ndim == 1`. |
| **Exact length** | `values.shape[0] == request.eval_length`. |
| **`NaN` means "unavailable at this evaluation key"** | It is the only way to say "no value here". |
| **`±inf` is invalid and refused** | Checked at output validation, at frame minting, and again at artifact encode/decode. If your maths can divide by zero or overflow, convert to `NaN` yourself. |
| **Names unique** | Within your provider, and across every provider in the same plan (collision fails before computation). |
| **Reserved namespace** | Exactly the case-sensitive name `target` and any name beginning `target.` are refused — in **both** `feature_names` and `required_columns`, before any computation begins. |

The reserved predicate is exact and narrow. Nothing else is reserved:

| Name | Reserved? |
|---|---|
| `target` | **yes** |
| `target.forward_return_1` | **yes** |
| `target.` | **yes** |
| `Target`, `TARGET`, `TARGET.x` | no |
| `target_value`, `targets`, `mytarget.value` | no |

> Returning a `NaN` where you have no value is correct and expected. Returning
> `inf`, or silently returning `0.0` to avoid a `NaN`, is a bug — the second one
> fabricates data.

---

## 6. Identity and reproducibility rules

The cache key for a feature stage is built **before execution** from your
declaration: `provider_id`, `version`, canonical `params`, `feature_names`,
`required_columns` and the full `WindowSpec` — plus the input dataset
identities, the exact bounds, the split-policy identity, and the platform Git
commit.

That gives one hard rule:

> **Every semantic input that can change your output must be reachable from your
> declaration — in practice, from `params`.**

If two differently-configured instances of your provider produce the same
declaration, they receive the **same cache key**, and the second one will be
served the first one's numbers. That is a silent, durable wrong answer.

### Correct — identity-bearing

```python
class RollingStdClose:
    def __init__(self, window: int = 20) -> None:
        self._window_size = window
        self._window = _WindowSpec(window - 1, _GapPolicy.CROSS)

    @property
    def params(self):
        return {"window": self._window_size}   # the knob is declared
```

- a rolling window length → `params["window"]`
- a threshold or clip level → `params["threshold"]`
- a choice between two internal formulas → `params["mode"] = "log"` / `"simple"`
- a stochastic seed, if a provider ever needs one → `params["seed"]`
  ([ADR-0007](../adr/ADR-0007-materialization-policy.md) requires seeds to be
  identity-bearing; declaring one as an ordinary param satisfies that with zero
  extra machinery. No V1 reference provider is stochastic.)

Note that `gap_policy` and `nominal_interval` do **not** need to go in `params`
— they are already identity-bearing through `WindowSpec`.

### Incorrect — invisible to identity

```python
# every one of these is a defect
scale = float(os.environ["FEATURE_SCALE"])      # environment dependence
if datetime.now().weekday() < 5: ...            # wall clock
cfg = json.load(open("myfeature.json"))         # local config file
_CACHE.append(x); bias = len(_CACHE)            # mutable module-global state
noise = np.random.randn(n)                      # unseeded randomness
```

The first three are also blocked outright by the no-I/O and no-clock rules. The
last two are the dangerous ones because they *look* like ordinary code.

Determinism verification will catch them: `verify_determinism` recomputes the
stage twice with caching forced off and compares canonical artifact bytes. It
detects per-instance counters, module-level mutable state, single-ULP drift,
NaN-position drift and signed-zero drift. But it detects them *after* you have
written the bug — the point of this section is not to write it.

### `cost_hint` is not a computation semantic

Repeating it because it is the one field people expect to be identity-bearing
and it is not: `cost_hint` chooses whether output is persisted. It never changes
a number, and it is deliberately excluded from the identity key.

---

## 7. Gap behaviour

### Worked example

Take a key axis where the nominal interval is `1` and there is a gap between
positions 19 and 20:

```
positions:  ... 17  18  19  |  20  21  22  23 ...
keys:       ... 17  18  19  | 520 521 522 523 ...
                             ^ delta = 501, not 1  -> arithmetic gap
```

For a provider declaring `max_prior_rows = 3`:

| Policy | Output at positions 20, 21, 22 | Output at position 23 |
|---|---|---|
| `CROSS` | computed normally, using rows 17–20 etc. across the gap | computed normally |
| `BREAK` | **`NaN`, `NaN`, `NaN`** — exactly `max_prior_rows` rows | first value, window is entirely post-gap |

Under `BREAK` the platform also blanks the usual `max_prior_rows` leading rows
at the start of the evaluation range. The rule is uniform: a row is unavailable
if fewer than `max_prior_rows` observed rows precede it, or if any adjacent
transition inside its window deviates from `nominal_interval`.

You do **not** implement any of this. Write the plain trailing computation as if
gaps did not exist; the platform blanks what you were not entitled to compute.

### Gaps are arithmetic only

A gap means one thing: *the adjacent key delta differs from the declared nominal
interval*. It does **not** mean weekend, session close, holiday, outage, broker
downtime, or anything else. Asserting those meanings is forbidden by
[ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md) — the dataset cannot
prove them, and the clock domain is broker wall-clock with unknown DST history.

A TRAIN→VALIDATION segment seam is treated as an ordinary arithmetic
discontinuity, nothing more.

### Authoring prohibition: do not derive features from future key structure

> **This is a known limitation of the current leakage rigs, recorded here as an
> authoring rule. It is not a new architecture decision and it does not change
> ADR-0021.**

The release-blocking leakage rigs perturb **market value columns** — `open`,
`high`, `low`, `close`, and `tick_volume` — and assert your past outputs are
bit-for-bit unchanged. They do
**not** perturb the key axis, because the axis has to stay fixed for the
comparison to be well defined.

Consequence, established by the independent Phase-2 whole-phase review
(2026-08-13): a provider that derives values from **forward key-axis structure**
— for example `keys[i+1] - keys[i]`, or the number of remaining rows on the axis
— passes every existing rig while still using information that is not available
at decision time. Forward key spacing is effectively "how long until the next
bar", which is weekend/holiday structure by another name.

**Therefore, for normal V1 feature work:**

- Do not read `request.keys` at any index greater than your current evaluation
  position.
- Do not derive a feature value from the spacing, count or layout of future
  keys.
- Reading *backward* key structure within your declared `max_prior_rows` is
  fine; reading forward is not.
- In practice, most providers never touch `request.keys` at all — the reference
  providers do not.

If a research family genuinely requires a key-structure feature, that is an
explicit architecture/review decision, not something to introduce inside a
routine feature addition.

---

## 8. Complete worked examples

These mirror the style of
[`features/reference.py`](../../src/trading_research/features/reference.py).

> **Style is load-bearing here.** `features/reference.py` has an architecture
> guard pinning its module-level public names to exactly the provider class
> names plus `annotations`. That is why **every import in that module is
> underscore-aliased** (`import numpy as _np`, `... import CostHint as
> _CostHint`). A plain `import numpy as np` would add `np` to the module's
> public names and fail `test_slice_2b_modules_have_exact_public_surfaces`.
> Follow the aliasing convention exactly.

Assume this module preamble (already present in `reference.py`):

```python
from __future__ import annotations

import typing as _typing

import numpy as _np

from trading_research.core.errors import FeatureContractError as _FeatureContractError
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import FeatureRequest as _FeatureRequest
from trading_research.core.features import GapPolicy as _GapPolicy
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import WindowSpec as _WindowSpec
from trading_research.features.registry import FEATURE_PROVIDERS as _FEATURE_PROVIDERS

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

    import numpy.typing as _npt
```

### Example 1 — zero-lookback: `close − open`

Proves the simplest case: multiple required columns, no warm-up, no gap
sensitivity.

```python
class CloseMinusOpen:
    """Signed bar body: close minus open on the same observation."""

    @property
    def provider_id(self) -> str:
        """Return the stable implementation identity."""
        return "close_minus_open"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the sole declared output name."""
        return ("close_minus_open",)

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact readable market columns."""
        return ("close", "open")

    @property
    def window(self) -> _WindowSpec:
        """Return the zero-reach CROSS declaration."""
        return _WindowSpec(max_prior_rows=0, gap_policy=_GapPolicy.CROSS)

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return identity-bearing parameters."""
        return {}

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap deterministic computation."""
        return _CostHint.CHEAP

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute close minus open for evaluation rows."""
        start = request.eval_offset
        stop = start + request.eval_length
        close = _np.asarray(request.column("close"), dtype=_np.float64)
        opened = _np.asarray(request.column("open"), dtype=_np.float64)
        return {"close_minus_open": close[start:stop] - opened[start:stop]}
```

Note the shape: slice the context arrays down to `[eval_offset : eval_offset +
eval_length]` and return. With `max_prior_rows = 0` there is no warm-up and
`BREAK` can never invalidate a row, because a single-observation window contains
no adjacent transition.

This example's `open` dependency is covered by the live leakage-rig synthetic
and adversarial columns. Those fixtures were enlarged in Phase 6 without
weakening their comparisons; `open` and `tick_volume` now join `high`, `low`
and `close` as covered market-value inputs.

### Example 2 — finite rolling window: rolling standard deviation

Proves non-trivial warm-up and a real vectorised computation. The window length
is a parameter, so it must appear in `params` **and** drive `max_prior_rows`.

```python
class RollingStdClose:
    """Trailing sample standard deviation of close over observed rows."""

    def __init__(
        self,
        window: int = 20,
        *,
        gap_policy: _GapPolicy = _GapPolicy.CROSS,
        nominal_interval: int | None = None,
    ) -> None:
        if type(window) is not int or window < 2:
            raise _FeatureContractError("rolling window must be an int >= 2")
        self._window_size = window
        self._window = _WindowSpec(
            max_prior_rows=window - 1,          # N observations -> N - 1 prior rows
            gap_policy=gap_policy,
            nominal_interval=nominal_interval,
        )

    @property
    def provider_id(self) -> str:
        """Return the stable implementation identity."""
        return "rolling_std_close"

    @property
    def version(self) -> int:
        """Return the provider contract version."""
        return 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the sole declared output name."""
        return ("rolling_std_close",)

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Return the exact readable market columns."""
        return ("close",)

    @property
    def window(self) -> _WindowSpec:
        """Return the configured finite reach and gap policy."""
        return self._window

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Return the identity-bearing window length."""
        return {"window": self._window_size}

    @property
    def cost_hint(self) -> _CostHint:
        """Declare cheap deterministic computation."""
        return _CostHint.CHEAP

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Compute trailing observed-row standard deviations."""
        close = _np.asarray(request.column("close"), dtype=_np.float64)
        result = _np.full(request.eval_length, _np.nan, dtype=_np.float64)
        if close.size < self._window_size:
            return {"rolling_std_close": result}

        windows = _np.lib.stride_tricks.sliding_window_view(close, self._window_size)
        deviations = windows.std(axis=1, ddof=1, dtype=_np.float64)
        positions = _np.arange(
            request.eval_offset,
            request.eval_offset + request.eval_length,
            dtype=_np.int64,
        )
        available = positions >= self._window_size - 1
        result[available] = deviations[positions[available] - (self._window_size - 1)]
        return {"rolling_std_close": result}
```

Points worth copying:

- Start from an all-`NaN` result and fill only what you can compute. The
  platform will blank warm-up anyway, but this keeps `compute` honest on its
  own.
- `sliding_window_view` is trailing by construction — window index `k` covers
  source rows `k … k + N − 1`, so the value for context position `p` is at
  window index `p − (N − 1)`. Getting this mapping wrong is the classic
  off-by-one, and the backward-reach rig will catch it.
- Guard the short-input case explicitly rather than letting NumPy raise.
- The provider never mentions gaps. `gap_policy` is passed straight into
  `WindowSpec`; the platform does the rest.

### Example 3 — parameters and identity

Same class, two configurations, three distinct cache identities:

```python
RollingStdClose(window=20)                                    # params {"window": 20}
RollingStdClose(window=50)                                    # params {"window": 50}
RollingStdClose(window=20, gap_policy=GapPolicy.BREAK,
                nominal_interval=60)                          # same params, different WindowSpec
```

- Configurations 1 and 2 differ in `params` → different identity → different
  cache entry. Correct.
- Configurations 1 and 3 have **identical `params`** but different `WindowSpec`
  → still different identity, because the resolved window (`max_prior_rows`,
  `gap_policy`, `nominal_interval`) is identity-bearing in its own right.
  Correct.

Now the counter-example — the mistake this section exists to prevent:

```python
class BadlyParameterised:
    def __init__(self, window: int = 20, scale: float = 1.0) -> None:
        self._window_size = window
        self._scale = scale                       # <-- affects output
        self._window = _WindowSpec(window - 1, _GapPolicy.CROSS)

    @property
    def params(self):
        return {"window": self._window_size}      # <-- but NOT declared. DEFECT.
```

`BadlyParameterised(20, scale=1.0)` and `BadlyParameterised(20, scale=2.0)`
produce the **same identity key**. The second run is served the first run's
cached numbers, silently and durably.

**Checklist:** every constructor argument that can change a returned number must
appear in `params`, or be reflected in `WindowSpec`. No exceptions.

---

## 9. Registration

Registration is one line at the bottom of
[`features/reference.py`](../../src/trading_research/features/reference.py):

```python
_FEATURE_PROVIDERS.add("rolling_std_close", RollingStdClose)
```

- The **registry name** is what config and tests use; keep it equal to
  `provider_id` — all three reference providers do.
- The second argument is a **factory** — normally the class itself.
  `FEATURE_PROVIDERS.create(name, **params)` calls `factory(**params)`, so your
  constructor's keyword arguments *are* the configuration surface. Every
  registered provider must be constructible with **zero arguments**.
- Duplicate names raise `DuplicateComponentError` at import time.
- `features/__init__.py` imports `reference` for its registration side effect,
  so importing `trading_research.features` populates the registry.

### What registration triggers automatically

This is the part people miss. **Registering a provider enrols it in the
release-blocking rigs**, whether or not you write a single test yourself.
[`tests/test_feature_leakage.py`](../../tests/test_feature_leakage.py) reads
`FEATURE_PROVIDERS.names()` at import and parametrises over it, so your provider
is immediately swept by:

| Rig | What it proves about your provider |
|---|---|
| **Future perturbation** (single axis + multi-segment seam) | Nothing at or before a cutoff changes when later **market values** are replaced with adversarial values. Multiple cutoffs, including post-gap. |
| **Backward reach** (single + seam) | Perturbing rows strictly before `keys[i − max_prior_rows]` never changes value `i`. |
| **Segment seam** | Multi-segment output is bit-for-bit identical to the single contiguous axis. |
| **`BREAK` isolation** (single + seam) | Wrapped in a `BREAK` declaration, post-gap output is unaffected by pre-gap values. |

Comparison is **exact** — NaN masks compared as bytes, values compared as raw
`uint64` bits. No tolerance, no `skip`, no `xfail`; an architecture guard
forbids introducing any.

### Guard inventories you must update

Several architecture guards pin the *exact* inventory of live providers. Adding
one necessarily updates those inventories, and the tests will fail until you do:

| File | Constant / test | Change needed |
|---|---|---|
| `tests/test_architecture.py` | `SLICE_2B_MODULE_SURFACES["features/reference.py"]` | add the class name to **both** sets (declared `__all__` and module public names) |
| `tests/test_architecture.py` | `SLICE_2C_LIVE_FACTORY_PROVENANCE` | add `"name": ("trading_research.features.reference", "ClassName")` |
| `tests/test_architecture.py` | `test_slice_2b_reference_providers_have_the_exact_protocol_surface` | add the class name to the iterated tuple so its 8-member surface is pinned too |
| `tests/test_architecture.py` | `exact_imports["features/reference.py"]` in `test_slice_2b_modules_have_no_io_authority_or_later_stage_capability` | only if your provider needs an import not already listed |
| `tests/test_feature_leakage.py` | `COVERED_PROVIDER_NAMES` | add the registry name |
| `tests/test_feature_leakage.py` | `_synthetic_values()` **and** `_adversarial_values()` | only if you require a market column outside `{open, high, low, close, tick_volume}` — the rigs assert your required columns are covered, and the adversary raises on a column it has no perturbation for |

Also add the class to `__all__` in `features/reference.py` (it is sorted).

The leakage harness sizes its synthetic axis from each provider's declared
reach. A provider with a large finite reach therefore remains subject to the
same exact single-axis, seam, backward-reach and `BREAK` assertions; do not add
a fixed small-axis exception, skip or tolerance.

> **Read this carefully.** [`CLAUDE.md`](../../CLAUDE.md) states that
> *"intentionally widening or weakening an allowlist or architecture test is an
> architecture change subject to the same review discipline as changing an
> accepted ADR boundary."* Extending these inventories to admit a legitimate new
> provider is an expected, unavoidable part of adding a feature — but it is a
> deliberate act, so:
>
> - **Only add names.** Never relax a comparison (`==` must not become `>=` or
>   `issubset`), never delete a pin, never add a marker or skip.
> - **Call it out explicitly** in the commit message or PR description so it is
>   reviewed rather than absorbed.
> - If making your provider pass requires anything beyond adding names, **stop**
>   — that is the signal in §12 step 12.

---

## 10. Required tests for a new provider

Focused tests go in
[`tests/test_feature_execution.py`](../../tests/test_feature_execution.py),
which already has the scaffolding you need (`_frame`, `_request`, `_key`,
`_float_vector`, and the `TestReferenceProviders` golden style). Use **synthetic
data only** — never real market values in a fixture.

Checklist:

- [ ] **Declaration test.** Assert the full 8-member surface exactly:
      `provider_id`, `version`, `feature_names`, `required_columns`, `params`,
      `window`, `cost_hint`. Mirror the existing `TestReferenceProviders`
      assertion tuple.
- [ ] **Hand-computed golden.** A tiny frame (5–8 rows) with values you worked
      out by hand. Not a re-implementation of your own formula in the test —
      literal expected numbers.
- [ ] **Warm-up.** Exactly `max_prior_rows` leading `NaN`s — no more, no fewer —
      and the first available row has the expected value. For a zero-reach
      provider, assert **zero** leading `NaN`s.
- [ ] **Gap behaviour.** With a synthetic gap and a `BREAK` declaration, assert
      exactly `max_prior_rows` `NaN`s after the discontinuity and a finite value
      at the next row. With `CROSS`, assert the value is computed across the
      gap.
- [ ] **Declared reach.** Assert that changing a row **before**
      `p − max_prior_rows` leaves the value at `p` unchanged. Normally, changing
      the row **at** `p − max_prior_rows` must change it too. If a documented
      conservative alignment envelope makes that endpoint unreachable, prove
      the actual farthest influence and every non-influential alignment term
      separately, record the exact delta, and obtain explicit review approval.
      Never fabricate an endpoint dependency merely to satisfy the usual recipe.
- [ ] **Exact dtype and shape.** `dtype == np.float64`, `ndim == 1`,
      `shape[0] == request.eval_length`.
- [ ] **`NaN` semantics.** Where your formula is undefined, assert `NaN` — and
      assert no `±inf` anywhere.
- [ ] **Registry inclusion.** `"your_name" in FEATURE_PROVIDERS.names()`, and
      `FEATURE_PROVIDERS.create("your_name")` returns your class with zero
      arguments.
- [ ] **Parameter identity.** If parameterised, assert `params` reflects every
      constructor argument, and that two different configurations produce
      different `params` and/or different `WindowSpec`.
- [ ] **Automatic rig coverage** — no new test to write, but you must confirm
      `tests/test_feature_leakage.py` passes with your provider registered,
      including `test_rig_coverage_is_an_independent_exact_match_for_the_live_registry`.
- [ ] **Architecture guards** — `uv run pytest tests/test_architecture.py -q`
      green after the inventory updates in §9.
- [ ] **Full gate before merge** — `./scripts/gate.sh` (pytest + ruff + strict
      mypy), unfiltered and green.

> **Do not register a provider until it can pass the live rig suite.** The rigs
> are release-blocking: a registered provider that fails them turns the whole
> gate red for everyone. Develop against a local unregistered instance first
> (the rig helpers accept a plain factory), and register only once it is clean.

---

## 11. Common failure patterns

Each of the first six has a dedicated adversarial control in
[`tests/test_feature_leakage.py`](../../tests/test_feature_leakage.py) that
proves the rigs catch it. They are listed here so you recognise the shapes.

| # | Anti-pattern | What it looks like | Caught by |
|---|---|---|---|
| 1 | **Forward shift** | `close[p + 1]` | future-perturbation rig |
| 2 | **Centered window** | `(close[p-1] + close[p] + close[p+1]) / 3` | future-perturbation rig |
| 3 | **Whole-dataset normalization** | `(close[p] - close.mean()) / close.std()` over the *entire* array | future-perturbation rig |
| 4 | **Peeking at the range end** | `close[p] + close[-1]` | future-perturbation rig |
| 5 | **Under-declared reach** | declares 5, reads `close[p - 50]` | backward-reach rig (passes the future rig!) |
| 6 | **`BREAK` provider reading pre-gap rows** | adds a pre-gap value to post-gap output | `BREAK`-isolation rig (passes the future *and* reach rigs!) |
| 7 | **Undeclared column read** | `request.column("volume")` with `required_columns = ("close",)` | `FeatureRequestError` at runtime |
| 8 | **Hidden environment dependence** | `os.environ`, wall clock, local config, module-global accumulator, unseeded RNG | architecture guards and/or `verify_determinism` |
| 9 | **Wrong array type** | returning `int64`, `bool`, `float32` or `object` arrays | output validation |
| 10 | **Returning `±inf`** | unguarded division by zero | output validation, frame minting, and artifact encoding |
| 11 | **Reserved namespace** | `feature_names = ("target.ret_1",)` | refused before any computation |
| 12 | **Future key spacing** | `keys[p + 1] - keys[p]`, or using `len(request.keys)` | **NOT caught by any rig — see §7. Prohibited by authoring rule.** |

Note the escalation in rows 5, 6 and 12: each escapes the rig above it. Row 12
escapes all of them, which is exactly why it is an explicit authoring
prohibition rather than something you can rely on the gate to find.

Two more that are not leakage but will still fail you:

- **Mutating the request arrays.** `request.column(...)` and `request.keys`
  return read-only views; assignment raises `ValueError: assignment destination
  is read-only`. Copy if you need a working buffer.
- **Emitting a name you did not declare**, or omitting one you did — refused
  with a message naming your provider and the exact difference.

---

## 12. Agent procedure: adding a feature safely

A deterministic workflow. Follow it in order; do not skip ahead.

1. **Read this guide** end to end, and skim
   [ADR-0021](../adr/ADR-0021-columnar-feature-contract.md).
2. **Inspect the closest existing reference provider** in
   [`features/reference.py`](../../src/trading_research/features/reference.py) —
   `BarRange` for zero-lookback, `LogReturn1` for a one-row window,
   `RollingMeanClose` for a parameterised rolling window. Copy its structure,
   its property style and its underscore-aliased import convention.
3. **Define the formula and the required columns.** Write the formula down
   explicitly, including what happens where it is undefined (that becomes
   `NaN`). List the exact market columns it reads.
4. **Determine `max_prior_rows` exactly.** Count the observed rows strictly
   before the evaluation row that the formula touches. An `N`-observation
   window is `N − 1`. When combining sub-windows, take the maximum. When in
   doubt, over-declare — never under-declare.
5. **Choose `CROSS` or `BREAK`.** There is no default. If `BREAK`, the caller
   must supply a positive `nominal_interval`; expose it as a constructor
   keyword, as `RollingMeanClose` does.
6. **List every semantic parameter.** Enumerate the constructor arguments; every
   one that can change a returned number goes into `params` (or is already
   captured by `WindowSpec`). Verify against the §8 Example 3 counter-example.
7. **Implement the provider only.** One class in `features/reference.py`, the
   eight-member surface and nothing else. No new module, no new helper layer, no
   change to `execution.py`, `core/`, `artifacts/` or `experiments/`.
8. **Register it** — `_FEATURE_PROVIDERS.add("name", Class)` plus the `__all__`
   entry — and update the guard inventories listed in §9, adding names only.
9. **Add focused tests** per the §10 checklist, in
   `tests/test_feature_execution.py`, synthetic data only.
10. **Run the rigs and architecture tests:**
    ```bash
    uv run pytest tests/test_feature_leakage.py tests/test_feature_execution.py -q
    uv run pytest tests/test_architecture.py -q
    ```
11. **Run the full gate:** `./scripts/gate.sh` — pytest, ruff and strict mypy,
    unfiltered, all green.
12. **Stop and raise, rather than work around.** If making the feature fit
    requires *any* of the following, halt and ask for an architecture decision:
    - a new gap policy, partial-window output, `min_observations` or `TRUNCATE`;
    - unbounded or expanding history;
    - reading another provider's output;
    - reading a column the view does not carry, or any new data-access route;
    - a non-`float64` or sparse output column;
    - relaxing, deleting or skipping any architecture guard or rig assertion;
    - a feature derived from future key-axis structure (§7);
    - touching `execution.py`, `core/`, `artifacts/` or the data layer.

> **Do not broaden a platform abstraction to add one feature.** The Phase-2
> contract is deliberately narrow. If a single feature seems to require widening
> it, the correct outcome is a recorded decision — not a quiet generalisation.

### Definition of done

- [ ] Provider implemented in `features/reference.py`, eight members exactly.
- [ ] Registered, with `__all__` updated.
- [ ] §9 guard inventories updated by **addition only**, and the change called
      out in the commit message.
- [ ] §10 test checklist complete, synthetic data only.
- [ ] Leakage/reach/seam/`BREAK` rigs green with the provider registered.
- [ ] `./scripts/gate.sh` green unfiltered.
- [ ] No production file outside `features/reference.py` modified.
- [ ] No audit record, dataset, view manifest or existing cache artifact touched.

---

## 13. Copy-ready feature request template

Paste and fill this to ask an agent (or a colleague) for a new feature. You do
not need to know anything about the implementation to fill it in.

```markdown
## Feature request

**Feature name (output column):**
    e.g. rolling_std_close

**Registry / provider id:**
    e.g. rolling_std_close   (leave blank to reuse the feature name)

**Mathematical definition:**
    Plain formula, per evaluation bar. State explicitly what happens where it
    is undefined (division by zero, insufficient data).
    e.g. sample standard deviation (ddof=1) of `close` over the trailing N
         observations, inclusive of the current bar.

**Required market columns:**
    e.g. close
    (available columns include: open, high, low, close, tick_volume, spread,
     real_volume — the view must be opened with the ones you need)

**Parameters (name, type, default, meaning):**
    e.g. window: int = 20 — number of observations in the trailing window
    List every knob that can change a number.

**Expected lookback (max_prior_rows):**
    Number of observed rows strictly BEFORE the evaluation row that the formula
    reads. An N-observation window is N - 1. Say "0" for current-row-only.
    e.g. window - 1

**Gap behaviour:**
    [ ] CROSS  — the window may span an arithmetic gap
    [ ] BREAK  — a gap resets the window; needs a nominal_interval
    nominal_interval (BREAK only): e.g. 60

**Is the computation deterministic?**
    [ ] Yes — same inputs always give bit-identical output
    [ ] No  — describe the source of randomness and the seed parameter
    (Non-deterministic providers must expose a `seed` parameter.)

**Expected cost hint:**
    [ ] CHEAP      — vectorised arithmetic (default; use this unless sure)
    [ ] EXPENSIVE  — genuinely heavy; opts the stage into caching

**Anything else the implementer must know:**
    e.g. known edge cases, expected NaN counts, a reference implementation to
         cross-check against.
```

---

## 14. References

Canonical sources. **If this guide conflicts with any of them, they win.**

**Contracts and decisions**

- [ADR-0021 — Columnar feature contract](../adr/ADR-0021-columnar-feature-contract.md) — the binding feature contract
- [ADR-0005 — GapIndex, not SessionModel](../adr/ADR-0005-gapindex-not-sessionmodel.md) — gaps are arithmetic only
- [ADR-0006 — Fixed stage pipeline](../adr/ADR-0006-fixed-stage-pipeline.md) — why there is no DAG engine
- [ADR-0007 — MaterializationPolicy; identity key vs content hash](../adr/ADR-0007-materialization-policy.md) — caching, identity, seeds
- [ADR-0012 — Two-tier splits](../adr/ADR-0012-two-tier-splits.md) — Amendment 1 derives the purge from your declared reach
- [ADR-0016 — Determinism tiers](../adr/ADR-0016-determinism-tiers.md) — what reproducibility is claimed
- [ADR-0003 — Columnar hot path](../adr/ADR-0003-columnar-hot-path.md) · [ADR-0010 — Two-plane evaluation and OutcomeSpec](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)
- [ADR index](../adr/README.md)

**Plans and policy**

- [docs/phases/PHASE2_FEATURES.md](../phases/PHASE2_FEATURES.md) — normative Phase-2 plan
- [docs/architecture/V1.md](../architecture/V1.md) — layer boundaries
- [docs/research/RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) — why leakage discipline exists
- [PROJECT_STATE.md](../../PROJECT_STATE.md) · [CLAUDE.md](../../CLAUDE.md)

**Code**

- [`src/trading_research/features/reference.py`](../../src/trading_research/features/reference.py) — the three live reference providers
- [`src/trading_research/features/registry.py`](../../src/trading_research/features/registry.py) — `FEATURE_PROVIDERS`
- [`src/trading_research/features/execution.py`](../../src/trading_research/features/execution.py) — plan validation, masking, frame assembly
- [`src/trading_research/core/features.py`](../../src/trading_research/core/features.py) — `WindowSpec`, `GapPolicy`, `CostHint`, `FeatureRequest`, `ParamValue`
- [`src/trading_research/core/interfaces.py`](../../src/trading_research/core/interfaces.py) — the `FeatureProvider` protocol

**Tests**

- [`tests/test_feature_leakage.py`](../../tests/test_feature_leakage.py) — release-blocking rigs and the six adversarial controls
- [`tests/test_feature_execution.py`](../../tests/test_feature_execution.py) — goldens, masking, ordering, refusal paths
- [`tests/test_architecture.py`](../../tests/test_architecture.py) — dependency budgets, exact surfaces, guard inventories
- [`scripts/gate.sh`](../../scripts/gate.sh) — the gate you must run
