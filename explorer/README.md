# Trade Explorer

The visual side of [Trading Research Suite](../README.md). Browse experiment
families, compare runs, and inspect individual trades on an interactive chart.

## Try it

From this directory:

```bash
uv sync --locked --python 3.13
uv run --frozen python -m uvicorn explorer.demo:app --host 127.0.0.1 --port 8787
```

Open [localhost:8787](http://localhost:8787). This demo uses synthetic data.

For recorded research, configure your own data and run directories and launch
`explorer.api:app` instead. See [Getting started](../docs/GETTING_STARTED.md).

## Inside the viewer

- `src/explorer/`: read-only API, result readers, and the separate demo app.
- `prototype/`: plain HTML, CSS, and JavaScript; no frontend build step.
- `tests/`: API, bracket, time-semantics, and demo checks.
- `tools/make_fixture.py`: optional local fixture generator for your own data.

The viewer presents the engine's recorded decisions and outcomes. It does not
run experiments or modify research artifacts. Its dependency on the engine is
an editable path to the repository root, with its own environment and lockfile.

[Architecture](docs/ARCHITECTURE.md) · [Phone checks](docs/PHONE_TESTING.md) ·
[Development](../docs/DEVELOPMENT.md)
