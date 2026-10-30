---
name: gaps-research
description: Explain the Gap Sensitive Levels H1 study, read its recorded results, open its Explorer view, or run its exact frozen TRAIN template using the Trading Research MCP tools.
---

Use `research_capabilities` to check local setup, then `research_study` for the
frozen template. The bundled historical report is documentation; local results
come from `research_results`, whose default is the original recorded run.
If files are unavailable, say so. Never substitute the synthetic UI demo or
historical prose for results read from this user's workspace.

For a new idea or a changed Gaps hypothesis, use the bundled `research-experiment`
Skill. The exact Gaps tools remain unchanged; a general specification is not a
Gaps launch plan. Do not route new experiments into the frozen template.

For a results request, explain the question, sample size, exit reasons and
limitations in the user's language. Start with a short plain-language answer.
Offer the returned Explorer link only when `explorer.available` is true. A
loopback link belongs to the machine running the tools; remote users need a
local port forward. Keep native market keys as broker wall-clock, not UTC.

Costs and execution are unverified, and no Plane-A edge is established. Money
is conditional. Show fold-boundary closes prominently when they dominate.
Drawdown is the worst individual fold, not combined portfolio drawdown.

When the user requests a rerun, use `research_datasets`, `research_prepare`, then
`research_start` with that plan ID. Existing explicit execution authorization
is sufficient; a request to explain results alone is not execution authorization.
Use `research_status` to observe the returned run without reconciliation, then
`research_results` when complete. Reuse the same plan ID on a repeated start
request; creating a new plan means a separate attempt. An uncertain launch
requires operator inspection: never remove its receipt or invent a run ID.

The first release supports only this frozen template and its exact TRAIN
dataset. Personal import, catalog download and parameter variants are not
implemented. Do not edit the config, relax access rules or use a shell workaround
to expand the tool's scope. A new run from the public checkout has a different
code identity from the historical source run; preserve both identities.
