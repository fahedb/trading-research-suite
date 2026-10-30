"""AccessGrant-only full-integrity market-row loading.

The target manifest decides which bytes and rows are admitted.  The registry
supplies only a machine-local source directory.  Every target-listed file is
SHA-256 verified before the first Parquet decoder is constructed, while only
partitions overlapping the caller's inclusive bounds are decoded.
"""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from trading_research.core.errors import (
    ClockDomainError,
    IntegrityError,
    MarketFrameError,
    PartitionReadError,
    TimeUnitMismatchError,
    UnsupportedMarketFrameRoleError,
)
from trading_research.core.frames import MarketFrame, _make_market_frame
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.core.time import NativeTimeKey
from trading_research.data.adapters.mt5 import (
    _decode_partition,
    _DecodedPartition,
    _validate_target_schema,
)
from trading_research.data.gating import AccessGrant, SplitPolicy
from trading_research.data.manifests import ColumnPurpose, PartitionMetadata
from trading_research.data.registry import DatasetRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from typing import BinaryIO

    from trading_research.data.manifests import NormalizedManifest

__all__ = ["MarketFrameLoader"]

type _Array = npt.NDArray[np.generic]

_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _LoadBounds:
    """Validated inclusive request bounds on the target's native axis."""

    lo: int
    hi: int


@dataclass(frozen=True, slots=True)
class _VerifiedPartition:
    """One target partition whose exact physical bytes passed full integrity."""

    metadata: PartitionMetadata
    path: Path
    source: BinaryIO


class MarketFrameLoader:
    """Load immutable frames through one configured registry and split policy.

    Construction configures metadata resolution.  :meth:`load` is the sole
    public row-loading entry point and accepts only an already-issued
    :class:`AccessGrant` as its target authority.
    """

    __slots__ = ("_policy", "_registry")

    def __init__(self, *, registry: DatasetRegistry, split_policy: SplitPolicy) -> None:
        if not isinstance(registry, DatasetRegistry):
            raise TypeError("registry must be a DatasetRegistry")
        if not isinstance(split_policy, SplitPolicy):
            raise TypeError("split_policy must be a resolved SplitPolicy")
        self._registry = registry
        self._policy = split_policy

    def load(
        self,
        grant: AccessGrant,
        *,
        columns: Sequence[str] | None = None,
        lo: NativeTimeKey | None = None,
        hi: NativeTimeKey | None = None,
    ) -> MarketFrame:
        """Verify the exact target and return its requested immutable rows.

        Authorization and any required audit already happened when ``grant``
        was issued.  This method deliberately does not classify, authorize,
        audit, or issue another grant.
        """
        if type(grant) is not AccessGrant:
            raise MarketFrameError("public market-row loading requires an AccessGrant")
        try:
            dataset_ref = grant.dataset_ref
        except (AttributeError, TypeError, ValueError) as exc:
            raise MarketFrameError("AccessGrant is not structurally complete") from exc
        if not isinstance(dataset_ref, DatasetRef):
            raise MarketFrameError("AccessGrant carries no valid DatasetRef")

        candidate = self._registry.resolve(dataset_ref)
        manifest = candidate.manifest
        if manifest.role is ViewRole.RAW:
            raise UnsupportedMarketFrameRoleError(
                "RAW is a provenance and partition-source identity, not a public "
                "MarketFrame target in V1"
            )

        requested = _validated_columns(manifest, columns)
        schema = _validate_target_schema(manifest, requested)
        bounds = _validated_bounds(manifest, lo=lo, hi=hi)
        source_location = self._registry.resolve_partition_source_location(candidate)
        paths = tuple(
            (partition, _target_partition_path(source_location.directory, partition))
            for partition in manifest.partitions
        )

        # This is intentionally the last metadata-only operation before the
        # first partition stat/open.  Moving it below `_verify_all` is a gate
        # bypass and is covered by ordering tests.
        grant.require_matches(
            registered_manifest=candidate,
            split_policy_identity=self._policy.identity,
        )

        with ExitStack() as stack:
            verified = _verify_all(paths, stack)
            key_parts: list[_Array] = []
            value_parts: dict[str, list[_Array]] = {name: [] for name in requested}
            for item in verified:
                partition = item.metadata
                if partition.last_key < bounds.lo or partition.first_key > bounds.hi:
                    continue
                decoded = _decode_partition(
                    item.source,
                    path_label=str(item.path),
                    value_columns=requested,
                    admitted_first_key=partition.first_key,
                    admitted_last_key=partition.last_key,
                )
                _require_unchanged_after_decode(item)
                admitted_keys, admitted_values = _validated_admitted_slice(
                    decoded,
                    partition=partition,
                    expected_schema=schema,
                )
                left = int(np.searchsorted(admitted_keys, bounds.lo, side="left"))
                right = int(np.searchsorted(admitted_keys, bounds.hi, side="right"))
                key_parts.append(admitted_keys[left:right])
                for name in requested:
                    value_parts[name].append(admitted_values[name][left:right])

            keys = _assemble(key_parts, np.dtype("int64"))
            values = {
                name: _assemble(parts, schema[name]) for name, parts in value_parts.items()
            }

        return _make_market_frame(
            dataset_ref=dataset_ref,
            clock_domain=manifest.clock_domain,
            unit=manifest.time_unit,
            schema={name: dtype.name for name, dtype in schema.items()},
            keys=keys,
            columns=values,
            admitted_lo=bounds.lo,
            admitted_hi=bounds.hi,
        )


