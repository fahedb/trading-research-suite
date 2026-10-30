# ADR-0020 — Cost semantics quarantined until a dedicated audit

**Status:** ACCEPTED · **Date:** 2026-08-06 · **Ratified:** 2026-08-06 after the Phase 0B [independent review](../architecture/reviews/2026-08-06-independent-review.md)

## Context
The dataset carries a per-bar `spread` column whose exact semantics
(units, sampling point within the bar) are unverified, and the broker's
commission schedule, slippage characteristics, and lot value are
undocumented in this repository. Simulated PnL built on invented cost
assumptions is falsely precise and worse than no simulation.

## Decision
**Cost semantics remain UNVERIFIED until a dedicated audit**, which is the
entry gate of the simulation phase (Phase 4). The platform does not invent
spread interpretation, commission, slippage, or lot-value assumptions.
ExecutionSpec **reserves** explicit fields (delay, fill reference, spread
model, commission model, slippage model, position limits) but they remain
semantically unbound; no cost-bearing trade simulation runs before the
audit binds them from broker documentation.

## Amendment 1 — the gate is on claims, not on construction (2026-08-14, Phase-4 planning; **revised 2026-08-14** in pre-merge review remediation)

> **Revision note.** The first draft of this amendment made
> `cost_semantics: verified` a declaration the platform recorded but did not
> check — clause 5 read as an honour-system rule, and the Phase-4 plan called a
> false declaration "a documentation error". A research platform must not let a
> config self-certify an evidence state the repository has not reached. Clause 5
> is therefore replaced by clauses 5–7 below: **VERIFIED is fail-closed against
> a repository-controlled binding**. The closure rule it referred to is also
> restated, because the original was internally unsatisfiable (COST_AUDIT §0
> RM-2). Nothing in clauses 1–4 changed, and no owner decision changed.

The original decision reads, in practice, as *"no simulator until the audit
closes"*. Applied literally it blocks the wrong thing. The audit's open
questions — pip size, contract size, price side, commission basis, currency
conversion — determine whether a **number is true**, not whether the
**arithmetic that produced it is correct**. Hand-computed synthetic fixtures
verify the second without touching the first, and holding the simulator hostage
to external broker documentation would mean the arithmetic stays unverified
until the day it is first trusted.

The gate is therefore re-scoped, not relaxed:

1. **The audit becomes an explicit evidence record**,
   [COST_AUDIT.md](../research/COST_AUDIT.md), in which every required fact is
   either **VERIFIED with exact evidence** or **UNKNOWN with the exact evidence
   still required**. There is no third state and no plausible default. Owner-
   supplied starting values (currently: spread 20 pips, commission $0.06 per
   0.01 lot for XAUUSD) are recorded as **AUDIT INPUTS** — configuration, never
   evidence.
2. **The execution configuration declares `cost_semantics` explicitly**
   (`unverified` | `verified`), with no default, inside the identified
   `ExecutionCostBinding` that also carries the contingent cost and
   price-model fields ([ADR-0023](ADR-0023-phase-4-simulation-contracts.md)
   §8). It is part of the resolved config and therefore of
   `experiment_fingerprint`.
3. **A run with `cost_semantics: unverified` may execute** — including over
   published TRAIN — and is **permanently marked** in its `RunManifest`, its
   stored simulation summary and every Plane-B inventory row, in the spirit of
   `oos_touched`.
