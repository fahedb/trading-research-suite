# Public distribution

This repository is a combined source snapshot of the Research Engine and Trade
Explorer. It starts with a fresh Git history. The original research repositories
remain independent from this distribution.

## Included

- Engine and Explorer source, tests, example configurations, and dependency locks.
- Architecture, extension guides, and research methodology documentation.
- An isolated synthetic Explorer demo and a screenshot generated from it.

## Excluded or replaced

- Original Git histories, local configuration, credentials, environments, and caches.
- Market datasets, generated view manifests, run artifacts, databases, and audit logs.
- The original market-derived Explorer JSON fixture.
- The source project's machine-specific progress ledger and phone-test record.
- Private home-directory paths, replaced with generic examples or portable defaults.

Historical research documents may describe recorded studies, but do not include
the underlying datasets or artifacts. Those studies retain their original
limitations and are not newly reproduced by this source distribution.

## Verification

The export is checked with Gitleaks and a separate file/path and credential
pattern review before publication. The final Git history is scanned as well.
Detailed release check results are recorded in [RELEASE_CHECKS.md](RELEASE_CHECKS.md).
These checks identify known secret patterns; they are not a guarantee that every
possible sensitive string can be detected.

## One repository, two environments

The engine's existing fingerprint rules apply to the whole repository. Explorer
changes therefore change future experiment identities and stage cache keys.
The Explorer's dependencies remain in its own lockfile and environment. See
[ADR-0029](adr/ADR-0029-public-suite-distribution.md).