def _validated_columns(
    manifest: NormalizedManifest,
    columns: Sequence[str] | None,
) -> tuple[str, ...]:
    """Return unique requested value columns, or refuse before partition I/O."""
    values = tuple(
        column.name for column in manifest.columns if column.purpose is ColumnPurpose.VALUE
    )
    if columns is None:
        return values
    if isinstance(columns, str):
        raise MarketFrameError("columns must be a sequence of names, not one string")
    requested = tuple(columns)
    if any(not isinstance(name, str) or not name for name in requested):
        raise MarketFrameError("every requested column name must be a nonempty string")
    duplicates = sorted({name for name in requested if requested.count(name) > 1})
    if duplicates:
        raise MarketFrameError(f"duplicate requested columns: {duplicates}")
    unavailable = [name for name in requested if name not in values]
    if unavailable:
        raise MarketFrameError(
            f"columns are unavailable as MarketFrame values: {unavailable}; available: {values}"
        )
    return requested


def _validated_bounds(
    manifest: NormalizedManifest,
    *,
    lo: NativeTimeKey | None,
    hi: NativeTimeKey | None,
) -> _LoadBounds:
    """Validate optional inclusive bounds without clipping or conversion."""
    admitted_lo = manifest.partitions[0].first_key
    admitted_hi = manifest.partitions[-1].last_key
    for label, bound in (("lo", lo), ("hi", hi)):
        if bound is None:
            continue
        if not isinstance(bound, NativeTimeKey):
            raise MarketFrameError(f"{label} must be a NativeTimeKey")
        if bound.clock_domain != manifest.clock_domain:
            raise ClockDomainError(
                f"{label} uses clock domain {bound.clock_domain.identifier!r}; target uses "
                f"{manifest.clock_domain.identifier!r}"
            )
        if bound.unit is not manifest.time_unit:
            raise TimeUnitMismatchError(
                f"{label} uses {bound.unit.value!r}; target uses {manifest.time_unit.value!r}"
            )

    lower = admitted_lo if lo is None else lo.value
    upper = admitted_hi if hi is None else hi.value
    if lower > upper:
        raise MarketFrameError(f"inclusive bounds are inverted: {lower} > {upper}")
    if lower < admitted_lo or lower > admitted_hi:
        raise MarketFrameError(
            f"lower bound {lower} is outside target admission [{admitted_lo}, {admitted_hi}]"
        )
    if upper < admitted_lo or upper > admitted_hi:
        raise MarketFrameError(
            f"upper bound {upper} is outside target admission [{admitted_lo}, {admitted_hi}]"
        )
    return _LoadBounds(lo=lower, hi=upper)


def _target_partition_path(directory: Path, partition: PartitionMetadata) -> Path:
    """Join one target-authoritative bare filename without discovery."""
    path = directory / partition.filename
    if path.parent != directory:
        raise IntegrityError(
            f"partition {partition.key!r} filename escapes its resolved source directory"
        )
    return path