4. **Money-denominated output of an unverified run may not be represented as an
   authoritative real-money estimate**, and may not be the deciding evidence for
   a promotion, OOS or lockbox decision
   ([RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7).
5. **`cost_semantics: verified` is fail-closed, not self-declared.** `core`
   holds exactly one frozen module constant,
   `VERIFIED_COST_BINDING: ExecutionCostBinding | None`, which is `None` while
   the audit is OPEN. A binding may declare `verified` **only** if that
   constant is not `None` and the declared binding equals it field-for-field,
   including a `binding_digest` the platform recomputes from the binding's own
   canonical document. Otherwise the run is **refused at config resolution** —
   `unverified` is the only reachable state. No config file, CLI argument or
   edited artifact can produce a verified run, because the authority is the
   repository, not the input.
6. **The binding is created only by the closing commit.** Setting or changing
   `VERIFIED_COST_BINDING` is an architecture-level change under `CLAUDE.md`
   change control — the same discipline as widening an architecture
   allowlist — and happens in the commit that flips COST_AUDIT.md to CLOSED,
   together with the ADR-0023 amendment that binds the executable price path.
   Re-opening the audit resets the constant to `None`.
7. **Nothing is retroactively upgraded.** Because the binding id and digest
   enter `experiment_fingerprint`, closure yields new run identities. Runs
   recorded while the audit was OPEN keep `cost_semantics: unverified`
   permanently; they are re-run under the verified binding, never re-labelled.
8. **The closure rule is stated over closure-required facts only.** Facts the
   implemented arithmetic consumes must be VERIFIED with named evidence;
   effects V1 deliberately does not model or enforce (slippage, financing,
   latency and rejections, corporate actions, broker lot/stop constraints, the
   unread `spread` column) are classified as declared limitations and do not
   block closure — nor does closure claim they are zero. The mechanically
   checkable rule lives in [COST_AUDIT.md](../research/COST_AUDIT.md) §1.1.

The **fill reference is the one reserved field this amendment deliberately
leaves unbound.** Which side of the market the recorded OHLC series represents
is COST_AUDIT fact A6, still UNKNOWN, and it determines both the executable
fill price and the bracket levels derived from it. V1 therefore declares a
*named provisional* price model rather than a permanent one, and the verified
side-dependent binding is written only when A6 and B3 are VERIFIED
([ADR-0023](ADR-0023-phase-4-simulation-contracts.md) §8,
[COST_AUDIT.md](../research/COST_AUDIT.md) §3.0). Binding it now — in either
direction — would be inventing a cost semantic in the exact place this ADR
exists to prevent it.

The reserved-field list above is also **not a construction mandate**. Phase 4
binds `spread` and `commission` as explicitly united **money** scalars, fixes
the decision delay as contract semantics rather than a knob
([ADR-0023](ADR-0023-phase-4-simulation-contracts.md) §4), and **omits the
slippage and position-limit fields entirely** — there is no evidence to bind
them to (COST_AUDIT §4 D1) and a framework without evidence is decoration.
Their absence is recorded as a declared limitation, never as an implied zero.

The core prohibition is unchanged and is what this amendment protects: **the
platform never invents a cost semantic** — and, as revised, **it does not
accept an input's claim that one has been verified.**

## Alternatives considered
- Plausible-default costs now ("spread × point, zero slippage") — produces
  authoritative-looking numbers nobody can defend; rejected.
- **Trusting a declared `cost_semantics: verified`** and treating a false
  declaration as a documentation error — the first draft of Amendment 1.
  Rejected on review: evidence state is repository-governed, so it must be
  enforced by the repository. A field a user can set is not a gate.
- **Parsing COST_AUDIT.md at runtime** to discover the audit status — rejected:
  it makes prose load-bearing, is brittle, and is easily satisfied by editing a
  heading. A frozen, digest-bearing constant in `core` is smaller, exact, and
  changes only under change control.
- **A cost-binding service, database or registry** — rejected as speculative
  infrastructure for a single symbol and a single account
  ([ADR-0015](ADR-0015-v1-single-symbol-scope.md)). One constant that is `None`
  by default is the smallest thing that fails closed.
- Frictionless simulation as an interim mode — **resolved by Amendment 1 and
  rejected as a mode**: a switch that makes costs disappear invites reporting
  the flattering variant. Costs are always declared explicitly; a zero-cost
  sensitivity check is a run whose declared zeros are recorded in its identity.
- Keeping the gate on construction — would leave the arithmetic unverified
  until the first day anyone relies on it; rejected by Amendment 1.

## Consequences
Until Phase 4: research lives in plane-A forecast evaluation, which needs
no cost model. The audit is an explicit external-input dependency (broker
documentation), recorded in the roadmap as a blocking decision.

From Phase 4 (Amendment 1): the arithmetic is verifiable immediately against
hand-computed fixtures, while every money number carries a permanent,
machine-readable evidence status. An unverified number can therefore never
quietly become a quoted one, and closing the audit is a documentation act with
a recorded date rather than a silent change of behaviour.

From the revision: the evidence status is **enforced rather than trusted**, so
the worst case of a mis-declared config is a refused run instead of a
falsely-authoritative number; and because the executable price path stays
explicitly unbound, closing the audit can *correct* execution semantics instead
of having to defend a guess made a year earlier. The cost is one extra
obligation at closure — write the executable-price rules and create the binding
constant in the same commit (COST_AUDIT §8) — which is the work that would have
been required anyway, done at the moment the evidence exists.

## When to reconsider
At Phase 4 entry, or immediately if authoritative broker documentation
arrives earlier. Re-open the audit whenever the broker, the account type or
the instrument changes: a closed audit is closed for one named account and
symbol only.

## Related
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) ·
[ADR-0023](ADR-0023-phase-4-simulation-contracts.md) ·
[docs/research/COST_AUDIT.md](../research/COST_AUDIT.md) ·
V1_ROADMAP.md Phase 4.
