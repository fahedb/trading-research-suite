# ADR-0024 — Multiple-testing denominator: the Trial Ledger, and no adjusted statistic in V1

**Status:** ACCEPTED · **Date:** 2026-08-15 · Added during Phase-5 planning ·
**Owner decisions:** OD-5A (with OD-5B, OD-5E and OD-5F) **OWNER APPROVED
2026-08-15**, recorded in
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §1

## Context

[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) Amendment 1 left one
question deliberately open:

> *"The exact multiple-testing denominator — how inventory entries map to
> independent trials, and how correlation between fanned-out variants is
> treated — is a blocking decision before any deflated statistic ships in
> Phase 5."*

[V1.md](../architecture/V1.md) §7 and
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §6 repeat it. Phase 5 is
the phase that makes trying a hundred variants a single command, so it is
also the phase in which that question must be answered before the capability
lands.

Two distinguishable jobs hide inside the open question:

- **Job A — count honestly.** How many distinct things were actually tried?
- **Job B — adjust a number for having tried that many.** Convert a raw
  metric into a statistic that survives the search.

Phase-5 planning inspected the repository against both. **Job A is fully
achievable from what the platform already records. Job B is not**, and this
ADR records why, rather than leaving the appearance that it was merely
postponed for convenience.

What the platform records per run, from `evaluation_inventory.json`
(schema `research-evaluation-inventory-v2`) and the run manifest: the
evaluation plane; `fitted_state_identity` (which differs per fold);
`model_output_artifact_identity`; the evaluator origin; metric names and
`MetricValue` scalars with `effective_samples`; on Plane A,
`evaluated_output_columns` and the OutcomeSpec origin with `horizon_rows` and
`decision_delay_rows`; on Plane B, the `DecisionPolicy` origin, the
`ExecutionSpec` and the cost binding; `policy_executions[]` recorded
**separately** from evaluations; `outcome_class`; and per run
`experiment_fingerprint`, `oos_touched`, `lockbox_touched`, `canonical` and
state.

What the platform does **not** record: any measure of how correlated two
variants are, any per-trial series of a statistic, and any null distribution.
Evaluators return `MetricValue(name, value, effective_samples)` — a metric,
not a test statistic. There is no p-value anywhere in the repository, and
[ADR-0023](ADR-0023-phase-4-simulation-contracts.md) with
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) deliberately exclude every
return-ratio statistic while no verified account base exists.

## Decision

### 1. The trial unit

**One trial is one distinct `(hypothesis, question)` pair that produced at
least one evaluation**, where:

- **hypothesis** := `experiment_fingerprint`
  ([ADR-0011](ADR-0011-run-identity-and-canonical-runs.md));
- **question** is composed from evaluation-inventory dimensions:
  - **Plane A:** `(evaluated_output_columns, outcome.plugin_id,
    outcome.version, outcome.params, horizon_rows, decision_delay_rows,
    evaluator.plugin_id, evaluator.version, evaluator.params, metric_name)`;
  - **Plane B:** `(policy.plugin_id, policy.version, policy.params,
    execution_spec, evaluator.plugin_id, evaluator.version,
    evaluator.params, metric_name)`.

Plane B does not carry an OutcomeSpec, horizon, decision delay or evaluated
output-column tuple. Those are Plane-A question dimensions and must not be
invented or inferred for a trade evaluation. The experiment fingerprint
already identifies the fixed model, data and resolved configuration; the
Plane-B tuple distinguishes the actual policy/execution/evaluator questions
asked within that hypothesis.

**Plane A and Plane B are counted separately and are never summed** into one
headline number. They answer different questions about different artifacts
and their denominators are not interchangeable.

### 2. Collapse and exclusion rules

