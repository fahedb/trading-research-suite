---
name: research-experiment
description: Turn a user's trading research idea into an explicit experiment, resolve missing decisions, freeze its specification, and integrate required indicators or research components in a coding-capable host. Use for new experiments and strategy variants; use gaps-research for reading or exactly rerunning the existing Gaps study.
---

# From idea to experiment

Lead the user from their own idea to a precise, versioned experiment and its
implementation. Do not force new ideas into Gaps or a preselected strategy.
Respond in the user's language, explain the next decision plainly, and keep
implementation details in saved artifacts unless they affect a research choice.

## Understand and complete the idea

Extract everything already stated in the conversation and existing research
documents. Distinguish a predictive question, a simulated trading strategy and
a descriptive study; an indicator alone is not a complete strategy.

Use `experiment_inventory` for registered component names and permitted-view
metadata. It does not prove that a named indicator has the requested formula.
Inspect the applicable component declarations and guides before promising reuse.
If tools or workspace setup are unavailable, continue the interview and explain
which operations cannot yet be performed. Never substitute the Gaps dataset.

Read [the interview guide](references/interview.md). Ask only unresolved decisions
that affect the hypothesis, observations, execution or interpretation. Prefer
one to three related questions at a time; explain meaningful alternatives.
Use existing authorization and delegated choices. Record proposed assumptions
explicitly; never silently pick a trading rule, timestamp meaning or data source.

Build the structured specification described in
[the specification reference](references/specification.md). Use
`experiment_review` as the draft evolves. Its missing-field list is a mechanical
check, not proof that the prose is unambiguous. Resolve contradictions and vague
terms yourself. Do not fill fields with “TBD”, “standard”, “whatever” or invented
evidence to make the validator pass.

## Freeze the agreed experiment

Present a short, concrete description of what will be tested, what counts as
success, the assumptions and the expected implementation/data work. Material
unknowns remain questions. Known missing implementations may remain tasks.

Once the user's instruction covers this exact specification, call
`experiment_freeze` with the digest returned by `experiment_review`, the real
supporting user message and its scope (`integrate` or `integrate_and_run`).
Ask for clarification only if that authorization is absent or new material
choices have not been accepted. Never manufacture a confirmation or treat
silence as acceptance. Freezing records an agreement; it does not launch a run.

Keep the returned revision and digest. On resume, use `experiment_get` before
working. A changed hypothesis or substantive convention needs the next revision
linked through `supersedes_sha256`; retain earlier revisions and unsuccessful
attempts. Do not modify the original Gaps study to stand in for a variant.

## Integrate what is missing

Read [the integration procedure](references/integration.md). Map each dependency
to evidence: available, derivable, needing code, needing source data, or
unsupported. Separate a missing indicator from missing observations.

In a coding-capable host with an authorized checkout, implement missing
components through the existing platform contracts and test them in an isolated
branch/worktree. Follow repository instructions and the relevant authoring guide.
A missing source dataset requires an explicit permitted source and admission;
never invent market observations, silently fill gaps or fetch arbitrary URLs.
External timed feeds/cross-clock joins are not implemented in V1.

Maintain a small integration record outside the checkout: frozen digest,
component mapping, data identities, changed code, test evidence, remaining
blockers and next action. A code patch, a passed schema and a frozen spec are
different milestones. Mark each only when its evidence exists.

## Execute and explain

Execution requires the frozen scope to include running, admitted and verified
data, a resolved engine config, passing required checks, bounded run/resource
limits and recorded clean code/lock identities. Use the existing engine's
documented detached CLI from a capable development host; record the invocation
and returned run ID before any retry. An uncertain start needs inspection, not
a fresh attempt. A pure MCP host currently has no generic launch tool: hand off
the ready artifacts and name that missing capability explicitly.

The `research_prepare`/`research_start` tools still execute only the exact Gaps
template. Never pass a new specification to them or alter their guard as part of
a study. They are not the general launch path. Follow the integration reference
for execution prerequisites and evidence, including Plane-A-first methodology.

Explain recorded results with sample sizes, unsuccessful/empty outcomes, costs
and limits. Only promise an Explorer trade view when actual TradeLogs exist;
descriptive/forecast-only results can be valid without trades. Do not tune the
frozen experiment after seeing results. A follow-up is a separately agreed
revision or hypothesis with its own recorded trial.
