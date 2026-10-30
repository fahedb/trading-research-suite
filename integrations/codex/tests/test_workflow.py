"""Protocol, integrity and retry checks using generated metadata, never private data."""

import asyncio
import json
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from trading_research.experiments.config import load_resolved_config

from trading_research_mcp.service import (
    CONFIG_DIGEST,
    HISTORICAL_RUN,
    TEMPLATE,
    Refused,
    ResearchService,
    Settings,
    atomic_json,
    digest,
    encoded,
)

SUITE = Path(__file__).resolve().parents[3]


@pytest.fixture
def study(tmp_path):
    settings = Settings(SUITE, tmp_path / "source", (), tmp_path / "workspace")
    service = ResearchService(settings)
    directory = settings.source_runs_root / "runs" / HISTORICAL_RUN
    directory.mkdir(parents=True)
    (directory / "metrics").mkdir()
    config = json.loads(load_resolved_config(SUITE / TEMPLATE).canonical_json())
    platform = {"git_commit": "a" * 40, "git_dirty": False, "diff_sha256": None}
    inputs = {"resolved_config": config, "platform": platform, "declared_lockbox_evaluation": False}
    identity = {
        "schema": "research-experiment-identity-v1",
        "fingerprint_inputs": inputs,
        "experiment_fingerprint": digest(encoded(inputs)),
    }
    state = {
        "schema": "research-run-state-v2",
        "run_id": HISTORICAL_RUN,
        "state": "COMPLETE",
        "oos_touched": False,
        "lockbox_touched": False,
        "updated_unix_ns": 1,
    }
    simulation = {"schema": "research-simulation-summary-v1", "results": []}
    evaluation = {"schema": "research-evaluation-inventory-v2", "entries": []}
    for fold, pnl, dd in [(0, 10.0, 7.0), (1, -3.0, 4.0)]:
        trade_id = str(fold) * 64
        simulation["results"].append(
            {
                "fold_index": fold,
                "num_trades": 1,
                "boundary_forced_close_count": 1,
                "counters": {},
                "trade_log_artifact_identity": trade_id,
            }
        )
        evaluation["entries"].insert(
            0,
            {
                "plane": "B",
                "trade_log_artifact_identity": trade_id,
                "metrics": [
                    {"name": k, "value_float64_bits": struct.pack(">d", v).hex()}
                    for k, v in [("net_pnl", pnl), ("max_drawdown", dd)]
                ],
            },
        )
    files = {
        "identity.json": identity,
        "config.resolved.json": config,
        "metrics/simulation-summary.json": simulation,
        "metrics/evaluation-summary.json": evaluation,
        "state.json": state,
    }
    hashes = {"root": {}, "metrics": {}}
    for name, value in files.items():
        payload = encoded(value)
        (directory / name).write_bytes(payload)
        section = "metrics" if name.startswith("metrics/") else "root"
        hashes[section][Path(name).name] = digest(payload)
    manifest = {
        "schema": "research-run-manifest-v3",
        "run_id": HISTORICAL_RUN,
        "state": "COMPLETE",
        "canonical": True,
        "platform": platform,
        "experiment_fingerprint": identity["experiment_fingerprint"],
        "access": {
            "oos_touched": False,
            "lockbox_touched": False,
            "declared_lockbox_evaluation": False,
        },
        "files": hashes,
        "simulation": {"cost_semantics_unverified": True},
    }
    (directory / "manifest.json").write_bytes(encoded(manifest))
    return service, directory


