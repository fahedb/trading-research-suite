# Development

Use a separate virtual environment for each component:

```bash
uv sync --locked
./scripts/gate.sh
cd explorer
uv sync --locked --python 3.13
uv run --frozen pytest -q
uv run --frozen ruff check .
```

The engine gate runs its tests, Ruff, and mypy. Performance qualification tests
need separately configured datasets and are excluded from the default gate.
Explorer tests against real run artifacts skip when those artifacts are absent;
the synthetic demo and component tests run without them. The original
market-derived fixture is not distributed, so its optional checks also skip.

Changes to either component share one Git history. Commit your changes before
starting canonical research runs. Keep data, run artifacts, secrets, and local
configuration outside version control.

## Package layout

An optional [GitHub Actions template](ci/README.md) can run these checks and a
secret scan on every push and pull request. It is included as a template until
workflow-editing permission is available.

| Path | Purpose |
| --- | --- |
| `src/trading_research/` | Engine, contracts, data layer, models, simulation, operations |
| `tests/` | Engine tests and architecture checks |
| `config/` | Example research configurations and split policies |
| `scripts/` | Quality gate and research utilities |
| `explorer/src/explorer/` | Viewer API and isolated synthetic demo |
| `explorer/prototype/` | Browser interface and vendored chart library |
| `explorer/tests/` | Viewer and demo checks |
| `docs/` | Guides, architecture decisions, and research methodology |

The chart library retains its own license and attribution. The project has not
yet selected a separate open-source license; publication alone is not a new
license grant. See [third-party notices](THIRD_PARTY.md).

## Codex adapter

The adapter has its own locked environment in `integrations/codex/`:

```bash
uv sync --locked --project integrations/codex
uv run --frozen --project integrations/codex pytest integrations/codex/tests
uv run --frozen --project integrations/codex ruff check integrations/codex
```

Run these in addition to the engine gate and Explorer checks. Adapter fixtures
are generated metadata; they contain no private market data. A live workspace
check must use explicit configured roots and an allowed TRAIN study.
