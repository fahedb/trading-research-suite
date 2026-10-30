> Developer reference. Historical phase milestones below describe the source
> project; see [the public distribution notes](PUBLICATION.md) for this repository.

# trading-research

An extensible, strategy-agnostic quantitative research platform. Built as a
**modular monolith**: one deployable, one process, with enforced boundaries
between layers.

This Linux repository **consumes immutable, versioned market datasets**; data
acquisition (Windows/MetaTrader 5 export) happens outside this repository
entirely and delivers hash-manifested Parquet datasets to a directory the
platform treats as read-only. The platform is organised around a pipeline
that many unlike research approaches share — rule-based, statistical, ML,
similarity, regime — never around one strategy.

## START HERE

| If you want… | Read |
|---|---|
| Current project state & exact next step | [PROJECT_STATE.md](../PROJECT_STATE.md) |
| The architecture (canonical reference) | [docs/architecture/V1.md](architecture/V1.md) |
| The construction plan & phase status | [docs/roadmap/V1_ROADMAP.md](roadmap/V1_ROADMAP.md) |
| Data-discipline & statistics rules | [docs/research/RESEARCH_POLICY.md](research/RESEARCH_POLICY.md) |
| What is known (and not) about trading costs | [docs/research/COST_AUDIT.md](research/COST_AUDIT.md) |
| How to add a new feature provider | [docs/guides/ADDING_FEATURES.md](guides/ADDING_FEATURES.md) |
| How to add a new representation model | [docs/guides/ADDING_REPRESENTATIONS.md](guides/ADDING_REPRESENTATIONS.md) |
| How to add a new research model | [docs/guides/ADDING_RESEARCH_MODELS.md](guides/ADDING_RESEARCH_MODELS.md) |
| How to add a new decision policy | [docs/guides/ADDING_DECISION_POLICIES.md](guides/ADDING_DECISION_POLICIES.md) |
| How to add a new outcome specification | [docs/guides/ADDING_OUTCOMES.md](guides/ADDING_OUTCOMES.md) |
| Why each decision was made | [docs/adr/README.md](adr/README.md) |
| The independent review that ratified it | [docs/architecture/reviews/2026-08-06-independent-review.md](architecture/reviews/2026-08-06-independent-review.md) |
| Rules for AI coding sessions | [CLAUDE.md](../CLAUDE.md) |

> Architecture status: **ACCEPTED** (ratified 2026-08-06 after an
> [independent review](architecture/reviews/2026-08-06-independent-review.md)),
> with [ADR-0021](adr/ADR-0021-columnar-feature-contract.md) accepted
> 2026-08-12, [ADR-0022](adr/ADR-0022-phase-3-research-contracts.md)
> accepted 2026-08-13 and
> [ADR-0023](adr/ADR-0023-phase-4-simulation-contracts.md) accepted
> 2026-08-14, and
> [ADR-0024](adr/ADR-0024-multiple-testing-trial-ledger.md) accepted
> 2026-08-15.

## Build state

The repository is being refactored from the original skeleton into the V1
architecture, phase by phase, per the
[roadmap](roadmap/V1_ROADMAP.md).

