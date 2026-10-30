"""What the manifest readers accept, refuse, and normalize.

Every test here is metadata-only: no Parquet file is opened, and the real
dataset is read for its manifests alone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.manifest_fixtures import (
    DIGEST_A,
    DIGEST_POLICY,
    curated_manifest,
    raw_manifest,
    v11_manifest,
    write_manifest,
)
from trading_research.core import (
    CalendarTimePolicy,
    ClockDomain,
    MalformedManifestError,
    TimeUnit,
    UnsupportedManifestFormatError,
    ViewRole,
)
from trading_research.data.manifests import (
    ColumnPurpose,
    ManifestFormat,
    NativeKeyRange,
    SplitPolicyRef,
    read_manifest,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
DATASET_ROOT = Path("/path/to/research-data")
RAW_MANIFEST = DATASET_ROOT / "XAUUSD_S_M1_MT5_V1" / "manifest.json"
CURATED_MANIFEST = DATASET_ROOT / "XAUUSD_S_M1_CURATED_V1" / "manifest.json"

real_dataset = pytest.mark.skipif(
    not RAW_MANIFEST.exists(), reason="real dataset root not present on this machine"
)


class TestFormatSelection:
    def test_an_unrecognised_format_is_refused_rather_than_guessed(
        self, tmp_path: Path
    ) -> None:
        path = write_manifest(tmp_path / "d", {"something": "else"})
        with pytest.raises(UnsupportedManifestFormatError, match="no recognised format marker"):
            read_manifest(path)

    def test_an_unsupported_v11_version_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        document["manifest_format_version"] = "1.2"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(UnsupportedManifestFormatError, match=r"1\.2"):
            read_manifest(path)

    def test_invalid_json_is_refused(self, tmp_path: Path) -> None:
        directory = tmp_path / "d"
        directory.mkdir()
        path = directory / "manifest.json"
        path.write_text("{not json")
        with pytest.raises(MalformedManifestError, match="not valid JSON"):
            read_manifest(path)

    def test_a_non_object_document_is_refused(self, tmp_path: Path) -> None:
        directory = tmp_path / "d"
        directory.mkdir()
        path = directory / "manifest.json"
        path.write_text("[1, 2, 3]")
        with pytest.raises(MalformedManifestError, match="must be a JSON object"):
            read_manifest(path)


class TestDigestIsOverExactBytes:
    def test_the_digest_matches_the_file_bytes(self, tmp_path: Path) -> None:
        """Not a re-serialization: formatting must not change identity."""
        path = write_manifest(tmp_path / "d", v11_manifest())
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        assert read_manifest(path).manifest_sha256 == expected

    def test_the_same_bytes_at_another_path_have_the_same_identity(
        self, tmp_path: Path
    ) -> None:
        document = v11_manifest()
        first = write_manifest(tmp_path / "one", document)
        second = write_manifest(tmp_path / "two", document)
        assert read_manifest(first).identity == read_manifest(second).identity

    def test_reformatting_the_same_content_changes_identity(self, tmp_path: Path) -> None:
        """The digest pins bytes, not parsed content — deliberately strict."""
        directory = tmp_path / "d"
        directory.mkdir()
        path = directory / "manifest.json"
        path.write_text(json.dumps(v11_manifest(), indent=4))
        compact = write_manifest(tmp_path / "e", v11_manifest())
        assert read_manifest(path).manifest_sha256 != read_manifest(compact).manifest_sha256


class TestViewManifestV11:
    def test_a_valid_manifest_normalizes(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        assert manifest.dataset_id == "SYNTH_TRAIN_V1"
        assert manifest.role is ViewRole.TRAIN
        assert manifest.source_format is ManifestFormat.VIEW_MANIFEST_V1_1
        assert manifest.discovery_default is True
        assert manifest.time_unit is TimeUnit.SECONDS
        assert manifest.calendar_time_policy is CalendarTimePolicy.NATIVE_WALLCLOCK_V1
        assert manifest.expected_row_count == 1_000

    def test_columns_carry_dtype_and_purpose(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        assert manifest.time_key_column == "native_key"
        assert manifest.frame_columns == ("native_key", "close")
        assert [c.dtype for c in manifest.columns] == ["int64", "float64", "string"]

    def test_a_pinned_parent_digest_is_preserved(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        assert manifest.declared_parent is not None
        assert manifest.declared_parent.manifest_sha256 == DIGEST_A

    def test_the_exact_split_policy_reference_is_preserved(self, tmp_path: Path) -> None:
        document = v11_manifest(policy_sha256=DIGEST_POLICY.upper())
        manifest = read_manifest(write_manifest(tmp_path / "d", document))
        assert manifest.split_policy_ref == SplitPolicyRef(
            adr="ADR-0012",
            policy_version="v1",
            policy_sha256=DIGEST_POLICY,
        )

    @pytest.mark.parametrize(
        "field",
        [
            "view_id",
            "role",
            "discovery_default",
            "admitted_native_range",
            "parent",
            "partition_source",
            "split_policy_ref",
            "columns",
        ],
    )
    def test_a_missing_required_field_is_refused(self, tmp_path: Path, field: str) -> None:
        document = v11_manifest()
        del document[field]
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_an_unknown_field_is_refused(self, tmp_path: Path) -> None:
        """The platform writes these files, so a surprise field is a bug, not data."""
        document = v11_manifest()
        document["extra_field"] = "surprise"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_a_malformed_sha256_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        document["admitted_partitions"][0]["sha256"] = "nope"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_a_missing_split_policy_digest_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        del document["split_policy_ref"]["policy_sha256"]
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_a_malformed_split_policy_digest_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest(policy_sha256="not-a-digest")
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_an_inverted_admitted_range_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        document["admitted_native_range"] = {"start": 500, "end": 100}
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_a_null_admitted_range_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        document["admitted_native_range"] = None
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_a_range_disagreeing_with_observed_endpoints_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The range records observations; a partition past its end is a lie."""
        document = v11_manifest()
        document["admitted_partitions"][0]["last_key"] = 9_999
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="observed admitted endpoints"):
            read_manifest(path)

    @pytest.mark.parametrize("role", ["oos", "raw", "curated", "snapshot", "lockbox"])
    def test_a_gated_role_may_not_be_default_discoverable(
        self, tmp_path: Path, role: str
    ) -> None:
        """A generator bug here would disable the platform's first defence silently."""
        document = v11_manifest(role=role, discovery_default=True)
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="must not be discovery_default"):
            read_manifest(path)

    @pytest.mark.parametrize("role", ["train", "validation"])
    def test_working_views_may_be_default_discoverable(
        self, tmp_path: Path, role: str
    ) -> None:
        document = v11_manifest(role=role, discovery_default=True)
        assert read_manifest(write_manifest(tmp_path / "d", document)).discovery_default

    def test_a_manifest_with_no_time_key_column_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        document["columns"][0]["purpose"] = "value"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="exactly one time-key column"):
            read_manifest(path)

    def test_evaluation_bounds_are_not_manifest_fields(self, tmp_path: Path) -> None:
        """context/eval bounds describe one request, not the view itself."""
        for field in ("context_start", "eval_start", "eval_end"):
            document = v11_manifest()
            document[field] = 1_000
            path = write_manifest(tmp_path / f"d_{field}", document)
            with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
                read_manifest(path)


