"""Invariants of the native time axis.

The distinction under test throughout: these types provide *native ordering*
and refuse *civil-time interpretation*. A test that decoded a value into a
calendar instant would be testing something the platform deliberately cannot
do.
"""

from __future__ import annotations

import pytest

from trading_research.core import (
    CalendarTimePolicy,
    ClockDomain,
    ClockDomainError,
    ContractViolationError,
    NativeTimeKey,
    TimeUnit,
    TimeUnitMismatchError,
)

# The domain the current adapter supplies. It lives in the tests, not in
# ``core`` — core must never name a venue.
BROKER = ClockDomain("mt5.justmarkets-demo3.wallclock")
OTHER = ClockDomain("other.venue.wallclock")

INT64_MIN = -9_223_372_036_854_775_808
INT64_MAX = 9_223_372_036_854_775_807


def _key(
    value: int, *, unit: TimeUnit = TimeUnit.SECONDS, domain: ClockDomain = BROKER
) -> NativeTimeKey:
    return NativeTimeKey(value=value, unit=unit, clock_domain=domain)


class TestTimeUnit:
    def test_the_four_accepted_resolutions_exist(self) -> None:
        assert {u.value for u in TimeUnit} == {
            "seconds",
            "milliseconds",
            "microseconds",
            "nanoseconds",
        }

    def test_units_carry_no_conversion_factor(self) -> None:
        """A conversion factor here would invite exactly the implicit rescaling V1 forbids."""
        assert not hasattr(TimeUnit.SECONDS, "per_second")
        assert not hasattr(TimeUnit.SECONDS, "to_seconds")


class TestCalendarTimePolicy:
    def test_v1_ships_exactly_one_policy(self) -> None:
        assert [p.value for p in CalendarTimePolicy] == ["native-wallclock-v1"]

    def test_the_policy_is_an_identity_only(self) -> None:
        """Slice 1A names the policy; decoding under it does not exist yet."""
        policy = CalendarTimePolicy.NATIVE_WALLCLOCK_V1
        assert not hasattr(policy, "decode")
        assert not hasattr(policy, "to_civil_time")


class TestClockDomain:
    def test_a_dotted_venue_identifier_is_accepted(self) -> None:
        assert ClockDomain("mt5.justmarkets-demo3.wallclock").identifier == (
            "mt5.justmarkets-demo3.wallclock"
        )

    def test_domains_are_compared_by_exact_identifier(self) -> None:
        assert ClockDomain("a.b") == ClockDomain("a.b")
        assert ClockDomain("a.b") != ClockDomain("a.c")

    def test_domain_comparison_is_case_sensitive(self) -> None:
        """Identifiers are opaque labels; the platform does not fold their case."""
        assert ClockDomain("Venue.Wallclock") != ClockDomain("venue.wallclock")

    def test_domains_are_hashable(self) -> None:
        assert len({ClockDomain("a.b"), ClockDomain("a.b"), ClockDomain("a.c")}) == 2

    def test_domains_are_immutable(self) -> None:
        domain = ClockDomain("a.b")
        with pytest.raises(AttributeError):
            domain.identifier = "c.d"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "identifier",
        [
            "",
            " ",
            "has space",
            "\tleading-tab",
            "trailing ",
            ".leading-dot",
            "trailing-dot.",
            "-leading-hyphen",
            "path/like",
            "back\\slash",
            "new\nline",
            "null\x00byte",
        ],
    )
    def test_structurally_invalid_identifiers_are_refused(self, identifier: str) -> None:
        with pytest.raises(ContractViolationError, match=r"ClockDomain\.identifier"):
            ClockDomain(identifier)

    def test_over_long_identifiers_are_refused(self) -> None:
        with pytest.raises(ContractViolationError, match="exceeds"):
            ClockDomain("a" * 129)

    def test_non_string_identifiers_are_refused(self) -> None:
        with pytest.raises(ContractViolationError, match="must be a string"):
            ClockDomain(42)  # type: ignore[arg-type]

    def test_no_conversion_behaviour_is_offered(self) -> None:
        domain = ClockDomain("a.b")
        assert not hasattr(domain, "to_utc")
        assert not hasattr(domain, "offset")


