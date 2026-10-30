"""Invariants of dataset and view identity.

The property these tests exist to protect: canonical research identity is
exactly ``(dataset_id, manifest_sha256)``. Not a path, not a role, not the
machine it was read on.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from trading_research.core import (
    ContractViolationError,
    DatasetIdentity,
    DatasetRef,
    ViewRole,
)

DIGEST = "ba2596ee285ed764ca5e5f0ed25ad66006d435443d6010586f909cf98c39c9d0"
OTHER_DIGEST = "499a04e227ecab8034f1dc71255ca1d839a886f09bc4204ed434441952fcaaf2"


def _ref(
    dataset_id: str = "XAUUSD_S_M1_CURATED_V1",
    digest: str = DIGEST,
    role: ViewRole = ViewRole.CURATED,
) -> DatasetRef:
    return DatasetRef(dataset_id=dataset_id, manifest_sha256=digest, role=role)


class TestViewRole:
    def test_the_seven_accepted_roles_exist(self) -> None:
        assert {r.value for r in ViewRole} == {
            "raw",
            "curated",
            "snapshot",
            "train",
            "validation",
            "oos",
            "lockbox",
        }


class TestDatasetRefConstruction:
    def test_a_reference_carries_identity_and_role(self) -> None:
        ref = _ref()
        assert ref.dataset_id == "XAUUSD_S_M1_CURATED_V1"
        assert ref.manifest_sha256 == DIGEST
        assert ref.role is ViewRole.CURATED

    def test_references_are_immutable(self) -> None:
        ref = _ref()
        with pytest.raises(AttributeError):
            ref.dataset_id = "other"  # type: ignore[misc]

    def test_references_are_hashable(self) -> None:
        assert len({_ref(), _ref(), _ref(digest=OTHER_DIGEST)}) == 2

    @pytest.mark.parametrize(
        "dataset_id",
        ["", " ", "has space", ".leading", "trailing.", "-leading", "\ttab"],
    )
    def test_empty_or_malformed_identifiers_are_refused(self, dataset_id: str) -> None:
        with pytest.raises(ContractViolationError, match="dataset_id"):
            _ref(dataset_id=dataset_id)

    def test_over_long_identifiers_are_refused(self) -> None:
        with pytest.raises(ContractViolationError, match="exceeds"):
            _ref(dataset_id="A" * 129)

    def test_non_string_identifiers_are_refused(self) -> None:
        with pytest.raises(ContractViolationError, match="must be a string"):
            _ref(dataset_id=42)  # type: ignore[arg-type]

    def test_a_role_must_be_a_view_role(self) -> None:
        with pytest.raises(ContractViolationError, match="must be a ViewRole"):
            _ref(role="train")  # type: ignore[arg-type]


class TestManifestDigest:
    def test_a_canonical_lowercase_digest_is_accepted(self) -> None:
        assert _ref(digest=DIGEST).manifest_sha256 == DIGEST

    def test_uppercase_digests_are_normalized_rather_than_rejected(self) -> None:
        """Both spellings denote one manifest, so identity must not depend on casing."""
        assert _ref(digest=DIGEST.upper()).manifest_sha256 == DIGEST
        assert _ref(digest=DIGEST.upper()) == _ref(digest=DIGEST)
        assert (
            _ref(digest=DIGEST.upper()).canonical_identity == _ref(digest=DIGEST).canonical_identity
        )

    @pytest.mark.parametrize(
        "digest",
        [
            "",
            "abc",
            DIGEST[:-1],
            DIGEST + "a",
            "g" * 64,
            f" {DIGEST}",
            f"sha256:{DIGEST}",
        ],
    )
    def test_malformed_digests_are_refused(self, digest: str) -> None:
        with pytest.raises(ContractViolationError, match="manifest_sha256"):
            _ref(digest=digest)

    def test_non_string_digests_are_refused(self) -> None:
        with pytest.raises(ContractViolationError, match="must be a string"):
            _ref(digest=42)  # type: ignore[arg-type]


class TestCanonicalIdentity:
    def test_canonical_identity_is_exactly_two_components(self) -> None:
        """Adding a field here is an architectural change, not a convenience."""
        assert [f.name for f in fields(DatasetIdentity)] == ["dataset_id", "manifest_sha256"]

    def test_role_is_absent_from_canonical_identity(self) -> None:
        assert "role" not in {f.name for f in fields(DatasetIdentity)}
        assert not hasattr(_ref().canonical_identity, "role")

    def test_role_remains_available_on_the_reference(self) -> None:
        assert _ref(role=ViewRole.TRAIN).role is ViewRole.TRAIN

    def test_the_same_manifest_under_different_roles_shares_one_identity(self) -> None:
        """Role changes the manifest, hence the digest — so a shared digest means one identity."""
        train = _ref(role=ViewRole.TRAIN)
        lockbox = _ref(role=ViewRole.LOCKBOX)
        assert train.canonical_identity == lockbox.canonical_identity

    def test_but_those_references_remain_distinct_objects(self) -> None:
        """At most one role is correct; collapsing them under == would hide the disagreement.

        The check that a ref's role matches the role its manifest declares
        requires reading the manifest, so it belongs to Slice 1B.
        """
        train = _ref(role=ViewRole.TRAIN)
        lockbox = _ref(role=ViewRole.LOCKBOX)
        assert train != lockbox
        assert len({train, lockbox}) == 2

    def test_identity_differs_when_the_manifest_differs(self) -> None:
        assert _ref().canonical_identity != _ref(digest=OTHER_DIGEST).canonical_identity

    def test_identity_differs_when_the_dataset_differs(self) -> None:
        assert _ref().canonical_identity != _ref(dataset_id="OTHER_V1").canonical_identity

    def test_identities_are_hashable_and_deduplicate(self) -> None:
        assert len({_ref().canonical_identity, _ref(role=ViewRole.TRAIN).canonical_identity}) == 1

    def test_identity_validates_its_own_components(self) -> None:
        with pytest.raises(ContractViolationError, match="dataset_id"):
            DatasetIdentity(dataset_id="", manifest_sha256=DIGEST)
        with pytest.raises(ContractViolationError, match="manifest_sha256"):
            DatasetIdentity(dataset_id="A_V1", manifest_sha256="nope")


class TestPathsAreNotIdentity:
    def test_the_contract_carries_no_path_field(self) -> None:
        assert {f.name for f in fields(DatasetRef)} == {
            "dataset_id",
            "manifest_sha256",
            "role",
        }
        assert {f.name for f in fields(DatasetIdentity)} == {
            "dataset_id",
            "manifest_sha256",
        }

    @pytest.mark.parametrize(
        "path_like",
        [
            "/path/to/research-data/XAUUSD_S_M1_CURATED_V1",
            "../XAUUSD_S_M1_MT5_V1",
            "data/XAUUSD_S_M1_CURATED_V1",
            "C:\\data\\XAUUSD",
        ],
    )
    def test_a_path_cannot_be_smuggled_in_as_a_dataset_id(self, path_like: str) -> None:
        with pytest.raises(ContractViolationError, match="dataset_id"):
            _ref(dataset_id=path_like)

    def test_no_resolution_behaviour_is_offered(self) -> None:
        """Slice 1A never touches disk; resolving a ref to files is Slice 1B."""
        ref = _ref()
        for absent in ("path", "resolve", "load", "open", "manifest_path"):
            assert not hasattr(ref, absent), f"{absent} would make location part of identity"
