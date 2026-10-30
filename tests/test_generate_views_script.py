"""Synthetic-only tests for the Slice 1F Stage-B operational script."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest
import scripts.generate_views as script

import trading_research.data.views_generation as generation
from tests.load_fixtures import GenerationCase, make_generation_case
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.data.audit import JsonlAuditWriter
from trading_research.data.gating import (
    AccessContext,
    AccessGrant,
    AccessRequest,
    SplitPolicy,
    SplitPolicyIdentity,
)
from trading_research.data.integrity import MarketFrameLoader
from trading_research.data.manifests import read_manifest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trading_research.core.frames import MarketFrame
    from trading_research.data.registry import DatasetRegistry


_GIT_COMMIT = "a" * 40
_GENERATED_AT = "2026-08-09T12:34:56Z"
_POLICY_CAP_ENCODED = "synthetic-native-key-3999"
_GENERATED_ROLES = (
    ViewRole.SNAPSHOT,
    ViewRole.TRAIN,
    ViewRole.VALIDATION,
    ViewRole.OOS,
    ViewRole.LOCKBOX,
)


@dataclass(frozen=True, slots=True)
class _SyntheticStageB:
    case: GenerationCase
    config: script._StageBConfig


def _clean_git(repo_root: Path, script_path: Path) -> script._GitState:
    del repo_root, script_path
    return script._GitState(
        head=_GIT_COMMIT,
        local_main=_GIT_COMMIT,
        origin_main=_GIT_COMMIT,
        clean=True,
        script_committed_at_head=True,
    )


def _generated_at() -> str:
    return _GENERATED_AT


def _fake_smoke(repo_root: Path) -> None:
    del repo_root


def _forbidden(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("a post-confirmation production-capable hook was reached")


def _hooks(
    *,
    inspect_git: script._InspectGitHook = _clean_git,
    fingerprint: script._FingerprintHook = script._canonical_source_fingerprint,
    prepare_bundle: script._PrepareHook = generation._prepare_view_bundle,
    smoke: script._SmokeHook = _fake_smoke,
    authorize: script._AuthorizeHook = script._authorize_existing,
    execute: script._ExecuteHook = generation._execute_prepared_bundle,
) -> script._Hooks:
    return script._Hooks(
        inspect_git=inspect_git,
        fingerprint=fingerprint,
        generated_at_utc=_generated_at,
        prepare_bundle=prepare_bundle,
        run_train_smoke=smoke,
        authorize_curated=authorize,
        execute_bundle=execute,
    )


def _make_synthetic_stage_b(tmp_path: Path) -> _SyntheticStageB:
    """Adapt the Stage-A fixture to the production script's id-based layout."""
    case = make_generation_case(tmp_path / "fixture", platform_git_commit=_GIT_COMMIT)
    raw_directory = case.partition_source.manifest_path.parent
    curated_directory = case.curated_source.manifest_path.parent
    raw_directory.rename(case.source_root / case.partition_source.identity.dataset_id)
    curated_directory.rename(case.source_root / case.curated_source.identity.dataset_id)

    runs_root = (tmp_path / "synthetic-runs").resolve()
    runs_root.mkdir()
    repo_root = (tmp_path / "synthetic-repo").resolve()
    ids = case.generated_view_ids
    config = script._StageBConfig(
        repo_root=repo_root,
        script_path=repo_root / "scripts/generate_views.py",
        dataset_root=case.source_root,
        views_root=case.views_root,
        audit_path=runs_root / "audit.jsonl",
        policy_path=case.source_root / "split-policy.json",
        source_ref=case.curated_ref,
        partition_source=case.partition_source.identity,
        policy_identity=case.policy.identity,
        view_ids=generation._ViewIds(
            snapshot=ids[ViewRole.SNAPSHOT],
            train=ids[ViewRole.TRAIN],
            validation=ids[ViewRole.VALIDATION],
            oos=ids[ViewRole.OOS],
            lockbox=ids[ViewRole.LOCKBOX],
        ),
        generator_name=script._GENERATOR_NAME,
        generator_version=script._GENERATOR_VERSION,
        policy_cap_encoded=_POLICY_CAP_ENCODED,
        expected_source_fingerprint=script._canonical_source_fingerprint(case.source_root).sha256,
    )
    return _SyntheticStageB(case=case, config=config)