def _open_partition_for_integrity(path: Path) -> BinaryIO:
    """Open one non-symlink regular file without following a final symlink."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise IntegrityError(f"cannot safely open target partition {path}: {exc}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise IntegrityError(f"target partition is not a regular file: {path}")
        return os.fdopen(descriptor, "rb", closefd=True)
    except BaseException:
        os.close(descriptor)
        raise


def _partition_size(source: BinaryIO) -> int:
    """Return the size of the already-open regular file."""
    try:
        return os.fstat(source.fileno()).st_size
    except OSError as exc:
        raise IntegrityError(f"cannot stat open partition: {exc}") from exc


def _sha256_partition(source: BinaryIO) -> str:
    """Hash an entire open partition and rewind it for any later decoder."""
    digest = hashlib.sha256()
    try:
        source.seek(0)
        while chunk := source.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
        source.seek(0)
    except OSError as exc:
        raise IntegrityError(f"cannot hash target partition: {exc}") from exc
    return digest.hexdigest()


def _verify_all(
    paths: tuple[tuple[PartitionMetadata, Path], ...],
    stack: ExitStack,
) -> tuple[_VerifiedPartition, ...]:
    """Open/stat all target files, then hash all, before returning any decoder input."""
    opened: list[_VerifiedPartition] = []
    for partition, path in paths:
        source = stack.enter_context(_open_partition_for_integrity(path))
        size = _partition_size(source)
        if partition.byte_size is not None and size != partition.byte_size:
            raise IntegrityError(
                f"partition {partition.key!r} size mismatch: target declares "
                f"{partition.byte_size}, file has {size}"
            )
        opened.append(_VerifiedPartition(metadata=partition, path=path, source=source))

    for item in opened:
        observed = _sha256_partition(item.source)
        if observed != item.metadata.sha256:
            raise IntegrityError(
                f"partition {item.metadata.key!r} SHA-256 mismatch: target pins "
                f"{item.metadata.sha256}, file hashes to {observed}"
            )
    return tuple(opened)


def _require_unchanged_after_decode(item: _VerifiedPartition) -> None:
    """Discard decoded rows if the verified inode changed during the read.

    Holding the verified descriptor defeats path replacement, but another
    process with write access can still modify that inode in place.  A second
    complete digest after materialization closes that benign-concurrency
    window: no frame is constructed unless the bytes still match the target.
    """
    observed = _sha256_partition(item.source)
    if observed != item.metadata.sha256:
        raise IntegrityError(
            f"partition {item.metadata.key!r} changed during decode: target pins "
            f"{item.metadata.sha256}, file now hashes to {observed}"
        )


def _validated_admitted_slice(
    decoded: _DecodedPartition,
    *,
    partition: PartitionMetadata,
    expected_schema: Mapping[str, np.dtype[np.generic]],
) -> tuple[_Array, Mapping[str, _Array]]:
    """Validate the complete Arrow-restricted admission before caller bounds."""
    keys = decoded.keys
    if keys.ndim != 1 or keys.dtype != np.dtype("int64"):
        raise PartitionReadError("admitted native keys must be one-dimensional exact int64")
    if tuple(decoded.columns) != tuple(expected_schema):
        raise PartitionReadError(
            "decoded value columns do not exactly match the requested target schema"
        )
    admitted_length = len(keys)
    for name, values in decoded.columns.items():
        if values.ndim != 1 or len(values) != admitted_length:
            raise PartitionReadError(
                f"decoded column {name!r} length/shape disagrees with admitted keys"
            )
        if values.dtype != expected_schema[name]:
            raise PartitionReadError(
                f"decoded column {name!r} has dtype {values.dtype}; "
                f"expected {expected_schema[name]}"
            )

    if len(keys) != partition.rows:
        raise PartitionReadError(
            f"partition {partition.key!r} admits {len(keys)} decoded rows; "
            f"target declares {partition.rows}"
        )
    if len(keys) == 0:
        raise PartitionReadError(f"partition {partition.key!r} admitted slice is empty")
    if int(keys[0]) != partition.first_key:
        raise PartitionReadError(
            f"partition {partition.key!r} first admitted key {int(keys[0])} "
            f"differs from target {partition.first_key}"
        )
    if int(keys[-1]) != partition.last_key:
        raise PartitionReadError(
            f"partition {partition.key!r} last admitted key {int(keys[-1])} "
            f"differs from target {partition.last_key}"
        )
    if len(keys) > 1 and not bool(np.all(np.greater(keys[1:], keys[:-1]))):
        raise PartitionReadError(
            f"partition {partition.key!r} admitted native keys are not strictly increasing"
        )
    return keys, decoded.columns


def _assemble(parts: list[_Array], dtype: np.dtype[np.generic]) -> _Array:
    """Assemble selected request slices without casting."""
    if not parts:
        return np.empty(0, dtype=dtype)
    values = parts[0] if len(parts) == 1 else np.concatenate(parts)
    if values.dtype != dtype:
        raise PartitionReadError(
            f"assembled column dtype {values.dtype} differs from required {dtype}"
        )
    return values
