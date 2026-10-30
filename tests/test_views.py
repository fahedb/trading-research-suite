"""Slice 1E bounded-view, context, and adjacent-union semantics."""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.load_fixtures import (
    MultiRoleLoadCase,
    make_load_case,
    make_multi_role_load_case,
    write_policy,
)
from trading_research.core import (
    ClockDomain,
    ClockDomainError,
    ContractViolationError,
    DatasetNotFoundError,
    DatasetRef,
    InsufficientContextError,
    MarketFrame,
    MarketFrameError,
    NativeTimeKey,
    TimeUnit,
    TimeUnitMismatchError,
    TradingResearchError,
    ViewBoundsError,
    ViewCompositionError,
    ViewMemberError,
    ViewRole,
)
from trading_research.data import (
    AccessGrant,
    BoundedSegment,
    BoundedView,
    ContextResolution,
    ContextShortfall,
    DatasetRegistry,
    MarketFrameLoader,
    RegisteredManifest,
    SplitPolicy,
    ViewUnion,
    ViewUnionMember,
    open_bounded_view,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


@pytest.fixture
def multi_role_case(tmp_path: Path) -> MultiRoleLoadCase:
    return make_multi_role_load_case(tmp_path)


def _loader(case: MultiRoleLoadCase) -> MarketFrameLoader:
    return MarketFrameLoader(registry=case.registry, split_policy=case.policy)


def _key(
    case: MultiRoleLoadCase,
    value: int,
    *,
    unit: TimeUnit | None = None,
    domain: ClockDomain | None = None,
) -> NativeTimeKey:
    return NativeTimeKey(
        value=value,
        unit=case.policy.time_unit if unit is None else unit,
        clock_domain=case.policy.clock_domain if domain is None else domain,
    )


def _open(
    case: MultiRoleLoadCase,
    *,
    roles: Sequence[ViewRole],
    context_start: int,
    eval_start: int,
    eval_end: int,
    context_shortfall: ContextShortfall | None = None,
) -> BoundedView:
    return open_bounded_view(
        loader=_loader(case),
        grants=tuple(case.grants[role] for role in roles),
        context_start=_key(case, context_start),
        eval_start=_key(case, eval_start),
        eval_end=_key(case, eval_end),
        columns=("close", "spread"),
        context_shortfall=context_shortfall,
    )


def _union_member(case: MultiRoleLoadCase, role: ViewRole) -> ViewUnionMember:
    grant = case.grants[role]
    return ViewUnionMember(dataset_ref=grant.dataset_ref, grant=grant)


class TestConstructionAndBounds:
    def test_valid_order_and_exact_public_boundaries(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
            context_start=150,
            eval_start=1_101,
            eval_end=1_201,
        )

        assert view.context_start == _key(multi_role_case, 150)
        assert view.eval_start == _key(multi_role_case, 1_101)
        assert view.eval_end == _key(multi_role_case, 1_201)
        assert view.eval_frame.keys.tolist() == [1_101, 1_102, 1_150, 1_200, 1_201]
        assert [segment.role for segment in view.segments] == [
            ViewRole.TRAIN,
            ViewRole.VALIDATION,
        ]

    @pytest.mark.parametrize(
        ("context_start", "eval_start", "eval_end"),
        [
            (1_102, 1_101, 1_200),
            (1_100, 1_201, 1_200),
            (1_202, 1_201, 1_200),
        ],
        ids=["context-after-eval-start", "eval-start-after-end", "both-inverted"],
    )
    def test_every_invalid_boundary_order_refuses(
        self,
        multi_role_case: MultiRoleLoadCase,
        context_start: int,
        eval_start: int,
        eval_end: int,
    ) -> None:
        with pytest.raises(ViewBoundsError):
            _open(
                multi_role_case,
                roles=(ViewRole.VALIDATION,),
                context_start=context_start,
                eval_start=eval_start,
                eval_end=eval_end,
            )

    def test_cross_domain_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ClockDomainError):
            open_bounded_view(
                loader=_loader(multi_role_case),
                grants=(multi_role_case.grants[ViewRole.VALIDATION],),
                context_start=_key(
                    multi_role_case,
                    1_100,
                    domain=ClockDomain("other.wallclock"),
                ),
                eval_start=_key(multi_role_case, 1_101),
                eval_end=_key(multi_role_case, 1_202),
            )

    def test_cross_unit_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(TimeUnitMismatchError):
            open_bounded_view(
                loader=_loader(multi_role_case),
                grants=(multi_role_case.grants[ViewRole.VALIDATION],),
                context_start=_key(
                    multi_role_case,
                    1_100,
                    unit=TimeUnit.MILLISECONDS,
                ),
                eval_start=_key(multi_role_case, 1_101),
                eval_end=_key(multi_role_case, 1_202),
            )

    def test_requested_boundaries_inside_observed_gaps_are_legal(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.VALIDATION,),
            context_start=1_103,
            eval_start=1_140,
            eval_end=1_200,
        )
        assert view.segments[0].frame.keys.tolist() == [1_150, 1_200]
        assert view.eval_frame.keys.tolist() == [1_150, 1_200]

    def test_policy_adjacent_observed_gap_boundary_is_legal(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
            context_start=500,
            eval_start=1_100,
            eval_end=1_102,
        )
        assert [segment.role for segment in view.segments] == [ViewRole.VALIDATION]
        assert view.context.resolved_context_start == _key(multi_role_case, 500)

    def test_zero_length_context_is_legal(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.VALIDATION,),
            context_start=1_150,
            eval_start=1_150,
            eval_end=1_201,
        )
        assert view.eval_frame.keys.tolist() == [1_150, 1_200, 1_201]

    def test_single_row_evaluation_is_legal(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.VALIDATION,),
            context_start=1_100,
            eval_start=1_150,
            eval_end=1_150,
        )
        assert view.eval_frame.keys.tolist() == [1_150]

    def test_empty_evaluation_refuses_with_view_bounds_error(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        with pytest.raises(ViewBoundsError, match="zero observed rows"):
            _open(
                multi_role_case,
                roles=(ViewRole.VALIDATION,),
                context_start=1_100,
                eval_start=1_160,
                eval_end=1_190,
            )

    def test_evaluation_must_stay_inside_final_member(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        valid = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN,),
            context_start=100,
            eval_start=101,
            eval_end=202,
        )
        assert valid.eval_frame.num_rows > 0
        with pytest.raises(ViewBoundsError, match="observed admitted range"):
            _open(
                multi_role_case,
                roles=(ViewRole.TRAIN,),
                context_start=100,
                eval_start=101,
                eval_end=203,
            )


