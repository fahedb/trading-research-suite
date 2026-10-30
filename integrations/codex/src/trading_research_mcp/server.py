"""Stdio MCP entry point and explicit local workspace setup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from trading_research_mcp.service import (
    HISTORICAL_RUN,
    ResearchService,
    Settings,
    atomic_json,
)
from trading_research_mcp.specification import (
    Approval,
    ExperimentSpec,
    SpecificationStore,
    inventory,
    review_spec,
)

DEFAULT_CONFIG = Path.home() / ".config/trading-research-suite/codex.json"
READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
START = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def create_server(config_path: Path) -> FastMCP:
    server = FastMCP(
        "Trading Research",
        instructions="Use experiment_* tools to review and freeze general research specifications. "
        "A frozen specification is not a runnable engine config. "
        "research_study/datasets/prepare/start/status/results support only the frozen Gaps study. "
        "Reading results or freezing a specification does not start a run. "
        "Use research_start only when the user requested execution. "
        "Preserve cost limitations and native wall-clock semantics.",
    )

    def service() -> ResearchService:
        if not config_path.is_file():
            raise ValueError("Workspace not configured. Follow docs/CODEX.md in the suite.")
        return ResearchService(Settings.load(config_path))

    @server.tool(annotations=READ)
    def research_capabilities() -> dict[str, Any]:
        """Describe installed capabilities, study availability and remaining limitations."""
        return service().capabilities()

    @server.tool(annotations=READ)
    def research_study() -> dict[str, Any]:
        """Explain the frozen Gaps H1 template and identify its historical report and run."""
        return service().study()

    @server.tool(annotations=READ)
    def research_datasets() -> dict[str, Any]:
        """Check the exact required TRAIN dataset using manifest metadata, without market rows."""
        return service().datasets()

    @server.tool(annotations=READ)
    def research_status(run_id: str = HISTORICAL_RUN) -> dict[str, Any]:
        """Read a Gaps run's recorded state without stale-run reconciliation or writes."""
        return service().status(run_id)

    @server.tool(annotations=READ)
    def research_results(run_id: str = HISTORICAL_RUN) -> dict[str, Any]:
        """Read hash-checked Gaps results and a matching local Explorer link; no new run."""
        return service().results(run_id)

    @server.tool(annotations=WRITE)
    def research_prepare() -> dict[str, Any]:
        """Write a plan for one exact frozen TRAIN rerun; metadata checks only, no execution."""
        return service().prepare()

    @server.tool(annotations=START)
    def research_start(plan_id: str) -> dict[str, Any]:
        """Launch a prepared rerun when requested. Repeated plan IDs never launch twice."""
        return service().start(plan_id)

    @server.tool(annotations=READ)
    def experiment_inventory() -> dict[str, Any]:
        """List available component names and dataset metadata; never read market rows."""
        return inventory(service().settings)

    @server.tool(annotations=READ)
    def experiment_review(spec: ExperimentSpec) -> dict[str, Any]:
        """Find missing specification fields and integration gaps. No writes or execution."""
        return review_spec(spec)

    @server.tool(annotations=START)
    def experiment_freeze(spec: ExperimentSpec, approval: Approval) -> dict[str, Any]:
        """Save an immutable experiment revision with digest-bound user instruction evidence.

        This records assistant-reported authorization, not authentication. It never
        launches a run, downloads data or establishes semantic/runtime correctness.
        """
        return SpecificationStore(service().settings).freeze(spec, approval)

    @server.tool(annotations=READ)
    def experiment_get(experiment_id: str, revision: int = 1) -> dict[str, Any]:
        """Read and check an immutable experiment specification before resuming integration."""
        return SpecificationStore(service().settings).get(experiment_id, revision)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command")
    setup = subparsers.add_parser("configure", help="Save local paths outside the public checkout")
    setup.add_argument("--suite-root", type=Path, required=True)
    setup.add_argument("--source-runs-root", type=Path, required=True)
    setup.add_argument("--data-root", type=Path, action="append", default=[])
    setup.add_argument("--workspace", type=Path, required=True)
    setup.add_argument("--explorer-url")
    args = parser.parse_args()
    if args.command == "configure":
        settings = Settings(
            args.suite_root.resolve(),
            args.source_runs_root.resolve(),
            tuple(p.resolve() for p in args.data_root),
            args.workspace.resolve(),
            args.explorer_url,
        )
        settings.validate()
        document = {
            "suite_root": str(settings.suite_root),
            "source_runs_root": str(settings.source_runs_root),
            "data_roots": [str(p) for p in settings.data_roots],
            "workspace": str(settings.workspace),
            "explorer_url": settings.explorer_url,
        }
        destination = args.config.expanduser().resolve()
        if destination.is_relative_to(settings.suite_root) or any(
            destination.is_relative_to(p) for p in (settings.source_runs_root, *settings.data_roots)
        ):
            parser.error("Keep machine configuration outside code, research runs and datasets")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            if json.loads(destination.read_text()) != document:
                parser.error("Configuration already exists; edit different settings explicitly")
        else:
            atomic_json(destination, document)
        print(f"Workspace configured: {destination}")
        return
    create_server(args.config.expanduser().resolve()).run(transport="stdio")


if __name__ == "__main__":
    main()
