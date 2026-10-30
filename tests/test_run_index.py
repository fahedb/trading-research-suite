from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import TYPE_CHECKING

import pytest

import trading_research.index.build as build_module
from tests.test_runs import BASE_NS, STALE_NS, _evaluated_inventory, _register, _start
from trading_research.experiments.runs import (
    RunState,
    _finish_run,
    _record_run_access,
    _record_run_access_attempt,
)
from trading_research.index.build import (
    _canonical_table_dump,
    _index_status,
    _IndexError,
    _IndexRebuildRequiredError,
    _open_fresh_index,
    _rebuild_index,
    _source_token,
)

if TYPE_CHECKING:
    from pathlib import Path


def _truth_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if "index" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        digest.update(relative.encode())
        digest.update(str(metadata.st_mode).encode())
        digest.update(str(metadata.st_mtime_ns).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _terminal_run(root: Path, *, run_index: int = 1) -> str:
    run_id = _register(root, run_index=run_index)
    _start(root, run_id)
    _finish_run(
        runs_root=root,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=0.25),
        failure_reason=None,
        now_unix_ns=BASE_NS + 200 + run_index,
    )
    return run_id


def test_full_index_is_derived_queryable_and_read_only(tmp_path: Path) -> None:
    run_id = _terminal_run(tmp_path)
    before = _truth_digest(tmp_path)

    result = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
        stale_after_ns=STALE_NS,
    )

    assert result._mode == "full"
    assert result._run_count == 1
    assert before == _truth_digest(tmp_path)
    connection = _open_fresh_index(tmp_path)
    try:
        row = connection.execute(
            """
            SELECT run_id, state, outcome_class, evaluation_count,
                   dataset_role, platform_git_commit
            FROM run
            """
        ).fetchone()
        assert row == (run_id, "COMPLETE", "evaluated", 1, None, "a" * 40)
        metric = connection.execute(
            "SELECT metric_name, value_bits_hex, effective_samples FROM metric"
        ).fetchone()
        assert metric == ("rank_ic", "3fd0000000000000", 4)
    finally:
        connection.close()


def test_audit_references_are_indexed_as_an_ordered_plural_relation(tmp_path: Path) -> None:
    run_id = _register(tmp_path, run_index=1)
    _start(tmp_path, run_id)
    sessions = (
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    )
    event_id = "00000000-0000-4000-8000-000000000003"
    for offset, session_id in enumerate(sessions):
        _record_run_access_attempt(
            runs_root=tmp_path,
            run_id=run_id,
            session_id=session_id,
            now_unix_ns=BASE_NS + 110 + offset,
        )
    _record_run_access(
        runs_root=tmp_path,
        run_id=run_id,
        oos_touched=True,
        lockbox_touched=False,
        session_id=sessions[0],
        audit_event_id=event_id,
        now_unix_ns=BASE_NS + 120,
    )
    _finish_run(
        runs_root=tmp_path,
        run_id=run_id,
        terminal_state=RunState.COMPLETE,
        inventory=_evaluated_inventory(value=0.25),
        failure_reason=None,
        now_unix_ns=BASE_NS + 200,
    )

    _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    connection = _open_fresh_index(tmp_path)
    try:
        rows = connection.execute(
            "SELECT ordinal, session_id, audit_event_id FROM access_audit_ref ORDER BY ordinal"
        ).fetchall()
        assert rows == [(0, sessions[0], event_id), (1, sessions[1], None)]
    finally:
        connection.close()


def test_deleting_and_rebuilding_index_reproduces_all_rows(tmp_path: Path) -> None:
    _terminal_run(tmp_path)
    first = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    dump_one = _canonical_table_dump(first._path)
    truth_one = _truth_digest(tmp_path)
    first._path.unlink()

    second = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )

    assert _canonical_table_dump(second._path) == dump_one
    assert _truth_digest(tmp_path) == truth_one


def test_incremental_build_is_published_as_a_complete_new_generation(tmp_path: Path) -> None:
    _terminal_run(tmp_path)
    first = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    first_bytes = first._path.read_bytes()
    _register(tmp_path, run_index=2)

    second = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=True,
        now_unix_ns=BASE_NS + 2_000,
    )

    assert second._generation == 2
    assert second._run_count == 2
    assert first_bytes != second._path.read_bytes()
    assert _index_status(tmp_path)["fresh"] is True


