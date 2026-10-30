"""Range-derived access classification and write-ahead authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid1, uuid4

import pytest

from tests.manifest_fixtures import raw_manifest, v11_manifest, write_manifest
from trading_research.core.errors import (
    AccessDeniedError,
    AccessGrantMismatchError,
    AuditWriteError,
    DatasetRefMismatchError,
    InvalidAccessReasonError,
    MalformedSplitPolicyError,
    SplitPolicyMismatchError,
    SplitPolicyNotFoundError,
    UnsupportedSplitPolicyError,
)
from trading_research.core.identity import DatasetIdentity, DatasetRef, ViewRole
from trading_research.core.time import CalendarTimePolicy, ClockDomain, TimeUnit
from trading_research.data.audit import JsonlAuditWriter
from trading_research.data.gating import (
    AccessClassification,
    AccessContext,
    AccessGrant,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    SplitPolicy,
    SplitPolicyIdentity,
    authorize_access,
    classify_access,
    read_split_policy,
)
from trading_research.data.manifests import (
    ManifestFormat,
    SplitPolicyRef,
    read_manifest,
)
from trading_research.data.registry import DatasetRegistry, RegisteredManifest

if TYPE_CHECKING:
    from collections.abc import Mapping


REPOSITORY_POLICY = (
    Path(__file__).parents[1] / "config" / "split-policies" / "adr-0012-v1.json"
)


class RecordingAuditWriter:
    """In-memory writer double preserving the exact mapping it received."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


class FailingAuditWriter:
    """Writer double for the no-audit/no-grant refusal path."""

    def append(self, event: Mapping[str, object]) -> None:
        del event
        raise AuditWriteError("fault-injected audit failure")


def _policy_document(*, oos_end: int = 399) -> dict[str, object]:
    """Small native-key policy whose boundary can vary between tests."""
    return {
        "schema_version": "1",
        "adr": "ADR-0012",
        "policy_version": "v1",
        "clock_domain": "mt5.synth-server.wallclock",
        "time_unit": "seconds",
        "calendar_time_policy": "native-wallclock-v1",
        "ranges": {
            "train": {"start_inclusive_key": 100, "end_inclusive_key": 199},
            "validation": {"start_inclusive_key": 200, "end_inclusive_key": 299},
            "oos": {"start_inclusive_key": 300, "end_inclusive_key": oos_end},
            "lockbox": {
                "start_inclusive_key": oos_end + 1,
                "end_inclusive_key": oos_end + 150,
            },
        },
        "provenance": {
            "normative_source": "docs/adr/ADR-0012-two-tier-splits.md",
            "ratified_on": "2026-08-06",
        },
    }


def _write_policy(tmp_path: Path, *, oos_end: int = 399) -> tuple[Path, SplitPolicy]:
    """Write and resolve one synthetic policy; no data files are involved."""
    path = tmp_path / f"policy-{oos_end}.json"
    path.write_text(json.dumps(_policy_document(oos_end=oos_end), indent=2))
    return path, read_split_policy(path)


def _view_registry(
    tmp_path: Path,
    policy: SplitPolicy,
    *,
    role: ViewRole,
    first_key: int,
    last_key: int,
    view_id: str | None = None,
) -> tuple[DatasetRegistry, DatasetRef]:
    """Build one exact v1.1 view plus its synthetic raw partition source."""
    root = tmp_path / f"root-{role.value}-{first_key}-{last_key}"
    parent_path = write_manifest(
        root / "parent",
        raw_manifest(first_key=100, last_key=max(last_key, policy.lockbox_range.end)),
    )
    parent_digest = read_manifest(parent_path).manifest_sha256
    dataset_id = view_id or f"SYNTH_{role.value.upper()}_V1"
    document = v11_manifest(
        view_id=dataset_id,
        role=role.value,
        discovery_default=role in {ViewRole.TRAIN, ViewRole.VALIDATION},
        parent_sha256=parent_digest,
        source_sha256=parent_digest,
        policy_sha256=policy.identity.policy_sha256,
        first_key=first_key,
        last_key=last_key,
        rows=max(1, last_key - first_key + 1),
    )
    view_path = write_manifest(root / "view", document)
    registry = DatasetRegistry.discover([root])
    return registry, DatasetRef(
        dataset_id=dataset_id,
        manifest_sha256=read_manifest(view_path).manifest_sha256,
        role=role,
    )


