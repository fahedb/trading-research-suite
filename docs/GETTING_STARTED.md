# Getting started

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.
uv installs the required Python version when needed. The engine targets
Python 3.13.

```bash
git clone https://github.com/fahedb/trading-research-suite.git
cd trading-research-suite
```

## Try the Explorer

```bash
cd explorer
uv sync --locked --python 3.13
uv run --frozen python -m uvicorn explorer.demo:app --host 127.0.0.1 --port 8787
```

Open [localhost:8787](http://localhost:8787). Choose the demo family, open a run,
then select **Explore trades**. You can move between trades and switch chart
timeframes.

Every demo screen is labelled **Synthetic demo**. Its prices and trades are
generated examples, not recorded research or performance evidence. No market
data or credentials are needed.

## Use your own data

From the repository root, install the engine and see the available commands:

```bash
uv sync --locked
uv run --frozen trading-research --help
```

The engine consumes versioned, hash-manifested datasets. Data acquisition is
outside this project. Start with the [data model](architecture/V1.md#4-data-and-view-model)
and [research policy](research/RESEARCH_POLICY.md) before preparing your data.
The configurations in `config/` are research examples: they reference datasets
that are not bundled here and are not ready-to-run examples on a fresh checkout.

Keep datasets, generated views, and run artifacts outside this checkout. For the
Explorer, set the following to your own locations:

```bash
export TRE_DATA_ROOT=/path/to/research-data
export TRE_VIEWS_ROOT=/path/to/research-views
export TRE_RUNS_ROOT=/path/to/research-runs
```

`TRE_RESEARCH_REPO` defaults to this repository's root. If using custom split
policies, set `TRE_SPLIT_POLICIES` to their paths separated by your operating
system's path separator (`:` on Linux/macOS). Without overrides, the two policies
in `config/split-policies/` are used.

Build or refresh the run index with the engine's `index` commands; see
`uv run --frozen trading-research index --help`. Then start the real viewer:

```bash
cd explorer
uv run --frozen python -m uvicorn explorer.api:app --host 127.0.0.1 --port 8787
```

The viewer expects completed, admitted research runs and their data. Without an
index it displays an availability error. The API reference is at `/api/docs`.
Its default data directories live under `~/.local/share/trading-research-suite/`
and can all be overridden as above. The viewer has no authentication layer;
the commands above bind it to your own computer.
