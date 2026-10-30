"""Exception hierarchy for the research platform.

A single root exception lets callers catch everything the platform raises
without also swallowing unrelated runtime errors.
"""

from __future__ import annotations


class TradingResearchError(Exception):
    """Root of every exception raised by this platform."""


class ContractViolationError(TradingResearchError):
    """A component violated a documented contract of the core interfaces."""


class ClockDomainError(ContractViolationError):
    """Native time values from different clock domains were ordered.

    Two clock domains have no known offset between them — the platform ships
    no resolver — so asking which of two values came first is unanswerable
    rather than merely inconvenient. Refusing is the only honest response.
    """


class TimeUnitMismatchError(ContractViolationError):
    """Native time values with different units were ordered.

    Converting between units would require rescaling the raw integer, which
    V1 never does implicitly: a silent conversion is indistinguishable from
    a correct answer until it is wrong by three orders of magnitude.
    """


class FeatureContractError(ContractViolationError):
    """A columnar feature declaration or provider output is malformed."""


class FeatureRequestError(FeatureContractError):
    """A bounded columnar feature request cannot be constructed or read."""


class FeatureFrameError(FeatureContractError):
    """A computed feature frame violates its dense columnar contract."""


class ManifestError(TradingResearchError):
    """Base class for failures reading or interpreting a dataset manifest."""


class UnsupportedManifestFormatError(ManifestError):
    """A manifest declares a format or version this platform cannot read.

    Guessing at an unrecognised layout is how a field silently changes
    meaning between exporter versions, so an unknown format is refused
    rather than parsed optimistically.
    """


class MalformedManifestError(ManifestError):
    """A manifest is the right format but its content is unusable."""


class DatasetRegistryError(TradingResearchError):
    """Base class for dataset catalog failures.

    Distinct from :class:`RegistryError`, which covers the plugin registry.
    """


class RegistryRootError(DatasetRegistryError):
    """A configured registry root is missing, unusable, or not a directory."""


class ManifestLocationError(DatasetRegistryError):
    """A manifest-declared relative path escapes every configured root.

    A manifest that can name files outside the roots it was discovered under
    would make the root configuration decorative.
    """


class DuplicateDatasetIdError(DatasetRegistryError):
    """One dataset or view id was found at more than one location.

    Never resolved by preferring one copy: which one a run consumed would
    then depend on scan order, and two runs pinning the same id could have
    read different bytes.
    """


class DatasetNotFoundError(DatasetRegistryError):
    """No discovered manifest carries the requested identity or role."""


class ParentResolutionError(DatasetRegistryError):
    """A manifest's declared lineage cannot be honoured.

    Covers both the parent and the partition source: the named identity is
    missing from every configured root, duplicated, or its discovered
    manifest disagrees with a pinned digest.
    """


class DatasetRefMismatchError(ContractViolationError):
    """A DatasetRef does not describe the manifest it names.

    Covers a wrong id, a manifest whose bytes no longer hash to the pinned
    digest, and a role disagreeing with the manifest's own declaration.
    """


class SplitPolicyError(TradingResearchError):
    """Base class for failures resolving or validating split policy."""


class SplitPolicyNotFoundError(SplitPolicyError):
    """The explicitly configured split-policy record could not be read."""


class MalformedSplitPolicyError(SplitPolicyError):
    """A split-policy record has invalid JSON, fields, axes, or ranges."""


class UnsupportedSplitPolicyError(SplitPolicyError):
    """A split-policy record declares a schema the platform does not support."""


class SplitPolicyMismatchError(SplitPolicyError):
    """A requested or manifest-pinned policy differs from the resolved bytes."""


class AccessDeniedError(TradingResearchError):
    """A classifiable dataset access request failed its required ceremony."""

    def __init__(self, message: str, *, denial_code: str) -> None:
        super().__init__(message)
        self.denial_code = denial_code


class InvalidAccessReasonError(AccessDeniedError):
    """An access reason is empty, malformed, or exceeds its public bound."""


class AuditError(TradingResearchError):
    """Base class for failures persisting a write-ahead access decision."""


class AuditOpenError(AuditError):
    """The audit log could not be safely opened or created."""


class AuditLockError(AuditError):
    """The audit log's exclusive append lock could not be acquired in time."""


class AuditCorruptionError(AuditError):
    """The audit log has a malformed or torn tail and must not be extended."""


class AuditWriteError(AuditError):
    """The audit event could not be appended in one complete low-level write."""


class AuditSyncError(AuditError):
    """The appended event or first-created directory entry could not be synced."""


class AccessGrantMismatchError(ContractViolationError):
    """An access grant was presented for different data or split-policy bytes."""


class IntegrityError(TradingResearchError):
    """A target-listed data file failed its declared integrity checks."""


class PartitionReadError(TradingResearchError):
    """An admitted partition could not be decoded or matched to its schema."""


class MarketFrameError(ContractViolationError):
    """A market frame or requested frame operation violated its compact contract."""


class UnsupportedMarketFrameRoleError(MarketFrameError):
    """A dataset role is not a legal public market-frame target."""


class ViewCompositionError(ContractViolationError):
    """A bounded view or adjacent union violates its composition contract."""


class ViewBoundsError(ViewCompositionError):
    """Requested context or evaluation bounds cannot form a bounded view."""


class ViewMemberError(ViewCompositionError):
    """A proposed view member has an illegal role, identity, or authority."""


class InsufficientContextError(ViewCompositionError):
    """Requested backward context is unavailable without accepted evidence."""


class RegistryError(TradingResearchError):
    """Base class for plugin registry failures."""


class UnknownComponentError(RegistryError):
    """A component name was requested that is not registered."""


class DuplicateComponentError(RegistryError):
    """A component name was registered twice under the same registry."""