| Layer | State |
|---|---|
| **Data layer** (`core/time`, `core/frames`, `data/`) | **Phase 1 COMPLETE.** `NativeTimeKey` + `ClockDomain`, hash-verified `MarketFrame` loading, hash-pinned Tier-1 views, gating + write-ahead audit, `GapIndex`, `open_bounded_view`. |
| **Features** (`core/features`, `features/`, `artifacts/`) | **Phase 2 COMPLETE.** Columnar `FeatureProvider` with declared finite reach and explicit gap policy, dense `FeatureFrame`, stage identity, immutable content-verified artifact store, materialization policy, determinism verification. |
| **Research stages and runs** (`models/`, `representations/`, `decisions/`, `evaluation/`, `experiments/`) | **Phase 3 COMPLETE (2026-08-14); CP-12 GREEN and OWNER APPROVED.** Slices 3A–3J provide immutable columnar contracts, fit/infer execution, complete fitted-state identity, row-locality proofs, derived-purge folds, closed decision/evaluation stages, files-first run records, strict resolved configs, the four-verb CLI, detached execution, cache/resume, stored cache-bypassing verification and the closed six-loop qualification harness. |
| **Orchestration** (`experiments/`) | The fixed V1 pipeline, zero-reinference policy fan-out, exact `F·S·E + F·P·B` inventory, crash-safe files-first records, V1/V2-compatible scanning, resolved config, CLI, detached execution, resume and cache-bypassing verification are implemented and qualified. |
| **Simulation** (`simulation/`) | **Phase 4 COMPLETE (2026-08-15); CP-13/CP-14/CP-15/CP-16 GREEN and OWNER APPROVED.** One pure bracketed single-position simulator produces immutable `TradeLog`/`EquityCurve` artifacts and exactly two Plane-B evaluator families. The full-axis TRAIN median was 1.325094530 s versus the 60 s budget, with zero audit delta. Results carry the evidence state from [COST_AUDIT.md](research/COST_AUDIT.md), which remains OPEN and fail-closed: `verified` is unreachable until a repository-controlled binding exists. The recorded price path remains explicitly `RECORDED_PATH_UNVERIFIED`; no result is a verified execution or real-money estimate. See [PHASE4_SIMULATION.md](phases/PHASE4_SIMULATION.md) and [ADR-0023](adr/ADR-0023-phase-4-simulation-contracts.md). |
| **Experiment operations** (`ops/`, `index/`) | **Phase 5 COMPLETE (2026-08-16); CP-17…CP-21 GREEN and OWNER APPROVED.** Deterministic sweeps, bounded whole-run pools, derived SQLite indexing, comparisons and the files-authoritative Trial Ledger are implemented. |
| **First research strategy** | **Zone Trader v13 S-1…S-9 IMPLEMENTED on the Phase-6 branch (2026-08-19); pre-merge review pending.** The preregistered TRAIN-only H1 study completed five folds and two Plane-A trials, found no consistent positive sign information, and touched no OOS/LOCKBOX. Plane B remains deferred. See [PHASE6_ZONE_TRADER.md](phases/PHASE6_ZONE_TRADER.md). |

The load-bearing separations are enforced by `tests/test_architecture.py` and
carry through every phase: research output free of trading vocabulary,
decisions isolated in `DecisionPolicy`, features isolated from models, and a
strict layer dependency budget (`core` imports nothing internal; everything
else imports only `core`; `experiments` is the sole composition root).

The original per-point carriers are retired. `MarketDataProvider` and the
per-point feature path were removed in Phases 1/2; Phase-3 Slice 3A removed the
remaining row-oriented research carriers with no shim. Research stages now
exchange `FeatureFrame`, optional `RepresentationFrame`, `ModelOutputFrame`,
`TrainingTargetFrame` / `OutcomeFrame`, and `IntentFrame`.

## Layout

```
src/trading_research/
    core/              contracts, time model, identity, frames, registry
    data/              dataset registry, gating, audit, integrity, views
    artifacts/         stage identity, artifact store, materialization,
                       determinism verification
    features/          feature calculation
    representations/   dense encoders
    models/            research models
    decisions/         model output -> intent
    simulation/        pure single-position bracket simulation
    evaluation/        Plane-A and Plane-B reference evaluators
    experiments/       composition root; config -> persisted run
docs/                  architecture, ADRs, roadmap, research policy, guides
scripts/gate.sh        local quality gate
```

`core` imports from no other layer. `experiments` may import from all of
them. Everything else may import only `core`.

## Development

```bash
uv sync
./scripts/gate.sh      # pytest + ruff + mypy — run before claiming anything works
```

Python 3.13, pinned in `.python-version`, managed by uv independently of the
system interpreter. Machine-specific paths (dataset root, runs root, TMPDIR)
live in `CLAUDE.local.md` (git-ignored; template: `CLAUDE.local.md.example`).