class TestSplitPolicyReferenceCompatibility:
    def test_legacy_raw_has_no_split_policy_reference(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "raw", raw_manifest()))
        assert manifest.split_policy_ref is None

    def test_legacy_curated_has_no_split_policy_reference(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "curated", curated_manifest()))
        assert manifest.split_policy_ref is None

    def test_normalized_reference_refuses_a_malformed_digest(self) -> None:
        with pytest.raises(MalformedManifestError, match="exactly 64 hexadecimal"):
            SplitPolicyRef(
                adr="ADR-0012", policy_version="v1", policy_sha256="not-a-digest"
            )


class TestRequiredAdmittedNativeRange:
    @pytest.mark.parametrize(
        "document",
        [raw_manifest(), curated_manifest(), v11_manifest()],
        ids=["legacy-raw", "legacy-curated", "view-v1.1"],
    )
    def test_every_current_reader_produces_a_concrete_range(
        self,
        tmp_path: Path,
        document: dict[str, Any],
    ) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", document))
        assert isinstance(manifest.admitted_native_range, NativeKeyRange)

    def test_normalized_manifest_runtime_construction_refuses_none(
        self,
        tmp_path: Path,
    ) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        with pytest.raises(MalformedManifestError, match="concrete NativeKeyRange"):
            replace(manifest, admitted_native_range=None)  # type: ignore[arg-type]

    @real_dataset
    def test_real_legacy_readers_produce_concrete_ranges(self) -> None:
        for path in (RAW_MANIFEST, CURATED_MANIFEST):
            assert isinstance(read_manifest(path).admitted_native_range, NativeKeyRange)


