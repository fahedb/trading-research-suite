#!/usr/bin/env bash
# Local quality gate — run before claiming any change works.
# Usage: ./scripts/gate.sh   (from anywhere; resolves the repo root itself)
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== gate: pytest =="
uv run pytest -q

echo "== gate: ruff =="
uv run ruff check .

echo "== gate: mypy =="
uv run mypy src tests scripts

echo "== gate: ALL GREEN =="