- **Folds collapse.** Distinct `fitted_state_identity` values within one
  `(hypothesis, question)` do **not** multiply the trial count. Walk-forward
  folds are *procedural* — part of a modelling procedure, not integrity
  boundaries ([ADR-0012](ADR-0012-two-tier-splits.md),
  [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §4) — so K folds are K
  measurements of one hypothesis, not K hypotheses. Counting them would also
  create a perverse incentive to use fewer folds.
- **Attempts collapse.** Multiple `run_id`s sharing one
  `experiment_fingerprint` — retries, and the two runs of a determinism
  verification — do **not** multiply the trial count.
- **Descriptive runs contribute zero.** A run that legitimately ends before
  any evaluation is a recorded execution, not a statistical evaluation.
- **Pre-evaluation failures contribute zero.** Syntax errors, environment or
  disk failures, crashes before evaluation and platform-bug contract failures
  produced no evaluation. They remain permanently recorded failed executions
  and are never deleted or hidden.
- **Executed-but-unevaluated policies contribute zero.**
  `policy_executions[]` is reported, never counted.
- **Policy fan-out affects Plane B only, and only where an actual Plane-B
  evaluation occurred.** A Plane-A forecast evaluator consumes a
  `ModelOutputFrame` and OutcomeSpec targets and never sees an `IntentFrame`
  ([ADR-0009](ADR-0009-generic-output-and-intentframe-family.md),
  [V1.md](../architecture/V1.md) §7), so policy fan-out alone does not
  multiply Plane-A evaluations.

Every field this rule reads exists in the corresponding Plane-A or Plane-B
entry in `evaluation_inventory.json` today. The rule is **fully mechanical
and computable from files as they are**, with no evaluation-inventory schema
change (owner decision OD-5E; Plane-B reconciliation formally owner-approved
as OD-5F on 2026-08-15, with the approval provenance recorded below).

### 3. Fold identity rests on a pinned invariant, not on a new field

`ResolvedExperimentConfig` carries exactly one model, so within one run the
distinct `fitted_state_identity` values under one *question* are the folds.
V1 keeps the existing inventory representation and **pins that invariant with
an executable canary test** (PHASE5_EXPERIMENT_OPS.md T-18). **If the
invariant ever ceases to hold, the test must fail loudly and this schema
decision must be reopened before trial counting continues.**

### 4. Correlation is acknowledged, not estimated

- Related sweep variants **can be, and usually are, correlated**: neighbouring
  parameter values produce highly correlated signals.
- **The platform does not currently record enough information to estimate a
  defensible effective number of independent trials.** Doing so requires one
  of: the joint distribution or covariance of the trial statistics (needing a
  per-trial *series*, where the platform stores one scalar per entry); a
  declared parametric correlation model (which the repository has not
  approved); or a resampling procedure over full per-trial return series
  (which the reporting layer does not have and which would require defining a
  null).
- **Phase 5 therefore counts those variants individually**, as a conservative
  mechanical denominator. Over-counting is the safe direction; collapsing
  correlated variants toward one would understate the search burden.
- **The platform does not claim those trials are statistically independent.**
  Every report carries a mandatory caveat saying so and stating that the true
  effective number of independent trials is smaller than the figure shown and
  is not estimated here.

Related: [ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) Amendment 2's
commit-sensitivity over-segmentation is likewise left uncorrected. It inflates
the denominator, which is the conservative direction; the hypothesis/comparison
key that would merge such runs is deliberately **not** built, because merging
shrinks the denominator.

### 5. No adjusted or deflated statistic ships in V1 — **OD-5A**

**Phase 5 ships no deflated statistic, adjusted metric, p-value, significance
claim or effective-independent-trial estimate.** Specifically excluded:
Deflated Sharpe Ratio, Bonferroni, Šidák, Benjamini–Hochberg, White's Reality
Check, Hansen's SPA, and any equivalent correction.

The repository lacks the approved statistical inputs such a statistic needs:

- **No p-values.** Evaluators produce metrics without null distributions.
  Bonferroni, Šidák and Benjamini–Hochberg all require p-values.
- **No Sharpe or any ratio statistic.**
  [ADR-0023](ADR-0023-phase-4-simulation-contracts.md) and
  [ADR-0020](ADR-0020-cost-semantics-quarantine.md) exclude them while no
  verified account base exists, so the Deflated Sharpe Ratio has no input.
- **No per-trial statistic series.** Reality Check and SPA need full per-trial
  return series and a defined null.

Manufacturing any of those inputs — assuming an IID null to turn a rank
correlation into a p-value, or inventing a correlation model — would be
**inventing a methodology the repository has not approved**, which violates
the research-integrity goals this platform exists to serve. An adjusted number
carries more authority than a raw one and is far harder to withdraw once
quoted; publishing a fabricated adjustment would be strictly worse than
publishing none. This is also the literal instruction ADR-0011 Amendment 1
already gave: *"no run-count-based correction may be published in the
meantime."*

The existing architecture guard forbidding the term `deflated` in production
code is **not weakened**. It becomes the mechanical enforcement of this
clause.

### 6. What ships instead: the Trial Ledger

The platform reports the **denominator**, honestly computed and mechanically
derived, printed beside every comparison: Plane-A and Plane-B trial counts;
distinct hypotheses; runs examined and attempts collapsed; fold multiplicity
(reported, never multiplied); descriptive runs and pre-evaluation failures
(shown, excluded from the denominator); policies executed but not evaluated;
OOS and lockbox evaluated-run counts, in-scope linked access-event counts and
audit-log-wide access-event totals; unverified-cost-semantics entries;
noncanonical runs; collapsed sweep duplicates; and the
`effective_samples` distribution.

**Default scope is all runs in the runs root (OD-5B)**, with explicit
narrowing by sweep or time range recorded in the report header. The default
exposes the cumulative research-search burden rather than a flattering local
subset.

**Every number the ledger prints must be reproducible from run directories
without the SQLite index**, which is what keeps the derived index free of any
statistical authority ([ADR-0017](ADR-0017-storage-files-as-truth.md)).

### 7. Run-to-audit linkage is plural and preserves the frozen audit schema

The current experiment pipeline performs one dataset authorization for one
`run_id`, and a retry creates a new `run_id`. That is an implementation fact,
not a safe cardinality contract: run state already accumulates access facts
across calls, and the bounded-view contract accepts a sequence of grants.
Phase 5 therefore must not persist a singleton audit reference.

Each run records an ordered, append-only `access_audit_refs` collection. Each
entry contains the authorization invocation's UUID4 `session_id` and its
optional `audit_event_id`. The session is recorded before authorization. If
authorization returns an `AccessGrant`, its event identifier (if any) is
attached to that same entry. A denied protected access writes its audit event
before raising, so the pre-recorded session still provides an exact join even
though no grant — and therefore no event identifier — is returned. One
authorization invocation may match at most one access-audit event; duplicate,
missing or contradictory matches are reported as discrepancies and are never
silently reconciled.

The join is bidirectional. After resolving all in-scope run references, the
ledger walks every authoritative audit access event whose dataset role and
classification identify OOS or LOCKBOX experiment access. Any such event not
referenced by an in-scope run is reported as `audit_unreferenced`. The report
shows both in-scope linked counts and whole-audit totals; a narrowed sweep/time
scope therefore cannot silently reduce the visible protected-access history.
Fully-gated historical access to RAW, CURATED or SNAPSHOT is not a relevant
experiment event and is excluded by role, even though it shares the
`fully_gated` classification used by LOCKBOX.

This shape supports zero, one or many authorization invocations per run and
does **not** change the append-only audit-event schema. The terminal run
manifest carries the collection, so both index-backed and files-only Trial
Ledger implementations use the same durable linkage. The audit log remains
authoritative; a manifest stores links, never duplicated audit truth.

**OD-5F approval provenance (independent pre-merge review F-1).** The review
returned REQUEST CHANGES because the repository did not contain sufficient
owner provenance for the combined decision. The owner then explicitly
approved OD-5F in full on **2026-08-15**: Plane B is keyed by policy,
execution specification, evaluator and metric name, with model/data/config
distinctions supplied by `experiment_fingerprint`; Plane-A-only Outcome fields
remain absent; and `access_audit_refs` stays plural with minimal `session_id`
plus optional `audit_event_id`. This is an owner approval record, not an
implementation attestation or a retroactive claim about when implementation
occurred.

**Implementation status (2026-08-15).** Run state schema v2 and manifest
schema v3 implement the ordered collection while retaining read compatibility
with earlier state/manifest schemas. The orchestrator persists a UUID4 session
before every gate call and attaches the returned event ID afterward when one
exists. The Trial Ledger joins every plural reference, treats a protected run
with no reference as a discrepancy, checks event/session/role/outcome and
classification exactly, and has files-only/index equivalence plus allowed and
denied multi-reference tests.

The clean Phase-5 qualification at
`b9f74ea748c399be657335aaf38ac9492da57b7d` produced identical files-only and
index-backed ledgers. Across its two real 100-child TRAIN sweeps the ledger
reported 200 distinct hypotheses, 200 Plane-A trials, zero Plane-B trials and
zero audit discrepancies; the append-only audit log was byte-identical before
and after. This is implementation evidence only and does not substitute for
CP-20's required independent review and explicit owner approval.

**CP-20 closeout status (2026-08-16).** The independent review and its bounded
F-1…F-7 remediation follow-ups passed at exact reviewed head
`22c08a27ef9f052e3c00cf605cc10c2962d1469a`. The owner explicitly approved
CP-20, Phase-5 closeout and merge on 2026-08-16. The review and closeout made
no change to this methodology: the Trial Ledger remains the only V1
multiple-testing output, the audit log remains authoritative, and no adjusted
statistic, p-value or significance claim was introduced.

## Alternatives considered

- **Divide by run count.** Rejected, and already rejected by ADR-0011
  Amendment 1: it both understates (one run yields many inventory entries) and
  overstates (attempts share a fingerprint), and would make any correction
  cosmetic.
- **Count every inventory entry, including folds.** Rejected: it inflates the
  denominator by the fold count for no informational gain and penalises the
  good practice of using more folds.
- **Estimate an effective number of independent trials.** Rejected for V1:
  the required inputs are not recorded, and every available shortcut requires
  an unapproved assumption.
- **Ship Bonferroni as "at least something".** Rejected: it needs p-values the
  platform does not produce, so shipping it would mean inventing them.
- **Defer the whole report to a later phase.** Rejected: it leaves ADR-0011's
  blocking decision open indefinitely while the capability that makes it
  urgent ships anyway, and it forgoes the one integrity mechanism Phase 5 is
  uniquely positioned to deliver.

## Consequences

- ADR-0011 Amendment 1's blocking decision is **resolved** (see
  [ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) Amendment 4). Phase 5
  is unblocked without shipping a statistic the evidence cannot support.
