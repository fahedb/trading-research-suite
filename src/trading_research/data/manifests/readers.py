"""Readers that turn each supported manifest format into the normalized model.

Format selection is by explicit marker, never by shape-sniffing or filename.
An unrecognised marker is refused: a manifest whose format we are guessing at
is a manifest whose fields we are guessing at, and the fields decide which
bytes count as research data.

This module is where vendor vocabulary lives. ``mt5_time_raw``,
``trade_server`` and the rest appear here and nowhere above — an architecture
test keeps ``core`` free of them.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from trading_research.core.errors import (
    MalformedManifestError,
    UnsupportedManifestFormatError,
)
from trading_research.core.identity import DatasetIdentity, ViewRole
from trading_research.core.time import CalendarTimePolicy, ClockDomain, TimeUnit
from trading_research.data.manifests.models import (
    ColumnPurpose,
    DeclaredParent,
    ManifestColumn,
    ManifestFormat,
    NativeKeyRange,
    NormalizedManifest,
    PartitionMetadata,
    SplitPolicyRef,
)
from trading_research.data.manifests.v11_schema import ViewManifestV11

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "MANIFEST_FILENAME",
    "read_manifest",
]

# The one filename the registry searches for. Discovering metadata by name is
# legal; discovering *data* by globbing for partition files is not, and an
# architecture test enforces the difference.
MANIFEST_FILENAME = "manifest.json"

# Exporter versions whose field meanings this platform has actually checked.
_SUPPORTED_RAW_BUILDER_VERSIONS = frozenset({"1.0.0"})
_SUPPORTED_CURATION_VERSIONS = frozenset({"1.0.0"})

# The adapter that can read partitions produced by the MT5 exporter.
_MT5_ADAPTER_ID = "mt5"

# The MT5 exporter writes the broker's wall-clock reading through the Unix
# epoch convention: the integer is seconds, but its zero point is the broker's
# clock, not any reference clock. That is a property of this exporter version,
# which is why the version is checked before the unit is asserted.
_MT5_TIME_KEY_COLUMN = "mt5_time_raw"
_MT5_DECODED_COLUMN = "timestamp_mt5_encoded"
_MT5_TIME_UNIT = TimeUnit.SECONDS
_MT5_REQUIRED_TIMESTAMP_POLICY = "raw_only"
_MT5_EXPECTED_CLOCK_CLASSIFICATION = "BROKER_WALL_CLOCK_ENCODED_AS_EPOCH"

# Columns the MT5 exporter emits, when a manifest does not list them itself.
_MT5_DEFAULT_COLUMNS = (
    "mt5_time_raw",
    "timestamp_mt5_encoded",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)


def read_manifest(path: Path) -> NormalizedManifest:
    """Read one manifest file and normalize it.

    Args:
        path: The manifest file. Only this file is read; no partition file is
            opened and no directory is scanned.

    Returns:
        The normalized manifest, with its digest computed over the exact bytes
        on disk.

    Raises:
        MalformedManifestError: If the file is unreadable or is not a JSON
            object.
        UnsupportedManifestFormatError: If no reader recognises its format.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise MalformedManifestError(f"cannot read manifest {path}: {exc}") from exc

    digest = hashlib.sha256(raw_bytes).hexdigest()

    try:
        document = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise MalformedManifestError(f"manifest {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise MalformedManifestError(
            f"manifest {path} must be a JSON object, got {type(document).__name__}"
        )

    if document.get("manifest_format_version") is not None:
        return _read_view_manifest_v11(document, digest, path)
    if document.get("kind") == "curated_view":
        return _read_curated_v1(document, digest, path)
    if document.get("source") == "MetaTrader5":
        return _read_mt5_raw_v1(document, digest, path)
    raise UnsupportedManifestFormatError(
        f"manifest {path} declares no recognised format marker; expected one of "
        "'manifest_format_version', kind='curated_view', or source='MetaTrader5'"
    )


# ------------------------------------------------------------------ helpers


def _require(document: Mapping[str, Any], key: str, path: Path) -> Any:  # noqa: ANN401
    """Return a required field.

    Raises:
        MalformedManifestError: If the field is absent.
    """
    if key not in document:
        raise MalformedManifestError(f"manifest {path} is missing required field {key!r}")
    return document[key]


