# Codex integration: first release plan

Status: version 0.3 supports the frozen Gaps workflow and checked general
fold-fit experiments on admitted TRAIN/VALIDATION data. See [setup](CODEX.md),
[authoring](EXPERIMENT_WORKFLOW.md) and [general execution](GENERAL_EXPERIMENTS.md).
A standalone synthetic research example is included. Personal import and catalog
download remain planned. The original delivery plan below records how the
integration was built; current execution boundaries are in ADR-0032.

## The first working experience

A user installs the suite, connects it to Codex CLI, and asks for a small research
experiment. The engine runs it locally, the assistant explains the recorded
results, and the user opens the actual trades in the Explorer.

The owner selected the existing frozen Gap Sensitive Levels H1 TRAIN study as
the first template. The first slice reads its stored results and supports an
exact-template rerun in a separate workspace. Personal-data import and a downloadable dataset catalog follow that
slice and remain requirements for the broader release.

## What exists and what is missing

| Component | Current state | Remaining work |
| --- | --- | --- |
| Research engine | Strict configurations, features/models/policies, evaluations, simulation, immutable run records, verification | Synthetic research example included; broader user walkthroughs remain |
| Engine commands | Exact Gaps adapter plus checked general fold-fit plans | No generic shell or arbitrary-code tool |
| Background execution | Detached engine worker verified after MCP disconnect on Linux | More platforms and installation environments |
| Explorer | Real result readers, run overview, trade charts and timeframes | Start/configure it for the user's workspace and publish the completed run to its index |
| Existing synthetic demo | Generated in-memory prices and trades for UI preview | It is not a research run and cannot stand in for the engine example |
| MCP server | Local stdio server with seven Gaps tools and ten general tools | Guided data admission and broader supported modes |
| Codex plugin and Skills | Plugin, local marketplace, Gaps and general experiment Skills, and installer | A complete new-experiment walkthrough, public directory submission and more platforms |
| Personal data | Engine accepts admitted, manifest-backed datasets | Guided import, column/time interpretation, validation and admission |
| Downloadable catalog | Absent | Approved packages, catalog metadata, integrity checks and download/install flow |

The existing example configurations reference external datasets. Engine tests
already construct synthetic catalogs and execute real pipelines, but those test
helpers are not a supported installation or demonstration API.

## Proposed boundaries

```mermaid
flowchart LR
    A[Codex CLI and Skills] --> B[Local MCP adapter]
    B --> C[Existing engine commands]
    C --> D[Dataset and run workspace]
    D --> E[Read-only Explorer]
    B --> E
```

The adapter is an external integration package, outside
`src/trading_research/`. The engine remains the execution authority and the
Explorer remains a reader. The adapter must not become a second simulator,
evaluation implementation, run-state authority or job scheduler.

Use the engine's JSON command interfaces for execution and indexing. Use
supported read interfaces for observations. Any missing public boundary must be
documented and covered by an ADR before implementation; do not reach into
private helpers simply because tests use them. No existing architecture guard
needs to be relaxed for this plan.

### Important details found in the code

- **A chart needs actual simulation output.** The Explorer lists completed runs
  with `policy_execution_count > 0`, not every descriptive or forecast-only run.
  The example must produce TradeLog artifacts. Forecast evaluation and simulation
  results must retain their separate meanings.
- **Status is not automatically read-only.** The existing engine `status` and
  `list` commands call a reconciling scan that can terminalize stale runs.
  A tool advertised as read-only must use an established non-mutating path, such
  as `scan_runs(..., reconcile=False)`, rather than those commands. Index rebuild
  already scans without reconciliation and writes only the derived index.
- **Background execution currently targets Linux.** Workers use `systemd-run
  --user` with a `tmux` fallback; other paths use `fcntl`. Start with Linux and
  Codex CLI. Windows, WSL and macOS compatibility require separate validation.