class TestNativeTimeKeyConstruction:
    def test_a_key_carries_value_unit_and_domain(self) -> None:
        key = _key(1_577_926_800)
        assert key.value == 1_577_926_800
        assert key.unit is TimeUnit.SECONDS
        assert key.clock_domain == BROKER

    def test_keys_are_immutable(self) -> None:
        key = _key(1)
        with pytest.raises(AttributeError):
            key.value = 2  # type: ignore[misc]

    def test_keys_are_hashable_and_deduplicate_by_all_three_fields(self) -> None:
        assert len({_key(1), _key(1), _key(2)}) == 2
        assert len({_key(1), _key(1, domain=OTHER)}) == 2
        assert len({_key(1), _key(1, unit=TimeUnit.MILLISECONDS)}) == 2

    @pytest.mark.parametrize("value", [INT64_MIN, -1, 0, 1, INT64_MAX])
    def test_signed_int64_boundary_values_are_accepted(self, value: int) -> None:
        assert _key(value).value == value

    @pytest.mark.parametrize("value", [INT64_MIN - 1, INT64_MAX + 1])
    def test_values_outside_signed_int64_are_refused(self, value: int) -> None:
        with pytest.raises(ContractViolationError, match="signed 64-bit range"):
            _key(value)

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_is_refused_despite_subclassing_int(self, value: bool) -> None:
        """``NativeTimeKey(True, ...)`` would otherwise silently become key 1.

        Note there is no ``type: ignore`` here: ``bool`` satisfies the ``int``
        annotation statically, so the type checker cannot catch this and the
        runtime check is the only thing standing between a stray flag and a
        plausible-looking timestamp.
        """
        with pytest.raises(ContractViolationError, match="got bool"):
            _key(value)

    @pytest.mark.parametrize("value", [1.0, "1", None])
    def test_non_integer_values_are_refused(self, value: object) -> None:
        with pytest.raises(ContractViolationError, match="must be an integer"):
            _key(value)  # type: ignore[arg-type]

    def test_untagged_time_is_impossible(self) -> None:
        with pytest.raises(ContractViolationError, match="must be a TimeUnit"):
            NativeTimeKey(value=1, unit="seconds", clock_domain=BROKER)  # type: ignore[arg-type]
        with pytest.raises(ContractViolationError, match="must be a ClockDomain"):
            NativeTimeKey(value=1, unit=TimeUnit.SECONDS, clock_domain="broker")  # type: ignore[arg-type]

    def test_no_civil_time_interpretation_is_offered(self) -> None:
        """Native ordering is provided; decoding to a calendar instant is not."""
        key = _key(1_577_926_800)
        for absent in ("to_datetime", "to_utc", "isoformat", "date", "timestamp"):
            assert not hasattr(key, absent), f"{absent} would claim civil-time meaning"


class TestNativeTimeKeyOrdering:
    def test_keys_on_one_axis_order_by_value(self) -> None:
        assert _key(1) < _key(2)
        assert _key(2) > _key(1)
        assert _key(1) <= _key(1)
        assert _key(1) >= _key(1)

    def test_keys_on_one_axis_sort(self) -> None:
        assert [k.value for k in sorted([_key(3), _key(1), _key(2)])] == [1, 2, 3]

    def test_ordering_across_domains_is_refused(self) -> None:
        with pytest.raises(ClockDomainError, match="across clock domains"):
            _ = _key(1) < _key(2, domain=OTHER)

    @pytest.mark.parametrize("op", ["lt", "le", "gt", "ge"])
    def test_every_ordering_operator_refuses_across_domains(self, op: str) -> None:
        import operator

        with pytest.raises(ClockDomainError):
            getattr(operator, op)(_key(1), _key(2, domain=OTHER))

    def test_ordering_across_units_is_refused_rather_than_converted(self) -> None:
        """1000 milliseconds is not silently 1 second: no implicit conversion exists."""
        with pytest.raises(TimeUnitMismatchError, match="across units"):
            _ = _key(1) < _key(1000, unit=TimeUnit.MILLISECONDS)

    @pytest.mark.parametrize("op", ["lt", "le", "gt", "ge"])
    def test_every_ordering_operator_refuses_across_units(self, op: str) -> None:
        import operator

        with pytest.raises(TimeUnitMismatchError):
            getattr(operator, op)(_key(1), _key(2, unit=TimeUnit.NANOSECONDS))

    def test_ordering_against_a_bare_integer_is_refused(self) -> None:
        """A bare int carries no domain, so it has no place on this axis."""
        with pytest.raises(ContractViolationError, match="cannot be ordered against int"):
            _ = _key(1) < 2  # type: ignore[operator]


class TestNativeTimeKeyEquality:
    def test_equality_requires_all_three_fields(self) -> None:
        assert _key(1) == _key(1)
        assert _key(1) != _key(2)
        assert _key(1) != _key(1, domain=OTHER)
        assert _key(1) != _key(1, unit=TimeUnit.MILLISECONDS)

    def test_equality_across_domains_is_false_rather_than_an_error(self) -> None:
        """Ordering refuses across domains; equality must not, or containers break.

        "Are these the same key?" is answerable across domains — no. Only
        "which came first?" is unanswerable, and only that one raises.
        """
        assert (_key(1) == _key(1, domain=OTHER)) is False
        assert _key(1) in {_key(1), _key(9, domain=OTHER)}
        assert {_key(1, domain=OTHER): "v"}[_key(1, domain=OTHER)] == "v"

    def test_equality_against_a_foreign_type_is_false_rather_than_an_error(self) -> None:
        bare_int: object = 1
        text: object = "1"
        assert (_key(1) == bare_int) is False
        assert (_key(1) == text) is False
