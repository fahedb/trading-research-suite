"""Execute the Phase-3 CLI with ``python -m trading_research.experiments``."""

from trading_research.experiments.cli import main as _main

__all__: list[str] = []


if __name__ == "__main__":
    raise SystemExit(_main())
