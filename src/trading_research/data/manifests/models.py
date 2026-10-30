"""One internal shape for every manifest format the platform can read.

Three source formats exist today and more will follow. Rather than teach the
registry, the loader and every later stage to speak all of them, each reader
normalizes into the types here — so a format change is a reader change, not a
platform change.

What these types deliberately do **not** carry is location. A manifest's
canonical identity is ``(dataset_id, manifest_sha256)``; where its bytes
happen to sit is resolver configuration that belongs to the registry, not to
the metadata. Copying a manifest to another registered root must not change
what it is.

Partition location follows the same rule: a partition names a bare
``filename``, and *whose* files those are is said by identity — the v1.1
``partition_source``, or a legacy format's own convention (beside the
manifest for raw; under the declared parent for the curated view). The one
place a source-declared relative path survives is
:class:`DeclaredParent.relative_path`, preserved from the immutable legacy
curated format as provenance; the registry checks it cannot escape the
configured roots, and it never participates in identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from trading_research.core.errors import MalformedManifestError
from trading_research.core.identity import DatasetIdentity

if TYPE_CHECKING:
    from trading_research.core.identity import ViewRole
    from trading_research.core.time import CalendarTimePolicy, ClockDomain, TimeUnit

__all__ = [
    "ColumnPurpose",
    "DeclaredParent",
    "ManifestColumn",
    "ManifestFormat",
    "NativeKeyRange",
    "NormalizedManifest",
    "PartitionMetadata",
    "SplitPolicyRef",
]

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class ManifestFormat(StrEnum):
    """A source format this platform knows how to read.

    Recorded on every normalized manifest so a diagnostic can say which
    reader produced it without re-reading the file.
    """

    MT5_RAW_V1 = "mt5-raw-1.0.0"
    CURATED_VIEW_V1 = "curated-view-1.0.0"
    VIEW_MANIFEST_V1_1 = "view-manifest-1.1"


class ColumnPurpose(StrEnum):
    """What a declared column is for.

    The distinction exists because not every column a dataset stores belongs
    in a research frame. A decoded-timestamp column, for instance, is a
    convenience for humans reading the file and a standing invitation to
    treat it as civil time; marking it display-only keeps it out of the
    computational path by construction rather than by convention.
    """

    TIME_KEY = "time_key"
    VALUE = "value"
    DISPLAY_ONLY = "display_only"


@dataclass(frozen=True, slots=True)
class ManifestColumn:
    """One column a manifest declares.

    ``dtype`` is optional because the legacy formats name their columns
    without typing them. Inventing a type here would be a guess wearing a
    schema's clothes; later slices verify types against the data instead.
    """

    name: str
    purpose: ColumnPurpose
    dtype: str | None = None


@dataclass(frozen=True, slots=True)
class NativeKeyRange:
    """An inclusive range of native time keys.

    Bare integers rather than :class:`~trading_research.core.time.NativeTimeKey`
    values: a range is meaningful only within one manifest, which already
    carries the unit and clock domain, and repeating those tags per bound
    invites two halves of one range disagreeing about their axis.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        """Reject inverted ranges.

        Raises:
            MalformedManifestError: If ``end`` precedes ``start``.
        """
        if self.end < self.start:
            raise MalformedManifestError(
                f"native key range is inverted: start {self.start} > end {self.end}"
            )


@dataclass(frozen=True, slots=True)
class PartitionMetadata:
    """One admitted partition, described by its **admitted** span.

    This is metadata only. Nothing here opens the file: the digest is what
    the manifest claims, and verifying that claim against the bytes is the
    integrity step in a later slice.

    ``first_key``, ``last_key`` and ``rows`` all describe what the view
    **admits** from this partition — the first and last actual observed
    admitted row keys, and the count of admitted rows. They are not claims
    about the physical file: a row-bounded view's source partition
    legitimately extends past ``last_key``, and nothing here constrains it.
    For a whole-partition view the admitted and physical readings coincide.

    Attributes:
        key: The manifest's own label for the partition (a year, typically).
        filename: Bare file name — no separators, no traversal. Where it
            lives is resolved by the registry from the manifest's partition
            source, never from a path stored here.
        rows: Number of rows this view admits from the partition.
        first_key: First admitted observed native time key.
        last_key: Last admitted observed native time key.
        sha256: Digest the manifest pins for the (whole) file.
        byte_size: File size where the format records one.
    """

    key: str
    filename: str
    rows: int
    first_key: int
    last_key: int
    sha256: str
    byte_size: int | None = None

    def __post_init__(self) -> None:
        """Reject internally inconsistent partition metadata.

        Raises:
            MalformedManifestError: If the filename carries a path, the
                admitted span is inverted, or the row count is negative.
        """
        if (
            "/" in self.filename
            or "\\" in self.filename
            or ".." in self.filename
            or self.filename.startswith(("~", "-"))
            or not self.filename
        ):
            raise MalformedManifestError(
                f"partition {self.key!r} filename {self.filename!r} must be a bare "
                "file name: no separators, no traversal"
            )
        if self.last_key < self.first_key:
            raise MalformedManifestError(
                f"partition {self.key!r} has an inverted admitted span: "
                f"first {self.first_key} > last {self.last_key}"
            )
        if self.rows < 0:
            raise MalformedManifestError(f"partition {self.key!r} declares {self.rows} rows")


