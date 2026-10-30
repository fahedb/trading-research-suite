# Optional GitHub checks

The [workflow template](checks.yml.example) runs the engine gate, Explorer
tests/lint, and Gitleaks against the repository's Git history.

The publishing connection could create the repository and push source code, but
GitHub refused the workflow file because that connection lacks the `workflow`
scope. The checks were run locally for the first release; the template is not an
active workflow.

To enable it, use a GitHub connection authorized to edit Actions workflows, then
copy the template to `.github/workflows/checks.yml` and commit and push it. For a
GitHub CLI OAuth login, the account owner can request that scope with:

```bash
gh auth refresh --hostname github.com --scopes workflow
```

From the repository root:

```bash
mkdir -p .github/workflows
cp docs/ci/checks.yml.example .github/workflows/checks.yml
git add .github/workflows/checks.yml
git commit -m "Enable automated checks"
git push
```

The workflow uses read-only repository permissions and no project secrets.
