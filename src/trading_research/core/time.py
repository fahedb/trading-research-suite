"""The platform's only time axis: opaque integers tagged with their domain.

Market data here arrives as an integer produced by some venue's clock. What
that integer *means* in civil time is a separate question, often unanswerable:
the encoding convention, the offset, and the daylight-saving history of the
originating clock may all be unknown and unrecoverable. Converting such an
integer into a civil timestamp would replace a known unknown with a confident
falsehood.

So the platform does not convert. It orders. Ordering, point-in-time slicing,
window boundaries, and split boundaries are all answerable from the raw
integer alone, provided two values are only ever compared when they came from
the same clock at the same resolution. That proviso is what
:class:`NativeTimeKey` exists to enforce.

The invariant is **no untagged time**, not "everything must be one reference
clock". Nothing in this module imports :mod:`datetime`, and nothing here
attaches civil-time meaning to a value:

* **native ordering** — "which integer came first in this domain" — is what
  this module provides, and it needs no calendar;
* **civil-time interpretation** — "what wall-clock instant is this" — is a
  separate, policy-governed concern (:class:`CalendarTimePolicy`) that this
  module deliberately does not implement.

These are scalar boundary types: manifest ranges, split boundaries, config
values, audit records. Bulk market data carries a bare integer array plus one
frame-level ``(unit, clock_domain)`` tag — never one of these objects per row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from trading_research.core.errors import (
    ClockDomainError,
    ContractViolationError,
    TimeUnitMismatchError,
)

__all__ = [
    "CalendarTimePolicy",
    "ClockDomain",
    "NativeTimeKey",
    "TimeUnit",
]

# A native value is a signed 64-bit integer. Values outside this range cannot
# round-trip through the columnar storage the data layer will use, so they are
# refused at the boundary rather than truncated somewhere downstream.
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

# Domain identifiers are opaque labels, but not arbitrary strings: they appear
# in manifests, audit records and error messages, and two identifiers that
# differ only by surrounding whitespace would read as one domain while
# behaving as two. Alphanumeric ends, dots/underscores/hyphens inside.
_DOMAIN_IDENTIFIER = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
_MAX_DOMAIN_IDENTIFIER_LENGTH = 128


class TimeUnit(StrEnum):
    """Resolution of a native time value.

    Members are labels, not conversion factors. The platform never rescales a
    value from one unit to another: a dataset's unit is a fact about how its
    source encoded time, and reinterpreting it is a data-provenance decision,
    not an arithmetic one.
    """

    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"
    MICROSECONDS = "microseconds"
    NANOSECONDS = "nanoseconds"


class CalendarTimePolicy(StrEnum):
    """Declares *how* a native value may be read as civil time, where allowed.

    Naming a policy is how a caller says "I am about to do something
    calendar-shaped, and here is the interpretation I am relying on" — so that
    the assumption is recorded rather than assumed. V1 ships exactly one
    policy, and this slice defines only its identity: no decoding, no
    conversion, and no calendar arithmetic exists yet.
    """

    NATIVE_WALLCLOCK_V1 = "native-wallclock-v1"


@dataclass(frozen=True, slots=True)
class ClockDomain:
    """The clock a native value came from, as an opaque identifier.

    Equality is exact string equality, and that is the whole of its meaning:
    the platform knows of no offset, drift or relationship between two
    domains, so two identifiers are either the same clock or unrelated. The
    identifier is supplied by whichever adapter read the data; nothing in
    ``core`` knows or names a specific venue.
    """

    identifier: str

    def __post_init__(self) -> None:
        """Reject identifiers that cannot serve as a stable, readable label.

        Raises:
            ContractViolationError: If the identifier is not a string, is
                empty, is over-long, or contains whitespace, path separators
                or other characters outside the permitted set.
        """
        if not isinstance(self.identifier, str):
            raise ContractViolationError(
                f"ClockDomain.identifier must be a string, got {type(self.identifier).__name__}"
            )
        if not self.identifier:
            raise ContractViolationError("ClockDomain.identifier must not be empty")
        if len(self.identifier) > _MAX_DOMAIN_IDENTIFIER_LENGTH:
            raise ContractViolationError(
                f"ClockDomain.identifier exceeds {_MAX_DOMAIN_IDENTIFIER_LENGTH} characters: "
                f"{len(self.identifier)}"
            )
        if _DOMAIN_IDENTIFIER.match(self.identifier) is None:
            raise ContractViolationError(
                f"ClockDomain.identifier {self.identifier!r} is not a valid domain identifier; "
                "expected alphanumeric ends with '.', '_' or '-' inside, and no whitespace"
            )


@dataclass(frozen=True, slots=True)
class NativeTimeKey:
    """One instant on one clock, at one resolution, as an opaque integer.

    Two keys may be **ordered** only when they share both a unit and a clock
    domain; otherwise ordering raises. **Equality never raises** — keys are
    put in sets and dict keys while resolving manifests and configs, and an
    equality test that could explode would make those containers unusable.
    Keys from different domains are simply not equal, which is both true and
    safe. The asymmetry is deliberate:

    * ``==`` asks "are these the same key?" — answerable across domains (no);
    * ``<`` asks "which came first?" — not answerable across domains, so it
      refuses instead of inventing an answer.

    Attributes:
        value: The raw integer as the source encoded it. It carries no civil
            time meaning and must not be decoded here.
        unit: Resolution of ``value``.
        clock_domain: The clock ``value`` was produced by.
    """

    value: int
    unit: TimeUnit
    clock_domain: ClockDomain

    def __post_init__(self) -> None:
        """Validate the value's type and range, and the tags' types.

        Raises:
            ContractViolationError: If ``value`` is a bool, is not an integer,
                or falls outside the signed 64-bit range; or if ``unit`` or
                ``clock_domain`` is not the expected type.
        """
        # bool is a subclass of int, so ``NativeTimeKey(True, ...)`` would
        # otherwise silently become the key 1.
        if isinstance(self.value, bool):
            raise ContractViolationError(
                f"NativeTimeKey.value must be an integer, got bool {self.value!r}"
            )
        if not isinstance(self.value, int):
            raise ContractViolationError(
                f"NativeTimeKey.value must be an integer, got {type(self.value).__name__}"
            )
        if not _INT64_MIN <= self.value <= _INT64_MAX:
            raise ContractViolationError(
                f"NativeTimeKey.value {self.value} is outside the signed 64-bit range "
                f"[{_INT64_MIN}, {_INT64_MAX}]"
            )
        if not isinstance(self.unit, TimeUnit):
            raise ContractViolationError(
                f"NativeTimeKey.unit must be a TimeUnit, got {type(self.unit).__name__}"
            )
        if not isinstance(self.clock_domain, ClockDomain):
            raise ContractViolationError(
                "NativeTimeKey.clock_domain must be a ClockDomain, got "
                f"{type(self.clock_domain).__name__}"
            )

    def _require_orderable_with(self, other: NativeTimeKey) -> None:
        """Refuse to order two keys that are not on a common axis.

        Raises:
            ContractViolationError: If ``other`` is not a NativeTimeKey.
            TimeUnitMismatchError: If the units differ.
            ClockDomainError: If the clock domains differ.
        """
        if not isinstance(other, NativeTimeKey):
            raise ContractViolationError(
                f"NativeTimeKey cannot be ordered against {type(other).__name__}"
            )
        if self.unit is not other.unit:
            raise TimeUnitMismatchError(
                f"cannot order native time across units: {self.unit.value} vs "
                f"{other.unit.value}; no implicit conversion exists"
            )
        if self.clock_domain != other.clock_domain:
            raise ClockDomainError(
                f"cannot order native time across clock domains: "
                f"{self.clock_domain.identifier!r} vs {other.clock_domain.identifier!r}; "
                "no resolver exists between domains"
            )

    def __lt__(self, other: NativeTimeKey) -> bool:
        """Return whether this key precedes ``other`` on their shared axis."""
        self._require_orderable_with(other)
        return self.value < other.value

    def __le__(self, other: NativeTimeKey) -> bool:
        """Return whether this key precedes or equals ``other``."""
        self._require_orderable_with(other)
        return self.value <= other.value

    def __gt__(self, other: NativeTimeKey) -> bool:
        """Return whether this key follows ``other`` on their shared axis."""
        self._require_orderable_with(other)
        return self.value > other.value

    def __ge__(self, other: NativeTimeKey) -> bool:
        """Return whether this key follows or equals ``other``."""
        self._require_orderable_with(other)
        return self.value >= other.value
