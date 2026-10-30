# Publication checks

Checked on 2026-10-30 with Python 3.13.14 and uv 0.12.1.

| Check | Result |
| --- | --- |
| Engine test suite | 1,916 passed; 22 skipped |
| Engine Ruff | Passed |
| Engine mypy | Passed; 190 source files |
| Explorer test suite | 97 passed; 55 skipped |
| Explorer Ruff | Passed |
| Desktop browser | Family → runs → overview → trades; next trade and all four timeframes passed |
| Mobile-sized browser | Same flow passed at 390 × 844; no browser errors |
| Fresh local clone | Both locked environments installed; engine CLI and demo API passed |
| Gitleaks 8.30.1, export contents | No findings |
| Gitleaks 8.30.1, new Git history | No findings |
| Separate file and credential-pattern review | No sensitive-pattern findings or unreviewed binary files |
| Research engine source comparison | All `src/trading_research/` files match the source snapshot byte for byte |

Performance qualifications and real-data integration tests require external
datasets and run artifacts; they were not executed for publication. Missing-data
checks account for skipped tests, including the excluded original Explorer
fixture. Browser checks used Chromium with emulated desktop and mobile viewport
sizes, not a physical phone.

Only tracked source files were copied from the original repositories. Original
Git histories, local configuration, market data, view manifests, run artifacts,
audit logs, and environments were excluded. The README screenshots show only the
synthetic demo. Git authorship uses a GitHub noreply address.

Secret scans use the scanner's default rules without repository allowlists or
baselines. The separate review checks private paths and network addresses,
credential-bearing URLs, common token and private-key formats, personal email
patterns, generated-data files, and unexpected symlinks or binary files. These
checks reduce accidental disclosure; they do not prove that every possible
sensitive string is detectable.

An [optional GitHub workflow](ci/checks.yml.example) is ready to repeat engine
and Explorer checks and scan Git history for secrets. It is not active: the
publishing connection lacks GitHub's `workflow` scope. Its actions are pinned by
commit and the scanner download is verified against a pinned SHA-256 digest.
See [activation instructions](ci/README.md).