class TestSlicingAndReachability:
    @pytest.mark.parametrize(
        ("eval_start", "eval_end", "expected"),
        [
            (1_101, 1_202, [1_101, 1_102, 1_150, 1_200, 1_201, 1_202]),
            (1_100, 1_201, [1_100, 1_101, 1_102, 1_150, 1_200, 1_201]),
            (1_101, 1_201, [1_101, 1_102, 1_150, 1_200, 1_201]),
        ],
        ids=["left-edge", "right-edge", "both-edges"],
    )
    def test_inclusive_edge_slicing(
        self,
        multi_role_case: MultiRoleLoadCase,
        eval_start: int,
        eval_end: int,
        expected: list[int],
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.VALIDATION,),
            context_start=1_100,
            eval_start=eval_start,
            eval_end=eval_end,
        )
        assert view.eval_frame.keys.tolist() == expected

    def test_deterministic_fixed_seed_boundary_sweep(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        observed = [1_100, 1_101, 1_102, 1_150, 1_200, 1_201, 1_202]
        generator = random.Random(10_012)
        for _ in range(80):
            left = generator.randint(1_100, 1_202)
            right = generator.randint(left, 1_202)
            expected = [key for key in observed if left <= key <= right]
            if not expected:
                with pytest.raises(ViewBoundsError):
                    _open(
                        multi_role_case,
                        roles=(ViewRole.VALIDATION,),
                        context_start=left,
                        eval_start=left,
                        eval_end=right,
                    )
                continue
            view = _open(
                multi_role_case,
                roles=(ViewRole.VALIDATION,),
                context_start=left,
                eval_start=left,
                eval_end=right,
            )
            assert view.eval_frame.keys.tolist() == expected
            assert max(view.eval_frame.keys) <= right

    def test_no_row_after_eval_end_is_reachable_through_any_frame_operation(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION, ViewRole.OOS),
            context_start=150,
            eval_start=2_101,
            eval_end=2_150,
        )

        assert view.eval_frame.keys.tolist() == [2_101, 2_102, 2_150]
        for segment in view.segments:
            frame = segment.frame
            assert all(key <= view.eval_end.value for key in frame.keys)
            selected = frame.select(("spread",))
            assert all(key <= view.eval_end.value for key in selected.keys)
            if frame.num_rows:
                sibling = frame.slice_by_keys(
                    _key(multi_role_case, int(frame.keys[0])),
                    _key(multi_role_case, int(frame.keys[-1])),
                )
                assert all(key <= view.eval_end.value for key in sibling.keys)

        with pytest.raises(MarketFrameError, match="exceeds frame bounds"):
            view.eval_frame.slice_by_keys(
                view.eval_start,
                _key(multi_role_case, view.eval_end.value + 1),
            )

    def test_bounded_view_retains_no_loading_authority_or_location(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
            context_start=150,
            eval_start=1_101,
            eval_end=1_150,
        )
        forbidden = (
            MarketFrameLoader,
            AccessGrant,
            DatasetRegistry,
            RegisteredManifest,
            SplitPolicy,
            Path,
        )
        pending: list[object] = [view]
        seen: set[int] = set()
        while pending:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            assert not isinstance(value, forbidden)
            if isinstance(value, MarketFrame):
                assert all(key <= view.eval_end.value for key in value.keys)
                continue
            if isinstance(value, tuple):
                pending.extend(value)
                continue
            slots = getattr(type(value), "__slots__", ())
            for slot in slots:
                pending.append(getattr(value, slot))

        public = {name for name in dir(view) if not name.startswith("_")}
        assert "frame" not in public
        assert not public & {
            "loader",
            "grants",
            "registry",
            "manifest",
            "policy",
            "path",
            "root",
        }


