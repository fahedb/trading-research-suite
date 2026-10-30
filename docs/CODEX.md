# Research with Codex

Describe a new research idea with the **Research Experiment** Skill, clarify its
missing details and save an agreed specification before integrating components.
Read [the experiment workflow](EXPERIMENT_WORKFLOW.md) for what happens next.

The plugin also connects to the existing **Gap Sensitive Levels H1** study:
read its saved results or request a rerun of its frozen template. The Explorer
gives you the visual view of those results.

Try: **“Show me the Gaps research and explain what its results actually tell us.”**

This release is for Linux and Codex CLI. It includes a local MCP server,
a plugin and two workflow Skills. It needs no separate LLM API key. Your historical
run files and datasets stay outside the repository; the tools send requested
summaries to your Codex conversation, so your Codex account's data settings apply.

## Connect an existing workspace

Install [uv](https://docs.astral.sh/uv/) and Codex CLI, then clone the suite.
Use an absolute path for each root. `--workspace` must be a **new, separate
location**, outside the checkout, original run store and dataset roots.

```bash
bash scripts/install-codex.sh \
  --source-runs-root /absolute/path/to/existing-runs \
  --data-root /absolute/path/to/data \
  --data-root /absolute/path/to/views \
  --workspace /absolute/path/to/codex-workspace \
  --explorer-url http://127.0.0.1:8788
```

Use your actual Explorer port; omit `--explorer-url` if it is not running.
The installer saves paths to `~/.config/trading-research-suite/codex.json`, creates
a launcher in `~/.local/bin`, and installs the plugin from the local checkout.
Keep `~/.local/bin` on PATH and start a fresh Codex session. Existing different
configuration or launchers are refused rather than overwritten.

The public repository contains the study code and report, **not its market
dataset or private results**. Without them, Codex can explain the template but
cannot display the historical run or execute it. Importing arbitrary CSV files
and downloading an approved dataset catalog are still planned.

## What the tools do

| Tool | What happens |
| --- | --- |
| `experiment_inventory` | Lists registered component names and dataset metadata without reading market rows |
| `experiment_review` | Finds missing fields and integration tasks in a new experiment specification |
| `experiment_freeze` | Saves the agreed specification as an immutable revision; does not launch anything |
| `experiment_get` | Restores and checks a frozen specification before resuming work |
| `research_capabilities` | Shows what is installed and whether the historical run is present |
| `research_study` | Explains the frozen template and its assumptions |
| `research_datasets` | Checks the exact required TRAIN identity using manifest metadata |
| `research_status` | Reads recorded run state without changing it |
| `research_results` | Checks recorded JSON hashes, reads results and checks the Explorer link |
| `research_prepare` | Saves a rerun plan after configuration, metadata and clean-checkout checks |
| `research_start` | Launches that exact plan through the engine's detached CLI |

A request to explain results does not start a new run. For execution, ask
**“Rerun the frozen Gaps TRAIN template.”** Preparation does not read market rows;
the engine verifies full dataset integrity and access when it executes.
`systemd-run --user` or `tmux` must work for detached execution.

Repeated starts of one plan return the same launch receipt. An uncertain launch
blocks further execution until an operator inspects the receipt and engine run
store. Do not delete receipts or silently create another plan to retry. The
receipt is in `<workspace>/launches/`; authoritative runs are in
`<workspace>/engine/runs/`. Only one adapter-launched run can be active.

## Open a new run in the Explorer

An existing Explorer continues to show its configured historical run store.
To view new adapter runs, start another local instance pointing at the new store:

```bash
uv sync --locked --project explorer
TRE_RUNS_ROOT=/absolute/path/to/codex-workspace/engine \
TRE_DATA_ROOT=/absolute/path/to/data \
TRE_VIEWS_ROOT=/absolute/path/to/views \
uv run --frozen --project explorer python -m uvicorn explorer.api:app \
  --host 127.0.0.1 --port 8789
```

Set `explorer_url` in your local `codex.json` to that port when switching stores.
Direct run links work without an index; the Explorer's experiment listing needs
the explicit [index rebuild](GETTING_STARTED.md). Results tools never rebuild
indexes or start servers. On a remote machine, forward the local port before
opening the link in your browser.

## Interpretation and current limits

The original study produced four trades, all closed at fold boundaries. This
demonstrates the workflow; it does not establish an edge. Cost assumptions and
execution prices remain unverified. The tools retain these limitations and
report drawdown by individual fold. Market keys remain native wall-clock.

General experiment planning/freezing is supported. Automatic MCP execution
supports only the exact existing TRAIN template. General component integration
uses Codex's coding tools and the suite's authoring contracts; CSV admission and
catalog downloads remain unimplemented. No sweeps or protected-data unlocks are
exposed. New runs record the public
checkout's commit, so their identities differ from the original research run.
Keep this checkout clean; data and generated files belong in the workspace.

For implementation and future work, see [the integration plan](CODEX_MVP.md)
and [ADR-0030](adr/ADR-0030-codex-gaps-adapter.md). Packaging follows the
[official plugin format](https://developers.openai.com/plugins/build/plugins)
and [local MCP support](https://learn.chatgpt.com/docs/extend/mcp).