def snapshot(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_results_join_by_artifact_preserve_folds_and_never_write(study):
    service, directory = study
    before = snapshot(directory)
    result = service.results()
    assert result["summary"]["conditional_net_pnl_sum"] == 7
    assert result["summary"]["worst_fold_drawdown"] == 7
    assert result["folds"][0]["metrics"]["net_pnl"] == 10
    assert result["summary"]["boundary_forced_close_count"] == 2
    assert result["explorer"]["available"] is False
    service.status()
    assert snapshot(directory) == before
    assert not service.settings.workspace.exists()


def test_tampered_result_is_a_hard_error(study):
    service, directory = study
    (directory / "metrics/evaluation-summary.json").write_text("{}")
    with pytest.raises(Refused, match="hash mismatch"):
        service.results()


def test_protected_or_changed_config_refused_before_metrics(study):
    service, directory = study
    path = directory / "identity.json"
    identity = json.loads(path.read_text())
    identity["fingerprint_inputs"]["resolved_config"]["dataset"]["role"] = "lockbox"
    path.write_bytes(encoded(identity))
    (directory / "metrics/evaluation-summary.json").unlink()
    with pytest.raises(Refused, match="exact frozen TRAIN"):
        service.results()


@pytest.mark.parametrize("name", ["../other", "01M4110M4MMQ9E73DV2ZJMAS92\n"])
def test_run_path_traversal_refused(study, name):
    with pytest.raises(Refused, match="bare run ID"):
        study[0].results(name)


def test_artifact_symlink_refused(study, tmp_path):
    service, directory = study
    path = directory / "identity.json"
    outside = tmp_path / "identity.json"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(Refused, match="symlink"):
        service.results()


def test_stale_running_observation_does_not_terminalize(study):
    service, directory = study
    path = directory / "state.json"
    state = json.loads(path.read_text())
    state["state"] = "RUNNING"
    path.write_bytes(encoded(state))
    before = snapshot(directory)
    assert service.status()["state"] == "RUNNING"
    with pytest.raises(Refused, match="COMPLETE"):
        service.results()
    assert snapshot(directory) == before


def test_empty_metrics_remain_unknown(study):
    service, directory = study
    path = directory / "metrics/evaluation-summary.json"
    document = json.loads(path.read_text())
    for entry in document["entries"]:
        for metric in entry["metrics"]:
            metric["value_float64_bits"] = None
    path.write_bytes(encoded(document))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["metrics"][path.name] = digest(path.read_bytes())
    manifest_path.write_bytes(encoded(manifest))
    summary = service.results()["summary"]
    assert summary["conditional_net_pnl_sum"] is None
    assert summary["worst_fold_drawdown"] is None


def test_workspace_cannot_overlap_code_data_or_source_runs(tmp_path):
    for root in [SUITE, tmp_path / "source", tmp_path / "data"]:
        with pytest.raises(Refused, match="separate"):
            Settings(SUITE, tmp_path / "source", (tmp_path / "data",), root / "new").validate()


def test_external_explorer_refused(tmp_path):
    with pytest.raises(Refused, match=r"127\.0\.0\.1"):
        Settings(
            SUITE, tmp_path / "source", (), tmp_path / "work", "https://example.com"
        ).validate()


@pytest.fixture
def launchable(study, monkeypatch, tmp_path):
    service, _ = study
    # Only the launch boundary is faked; persistence, locks and retry policy are real.
    fake_suite = tmp_path / "suite"
    (fake_suite / ".venv/bin").mkdir(parents=True)
    (fake_suite / ".venv/bin/trading-research").write_text("test executable")
    service = ResearchService(
        Settings(fake_suite, service.settings.source_runs_root, (), service.settings.workspace)
    )
    evidence = {"config_sha256": CONFIG_DIGEST, "git_commit": "a" * 40}
    monkeypatch.setattr(service, "_evidence", lambda: evidence.copy())
    monkeypatch.setattr("trading_research_mcp.service.shutil.which", lambda _: "/bin/launcher")
    return service, evidence


def test_start_retry_launches_once(launchable, monkeypatch):
    service, _ = launchable
    plan = service.prepare()
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps({"run_id": HISTORICAL_RUN, "state": "PENDING", "launcher": "tmux"}),
            "",
        )
    )
    monkeypatch.setattr("trading_research_mcp.service.subprocess.run", run)
    first = service.start(plan["plan_id"])
    assert first["launch_state"] == "launched"
    assert service.start(plan["plan_id"]) == first
    assert run.call_count == 1
    assert "--unlock-gated" not in run.call_args.args[0]
    assert "--foreground" not in run.call_args.args[0]


def test_changed_prepared_inputs_block_launch(launchable, monkeypatch):
    service, evidence = launchable
    plan = service.prepare()
    evidence["git_commit"] = "b" * 40
    run = Mock()
    monkeypatch.setattr("trading_research_mcp.service.subprocess.run", run)
    with pytest.raises(Refused, match="inputs changed"):
        service.start(plan["plan_id"])
    run.assert_not_called()


def test_uncertain_launch_blocks_retry_and_new_plan(launchable, monkeypatch):
    service, _ = launchable
    plan = service.prepare()
    run = Mock(side_effect=subprocess.TimeoutExpired("engine", 60))
    monkeypatch.setattr("trading_research_mcp.service.subprocess.run", run)
    receipt = service.start(plan["plan_id"])
    assert receipt["launch_state"] == "uncertain"
    assert service.start(plan["plan_id"]) == receipt
    with pytest.raises(Refused, match="operator inspection"):
        service.start(service.prepare()["plan_id"])
    assert run.call_count == 1


def test_active_run_blocks_new_plan(launchable, monkeypatch):
    service, _ = launchable
    plan = service.prepare()
    launches = service.settings.workspace / "launches"
    launches.mkdir()
    atomic_json(launches / "previous.json", {"launch_state": "launched", "run_id": HISTORICAL_RUN})
    monkeypatch.setattr(service, "status", lambda _: {"state": "RUNNING"})
    with pytest.raises(Refused, match="still active"):
        service.start(plan["plan_id"])


def test_real_stdio_handshake_results_and_invalid_id(study, tmp_path):
    service, directory = study
    before = snapshot(directory)
    config = tmp_path / "codex.json"
    atomic_json(
        config,
        {
            "suite_root": str(SUITE),
            "source_runs_root": str(service.settings.source_runs_root),
            "workspace": str(service.settings.workspace),
            "data_roots": [],
            "explorer_url": None,
        },
    )

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "trading_research_mcp.server", "--config", str(config)],
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {
                "research_capabilities",
                "research_study",
                "research_datasets",
                "research_status",
                "research_results",
                "research_prepare",
                "research_start",
                "experiment_inventory",
                "experiment_review",
                "experiment_freeze",
                "experiment_get",
            }
            assert tools["research_results"].annotations.readOnlyHint
            assert not tools["research_start"].annotations.readOnlyHint
            result = await session.call_tool("research_results", {})
            assert not result.isError
            assert result.structuredContent["summary"]["trade_count"] == 2
            refused = await session.call_tool("research_results", {"run_id": "../escape"})
            assert refused.isError

    asyncio.run(exercise())
    assert snapshot(directory) == before
