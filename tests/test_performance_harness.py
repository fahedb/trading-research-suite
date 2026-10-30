from __future__ import annotations

import os
from io import StringIO
from typing import TYPE_CHECKING
from uuid import uuid4

from tests.load_fixtures import RecordingAuditWriter, make_load_case
from tests.performance import _snapshot_operator as snapshot
from tests.performance._measure_child import (
    COLD_UNAVAILABLE_RESULT,
    _manual_cold_samples,
    _retained_payload_bytes,
    _summarize_samples,
    _timed_load,
)
from trading_research.core import ViewRole
from trading_research.data.gating import (
    AccessClassification,
    AccessContext,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    authorize_access,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_snapshot_request_is_exactly_reasoned_and_identity_bound() -> None:
    request = snapshot._snapshot_access_request()

    assert request.dataset_ref == snapshot._SNAPSHOT_REF
    assert request.reason == AccessReason(
        code="phase1h-performance-qualification",
        detail=(
            "Phase 1 infrastructure qualification of the published SNAPSHOT: integrity "
            "verification, load timing and peak RSS only. Not research or evaluation use. "
            "Load, measure, discard — no value-column content is inspected, derived or retained."
        ),
    )
    assert request.gated_unlock == GatedUnlock(
        target=snapshot._SNAPSHOT_REF.canonical_identity
    )


def test_gated_grant_reuses_fresh_equal_policy_and_registry(tmp_path: Path) -> None:
    case = make_load_case(tmp_path, role=ViewRole.SNAPSHOT)
    writer = RecordingAuditWriter()
    ref = case.grant.dataset_ref
    grant = authorize_access(
        registry=case.registry,
        request=AccessRequest(
            dataset_ref=ref,
            reason=AccessReason(
                code="phase1h-performance-qualification",
                detail=snapshot._ACCESS_REASON_DETAIL,
            ),
            gated_unlock=GatedUnlock(target=ref.canonical_identity),
        ),
        policy=case.policy,
        context=AccessContext(
            session_id=uuid4(),
            os_user="synthetic-operator",
            hostname="synthetic-vm",
            pid=os.getpid(),
            platform_git_commit="a" * 40,
            platform_git_dirty=False,
        ),
        audit_writer=writer,
    )

    assert grant.classification is AccessClassification.FULLY_GATED
    assert grant.audit_event_id is not None
    assert len(writer.events) == 1
    assert writer.events[0]["outcome"] == "allowed"
    assert writer.events[0]["classification"] == "fully_gated"
    assert writer.events[0]["authorization"] == {
        "unlock_present": True,
        "unlock_target": {
            "dataset_id": ref.dataset_id,
            "manifest_sha256": ref.manifest_sha256,
        },
        "reason": {
            "code": "phase1h-performance-qualification",
            "detail": snapshot._ACCESS_REASON_DETAIL,
        },
    }

    frame, elapsed_seconds = _timed_load(
        grant,
        columns=("close", "spread"),
        policy_path=case.target.root / "policy.json",
        registry_roots=(case.target.root,),
    )
    assert elapsed_seconds >= 0.0
    assert frame.dataset_ref == ref
    assert frame.num_rows == case.target.manifest.expected_row_count
    assert frame.column_names == ("close", "spread")
    assert len(writer.events) == 1


def test_retained_payload_bytes_drop_with_column_selection(tmp_path: Path) -> None:
    case = make_load_case(tmp_path, role=ViewRole.TRAIN)
    policy_path = case.target.root / "policy.json"
    registry_roots = (case.target.root,)

    full, _ = _timed_load(
        case.grant,
        columns=None,
        policy_path=policy_path,
        registry_roots=registry_roots,
    )
    selected, _ = _timed_load(
        case.grant,
        columns=("close", "spread"),
        policy_path=policy_path,
        registry_roots=registry_roots,
    )

    full_payload_bytes = _retained_payload_bytes(full)
    selected_payload_bytes = _retained_payload_bytes(selected)

    assert full.num_rows == selected.num_rows
    assert selected.column_names == ("close", "spread")
    assert selected_payload_bytes > 0
    assert selected_payload_bytes < full_payload_bytes


def test_manual_cold_handshake_discards_trial_zero_and_keeps_five() -> None:
    observed: list[float] = []
    values = iter((99.0, 1.0, 2.0, 3.0, 4.0, 5.0))

    def load_once() -> float:
        value = next(values)
        observed.append(value)
        return value

    stderr = StringIO()
    samples = _manual_cold_samples(
        load_once,
        stdin=StringIO("\n" * 6),
        stderr=stderr,
    )

    assert observed == [99.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert samples == (1.0, 2.0, 3.0, 4.0, 5.0)
    prompts = stderr.getvalue()
    assert "discarded cold trial" in prompts
    for index in range(1, 6):
        assert f"measured cold #{index}" in prompts


def test_manual_cold_handshake_records_exact_sudo_unavailable_result() -> None:
    calls = 0

    def load_once() -> float:
        nonlocal calls
        calls += 1
        return 1.0

    samples = _manual_cold_samples(
        load_once,
        stdin=StringIO("sudo unavailable\n"),
        stderr=StringIO(),
    )

    assert samples is None
    assert calls == 0
    assert COLD_UNAVAILABLE_RESULT == "not measured on this machine — sudo unavailable"


def test_snapshot_output_is_operational_fields_only() -> None:
    result = snapshot._SnapshotResult(
        pre_load_rss_bytes=100,
        peak_rss_bytes=200,
        warm=_summarize_samples((1.0, 1.1, 1.2, 1.3, 1.4)),
        cold=None,
        cold_unavailable=True,
        stop_reason=None,
    )
    stdout = StringIO()

    snapshot._render_result(result, stdout=stdout)

    keys = {
        line.partition("=")[0] for line in stdout.getvalue().splitlines()
    }
    assert keys == {
        "target_identity",
        "manifest_sha256",
        "row_count",
        "column_names",
        "pre_load_rss_bytes",
        "peak_rss_bytes",
        "rss_status",
        "warm_samples_seconds",
        "warm_median_seconds",
        "warm_minimum_seconds",
        "warm_status",
        "cold_status",
        "integrity_status",
        "audit_count_status",
    }
    assert f"cold_status={COLD_UNAVAILABLE_RESULT}" in stdout.getvalue()