@dataclass(frozen=True, slots=True)
class DeclaredParent:
    """The parent a manifest *claims*, exactly as written.

    Kept separate from the identity the registry resolves, because the two
    are different kinds of fact. ``manifest_sha256`` is ``None`` for the
    legacy formats, which reference a parent by name without pinning its
    manifest — recording ``None`` says so honestly instead of implying a
    guarantee that was never made.
    """

    dataset_id: str
    relative_path: str | None = None
    manifest_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SplitPolicyRef:
    """Exact versioned split-policy artifact referenced by a generated view.

    The ADR and policy version are the logical policy identity. The digest
    pins the exact machine-readable record so that editing bytes under an
    existing version cannot silently change how an existing view is gated.
    """

    adr: str
    policy_version: str
    policy_sha256: str

    def __post_init__(self) -> None:
        """Validate the logical reference and normalize its exact-byte digest.

        Raises:
            MalformedManifestError: If a logical component is empty or the
                policy digest is not exactly 64 hexadecimal characters.
        """
        if not isinstance(self.adr, str) or not self.adr:
            raise MalformedManifestError("split policy ADR must be a nonempty string")
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise MalformedManifestError(
                "split policy version must be a nonempty string"
            )
        if (
            not isinstance(self.policy_sha256, str)
            or _SHA256_HEX.fullmatch(self.policy_sha256) is None
        ):
            raise MalformedManifestError(
                "split policy SHA-256 must be exactly 64 hexadecimal characters"
            )
        object.__setattr__(self, "policy_sha256", self.policy_sha256.lower())