class TestContextAuthorization:
    def test_same_view_context(self, multi_role_case: MultiRoleLoadCase) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.VALIDATION,),
            context_start=1_101,
            eval_start=1_150,
            eval_end=1_202,
        )
        assert [segment.role for segment in view.segments] == [ViewRole.VALIDATION]
        assert view.segments[0].frame.keys.tolist() == [
            1_101,
            1_102,
            1_150,
            1_200,
            1_201,
            1_202,
        ]

    def test_earlier_authorized_context(self, multi_role_case: MultiRoleLoadCase) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
            context_start=200,
            eval_start=1_100,
            eval_end=1_102,
        )
        assert [segment.frame.keys.tolist() for segment in view.segments] == [
            [200, 201, 202],
            [1_100, 1_101, 1_102],
        ]

    def test_multi_view_consecutive_chain(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION, ViewRole.OOS),
            context_start=201,
            eval_start=2_100,
            eval_end=2_102,
        )
        assert [segment.role for segment in view.segments] == [
            ViewRole.TRAIN,
            ViewRole.VALIDATION,
            ViewRole.OOS,
        ]

    def test_oos_to_ceremony_authorized_lockbox_evaluation_is_legal(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.OOS, ViewRole.LOCKBOX),
            context_start=2_200,
            eval_start=3_100,
            eval_end=3_102,
        )
        assert [segment.role for segment in view.segments] == [
            ViewRole.OOS,
            ViewRole.LOCKBOX,
        ]
        assert view.eval_frame.dataset_ref.role is ViewRole.LOCKBOX

    def test_policy_adjacency_ignores_observed_key_distance(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
            context_start=202,
            eval_start=1_100,
            eval_end=1_101,
        )
        assert view.segments[0].frame.keys.tolist() == [202]
        assert view.segments[1].frame.keys.tolist() == [1_100, 1_101]

    def test_later_view_context_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ViewMemberError, match="consecutive"):
            _open(
                multi_role_case,
                roles=(ViewRole.VALIDATION, ViewRole.TRAIN),
                context_start=100,
                eval_start=101,
                eval_end=202,
            )

    def test_skipped_role_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ViewMemberError, match="consecutive"):
            _open(
                multi_role_case,
                roles=(ViewRole.TRAIN, ViewRole.OOS),
                context_start=100,
                eval_start=2_100,
                eval_end=2_102,
            )

    def test_duplicate_member_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ViewMemberError, match="duplicate role"):
            open_bounded_view(
                loader=_loader(multi_role_case),
                grants=(
                    multi_role_case.grants[ViewRole.VALIDATION],
                    multi_role_case.grants[ViewRole.VALIDATION],
                ),
                context_start=_key(multi_role_case, 1_100),
                eval_start=_key(multi_role_case, 1_101),
                eval_end=_key(multi_role_case, 1_202),
            )

    def test_unauthorized_gated_member_refuses(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        with pytest.raises(ViewMemberError, match="AccessGrant"):
            open_bounded_view(
                loader=_loader(multi_role_case),
                grants=(multi_role_case.targets[ViewRole.LOCKBOX],),  # type: ignore[arg-type]
                context_start=_key(multi_role_case, 3_100),
                eval_start=_key(multi_role_case, 3_101),
                eval_end=_key(multi_role_case, 3_102),
            )

    @pytest.mark.parametrize(
        "role",
        [ViewRole.RAW, ViewRole.CURATED, ViewRole.SNAPSHOT],
    )
    def test_non_tier_one_bounded_member_refuses(
        self, tmp_path: Path, role: ViewRole
    ) -> None:
        case = make_load_case(tmp_path, role=role)
        loader = MarketFrameLoader(registry=case.registry, split_policy=case.policy)
        admitted = case.target.manifest.admitted_native_range
        axis = case.target.manifest
        with pytest.raises(ViewMemberError, match="not a Tier-1 evaluation member"):
            open_bounded_view(
                loader=loader,
                grants=(case.grant,),
                context_start=NativeTimeKey(
                    admitted.start,
                    axis.time_unit,
                    axis.clock_domain,
                ),
                eval_start=NativeTimeKey(
                    admitted.start,
                    axis.time_unit,
                    axis.clock_domain,
                ),
                eval_end=NativeTimeKey(
                    admitted.end,
                    axis.time_unit,
                    axis.clock_domain,
                ),
            )

    def test_loader_without_grants_authorizes_nothing(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        with pytest.raises(ViewMemberError, match="at least one"):
            open_bounded_view(
                loader=_loader(multi_role_case),
                grants=(),
                context_start=_key(multi_role_case, 100),
                eval_start=_key(multi_role_case, 101),
                eval_end=_key(multi_role_case, 102),
            )

    def test_each_actual_load_receives_its_corresponding_exact_grant(
        self,
        multi_role_case: MultiRoleLoadCase,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_load = MarketFrameLoader.load
        observed: list[AccessGrant] = []

        def traced_load(
            self: MarketFrameLoader,
            grant: AccessGrant,
            **kwargs: object,
        ) -> MarketFrame:
            observed.append(grant)
            return real_load(self, grant, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(MarketFrameLoader, "load", traced_load)
        _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION, ViewRole.OOS),
            context_start=200,
            eval_start=2_100,
            eval_end=2_102,
        )
        assert observed == [
            multi_role_case.grants[ViewRole.TRAIN],
            multi_role_case.grants[ViewRole.VALIDATION],
            multi_role_case.grants[ViewRole.OOS],
        ]

    def test_exact_grant_loader_member_mismatch_refuses(self, tmp_path: Path) -> None:
        case = make_multi_role_load_case(tmp_path / "catalog")
        other = make_load_case(tmp_path / "other", view_id="OTHER_TRAIN_V1")
        with pytest.raises(DatasetNotFoundError):
            open_bounded_view(
                loader=_loader(case),
                grants=(other.grant,),
                context_start=_key(case, 100),
                eval_start=_key(case, 101),
                eval_end=_key(case, 102),
            )

    def test_split_policy_mismatch_refuses_before_loading(
        self,
        tmp_path: Path,
        multi_role_case: MultiRoleLoadCase,
    ) -> None:
        alternate = write_policy(tmp_path / "alternate-policy.json", alternate_bytes=True)
        loader = MarketFrameLoader(
            registry=multi_role_case.registry,
            split_policy=alternate,
        )
        with pytest.raises(ViewMemberError, match="different split-policy bytes"):
            open_bounded_view(
                loader=loader,
                grants=(multi_role_case.grants[ViewRole.TRAIN],),
                context_start=_key(multi_role_case, 100),
                eval_start=_key(multi_role_case, 101),
                eval_end=_key(multi_role_case, 102),
            )

    def test_insufficient_context_refuses_by_default(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        with pytest.raises(InsufficientContextError, match="requested 50, resolved 100"):
            _open(
                multi_role_case,
                roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
                context_start=50,
                eval_start=1_100,
                eval_end=1_102,
            )

    def test_structured_short_context_acceptance_is_retained(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        evidence = ContextShortfall(
            requested_context_start=_key(multi_role_case, 50),
            resolved_context_start=_key(multi_role_case, 100),
            reason_code="limited-history",
            detail="The declared warm-up accepts the available synthetic history.",
            accepted=True,
        )
        view = _open(
            multi_role_case,
            roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
            context_start=50,
            eval_start=1_100,
            eval_end=1_102,
            context_shortfall=evidence,
        )
        assert view.context.requested_context_start == _key(multi_role_case, 50)
        assert view.context.resolved_context_start == _key(multi_role_case, 100)
        assert view.context.shortfall is evidence
        assert view.context.short_context_accepted
        assert view.context.shortfall.reason_code == "limited-history"
        assert view.context.shortfall.accepted is True

    @pytest.mark.parametrize("accepted", [False, True])
    def test_unaccepted_or_boundary_mismatched_shortfall_refuses(
        self,
        multi_role_case: MultiRoleLoadCase,
        accepted: bool,
    ) -> None:
        resolved = 100 if not accepted else 101
        evidence = ContextShortfall(
            requested_context_start=_key(multi_role_case, 50),
            resolved_context_start=_key(multi_role_case, resolved),
            reason_code="limited-history",
            detail="Synthetic refusal evidence.",
            accepted=accepted,
        )
        with pytest.raises(InsufficientContextError, match="exactly match"):
            _open(
                multi_role_case,
                roles=(ViewRole.TRAIN, ViewRole.VALIDATION),
                context_start=50,
                eval_start=1_100,
                eval_end=1_102,
                context_shortfall=evidence,
            )


class TestViewUnion:
    def test_train_union(self, multi_role_case: MultiRoleLoadCase) -> None:
        union = ViewUnion(members=(_union_member(multi_role_case, ViewRole.TRAIN),))
        assert [member.role for member in union.members] == [ViewRole.TRAIN]

    def test_train_validation_union(self, multi_role_case: MultiRoleLoadCase) -> None:
        union = ViewUnion(
            members=(
                _union_member(multi_role_case, ViewRole.TRAIN),
                _union_member(multi_role_case, ViewRole.VALIDATION),
            )
        )
        assert [member.role for member in union.members] == [
            ViewRole.TRAIN,
            ViewRole.VALIDATION,
        ]

    def test_train_validation_authorized_oos_union(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        union = ViewUnion(
            members=tuple(
                _union_member(multi_role_case, role)
                for role in (ViewRole.TRAIN, ViewRole.VALIDATION, ViewRole.OOS)
            )
        )
        assert [member.role for member in union.members] == [
            ViewRole.TRAIN,
            ViewRole.VALIDATION,
            ViewRole.OOS,
        ]
        assert all(member.dataset_ref.manifest_sha256 for member in union.members)
        assert not hasattr(union, "fingerprint")
        assert not hasattr(union, "union_hash")

    def test_reversed_order_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ViewMemberError):
            ViewUnion(
                members=(
                    _union_member(multi_role_case, ViewRole.VALIDATION),
                    _union_member(multi_role_case, ViewRole.TRAIN),
                )
            )

    def test_duplicate_member_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        member = _union_member(multi_role_case, ViewRole.TRAIN)
        with pytest.raises(ViewMemberError, match="duplicate"):
            ViewUnion(members=(member, member))

    def test_skipped_member_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ViewMemberError, match="adjacent"):
            ViewUnion(
                members=(
                    _union_member(multi_role_case, ViewRole.TRAIN),
                    _union_member(multi_role_case, ViewRole.OOS),
                )
            )

    def test_unauthorized_oos_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        oos_ref = multi_role_case.grants[ViewRole.OOS].dataset_ref
        with pytest.raises(ViewMemberError, match="does not exactly match"):
            ViewUnionMember(
                dataset_ref=oos_ref,
                grant=multi_role_case.grants[ViewRole.TRAIN],
            )

    def test_lockbox_refuses(self, multi_role_case: MultiRoleLoadCase) -> None:
        with pytest.raises(ViewMemberError, match="lockbox"):
            ViewUnion(
                members=(
                    _union_member(multi_role_case, ViewRole.TRAIN),
                    _union_member(multi_role_case, ViewRole.VALIDATION),
                    _union_member(multi_role_case, ViewRole.OOS),
                    _union_member(multi_role_case, ViewRole.LOCKBOX),
                )
            )

    @pytest.mark.parametrize(
        "role",
        [ViewRole.RAW, ViewRole.CURATED, ViewRole.SNAPSHOT],
    )
    def test_non_research_role_refuses(self, tmp_path: Path, role: ViewRole) -> None:
        case = make_load_case(tmp_path, role=role)
        member = ViewUnionMember(dataset_ref=case.grant.dataset_ref, grant=case.grant)
        with pytest.raises(ViewMemberError, match=role.value):
            ViewUnion(members=(member,))

    def test_member_hash_mismatch_refuses(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        grant = multi_role_case.grants[ViewRole.TRAIN]
        mismatched = DatasetRef(
            dataset_id=grant.dataset_ref.dataset_id,
            manifest_sha256="f" * 64,
            role=ViewRole.TRAIN,
        )
        with pytest.raises(ViewMemberError, match="does not exactly match"):
            ViewUnionMember(dataset_ref=mismatched, grant=grant)

    def test_split_policy_mismatch_refuses(self, tmp_path: Path) -> None:
        standard = make_load_case(tmp_path / "standard", role=ViewRole.TRAIN)
        alternate = make_load_case(
            tmp_path / "alternate",
            role=ViewRole.VALIDATION,
            alternate_policy_bytes=True,
        )
        train = ViewUnionMember(
            dataset_ref=standard.grant.dataset_ref,
            grant=standard.grant,
        )
        validation = ViewUnionMember(
            dataset_ref=alternate.grant.dataset_ref,
            grant=alternate.grant,
        )
        with pytest.raises(ViewMemberError, match="different split-policy bytes"):
            ViewUnion(members=(train, validation))

    def test_hand_written_range_substitute_is_refused(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        member = _union_member(multi_role_case, ViewRole.TRAIN)
        with pytest.raises(TypeError):
            ViewUnion(  # type: ignore[call-arg]
                members=(member,),
                start=_key(multi_role_case, 100),
            )

    def test_union_retains_neither_rows_nor_authority(
        self, multi_role_case: MultiRoleLoadCase
    ) -> None:
        union = ViewUnion(
            members=(
                _union_member(multi_role_case, ViewRole.TRAIN),
                _union_member(multi_role_case, ViewRole.VALIDATION),
            )
        )
        retained = [union, *union.members]
        assert not any(isinstance(value, (MarketFrame, AccessGrant)) for value in retained)
        assert not any(
            token in slot.lower()
            for value in retained
            for slot in getattr(type(value), "__slots__", ())
            for token in ("frame", "grant", "loader", "path", "root", "registry")
        )


class TestValueContracts:
    @pytest.mark.parametrize("contract", [BoundedSegment, BoundedView, ContextResolution])
    def test_invalid_direct_construction_is_disabled(self, contract: type[object]) -> None:
        with pytest.raises(TypeError):
            contract()

    def test_contracts_are_frozen(self, multi_role_case: MultiRoleLoadCase) -> None:
        view = _open(
            multi_role_case,
            roles=(ViewRole.VALIDATION,),
            context_start=1_100,
            eval_start=1_101,
            eval_end=1_102,
        )
        with pytest.raises(AttributeError):
            view._eval_end = _key(multi_role_case, 1_202)  # type: ignore[misc]
        with pytest.raises(AttributeError):
            view.segments[0]._frame = view.eval_frame  # type: ignore[misc]

    def test_slice_1e_errors_form_one_small_hierarchy(self) -> None:
        assert issubclass(ViewCompositionError, ContractViolationError)
        assert issubclass(ViewBoundsError, ViewCompositionError)
        assert issubclass(ViewMemberError, ViewCompositionError)
        assert issubclass(InsufficientContextError, ViewCompositionError)
        assert issubclass(ViewCompositionError, TradingResearchError)