def _raw_registry(tmp_path: Path, policy: SplitPolicy) -> tuple[DatasetRegistry, DatasetRef]:
    """Build a legacy raw identity, which has no policy-ref field by format."""
    root = tmp_path / "legacy-root"
    path = write_manifest(
        root / "raw",
        raw_manifest(first_key=100, last_key=policy.lockbox_range.end),
    )
    manifest = read_manifest(path)
    return DatasetRegistry.discover([root]), DatasetRef(
        dataset_id=manifest.dataset_id,
        manifest_sha256=manifest.manifest_sha256,
        role=ViewRole.RAW,
    )


def _context() -> AccessContext:
    return AccessContext(
        session_id=uuid4(),
        os_user="researcher",
        hostname="research-vm",
        pid=1234,
        platform_git_commit="a" * 40,
        platform_git_dirty=False,
    )


def _reason() -> AccessReason:
    return AccessReason(code="owner-review", detail="Approved synthetic access test")


def _event_section(event: Mapping[str, object], name: str) -> dict[str, object]:
    return cast("dict[str, object]", event[name])


class TestSplitPolicyReader:
    def test_direct_policy_construction_is_disabled(self) -> None:
        with pytest.raises(TypeError):
            SplitPolicy()

    def test_reads_strict_policy_and_hashes_exact_bytes(self, tmp_path: Path) -> None:
        path, policy = _write_policy(tmp_path)
        assert policy.identity == SplitPolicyIdentity(
            adr="ADR-0012",
            policy_version="v1",
            policy_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        assert policy.clock_domain == ClockDomain("mt5.synth-server.wallclock")
        assert policy.time_unit is TimeUnit.SECONDS
        assert policy.calendar_time_policy is CalendarTimePolicy.NATIVE_WALLCLOCK_V1
        assert policy.oos_range.end == 399

    def test_repository_policy_resolves_under_the_strict_reader(self) -> None:
        policy = read_split_policy(REPOSITORY_POLICY)
        assert policy.identity == SplitPolicyIdentity(
            adr="ADR-0012",
            policy_version="v1",
            policy_sha256=hashlib.sha256(REPOSITORY_POLICY.read_bytes()).hexdigest(),
        )

    def test_relocating_identical_policy_bytes_preserves_identity(self, tmp_path: Path) -> None:
        path, policy = _write_policy(tmp_path)
        relocated = tmp_path / "elsewhere" / "renamed.json"
        relocated.parent.mkdir()
        relocated.write_bytes(path.read_bytes())
        assert read_split_policy(relocated).identity == policy.identity

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("adr", "ADR-9999"),
            ("policy_version", "v2"),
            ("policy_sha256", "f" * 64),
        ],
    )
    def test_requested_exact_identity_must_match(
        self, tmp_path: Path, field: str, value: str
    ) -> None:
        path, policy = _write_policy(tmp_path)
        changes = {field: value}
        wrong = replace(policy.identity, **changes)
        with pytest.raises(SplitPolicyMismatchError, match="requested split policy"):
            read_split_policy(path, expected_identity=wrong)

    def test_reformatting_policy_bytes_invalidates_an_exact_request(self, tmp_path: Path) -> None:
        path, policy = _write_policy(tmp_path)
        path.write_text(json.dumps(_policy_document(), separators=(",", ":")))
        with pytest.raises(SplitPolicyMismatchError, match="requested split policy"):
            read_split_policy(path, expected_identity=policy.identity)

    def test_missing_policy_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(SplitPolicyNotFoundError, match="cannot read"):
            read_split_policy(tmp_path / "missing.json")

    def test_unsupported_schema_refuses(self, tmp_path: Path) -> None:
        document = _policy_document()
        document["schema_version"] = "2"
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(UnsupportedSplitPolicyError, match="schema_version"):
            read_split_policy(path)

    def test_duplicate_key_refuses_at_any_depth(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.json"
        path.write_text(
            '{"schema_version":"1","schema_version":"1","adr":"ADR-0012",'
            '"policy_version":"v1","clock_domain":"mt5.synth-server.wallclock",'
            '"time_unit":"seconds","calendar_time_policy":"native-wallclock-v1",'
            '"ranges":{},"provenance":{}}'
        )
        with pytest.raises(MalformedSplitPolicyError, match="duplicate object key"):
            read_split_policy(path)

    @pytest.mark.parametrize("field", ["adr", "ranges", "provenance"])
    def test_missing_top_level_field_refuses(self, tmp_path: Path, field: str) -> None:
        document = _policy_document()
        del document[field]
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError, match="missing"):
            read_split_policy(path)

    def test_unknown_top_level_field_refuses(self, tmp_path: Path) -> None:
        document = _policy_document()
        document["gating_boundary"] = 399
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError, match="unknown"):
            read_split_policy(path)

    @pytest.mark.parametrize(
        ("role", "field"),
        [("train", "extra"), ("validation", "start_inclusive_key")],
    )
    def test_range_fields_are_strict(
        self, tmp_path: Path, role: str, field: str
    ) -> None:
        document = _policy_document()
        ranges = cast("dict[str, dict[str, object]]", document["ranges"])
        if field == "extra":
            ranges[role][field] = 1
        else:
            del ranges[role][field]
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError):
            read_split_policy(path)

    @pytest.mark.parametrize("value", [True, 1.5, 2**63])
    def test_range_bound_must_be_int64(self, tmp_path: Path, value: object) -> None:
        document = _policy_document()
        ranges = cast("dict[str, dict[str, object]]", document["ranges"])
        ranges["train"]["start_inclusive_key"] = value
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError):
            read_split_policy(path)

    def test_inverted_range_refuses(self, tmp_path: Path) -> None:
        document = _policy_document()
        ranges = cast("dict[str, dict[str, object]]", document["ranges"])
        ranges["train"] = {"start_inclusive_key": 199, "end_inclusive_key": 100}
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError, match="inverted"):
            read_split_policy(path)

    def test_overlapping_ranges_refuse(self, tmp_path: Path) -> None:
        document = _policy_document()
        ranges = cast("dict[str, dict[str, object]]", document["ranges"])
        ranges["validation"]["start_inclusive_key"] = 199
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError, match="overlap"):
            read_split_policy(path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("clock_domain", "not a domain/"),
            ("time_unit", "minutes"),
            ("calendar_time_policy", "invented-policy"),
        ],
    )
    def test_invalid_axis_refuses(self, tmp_path: Path, field: str, value: str) -> None:
        document = _policy_document()
        document[field] = value
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(document))
        with pytest.raises(MalformedSplitPolicyError, match="invalid axis"):
            read_split_policy(path)

    def test_nonfinite_number_refuses(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.json"
        text = json.dumps(_policy_document()).replace("100", "NaN", 1)
        path.write_text(text)
        with pytest.raises(MalformedSplitPolicyError, match="non-finite"):
            read_split_policy(path)


class TestAccessModels:
    def test_public_model_fields_match_the_approved_slice_contract(self) -> None:
        assert [field.name for field in fields(AccessReason)] == ["code", "detail"]
        assert [field.name for field in fields(GatedUnlock)] == ["target"]
        assert [field.name for field in fields(AccessRequest)] == [
            "dataset_ref",
            "reason",
            "gated_unlock",
        ]
        assert [field.name for field in fields(AccessContext)] == [
            "session_id",
            "os_user",
            "hostname",
            "pid",
            "platform_git_commit",
            "platform_git_dirty",
        ]
        assert [field.name for field in fields(AccessGrant)] == [
            "registered_manifest",
            "classification",
            "split_policy_identity",
            "session_id",
            "audit_event_id",
        ]

    @pytest.mark.parametrize(
        "code",
        ["", "Upper", "has space", "under_score", "-leading", "trailing-", "a" * 65],
    )
    def test_reason_code_is_a_bounded_lower_case_slug(self, code: str) -> None:
        with pytest.raises(InvalidAccessReasonError) as excinfo:
            AccessReason(code=code, detail="detail")
        assert excinfo.value.denial_code == "invalid_reason"

    def test_reason_detail_is_trimmed(self) -> None:
        reason = AccessReason(code="oos-check", detail="  deliberate evaluation  ")
        assert reason.detail == "deliberate evaluation"

    @pytest.mark.parametrize("detail", ["", "  ", "é" * 1_025])
    def test_reason_detail_is_nonempty_and_utf8_bounded(self, detail: str) -> None:
        with pytest.raises(InvalidAccessReasonError):
            AccessReason(code="oos-check", detail=detail)

    def test_access_classification_is_ordered(self) -> None:
        assert (
            AccessClassification.NORMAL
            < AccessClassification.OOS_EXPLICIT
            < AccessClassification.FULLY_GATED
        )

    def test_context_requires_uuid4(self) -> None:
        with pytest.raises(AccessDeniedError, match="UUID4"):
            replace(_context(), session_id=uuid1())

    def test_context_requires_cleanly_typed_platform_facts(self) -> None:
        with pytest.raises(AccessDeniedError, match="git_commit"):
            replace(_context(), platform_git_commit="not-a-commit")
        with pytest.raises(AccessDeniedError, match="positive integer"):
            replace(_context(), pid=True)

    def test_unlock_is_bound_to_exact_identity(self) -> None:
        target = DatasetIdentity(dataset_id="SYNTH_V1", manifest_sha256="a" * 64)
        assert GatedUnlock(target).target == target


class TestClassification:
    @pytest.mark.parametrize(
        ("role", "first_key", "last_key", "expected"),
        [
            (ViewRole.TRAIN, 110, 190, AccessClassification.NORMAL),
            (ViewRole.VALIDATION, 210, 290, AccessClassification.NORMAL),
            (ViewRole.OOS, 310, 390, AccessClassification.OOS_EXPLICIT),
            (ViewRole.LOCKBOX, 410, 490, AccessClassification.FULLY_GATED),
            (ViewRole.SNAPSHOT, 100, 490, AccessClassification.FULLY_GATED),
            (ViewRole.RAW, 110, 190, AccessClassification.FULLY_GATED),
            (ViewRole.CURATED, 110, 190, AccessClassification.FULLY_GATED),
        ],
    )
    def test_range_classification_and_role_floor(
        self,
        tmp_path: Path,
        role: ViewRole,
        first_key: int,
        last_key: int,
        expected: AccessClassification,
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=role, first_key=first_key, last_key=last_key
        )
        manifest = registry.resolve(ref).manifest
        assert classify_access(manifest, policy) is expected

    def test_gating_has_no_speculative_unknown_range_path(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "src"
            / "trading_research"
            / "data"
            / "gating.py"
        ).read_text()
        assert "unknown_admitted_range" not in source

    def test_end_equal_to_boundary_is_not_fully_gated(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.OOS, first_key=300, last_key=399
        )
        assert (
            classify_access(registry.resolve(ref).manifest, policy)
            is AccessClassification.OOS_EXPLICIT
        )

    def test_policy_boundary_is_read_from_bytes_not_hardcoded(self, tmp_path: Path) -> None:
        _, later = _write_policy(tmp_path, oos_end=399)
        _, earlier = _write_policy(tmp_path, oos_end=349)
        registry, ref = _view_registry(
            tmp_path, later, role=ViewRole.TRAIN, first_key=310, last_key=375
        )
        # Remove the generated-view policy pin and containment contract solely
        # to isolate the pure boundary rule under two valid policy artifacts.
        subject = replace(
            registry.resolve(ref).manifest,
            source_format=ManifestFormat.MT5_RAW_V1,
            split_policy_ref=None,
            calendar_time_policy=None,
        )
        assert classify_access(subject, later) is AccessClassification.OOS_EXPLICIT
        assert classify_access(subject, earlier) is AccessClassification.FULLY_GATED

    def test_future_dataset_id_does_not_evade_range_gate(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path,
            policy,
            role=ViewRole.SNAPSHOT,
            first_key=100,
            last_key=450,
            view_id="FUTURE_UNSEEN_DATASET_V9",
        )
        assert (
            classify_access(registry.resolve(ref).manifest, policy)
            is AccessClassification.FULLY_GATED
        )

    def test_legacy_parent_without_policy_ref_uses_supplied_policy(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        manifest = registry.resolve(ref).manifest
        assert manifest.split_policy_ref is None
        assert classify_access(manifest, policy) is AccessClassification.FULLY_GATED

    def test_generated_view_requires_exact_policy_ref(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        manifest = registry.resolve(ref).manifest
        with pytest.raises(SplitPolicyMismatchError, match="no exact"):
            classify_access(replace(manifest, split_policy_ref=None), policy)
        wrong_ref = SplitPolicyRef(
            adr=policy.identity.adr,
            policy_version=policy.identity.policy_version,
            policy_sha256="f" * 64,
        )
        with pytest.raises(SplitPolicyMismatchError, match="pins split policy"):
            classify_access(replace(manifest, split_policy_ref=wrong_ref), policy)

    def test_manifest_axis_must_match_policy(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        manifest = registry.resolve(ref).manifest
        with pytest.raises(SplitPolicyMismatchError, match="clock domain"):
            classify_access(
                replace(manifest, clock_domain=ClockDomain("another.domain")), policy
            )
        with pytest.raises(SplitPolicyMismatchError, match="time unit"):
            classify_access(replace(manifest, time_unit=TimeUnit.MILLISECONDS), policy)
        with pytest.raises(SplitPolicyMismatchError, match="calendar-time policy"):
            classify_access(replace(manifest, calendar_time_policy=None), policy)

    def test_generated_tier_one_range_must_be_policy_contained(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        manifest = registry.resolve(ref).manifest
        admitted = manifest.admitted_native_range
        widened = replace(
            manifest,
            admitted_native_range=replace(admitted, end=210),
            partitions=(replace(manifest.partitions[0], last_key=210),),
        )
        with pytest.raises(SplitPolicyMismatchError, match="outside policy range"):
            classify_access(widened, policy)


class TestAuthorization:
    def test_exact_resolution_failure_is_not_an_access_decision(
        self, tmp_path: Path
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        stale = replace(ref, manifest_sha256="f" * 64)
        writer = RecordingAuditWriter()

        with pytest.raises(DatasetRefMismatchError):
            authorize_access(
                registry=registry,
                request=AccessRequest(stale),
                policy=policy,
                context=_context(),
                audit_writer=writer,
            )

        assert writer.events == []

    def test_axis_failure_is_not_an_access_decision(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        document = _policy_document()
        document["clock_domain"] = "other.synth.wallclock"
        mismatched_path = tmp_path / "other-axis.json"
        mismatched_path.write_text(json.dumps(document))
        mismatched = read_split_policy(mismatched_path)
        writer = RecordingAuditWriter()

        with pytest.raises(SplitPolicyMismatchError, match="clock domain"):
            authorize_access(
                registry=registry,
                request=AccessRequest(ref),
                policy=mismatched,
                context=_context(),
                audit_writer=writer,
            )

        assert writer.events == []

    def test_policy_reference_failure_is_not_an_access_decision(
        self, tmp_path: Path
    ) -> None:
        path, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        path.write_text(json.dumps(_policy_document(), separators=(",", ":")))
        changed_bytes = read_split_policy(path)
        writer = RecordingAuditWriter()

        with pytest.raises(SplitPolicyMismatchError, match="pins split policy"):
            authorize_access(
                registry=registry,
                request=AccessRequest(ref),
                policy=changed_bytes,
                context=_context(),
                audit_writer=writer,
            )

        assert writer.events == []

    def test_normal_access_returns_unaudited_grant(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        writer = RecordingAuditWriter()
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(ref),
            policy=policy,
            context=_context(),
            audit_writer=writer,
        )
        assert grant.classification is AccessClassification.NORMAL
        assert grant.dataset_ref == ref
        assert grant.audit_event_id is None
        assert writer.events == []

    @pytest.mark.parametrize("extra", ["reason", "unlock"])
    def test_normal_access_rejects_extra_authorization_without_audit(
        self, tmp_path: Path, extra: str
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        request = (
            AccessRequest(ref, reason=_reason())
            if extra == "reason"
            else AccessRequest(ref, gated_unlock=GatedUnlock(ref.canonical_identity))
        )
        writer = RecordingAuditWriter()
        with pytest.raises(AccessDeniedError) as excinfo:
            authorize_access(
                registry=registry,
                request=request,
                policy=policy,
                context=_context(),
                audit_writer=writer,
            )
        assert excinfo.value.denial_code == "unexpected_authorization"
        assert writer.events == []

    def test_oos_requires_reason_and_audits_denial(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.OOS, first_key=310, last_key=390
        )
        writer = RecordingAuditWriter()
        with pytest.raises(AccessDeniedError) as excinfo:
            authorize_access(
                registry=registry,
                request=AccessRequest(ref),
                policy=policy,
                context=_context(),
                audit_writer=writer,
            )
        assert excinfo.value.denial_code == "reason_required"
        assert writer.events[0]["outcome"] == "denied"
        assert writer.events[0]["denial_code"] == "reason_required"

    def test_oos_reason_audits_then_grants_without_unlock(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.OOS, first_key=310, last_key=390
        )
        writer = RecordingAuditWriter()
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(ref, reason=_reason()),
            policy=policy,
            context=_context(),
            audit_writer=writer,
        )
        assert grant.classification is AccessClassification.OOS_EXPLICIT
        assert grant.audit_event_id == writer.events[0]["event_id"]
        assert writer.events[0]["outcome"] == "allowed"
        assert _event_section(writer.events[0], "authorization")["unlock_present"] is False

    def test_oos_rejects_extra_unlock_and_audits(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.OOS, first_key=310, last_key=390
        )
        writer = RecordingAuditWriter()
        with pytest.raises(AccessDeniedError) as excinfo:
            authorize_access(
                registry=registry,
                request=AccessRequest(
                    ref,
                    reason=_reason(),
                    gated_unlock=GatedUnlock(ref.canonical_identity),
                ),
                policy=policy,
                context=_context(),
                audit_writer=writer,
            )
        assert excinfo.value.denial_code == "unexpected_gated_unlock"
        assert writer.events[0]["outcome"] == "denied"

    @pytest.mark.parametrize(
        ("reason", "unlock", "denial_code"),
        [
            (False, False, "unlock_and_reason_required"),
            (True, False, "unlock_required"),
            (False, True, "reason_required"),
        ],
    )
    def test_fully_gated_partial_ceremonies_are_audited_denials(
        self,
        tmp_path: Path,
        reason: bool,
        unlock: bool,
        denial_code: str,
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        writer = RecordingAuditWriter()
        request = AccessRequest(
            ref,
            reason=_reason() if reason else None,
            gated_unlock=GatedUnlock(ref.canonical_identity) if unlock else None,
        )
        with pytest.raises(AccessDeniedError) as excinfo:
            authorize_access(
                registry=registry,
                request=request,
                policy=policy,
                context=_context(),
                audit_writer=writer,
            )
        assert excinfo.value.denial_code == denial_code
        assert writer.events[0]["outcome"] == "denied"
        assert writer.events[0]["classification"] == "fully_gated"

    def test_gated_unlock_for_different_identity_is_audited_denial(
        self, tmp_path: Path
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        wrong = DatasetIdentity(dataset_id="OTHER_V1", manifest_sha256="f" * 64)
        writer = RecordingAuditWriter()
        with pytest.raises(AccessDeniedError) as excinfo:
            authorize_access(
                registry=registry,
                request=AccessRequest(
                    ref, reason=_reason(), gated_unlock=GatedUnlock(wrong)
                ),
                policy=policy,
                context=_context(),
                audit_writer=writer,
            )
        assert excinfo.value.denial_code == "unlock_identity_mismatch"
        authorization = _event_section(writer.events[0], "authorization")
        assert authorization["unlock_target"] == {
            "dataset_id": "OTHER_V1",
            "manifest_sha256": "f" * 64,
        }

    def test_full_ceremony_audits_then_grants(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        context = _context()
        writer = RecordingAuditWriter()
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(
                ref,
                reason=_reason(),
                gated_unlock=GatedUnlock(ref.canonical_identity),
            ),
            policy=policy,
            context=context,
            audit_writer=writer,
        )
        assert grant.classification is AccessClassification.FULLY_GATED
        assert grant.session_id == context.session_id
        assert grant.audit_event_id == writer.events[0]["event_id"]
        assert writer.events[0]["outcome"] == "allowed"

    def test_audit_failure_returns_no_grant_and_never_calls_grant_factory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)

        def forbidden_issue(
            cls: type[AccessGrant],
            *,
            registered_manifest: RegisteredManifest,
            classification: AccessClassification,
            split_policy_identity: SplitPolicyIdentity,
            session_id: UUID,
            audit_event_id: str | None,
        ) -> AccessGrant:
            del (
                cls,
                registered_manifest,
                classification,
                split_policy_identity,
                session_id,
                audit_event_id,
            )
            pytest.fail("grant factory ran before a successful audit write")

        monkeypatch.setattr(AccessGrant, "_issue", classmethod(forbidden_issue))
        with pytest.raises(AuditWriteError, match="fault-injected"):
            authorize_access(
                registry=registry,
                request=AccessRequest(
                    ref,
                    reason=_reason(),
                    gated_unlock=GatedUnlock(ref.canonical_identity),
                ),
                policy=policy,
                context=_context(),
                audit_writer=FailingAuditWriter(),
            )

    def test_denial_audit_failure_is_chained_from_intended_denial(
        self, tmp_path: Path
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        with pytest.raises(AuditWriteError) as excinfo:
            authorize_access(
                registry=registry,
                request=AccessRequest(ref),
                policy=policy,
                context=_context(),
                audit_writer=FailingAuditWriter(),
            )
        assert isinstance(excinfo.value.__cause__, AccessDeniedError)
        assert excinfo.value.__cause__.denial_code == "unlock_and_reason_required"

    def test_event_schema_is_exact_and_json_native(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        context = _context()
        writer = RecordingAuditWriter()
        authorize_access(
            registry=registry,
            request=AccessRequest(
                ref,
                reason=_reason(),
                gated_unlock=GatedUnlock(ref.canonical_identity),
            ),
            policy=policy,
            context=context,
            audit_writer=writer,
        )
        event = writer.events[0]
        assert set(event) == {
            "schema_version",
            "event_id",
            "event_type",
            "event_time_utc",
            "outcome",
            "denial_code",
            "caller",
            "platform",
            "dataset_ref",
            "manifest_axis",
            "admitted_native_range",
            "split_policy",
            "classification",
            "classification_basis",
            "authorization",
        }
        assert event["schema_version"] == "1"
        assert event["event_type"] == "dataset_access_decision"
        assert cast("str", event["event_time_utc"]).endswith("Z")
        assert UUID(cast("str", event["event_id"])).version == 4
        assert event["denial_code"] is None
        assert _event_section(event, "caller") == {
            "session_id": str(context.session_id),
            "os_user": "researcher",
            "hostname": "research-vm",
            "pid": 1234,
        }
        assert _event_section(event, "platform") == {
            "git_commit": "a" * 40,
            "git_dirty": False,
        }
        assert _event_section(event, "dataset_ref") == {
            "dataset_id": ref.dataset_id,
            "manifest_sha256": ref.manifest_sha256,
            "role": "raw",
        }
        assert _event_section(event, "manifest_axis") == {
            "clock_domain": "mt5.synth-server.wallclock",
            "time_unit": "seconds",
        }
        assert _event_section(event, "split_policy") == {
            "adr": policy.identity.adr,
            "policy_version": policy.identity.policy_version,
            "policy_sha256": policy.identity.policy_sha256,
        }
        assert event["admitted_native_range"] == {
            "start": 100,
            "end": policy.lockbox_range.end,
        }
        assert _event_section(event, "authorization") == {
            "unlock_present": True,
            "unlock_target": {
                "dataset_id": ref.dataset_id,
                "manifest_sha256": ref.manifest_sha256,
            },
            "reason": {
                "code": "owner-review",
                "detail": "Approved synthetic access test",
            },
        }
        assert event["classification_basis"] == "range_exceeds_oos_boundary"
        json.dumps(event, allow_nan=False)

    def test_real_jsonl_writer_persists_the_event_returned_in_the_grant(
        self, tmp_path: Path
    ) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _raw_registry(tmp_path, policy)
        audit_path = tmp_path / "audit.jsonl"
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(
                ref,
                reason=_reason(),
                gated_unlock=GatedUnlock(ref.canonical_identity),
            ),
            policy=policy,
            context=_context(),
            audit_writer=JsonlAuditWriter(audit_path),
        )
        lines = audit_path.read_text().splitlines()
        assert len(lines) == 1
        persisted = cast("dict[str, object]", json.loads(lines[0]))
        assert persisted["event_id"] == grant.audit_event_id
        assert persisted["outcome"] == "allowed"


class TestGrantBinding:
    def test_ordinary_constructor_is_disabled(self) -> None:
        with pytest.raises(TypeError):
            AccessGrant()

    def test_grant_is_frozen(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(ref),
            policy=policy,
            context=_context(),
            audit_writer=RecordingAuditWriter(),
        )
        with pytest.raises(AttributeError):
            grant.audit_event_id = "replacement"  # type: ignore[misc]

    def test_grant_refuses_different_identity_or_policy(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(ref),
            policy=policy,
            context=_context(),
            audit_writer=RecordingAuditWriter(),
        )
        other_manifest = replace(
            grant.registered_manifest.manifest,
            dataset_id="DIFFERENT_DATASET_V1",
            manifest_sha256="f" * 64,
        )
        other_registered = replace(grant.registered_manifest, manifest=other_manifest)
        with pytest.raises(AccessGrantMismatchError, match="access grant is for"):
            grant.require_matches(
                registered_manifest=other_registered,
                split_policy_identity=policy.identity,
            )
        with pytest.raises(AccessGrantMismatchError, match="policy"):
            grant.require_matches(
                registered_manifest=grant.registered_manifest,
                split_policy_identity=replace(policy.identity, policy_sha256="e" * 64),
            )

    def test_grant_accepts_its_exact_manifest_and_policy(self, tmp_path: Path) -> None:
        _, policy = _write_policy(tmp_path)
        registry, ref = _view_registry(
            tmp_path, policy, role=ViewRole.TRAIN, first_key=110, last_key=190
        )
        grant = authorize_access(
            registry=registry,
            request=AccessRequest(ref),
            policy=policy,
            context=_context(),
            audit_writer=RecordingAuditWriter(),
        )
        grant.require_matches(
            registered_manifest=grant.registered_manifest,
            split_policy_identity=policy.identity,
        )