@dataclass(frozen=True, slots=True)
class NormalizedManifest:
    """What one manifest says, in the platform's own vocabulary.

    Attributes:
        dataset_id: Canonical id of the dataset or view.
        manifest_sha256: Digest over the manifest's exact bytes, computed by
            the reader — not a value the manifest states about itself.
        role: The role the manifest declares.
        source_format: Which format the reader recognised.
        discovery_default: Whether ordinary discovery may return this. False
            for everything except the working split views.
        adapter_id: Which data adapter can read this dataset's partitions.
        symbol: Instrument the data describes.
        timeframe: Bar interval label as the source states it.
        clock_domain: Clock the native keys came from.
        time_unit: Resolution of the native keys.
        calendar_time_policy: Policy under which any calendar-shaped
            boundary in this manifest was expressed, where one applies.
        columns: Declared columns, in declaration order.
        partitions: Admitted partitions, ordered by first admitted key.
        admitted_native_range: The range of **actual observed** admitted row
            keys — its start is the first admitted row's key and its end the
            last. It is not the split policy's calendar boundary; that intent
            lives with the policy reference, not here. Every V1 reader must
            resolve this concrete range from admitted partition endpoints.
        expected_row_count: Rows the manifest admits. Must equal the sum of
            the partitions' admitted rows — validated, so it cannot drift
            into a second source of truth.
        declared_parent: The parent as claimed by the source, if any.
        partition_source: Identity of the dataset whose partition files this
            manifest references (v1.1). ``None`` for the legacy formats,
            whose file location follows their own convention: beside the
            manifest for raw, under the declared parent for the curated
            view.
        split_policy_ref: Exact split-policy artifact referenced by a v1.1
            generated view. ``None`` for immutable legacy formats, which had
            no field capable of recording one; their access caller must
            supply the configured policy explicitly.
    """

    dataset_id: str
    manifest_sha256: str
    role: ViewRole
    source_format: ManifestFormat
    discovery_default: bool
    adapter_id: str
    symbol: str
    timeframe: str
    clock_domain: ClockDomain
    time_unit: TimeUnit
    calendar_time_policy: CalendarTimePolicy | None
    columns: tuple[ManifestColumn, ...]
    partitions: tuple[PartitionMetadata, ...]
    admitted_native_range: NativeKeyRange
    expected_row_count: int
    declared_parent: DeclaredParent | None = None
    partition_source: DatasetIdentity | None = None
    split_policy_ref: SplitPolicyRef | None = None

    def __post_init__(self) -> None:
        """Check the manifest is coherent with itself.

        Raises:
            MalformedManifestError: If columns are duplicated or lack exactly
                one time key; if partitions are unordered or overlap; if the
                admitted range does not equal the observed admitted
                endpoints; or if the declared row count disagrees with the
                partitions.
        """
        self._validate_columns()
        self._validate_partitions()
        self._validate_row_count()

    def _validate_columns(self) -> None:
        """Require unique names and exactly one time-key column.

        Raises:
            MalformedManifestError: If names collide, or the number of
                time-key columns is not exactly one.
        """
        names = [column.name for column in self.columns]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise MalformedManifestError(
                f"{self.dataset_id}: duplicate column names {duplicates}"
            )
        time_keys = [c.name for c in self.columns if c.purpose is ColumnPurpose.TIME_KEY]
        if len(time_keys) != 1:
            raise MalformedManifestError(
                f"{self.dataset_id}: expected exactly one time-key column, got {time_keys}"
            )

    def _validate_partitions(self) -> None:
        """Require ordered, non-overlapping spans matching the required range.

        The view-level range must *equal* the observed admitted endpoints —
        not merely contain them. The range records what
        was actually admitted; a wider range would smuggle a calendar
        intention into a field that documents observations. The intended
        policy boundary has its own home (the split-policy reference and,
        where bound by scanning, the snapshot-binding provenance).

        Raises:
            MalformedManifestError: If there are no partitions, if two
                admitted spans overlap or are out of order, or if the
                admitted range is absent/invalid or does not equal the
                observed endpoints.
        """
        if not self.partitions:
            raise MalformedManifestError(f"{self.dataset_id}: manifest admits no partitions")
        previous: PartitionMetadata | None = None
        for partition in self.partitions:
            if previous is not None and partition.first_key <= previous.last_key:
                raise MalformedManifestError(
                    f"{self.dataset_id}: partitions {previous.key!r} and {partition.key!r} "
                    "have overlapping or out-of-order admitted spans"
                )
            previous = partition
        observed_start = self.partitions[0].first_key
        observed_end = self.partitions[-1].last_key
        declared = self.admitted_native_range
        if not isinstance(declared, NativeKeyRange):
            raise MalformedManifestError(
                f"{self.dataset_id}: admitted_native_range must be a concrete "
                "NativeKeyRange in V1"
            )
        if (declared.start, declared.end) != (observed_start, observed_end):
            raise MalformedManifestError(
                f"{self.dataset_id}: admitted_native_range "
                f"[{declared.start}, {declared.end}] must equal the observed admitted "
                f"endpoints [{observed_start}, {observed_end}]; the range records actual "
                "observed rows, not a calendar-policy boundary"
            )

    def _validate_row_count(self) -> None:
        """Require the declared row count to equal the partitions' sum.

        Raises:
            MalformedManifestError: On any disagreement — an unvalidated
                declared count would be a second source of truth about how
                much data a view admits.
        """
        partition_total = sum(partition.rows for partition in self.partitions)
        if self.expected_row_count != partition_total:
            raise MalformedManifestError(
                f"{self.dataset_id}: expected_row_count {self.expected_row_count} does not "
                f"equal the sum of admitted partition rows {partition_total}"
            )

    @property
    def identity(self) -> DatasetIdentity:
        """The canonical identity of this manifest. Role and location absent."""
        return DatasetIdentity(
            dataset_id=self.dataset_id, manifest_sha256=self.manifest_sha256
        )

    @property
    def time_key_column(self) -> str:
        """Name of the column carrying native time keys.

        Lets later stages find the time axis without any layer above the
        adapter knowing what a particular vendor calls it.
        """
        return next(c.name for c in self.columns if c.purpose is ColumnPurpose.TIME_KEY)

    @property
    def frame_columns(self) -> tuple[str, ...]:
        """Columns admissible into a research frame, in declaration order.

        Display-only columns are excluded here rather than filtered later, so
        that "which columns may be computed on" has one answer.
        """
        return tuple(
            c.name for c in self.columns if c.purpose is not ColumnPurpose.DISPLAY_ONLY
        )