def test_failed_staging_build_leaves_published_generation_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _terminal_run(tmp_path)
    result = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    before = result._path.read_bytes()

    def fail_refresh(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("synthetic busy")

    monkeypatch.setattr(build_module, "_refresh_staging", fail_refresh)
    with pytest.raises(sqlite3.OperationalError, match="synthetic busy"):
        _rebuild_index(
            tmp_path,
            builder_git_commit="a" * 40,
            incremental=True,
            now_unix_ns=BASE_NS + 2_000,
        )
    assert result._path.read_bytes() == before
    assert not tuple((tmp_path / "index").glob("*.tmp"))


def test_corrupt_existing_index_recovers_through_full_staging_rebuild(tmp_path: Path) -> None:
    _terminal_run(tmp_path)
    result = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    result._path.write_bytes(b"not sqlite")

    recovered = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=True,
        now_unix_ns=BASE_NS + 2_000,
    )

    assert recovered._mode == "full-recovery"
    assert _index_status(tmp_path)["available"] is True


def test_index_refuses_stale_or_scan_error_content(tmp_path: Path) -> None:
    _terminal_run(tmp_path)
    _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    _register(tmp_path, run_index=2)
    with pytest.raises(_IndexRebuildRequiredError, match="stale"):
        _open_fresh_index(tmp_path)

    state_path = tmp_path / "runs" / _register(tmp_path, run_index=3) / "state.json"
    state_path.write_text("{}")
    result = _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 2_000,
    )
    assert result._scan_error_count == 1
    with pytest.raises(_IndexRebuildRequiredError, match="scan errors"):
        _open_fresh_index(tmp_path)


def test_stray_entry_in_run_collection_is_a_hard_error(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "registry.sqlite").write_text("forbidden")
    with pytest.raises(_IndexError, match="unexpected entry"):
        _rebuild_index(
            tmp_path,
            builder_git_commit="a" * 40,
            incremental=False,
            now_unix_ns=BASE_NS + 1_000,
        )


def test_index_contains_no_non_index_truth_mutation(tmp_path: Path) -> None:
    _terminal_run(tmp_path)
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({"event": "fixed"}) + "\n")
    before_audit = audit.read_bytes()
    before_truth = _truth_digest(tmp_path)
    _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    assert audit.read_bytes() == before_audit
    assert _truth_digest(tmp_path) == before_truth
    assert not any(name.endswith(("-wal", "-shm")) for name in os.listdir(tmp_path / "index"))


def test_readers_keep_a_complete_generation_while_rebuild_publishes_next(
    tmp_path: Path,
) -> None:
    _terminal_run(tmp_path, run_index=1)
    _rebuild_index(
        tmp_path,
        builder_git_commit="a" * 40,
        incremental=False,
        now_unix_ns=BASE_NS + 1_000,
    )
    old_reader = _open_fresh_index(tmp_path)
    try:
        assert old_reader.execute("SELECT COUNT(*) FROM run").fetchone() == (1,)
        _terminal_run(tmp_path, run_index=2)
        _rebuild_index(
            tmp_path,
            builder_git_commit="a" * 40,
            incremental=True,
            now_unix_ns=BASE_NS + 2_000,
        )
        assert old_reader.execute("SELECT COUNT(*) FROM run").fetchone() == (1,)
        new_reader = _open_fresh_index(tmp_path)
        try:
            assert new_reader.execute("SELECT COUNT(*) FROM run").fetchone() == (2,)
        finally:
            new_reader.close()
    finally:
        old_reader.close()


def test_index_status_on_missing_root_is_non_mutating(tmp_path: Path) -> None:
    missing = (tmp_path / "absent").resolve()
    status = _index_status(missing)
    assert status["available"] is False
    assert status["fresh"] is False
    assert not missing.exists()


def test_sweep_invocation_receipts_do_not_stale_the_research_index(tmp_path: Path) -> None:
    sweep = tmp_path / "sweeps" / "sweep-a"
    sweep.mkdir(parents=True)
    (sweep / "sweep.json").write_text("{}\n", encoding="ascii")
    before = _source_token(tmp_path)

    (sweep / "invocations.jsonl").write_text('{"status":"complete"}\n', encoding="ascii")
    (sweep / "dispatcher.lock").touch()

    assert _source_token(tmp_path) == before