- **The installed plugin is not necessarily a Git checkout.** Canonical runs
  require a clean recorded repository and its engine lockfile. Provision a
  version-pinned suite checkout separately from plugin caches and keep generated
  data/configurations/results outside it. A wheel or ZIP without Git evidence
  must not fabricate a commit or be presented as canonical.
- **Read results from files.** SQLite is a disposable listing accelerator. The
  assistant must preserve dataset identity, run/fold references and recorded
  cost limitations when explaining results.

## Initial MCP tools

General authoring tools added in version 0.2 (version 0.3 execution tools are
documented in [the walkthrough](GENERAL_EXPERIMENTS.md)):

| Tool | Purpose | Effects |
| --- | --- | --- |
| `experiment_inventory` | List public registry names and dataset administrative metadata | Read-only; no market rows |
| `experiment_review` | Find structural omissions and record known integration gaps | Read-only; does not prove semantic correctness |
| `experiment_freeze` | Bind the agreed specification to user-instruction evidence and a digest | Write an immutable revision outside the checkout; no run |
| `experiment_get` | Restore a revision with identity/digest checks | Read-only |

These support the general `research-experiment` Skill. Missing implementations
can remain explicit tasks at freeze; unanswered research semantics cannot.
Code authoring uses the host's own development tools and existing component
contracts, not a generic shell MCP endpoint. See
[ADR-0031](adr/ADR-0031-experiment-authoring-workflow.md).

The following `research_*` workflow tools support the exact frozen Gaps template.
`research_study` also explains the configuration and locates its historical report.

| Tool | Purpose | Effects |
| --- | --- | --- |
| `research_capabilities` | Report supported templates, setup and limits | Read-only |
| `research_datasets` | List admitted datasets and administrative metadata | Read-only; no market rows |
| `research_prepare` | Build and validate a bounded experiment from an existing template | Write a plan in the user workspace; no run registration |
| `research_start` | Start the exact prepared plan | Register and launch an engine run |
| `research_status` | Read recorded state, heartbeat and available progress | Read-only; no reconciliation |
| `research_results` | Return bounded recorded results and an Explorer link | Read-only; no evaluation or index rebuild |

For the first slice, `research_prepare` accepts only the frozen Gaps template;
there are no parameter overrides. It returns the normalized plan,
dataset identity, code/lock identities and declared execution limits. Parsing a
valid JSON configuration alone must not be reported as a full runtime preflight.
`research_start` checks that the prepared inputs still match before launch.

The launch sequence must prevent duplicate tool retries from silently starting
additional attempts. Keep an atomic request-to-run record outside the checkout;
an uncertain launch is reported as uncertain and checked before retrying.
The engine's run directory remains authoritative. One active run is the initial
default; wide sweeps are not exposed by the adapter. New component authoring uses
the general Skill and the coding host, outside this bounded launch interface.

Index refresh and starting the local Explorer are explicit workspace operations,
outside read-only result tools. Return availability honestly if the viewer is
not running. Bind the viewer to loopback and select an available local port.

## Installation and data

The first supported setup is Codex CLI on Linux, Git and uv-managed Python 3.13.
Verify the detached launcher before offering a long-running experiment. Preserve
the engine and Explorer's separate environments and pinned lockfiles. The user's
existing Codex authentication is used by Codex; the MCP adapter does not need to
collect an LLM API key.

Package the plugin manifest, MCP launch configuration, and Skills for setup,
planning a research run, and explaining results. Use local plugin distribution
for the first release. Installation/activation must be tested in a fresh Codex
session; command availability alone does not prove that the plugin loads.

The dataset workflow will expose two choices:

1. **Use my data:** a guided importer checks explicit columns and time semantics,
   creates a new dataset identity and reports problems before admission.
2. **Get a dataset:** a configured catalog offers versioned, approved packages;
   downloads verify declared content digests before admission.