@real_dataset
class TestRealRawManifest:
    def test_it_parses_without_opening_a_partition(self) -> None:
        manifest = read_manifest(RAW_MANIFEST)
        assert manifest.dataset_id == "XAUUSD_S_M1_MT5_V1"
        assert manifest.role is ViewRole.RAW
        assert manifest.source_format is ManifestFormat.MT5_RAW_V1
        assert manifest.expected_row_count == 2_428_487
        assert len(manifest.partitions) == 16

    def test_the_raw_dataset_is_never_default_discoverable(self) -> None:
        assert read_manifest(RAW_MANIFEST).discovery_default is False

    def test_the_clock_domain_is_derived_from_the_declared_server(self) -> None:
        manifest = read_manifest(RAW_MANIFEST)
        assert manifest.clock_domain == ClockDomain("mt5.justmarkets-demo3.wallclock")
        assert manifest.time_unit is TimeUnit.SECONDS

    def test_it_declares_no_calendar_policy(self) -> None:
        """The legacy exporter states no policy; inventing one would be a claim."""
        assert read_manifest(RAW_MANIFEST).calendar_time_policy is None

    def test_partition_metadata_is_retained_for_later_slices(self) -> None:
        first = read_manifest(RAW_MANIFEST).partitions[0]
        assert first.filename.endswith(".parquet")
        assert "/" not in first.filename
        assert len(first.sha256) == 64
        assert first.byte_size is not None


@real_dataset
class TestRealCuratedManifest:
    def test_it_parses_without_opening_a_partition(self) -> None:
        manifest = read_manifest(CURATED_MANIFEST)
        assert manifest.dataset_id == "XAUUSD_S_M1_CURATED_V1"
        assert manifest.role is ViewRole.CURATED
        assert manifest.source_format is ManifestFormat.CURATED_VIEW_V1
        assert manifest.expected_row_count == 2_333_614
        assert len(manifest.partitions) == 7

    def test_the_curated_dataset_is_never_default_discoverable(self) -> None:
        assert read_manifest(CURATED_MANIFEST).discovery_default is False

    def test_its_declared_parent_pins_no_digest(self) -> None:
        """The legacy format names a parent without pinning it; say so honestly."""
        parent = read_manifest(CURATED_MANIFEST).declared_parent
        assert parent is not None
        assert parent.dataset_id == "XAUUSD_S_M1_MT5_V1"
        assert parent.manifest_sha256 is None
        assert parent.relative_path == "../XAUUSD_S_M1_MT5_V1"

    def test_partition_filenames_are_bare_and_the_parent_path_is_provenance(self) -> None:
        """Bare filenames resolve under the declared parent.

        The curated view stores no data; its source-declared parent path
        survives as provenance, never as identity.
        """
        manifest = read_manifest(CURATED_MANIFEST)
        assert all(
            p.filename.endswith(".parquet") and "/" not in p.filename
            for p in manifest.partitions
        )
        assert manifest.declared_parent is not None
        assert manifest.declared_parent.relative_path == "../XAUUSD_S_M1_MT5_V1"
        assert manifest.partition_source is None

    def test_the_decoded_timestamp_column_is_display_only(self) -> None:
        """Owner decision: it never enters a frame, so it cannot be mistaken for civil time."""
        manifest = read_manifest(CURATED_MANIFEST)
        decoded = next(c for c in manifest.columns if c.name == "timestamp_mt5_encoded")
        assert decoded.purpose is ColumnPurpose.DISPLAY_ONLY
        assert "timestamp_mt5_encoded" not in manifest.frame_columns
        assert manifest.time_key_column == "mt5_time_raw"

    def test_excluded_years_are_absent_from_admitted_partitions(self) -> None:
        keys = {p.key for p in read_manifest(CURATED_MANIFEST).partitions}
        assert keys == {"2020", "2021", "2022", "2023", "2024", "2025", "2026"}
        assert not keys & {"2011", "2015", "2019"}


