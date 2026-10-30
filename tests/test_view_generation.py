"""Synthetic behavioral tests for Slice 1F Stage-A view generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest

import trading_research.data.views_generation as generation
from tests.load_fixtures import (
    GenerationCase,
    PartitionSpec,
    RecordingAuditWriter,
    make_generation_case,
    write_policy,
)
from trading_research.core.errors import AuditWriteError, MarketFrameError
from trading_research.core.identity import DatasetIdentity, DatasetRef, ViewRole
from trading_research.core.time import NativeTimeKey
from trading_research.data.gating import (
    AccessGrant,
    AccessReason,
    AccessRequest,
    GatedUnlock,
    authorize_access,
)
from trading_research.data.integrity import MarketFrameLoader
from trading_research.data.manifests import (
    ColumnPurpose,
    ManifestColumn,
    ManifestFormat,
    ViewManifestV11,
    read_manifest,
)
from trading_research.data.manifests.models import SplitPolicyRef
from trading_research.data.registry import DatasetRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from trading_research.core.frames import MarketFrame


_GENERATED_AT = "2026-08-09T12:34:56Z"
_GENERATOR_NAME = "slice-1f-stage-a-tests"
_GENERATOR_VERSION = "1.0.0"
_CAP_ENCODED = "synthetic-native-key-3999"
_GENERATED_ROLES = (
    ViewRole.SNAPSHOT,
    ViewRole.TRAIN,
    ViewRole.VALIDATION,
    ViewRole.OOS,
    ViewRole.LOCKBOX,
)
_TIER_ONE_ROLES = (
    ViewRole.TRAIN,
    ViewRole.VALIDATION,
    ViewRole.OOS,
    ViewRole.LOCKBOX,
)
_EXPECTED_COLUMNS = (
    ("mt5_time_raw", "int64", "time_key"),
    ("timestamp_mt5_encoded", "datetime64[ns]", "display_only"),
    ("open", "float64", "value"),
    ("high", "float64", "value"),
    ("low", "float64", "value"),
    ("close", "float64", "value"),
    ("tick_volume", "int64", "value"),
    ("spread", "int32", "value"),
    ("real_volume", "int64", "value"),
)


def _spec(
    case: GenerationCase,
    *,
    generated_at_utc: str = _GENERATED_AT,
    generator_name: str = _GENERATOR_NAME,
    generator_version: str = _GENERATOR_VERSION,
    platform_git_commit: str | None = None,
    policy_cap_encoded: str = _CAP_ENCODED,
) -> generation._BundleSpec:
    ids = case.generated_view_ids
    return generation._BundleSpec(
        source_ref=case.curated_ref,
        view_ids=generation._ViewIds(
            snapshot=ids[ViewRole.SNAPSHOT],
            train=ids[ViewRole.TRAIN],
            validation=ids[ViewRole.VALIDATION],
            oos=ids[ViewRole.OOS],
            lockbox=ids[ViewRole.LOCKBOX],
        ),
        views_root=case.views_root,
        generated_at_utc=generated_at_utc,
        generator=generation._GeneratorProvenance(
            name=generator_name,
            version=generator_version,
            platform_git_commit=(
                case.context.platform_git_commit
                if platform_git_commit is None
                else platform_git_commit
            ),
        ),
        policy_cap_encoded=policy_cap_encoded,
    )


def _run(
    case: GenerationCase,
    *,
    spec: generation._BundleSpec | None = None,
) -> tuple[generation._GenerationResult, AccessGrant, generation._PreparedBundle]:
    selected_spec = _spec(case) if spec is None else spec
    prepared = generation._prepare_view_bundle(
        registry=case.registry,
        policy=case.policy,
        spec=selected_spec,
    )
    grant = case.authorize_curated()
    result = generation._execute_prepared_bundle(
        prepared=prepared,
        loader=case.loader,
        grant=grant,
        context=case.context,
        audit_writer=case.audit,
    )
    return result, grant, prepared


def _wire(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_bytes()))


def _generated_ref(result: generation._GenerationResult, role: ViewRole) -> DatasetRef:
    artifact = result.artifacts[role]
    return DatasetRef(
        dataset_id=artifact.view_id,
        manifest_sha256=artifact.manifest_sha256,
        role=role,
    )


def _assert_one_keys_only_load(
    case: GenerationCase,
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[AccessGrant, dict[str, object]]]:
    calls: list[tuple[AccessGrant, dict[str, object]]] = []
    real_load = MarketFrameLoader.load

    def traced_load(
        self: MarketFrameLoader,
        grant: AccessGrant,
        **kwargs: object,
    ) -> MarketFrame:
        calls.append((grant, dict(kwargs)))
        frame = real_load(self, grant, **kwargs)  # type: ignore[arg-type]
        assert frame.keys.tolist() == [
            key for spec in case.partition_specs for key in spec.physical_keys
        ]
        assert frame.column_names == ()
        assert frame.schema == {}
        return frame

    monkeypatch.setattr(MarketFrameLoader, "load", traced_load)
    return calls


def test_fresh_generation_uses_one_complete_keys_only_load_and_orders_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    calls = _assert_one_keys_only_load(case, monkeypatch)
    order: list[str] = []
    real_append = RecordingAuditWriter.append
    real_allocate = generation._allocate_binding_event_id
    real_publish = generation._publish_manifest_bytes

    def traced_append(
        self: RecordingAuditWriter,
        event: Mapping[str, object],
    ) -> None:
        if self is case.audit:
            order.append(f"audit:{event['event_type']}")
        real_append(self, event)

    def traced_allocate(prepared: generation._PreparedBundle) -> str:
        order.append("allocate:snapshot_binding")
        return real_allocate(prepared)

    def traced_publish(path: Path, payload: bytes) -> bool:
        order.append(f"publish:{path.parent.name}")
        return real_publish(path, payload)

    # Preserve the load tracer installed above while adding its ordering signal.
    installed_load = MarketFrameLoader.load

    def ordered_load(
        self: MarketFrameLoader,
        grant: AccessGrant,
        **kwargs: object,
    ) -> MarketFrame:
        order.append("load:keys-only")
        return installed_load(self, grant, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(RecordingAuditWriter, "append", traced_append)
    monkeypatch.setattr(generation, "_allocate_binding_event_id", traced_allocate)
    monkeypatch.setattr(generation, "_publish_manifest_bytes", traced_publish)
    monkeypatch.setattr(MarketFrameLoader, "load", ordered_load)

    result, grant, prepared = _run(case)

    assert prepared.mode is generation._GenerationMode.FRESH
    assert result.mode is generation._GenerationMode.FRESH
    assert len(calls) == 1
    assert calls[0] == (grant, {"columns": ()})
    assert result.bound_key == 3_999
    snapshot_name = case.generated_view_ids[ViewRole.SNAPSHOT]
    assert order == [
        "audit:dataset_access_decision",
        "allocate:snapshot_binding",
        "load:keys-only",
        "audit:snapshot_binding",
        f"publish:{snapshot_name}",
        *(f"publish:{case.generated_view_ids[role]}" for role in _TIER_ONE_ROLES),
    ]


def test_all_five_manifests_are_strict_normalized_resolvable_and_complete(
    tmp_path: Path,
) -> None:
    case = make_generation_case(tmp_path)
    result, _, _ = _run(case)
    expected_ranges = {
        ViewRole.SNAPSHOT: (100, 3_999, 12),
        ViewRole.TRAIN: (100, 999, 3),
        ViewRole.VALIDATION: (1_000, 1_999, 3),
        ViewRole.OOS: (2_000, 2_999, 3),
        ViewRole.LOCKBOX: (3_000, 3_999, 3),
    }
    expected_defaults = {
        ViewRole.SNAPSHOT: False,
        ViewRole.TRAIN: True,
        ViewRole.VALIDATION: True,
        ViewRole.OOS: False,
        ViewRole.LOCKBOX: False,
    }
    snapshot_artifact = result.artifacts[ViewRole.SNAPSHOT]
    snapshot_identity = DatasetIdentity(
        snapshot_artifact.view_id,
        snapshot_artifact.manifest_sha256,
    )
    policy_ref = SplitPolicyRef(
        adr=case.policy.identity.adr,
        policy_version=case.policy.identity.policy_version,
        policy_sha256=case.policy.identity.policy_sha256,
    )

    for role in _GENERATED_ROLES:
        artifact = result.artifacts[role]
        assert artifact.created
        assert artifact.payload.endswith(b"\n")
        assert artifact.path.read_bytes() == artifact.payload
        assert hashlib.sha256(artifact.payload).hexdigest() == artifact.manifest_sha256
        strict = ViewManifestV11.model_validate_json(artifact.payload)
        normalized = read_manifest(artifact.path)
        assert strict.role == role.value
        assert normalized.dataset_id == case.generated_view_ids[role]
        assert normalized.role is role
        assert normalized.source_format is ManifestFormat.VIEW_MANIFEST_V1_1
        assert normalized.discovery_default is expected_defaults[role]
        assert (
            normalized.admitted_native_range.start,
            normalized.admitted_native_range.end,
            normalized.expected_row_count,
        ) == expected_ranges[role]
        assert normalized.partition_source == case.partition_source.identity
        assert normalized.split_policy_ref == policy_ref
        assert normalized.declared_parent is not None
        expected_parent = (
            case.curated_source.identity if role is ViewRole.SNAPSHOT else snapshot_identity
        )
        assert normalized.declared_parent.dataset_id == expected_parent.dataset_id
        assert normalized.declared_parent.manifest_sha256 == expected_parent.manifest_sha256
        assert (
            tuple(
                (column.name, column.dtype, column.purpose.value) for column in normalized.columns
            )
            == _EXPECTED_COLUMNS
        )
        assert normalized.symbol == case.curated_source.manifest.symbol
        assert normalized.timeframe == case.curated_source.manifest.timeframe
        assert normalized.adapter_id == case.curated_source.manifest.adapter_id

    discovered = case.rediscover_with_views()
    for role in _GENERATED_ROLES:
        assert discovered.resolve(_generated_ref(result, role)).manifest.role is role
    discovered_defaults = {
        (entry.manifest.dataset_id, entry.manifest.role) for entry in discovered.default_views()
    }
    assert discovered_defaults == {
        (case.generated_view_ids[ViewRole.TRAIN], ViewRole.TRAIN),
        (case.generated_view_ids[ViewRole.VALIDATION], ViewRole.VALIDATION),
    }


def test_straddling_files_retain_physical_identity_with_role_specific_spans(
    tmp_path: Path,
) -> None:
    case = make_generation_case(tmp_path)
    result, _, _ = _run(case)
    source_by_key = {
        partition.key: partition for partition in case.curated_source.manifest.partitions
    }

    train = read_manifest(result.artifacts[ViewRole.TRAIN].path).partitions
    validation = read_manifest(result.artifacts[ViewRole.VALIDATION].path).partitions
    oos = read_manifest(result.artifacts[ViewRole.OOS].path).partitions
    lockbox = read_manifest(result.artifacts[ViewRole.LOCKBOX].path).partitions
    snapshot = read_manifest(result.artifacts[ViewRole.SNAPSHOT].path).partitions

    assert [(part.key, part.first_key, part.last_key, part.rows) for part in train] == [
        ("wide-first", 100, 999, 3)
    ]
    assert [(part.key, part.first_key, part.last_key, part.rows) for part in validation] == [
        ("wide-first", 1_000, 1_999, 3)
    ]
    assert [(part.key, part.first_key, part.last_key, part.rows) for part in oos] == [
        ("wide-second", 2_000, 2_999, 3)
    ]
    assert [(part.key, part.first_key, part.last_key, part.rows) for part in lockbox] == [
        ("wide-second", 3_000, 3_999, 3)
    ]
    assert [(part.key, part.first_key, part.last_key, part.rows) for part in snapshot] == [
        ("wide-first", 100, 1_999, 6),
        ("wide-second", 2_000, 3_999, 6),
    ]
    for partition in (*train, *validation, *oos, *lockbox, *snapshot):
        physical = source_by_key[partition.key]
        assert partition.filename == physical.filename
        assert partition.sha256 == physical.sha256
        assert partition.byte_size == physical.byte_size


def test_curated_access_audit_failure_stops_before_grant_read_or_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    prepared = generation._prepare_view_bundle(
        registry=case.registry,
        policy=case.policy,
        spec=_spec(case),
    )

    def fail_access(
        self: RecordingAuditWriter,
        event: Mapping[str, object],
    ) -> NoReturn:
        assert self is case.audit
        assert event["event_type"] == "dataset_access_decision"
        raise AuditWriteError("injected CURATED access append failure")

    def forbidden_load(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("a failed authorization must not reach the loader")

    monkeypatch.setattr(RecordingAuditWriter, "append", fail_access)
    monkeypatch.setattr(MarketFrameLoader, "load", forbidden_load)
    with pytest.raises(AuditWriteError, match="CURATED access"):
        case.authorize_curated()

    assert prepared.mode is generation._GenerationMode.FRESH
    assert case.audit.events == []
    assert not tuple(case.views_root.rglob("manifest.json"))


def test_fresh_audit_hash_and_failure_before_snapshot_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = make_generation_case(tmp_path / "success")
    result, grant, _ = _run(success)

    assert [event["event_type"] for event in success.audit.events] == [
        "dataset_access_decision",
        "snapshot_binding",
    ]
    binding_event = success.audit.events[1]
    snapshot = result.artifacts[ViewRole.SNAPSHOT]
    assert binding_event["source_access_event_id"] == grant.audit_event_id
    assert binding_event["snapshot_view_id"] == snapshot.view_id
    assert binding_event["snapshot_manifest_sha256"] == snapshot.manifest_sha256
    assert (
        hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
        == binding_event["snapshot_manifest_sha256"]
    )
    assert binding_event["curated_source"] == {
        "dataset_id": success.curated_source.identity.dataset_id,
        "manifest_sha256": success.curated_source.identity.manifest_sha256,
    }
    assert binding_event["partition_source"] == {
        "dataset_id": success.partition_source.identity.dataset_id,
        "manifest_sha256": success.partition_source.identity.manifest_sha256,
    }
    assert binding_event["split_policy"] == {
        "adr": success.policy.identity.adr,
        "policy_version": success.policy.identity.policy_version,
        "policy_sha256": success.policy.identity.policy_sha256,
    }
    assert binding_event["policy_cap_key"] == 3_999
    assert binding_event["bound_key"] == 3_999
    assert binding_event["binding_procedure"] == "market-frame-loader-keys-only"
    assert binding_event["binding_procedure_version"] == "1"
    assert binding_event["caller"] == {
        "session_id": str(success.context.session_id),
        "os_user": success.context.os_user,
        "hostname": success.context.hostname,
        "pid": success.context.pid,
    }
    assert binding_event["platform"] == {
        "git_commit": success.context.platform_git_commit,
        "git_dirty": success.context.platform_git_dirty,
    }

    failure = make_generation_case(tmp_path / "failure")
    real_append = RecordingAuditWriter.append

    def fail_binding(
        self: RecordingAuditWriter,
        event: Mapping[str, object],
    ) -> None:
        if self is failure.audit and event["event_type"] == "snapshot_binding":
            raise AuditWriteError("injected snapshot-binding append failure")
        real_append(self, event)

    monkeypatch.setattr(RecordingAuditWriter, "append", fail_binding)
    prepared = generation._prepare_view_bundle(
        registry=failure.registry,
        policy=failure.policy,
        spec=_spec(failure),
    )
    failure_grant = failure.authorize_curated()
    with pytest.raises(AuditWriteError, match="injected"):
        generation._execute_prepared_bundle(
            prepared=prepared,
            loader=failure.loader,
            grant=failure_grant,
            context=failure.context,
            audit_writer=failure.audit,
        )
    assert [event["event_type"] for event in failure.audit.events] == ["dataset_access_decision"]
    assert not tuple(failure.views_root.rglob("manifest.json"))


def test_same_semantics_timestamp_and_provenance_are_byte_deterministic(
    tmp_path: Path,
) -> None:
    first = make_generation_case(tmp_path / "first")
    second = make_generation_case(tmp_path / "second")
    first_result, _, _ = _run(first)
    second_result, _, _ = _run(second)

    assert {
        role: (
            first_result.artifacts[role].payload,
            first_result.artifacts[role].manifest_sha256,
        )
        for role in _GENERATED_ROLES
    } == {
        role: (
            second_result.artifacts[role].payload,
            second_result.artifacts[role].manifest_sha256,
        )
        for role in _GENERATED_ROLES
    }
    snapshot_wire = _wire(first_result.artifacts[ViewRole.SNAPSHOT].path)
    event = first.audit.events[1]
    assert snapshot_wire["generated_at_utc"] == _GENERATED_AT
    assert snapshot_wire["generator"] == {
        "name": _GENERATOR_NAME,
        "version": _GENERATOR_VERSION,
        "platform_git_commit": first.context.platform_git_commit,
    }
    assert snapshot_wire["snapshot_binding"]["audit_event_id"] == event["event_id"]


def test_second_dataset_and_compatible_policy_use_same_generation_path(
    tmp_path: Path,
) -> None:
    ranges = {
        ViewRole.TRAIN: (10_000, 10_099),
        ViewRole.VALIDATION: (10_100, 10_199),
        ViewRole.OOS: (10_200, 10_299),
        ViewRole.LOCKBOX: (10_300, 10_399),
    }
    specs = (
        PartitionSpec(
            "cross-three",
            "continental-bars.parquet",
            (10_010, 10_099, 10_100, 10_150, 10_199, 10_200, 10_250),
            10_010,
            10_250,
            7,
        ),
        PartitionSpec(
            "tail",
            "terminal-bars.parquet",
            (10_299, 10_300, 10_350, 10_399, 10_400),
            10_299,
            10_400,
            5,
        ),
    )
    case = make_generation_case(
        tmp_path,
        specs=specs,
        policy_ranges=ranges,
        symbol="EUR_SYNTH",
        timeframe="H5",
        raw_dataset_id="SECOND_FAMILY_RAW",
        curated_dataset_id="SECOND_FAMILY_CURATED",
        trade_server="Other-Synth",
        policy_version="second-compatible-v1",
    )

    result, _, _ = _run(case, spec=_spec(case, policy_cap_encoded="second-cap-10399"))

    assert result.bound_key == 10_399
    assert set(result.artifacts) == set(_GENERATED_ROLES)
    assert [path.name for path in case.partition_paths] == [
        "continental-bars.parquet",
        "terminal-bars.parquet",
    ]
    expected = {
        ViewRole.SNAPSHOT: (10_010, 10_399, 11),
        ViewRole.TRAIN: (10_010, 10_099, 2),
        ViewRole.VALIDATION: (10_100, 10_199, 3),
        ViewRole.OOS: (10_200, 10_299, 3),
        ViewRole.LOCKBOX: (10_300, 10_399, 3),
    }
    for role, (first, last, rows) in expected.items():
        manifest = read_manifest(result.artifacts[role].path)
        assert manifest.symbol == "EUR_SYNTH"
        assert manifest.timeframe == "H5"
        assert manifest.dataset_id == case.generated_view_ids[role]
        assert (
            manifest.admitted_native_range.start,
            manifest.admitted_native_range.end,
            manifest.expected_row_count,
        ) == (first, last, rows)
    train_part = read_manifest(result.artifacts[ViewRole.TRAIN].path).partitions[0]
    validation_part = read_manifest(result.artifacts[ViewRole.VALIDATION].path).partitions[0]
    oos_parts = read_manifest(result.artifacts[ViewRole.OOS].path).partitions
    assert (train_part.key, validation_part.key, oos_parts[0].key) == (
        "cross-three",
        "cross-three",
        "cross-three",
    )
    assert (train_part.first_key, train_part.last_key) != (
        validation_part.first_key,
        validation_part.last_key,
    )


def test_partial_policy_coverage_succeeds_but_an_empty_role_refuses(
    tmp_path: Path,
) -> None:
    partial = make_generation_case(tmp_path / "partial")
    partial_result, _, _ = _run(partial)
    assert (
        read_manifest(partial_result.artifacts[ViewRole.TRAIN].path).admitted_native_range.start
        == 100
    )
    assert partial.policy.range_for(ViewRole.TRAIN).start == 0

    empty = make_generation_case(
        tmp_path / "empty",
        specs=(
            PartitionSpec(
                "gapped",
                "gapped-across-validation.parquet",
                (100, 999, 2_000, 2_999, 3_000, 3_999, 4_000),
                100,
                4_000,
                7,
            ),
        ),
    )
    prepared = generation._prepare_view_bundle(
        registry=empty.registry,
        policy=empty.policy,
        spec=_spec(empty),
    )
    grant = empty.authorize_curated()
    with pytest.raises(generation._ViewGenerationError, match=r"validation.*zero observed"):
        generation._execute_prepared_bundle(
            prepared=prepared,
            loader=empty.loader,
            grant=grant,
            context=empty.context,
            audit_writer=empty.audit,
        )
    assert [event["event_type"] for event in empty.audit.events] == ["dataset_access_decision"]
    assert not tuple(empty.views_root.rglob("manifest.json"))


def test_cap_need_not_be_observed_and_no_eligible_bound_refuses(tmp_path: Path) -> None:
    below_cap = make_generation_case(
        tmp_path / "below-cap",
        specs=(
            PartitionSpec(
                "all-roles",
                "below-cap.parquet",
                (100, 999, 1_000, 1_999, 2_000, 2_999, 3_000, 3_100),
                100,
                3_100,
                8,
            ),
        ),
    )
    result, _, _ = _run(below_cap)
    assert result.bound_key == 3_100

    above_cap = make_generation_case(
        tmp_path / "above-cap",
        specs=(
            PartitionSpec(
                "future",
                "future-only.parquet",
                (4_000, 4_100),
                4_000,
                4_100,
                2,
            ),
        ),
    )
    prepared = generation._prepare_view_bundle(
        registry=above_cap.registry,
        policy=above_cap.policy,
        spec=_spec(above_cap),
    )
    grant = above_cap.authorize_curated()
    with pytest.raises(generation._ViewGenerationError, match="no observed native key"):
        generation._execute_prepared_bundle(
            prepared=prepared,
            loader=above_cap.loader,
            grant=grant,
            context=above_cap.context,
            audit_writer=above_cap.audit,
        )


def test_strict_wire_validation_precedes_binding_audit_and_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    prepared = generation._prepare_view_bundle(
        registry=case.registry,
        policy=case.policy,
        spec=_spec(case),
    )
    grant = case.authorize_curated()
    real_document = generation._manifest_document

    def invalid_document(**kwargs: object) -> dict[str, object]:
        document = real_document(**kwargs)  # type: ignore[arg-type]
        document["unexpected_generation_field"] = True
        return document

    monkeypatch.setattr(generation, "_manifest_document", invalid_document)
    with pytest.raises(generation._ViewGenerationError, match=r"strict v1\.1"):
        generation._execute_prepared_bundle(
            prepared=prepared,
            loader=case.loader,
            grant=grant,
            context=case.context,
            audit_writer=case.audit,
        )
    assert [event["event_type"] for event in case.audit.events] == ["dataset_access_decision"]
    assert not tuple(case.views_root.rglob("manifest.json"))


def test_future_declared_adapter_column_flows_without_generator_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    future_column = ManifestColumn(
        name="future_metric",
        dtype=None,
        purpose=ColumnPurpose.VALUE,
    )
    future_manifest = replace(
        case.curated_source.manifest,
        columns=(*case.curated_source.manifest.columns, future_column),
    )
    future_source = replace(case.curated_source, manifest=future_manifest)
    adapter_declarations = {
        adapter_id: dict(declaration)
        for adapter_id, declaration in generation._GENERATION_DTYPES_BY_ADAPTER.items()
    }
    adapter_declarations[future_manifest.adapter_id][future_column.name] = "float32"
    monkeypatch.setattr(
        generation,
        "_GENERATION_DTYPES_BY_ADAPTER",
        MappingProxyType(
            {
                adapter_id: MappingProxyType(declaration)
                for adapter_id, declaration in adapter_declarations.items()
            }
        ),
    )

    generated = generation._generated_columns(future_source)

    assert generated[:-1] == generation._generated_columns(case.curated_source)
    assert generated[-1] == {
        "name": "future_metric",
        "dtype": "float32",
        "purpose": "value",
    }


def test_resume_anchor_preflight_then_one_new_access_and_scan_without_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    fresh, _, _ = _run(case)
    original = {role: fresh.artifacts[role].payload for role in _GENERATED_ROLES}
    for role in _TIER_ONE_ROLES:
        fresh.artifacts[role].path.unlink()
    calls = _assert_one_keys_only_load(case, monkeypatch)

    event_count = len(case.audit.events)
    prepared = generation._prepare_view_bundle(
        registry=case.registry,
        policy=case.policy,
        spec=_spec(case),
    )
    assert prepared.mode is generation._GenerationMode.RESUME
    assert len(case.audit.events) == event_count
    grant = case.authorize_curated()
    result = generation._execute_prepared_bundle(
        prepared=prepared,
        loader=case.loader,
        grant=grant,
        context=case.context,
        audit_writer=case.audit,
    )

    assert result.mode is generation._GenerationMode.RESUME
    assert len(calls) == 1
    assert calls[0] == (grant, {"columns": ()})
    assert [event["event_type"] for event in case.audit.events] == [
        "dataset_access_decision",
        "snapshot_binding",
        "dataset_access_decision",
    ]
    assert not result.artifacts[ViewRole.SNAPSHOT].created
    assert all(result.artifacts[role].created for role in _TIER_ONE_ROLES)
    assert {role: result.artifacts[role].path.read_bytes() for role in _GENERATED_ROLES} == original


def test_resume_partial_and_identical_children_are_immutable_no_ops(tmp_path: Path) -> None:
    partial = make_generation_case(tmp_path / "partial")
    fresh, _, _ = _run(partial)
    original = {role: fresh.artifacts[role].payload for role in _GENERATED_ROLES}
    missing = {ViewRole.VALIDATION, ViewRole.LOCKBOX}
    for role in missing:
        fresh.artifacts[role].path.unlink()
    resumed, _, _ = _run(partial)
    assert resumed.mode is generation._GenerationMode.RESUME
    assert {role for role in _TIER_ONE_ROLES if resumed.artifacts[role].created} == missing
    assert {
        role: resumed.artifacts[role].path.read_bytes() for role in _GENERATED_ROLES
    } == original

    identical = make_generation_case(tmp_path / "identical")
    first, _, _ = _run(identical)
    second, _, _ = _run(identical)
    assert second.mode is generation._GenerationMode.RESUME
    assert not any(artifact.created for artifact in second.artifacts.values())
    assert {role: second.artifacts[role].payload for role in _GENERATED_ROLES} == {
        role: first.artifacts[role].payload for role in _GENERATED_ROLES
    }
    assert [event["event_type"] for event in identical.audit.events].count("snapshot_binding") == 1


def test_resume_conflicting_child_refuses_without_overwrite_or_rebinding(
    tmp_path: Path,
) -> None:
    case = make_generation_case(tmp_path)
    fresh, _, _ = _run(case)
    conflict = fresh.artifacts[ViewRole.OOS].path
    conflicting_bytes = conflict.read_bytes() + b" "
    conflict.write_bytes(conflicting_bytes)

    prepared = generation._prepare_view_bundle(
        registry=case.registry,
        policy=case.policy,
        spec=_spec(case),
    )
    grant = case.authorize_curated()
    with pytest.raises(generation._ResumeRefusedError, match="oos manifest differs"):
        generation._execute_prepared_bundle(
            prepared=prepared,
            loader=case.loader,
            grant=grant,
            context=case.context,
            audit_writer=case.audit,
        )
    assert conflict.read_bytes() == conflicting_bytes
    assert [event["event_type"] for event in case.audit.events].count("snapshot_binding") == 1


@pytest.mark.parametrize(
    "mismatch",
    [
        "corrupt",
        "policy",
        "family",
        "generator-name",
        "generator-version",
        "generator-commit",
    ],
)
def test_resume_anchor_mismatch_refuses_before_authorization_or_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    anchor_case = make_generation_case(tmp_path / "anchor")
    fresh, _, _ = _run(anchor_case)
    audit_count = len(anchor_case.audit.events)
    registry = anchor_case.registry
    policy = anchor_case.policy
    spec = _spec(anchor_case)

    def forbidden_load(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("resume anchor preflight must precede source loading")

    monkeypatch.setattr(MarketFrameLoader, "load", forbidden_load)
    if mismatch == "corrupt":
        fresh.artifacts[ViewRole.SNAPSHOT].path.write_bytes(b"{not-json")
    elif mismatch == "policy":
        policy = write_policy(
            tmp_path / "mismatched-policy.json",
            alternate_bytes=True,
            policy_version="synthetic-v1",
        )
    elif mismatch == "family":
        other = make_generation_case(
            tmp_path / "other",
            symbol="OTHER_ASSET",
            raw_dataset_id="OTHER_RAW",
            curated_dataset_id="OTHER_CURATED",
            source_root=tmp_path / "other-source",
            views_root=anchor_case.views_root,
            generated_view_ids=anchor_case.generated_view_ids,
        )
        registry = other.registry
        policy = other.policy
        spec = _spec(other)
    elif mismatch == "generator-name":
        spec = _spec(anchor_case, generator_name="incompatible-generator")
    elif mismatch == "generator-version":
        spec = _spec(anchor_case, generator_version="incompatible-generator")
    else:
        spec = _spec(anchor_case, platform_git_commit="b" * 40)

    expected_audit = anchor_case.audit.events if mismatch != "family" else other.audit.events
    before = len(expected_audit)
    with pytest.raises(generation._ResumeRefusedError):
        generation._prepare_view_bundle(registry=registry, policy=policy, spec=spec)
    assert len(expected_audit) == before
    assert len(anchor_case.audit.events) == audit_count


def test_resume_rejects_semantically_edited_binding_event_id_before_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    fresh, _, _ = _run(case)
    anchor = fresh.artifacts[ViewRole.SNAPSHOT].path
    document = _wire(anchor)
    document["snapshot_binding"]["audit_event_id"] = "edited-binding-id"
    anchor.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def forbidden_load(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("binding preflight must precede authorization and loading")

    monkeypatch.setattr(MarketFrameLoader, "load", forbidden_load)
    audit_count = len(case.audit.events)
    with pytest.raises(generation._ResumeRefusedError, match="binding event id"):
        generation._prepare_view_bundle(
            registry=case.registry,
            policy=case.policy,
            spec=_spec(case),
        )
    assert len(case.audit.events) == audit_count


def test_generation_refuses_ambient_or_unsafe_output_locations(tmp_path: Path) -> None:
    case = make_generation_case(tmp_path)
    base = _spec(case)
    with pytest.raises(generation._ViewGenerationError, match="absolute path"):
        generation._BundleSpec(
            source_ref=base.source_ref,
            view_ids=base.view_ids,
            views_root=Path("relative-views"),
            generated_at_utc=base.generated_at_utc,
            generator=base.generator,
            policy_cap_encoded=base.policy_cap_encoded,
        )

    split_root_registry = DatasetRegistry.discover(
        [
            case.curated_source.manifest_path.parent,
            case.partition_source.manifest_path.parent,
        ]
    )
    overlapping = replace(base, views_root=case.partition_directory)
    with pytest.raises(generation._ViewGenerationError, match="partition-source"):
        generation._prepare_view_bundle(
            registry=split_root_registry,
            policy=case.policy,
            spec=overlapping,
        )

    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    snapshot_directory = case.views_root / case.generated_view_ids[ViewRole.SNAPSHOT]
    snapshot_directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(generation._ResumeRefusedError, match="real directory"):
        generation._prepare_view_bundle(
            registry=case.registry,
            policy=case.policy,
            spec=base,
        )


def test_generated_id_may_not_collide_with_source_registry(tmp_path: Path) -> None:
    case = make_generation_case(tmp_path)
    ids = case.generated_view_ids
    colliding = generation._ViewIds(
        snapshot=case.partition_source.manifest.dataset_id,
        train=ids[ViewRole.TRAIN],
        validation=ids[ViewRole.VALIDATION],
        oos=ids[ViewRole.OOS],
        lockbox=ids[ViewRole.LOCKBOX],
    )
    with pytest.raises(generation._ViewGenerationError, match="collides"):
        generation._prepare_view_bundle(
            registry=case.registry,
            policy=case.policy,
            spec=replace(_spec(case), view_ids=colliding),
        )


def test_views_root_symlink_swap_between_prepare_and_execute_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = make_generation_case(tmp_path)
    prepared = generation._prepare_view_bundle(
        registry=case.registry,
        policy=case.policy,
        spec=_spec(case),
    )
    original_root = case.views_root
    saved_root = (tmp_path / "saved-views-root").resolve()
    outside = (tmp_path / "outside-after-prepare").resolve()
    original_root.rename(saved_root)
    outside.mkdir()
    original_root.symlink_to(outside, target_is_directory=True)
    grant = case.authorize_curated()

    def forbidden_load(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("a replaced views root must refuse before source loading")

    monkeypatch.setattr(MarketFrameLoader, "load", forbidden_load)
    with pytest.raises(generation._ViewGenerationError, match="must not traverse symlinks"):
        generation._execute_prepared_bundle(
            prepared=prepared,
            loader=case.loader,
            grant=grant,
            context=case.context,
            audit_writer=case.audit,
        )
    assert [event["event_type"] for event in case.audit.events] == [
        "dataset_access_decision"
    ]
    assert not tuple(outside.rglob("manifest.json"))


def _authorize_generated(
    *,
    case: GenerationCase,
    registry: DatasetRegistry,
    ref: DatasetRef,
    audit: RecordingAuditWriter,
) -> AccessGrant:
    if ref.role in {ViewRole.TRAIN, ViewRole.VALIDATION}:
        request = AccessRequest(dataset_ref=ref)
    elif ref.role is ViewRole.OOS:
        request = AccessRequest(
            dataset_ref=ref,
            reason=AccessReason(
                code="post-cap-proof",
                detail="Synthetic generated-view post-cap integration proof",
            ),
        )
    else:
        request = AccessRequest(
            dataset_ref=ref,
            reason=AccessReason(
                code="post-cap-proof",
                detail="Synthetic generated-view post-cap integration proof",
            ),
            gated_unlock=GatedUnlock(target=ref.canonical_identity),
        )
    return authorize_access(
        registry=registry,
        request=request,
        policy=case.policy,
        context=case.context,
        audit_writer=audit,
    )


def test_generated_views_hide_physical_post_cap_rows_through_registry_gate_and_loader(
    tmp_path: Path,
) -> None:
    case = make_generation_case(tmp_path)
    result, _, _ = _run(case)
    assert max(key for spec in case.partition_specs for key in spec.physical_keys) == 4_001
    registry = case.rediscover_with_views()
    loader = MarketFrameLoader(registry=registry, split_policy=case.policy)
    audit = RecordingAuditWriter()
    expected = {
        ViewRole.SNAPSHOT: (100, 3_999),
        ViewRole.TRAIN: (100, 999),
        ViewRole.VALIDATION: (1_000, 1_999),
        ViewRole.OOS: (2_000, 2_999),
        ViewRole.LOCKBOX: (3_000, 3_999),
    }

    for role, (first, last) in expected.items():
        ref = _generated_ref(result, role)
        grant = _authorize_generated(case=case, registry=registry, ref=ref, audit=audit)
        frame = loader.load(grant, columns=())
        assert (int(frame.keys[0]), int(frame.keys[-1])) == (first, last)
        assert all(int(key) <= last for key in frame.keys)
        outside = NativeTimeKey(
            value=last + 1,
            unit=frame.unit,
            clock_domain=frame.clock_domain,
        )
        with pytest.raises(MarketFrameError, match="outside target admission"):
            loader.load(grant, columns=(), hi=outside)
