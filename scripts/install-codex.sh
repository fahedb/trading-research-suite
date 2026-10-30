#!/usr/bin/env bash
# Install a local Codex plugin against this checkout and explicit private paths.
set -euo pipefail
suite_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uv sync --locked --project "$suite_root"
uv sync --locked --project "$suite_root/integrations/codex"
executable="$suite_root/integrations/codex/.venv/bin/trading-research-mcp"
"$executable" configure --suite-root "$suite_root" "$@"
mkdir -p "$HOME/.local/bin"
launcher="$HOME/.local/bin/trading-research-mcp"
if [[ -e "$launcher" || -L "$launcher" ]]; then
  if [[ "$(readlink -f "$launcher")" != "$(readlink -f "$executable")" ]]; then
    echo "An existing trading-research-mcp launcher points elsewhere; inspect it first." >&2
    exit 1
  fi
else
  ln -s "$executable" "$launcher"
fi
export PATH="$HOME/.local/bin:$PATH"
codex plugin marketplace add "$suite_root"
codex plugin add trading-research@trading-research-suite
echo 'Installed. Start a new Codex session with ~/.local/bin on PATH.'