- Raw run count remains an invalid denominator. The **evaluation inventory is
  the only source** for trial counting.
- Rankings become unreadable without their denominator, by construction: the
  caveat and the trial context travel with every comparison.
- The `deflated` architecture guard gains a second purpose and must stay.
- Researchers must continue to reason about search burden themselves. The
  ledger counts *evaluations*, not *knowledge* — and, as
  [RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §3 states about the
  lockbox, nothing tracks what a researcher now knows.

## When to reconsider

Reopen in a **new ADR** — never by quiet extension of this one — when **all**
of the following hold:

1. an explicit statistical methodology is proposed and owner-approved,
   naming its null hypothesis, its assumptions and its failure modes; **and**
2. the inputs that methodology requires are **actually persisted** by the
   platform — for example per-trial statistic series, or a metric with an
   approved null distribution, or a verified account base making a ratio
   statistic constructible; **and**
3. the treatment of correlation between related variants is stated explicitly
   rather than assumed away.

A change to the one-model-per-config invariant (§3) is a separate, narrower
trigger: it reopens the fold-identity representation only, and must be
resolved before trial counting continues.

## Related

[ADR-0011](ADR-0011-run-identity-and-canonical-runs.md) (run identity, trial
counting, Amendment 4) ·
[ADR-0009](ADR-0009-generic-output-and-intentframe-family.md) (why policy
fan-out is Plane-B only) ·
[ADR-0010](ADR-0010-two-plane-evaluation-and-outcomespec.md) (the two planes) ·
[ADR-0012](ADR-0012-two-tier-splits.md) (folds are procedural) ·
[ADR-0017](ADR-0017-storage-files-as-truth.md) (the index is derived, so the
ledger must be reproducible from files) ·
[ADR-0020](ADR-0020-cost-semantics-quarantine.md) and
[ADR-0023](ADR-0023-phase-4-simulation-contracts.md) (why no ratio statistic
exists) ·
[RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §6 ·
[PHASE5_EXPERIMENT_OPS.md](../phases/PHASE5_EXPERIMENT_OPS.md) §14 ·
[V1.md](../architecture/V1.md) §7.