def _invoke(
    argv: Sequence[str],
    *,
    config: script._StageBConfig,
    hooks: script._Hooks,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    status = script.main(
        argv,
        config=config,
        hooks=hooks,
        stdout=stdout,
        stderr=stderr,
    )
    return status, stdout.getvalue(), stderr.getvalue()


def _audit_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        cast("dict[str, object]", json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _manifest_paths(config: script._StageBConfig) -> dict[ViewRole, Path]:
    return {
        role: config.views_root / view_id / "manifest.json"
        for role, view_id in config.view_ids.items()
    }


@pytest.mark.parametrize("argv", [(), ("preflight",)])
def test_default_and_explicit_preflight_create_only_the_empty_views_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    synthetic.config.views_root.rmdir()

    monkeypatch.setattr(MarketFrameLoader, "load", _forbidden)
    monkeypatch.setattr(JsonlAuditWriter, "append", _forbidden)
    status, output, error = _invoke(
        argv,
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 0
    assert error == ""
    assert synthetic.config.views_root.is_dir()
    assert tuple(synthetic.config.views_root.iterdir()) == ()
    assert not synthetic.config.audit_path.exists()
    assert not tuple(synthetic.config.views_root.rglob("manifest.json"))
    assert "metadata only; no Parquet opened" in output
    assert "Preflight side effect: created only the empty canonical views root" in output
    assert "Permitted preflight side effect: empty canonical views root only" in output
    assert f"CURATED identity: {synthetic.config.source_ref.dataset_id}" in output
    assert synthetic.config.partition_source.dataset_id in output
    assert synthetic.config.policy_identity.policy_sha256 in output
    assert "_prepare_view_bundle mode: fresh" in output
    assert "Bundle state: fresh" in output
    assert "Immutable-source fingerprint command:" in output
    assert synthetic.config.expected_source_fingerprint is not None
    assert synthetic.config.expected_source_fingerprint in output
    assert "generated_at_utc procedure:" in output
    for _, view_id in synthetic.config.view_ids.items():
        assert view_id in output
    assert (
        "No smoke, authorization, grant, loader, audit append, or view generation occurred"
        in output
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("execute",),
        ("execute", "--confirm", "generate views"),
        ("execute", "--confirm"),
        ("execute", "--confirm", script._CONFIRMATION_LITERAL, "extra"),
    ],
)
def test_execute_refuses_missing_wrong_or_malformed_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    monkeypatch.setattr(MarketFrameLoader, "load", _forbidden)
    monkeypatch.setattr(JsonlAuditWriter, "append", _forbidden)

    status, _, error = _invoke(
        argv,
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 2
    assert "REFUSED:" in error
    assert script._CONFIRMATION_LITERAL in error
    assert not synthetic.config.audit_path.exists()
    assert not tuple(synthetic.config.views_root.rglob("manifest.json"))


def test_confirmed_execution_orders_prepare_smoke_authorize_and_bundle(
    tmp_path: Path,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    order: list[str] = []

    def prepare(
        *,
        registry: DatasetRegistry,
        policy: SplitPolicy,
        spec: generation._BundleSpec,
    ) -> generation._PreparedBundle:
        order.append("preflight")
        return generation._prepare_view_bundle(registry=registry, policy=policy, spec=spec)

    def smoke(repo_root: Path) -> None:
        del repo_root
        order.append("smoke")

    def authorize(
        *,
        registry: DatasetRegistry,
        request: AccessRequest,
        policy: SplitPolicy,
        context: AccessContext,
        audit_writer: script._AuditAppender,
    ) -> AccessGrant:
        order.append("authorize")
        return script._authorize_existing(
            registry=registry,
            request=request,
            policy=policy,
            context=context,
            audit_writer=audit_writer,
        )

    def execute(
        *,
        prepared: generation._PreparedBundle,
        loader: MarketFrameLoader,
        grant: AccessGrant,
        context: AccessContext,
        audit_writer: script._AuditAppender,
    ) -> generation._GenerationResult:
        order.append("execute bundle")
        return generation._execute_prepared_bundle(
            prepared=prepared,
            loader=loader,
            grant=grant,
            context=context,
            audit_writer=audit_writer,
        )

    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(
            prepare_bundle=prepare,
            smoke=smoke,
            authorize=authorize,
            execute=execute,
        ),
    )

    assert status == 0
    assert error == ""
    assert order == ["preflight", "smoke", "authorize", "execute bundle"]


def test_smoke_failure_prevents_authorization_audit_and_generation(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    calls: list[str] = []

    def failing_smoke(repo_root: Path) -> NoReturn:
        del repo_root
        calls.append("smoke")
        raise subprocess.CalledProcessError(1, ["synthetic-real-smoke"])

    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(smoke=failing_smoke, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "REFUSED:" in error
    assert calls == ["smoke"]
    assert not synthetic.config.audit_path.exists()
    assert not tuple(synthetic.config.views_root.rglob("manifest.json"))


@pytest.mark.parametrize(
    "pytest_output",
    [
        "1 skipped in 0.01s\n",
        "11 passed in 0.01s\n",
    ],
)
def test_non_single_pass_real_smoke_evidence_refuses_before_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pytest_output: str,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    assert synthetic.config.expected_source_fingerprint is not None
    smoke_commands: list[Sequence[str]] = []

    def fingerprint(root: Path) -> script._SourceFingerprint:
        assert root == synthetic.config.dataset_root
        return script._SourceFingerprint(
            sha256=cast("str", synthetic.config.expected_source_fingerprint),
            entry_count=22,
        )

    def skipped_pytest(
        arguments: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        smoke_commands.append(arguments)
        return subprocess.CompletedProcess(
            args=arguments,
            returncode=0,
            stdout=pytest_output,
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", skipped_pytest)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(
            fingerprint=fingerprint,
            smoke=script._run_real_train_smoke,
            authorize=_forbidden,
            execute=_forbidden,
        ),
    )

    assert status == 1
    assert "did not report exactly one passing test" in error
    assert pytest_output.strip() in error
    assert len(smoke_commands) == 1
    assert script._REAL_TRAIN_SMOKE_NODE in smoke_commands[0]
    assert not synthetic.config.audit_path.exists()
    assert not tuple(synthetic.config.views_root.rglob("manifest.json"))


def test_exact_single_pass_real_smoke_evidence_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Sequence[str]] = []

    def passing_pytest(
        arguments: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(
            args=arguments,
            returncode=0,
            stdout="1 passed, 1 warning in 0.01s\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", passing_pytest)
    script._run_real_train_smoke(tmp_path)

    assert len(calls) == 1
    assert script._REAL_TRAIN_SMOKE_NODE in calls[0]
    assert "-q" not in calls[0]


def test_repository_change_during_smoke_refuses_before_authorization(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    first = _clean_git(Path(), Path())
    second = script._GitState(
        head="b" * 40,
        local_main="b" * 40,
        origin_main="b" * 40,
        clean=True,
        script_committed_at_head=True,
    )
    git_states = iter((first, second))
    smoke_calls: list[Path] = []

    def inspect_git(repo_root: Path, script_path: Path) -> script._GitState:
        del repo_root, script_path
        return next(git_states)

    def smoke(repo_root: Path) -> None:
        smoke_calls.append(repo_root)

    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(
            inspect_git=inspect_git,
            smoke=smoke,
            authorize=_forbidden,
            execute=_forbidden,
        ),
    )

    assert status == 1
    assert "repository identity changed during the real TRAIN smoke" in error
    assert smoke_calls == [synthetic.config.repo_root]
    assert not synthetic.config.audit_path.exists()
    assert not tuple(synthetic.config.views_root.rglob("manifest.json"))


def test_fresh_synthetic_execution_produces_five_manifests_and_two_audits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    loads: list[tuple[AccessGrant, dict[str, object]]] = []
    real_load = MarketFrameLoader.load

    def traced_load(
        self: MarketFrameLoader,
        grant: AccessGrant,
        **kwargs: object,
    ) -> MarketFrame:
        loads.append((grant, dict(kwargs)))
        return cast("MarketFrame", cast("Any", real_load)(self, grant, **kwargs))

    monkeypatch.setattr(MarketFrameLoader, "load", traced_load)
    status, output, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )

    paths = _manifest_paths(synthetic.config)
    records = _audit_records(synthetic.config.audit_path)
    assert status == 0
    assert error == ""
    assert set(paths) == set(_GENERATED_ROLES)
    assert all(path.is_file() for path in paths.values())
    assert {read_manifest(path).role for path in paths.values()} == set(_GENERATED_ROLES)
    assert len(loads) == 1
    assert loads[0][1] == {"columns": ()}
    assert [record["event_type"] for record in records] == [
        "dataset_access_decision",
        "snapshot_binding",
    ]
    assert stat.S_IMODE(synthetic.config.audit_path.stat().st_mode) == 0o600
    assert "Stage-B accepted bundle executor completed" in output
    assert "Execution mode: fresh" in output
    assert "Audit outcome: 1 dataset_access_decision, then 1 snapshot_binding" in output
    assert "Immutable-source fingerprint before/after:" in output
    assert "CP-4 evidence candidate rendered" in output


def test_partial_resume_adds_one_access_decision_and_restores_only_missing_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    first_status, _, first_error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert first_status == 0
    assert first_error == ""

    paths = _manifest_paths(synthetic.config)
    original = {role: path.read_bytes() for role, path in paths.items()}
    missing_role = ViewRole.VALIDATION
    paths[missing_role].unlink()
    load_calls: list[dict[str, object]] = []
    real_load = MarketFrameLoader.load

    def traced_load(
        self: MarketFrameLoader,
        grant: AccessGrant,
        **kwargs: object,
    ) -> MarketFrame:
        load_calls.append(dict(kwargs))
        return cast("MarketFrame", cast("Any", real_load)(self, grant, **kwargs))

    monkeypatch.setattr(MarketFrameLoader, "load", traced_load)
    status, output, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )

    records = _audit_records(synthetic.config.audit_path)
    assert status == 0
    assert error == ""
    assert load_calls == [{"columns": ()}]
    assert [record["event_type"] for record in records] == [
        "dataset_access_decision",
        "snapshot_binding",
        "dataset_access_decision",
    ]
    assert paths[missing_role].read_bytes() == original[missing_role]
    assert {
        role: path.read_bytes() for role, path in paths.items() if role is not missing_role
    } == {role: payload for role, payload in original.items() if role is not missing_role}
    assert "Bundle state: partial resume" in output
    assert "Execution mode: resume" in output
    assert "Audit outcome: 1 dataset_access_decision, 0 snapshot_binding" in output


def test_complete_bundle_is_terminal_noop_before_confirmation_or_real_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    first_status, _, first_error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert first_status == 0
    assert first_error == ""

    paths = _manifest_paths(synthetic.config)
    manifests_before = {
        role: (path.read_bytes(), path.stat().st_mtime_ns) for role, path in paths.items()
    }
    audit_before = (
        synthetic.config.audit_path.read_bytes(),
        synthetic.config.audit_path.stat().st_mtime_ns,
    )
    monkeypatch.setattr(MarketFrameLoader, "load", _forbidden)
    monkeypatch.setattr(JsonlAuditWriter, "append", _forbidden)

    status, output, error = _invoke(
        ("execute",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 0
    assert error == ""
    assert "Bundle state: already complete" in output
    assert "Bundle already complete — nothing to execute." in output
    assert {
        role: (path.read_bytes(), path.stat().st_mtime_ns) for role, path in paths.items()
    } == manifests_before
    assert (
        synthetic.config.audit_path.read_bytes(),
        synthetic.config.audit_path.stat().st_mtime_ns,
    ) == audit_before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("dirty", "worktree is dirty"),
        ("main", "local main differs from origin/main"),
        ("head", "HEAD is not the intended"),
        ("script", "not the committed version"),
    ],
)
def test_unsafe_repository_state_refuses_before_preparation(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    base = _clean_git(Path(), Path())
    if mutation == "dirty":
        state = replace(base, clean=False)
    elif mutation == "main":
        state = replace(base, local_main="b" * 40)
    elif mutation == "head":
        state = replace(base, head="b" * 40)
    else:
        state = replace(base, script_committed_at_head=False)

    def inspect_git(repo_root: Path, script_path: Path) -> script._GitState:
        del repo_root, script_path
        return state

    status, _, error = _invoke(
        ("preflight",),
        config=synthetic.config,
        hooks=_hooks(
            inspect_git=inspect_git,
            prepare_bundle=_forbidden,
            smoke=_forbidden,
            authorize=_forbidden,
            execute=_forbidden,
        ),
    )

    assert status == 1
    assert message in error
    assert not synthetic.config.audit_path.exists()
    assert not tuple(synthetic.config.views_root.rglob("manifest.json"))


def test_clean_committed_feature_branch_is_allowed_when_explicitly_configured(
    tmp_path: Path,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    state = replace(
        _clean_git(Path(), Path()),
        head="b" * 40,
        local_main="c" * 40,
        origin_main="d" * 40,
    )

    def inspect_git(repo_root: Path, script_path: Path) -> script._GitState:
        del repo_root, script_path
        return state

    config = replace(synthetic.config, require_canonical_main=False)
    status, output, error = _invoke(
        ("preflight",),
        config=config,
        hooks=_hooks(inspect_git=inspect_git),
    )

    assert status == 0
    assert error == ""
    assert f"Git HEAD: {state.head}" in output
    assert "Preflight complete" in output


def test_source_policy_and_fingerprint_identity_mismatches_refuse(tmp_path: Path) -> None:
    source_case = _make_synthetic_stage_b(tmp_path / "source")
    wrong_source = replace(
        source_case.config,
        source_ref=DatasetRef(
            dataset_id=source_case.config.source_ref.dataset_id,
            manifest_sha256="b" * 64,
            role=ViewRole.CURATED,
        ),
    )
    status, _, error = _invoke(
        ("preflight",), config=wrong_source, hooks=_hooks(prepare_bundle=_forbidden)
    )
    assert status == 1
    assert "CURATED source manifest digest mismatches" in error

    policy_case = _make_synthetic_stage_b(tmp_path / "policy")
    wrong_policy = replace(
        policy_case.config,
        policy_identity=SplitPolicyIdentity(
            adr=policy_case.config.policy_identity.adr,
            policy_version=policy_case.config.policy_identity.policy_version,
            policy_sha256="b" * 64,
        ),
    )
    status, _, error = _invoke(
        ("preflight",), config=wrong_policy, hooks=_hooks(prepare_bundle=_forbidden)
    )
    assert status == 1
    assert "requested split policy" in error

    fingerprint_case = _make_synthetic_stage_b(tmp_path / "fingerprint")
    wrong_fingerprint = replace(
        fingerprint_case.config,
        expected_source_fingerprint="b" * 64,
    )
    status, _, error = _invoke(
        ("preflight",),
        config=wrong_fingerprint,
        hooks=_hooks(prepare_bundle=_forbidden),
    )
    assert status == 1
    assert "fingerprint differs" in error


def test_absent_views_root_inside_source_is_refused_without_creation(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    overlapping = synthetic.config.dataset_root / "forbidden-generated-views"
    config = replace(synthetic.config, views_root=overlapping)

    status, _, error = _invoke(
        ("preflight",),
        config=config,
        hooks=_hooks(prepare_bundle=_forbidden),
    )

    assert status == 1
    assert "generated views root must be separate" in error
    assert not overlapping.exists()
    assert not synthetic.config.audit_path.exists()


def test_registry_collision_refuses_during_preflight(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    ids = synthetic.config.view_ids
    colliding = generation._ViewIds(
        snapshot=synthetic.config.partition_source.dataset_id,
        train=ids.train,
        validation=ids.validation,
        oos=ids.oos,
        lockbox=ids.lockbox,
    )

    status, _, error = _invoke(
        ("preflight",),
        config=replace(synthetic.config, view_ids=colliding),
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "collides with registered manifest" in error
    assert not synthetic.config.audit_path.exists()


def test_orphan_children_refuse_without_new_audit_or_real_hooks(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()
    _manifest_paths(synthetic.config)[ViewRole.SNAPSHOT].unlink()

    status, _, error = _invoke(
        ("preflight",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "parent" in error.lower()
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_conflicting_child_refuses_during_metadata_preflight(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()
    child = _manifest_paths(synthetic.config)[ViewRole.TRAIN]
    document = cast("dict[str, Any]", json.loads(child.read_bytes()))
    document["generator"]["name"] = "conflicting-generator"
    child.write_bytes(generation._canonical_manifest_bytes(document))

    status, _, error = _invoke(
        ("preflight",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "wrong generator provenance" in error
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_child_physical_partition_identity_conflict_refuses_preflight(
    tmp_path: Path,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()
    child = _manifest_paths(synthetic.config)[ViewRole.TRAIN]
    document = cast("dict[str, Any]", json.loads(child.read_bytes()))
    document["admitted_partitions"][0]["sha256"] = "b" * 64
    child.write_bytes(generation._canonical_manifest_bytes(document))

    status, _, error = _invoke(
        ("preflight",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "conflicting physical partition identity" in error
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_child_non_generator_digest_encoding_refuses_complete_noop(
    tmp_path: Path,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()
    child = _manifest_paths(synthetic.config)[ViewRole.TRAIN]
    document = cast("dict[str, Any]", json.loads(child.read_bytes()))
    partition = cast("dict[str, Any]", document["admitted_partitions"][0])
    partition["sha256"] = cast("str", partition["sha256"]).upper()
    child.write_bytes(generation._canonical_manifest_bytes(document))

    status, output, error = _invoke(
        ("execute",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "non-generator partition digest encoding" in error
    assert "Bundle already complete" not in output
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_child_coercive_numeric_wire_value_refuses_complete_noop(
    tmp_path: Path,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()
    child = _manifest_paths(synthetic.config)[ViewRole.TRAIN]
    document = cast("dict[str, Any]", json.loads(child.read_bytes()))
    partition = cast("dict[str, Any]", document["admitted_partitions"][0])
    partition["rows"] = str(partition["rows"])
    child.write_bytes(generation._canonical_manifest_bytes(document))

    status, output, error = _invoke(
        ("execute",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "coercive non-generator wire values" in error
    assert "Bundle already complete" not in output
    assert synthetic.config.audit_path.read_bytes() == audit_before


@pytest.mark.parametrize(
    ("conflict", "message"),
    [
        ("duplicate", "repeats a physical partition slot"),
        ("zero-row", "zero-row partition contribution"),
    ],
)
def test_child_duplicate_or_zero_row_partition_refuses_preflight(
    tmp_path: Path,
    conflict: str,
    message: str,
) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()
    child = _manifest_paths(synthetic.config)[ViewRole.TRAIN]
    document = cast("dict[str, Any]", json.loads(child.read_bytes()))
    partition = cast("dict[str, Any]", document["admitted_partitions"][0])
    if conflict == "duplicate":
        first = dict(partition)
        second = dict(partition)
        first["rows"] = 1
        first["last_key"] = first["first_key"]
        second["rows"] = partition["rows"] - 1
        second["first_key"] = first["first_key"] + 1
        document["admitted_partitions"] = [first, second]
    else:
        partition["rows"] = 0
        document["row_count_expected"] = 0
    child.write_bytes(generation._canonical_manifest_bytes(document))

    status, _, error = _invoke(
        ("preflight",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert message in error
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_anchor_provenance_mismatch_refuses_before_authorization(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    paths = _manifest_paths(synthetic.config)
    for role in script._TIER_ONE_ROLES:
        paths[role].unlink()
    anchor = paths[ViewRole.SNAPSHOT]
    document = cast("dict[str, Any]", json.loads(anchor.read_bytes()))
    document["generator"]["version"] = "incompatible"
    anchor.write_bytes(generation._canonical_manifest_bytes(document))
    audit_before = synthetic.config.audit_path.read_bytes()

    status, _, error = _invoke(
        ("preflight",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "generator version is incompatible" in error
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_anchor_notes_refuse_complete_bundle_terminal_noop(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    paths = _manifest_paths(synthetic.config)
    anchor = paths[ViewRole.SNAPSHOT]
    anchor_document = cast("dict[str, Any]", json.loads(anchor.read_bytes()))
    anchor_document["notes"] = "not emitted by the accepted generator"
    anchor_payload = generation._canonical_manifest_bytes(anchor_document)
    anchor.write_bytes(anchor_payload)
    anchor_digest = hashlib.sha256(anchor_payload).hexdigest()
    for role in script._TIER_ONE_ROLES:
        child = paths[role]
        child_document = cast("dict[str, Any]", json.loads(child.read_bytes()))
        child_document["parent"]["manifest_sha256"] = anchor_digest
        child.write_bytes(generation._canonical_manifest_bytes(child_document))
    audit_before = synthetic.config.audit_path.read_bytes()

    status, output, error = _invoke(
        ("execute",),
        config=synthetic.config,
        hooks=_hooks(smoke=_forbidden, authorize=_forbidden, execute=_forbidden),
    )

    assert status == 1
    assert "SNAPSHOT anchor has unexpected notes field" in error
    assert "Bundle already complete" not in output
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_anchor_change_after_prepare_refuses_before_complete_noop(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    status, _, error = _invoke(
        ("execute", "--confirm", script._CONFIRMATION_LITERAL),
        config=synthetic.config,
        hooks=_hooks(),
    )
    assert status == 0
    assert error == ""
    audit_before = synthetic.config.audit_path.read_bytes()

    def prepare_then_mutate_anchor(
        *,
        registry: DatasetRegistry,
        policy: SplitPolicy,
        spec: generation._BundleSpec,
    ) -> generation._PreparedBundle:
        prepared = generation._prepare_view_bundle(
            registry=registry,
            policy=policy,
            spec=spec,
        )
        assert prepared.anchor is not None
        prepared.anchor.path.write_bytes(prepared.anchor.raw_bytes + b" ")
        return prepared

    status, output, error = _invoke(
        ("execute",),
        config=synthetic.config,
        hooks=_hooks(
            prepare_bundle=prepare_then_mutate_anchor,
            smoke=_forbidden,
            authorize=_forbidden,
            execute=_forbidden,
        ),
    )

    assert status == 1
    assert "existing SNAPSHOT anchor changed after bundle preparation" in error
    assert "Bundle already complete" not in output
    assert synthetic.config.audit_path.read_bytes() == audit_before


def test_canonical_fingerprint_matches_documented_pipeline_and_excludes_mtime(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "fingerprint-root").resolve()
    nested = root / "nested"
    nested.mkdir(parents=True)
    ordinary = root / "manifest.json"
    ordinary.write_text("{}\n", encoding="utf-8")
    unreadable = nested / "synthetic.parquet"
    unreadable.write_bytes(b"synthetic payload that must never be opened")
    unreadable.chmod(0)
    try:
        observed = script._canonical_source_fingerprint(root)
        canonical = (
            subprocess.run(
                ["bash", "-c", script._CANONICAL_FINGERPRINT_PIPELINE],
                cwd=root,
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .split()[0]
        )
        assert observed.sha256 == canonical
        assert observed.entry_count == 3

        os.utime(ordinary, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        os.utime(unreadable, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        assert script._canonical_source_fingerprint(root) == observed

        unreadable.chmod(0o600)
        unreadable.write_bytes(b"different-sized synthetic payload")
        assert script._canonical_source_fingerprint(root).sha256 != observed.sha256
    finally:
        unreadable.chmod(0o600)


def test_script_has_bounded_composition_root_architecture() -> None:
    path = Path(script.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imported_roots = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imported_roots.isdisjoint(
        {"click", "docopt", "duckdb", "fire", "pandas", "polars", "pyarrow", "typer"}
    )

    call_leaves = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "load" not in call_leaves
    append_receivers = [
        ast.unparse(node.func.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
    ]
    assert append_receivers == ["existing"]
    forbidden_generation_semantics = {
        "_allocate_binding_event_id",
        "_derive_admissions",
        "_facts_for_range",
        "_manifest_document",
        "_publish_children",
        "_publish_fresh",
        "_publish_manifest_bytes",
        "_scan_complete_source_keys",
        "_snapshot_binding_event",
    }
    referenced_attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert referenced_attributes.isdisjoint(forbidden_generation_semantics)
    assert source.count("_generation._prepare_view_bundle") == 1
    assert source.count("_generation._execute_prepared_bundle") == 1
    assert "JsonlAuditWriter(config.audit_path)" in source
    assert "return authorize_access(" in source
    assert '_CONFIRMATION_LITERAL = "GENERATE VIEWS"' in source
    assert script._PRODUCTION_CONFIG.script_path == path.resolve()

    main_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main"
    ]
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and ast.unparse(node.test) == "__name__ == '__main__'"
    ]
    assert len(main_calls) == 1
    assert len(guards) == 1
    assert main_calls[0] in tuple(ast.walk(guards[0]))


def test_every_script_execution_fixture_is_bounded_to_tmp_path(tmp_path: Path) -> None:
    synthetic = _make_synthetic_stage_b(tmp_path)
    bounded_paths = (
        synthetic.config.repo_root,
        synthetic.config.script_path,
        synthetic.config.dataset_root,
        synthetic.config.views_root,
        synthetic.config.audit_path,
        synthetic.config.policy_path,
    )
    assert all(path == tmp_path or tmp_path in path.parents for path in bounded_paths)
    assert _hooks() is not script._PRODUCTION_HOOKS
    assert synthetic.config.dataset_root != script._DATASET_ROOT
    assert synthetic.config.views_root != script._VIEWS_ROOT
    assert synthetic.config.audit_path != script._AUDIT_PATH
