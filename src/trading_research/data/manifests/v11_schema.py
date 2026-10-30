"""Wire schema for generated view manifests, format 1.1.

The legacy formats are immutable artefacts produced elsewhere: the platform
reads them tolerantly because it cannot change them. Format 1.1 is the
opposite — the platform generates it, so anything unexpected in one is a bug
in the generator or an edit by hand, and both should fail loudly. Hence
``extra="forbid"`` throughout and required fields with no defaults.

Two things are deliberately impossible to write into a 1.1 file:

* **A machine-local path.** Lineage (``parent``, ``partition_source``) is
  named by ``(dataset_id, manifest_sha256)`` only, and a partition names a
  bare ``filename``. Where those identities live on a particular machine is
  the registry's business; relocating a dataset must never change what a
  view *is*, and a path inside the hashed bytes would do exactly that.
* **An authorization claim.** There is no ``gated`` field. Whether a view is
  gated is derived, at access time, from its admitted range and the
  versioned split policy — a stored boolean would be a second source of
  truth about the one question that must have exactly one.

These models describe the file on disk. They are parsed once at the boundary
and converted into
:class:`~trading_research.data.manifests.models.NormalizedManifest`; nothing
above the reader consumes them.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ViewManifestV11",
]

_SHA256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]

# Only the working split views may be returned by ordinary discovery. Every
# other role — including OOS — must be asked for by name (ADR-0014).
_DISCOVERABLE_ROLES = frozenset({"train", "validation"})


class _Strict(BaseModel):
    """Base for every 1.1 fragment: frozen, and unknown fields are errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class NativeRangeV11(_Strict):
    """An inclusive native-key range."""

    start: int
    end: int

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        """Reject an inverted range."""
        if self.end < self.start:
            raise ValueError(f"native range is inverted: start {self.start} > end {self.end}")
        return self


class ColumnV11(_Strict):
    """One declared column, with its type and its purpose."""

    name: str = Field(min_length=1)
    dtype: str = Field(min_length=1)
    purpose: Literal["time_key", "value", "display_only"]


class LineageRefV11(_Strict):
    """A lineage reference: canonical identity, and nothing else.

    Used for both ``parent`` and ``partition_source``. Deliberately carries
    no path — the registry resolves the identity to a machine-local location
    through its configured roots. Identity says *what*; registry
    configuration says *where*.
    """

    dataset_id: str = Field(min_length=1)
    manifest_sha256: _SHA256


class PartitionV11(_Strict):
    """One admitted partition, described by its **admitted** span.

    ``first_key`` and ``last_key`` are the first and last actual observed row
    keys **this view admits from that partition** — not the physical
    first/last keys of the underlying file. ``rows`` counts admitted rows,
    not physical rows. For a whole-partition view the two readings coincide;
    for a row-bounded view (SNAPSHOT, LOCKBOX) the physical file legitimately
    extends beyond ``last_key``, and nothing here constrains it.

    ``filename`` is a bare file name resolved against the location the
    registry derives from ``partition_source`` — never a path.
    """

    key: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sha256: _SHA256
    rows: int = Field(ge=0)
    first_key: int
    last_key: int
    bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        """Reject path-bearing filenames and inverted admitted spans."""
        if (
            "/" in self.filename
            or "\\" in self.filename
            or ".." in self.filename
            or self.filename.startswith(("~", "-"))
        ):
            raise ValueError(
                f"partition filename {self.filename!r} must be a bare file name: "
                "no separators, no traversal, no absolute or home-relative paths"
            )
        if self.last_key < self.first_key:
            raise ValueError(
                f"partition {self.key!r} admitted span is inverted: "
                f"first {self.first_key} > last {self.last_key}"
            )
        return self


class SplitPolicyRefV11(_Strict):
    """Which exact split-policy artifact produced this view's boundaries.

    This — not the admitted range — is where the *intended* research
    boundary lives. The admitted range records what was actually observed.
    The digest pins exact policy bytes rather than trusting a mutable version
    label alone.
    """

    adr: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_sha256: _SHA256


class SnapshotBindingV11(_Strict):
    """How a row-bounded view's upper boundary was bound.

    Present only where a boundary was resolved by scanning observed keys, so
    that the procedure, its result and its audit trail travel with the view.
    """

    policy_cap_encoded: str = Field(min_length=1)
    policy_cap_key: int
    bound_key: int
    method: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _bound_within_cap(self) -> Self:
        """Reject a bound that exceeds the policy cap it was derived from."""
        if self.bound_key > self.policy_cap_key:
            raise ValueError(
                f"bound_key {self.bound_key} exceeds policy_cap_key {self.policy_cap_key}"
            )
        return self


class GeneratorV11(_Strict):
    """Provenance of the tool that wrote this manifest."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    platform_git_commit: str = Field(min_length=1)


class ViewManifestV11(_Strict):
    """A generated view manifest, format 1.1.

    Note what is absent, and why:

    * ``context_start`` / ``eval_start`` / ``eval_end`` — those describe a
      particular evaluation request, not the view itself; recording them here
      would freeze one experiment's choice into an integrity artefact that
      many experiments share.
    * ``gated`` — effective gating is derived from the admitted range and
      the versioned split policy at access time (Slice 1C). A stored boolean
      would be a second, driftable source of authorization truth.
    * any ``relative_path`` — lineage is identity, location is registry
      configuration. A path inside the hashed bytes would make a purely
      physical relocation change the view's canonical identity.

    ``admitted_native_range`` records the **actual observed** admitted keys:
    its start is the first admitted row's key and its end the last, even
    where the split policy's calendar boundary lies earlier or later. The
    policy intent is recorded separately in ``split_policy_ref`` and, where a
    boundary was bound by scanning, in ``snapshot_binding``.
    """

    manifest_format_version: Literal["1.1"]
    view_id: str = Field(min_length=1)
    role: Literal["raw", "curated", "snapshot", "train", "validation", "oos", "lockbox"]
    discovery_default: bool
    adapter_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    clock_domain: str = Field(min_length=1)
    time_unit: Literal["seconds", "milliseconds", "microseconds", "nanoseconds"]
    calendar_time_policy: Literal["native-wallclock-v1"]
    columns: tuple[ColumnV11, ...] = Field(min_length=1)
    admitted_partitions: tuple[PartitionV11, ...] = Field(min_length=1)
    admitted_native_range: NativeRangeV11
    row_count_expected: int = Field(ge=0)
    parent: LineageRefV11
    partition_source: LineageRefV11
    split_policy_ref: SplitPolicyRefV11
    generated_at_utc: str = Field(min_length=1)
    generator: GeneratorV11
    snapshot_binding: SnapshotBindingV11 | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _only_working_views_are_discoverable(self) -> Self:
        """Refuse a manifest that would make gated data ambiently reachable.

        The discovery default is the platform's first line of defence, and a
        generator bug that flipped it on a lockbox view would disable that
        defence silently. Checked here so a bad file cannot be loaded at all.
        """
        if self.discovery_default and self.role not in _DISCOVERABLE_ROLES:
            raise ValueError(
                f"role {self.role!r} must not be discovery_default; only "
                f"{sorted(_DISCOVERABLE_ROLES)} may be"
            )
        return self