Both enter the same engine data layer. Never guess UTC semantics or silently
change timestamps. Downloads use a configured catalog and approved origins,
rather than letting the model fetch arbitrary URLs. Publish only data whose
distribution rights and intended access role have been checked. Storage hosting
is a separate deployment decision; the first slice does not expose a research
server or distribute internal datasets.

The synthetic example has its own explicit clock domain, deterministic
generator, split policy and manifests. Generation writes a new external artifact
and never modifies existing datasets. Research access remains TRAIN-only for
the first slice. OOS/LOCKBOX unlocks are not exposed as tools. Synthetic runs
still use the engine's current unverified execution/cost semantics, with their
synthetic provenance clearly labelled; no new cost-verification claim is made.

## Delivery order and evidence of completion

| Step | Deliverable | Acceptance check |
| --- | --- | --- |
| 0 | Source mapping and specification | Complete |
| 1 | Existing Gaps results through MCP and Explorer | Implemented; recorded-file checks, unchanged source artifacts and real Codex tool invocation verified |
| 2 | Exact frozen Gaps rerun | Implemented; a canonical rerun completed after MCP disconnect and repeating its plan returned the same run |
| 3 | Codex plugin and Skill | Installed and discovered in a fresh Codex app-server session; local MCP invocation verified without an LLM call |
| 4 | Standalone synthetic engine example | Implemented with admitted synthetic data and checked preparation; separate from the UI preview |
| 5 | Personal import and dataset catalog | Planned; both must create validated, identity-pinned datasets |
| 6 | Public installation polish | Requires a new-user walkthrough with distributable data |


The underlying execution must survive closing the MCP client. Tampered dataset
files and changed prepared inputs must be refused. Protected-data paths remain
blocked. The complete existing engine gate remains required; add focused adapter
and installation checks when their implementation exists.

## Current verification and external references

The initial source mapping is followed by the Gaps implementation in
`integrations/codex/`. Tests cover real stdio discovery/tool calls, protected
and changed-template refusal, file corruption, read-only status/results,
changed plans, duplicate requests, uncertain launches and active-run blocking.
The installed plugin was also loaded through Codex CLI 0.163.0's app-server;
its Skill, seven tools and actual historical-results invocation were verified.
No model-generated interpretation is claimed by that protocol check. A real
canonical TRAIN rerun also completed in a separate workspace through the MCP
start tool after its client disconnected. A repeat start returned the same
receipt. The original run and dataset roots retained their file sizes and
modification times; both original source repositories remained clean.

See [ADR-0030](adr/ADR-0030-codex-gaps-adapter.md) for the approved boundary.
Public installation includes no private market data or run files. The original
research repositories remain separate and unchanged.

Implementation references, checked 2026-10-30:

- [Local MCP configuration](https://learn.chatgpt.com/docs/extend/mcp).
- [Plugin packaging and local distribution](https://developers.openai.com/plugins/build/plugins).
- [Plugin installation](https://learn.chatgpt.com/docs/plugins).
- [Skill packaging and supporting references](https://learn.chatgpt.com/docs/build-skills).

Source anchors:

- [Configuration validation](../src/trading_research/experiments/config.py).
- [Execution, status and detached launch](../src/trading_research/experiments/cli.py).
- [Git and lockfile evidence](../src/trading_research/experiments/orchestration.py).
- [Run identity](../src/trading_research/experiments/run_identity.py).
- [Run scans and reconciliation](../src/trading_research/experiments/runs.py).
- [Index operations](../src/trading_research/ops/cli.py) and [non-mutating index source scan](../src/trading_research/index/build.py).
- [Synthetic pipeline tests](../tests/test_experiments.py) and [data fixtures](../tests/load_fixtures.py).
- [Real Explorer listing](../explorer/src/explorer/index_read.py), [API](../explorer/src/explorer/api.py), and [preview-only demo](../explorer/src/explorer/demo.py).
- [Distribution and code identity](adr/ADR-0029-public-suite-distribution.md).
