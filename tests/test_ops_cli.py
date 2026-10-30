from __future__ import annotations

import json
from typing import TYPE_CHECKING

import trading_research.ops.cli as ops_cli
from tests.test_resume import _planned_sweep
from tests.test_run_index_query import _complete
from trading_research.index.build import _rebuild_index

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_top_level_cli_delegates_the_four_existing_experiment_verbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, ...]] = []

    def delegated(arguments: tuple[str, ...]) -> int:
        observed.append(arguments)
        return 7

    monkeypatch.setattr(ops_cli, "_experiments_main", delegated)
    for verb in ("run", "status", "list", "verify"):
        assert ops_cli.main((verb, "synthetic")) == 7
    assert observed == [
        ("run", "synthetic"),
        ("status", "synthetic"),
        ("list", "synthetic"),
        ("verify", "synthetic"),
    ]


def test_index_and_query_cli_emit_stable_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, fingerprint = _complete(
        tmp_path,
        run_index=1,
        parameter=1,
        value=0.5,
    )
    monkeypatch.setattr(ops_cli, "_git_commit", lambda _root: "a" * 40)
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    assert (
        ops_cli.main(
            (
                "index",
                "rebuild",
                "--runs-root",
                str(tmp_path),
                "--repository-root",
                str(repository_root),
                "--json",
            )
        )
        == 0
    )
    built = json.loads(capsys.readouterr().out)
    assert built["run_count"] == 1
    assert built["scan_error_count"] == 0

    assert (
        ops_cli.main(
            (
                "runs",
                "query",
                "--runs-root",
                str(tmp_path),
                "--metric",
                "rank_ic",
                "--plane",
                "A",
                "--json",
            )
        )
        == 0
    )
    queried = json.loads(capsys.readouterr().out)
    assert queried["rows"][0]["run_id"] == run_id
    assert queried["rows"][0]["effective_samples"] == 4

    assert (
        ops_cli.main(
            (
                "runs",
                "compare",
                "--runs-root",
                str(tmp_path),
                "--metric",
                "rank_ic",
                "--fingerprint",
                fingerprint,
                "--json",
            )
        )
        == 0
    )
    compared = json.loads(capsys.readouterr().out)
    assert compared["groups"][0]["descriptive_cross_fold_mean"] == 0.5
    assert compared["trial_context"]["plane_a_trials"] == 1
    assert "NOT statistically independent" in compared["trial_context"]["caveat"]


def test_index_status_returns_nonzero_for_missing_or_stale_index(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        ops_cli.main(
            (
                "index",
                "status",
                "--runs-root",
                str(tmp_path),
                "--json",
            )
        )
        == 1
    )
    status = json.loads(capsys.readouterr().out)
    assert status["available"] is False
    assert status["fresh"] is False


def test_query_from_file_is_a_closed_fingerprint_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_id, fingerprint = _complete(
        tmp_path,
        run_index=1,
        parameter=1,
        value=0.5,
    )
    monkeypatch.setattr(ops_cli, "_git_commit", lambda _root: "a" * 40)
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    assert (
        ops_cli.main(
            (
                "index",
                "rebuild",
                "--runs-root",
                str(tmp_path),
                "--repository-root",
                str(repository_root),
                "--json",
            )
        )
        == 0
    )
    capsys.readouterr()
    query = tmp_path / "query.json"
    query.write_text(json.dumps({"fingerprints": [fingerprint]}))
    assert (
        ops_cli.main(
            (
                "runs",
                "compare",
                "--runs-root",
                str(tmp_path),
                "--metric",
                "rank_ic",
                "--query-from-file",
                str(query),
                "--json",
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["groups"]


def test_sweep_run_and_trial_report_cli_are_composed_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, runtime = _planned_sweep(tmp_path, seeds=(71, 72))
    assert (
        ops_cli.main(
            (
                "sweep",
                "run",
                "--runs-root",
                str(runtime._runs_root),
                "--sweep",
                str(plan._document["sweep_id"]),
                "--max-workers",
                "2",
                "--memory-budget-bytes",
                "536870912",
                "--assumed-peak-rss-bytes",
                "134217728",
                "--allow-noncanonical",
                "--stale-after-ns",
                "1000000000",
                "--poll-interval-seconds",
                "0.01",
                "--json",
            )
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] == 2
    assert summary["successful"] is True

    _rebuild_index(
        runtime._runs_root,
        builder_git_commit="a" * 40,
        incremental=False,
    )
    assert (
        ops_cli.main(
            (
                "trials",
                "report",
                "--runs-root",
                str(runtime._runs_root),
                "--sweep",
                str(plan._document["sweep_id"]),
                "--json",
            )
        )
        == 0
    )
    ledger = json.loads(capsys.readouterr().out)
    assert ledger["plane_a_trials"] == 2
    assert ledger["scope"]["kind"] == "sweep"


def test_detached_sweep_records_actual_memorymax_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(ops_cli, "_read_sweep_plan", lambda *_args: object())

    def launch(command: tuple[str, ...], **kwargs: object) -> str:
        observed["command"] = command
        observed.update(kwargs)
        return "systemd-run-memorymax"

    def record(*_args: object, **kwargs: object) -> None:
        observed["receipt"] = kwargs

    monkeypatch.setattr(ops_cli, "_launch_detached", launch)
    monkeypatch.setattr(ops_cli, "_record_detached_launch", record)
    runs_root = tmp_path.resolve()
    assert (
        ops_cli.main(
            (
                "sweep",
                "run",
                "--runs-root",
                str(runs_root),
                "--sweep",
                "planned",
                "--detach",
                "--json",
            )
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["memory_max_enforced"] is True
    assert observed["memory_max_bytes"] == 17_179_869_184
    command = observed["command"]
    assert type(command) is tuple
    assert "--launch-mode" in command
    assert observed["receipt"]
