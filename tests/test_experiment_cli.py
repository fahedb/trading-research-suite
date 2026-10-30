"""The closed four-verb CLI and approved detached launchers."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

import trading_research.experiments.cli as cli
from tests.load_fixtures import PartitionSpec, make_load_case
from tests.test_experiments import _config_document

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest


def _command_names() -> set[str]:
    action = next(
        action
        for action in cli._parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(action.choices)


def test_cli_has_exactly_five_verbs_and_no_comparison_override() -> None:
    assert _command_names() == {"run", "status", "list", "verify", "preflight"}
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "tolerance" not in source.lower()


def test_detached_launch_prefers_systemd_user_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def succeed(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", succeed)

    launcher = cli._launch_detached(("python", "-m", "worker"), run_id="01M00218B441CG7MH23WHT5XTS")

    assert launcher == "systemd-run"
    assert calls[0][:6] == (
        "systemd-run",
        "--user",
        "--collect",
        "--quiet",
        "--unit",
        "trading-research-01m00218b441cg7mh23wht5xts",
    )
    assert set(calls[0][6:11]) == {
        "--setenv=MKL_NUM_THREADS=1",
        "--setenv=NUMEXPR_NUM_THREADS=1",
        "--setenv=OMP_NUM_THREADS=1",
        "--setenv=OPENBLAS_NUM_THREADS=1",
        "--setenv=VECLIB_MAXIMUM_THREADS=1",
    }
    assert calls[0][11:] == ("--", "python", "-m", "worker")


def test_detached_launch_falls_back_to_tmux_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def launch(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        normalized = tuple(command)
        calls.append((normalized, kwargs))
        return subprocess.CompletedProcess(
            command,
            1 if normalized[0] == "systemd-run" else 0,
            "",
            "no user manager" if normalized[0] == "systemd-run" else "",
        )

    monkeypatch.setattr(subprocess, "run", launch)

    launcher = cli._launch_detached(("python", "worker.py"), run_id="01M00218B441CG7MH23WHT5XTS")

    assert launcher == "tmux"
    assert calls[1][0][:6] == (
        "tmux",
        "new-session",
        "-d",
        "-s",
        "trading-research-01m00218b441cg7mh23wht5xts",
        "--",
    )
    assert calls[1][0][6] == "env"
    assert set(calls[1][0][7:12]) == {
        "MKL_NUM_THREADS=1",
        "NUMEXPR_NUM_THREADS=1",
        "OMP_NUM_THREADS=1",
        "OPENBLAS_NUM_THREADS=1",
        "VECLIB_MAXIMUM_THREADS=1",
    }
    for _command, kwargs in calls:
        environment = cast("dict[str, str]", kwargs["env"])
        assert all(
            environment[name] == "1"
            for name in (
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        )
    assert all("shell" not in kwargs for _command, kwargs in calls)


def test_detached_sweep_memory_limit_is_enforced_or_labelled_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def launch(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        normalized = tuple(command)
        calls.append(normalized)
        return subprocess.CompletedProcess(
            command,
            1 if normalized[0] == "systemd-run" else 0,
            "",
            "no delegation" if normalized[0] == "systemd-run" else "",
        )

    monkeypatch.setattr(subprocess, "run", launch)
    launcher = cli._launch_detached(
        ("python", "dispatcher.py"),
        run_id="sweep-planned",
        memory_max_bytes=17_179_869_184,
    )
    assert "--property=MemoryMax=17179869184" in calls[0]
    assert launcher == "tmux-no-memorymax"


def test_foreground_cli_runs_from_resolved_config_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = tuple(range(100, 146))
    case = make_load_case(
        tmp_path,
        specs=(PartitionSpec("p0", "bars.parquet", keys, 100, 145, len(keys)),),
    )
    config_path = tmp_path / "cli-config.json"
    config_path.write_text(json.dumps(_config_document(case)), encoding="utf-8")
    runs_root = (tmp_path / "cli-runs").resolve()
    audit_path = (tmp_path / "audit.jsonl").resolve()
    repository = Path.cwd().resolve()

    result = cli.main(
        (
            "run",
            str(config_path),
            "--data-root",
            str(case.source_directory.parent),
            "--runs-root",
            str(runs_root),
            "--split-policy",
            str(case.source_directory.parent / "policy.json"),
            "--audit",
            str(audit_path),
            "--repository-root",
            str(repository),
            "--dependency-lock",
            str(repository / "uv.lock"),
            "--heartbeat-interval-seconds",
            "0.01",
            "--foreground",
            "--json",
        )
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["state"] == "COMPLETE"
    assert output["evaluation_count"] == 3
    assert not audit_path.exists()
