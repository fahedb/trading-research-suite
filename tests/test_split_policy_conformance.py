"""Conformance gate for the owner-approved ADR-0012 V1 split policy."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from trading_research.core.identity import ViewRole
from trading_research.data.gating import SplitPolicy, read_split_policy

REPOSITORY_POLICY = (
    Path(__file__).parents[1] / "config" / "split-policies" / "adr-0012-v1.json"
)
BTC_REPOSITORY_POLICY = (
    Path(__file__).parents[1]
    / "config"
    / "split-policies"
    / "adr-0012-btc-v1.json"
)

# CP-2 N-2: these accepted native-key boundaries are deliberately literals in
# this conformance test only. They must never be derived from civil time or
# copied into production generation code.
_EXPECTED_V1_RANGES: dict[ViewRole, tuple[int, int]] = {
    ViewRole.TRAIN: (1_577_836_800, 1_704_067_140),
    ViewRole.VALIDATION: (1_704_067_200, 1_735_689_540),
    ViewRole.OOS: (1_735_689_600, 1_767_225_540),
    ViewRole.LOCKBOX: (1_767_225_600, 1_785_542_340),
}


def _assert_v1_ranges_conform(policy: SplitPolicy) -> None:
    actual = {
        role: (policy.range_for(role).start, policy.range_for(role).end)
        for role in _EXPECTED_V1_RANGES
    }
    assert actual == _EXPECTED_V1_RANGES


def test_repository_v1_policy_record_is_complete_and_versioned() -> None:
    document = json.loads(REPOSITORY_POLICY.read_bytes())
    assert set(document) == {
        "schema_version",
        "adr",
        "policy_version",
        "clock_domain",
        "time_unit",
        "calendar_time_policy",
        "ranges",
        "provenance",
    }
    assert document["schema_version"] == "1"
    assert document["adr"] == "ADR-0012"
    assert document["policy_version"] == "v1"
    assert document["clock_domain"] == "mt5.justmarkets-demo3.wallclock"
    assert document["time_unit"] == "seconds"
    assert document["calendar_time_policy"] == "native-wallclock-v1"
    assert document["provenance"] == {
        "normative_source": "docs/adr/ADR-0012-two-tier-splits.md",
        "ratified_on": "2026-08-06",
    }


def test_repository_v1_policy_pins_all_four_adr_0012_ranges() -> None:
    policy = read_split_policy(REPOSITORY_POLICY)

    _assert_v1_ranges_conform(policy)


def test_repository_btc_v1_policy_is_a_clock_specific_projection() -> None:
    document = json.loads(BTC_REPOSITORY_POLICY.read_bytes())
    policy = read_split_policy(BTC_REPOSITORY_POLICY)

    assert document["schema_version"] == "1"
    assert document["adr"] == "ADR-0012"
    assert document["policy_version"] == "btc-v1"
    assert document["clock_domain"] == "mt5.justmarkets-demo.wallclock"
    assert document["time_unit"] == "seconds"
    assert document["calendar_time_policy"] == "native-wallclock-v1"
    assert document["provenance"] == {
        "normative_source": (
            "docs/adr/ADR-0012-two-tier-splits.md#"
            "amendment-4--clock-domain-specific-policy-projections-2026-09-09"
        ),
        "ratified_on": "2026-09-09",
    }
    _assert_v1_ranges_conform(policy)
    assert policy.identity.policy_sha256 == (
        "3896cd9bedfe9357279c02df4904cda92d698b2d079c65e7bed6455503d55899"
    )


def test_changed_policy_boundary_fails_v1_conformance(tmp_path: Path) -> None:
    document = json.loads(REPOSITORY_POLICY.read_bytes())
    document["ranges"]["train"]["end_inclusive_key"] += 1
    mutated_path = tmp_path / "mutated-adr-0012-v1.json"
    mutated_path.write_text(json.dumps(document, indent=2))

    mutated_policy = read_split_policy(mutated_path)
    with pytest.raises(AssertionError):
        _assert_v1_ranges_conform(mutated_policy)


def test_conformance_boundaries_are_direct_native_integer_literals() -> None:
    """Guard against replacing the accepted keys with civil-time derivation."""
    tree = ast.parse(Path(__file__).read_text())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_EXPECTED_V1_RANGES"
    )
    assert isinstance(assignment.value, ast.Dict)
    assert len(assignment.value.values) == 4
    assert all(
        isinstance(span, ast.Tuple)
        and len(span.elts) == 2
        and all(
            isinstance(boundary, ast.Constant) and type(boundary.value) is int
            for boundary in span.elts
        )
        for span in assignment.value.values
    )

    imported_roots = {
        alias.name.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imported_roots.isdisjoint({"calendar", "datetime", "time", "zoneinfo"})