def _require_str(document: Mapping[str, Any], key: str, path: Path) -> str:
    """Return a required string field.

    Raises:
        MalformedManifestError: If the field is absent or not a string.
    """
    value = _require(document, key, path)
    if not isinstance(value, str):
        raise MalformedManifestError(
            f"manifest {path} field {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def _require_int(document: Mapping[str, Any], key: str, path: Path) -> int:
    """Return a required integer field.

    Raises:
        MalformedManifestError: If the field is absent, is a bool, or is not
            an integer.
    """
    value = _require(document, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedManifestError(
            f"manifest {path} field {key!r} must be an integer, got {type(value).__name__}"
        )
    return value


def _require_list(document: Mapping[str, Any], key: str, path: Path) -> list[Any]:
    """Return a required list field.

    Raises:
        MalformedManifestError: If the field is absent or not a list.
    """
    value = _require(document, key, path)
    if not isinstance(value, list):
        raise MalformedManifestError(
            f"manifest {path} field {key!r} must be a list, got {type(value).__name__}"
        )
    return value


def _mt5_columns(names: tuple[str, ...], path: Path) -> tuple[ManifestColumn, ...]:
    """Assign a purpose to each column the MT5 exporter declares.

    The decoded-timestamp column is marked display-only: it is a mechanical
    re-encoding of the time key that reads like civil time, and letting it
    into a frame is the most direct route to someone treating broker
    wall-clock as a reference clock.

    Raises:
        MalformedManifestError: If the time-key column is absent.
    """
    if _MT5_TIME_KEY_COLUMN not in names:
        raise MalformedManifestError(
            f"manifest {path} declares no {_MT5_TIME_KEY_COLUMN!r} column"
        )
    columns: list[ManifestColumn] = []
    for name in names:
        if name == _MT5_TIME_KEY_COLUMN:
            purpose = ColumnPurpose.TIME_KEY
        elif name == _MT5_DECODED_COLUMN:
            purpose = ColumnPurpose.DISPLAY_ONLY
        else:
            purpose = ColumnPurpose.VALUE
        columns.append(ManifestColumn(name=name, purpose=purpose))
    return tuple(columns)


def _mt5_clock_domain(document: Mapping[str, Any], path: Path) -> ClockDomain:
    """Derive the clock domain from the exporter's own description of its clock.

    Derived rather than hardcoded so that a different broker or server yields
    a different domain automatically — which is what makes cross-domain
    comparison refuse instead of silently mixing two venues' clocks.

    Raises:
        MalformedManifestError: If the timestamp policy is not the raw
            passthrough this adapter understands.
    """
    policy = _require_str(document, "timestamp_policy", path)
    if policy != _MT5_REQUIRED_TIMESTAMP_POLICY:
        raise MalformedManifestError(
            f"manifest {path} declares timestamp_policy {policy!r}; this adapter reads only "
            f"{_MT5_REQUIRED_TIMESTAMP_POLICY!r}, where the stored integer is untouched"
        )
    trade_server = _require_str(document, "trade_server", path)
    return ClockDomain(f"{_MT5_ADAPTER_ID}.{trade_server.lower()}.wallclock")


def _check_mt5_builder_version(document: Mapping[str, Any], path: Path) -> None:
    """Refuse exporter versions whose field meanings have not been checked.

    Raises:
        UnsupportedManifestFormatError: If the builder version is unsupported.
    """
    version = _require_str(document, "dataset_builder_version", path)
    if version not in _SUPPORTED_RAW_BUILDER_VERSIONS:
        raise UnsupportedManifestFormatError(
            f"manifest {path} was built by dataset_builder_version {version!r}; "
            f"supported: {sorted(_SUPPORTED_RAW_BUILDER_VERSIONS)}"
        )


# ------------------------------------------------------------- raw (MT5 v1)


def _read_mt5_raw_v1(
    document: Mapping[str, Any], digest: str, path: Path
) -> NormalizedManifest:
    """Normalize the MT5 exporter's raw dataset manifest.

    The raw dataset spans every partition the exporter produced, including
    periods excluded from research and the live edge, so it is never
    discoverable by default.

    Raises:
        MalformedManifestError: If required fields are absent or malformed.
        UnsupportedManifestFormatError: If the builder version is unsupported.
    """
    _check_mt5_builder_version(document, path)

    clock_classification = document.get("terminal_time_model", {}).get("classification")
    if clock_classification != _MT5_EXPECTED_CLOCK_CLASSIFICATION:
        raise MalformedManifestError(
            f"manifest {path} classifies its clock as {clock_classification!r}; this adapter "
            f"reads only {_MT5_EXPECTED_CLOCK_CLASSIFICATION!r}"
        )

    partitions = tuple(
        _mt5_partition(entry, path, key_field="year")
        for entry in _require_list(document, "partitions", path)
    )
    span = _require(document, "range", path)
    if not isinstance(span, dict):
        raise MalformedManifestError(f"manifest {path} field 'range' must be an object")

    return NormalizedManifest(
        dataset_id=_require_str(document, "dataset_id", path),
        manifest_sha256=digest,
        role=ViewRole.RAW,
        source_format=ManifestFormat.MT5_RAW_V1,
        discovery_default=False,
        adapter_id=_MT5_ADAPTER_ID,
        symbol=_require_str(document, "symbol", path),
        timeframe=_require_str(document, "timeframe", path),
        clock_domain=_mt5_clock_domain(document, path),
        time_unit=_MT5_TIME_UNIT,
        calendar_time_policy=None,
        columns=_mt5_columns(_MT5_DEFAULT_COLUMNS, path),
        partitions=tuple(sorted(partitions, key=lambda p: p.first_key)),
        admitted_native_range=NativeKeyRange(
            start=_require_int(span, "first_raw", path),
            end=_require_int(span, "last_raw", path),
        ),
        expected_row_count=_require_int(document, "total_row_count", path),
        declared_parent=None,
        # A raw dataset's partitions are its own, sitting beside its
        # manifest — there is no separate source identity to name.
        partition_source=None,
        # The immutable legacy format had no split-policy reference. Access
        # must bind it to an explicitly configured policy in Slice 1C.
        split_policy_ref=None,
    )


def _mt5_partition(
    entry: Any,  # noqa: ANN401
    path: Path,
    *,
    key_field: str,
) -> PartitionMetadata:
    """Normalize one partition entry from an MT5-lineage manifest.

    Legacy views admit whole partitions, so the manifest's physical
    first/last keys and row counts *are* the admitted span and admitted
    rows — the two readings coincide.

    Raises:
        MalformedManifestError: If the entry is not an object or is missing
            required fields.
    """
    if not isinstance(entry, dict):
        raise MalformedManifestError(
            f"manifest {path} partition entry must be an object, got {type(entry).__name__}"
        )
    byte_size = entry.get("bytes")
    return PartitionMetadata(
        key=str(_require(entry, key_field, path)),
        filename=_require_str(entry, "filename", path),
        rows=_require_int(entry, "rows", path),
        first_key=_require_int(entry, "first_raw", path),
        last_key=_require_int(entry, "last_raw", path),
        sha256=_require_str(entry, "sha256", path),
        byte_size=byte_size if isinstance(byte_size, int) else None,
    )


# --------------------------------------------------------- curated view (v1)


def _read_curated_v1(
    document: Mapping[str, Any], digest: str, path: Path
) -> NormalizedManifest:
    """Normalize the legacy curated view manifest.

    The curated view is a pure reference: it stores no data of its own and
    points at its parent's partition files. It spans the whole usable history
    including the live edge, so it is never discoverable by default.

    Raises:
        MalformedManifestError: If required fields are absent or malformed.
        UnsupportedManifestFormatError: If the curation version is
            unsupported.
    """
    version = _require_str(document, "curation_format_version", path)
    if version not in _SUPPORTED_CURATION_VERSIONS:
        raise UnsupportedManifestFormatError(
            f"manifest {path} declares curation_format_version {version!r}; "
            f"supported: {sorted(_SUPPORTED_CURATION_VERSIONS)}"
        )

    parent_id = _require_str(document, "parent_raw_dataset_id", path)
    parent_relative = _require_str(document, "parent_raw_dataset_relative_path", path)

    partitions = tuple(
        _mt5_partition(entry, path, key_field="key")
        for entry in _require_list(document, "included_partitions", path)
    )

    return NormalizedManifest(
        dataset_id=_require_str(document, "curated_dataset_id", path),
        manifest_sha256=digest,
        role=ViewRole.CURATED,
        source_format=ManifestFormat.CURATED_VIEW_V1,
        discovery_default=False,
        adapter_id=_MT5_ADAPTER_ID,
        symbol=_require_str(document, "symbol", path),
        timeframe=_require_str(document, "timeframe", path),
        clock_domain=_mt5_clock_domain(document, path),
        time_unit=_MT5_TIME_UNIT,
        calendar_time_policy=None,
        columns=_mt5_columns(
            tuple(str(name) for name in _require_list(document, "columns", path)), path
        ),
        partitions=tuple(sorted(partitions, key=lambda p: p.first_key)),
        admitted_native_range=NativeKeyRange(
            start=_require_int(document, "first_usable_raw", path),
            end=_require_int(document, "last_usable_raw", path),
        ),
        expected_row_count=_require_int(document, "total_usable_rows", path),
        # The legacy format names its parent without pinning the parent's
        # manifest digest, and locates it by a source-declared relative path.
        # Both facts are preserved exactly as written — the None digest says
        # honestly that nothing was pinned, and the path is provenance the
        # registry checks but never treats as identity.
        declared_parent=DeclaredParent(
            dataset_id=parent_id, relative_path=parent_relative, manifest_sha256=None
        ),
        # The curated view stores no data; its partition files belong to the
        # declared parent, resolved by the registry through the parent's
        # identity. No pinned source identity exists to record here.
        partition_source=None,
        # The immutable legacy format had no split-policy reference. Access
        # must bind it to an explicitly configured policy in Slice 1C.
        split_policy_ref=None,
    )


# ------------------------------------------------------- view manifest (1.1)


def _read_view_manifest_v11(
    document: Mapping[str, Any], digest: str, path: Path
) -> NormalizedManifest:
    """Normalize a generated view manifest.

    Raises:
        UnsupportedManifestFormatError: If the declared version is not 1.1.
        MalformedManifestError: If the document fails strict validation.
    """
    declared_version = document.get("manifest_format_version")
    if declared_version != "1.1":
        raise UnsupportedManifestFormatError(
            f"manifest {path} declares manifest_format_version {declared_version!r}; "
            "supported: '1.1'"
        )
    try:
        parsed = ViewManifestV11.model_validate(document)
    except ValidationError as exc:
        raise MalformedManifestError(f"manifest {path} failed 1.1 validation: {exc}") from exc

    return NormalizedManifest(
        dataset_id=parsed.view_id,
        manifest_sha256=digest,
        role=ViewRole(parsed.role),
        source_format=ManifestFormat.VIEW_MANIFEST_V1_1,
        discovery_default=parsed.discovery_default,
        adapter_id=parsed.adapter_id,
        symbol=parsed.symbol,
        timeframe=parsed.timeframe,
        clock_domain=ClockDomain(parsed.clock_domain),
        time_unit=TimeUnit(parsed.time_unit),
        calendar_time_policy=CalendarTimePolicy(parsed.calendar_time_policy),
        columns=tuple(
            ManifestColumn(
                name=column.name, purpose=ColumnPurpose(column.purpose), dtype=column.dtype
            )
            for column in parsed.columns
        ),
        partitions=tuple(
            sorted(
                (
                    PartitionMetadata(
                        key=partition.key,
                        filename=partition.filename,
                        rows=partition.rows,
                        first_key=partition.first_key,
                        last_key=partition.last_key,
                        sha256=partition.sha256.lower(),
                        byte_size=partition.bytes,
                    )
                    for partition in parsed.admitted_partitions
                ),
                key=lambda p: p.first_key,
            )
        ),
        admitted_native_range=NativeKeyRange(
            start=parsed.admitted_native_range.start, end=parsed.admitted_native_range.end
        ),
        expected_row_count=parsed.row_count_expected,
        # 1.1 lineage is identity-only by construction: the schema has
        # nowhere to write a path, so relative_path is None by definition.
        declared_parent=DeclaredParent(
            dataset_id=parsed.parent.dataset_id,
            relative_path=None,
            manifest_sha256=parsed.parent.manifest_sha256.lower(),
        ),
        partition_source=DatasetIdentity(
            dataset_id=parsed.partition_source.dataset_id,
            manifest_sha256=parsed.partition_source.manifest_sha256,
        ),
        split_policy_ref=SplitPolicyRef(
            adr=parsed.split_policy_ref.adr,
            policy_version=parsed.split_policy_ref.policy_version,
            policy_sha256=parsed.split_policy_ref.policy_sha256,
        ),
    )
