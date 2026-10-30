# From an idea to a research experiment

Describe what you want to investigate in your own words. The **Research
Experiment** Skill helps Codex clarify the idea, save the agreed experiment and
work out what needs to be implemented. It applies to new research, not only Gaps.

For example: **“I want to test whether a volatility spike predicts what happens
over the next twenty bars.”**

Codex first uses what you have already said and what is available in the
workspace. It asks a few relevant questions at a time: what counts as a spike,
which observations to use, what to measure, and what result would matter.
You do not need to begin with a technical configuration file.

Once the idea is clear, Codex shows you the concrete experiment. Your instruction
to proceed is recorded against that version. Later changes get a new version;
the original agreement and earlier attempts remain visible.

It then identifies the work:

- Existing components can be reused when their behavior matches the experiment.
- A missing indicator can be implemented and tested from its agreed definition.
- A derived field can be calculated when the necessary source observations exist.
- Missing source data needs a suitable source and validation. It is never invented.
- An unsupported capability is reported explicitly, together with what it needs.

With Codex's code-editing tools and the suite checkout, the Skill guides component
implementation and verification. A completed design is not yet proof that an
experiment can run. Data admission, tests, executable configuration and the
execution budget must be checked too. Research results retain their sample-size,
cost and methodology limitations.

## What works today

The plugin includes the general Skill plus tools to inspect component/dataset
metadata, find missing specification fields, freeze a revision and restore it.
The saved specification is in your external workspace. The existing Gaps tools
continue to read and rerun their exact supported template.

Generic one-command MCP execution, guided CSV admission and catalog downloads
are not implemented. Component integration uses the coding host's normal tools;
a client with only MCP receives a clear handoff when it reaches those limits.
The Skill itself does not give an LLM extra filesystem, execution or data access.

Install or update using [Research with Codex](CODEX.md), start a new session, and
describe your experiment. You can explicitly ask Codex to use
`$research-experiment`. The first integration target should be one concrete new
experiment so the interview, implementation and results can be checked together.

## For developers

The entry point is
[`research-experiment/SKILL.md`](../plugins/trading-research/skills/research-experiment/SKILL.md).
Its references contain the interview, specification and integration procedures.
[ADR-0031](adr/ADR-0031-experiment-authoring-workflow.md) records the boundary.

`experiment_review` checks structural completeness and returns the exact digest.
It cannot prove that free text is scientifically unambiguous or that a claimed
implementation exists. Codex must inspect evidence and resolve those questions.
`experiment_freeze` binds the assistant-reported user instruction to that digest;
it is a local record, not user authentication or a substitute for access checks.

Automated tests exercise missing fields, conditional strategy requirements,
immutable revisions, retry/concurrency behavior, digest mismatches and the real
MCP protocol. Skill validation and host discovery check packaging. These checks
do not establish the quality of an LLM-led interview or autonomous integration
of every possible research idea; a new-experiment walkthrough is still needed.