class TestLegacyAdapterRefusals:
    def _raw_document(self) -> dict[str, Any]:
        """The real raw manifest, as a mutable document to corrupt per test."""
        document: dict[str, Any] = json.loads(RAW_MANIFEST.read_bytes())
        return document

    @real_dataset
    def test_an_unsupported_builder_version_is_refused(self, tmp_path: Path) -> None:
        document = self._raw_document()
        document["dataset_builder_version"] = "9.9.9"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(UnsupportedManifestFormatError, match=r"9\.9\.9"):
            read_manifest(path)

    @real_dataset
    def test_an_unexpected_timestamp_policy_is_refused(self, tmp_path: Path) -> None:
        """A different policy means the integer may have been rewritten."""
        document = self._raw_document()
        document["timestamp_policy"] = "normalized_utc"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="timestamp_policy"):
            read_manifest(path)

    @real_dataset
    def test_an_unexpected_clock_classification_is_refused(self, tmp_path: Path) -> None:
        document = self._raw_document()
        document["terminal_time_model"]["classification"] = "TRUE_UTC"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="classifies its clock"):
            read_manifest(path)


class TestCp1SchemaCorrections:
    """The CP-1 owner-approved v1.1 corrections, each provable at the parser.

    The synthetic physical span runs past the cap while the view admits only
    up to its observed bound key.
    """

    LOCKBOX_POLICY_START = 10_000
    FIRST_OBSERVED = 10_100
    POLICY_CAP = 20_000
    BOUND = 19_940
    # The physical file continues past the cap — deliberately
    # absent from the manifest: the schema describes admitted rows only.

    def _row_bounded(self, role: str) -> dict[str, Any]:
        document = v11_manifest(
            view_id=f"SYNTH_{role.upper()}_V1",
            role=role,
            discovery_default=False,
            first_key=self.FIRST_OBSERVED,
            last_key=self.BOUND,
            rows=195_000,
        )
        document["snapshot_binding"] = {
            "policy_cap_encoded": "synthetic-native-cap",
            "policy_cap_key": self.POLICY_CAP,
            "bound_key": self.BOUND,
            "method": "keys-only scan",
            "audit_event_id": "01SYNTHETICAUDITEVENT0000",
        }
        return document

    # -- corrections 1 and 2: removed fields stay removed ------------------

    def test_gated_is_rejected_as_an_unknown_field(self, tmp_path: Path) -> None:
        """Authorization truth lives in the split policy, never in a manifest."""
        document = v11_manifest()
        document["gated"] = False
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_parent_relative_path_is_rejected(self, tmp_path: Path) -> None:
        """Lineage is identity, so a stored path is refused.

        A path inside the hashed bytes would make a purely physical
        relocation change the child's canonical identity.
        """
        document = v11_manifest()
        document["parent"]["relative_path"] = "../parent"
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_v11_parent_accepts_identity_only(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        assert manifest.declared_parent is not None
        assert manifest.declared_parent.relative_path is None
        assert manifest.declared_parent.manifest_sha256 == DIGEST_A

    # -- correction 4: bare filenames and identity-based source ------------

    def test_partition_relative_path_is_rejected(self, tmp_path: Path) -> None:
        document = v11_manifest()
        partition = dict(document["admitted_partitions"][0])
        partition["relative_path"] = "p0.parquet"
        document["admitted_partitions"] = [partition]
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_partition_admitted_native_range_is_rejected(self, tmp_path: Path) -> None:
        """Row-bounding is expressed by the admitted first/last keys now."""
        document = v11_manifest()
        partition = dict(document["admitted_partitions"][0])
        partition["admitted_native_range"] = {"start": 1_000, "end": 1_999}
        document["admitted_partitions"] = [partition]
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    @pytest.mark.parametrize(
        "filename",
        [
            "a/b.parquet",
            "..\\b.parquet",
            "../b.parquet",
            "..",
            "/abs/b.parquet",
            "~home.parquet",
        ],
    )
    def test_path_bearing_filenames_are_rejected(
        self, tmp_path: Path, filename: str
    ) -> None:
        document = v11_manifest()
        document["admitted_partitions"][0]["filename"] = filename
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match=r"failed 1\.1 validation"):
            read_manifest(path)

    def test_partition_source_is_normalized_into_an_identity(self, tmp_path: Path) -> None:
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        assert manifest.partition_source is not None
        assert manifest.partition_source.dataset_id == "SYNTH_PARENT_V1"
        assert manifest.partition_source.manifest_sha256 == DIGEST_A

    # -- correction 5: one source of truth for row counts ------------------

    def test_a_row_count_mismatch_is_refused(self, tmp_path: Path) -> None:
        document = v11_manifest()
        document["row_count_expected"] = 999
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="sum of admitted partition rows"):
            read_manifest(path)

    # -- correction 3 + owner clarification: admitted-span semantics -------

    @pytest.mark.parametrize("role", ["snapshot", "lockbox"])
    def test_a_row_bounded_view_is_expressible(self, tmp_path: Path, role: str) -> None:
        """The blocker CP-1 found: row-bounded views must parse.

        SNAPSHOT and LOCKBOX end before the physical end of the 2026
        partition.
        """
        path = write_manifest(tmp_path / "d", self._row_bounded(role))
        manifest = read_manifest(path)
        admitted_range = manifest.admitted_native_range
        assert admitted_range is not None
        assert admitted_range.start == self.FIRST_OBSERVED
        assert admitted_range.end == self.BOUND
        assert manifest.expected_row_count == 195_000

    def test_the_physical_file_may_extend_past_the_admitted_span(
        self, tmp_path: Path
    ) -> None:
        """Nothing constrains the source file's physical end.

        The manifest speaks only about admitted rows.
        """
        manifest = read_manifest(write_manifest(tmp_path / "d", self._row_bounded("lockbox")))
        partition = manifest.partitions[0]
        assert partition.last_key == self.BOUND  # not the file's last key

    def test_policy_start_may_precede_the_first_observed_key(
        self, tmp_path: Path
    ) -> None:
        """Policy says Jan 1; the first admitted market row is Jan 2.

        The manifest records the observation; the intent lives in the
        policy reference and binding provenance.
        """
        manifest = read_manifest(write_manifest(tmp_path / "d", self._row_bounded("lockbox")))
        admitted_range = manifest.admitted_native_range
        assert admitted_range is not None
        assert admitted_range.start > self.LOCKBOX_POLICY_START

    def test_a_calendar_boundary_as_range_start_is_refused(self, tmp_path: Path) -> None:
        """A calendar boundary is not an observation.

        Writing the policy boundary into the observed range is the exact
        conflation the owner clarification forbids.
        """
        document = self._row_bounded("lockbox")
        document["admitted_native_range"]["start"] = self.LOCKBOX_POLICY_START
        path = write_manifest(tmp_path / "d", document)
        with pytest.raises(MalformedManifestError, match="observed admitted endpoints"):
            read_manifest(path)

    def test_the_schema_carries_no_location_field_anywhere(self, tmp_path: Path) -> None:
        """No lineage or partition field may name a machine-local location."""
        manifest = read_manifest(write_manifest(tmp_path / "d", v11_manifest()))
        for partition in manifest.partitions:
            assert not hasattr(partition, "relative_path")
        assert not hasattr(manifest.partition_source, "relative_path")
