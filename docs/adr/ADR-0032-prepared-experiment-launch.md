# ADR-0032 — Checked preparation and general experiment launch

Status: ACCEPTED for this distribution following the owner's instruction to
implement readiness checks, missing-component integration and an enforced launch
gate, and demonstrate the complete flow with a new small experiment.

## Decision

Extend the existing experiments composition root with a `preflight` CLI command.
It uses the engine's existing component resolver, normal TRAIN/VALIDATION access
grant, full-integrity loader, bounded views, ephemeral feature computation and
fold generator. It never fits, evaluates, registers a run, writes feature caches
or reads protected data. It checks actual feature and target availability and finite usable
rows in every fit/evaluation fold. Runtime failures remain possible; preflight is
not an evaluation or scientific validation of prose.

The external MCP adapter adds check, checkout verification, prepare, start,
status and result tools for general fold-fit experiments. Frozen cross-run models
and protected data remain outside this first general launch interface. Checks
return explicit blockers. A preparation additionally requires a passing checkout
quality-gate receipt for the exact clean source/lock identities. The receipt is
issued by actually running fixed repository checks, not by accepting a claimed
test result. Source checkout is trusted executable code, not a sandbox.

Bind the immutable specification, explicit component-to-config mapping, resolved
config, dataset metadata, split policy, code/lock identities and check evidence
to a prepared plan. Mapping verifies coverage and component kinds, not semantic
equivalence of arbitrary free text. The Skill remains responsible for evidence
and agreed research meaning. Missing source observations are never synthesized.

At start, revalidate preparation and fully reread/hash inputs in a fresh engine
process. Reserve one durable launch receipt under the existing adapter launch
lock, enforce the frozen revision's attempt budget, and launch through the
existing detached engine CLI. Identical retries return the same receipt;
uncertain launches block further starts. Do not change Gaps template guards.
Both adapter paths share the active-run exclusion. Bind numerical limits to the recorded approval and enforce them within the
supported maximum admitted row count, time and address space; add optional engine worker CPU,
address-space and alarm limits propagated through detached launch. The worker
continues to own run-state transitions and failure recording.

## Demonstration and architecture scope

Provide an explicitly synthetic, deterministic TRAIN example in a new external
directory, with its own named native clock and versioned synthetic split policy.
No original datasets or research stores are modified. Demonstrate rejection of
the missing feature, add a finite trailing close-position feature and a stateless
close-position score model through existing contracts, test their arithmetic/leakage,
then prepare and execute one predictive experiment. Synthetic results establish
workflow behavior only, never trading edge.

The existing closed CLI/component inventories gain only these named additions;
all import, causality, access, signature and lifecycle assertions remain intact.
This is an additive interface within the existing composition root, not a new
stage or dependency direction. General launch supersedes ADR-0031's temporary
missing-launch limitation; personal CSV admission and catalog downloads remain
separate work. Update the Skill to require the checked MCP path for supported
general experiments and report unsupported modes explicitly.

## Verification

Cover missing implementations/columns/history, invalid component mappings,
protected access refusal before market reads, corrupted data, stale code/config/
policy, missing or failed checks, revoked scope, concurrent/idempotent start and
attempt exhaustion. Preserve all existing architecture and engine gates. Verify
a real detached run after client disconnect and read its hash-checked results.
